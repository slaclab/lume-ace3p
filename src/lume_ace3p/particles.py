"""Field-emission reweighting of a Track3P impact dump into a Geant4 source.

Track3P tracks *macroparticles*: one per emitting face and emission time, each
carrying the normal field at its birth face (``InitialNormalField``) and that
face's area (``InitialFaceArea``). How many real electrons that macroparticle
stands for follows from the Fowler–Nordheim current density at the enhanced
field ``β·E``, times the face area, times the emission time -- and β, the field
enhancement factor, is the unknown these studies vary. Track3P is therefore run
once per cavity and field level, and this module reweights the same dump for any
β without re-tracking.

Two Fowler–Nordheim forms are implemented (``fn_model``):

* ``'fn'`` (default) -- the plain FN form of Lixin Ge's LCLS-II reference
  converter (``convert_track3p.py``), ``J = A/φ · (βE)² · exp(−B φ^1.5 / βE)``
  with ``A = 1.541434e-6 A eV V⁻²`` and ``B = 6.830890e9 V m⁻¹ eV^-1.5``; the
  emission time is one RF period ``1/frequency``; weights are real numbers and
  a macroparticle standing for less than one electron is dropped from the
  Geant4 file. With ``frequency: 1.2999e9``, ``work_function: 4.2`` and
  ``min_energy_ev: 1000`` this reproduces the shipped ``c3_16MV_beta120.data``
  to the file's own six significant figures
  (``tests/test_particles.py::test_fn_model_reproduces_lixins_particle_file``).
* ``'wang-loew'`` -- the form this module used before 2026-09-14, with the
  work-function-dependent prefactor ``1.54e-6 · 10^(4.52/√φ) / φ`` and
  ``B = 6.53e9``, kept for existing studies: it reproduces the
  ``track3p_particle_weight`` baseline bit-for-bit, which is why its weights are
  still **rounded to whole electrons** (the legacy behaviour) where ``'fn'``
  keeps them real. The two forms differ by about two orders of magnitude in
  weight at ``βE ≈ 2e9 V/m``, so a β inferred with one is not comparable with
  the other.

Per-bin β is this module's addition over the reference converter: ``beta`` may
be one value per axial (``Initial_z``) bin, with ``num_bins`` bins between the
filtered particles' z range or at explicit ``bin_edges``.
"""

import os
import warnings

import numpy as np
import pandas as pd


Q_E = 1.602176634e-19
# The charge the legacy ('wang-loew') weighting divided by. Kept verbatim: the
# 2.5e-9 relative difference from Q_E flips a rounding in the frozen
# track3p_particle_weight baseline.
_LEGACY_Q_E = 1.60217663e-19

TRACK3P_COLUMNS = [
    'InitialID', 'ImpactOrder', 'Initial_x', 'Initial_y', 'Initial_z',
    'Impact_x', 'Impact_y', 'Impact_z', 'InitialPhaseinRFcycle',
    'ImpactPhaseinRFcycle', 'ImpactEnergy', 'momentum_x', 'momentum_y',
    'momentum_z', 'ImpactFaceID', 'InitialNormalField', 'InitialFaceArea'
]

# Fowler-Nordheim constants per model: (prefactor(phi), B). J(βE) =
# prefactor(φ) · (βE)² · exp(−B · φ^1.5 / βE) in A/m² for βE in V/m, φ in eV.
FN_MODELS = {
    'fn': (lambda phi: 1.541434e-6 / phi, 6.830890e9),
    'wang-loew': (lambda phi: 1.54e-6 * 10 ** (4.52 / np.sqrt(phi)) / phi,
                  6.53e9),
}


def fowler_nordheim_current_density(beta_e, work_function, model='fn'):
    """FN current density [A/m²] at enhanced normal field `beta_e` [V/m] for a
    surface of `work_function` [eV], for either model in :data:`FN_MODELS`.

    Computed in log space and clipped to ``exp(±500)``, as the reference
    converter does, so a face with a vanishing field gives a negligible floor
    rather than a NaN or an overflow."""
    prefactor, b_fn = FN_MODELS[model]
    beta_e = np.maximum(np.abs(np.asarray(beta_e, dtype=float)), 1e-30)
    log_j = (np.log(prefactor(work_function)) + 2 * np.log(beta_e)
             - b_fn * work_function ** 1.5 / beta_e)
    return np.exp(np.clip(log_j, -500, 500))


def _as_list(value):
    if value is None:
        return None
    return list(value) if isinstance(value, (list, tuple, np.ndarray)) else [value]


class Particles:
    """Reweight a Track3P ``Initials-Impacts`` dump and write a particle file.

    ``particle_params`` keys (the ``particles`` module's YAML keys):

    ``work_function`` (eV, required), ``frequency`` (Hz, required unless the
    deprecated ``dt`` in seconds is given -- the emission time is ``1/frequency``
    or ``dt``), ``fn_model`` (``'fn'`` | ``'wang-loew'``, default ``'fn'``),
    ``beta`` (one value, or one per bin), ``num_bins`` (default ``len(beta)``),
    ``bin_edges`` (``num_bins + 1`` values, optional), ``min_energy_ev`` (drop
    impacts below this ``ImpactEnergy``; default 0, i.e. none), ``impact_order``
    and ``impact_face_id`` (a value or list; absent means no filter),
    ``output_format`` (``'track3p'`` | ``'geant4'``).
    """

    def __init__(self, particle_file, particle_params, output_file=None, workdir=None):
        self.particle_file = particle_file
        self.output_file = output_file or particle_file.replace('.txt', '_modified.txt')
        self.workdir = workdir or os.getcwd()
        self.impact_order = _as_list(particle_params.get('impact_order'))
        self.impact_face_id = _as_list(particle_params.get('impact_face_id'))
        self.work_function = particle_params.get('work_function')
        if self.work_function is None:
            raise ValueError("particles: 'work_function' (eV) is required.")
        self.min_energy_ev = float(particle_params.get('min_energy_ev', 0.0) or 0.0)
        self.fn_model = str(particle_params.get('fn_model', 'fn')).lower()
        if self.fn_model not in FN_MODELS:
            raise ValueError(
                f"particles: unknown fn_model '{self.fn_model}'; choose one of "
                f"{sorted(FN_MODELS)}.")
        self.emission_time = self._resolve_emission_time(particle_params)
        self.output_format = particle_params.get('output_format', 'track3p')
        beta = particle_params.get('beta')
        if beta is None:
            raise ValueError("particles: 'beta' is required (one value, or one "
                             "per bin).")
        self.beta = np.atleast_1d(np.asarray(beta, dtype=float))
        self.num_bins = int(particle_params.get('num_bins', len(self.beta)))
        self.bin_edges = particle_params.get('bin_edges', None)
        if self.bin_edges is not None:
            self.bin_edges = np.array(self.bin_edges)
            assert len(self.bin_edges) == self.num_bins + 1, (
                f"Length of bin_edges ({len(self.bin_edges)}) must be num_bins + 1 ({self.num_bins + 1})")
        assert len(self.beta) == self.num_bins, (
            f"Length of beta ({len(self.beta)}) must match num_bins ({self.num_bins})")

    @staticmethod
    def _resolve_emission_time(params):
        """The time one macroparticle's current flows: ``1/frequency``, or the
        deprecated ``dt``. Exactly one must be given."""
        frequency = params.get('frequency')
        dt = params.get('dt')
        if frequency is not None and dt is not None:
            raise ValueError(
                "particles: set 'frequency' (Hz) or the deprecated 'dt' (s), "
                "not both -- the emission time is 1/frequency.")
        if frequency is not None:
            frequency = float(frequency)
            if frequency <= 0:
                raise ValueError("particles: 'frequency' must be positive (Hz).")
            return 1.0 / frequency
        if dt is not None:
            warnings.warn(
                "particles: 'dt' is deprecated; set 'frequency' (Hz) instead. "
                "The emission time is one RF period, 1/frequency -- for "
                f"dt = {dt!r} that is frequency = {1.0 / float(dt)!r}.",
                DeprecationWarning, stacklevel=4)
            return float(dt)
        raise ValueError(
            "particles: 'frequency' (Hz, the RF frequency; emission time is one "
            "RF period) is required.")

    def load(self):
        filepath = os.path.join(self.workdir, self.particle_file)
        self.data = pd.read_csv(filepath, sep=r'\s+', comment='#',
                                header=None, names=TRACK3P_COLUMNS)

    def filter_particles(self):
        mask = np.ones(len(self.data), dtype=bool)
        if self.impact_order is not None:
            mask &= self.data['ImpactOrder'].isin(self.impact_order).values
        if self.impact_face_id is not None:
            mask &= self.data['ImpactFaceID'].isin(self.impact_face_id).values
        if self.min_energy_ev > 0:
            mask &= (self.data['ImpactEnergy'] >= self.min_energy_ev).values
        self.filtered = self.data[mask].copy()

    def assign_bins(self):
        z_vals = self.filtered['Initial_z']
        if self.bin_edges is not None:
            bin_edges = self.bin_edges
        elif len(z_vals):
            bin_edges = np.linspace(z_vals.min(), z_vals.max() + 1e-15, self.num_bins + 1)
        else:
            bin_edges = np.linspace(0.0, 1.0, self.num_bins + 1)
        self.filtered['Bin'] = np.digitize(z_vals, bin_edges) - 1
        self.filtered['Bin'] = self.filtered['Bin'].clip(0, self.num_bins - 1)
        # Warn if any bins are empty
        bin_counts = np.bincount(self.filtered['Bin'], minlength=self.num_bins)
        empty_bins = np.where(bin_counts == 0)[0]
        if len(empty_bins) > 0:
            print(f'WARNING: Bins {empty_bins.tolist()} have no particles. '
                  f'Check that num_bins matches the particle distribution.')

    def calculate_particle_weight(self):
        """Electrons per macroparticle: ``J(β·E) · area · emission_time / e``.

        Real-valued for ``'fn'``; rounded to whole electrons for ``'wang-loew'``,
        which is the legacy behaviour that model exists to preserve."""
        beta_per_particle = self.beta[self.filtered['Bin'].values]
        beta_e = beta_per_particle * self.filtered['InitialNormalField'].values
        areas = self.filtered['InitialFaceArea'].values
        if self.fn_model == 'wang-loew':
            # The pre-2026-09-14 arithmetic, operation for operation: direct
            # (not log-space) evaluation and the old charge constant, then
            # rounding to whole electrons. Any of the three changed flips a
            # rounding somewhere in a 300k-row dump.
            phi = self.work_function
            term1 = (1.54 * 10 ** (-6 + (4.52 / np.sqrt(phi)))) / phi
            term2 = -6.53e9 * (phi ** 1.5)
            current_density = term1 * np.power(beta_e, 2) * np.exp(term2 / beta_e)
            weight = np.round(current_density * areas * self.emission_time
                              / _LEGACY_Q_E)
        else:
            current_density = fowler_nordheim_current_density(
                beta_e, self.work_function, self.fn_model)
            weight = current_density * areas * self.emission_time / Q_E
        self.filtered['ParticleWeight'] = weight

    def write_output(self):
        if self.output_format == 'geant4':
            self._write_output_geant4()
        else:
            self._write_output_track3p()

    def _write_output_track3p(self):
        output_path = os.path.join(self.workdir, self.output_file)
        header = '#' + ' '.join(self.filtered.columns.tolist())
        np.savetxt(output_path, self.filtered.values,
                   header=header[1:], comments='#',
                   fmt=['%d', '%d',
                        '%.6e', '%.6e', '%.6e',
                        '%.6e', '%.6e', '%.6e',
                        '%.6e', '%.6e', '%.6e',
                        '%.6e', '%.6e', '%.6e',
                        '%d', '%.6e', '%.6e',
                        '%d', '%.6e'])

    def _write_output_geant4(self):
        # 10-column whitespace-separated source file consumed by the Geant4
        # particle-file reader. Columns (units in parentheses):
        #   x y z (m)  phase (RF cycles)  energy (eV)  weight  dx dy dz  face_id
        # weight is the electrons-per-macroparticle above, used as the event
        # weight; dx/dy/dz is the unit momentum direction (Geant4's
        # SetParticleMomentumDirection normalises anyway, and the reference
        # converter writes unit vectors). The reader ignores columns 4 and 10;
        # Lixin Ge's converter writes 0 there, this writes the impact phase and
        # the face ID. No header line: every row is one primary, and the
        # workflow counts rows for /run/beamOn.
        output_path = os.path.join(self.workdir, self.output_file)
        columns = ['Impact_x', 'Impact_y', 'Impact_z', 'ImpactPhaseinRFcycle',
                   'ImpactEnergy', 'ParticleWeight', 'momentum_x', 'momentum_y',
                   'momentum_z', 'ImpactFaceID']
        # A macroparticle standing for less than one electron emits no primary,
        # but would still count as a row for /run/beamOn: drop it. (For the
        # rounded legacy weights this is the old 'weight != 0' rule.)
        kept = self.filtered[self.filtered['ParticleWeight'] >= 1.0]
        values = kept[columns].values.astype(float)
        momentum = values[:, 6:9]
        norm = np.maximum(np.linalg.norm(momentum, axis=1), 1e-300)
        values[:, 6:9] = momentum / norm[:, None]
        np.savetxt(output_path, values, fmt='%.6e')

    def run(self):
        self.load()
        self.filter_particles()
        self.assign_bins()
        self.calculate_particle_weight()
        self.write_output()
        print(f'Track3P particle file written: {self.output_file}')
        print(f'  Filtered particles: {len(self.filtered)} '
              f'(ImpactOrder={self.impact_order}, ImpactFaceID={self.impact_face_id}, '
              f'ImpactEnergy>={self.min_energy_ev} eV)')
        print(f'  Bin counts: {np.bincount(self.filtered["Bin"], minlength=self.num_bins).tolist()}')
        print(f'  Bins: {self.num_bins}, Beta values: {self.beta.tolist()}, '
              f'FN model: {self.fn_model}, emission time: {self.emission_time:.4e} s')
        return self.filtered
