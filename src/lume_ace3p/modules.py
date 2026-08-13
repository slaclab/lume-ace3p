"""Module layer for the module/workflow/mode architecture.

This is the bottom layer of the three-layer design: modules (here), the
declarative :class:`~lume_ace3p.workflow_graph.Workflow` DAG that orders them,
and the workflow-agnostic modes in :mod:`lume_ace3p.modes` that drive it.

A ``Module`` is a thin adapter over one of the existing step wrappers
(``Cubit``, ``Omega3P``/``S3P``/``T3P``, ``Acdtool``, ``Particles``, ``Geant4``).
Each module declares the *artifact kinds* it ``requires`` (must exist upstream)
and ``provides`` (produces), so a future ``Workflow`` can order a declared list
of modules into a runnable DAG purely from those edges. The requires/provides
edges are deliberately additive — adding :class:`T3PModule` (``requires {mesh}``
/ ``provides {td_solution}``) needed no rule change, and a runnable Track3P
solver will likewise slot in as ``requires {em_solution}`` / ``provides
{track3p_particles}``.

The old skip-flags (``skip_cubit``/``skip_solver``/``skip_acdtool``) and the
``geant4_particle_file`` bypass have no place in this layer: in a declarative
module list, "skip X" is simply "do not list module X", and a prebuilt artifact
is expressed as a *source module*
(:class:`MeshSourceModule` / :class:`Track3PSourceModule` /
:class:`ParticleSourceModule`). The one exception is meshconvert, which is a
sub-step *inside* :class:`CubitModule` and stays a per-module ``meshconvert``
bool.
"""

import os
import shutil

import numpy as np

from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P, S3P, T3P
from lume_ace3p.acdtool import Acdtool
from lume_ace3p.geant4 import Geant4
from lume_ace3p.particles import Particles
from lume_ace3p.inputs import WorkflowInputs, _walk_ace3p


# --------------------------------------------------------------------------- #
# Artifact-kind vocabulary — the strings modules glue on. A module's
# ``requires``/``provides`` are sets drawn from this vocabulary.
# --------------------------------------------------------------------------- #

JOURNAL = 'journal'                    # Cubit journal file
MESH = 'mesh'                          # genesis/ncdf mesh (cubit+meshconvert, or provided)
EM_SOLUTION = 'em_solution'            # Omega3P/S3P frequency-domain solution
TD_SOLUTION = 'td_solution'            # T3P time-domain solution (wakefields)
RF_POST = 'rf_post'                    # acdtool postprocess results
TRACK3P_PARTICLES = 'track3p_particles'  # raw Track3P dump (produced EXTERNALLY today)
PARTICLE_SOURCE = 'particle_source'    # Geant4-format particle file (Particles output)
DOSE_GRID = 'dose_grid'                # Geant4 dose scoring output
EDEP_GRID = 'edep_grid'                # Geant4 energy-deposit scoring output

ARTIFACT_KINDS = frozenset({
    JOURNAL, MESH, EM_SOLUTION, TD_SOLUTION, RF_POST, TRACK3P_PARTICLES,
    PARTICLE_SOURCE, DOSE_GRID, EDEP_GRID,
})


# --------------------------------------------------------------------------- #
# RunContext — the per-evaluation state threaded through a module chain.
# --------------------------------------------------------------------------- #


class RunContext:
    """State for one evaluation of a module chain.

    ``artifacts`` maps an artifact kind (see the vocabulary above) to the path
    a module produced for it; ``outputs`` collects extracted scalar/structured
    quantities. Modules read ``inputs`` (a materialized :class:`WorkflowInputs`
    for this eval point), ``paths`` (resolved executable paths), and
    ``dry_run``.
    """

    def __init__(self, workdir, inputs=None, artifacts=None, outputs=None,
                 dry_run=False, paths=None, stage_mode='copy'):
        self.workdir = workdir
        self.inputs = inputs if inputs is not None else WorkflowInputs()
        self.artifacts = dict(artifacts) if artifacts else {}
        self.outputs = dict(outputs) if outputs else {}
        self.dry_run = dry_run
        self.paths = dict(paths) if paths else {}
        self.stage_mode = stage_mode

    def ensure_workdir(self):
        if self.workdir and not os.path.exists(self.workdir):
            os.makedirs(self.workdir, exist_ok=True)


def _ace3p_leaf_pairs(section):
    """Return the (path, value) leaf pairs of an ACE3P Section, matching the
    marker content the legacy per-workflow dry-run blocks wrote."""
    return [(path, value) for path, value in _walk_ace3p(section)]


def _append_marker(ctx, text):
    """Append a module's dry-run description to the workdir DRY_RUN.txt. Each
    module contributes its own block, so an assembled chain (Phase 2) yields a
    combined marker."""
    ctx.ensure_workdir()
    with open(os.path.join(ctx.workdir, 'DRY_RUN.txt'), 'a') as f:
        f.write(text)


STAGE_MODES = frozenset({'copy', 'symlink', 'hardlink'})


def _link_or_copy(mode, src, dest):
    """Materialize ``src`` at ``dest`` using the requested staging strategy.

    ``symlink`` writes an *absolute* symlink (the tool runs with ``cwd=workdir``,
    so a relative link would not resolve). ``hardlink`` falls back to a real copy
    on ``OSError`` — cross-device links (``EXDEV``) and filesystems that forbid
    hardlinks are common on WSL / network mounts — and warns so the fallback is
    not silent."""
    if mode == 'symlink':
        os.symlink(os.path.abspath(src), dest)
    elif mode == 'hardlink':
        try:
            os.link(src, dest)
        except OSError as exc:
            print(f"Warning: hardlink of {src} failed ({exc}); copying instead.")
            shutil.copy(src, dest)
    else:
        shutil.copy(src, dest)


def _stage_file(ctx, src):
    """Stage a source file into the workdir (unless already there) and return
    the in-workdir path. Used by the source modules and by any module that
    consumes an externally-supplied file.

    Honors ``ctx.stage_mode`` (``copy`` | ``symlink`` | ``hardlink``): all three
    land the file at ``workdir/basename`` so the co-location contract — every
    referenced file resolves as a bare basename under the run's ``cwd`` — is
    identical regardless of mode.

    INVARIANT: staged files are treated as read-only. Symlink and hardlink modes
    share bytes with the source, so any in-place write to a staged file would
    corrupt the original. Modules that mutate an input (Cubit/ACE3P/Geant4
    parameter merges) copy and rewrite their own input files separately; they do
    not go through this helper."""
    ctx.ensure_workdir()
    base = os.path.basename(src)
    dest = os.path.join(ctx.workdir, base)
    if not os.path.isfile(src) or os.path.abspath(src) == os.path.abspath(dest):
        return dest
    if os.path.lexists(dest):          # real file or a pre-existing/stale symlink
        return dest
    _link_or_copy(ctx.stage_mode, src, dest)
    return dest


# --------------------------------------------------------------------------- #
# Module base
# --------------------------------------------------------------------------- #


class Module:
    """Base class for a pipeline step.

    Subclasses set ``type`` (registry key), ``requires`` and ``provides``
    (artifact-kind sets), and implement :meth:`run`. :meth:`extract` pulls a
    scalar/structured quantity out of the module's own artifacts for the
    ``output_parameters`` spec; the default raises for modules with no
    extractable quantities.
    """

    type = None
    requires = frozenset()
    provides = frozenset()

    def __init__(self, config=None, name=None):
        self.config = dict(config) if config else {}
        self.name = name or self.type

    def run(self, ctx):
        raise NotImplementedError

    def extract(self, ctx, spec):
        raise NotImplementedError(
            f"module '{self.type}' exposes no extractable quantities")

    def field_index(self, ctx):
        """Return ``(label, values)`` for the shared index axis this module's
        field outputs are aligned to (e.g. S3P's ``('Frequency', array)``), or
        ``None`` if the module produces no index-aligned field outputs.

        The mode layer uses this seam to emit the S3P long-format sweep table
        (one row per (grid-point, frequency)) generically, without reaching
        into any solver-specific code."""
        return None

    def field(self, ctx):
        """Return this module's structured *field* output for the just-run
        evaluation, or ``None`` if it produces none.

        A field is the ragged/nested per-run output the hybrid data model keeps
        out of the flat result table (S3P ``{Frequency, S(m,n)...}`` arrays,
        Geant4 ``{dose/edep: {indices, values}}`` voxel grids). The mode layer
        persists it per row via :func:`lume_ace3p.results.save_field` and stores
        only the returned handle in the table's field-artifact column; the arrays
        reload on demand with :func:`lume_ace3p.results.load_field`. Modules that
        expose a :meth:`field_index` (S3P) are emitted long-format instead, so
        their :meth:`field` is not used for the sweep table."""
        return None

    def __repr__(self):
        return f'<{type(self).__name__} name={self.name!r}>'


# --------------------------------------------------------------------------- #
# Source modules — provide a prebuilt artifact from a supplied file.
# --------------------------------------------------------------------------- #


class MeshSourceModule(Module):
    """Provide a ``mesh`` from a prebuilt mesh file — the declarative
    replacement for ``skip_cubit`` + a supplied mesh."""

    type = 'mesh'
    provides = frozenset({MESH})

    def run(self, ctx):
        src = self.config.get('file')
        if src is None:
            raise ValueError("mesh source module requires a 'file'.")
        ctx.artifacts[MESH] = _stage_file(ctx, src)


class Track3PSourceModule(Module):
    """Provide ``track3p_particles`` from an externally-produced Track3P dump.

    This is a *source* module — there is no in-pipeline Track3P solver in this
    refactor. When a runnable Track3P/T3P solver is built later it will
    ``require {em_solution}`` and ``provide {track3p_particles}``; nothing here
    needs to change for that to slot in."""

    type = 'track3p_source'
    provides = frozenset({TRACK3P_PARTICLES})

    def run(self, ctx):
        src = self.config.get('file')
        if src is None:
            raise ValueError("track3p source module requires a 'file'.")
        ctx.artifacts[TRACK3P_PARTICLES] = _stage_file(ctx, src)


class ParticleSourceModule(Module):
    """Provide a ``particle_source`` directly from a Geant4-format particle
    file — the declarative way to supply a prebuilt Geant4 source file directly,
    bypassing the Particles weighting step."""

    type = 'particle_source'
    provides = frozenset({PARTICLE_SOURCE})

    def run(self, ctx):
        src = self.config.get('file')
        if src is None:
            raise ValueError("particle source module requires a 'file'.")
        ctx.artifacts[PARTICLE_SOURCE] = _stage_file(ctx, src)


# --------------------------------------------------------------------------- #
# Cubit
# --------------------------------------------------------------------------- #


class CubitModule(Module):
    """Provide a ``mesh`` from a Cubit ``journal``. Runs meshconvert unless
    ``meshconvert: false``."""

    type = 'cubit'
    provides = frozenset({MESH})

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.journal = self.config.get('journal') or self.config.get('cubit_input')
        self.meshconvert = self.config.get('meshconvert', True)

    def run(self, ctx):
        if self.journal is None:
            raise ValueError("cubit module requires a 'journal'.")
        if ctx.dry_run:
            # Legacy dry-run skipped Cubit entirely; record a nominal mesh path
            # (side-effect-free) so a downstream solver's requirement is met.
            base = os.path.splitext(os.path.basename(self.journal))[0]
            ctx.artifacts[MESH] = os.path.join(ctx.workdir, base + '.genesis')
            _append_marker(ctx, 'Dry run mode: Cubit step skipped.\n'
                                f'Cubit journal: {self.journal}\n'
                                f'Cubit inputs: {ctx.inputs.cubit}\n')
            return
        ctx.ensure_workdir()
        cubit = Cubit(self.journal, workdir=ctx.workdir,
                      ace3p_path=ctx.paths.get('ace3p', ''),
                      cubit_path=ctx.paths.get('cubit', ''),
                      mpi_caller=ctx.paths.get('mpi', ''))
        if ctx.inputs.cubit:
            cubit.set_value(ctx.inputs.cubit)
        cubit.run(mcflag=self.meshconvert)
        self._cubit = cubit
        mesh = getattr(cubit, 'exportfile', None)
        if mesh is None:
            cubit.get_export()
            mesh = getattr(cubit, 'exportfile', None)
        ctx.artifacts[MESH] = (os.path.join(ctx.workdir, mesh) if mesh
                               else ctx.workdir)


# --------------------------------------------------------------------------- #
# EM solvers
# --------------------------------------------------------------------------- #


class _SolverModule(Module):
    """Shared body for the ACE3P solver adapters — all require a ``mesh`` and
    provide a solution artifact, differing only in the wrapper they construct,
    the dry-run label, and which artifact kind they produce.

    Omega3P/S3P provide an ``em_solution`` (frequency domain); T3P provides a
    ``td_solution`` (time domain). The split is deliberate: ``acdtool`` requires
    ``em_solution``, so a T3P workflow that lists ``acdtool`` fails validation
    instead of silently running RF postprocessing on time-domain output."""

    requires = frozenset({MESH})
    provides = frozenset({EM_SOLUTION})
    _wrapper = None
    _label = ''
    _artifact = EM_SOLUTION

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.input_file = self.config.get('input') or self.config.get('ace3p_input')
        self.tasks = self.config.get('tasks', self.config.get('ace3p_tasks'))
        self.cores = self.config.get('cores', self.config.get('ace3p_cores'))
        self.opts = self.config.get('opts', self.config.get('ace3p_opts'))
        self._solver = None

    def run(self, ctx):
        if MESH not in ctx.artifacts:
            raise ValueError(f"module '{self.type}' requires a mesh artifact.")
        if ctx.dry_run:
            self._solver = None
            leaves = _ace3p_leaf_pairs(ctx.inputs.ace3p)
            _append_marker(ctx, f'Dry run mode: {self._label} step skipped.\n'
                                f'Cubit: {ctx.inputs.cubit}\n'
                                f'ACE3P: {[(_, v) for _, v in leaves]}\n')
            ctx.artifacts[self._artifact] = ctx.workdir
            return
        ctx.ensure_workdir()
        solver = self._wrapper(self.input_file,
                               ace3p_tasks=self.tasks,
                               ace3p_cores=self.cores,
                               ace3p_opts=self.opts,
                               workdir=ctx.workdir,
                               ace3p_path=ctx.paths.get('ace3p', ''),
                               mpi_caller=ctx.paths.get('mpi', ''))
        solver.set_value(ctx.inputs.ace3p)
        solver.run()
        self._solver = solver
        ctx.artifacts[self._artifact] = ctx.workdir


class Omega3PModule(_SolverModule):
    type = 'omega3p'
    _wrapper = Omega3P
    _label = 'Omega3P'

    # Omega3P's own output file (``omega3p.out``) is not parsed for scalars;
    # the RoverQ/kickFactor/maxFields quantities come from acdtool (rf_post),
    # so they are extracted by :class:`AcdtoolModule`, not here.


class S3PModule(_SolverModule):
    type = 's3p'
    _wrapper = S3P
    _label = 'S3P'

    def extract(self, ctx, spec):
        """Return an S-parameter quantity from the S3P solution.

        ``spec`` may be:
          * a string ``'S(0,0)'`` — the full frequency-indexed array,
          * a single-element list ``['S(0,0)']`` — same, first element used,
          * a mapping ``{'quantity': 'S(0,0)', 'at': {'frequency': f}}`` — the
            scalar value at frequency ``f`` (the objective form the Xopt driver
            needs).
        """
        solver = self._solver
        if solver is None:
            # Dry-run / no solver: mirror the legacy evaluate NaN sentinel.
            return np.array([float('nan')])
        data = solver.output_data
        assert len(data) > 0, 'No output data found, run S3P first.'
        quantity, frequency = self._parse_spec(spec)
        if quantity not in data:
            raise ValueError("Unknown section name '" + str(quantity)
                             + "' in output dict.")
        values = data[quantity]
        if frequency is None:
            return values
        freqs = list(data['Frequency'])
        try:
            idx = freqs.index(float(frequency))
        except ValueError:
            print('Inputted frequency to be optimized is not in frequency sweep.')
            return float('nan')
        return values[idx]

    @staticmethod
    def _parse_spec(spec):
        if isinstance(spec, dict):
            quantity = spec.get('quantity')
            at = spec.get('at') or {}
            return quantity, at.get('frequency')
        if isinstance(spec, list):
            return spec[0], None
        return spec, None

    def field_index(self, ctx):
        """S3P field outputs are indexed by frequency. Return
        ``('Frequency', array)``; under dry-run (no solver) return a single-row
        ``[0.0]`` sentinel so a swept long-format table still has one row per
        grid point."""
        solver = self._solver
        if solver is None:
            return 'Frequency', np.array([0.0])
        return 'Frequency', np.asarray(solver.output_data['Frequency'])

    def field(self, ctx):
        """Return the full S3P spectrum (``{IndexMap, Frequency, S(m,n)...}``)
        for the just-run evaluation, or ``None`` under dry-run.

        This is the structured field artifact for a single point. In a sweep,
        S3P goes long-format (its :meth:`field_index` puts one row per
        frequency), so this is used only when a caller wants to persist the raw
        spectrum for a row rather than explode it."""
        solver = self._solver
        if solver is None:
            return None
        return dict(solver.output_data)


class T3PModule(_SolverModule):
    """The T3P time-domain solver: requires a ``mesh``, provides a
    ``td_solution``.

    Exposes the wakefield monitor's results the same way :class:`S3PModule`
    exposes S-parameters — a scalar figure of merit plus arrays over a shared
    index — except the index is the wake coordinate ``s`` rather than frequency.
    """

    type = 't3p'
    provides = frozenset({TD_SOLUTION})
    _wrapper = T3P
    _label = 'T3P'
    _artifact = TD_SOLUTION

    # Bare quantity names this module answers to, used both by ``extract`` and
    # by the output-spec router in workflow_graph.
    QUANTITIES = frozenset({'loss_factor', 'kick_factor', 'W', 'I_bunch', 's'})

    # Extractable scalars -> the key ``parse_wakefield`` stores them under.
    _SCALARS = {'loss_factor': 'LossFactor', 'kick_factor': 'KickFactor'}

    def extract(self, ctx, spec):
        """Return a wakefield quantity from the T3P solution.

        ``spec`` may be:
          * ``'loss_factor'`` / ``'kick_factor'`` — the scalar figure of merit,
          * ``'W'`` / ``'I_bunch'`` / ``'s'`` — the full ``s``-indexed array,
          * a single-element list wrapping either of the above,
          * a mapping ``{'quantity': 'W', 'at': {'s': 0.05}}`` — the value at the
            wake position nearest ``s`` (the objective form an Xopt run needs).
        """
        solver = self._solver
        if solver is None:
            # Dry-run / no solver: same NaN sentinel S3PModule returns.
            return np.array([float('nan')])
        quantity, position = self._parse_spec(spec)
        data = solver.output_data
        if not data:
            raise ValueError(
                f"no T3P wakefield results to extract '{quantity}' from. T3P "
                "writes them only when the input file declares a WakeField "
                "monitor, e.g.\n"
                "  Monitor: { Type: WakeField  Name: wakefield ... }\n"
                f"Expected file: {os.path.join(solver.results_dir(), 'wakefield.out')} "
                f"under {ctx.workdir}.")

        if quantity in self._SCALARS:
            key = self._SCALARS[quantity]
            if key not in data:
                # Longitudinal runs report a loss factor, transverse ones a kick
                # factor. Name the one that IS present rather than return NaN.
                present = [name for name, k in self._SCALARS.items() if k in data]
                raise ValueError(
                    f"this is a {data.get('WakeType', 'unknown')} T3P run, which "
                    f"reports {present} — not '{quantity}'. The wake type follows "
                    "from the WakeField monitor's contour and the beam offset in "
                    "the input file.")
            return data[key]

        if quantity not in data:
            raise ValueError("Unknown quantity '" + str(quantity)
                             + "' in T3P output dict. Known quantities: "
                             + str(sorted(self.QUANTITIES)) + ".")
        values = data[quantity]
        if position is None:
            return values
        # Unlike S3P's frequency scan, the s grid is a solver-chosen consequence
        # of the timestep, so an exact match is not something a user can specify.
        # Take the nearest sample instead.
        grid = np.asarray(data['s'])
        if not grid.size:
            return float('nan')
        return values[int(np.argmin(np.abs(grid - float(position))))]

    @staticmethod
    def _parse_spec(spec):
        if isinstance(spec, dict):
            at = spec.get('at') or {}
            return spec.get('quantity'), at.get('s')
        if isinstance(spec, list):
            return spec[0], None
        return spec, None

    def field_index(self, ctx):
        """T3P field outputs are indexed by the wake coordinate ``s``. Returns
        ``('s', array)``; under dry-run (no solver) a single-row ``[0.0]``
        sentinel, so a swept long-format table still has one row per grid
        point — mirroring :meth:`S3PModule.field_index`."""
        solver = self._solver
        if solver is None:
            return 's', np.array([0.0])
        data = solver.output_data
        if not data:
            return 's', np.array([0.0])
        return 's', np.asarray(data['s'])

    def field(self, ctx):
        """Return the full wakefield result (``{s, W, I_bunch, LossFactor|
        KickFactor, WakeType, ...}``) for the just-run evaluation, or ``None``
        under dry-run / when the run declared no WakeField monitor."""
        solver = self._solver
        if solver is None or not solver.output_data:
            return None
        return dict(solver.output_data)


# --------------------------------------------------------------------------- #
# Acdtool postprocess
# --------------------------------------------------------------------------- #


class AcdtoolModule(Module):
    """Requires an ``em_solution``, provides ``rf_post``. Owns extraction of
    the RoverQ / kickFactor / maxFieldsOnSurface scalars pulled from the acdtool
    postprocess output."""

    type = 'acdtool'
    requires = frozenset({EM_SOLUTION})
    provides = frozenset({RF_POST})

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.input_file = self.config.get('input') or self.config.get('rfpost_input')
        self._acdtool = None

    def run(self, ctx):
        if EM_SOLUTION not in ctx.artifacts:
            raise ValueError("module 'acdtool' requires an em_solution artifact.")
        if ctx.dry_run:
            self._acdtool = None
            _append_marker(ctx, 'Dry run mode: Acdtool step skipped.\n'
                                f'Acdtool input: {self.input_file}\n')
            ctx.artifacts[RF_POST] = ctx.workdir
            return
        ctx.ensure_workdir()
        acdtool = Acdtool(self.input_file, workdir=ctx.workdir,
                          ace3p_path=ctx.paths.get('ace3p', ''),
                          mpi_caller=ctx.paths.get('mpi', ''))
        acdtool.run()
        self._acdtool = acdtool
        ctx.artifacts[RF_POST] = ctx.workdir

    def extract(self, ctx, spec):
        """Index the acdtool output by ``[section, mode/surface, entry, ...]``
        (e.g. ``['RoverQ', '0', 'RoQ']``)."""
        if self._acdtool is None:
            return float('nan')
        output_data = self._acdtool.output_data
        section = spec[0]
        if section == 'RoverQ':
            mode, entry = spec[1], spec[2]
            assert entry in {'Frequency', 'Qext', 'V_r', 'V_i', 'absV', 'RoQ'}, \
                "Unknown expression '" + entry + "' in 'RoverQ' section."
            return output_data[section][mode][entry]
        if section == 'kickFactor':
            mode, entry = spec[1], spec[2]
            assert entry in {'Frequency', 'Qext', 'Ks', 'V_r', 'V_i', 'absV'}, \
                "Unknown expression '" + entry + "' in 'kickFactor' section."
            return output_data[section][mode][entry]
        if section == 'maxFieldsOnSurface':
            surface, entry = spec[1], spec[2]
            assert entry in {'Emax', 'Emax_location', 'Hmax', 'Hmax_location'}, \
                "Unknown expression '" + entry + "' in 'maxFieldsOnSurface' section."
            if entry.endswith('location'):
                component = spec[3]
                return output_data[section][surface][entry][component]
            return output_data[section][surface][entry]
        raise ValueError("Unknown section name '" + str(section) + "' in output dict.")


# --------------------------------------------------------------------------- #
# Particles (field-emission weighting)
# --------------------------------------------------------------------------- #


class ParticlesModule(Module):
    """Requires ``track3p_particles``, provides ``particle_source``.

    Owns the ``beta`` / ``beta_input`` / ``beta_inputs`` resolution. Always runs
    (the field-emission weighting is pure Python and produces real numbers), even
    under dry-run — the Geant4 binary is the only thing a dry run skips, so the
    particle source it consumes is always produced."""

    type = 'particles'
    requires = frozenset({TRACK3P_PARTICLES})
    provides = frozenset({PARTICLE_SOURCE})

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.params = dict(self.config)
        self.output_file = self.params.pop('output', None) \
            or self.params.pop('particle_output', None)
        self._filtered = None

    def _resolve_beta(self, inputs):
        """Return the particle params for this run. When ``beta_input``
        (broadcast one input-space variable to all bins) or ``beta_inputs``
        (one variable per bin) is set, build the per-bin beta list from the
        ``particles`` bucket (the field-enhancement scaling belongs to this
        post-Track3P weighting step, not to Cubit). Falls back to the ``cubit``
        bucket for back-compat with legacy configs that declared β there."""
        params = self.params
        beta_input = params.get('beta_input')
        beta_inputs = params.get('beta_inputs')
        if beta_input is None and beta_inputs is None:
            return params
        if beta_input is not None and beta_inputs is not None:
            raise ValueError("Set only one of 'beta_input' or 'beta_inputs'.")
        num_bins = params.get('num_bins')
        if num_bins is None:
            raise ValueError("'num_bins' must be set when using "
                             "'beta_input' or 'beta_inputs'.")

        def beta_value(name):
            if name in inputs.particles:
                return float(inputs.particles[name])
            if name in inputs.cubit:      # back-compat: legacy β under cubit
                return float(inputs.cubit[name])
            raise KeyError(f"beta variable '{name}' not found in "
                           "'input_parameters' (expected under the "
                           "'particles:' bucket).")

        if beta_input is not None:
            effective_beta = [beta_value(beta_input)] * num_bins
        else:
            if len(beta_inputs) != num_bins:
                raise ValueError(f"len(beta_inputs)={len(beta_inputs)} must "
                                 f"equal num_bins={num_bins}.")
            effective_beta = [beta_value(n) for n in beta_inputs]

        params = dict(params)
        params['beta'] = effective_beta
        return params

    def run(self, ctx):
        if TRACK3P_PARTICLES not in ctx.artifacts:
            raise ValueError("module 'particles' requires a track3p_particles "
                             "artifact.")
        src = ctx.artifacts[TRACK3P_PARTICLES]
        base = os.path.basename(src)
        _stage_file(ctx, src)
        params = dict(self._resolve_beta(ctx.inputs))
        params.setdefault('output_format', 'geant4')
        particles = Particles(base, params, output_file=self.output_file,
                              workdir=ctx.workdir)
        self._filtered = particles.run()
        ctx.artifacts[PARTICLE_SOURCE] = os.path.join(ctx.workdir,
                                                      particles.output_file)

    def extract(self, ctx, spec):
        """Expose simple scalars off the weighted/filtered particle set.

        ``spec`` is ``'count'`` (number of filtered particles) or
        ``'total_weight'`` (sum of the field-emission ParticleWeight); a list
        wrapping either, or the target-schema mapping ``{'quantity': ...}``, is
        also accepted."""
        if isinstance(spec, dict):
            spec = spec.get('quantity')
        if isinstance(spec, list):
            spec = spec[0]
        if self._filtered is None:
            return float('nan')
        if spec == 'count':
            return int(len(self._filtered))
        if spec == 'total_weight':
            return float(self._filtered['ParticleWeight'].sum())
        raise ValueError("Unknown particles quantity '" + str(spec) + "'.")


# --------------------------------------------------------------------------- #
# Geant4 dose/edep
# --------------------------------------------------------------------------- #


class Geant4Module(Module):
    """Requires a ``particle_source``, provides ``dose_grid`` / ``edep_grid``.

    Owns ``_geometry_files``, ``_output_files`` and ``_read_scoring_output``."""

    type = 'geant4'
    requires = frozenset({PARTICLE_SOURCE})
    provides = frozenset({DOSE_GRID, EDEP_GRID})

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.geant4_input = self.config.get('geant4_input')
        self.geant4_threads = self.config.get('geant4_threads')
        self.geant4_opts = self.config.get('geant4_opts', '')
        self.geant4_particle_cmd = self.config.get('geant4_particle_cmd', 'particles')
        self.geant4_geometry_files = self.config.get('geant4_geometry_files') or []
        # Output files are normally named in the Geant4 input file
        # (output_dose / output_edep); these allow an explicit YAML override.
        # 'geant4_scoring_output' stays a back-compat alias for the dose file.
        self.geant4_dose_output = (self.config.get('geant4_dose_output')
                                   or self.config.get('geant4_scoring_output'))
        self.geant4_edep_output = self.config.get('geant4_edep_output')
        self.geant4_obj = None

    def run(self, ctx):
        if PARTICLE_SOURCE not in ctx.artifacts:
            raise ValueError("module 'geant4' requires a particle_source "
                             "artifact.")
        ctx.ensure_workdir()
        particle_file_path = ctx.artifacts[PARTICLE_SOURCE]
        macro_inputs = dict(ctx.inputs.macro) if ctx.inputs.macro else None

        # Build the Geant4 object first so we can read the input file's own
        # settings (STL geometry names, output filenames) before copying files.
        self.geant4_obj = None
        if self.geant4_input is not None:
            self.geant4_obj = Geant4(self.geant4_input,
                                     geant4_threads=self.geant4_threads or 1,
                                     geant4_opts=self.geant4_opts,
                                     workdir=ctx.workdir,
                                     mpi_caller=ctx.paths.get('mpi', ''),
                                     geant4_app_path=ctx.paths.get('geant4_app_path', ''),
                                     geant4_app_exe=ctx.paths.get('geant4_app_exe', ''))
            # Threads default is owned by the input file; only override when set.
            if self.geant4_threads is not None:
                self.geant4_obj.set_value({'nthreads': self.geant4_threads})
            else:
                # 'geant4_threads' drives the srun '-c' (CPUs reserved for the
                # step); when it is unset srun reserves only 1 CPU, but Geant4
                # still spawns the input file's 'nthreads' threads. If those
                # threads exceed the reserved CPU they contend for one core and
                # the run is slow. Warn when the two disagree.
                file_nthreads = self.geant4_obj.get_value('nthreads')
                try:
                    file_nthreads = int(file_nthreads)
                except (TypeError, ValueError):
                    file_nthreads = None
                # nthreads = 0 means Geant4 auto-detects all available cores,
                # which also disagrees with the single reserved CPU.
                if file_nthreads is not None and file_nthreads != 1:
                    detail = ('auto-detects all available cores'
                              if file_nthreads == 0
                              else f'spawns {file_nthreads} threads')
                    print("Warning: 'geant4_threads' is not set, so srun "
                          "reserves only 1 CPU for the Geant4 step, but the "
                          f"input file's 'nthreads = {file_nthreads}' "
                          f"{detail}. These threads will contend for a single "
                          "CPU. Set 'geant4_threads' in the geant4 module to "
                          "match 'nthreads'.")
            if particle_file_path is not None:
                self.geant4_obj.set_particle_file(
                    particle_file_path,
                    macro_value=os.path.basename(particle_file_path),
                    particle_cmd=self.geant4_particle_cmd)
            if macro_inputs:
                self.geant4_obj.set_value(macro_inputs)

        # Geometry files: union of any '*_stl' values named in the input file
        # and the explicit geant4_geometry_files list, de-duplicated by basename.
        geom_files = self._geometry_files()
        for geom in geom_files:
            _stage_file(ctx, geom)

        if ctx.dry_run:
            _append_marker(ctx, 'Dry run mode: Geant4 step skipped.\n'
                                f'Input file: {self.geant4_input}\n'
                                f'Particle file: {particle_file_path}\n'
                                f'Geometry files: {geom_files}\n'
                                f'Output files: {self._output_files()}\n'
                                f'Threads: {self.geant4_threads}\n'
                                f'Particles: {ctx.inputs.particles}\n'
                                f'Input overrides: {macro_inputs}\n')
            if self.geant4_obj is not None:
                self.geant4_obj.write_input()
            self._record_grid_artifacts(ctx)
            return

        self.geant4_obj.run()
        self._record_grid_artifacts(ctx)

    def _record_grid_artifacts(self, ctx):
        files = self._output_files()
        if files['dose']:
            ctx.artifacts[DOSE_GRID] = os.path.join(ctx.workdir, files['dose'])
        if files['edep']:
            ctx.artifacts[EDEP_GRID] = os.path.join(ctx.workdir, files['edep'])

    def _geometry_files(self):
        """Union of STL files named in the Geant4 input file ('*_stl' keys)
        and the explicit geant4_geometry_files list. Input-file names are
        resolved relative to the directory of geant4_input. De-duplicated by
        basename."""
        files = []
        seen = set()

        def add(path):
            base = os.path.basename(path)
            if base and base not in seen:
                seen.add(base)
                files.append(path)

        if self.geant4_obj is not None:
            input_dir = os.path.dirname(self.geant4_input)
            for key, value in self.geant4_obj.get_values().items():
                if key.endswith('_stl') and value:
                    candidate = os.path.join(input_dir, value) if input_dir else value
                    if os.path.isfile(candidate):
                        add(candidate)
                    elif os.path.isfile(value):
                        add(value)
        for geom in self.geant4_geometry_files:
            add(geom)
        return files

    def _output_files(self):
        """Resolve the dose / edep output filenames, preferring explicit YAML
        overrides and otherwise reading output_dose / output_edep from the
        Geant4 input file."""
        values = self.geant4_obj.get_values() if self.geant4_obj is not None else {}
        dose = self.geant4_dose_output or values.get('output_dose')
        edep = self.geant4_edep_output or values.get('output_edep')
        return {'dose': dose, 'edep': edep}

    def _read_scoring_output(self, ctx, filename):
        """Parse a whitespace ix iy iz value scoring file into
        ``{'indices': (M,3) array, 'values': (M,) array}`` (workdir from ctx).

        Delegates to :func:`lume_ace3p.surrogate_data.read_dose_file`, the single
        canonical dose parser — the surrogate/inversion path reads target dose
        files through the same code, so a target lines up bin-for-bin with the
        stored training grids."""
        if not filename:
            return None
        from lume_ace3p.surrogate_data import read_dose_file
        return read_dose_file(os.path.join(ctx.workdir, filename))

    def extract(self, ctx, spec):
        """Extract a scalar from the Geant4 dose/edep scoring output.

        ``spec`` is ``[section, entry]`` with section in {dose, edep, scoring}
        (``scoring`` is a back-compat alias for dose) and entry in
        {total, peak, peak_index}."""
        files = self._output_files()
        grids = {
            'dose': self._read_scoring_output(ctx, files['dose']),
            'edep': self._read_scoring_output(ctx, files['edep']),
        }
        grids['scoring'] = grids['dose']
        if not isinstance(spec, list) or len(spec) < 2:
            return float('nan')
        section, entry = spec[0], spec[1]
        if section not in grids:
            raise ValueError("Unknown section name '" + str(section) + "' in output dict.")
        scoring = grids[section]
        if scoring is None:
            return float('nan')
        if entry == 'total':
            return float(np.sum(scoring['values']))
        if entry == 'peak':
            return float(np.max(scoring['values']))
        if entry == 'peak_index':
            idx = int(np.argmax(scoring['values']))
            return tuple(scoring['indices'][idx])
        raise ValueError("Unknown entry '" + str(entry) + "' in '"
                         + str(section) + "' section.")

    def field(self, ctx):
        """Return the Geant4 voxel-grid field outputs for the just-run
        evaluation as ``{'dose': {indices, values}, 'edep': {...}}``, or
        ``None`` when neither scoring file is present (e.g. dry-run).

        These are the ragged 3-D grids the hybrid model keeps out of the flat
        table; the mode layer persists them per row and reloads on demand."""
        files = self._output_files()
        grids = {}
        for section in ('dose', 'edep'):
            grid = self._read_scoring_output(ctx, files[section])
            if grid is not None:
                # 'indices' is already a 2-D (M,3) array, so the field artifact
                # round-trips without pickling.
                grids[section] = {'indices': grid['indices'],
                                  'values': grid['values']}
        return grids or None


# --------------------------------------------------------------------------- #
# Registry — type string -> Module class.
# --------------------------------------------------------------------------- #

MODULE_REGISTRY = {
    CubitModule.type: CubitModule,
    MeshSourceModule.type: MeshSourceModule,
    Omega3PModule.type: Omega3PModule,
    S3PModule.type: S3PModule,
    T3PModule.type: T3PModule,
    AcdtoolModule.type: AcdtoolModule,
    Track3PSourceModule.type: Track3PSourceModule,
    ParticlesModule.type: ParticlesModule,
    ParticleSourceModule.type: ParticleSourceModule,
    Geant4Module.type: Geant4Module,
}


def build_module(module_type, config=None, name=None):
    """Construct a module instance from its registry type string."""
    key = str(module_type).lower()
    if key not in MODULE_REGISTRY:
        raise ValueError(f"Unknown module type '{module_type}'. Known types: "
                         f"{sorted(MODULE_REGISTRY)}.")
    return MODULE_REGISTRY[key](config=config, name=name)
