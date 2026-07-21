"""Training-data store for the Geant4 dose surrogate (Phase 2).

This is the thin persistence + loading layer that backs the
``collect_training_data`` mode (see :func:`lume_ace3p.modes.collect_training_data`)
and, later, the ``train_surrogate`` / ``invert_*`` modes. It is deliberately
*not* a new on-disk format: a training store is just

* a result table written through the shared :func:`lume_ace3p.results.write_table`
  (one row per DOE sample: the ``beta0..betaN`` columns, a recorded ``fidelity``,
  and a :data:`~lume_ace3p.results.FIELD_ARTIFACT_COLUMN` handle), plus
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

# Column name recording each sample's fidelity (Geant4 primary count). Kept as
# an explicit column so Phase 5 multi-fidelity training can filter on it.
FIDELITY_COLUMN = 'fidelity'


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
    * ``fidelity`` — ``(N,)`` recorded fidelity per sample.
    * ``table`` — the raw result :class:`pandas.DataFrame`.
    * ``manifest`` — the parsed manifest dict.
    """

    def __init__(self, beta, beta_names, dose, edep, indices, fidelity,
                 table, manifest):
        self.beta = beta
        self.beta_names = beta_names
        self.dose = dose
        self.edep = edep
        self.indices = indices
        self.fidelity = fidelity
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
    real grids.

    Raises ``FileNotFoundError`` if the store table is missing and
    ``ValueError`` if the stored grids have inconsistent voxel layouts (which
    would mean the fixed-``bin_edges`` invariant was violated mid-campaign)."""
    from lume_ace3p.results import load_field, FIELD_ARTIFACT_COLUMN

    table_path = os.path.join(store_path, TABLE_FILENAME)
    if not os.path.isfile(table_path):
        raise FileNotFoundError(
            f"no training table at {table_path}; run collect_training_data first.")
    table = pd.read_csv(table_path, sep='\t')

    manifest = {}
    manifest_path = os.path.join(store_path, MANIFEST_FILENAME)
    if os.path.isfile(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)

    beta_names = manifest.get('beta_names') or [
        c for c in table.columns if c.startswith('beta')]
    beta = table[beta_names].to_numpy(dtype=float)

    fidelity = (table[FIDELITY_COLUMN].to_numpy()
                if FIDELITY_COLUMN in table.columns
                else np.full(len(table), np.nan))

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
                         table, manifest)


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
