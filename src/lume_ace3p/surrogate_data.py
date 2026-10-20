"""Training-data store for the Geant4 dose surrogate (Phase 2).

This is the thin persistence + loading layer that backs the
``collect_training_data`` mode (see :func:`lume_ace3p.modes.collect_training_data`)
and, later, the ``train_surrogate`` / ``invert_*`` modes. It is deliberately
*not* a new on-disk format: a training store is just

* a result table written through the shared :func:`lume_ace3p.results.write_table`
  (one row per DOE sample: the ``beta0..betaN`` columns, the measured
  ``primaries`` / ``histories_per_macro`` / ``events``, and a :data:`~lume_ace3p.results.FIELD_ARTIFACT_COLUMN` handle), plus
* the per-sample dose/edep voxel grids persisted with
  :func:`lume_ace3p.results.save_field` (a ``.npz`` under each sample workdir),
  reloaded on demand with :func:`lume_ace3p.results.load_field`, plus
* a small ``manifest.json`` recording the shared invariants the GP needs to trust
  the data — the fixed ``bin_edges`` / ``num_bins`` (correctness constraint #1),
  the β variable order, the mesh shape, and the DOE/sampler provenance.

So the ``(β, dose_grid)`` pairing falls straight out of the table + field
artifacts; :func:`load_training_store` is a wrapper over ``load_field`` + the
table that returns an aligned ``β`` matrix and a stacked dose tensor with
consistent shapes.

The design-of-experiments sampler (:func:`sample_beta_doe`) is a scattered
(Latin-Hypercube / Sobol) design over the per-bin β bounds — **not** a tensor
grid, which is combinatorially infeasible in 8-D.
"""

import json
import os
import warnings

import numpy as np
import pandas as pd


# Canonical filenames inside a training store directory.
TABLE_FILENAME = 'training_table.txt'
MANIFEST_FILENAME = 'manifest.json'

# The typed 'fidelity:' label a DOE collection used to record per sample. It
# is not what the run did (the 2026-09 store wrote 10000 for runs of 82k to
# 134k primaries), so it is deprecated in favour of the measured columns below,
# and kept only so old stores and configs load.
FIDELITY_COLUMN = 'fidelity'

# Measured fidelity per run (dose campaign constraint #4): rows of the particle
# source, the Geant4 input's histories_per_macro, and their product.
PRIMARIES_COLUMN = 'primaries'
HPM_COLUMN = 'histories_per_macro'
EVENTS_COLUMN = 'events'
MEASURED_FIDELITY_COLUMNS = (PRIMARIES_COLUMN, HPM_COLUMN, EVENTS_COLUMN)
# The dose-npass input keys for histories_per_macro, in its own precedence
# order; absent means 1.
HPM_KEYS = ('histories_per_macro', 'nhistories', 'passes')


# --------------------------------------------------------------------------- #
# Dose scoring-mesh fingerprint (correctness constraint #3).
#
# PCA/POD stacks every run's dose grid into one (N, M) matrix and runs SVD in
# that shared R^M. That is only meaningful if row i, column j is the SAME
# physical voxel for all i — i.e. the scoring mesh (per-axis bin counts, physical
# extent, center) is identical across the whole campaign. A drifting mesh
# misaligns the POD basis exactly the way drifting bin_edges misaligns the input
# map. The mesh is defined by `mesh_nx/ny/nz` (bin counts), `mesh_cx/cy/cz`
# (center, mm) and `mesh_x/y/z` (half-sizes, mm) in the Geant4 input file, so a
# fingerprint is a cheap parse — no dose grid required, so it works under dry-run.
# --------------------------------------------------------------------------- #

# The nine scoring-mesh keys, grouped into the fingerprint fields they populate.
_MESH_BIN_KEYS = ('mesh_nx', 'mesh_ny', 'mesh_nz')
_MESH_CENTER_KEYS = ('mesh_cx', 'mesh_cy', 'mesh_cz')
_MESH_HALF_KEYS = ('mesh_x', 'mesh_y', 'mesh_z')


def _parse_key_value_file(path):
    """Parse a Geant4 ``key = value`` input file into a dict.

    Blank lines and ``#`` comments are skipped; only the first ``=`` splits a
    line. Self-contained (no dependency on the ``plotting/`` scripts, which are
    not an importable package) and tolerant of the same file format
    :class:`lume_ace3p.geant4.Geant4` reads."""
    kv = {}
    with open(path) as f:
        for line in f:
            text = line.strip()
            if not text or text.startswith('#') or '=' not in text:
                continue
            key, value = text.split('=', 1)
            key = key.strip()
            if key:
                kv[key] = value.strip()
    return kv


def read_mesh_fingerprint(geant4_input_path):
    """Return a canonical dose-mesh fingerprint from a Geant4 input file.

    The fingerprint is a plain dict::

        {'bins': [nx, ny, nz], 'center': [cx, cy, cz], 'half': [hx, hy, hz]}

    which fully determines the voxel geometry (extent = 2·half, per-axis voxel
    size = 2·half / bins). Returns ``None`` if the path is missing/unreadable or
    any of the nine ``mesh_*`` keys is absent or non-numeric — the caller decides
    whether that is fatal (it is, for a real Geant4 run; see
    :func:`lume_ace3p.modes._require_fixed_mesh`). This only reads the input
    file, so it is available under dry-run."""
    if not geant4_input_path or not os.path.isfile(geant4_input_path):
        return None
    kv = _parse_key_value_file(geant4_input_path)
    try:
        bins = [int(float(kv[k])) for k in _MESH_BIN_KEYS]
        center = [float(kv[k]) for k in _MESH_CENTER_KEYS]
        half = [float(kv[k]) for k in _MESH_HALF_KEYS]
    except (KeyError, ValueError, TypeError):
        return None
    if any(n <= 0 for n in bins):
        return None
    return {'bins': bins, 'center': center, 'half': half}


def mesh_fingerprints_match(a, b):
    """True iff two mesh fingerprints describe the same voxel geometry.

    ``None`` matches only ``None``. Bin counts compare exactly; the physical
    center/half compare with a tiny tolerance so a reformatted ``60`` vs
    ``60.0`` does not read as drift."""
    if a is None or b is None:
        return a is None and b is None
    if list(a.get('bins', [])) != list(b.get('bins', [])):
        return False
    for field in ('center', 'half'):
        av = np.asarray(a.get(field, []), dtype=float)
        bv = np.asarray(b.get(field, []), dtype=float)
        if av.shape != bv.shape or not np.allclose(av, bv, rtol=0.0, atol=1e-9):
            return False
    return True


# --------------------------------------------------------------------------- #
# Design-of-experiments sampler over the β space.
# --------------------------------------------------------------------------- #


def sample_beta_doe(bounds, num_samples, sampler='sobol', seed=0):
    """Return an ``(num_samples, D)`` array of β design points.

    ``bounds`` is a list of ``(lo, hi)`` pairs, one per β dimension (bin). The
    design is a scattered quasi-random sample scaled into those bounds, not a
    tensor grid — a full 8-D grid is combinatorially infeasible.

    ``sampler`` is ``'sobol'`` (default) or ``'lhs'`` /
    ``'latin_hypercube'``; ``seed`` makes the design reproducible so a resumed
    run reproduces the same points."""
    from scipy.stats import qmc

    bounds = [tuple(b) for b in bounds]
    dim = len(bounds)
    if dim == 0:
        raise ValueError("sample_beta_doe needs at least one β dimension.")
    if num_samples <= 0:
        raise ValueError("num_samples must be positive.")
    lo = np.array([b[0] for b in bounds], dtype=float)
    hi = np.array([b[1] for b in bounds], dtype=float)
    if np.any(hi <= lo):
        raise ValueError("each β bound must have hi > lo; got "
                         f"{list(bounds)}.")

    name = str(sampler).lower()
    if name == 'sobol':
        engine = qmc.Sobol(d=dim, scramble=True, seed=seed)
        # Sobol is balanced only at power-of-2 sample counts; a non-power-of-2
        # draw is still a valid (if slightly less uniform) design, so silence
        # the advisory rather than forcing the user to round num_samples.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            unit = engine.random(num_samples)
    elif name in ('lhs', 'latin_hypercube'):
        engine = qmc.LatinHypercube(d=dim, seed=seed)
        unit = engine.random(num_samples)
    else:
        raise ValueError(f"unknown sampler '{sampler}'. Use 'sobol' or 'lhs'.")

    return qmc.scale(unit, lo, hi)


# --------------------------------------------------------------------------- #
# Target-dose loading + voxel alignment (the inversion input seam, Phase 4).
#
# Inverting a dose profile means projecting it onto the PCA basis, which is a
# plain ``(dose - mean) @ Φ^T``: it assumes column j of the input is the SAME
# physical voxel as column j of the training grids. A raw Geant4 dose file lists
# voxels in whatever order it likes, so a target MUST be reordered onto the
# training voxel order before projection — otherwise the whole inversion is
# silently misaligned. This is the output-side analogue of the collection-time
# mesh pinning (correctness constraint #3).
#
# ``read_dose_file`` is the ONE canonical dose parser: it produces the
# ``{'indices', 'values'}`` shape that :func:`lume_ace3p.results.save_field`
# persists and the PCA basis is therefore built on.
# :meth:`lume_ace3p.modules.Geant4Module._read_scoring_output` delegates to it.
# (``plotting/geant4_deposit_common.parse_deposit_file`` returns a *different*
# shape — reshaped grids + mesh/units metadata — and stays for header metadata
# only; it is not the vector fed to ``project()``.)
# --------------------------------------------------------------------------- #


def read_dose_file(path):
    """Parse a Geant4 dose/edep scoring file into ``{'indices', 'values'}``.

    Reads the whitespace-or-comma ``ix iy iz value [...]`` voxel format, skipping
    blank lines, ``#`` comments and short rows — so it handles both the plain
    scoring dump and the comma-separated
    ``iX, iY, iZ, total(value), total(val^2), entry`` variant. Returns
    ``{'indices': (M,3) int array, 'values': (M,) float array}``, plus ``'val2'``
    (the ``total(val^2)`` column, the sum over events of each voxel's squared
    per-event dose) when every row has one, or ``None`` if the file is missing or
    holds no data rows. :func:`voxel_variance` turns ``val2`` into a variance.

    This is the canonical parser for training and inversion: the returned shape is
    exactly what ``save_field`` persists, so a target parsed here lines up with the
    stored training grids (after :func:`align_to_indices`)."""
    if not path or not os.path.isfile(path):
        return None
    indices = []
    values = []
    val2 = []
    with open(path) as f:
        for line in f:
            text = line.strip()
            if not text or text.startswith('#'):
                continue
            parts = text.replace(',', ' ').split()
            if len(parts) < 4:
                continue
            try:
                ix, iy, iz = int(parts[0]), int(parts[1]), int(parts[2])
                value = float(parts[3])
            except ValueError:
                continue
            indices.append((ix, iy, iz))
            values.append(value)
            try:
                val2.append(float(parts[4]))
            except (IndexError, ValueError):
                val2.append(None)
    if not values:
        return None
    grid = {'indices': np.asarray(indices, dtype=int),
            'values': np.asarray(values, dtype=float)}
    if all(v is not None for v in val2):
        grid['val2'] = np.asarray(val2, dtype=float)
    return grid


def voxel_variance(values, val2, events):
    """Per-voxel Monte Carlo variance of a dose total over ``events`` events.

    ``val2`` is the dose file's ``total(val^2)`` column, so the variance of the
    voxel total is ``val2 − values² / events``, clipped at zero. ``events`` is
    the number of histories the run launched (primaries × histories_per_macro),
    not the ``entry`` column: an event that deposits nothing in a voxel is still
    a sample of it."""
    values = np.asarray(values, dtype=float)
    return np.maximum(np.asarray(val2, dtype=float) - values ** 2 / float(events),
                      0.0)


def load_target_dose(target):
    """Load a target dose profile for inversion.

    ``target`` is either a stored field artifact (a ``.npz`` written by
    :func:`lume_ace3p.results.save_field` — e.g. a held-out training sample's
    ``field.npz``, which makes the recovery test trivial) or a raw Geant4 dose
    file. Returns ``(values (M,), indices (M,3))`` in the file's own order — call
    :func:`align_to_indices` to put it on the training voxel order before
    projecting.

    Raises ``FileNotFoundError`` if the path does not exist and ``ValueError`` if
    it carries no usable dose grid."""
    if not target:
        raise ValueError(
            "invert_optimize requires a 'target' dose (a stored field .npz or a "
            "raw Geant4 dose file).")
    if not os.path.isfile(target):
        raise FileNotFoundError(f"target dose file not found: {target}")

    if target.endswith('.npz'):
        from lume_ace3p.results import load_field
        field = load_field(target)
        section = (field or {}).get('dose') or (field or {}).get('edep')
        if section is None:
            raise ValueError(
                f"stored field artifact '{target}' has no dose/edep grid.")
        return (np.asarray(section['values'], dtype=float),
                np.asarray(section['indices'], dtype=int))

    grid = read_dose_file(target)
    if grid is None:
        raise ValueError(
            f"no dose data rows found in target file '{target}'. Expected a "
            "Geant4 scoring dump with 'ix iy iz value' rows.")
    return grid['values'], grid['indices']


def align_to_indices(values, indices, reference_indices):
    """Reorder a target dose onto the training grid's voxel order.

    ``values``/``indices`` are the target as loaded; ``reference_indices`` is the
    ``(M,3)`` voxel order the PCA basis was built on (the training store's
    ``indices``). Returns a ``(M,)`` value array whose column *j* is the voxel
    ``reference_indices[j]`` — exactly what ``project()`` expects.

    Hard-fails if the target does not cover the reference voxel set exactly: a
    missing or extra voxel means the target is on a different scoring mesh, and
    projecting it would silently misalign the basis (correctness constraint #3).
    Reordering is by ``(ix, iy, iz)`` key, so the target's own row order is
    irrelevant."""
    values = np.asarray(values, dtype=float).ravel()
    indices = np.asarray(indices, dtype=int)
    reference = np.asarray(reference_indices, dtype=int)
    if indices.ndim != 2 or indices.shape[1] != 3:
        raise ValueError(
            f"target voxel indices must be (M,3); got {indices.shape}.")
    if reference.ndim != 2 or reference.shape[1] != 3:
        raise ValueError(
            f"reference voxel indices must be (M,3); got {reference.shape}.")
    if values.shape[0] != indices.shape[0]:
        raise ValueError(
            f"target has {values.shape[0]} values but {indices.shape[0]} voxel "
            "indices.")

    lookup = {tuple(int(c) for c in row): j for j, row in enumerate(indices)}
    if len(lookup) != indices.shape[0]:
        raise ValueError(
            "target dose lists the same voxel more than once; cannot align it "
            "to the training grid unambiguously.")

    missing = []
    order = np.empty(reference.shape[0], dtype=int)
    for j, row in enumerate(reference):
        key = tuple(int(c) for c in row)
        pos = lookup.get(key)
        if pos is None:
            missing.append(key)
            if len(missing) > 5:
                break
        else:
            order[j] = pos
    if missing:
        raise ValueError(
            f"target dose is missing {len(missing)}+ voxel(s) present in the "
            f"training grid (e.g. {missing[:5]}); it is on a different scoring "
            "mesh than the surrogate was trained on. The PCA basis would be "
            "misaligned (constraint #3) — re-score the target on the training "
            "mesh.")
    if indices.shape[0] != reference.shape[0]:
        raise ValueError(
            f"target dose has {indices.shape[0]} voxels but the training grid "
            f"has {reference.shape[0]}; it covers a different scoring mesh "
            "(constraint #3).")
    return values[order]


# --------------------------------------------------------------------------- #
# Manifest.
# --------------------------------------------------------------------------- #


def write_manifest(store_path, manifest):
    """Write the store manifest (shared invariants + DOE provenance) as JSON."""
    if not os.path.isdir(store_path):
        os.makedirs(store_path, exist_ok=True)
    path = os.path.join(store_path, MANIFEST_FILENAME)
    with open(path, 'w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True, default=_json_default)
        f.write('\n')
    return path


def _json_default(obj):
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f'cannot serialize manifest value of type {type(obj)!r}')


# --------------------------------------------------------------------------- #
# Loading a store back into aligned arrays.
# --------------------------------------------------------------------------- #


class TrainingStore:
    """Aligned view over a collected training store.

    Attributes:

    * ``beta`` — ``(N, D)`` array of β design points (columns in ``beta_names``
      order).
    * ``beta_names`` — the ordered β column names (``beta0..``).
    * ``dose`` — ``(N, M)`` array of flattened dose voxel values (rows aligned
      with ``beta``), or ``None`` when no sample carried a dose grid (e.g. a
      dry-run store).
    * ``edep`` — ``(N, M)`` energy-deposit values, or ``None``.
    * ``indices`` — ``(M, 3)`` shared voxel ``(ix, iy, iz)`` index array for the
      dose/edep columns, or ``None``.
    * ``fidelity`` — ``(N,)`` the deprecated typed ``fidelity`` label per sample
      (NaN when none was given).
    * ``primaries`` / ``histories_per_macro`` / ``events`` — ``(N,)`` measured
      per run; NaN for a store collected before they were recorded.
    * ``table`` — the raw result :class:`pandas.DataFrame`.
    * ``manifest`` — the parsed manifest dict.
    """

    def __init__(self, beta, beta_names, dose, edep, indices, fidelity,
                 table, manifest, primaries=None, histories_per_macro=None,
                 events=None):
        self.beta = beta
        self.beta_names = beta_names
        self.dose = dose
        self.edep = edep
        self.indices = indices
        self.fidelity = fidelity
        nan = np.full(len(beta), np.nan)
        self.primaries = nan if primaries is None else primaries
        self.histories_per_macro = (nan if histories_per_macro is None
                                    else histories_per_macro)
        self.events = nan if events is None else events
        self.table = table
        self.manifest = manifest

    def __len__(self):
        return int(self.beta.shape[0])


def load_training_store(store_path):
    """Load a training store into aligned arrays in one call.

    Reads the result table + manifest, reloads each row's field artifact via
    :func:`lume_ace3p.results.load_field`, and stacks the dose (and edep) grids
    into ``(N, M)`` tensors aligned with the ``(N, D)`` β matrix. Rows with no
    stored field (dry-run) contribute to ``beta`` but leave the dose tensor
    ``None`` (there is nothing to stack) — the Phase-3 trainer is what requires
    real grids. Rows whose ``status`` is ``failed`` (a sample that failed under
    ``on_error: skip``) are left out.

    Raises ``FileNotFoundError`` if the store table is missing and
    ``ValueError`` if the stored grids have inconsistent voxel layouts (which
    would mean the fixed-``bin_edges`` invariant was violated mid-campaign)."""
    from lume_ace3p.results import (
        load_field, FIELD_ARTIFACT_COLUMN, STATUS_COLUMN)

    table_path = os.path.join(store_path, TABLE_FILENAME)
    if not os.path.isfile(table_path):
        raise FileNotFoundError(
            f"no training table at {table_path}; run collect_training_data first.")
    table = pd.read_csv(table_path, sep='\t')
    if STATUS_COLUMN in table.columns:
        table = table[table[STATUS_COLUMN] != 'failed'].reset_index(drop=True)

    manifest = {}
    manifest_path = os.path.join(store_path, MANIFEST_FILENAME)
    if os.path.isfile(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)

    beta_names = manifest.get('beta_names') or [
        c for c in table.columns if c.startswith('beta')]
    beta = table[beta_names].to_numpy(dtype=float)

    def column(name):
        return (table[name].to_numpy(dtype=float) if name in table.columns
                else np.full(len(table), np.nan))

    fidelity = column(FIDELITY_COLUMN)

    dose_rows, edep_rows = [], []
    indices = None
    have_any_field = False
    for handle in table.get(FIELD_ARTIFACT_COLUMN, pd.Series([None] * len(table))):
        field = load_field(handle) if _is_handle(handle) else None
        if field is None:
            dose_rows.append(None)
            edep_rows.append(None)
            continue
        have_any_field = True
        indices = _check_indices(indices, field)
        dose_rows.append(_values(field, 'dose'))
        edep_rows.append(_values(field, 'edep'))

    dose = _stack_rows(dose_rows) if have_any_field else None
    edep = _stack_rows(edep_rows) if have_any_field else None
    if indices is not None:
        _check_indices_against_manifest(indices, manifest)
    return TrainingStore(beta, list(beta_names), dose, edep, indices, fidelity,
                         table, manifest, primaries=column(PRIMARIES_COLUMN),
                         histories_per_macro=column(HPM_COLUMN),
                         events=column(EVENTS_COLUMN))


def _is_handle(handle):
    if handle is None or handle == '':
        return False
    if isinstance(handle, float) and np.isnan(handle):
        return False
    return True


def _values(field, section):
    section_dict = field.get(section)
    if section_dict is None:
        return None
    return np.asarray(section_dict['values'], dtype=float)


def _check_indices(indices, field):
    """Return the shared voxel index array, verifying every sample's grid uses
    the same voxel layout (a moving layout would break the PCA basis).

    Compares the voxel ``(ix, iy, iz)`` index arrays **bin-for-bin**, not merely
    by shape: two physically different meshes can share a voxel count yet map
    column j to different voxels, which would silently misalign the POD basis
    (correctness constraint #3)."""
    dose = field.get('dose') or field.get('edep')
    if dose is None:
        return indices
    these = np.asarray(dose['indices'])
    if indices is None:
        return these
    prev = np.asarray(indices)
    if these.shape != prev.shape:
        raise ValueError(
            "training grids have inconsistent voxel layouts across samples "
            f"({these.shape} vs {prev.shape}); the shared bin_edges / scoring "
            "mesh must be fixed for the whole campaign (constraint #3).")
    if not np.array_equal(these, prev):
        raise ValueError(
            "training grids share a voxel count but disagree on the voxel "
            "index layout across samples; the scoring mesh drifted mid-campaign "
            "(constraint #3) — the PCA basis would be misaligned. Re-collect "
            "with a fixed mesh, or resample onto a common reference grid.")
    return indices


def _check_indices_against_manifest(indices, manifest):
    """Cross-check the stacked voxel count against the manifest mesh fingerprint.

    The per-sample ``_check_indices`` already guarantees every row shares one
    voxel layout; this adds the independent check that the layout matches the
    geometry the manifest claims was pinned (``mesh['bins']`` → nx·ny·nz voxels).
    A mismatch means the stored grids and the recorded mesh contract disagree —
    surface it rather than train on a silently wrong basis (constraint #3)."""
    mesh = manifest.get('mesh') if isinstance(manifest, dict) else None
    if not mesh:
        return
    bins = mesh.get('bins')
    if not bins:
        return
    expected = int(np.prod([int(b) for b in bins]))
    actual = int(np.asarray(indices).shape[0])
    if actual != expected:
        raise ValueError(
            f"stored dose grids have {actual} voxels but the manifest mesh "
            f"fingerprint {list(bins)} implies {expected}; the scoring mesh and "
            "the recorded contract disagree (constraint #3).")


def _stack_rows(rows):
    """Stack per-sample value vectors into an ``(N, M)`` array, requiring every
    populated row to share the same length. Rows with no field (``None``) are
    dropped only if *all* rows are ``None`` (handled by the caller); otherwise a
    missing grid among real ones is an error worth surfacing."""
    lengths = {len(r) for r in rows if r is not None}
    if not lengths:
        return None
    if len(lengths) > 1:
        raise ValueError(
            f"training dose grids differ in voxel count across samples: {lengths}.")
    (width,) = lengths
    filled = [r if r is not None else np.full(width, np.nan) for r in rows]
    return np.vstack(filled)


# --------------------------------------------------------------------------- #
# Per-bin store: one 1-D β sweep per bin, the other bins switched off. This is
# what an additive surrogate (lume_ace3p.surrogate_additive) trains on; see
# plans/geant4_dose_campaign_plan.md.
# --------------------------------------------------------------------------- #

PER_BIN_DESIGN = 'per_bin'
BIN_COLUMN = 'bin'


def per_bin_beta(beta_value, b, num_bins, off_beta):
    """The β vector of the run that sets bin ``b`` to ``beta_value`` and every
    other bin to ``off_beta``."""
    beta = [float(off_beta)] * int(num_bins)
    beta[b] = float(beta_value)
    return beta


def plan_per_bin_primaries(dump_path, particle_params, grid, off_beta):
    """Primaries each per-bin run will launch, from the Track3P dump alone.

    Reweights the dump once per ``(bin, β)`` without running anything and
    counts the rows the Geant4 source file would hold. Returns ``(B, n)``
    primaries by emitting bin. Raises when a run would not isolate its bin (a
    switched-off bin still emits: ``off_beta`` too high) or would launch nothing
    (the grid starts below the bin's emission onset; size the grid against the
    dump)."""
    from lume_ace3p.particles import Particles

    params = dict(particle_params)
    num_bins = int(params['num_bins'])
    params['beta'] = [float(off_beta)] * num_bins
    params['output_format'] = 'geant4'
    particles = Particles(os.path.abspath(dump_path), params,
                          output_file='planned_primaries.unused',
                          workdir=os.path.dirname(os.path.abspath(dump_path)))
    particles.load()
    particles.filter_particles()
    particles.assign_bins()

    primaries = np.zeros((num_bins, len(grid)), dtype=int)
    for b in range(num_bins):
        for i, value in enumerate(grid):
            particles.beta = np.asarray(
                per_bin_beta(value, b, num_bins, off_beta), dtype=float)
            particles.calculate_particle_weight()
            counts = np.bincount(particles.geant4_rows()['Bin'],
                                 minlength=num_bins)
            leaked = [k for k in range(num_bins) if k != b and counts[k]]
            if leaked:
                raise ValueError(
                    f"off_beta={off_beta} does not switch bins off: the run for "
                    f"bin {b} at beta {value} still emits from bins {leaked}. "
                    "Lower off_beta (1.0 zeroes every weight under the "
                    "Fowler-Nordheim models).")
            primaries[b, i] = counts[b]
    dark = [(int(b), float(grid[i])) for b, i in zip(*np.where(primaries == 0))]
    if dark:
        raise ValueError(
            f"{len(dark)} per-bin run(s) would launch no primaries, e.g. (bin, "
            f"beta) {dark[:4]}: the grid starts below where those bins emit. "
            "Start the grid higher; a beta range is sized against its dump.")
    return primaries


class PerBinStore:
    """A per-bin training store as the arrays
    :meth:`~lume_ace3p.surrogate_additive.AdditiveSurrogate.fit` takes.

    ``grid`` ``(n,)``; ``dose`` and ``variance`` ``(B, n, M)``; ``primaries`` and
    ``histories_per_macro`` ``(B, n)``; ``indices`` ``(M, 3)``; plus
    ``beta_names``, ``manifest`` and the raw ``table``."""

    def __init__(self, grid, dose, variance, primaries, histories_per_macro,
                 indices, beta_names, manifest, table):
        self.grid = grid
        self.dose = dose
        self.variance = variance
        self.primaries = primaries
        self.histories_per_macro = histories_per_macro
        self.indices = indices
        self.beta_names = beta_names
        self.manifest = manifest
        self.table = table


def load_per_bin_store(store_path):
    """Load a per-bin store written by ``collect_training_data`` with
    ``design: per_bin``.

    Every ``(bin, grid point)`` must have a stored field with a ``val2``
    column, and all grids must share one voxel order; a gap names the runs to
    collect again (``on_error: skip`` leaves failed runs out, and a re-run
    retries them)."""
    from lume_ace3p.results import (
        load_field, FIELD_ARTIFACT_COLUMN, STATUS_COLUMN)

    table_path = os.path.join(store_path, TABLE_FILENAME)
    manifest_path = os.path.join(store_path, MANIFEST_FILENAME)
    if not os.path.isfile(table_path) or not os.path.isfile(manifest_path):
        raise FileNotFoundError(
            f"no per-bin store at {store_path} (needs {TABLE_FILENAME} and "
            f"{MANIFEST_FILENAME}); run collect_training_data with "
            "'design: per_bin' first.")
    with open(manifest_path) as f:
        manifest = json.load(f)
    if manifest.get('design') != PER_BIN_DESIGN:
        raise ValueError(
            f"store '{store_path}' was collected with design "
            f"'{manifest.get('design', 'doe')}', not '{PER_BIN_DESIGN}'; an "
            "additive surrogate trains on one 1-D sweep per bin.")
    table = pd.read_csv(table_path, sep='\t')
    grid = np.asarray(manifest['grid'], dtype=float)
    beta_names = list(manifest['beta_names'])
    num_bins, num_points = len(beta_names), grid.size

    cells = {}
    for _, row in table.iterrows():
        if STATUS_COLUMN in table.columns and row[STATUS_COLUMN] == 'failed':
            continue
        handle = row.get(FIELD_ARTIFACT_COLUMN)
        if not _is_handle(handle):
            continue
        b = int(row[BIN_COLUMN])
        i = int(np.argmin(np.abs(grid - float(row[beta_names[b]]))))
        cells[b, i] = row

    missing = [(b, float(grid[i])) for b in range(num_bins)
               for i in range(num_points) if (b, i) not in cells]
    if missing:
        raise ValueError(
            f"per-bin store '{store_path}' is missing {len(missing)} run(s), "
            f"e.g. (bin, beta) {missing[:4]}. Run collect_training_data again "
            "to fill them in.")

    dose = variance = None
    indices = None
    primaries = np.zeros((num_bins, num_points))
    hpm = np.zeros((num_bins, num_points))
    for (b, i), row in sorted(cells.items()):
        field = load_field(row[FIELD_ARTIFACT_COLUMN])
        section = (field or {}).get('dose')
        if section is None or 'val2' not in section:
            raise ValueError(
                f"run (bin {b}, beta {grid[i]:g}) has no dose grid with a "
                "total(val^2) column; the additive model needs each run's "
                "Monte Carlo variance.")
        if indices is None:
            indices = np.asarray(section['indices'], dtype=int)
            dose = np.empty((num_bins, num_points, indices.shape[0]))
            variance = np.empty_like(dose)
        values = align_to_indices(section['values'], section['indices'], indices)
        val2 = align_to_indices(section['val2'], section['indices'], indices)
        primaries[b, i] = float(row[PRIMARIES_COLUMN])
        hpm[b, i] = float(row[HPM_COLUMN])
        dose[b, i] = values
        variance[b, i] = voxel_variance(values, val2, primaries[b, i] * hpm[b, i])
    _check_indices_against_manifest(indices, manifest)
    return PerBinStore(grid, dose, variance, primaries, hpm, indices,
                       beta_names, manifest, table)
