"""Consolidated result module (Phase 5) — the hybrid data model's single seam.

This is where the refactor's result plumbing finally converges. Every
result-producing mode (``parameter_sweep``, ``single``, ``scalar_optimize``,
``gp_parameter_sweep``) emits its result table through the **one** writer here
(:func:`write_table`), and the old dict ``sweep_data`` tuple-keyed structure and
the hand-rolled ``tools.py`` writers have been removed entirely — this module is
the single writer.

The *hybrid* split (confirmed 2026-07-08):

* **Result table = pandas DataFrame.** One row per evaluation, columns = swept
  input variable names + extracted scalar outputs, plus an optional
  :data:`FIELD_ARTIFACT_COLUMN` referencing the stored field artifact for that
  row. Written with :func:`write_table` (tab-delimited ``to_csv``).
* **Field outputs keep their structured form.** S3P ``{Frequency, S(m,n)...}``
  arrays and Geant4 ``{indices, values}`` voxel grids are *not* flattened into
  the scalar table — they are ragged/nested. Each row's field is persisted with
  :func:`save_field` (a compact ``.npz``) and reloaded on demand with
  :func:`load_field`, which round-trips the arrays exactly. The one exception is
  the S3P long-format table, where the frequency-indexed values *are* the rows
  (handled in the mode layer via ``Module.field_index``); no field artifact is
  stored for that case.

The ACE3P ``Section`` input tree and ``WorkflowInputs`` are deliberately NOT
DataFrame-ified — they stay structured objects, per the plan.
"""

import json
import os

import numpy as np


# Column name for the optional per-row reference to a stored field artifact
# (a path/handle produced by :func:`save_field`). Present only in the wide
# result table when a module in the chain produces a structured field output
# (e.g. Geant4 voxel grids); absent for the S3P long-format table.
FIELD_ARTIFACT_COLUMN = 'field_artifact'


# --------------------------------------------------------------------------- #
# The single shared result-table writer.
# --------------------------------------------------------------------------- #


def write_table(df, filename):
    """Write a result :class:`pandas.DataFrame` to a tab-delimited text file.

    This is the one writer every result-producing mode routes through — the
    ``DataFrame.to_csv`` replacement for the hand-rolled sweep-table string
    builders. ``X.data`` from an Xopt run is already a DataFrame, so the Xopt
    modes log through here too. NaNs are rendered as ``nan`` (not blank) so the
    file round-trips through a whitespace reader without column drift.
    """
    df.to_csv(filename, sep='\t', index=False, na_rep='nan')


# --------------------------------------------------------------------------- #
# Field-artifact accessors — persist/load a row's structured field output.
# --------------------------------------------------------------------------- #


def _encode_str(text):
    """Encode a str as a uint8 array so it can ride inside an ``.npz`` without
    ``allow_pickle``."""
    return np.frombuffer(text.encode('utf-8'), dtype=np.uint8)


def _decode_str(arr):
    return bytes(arr.tolist()).decode('utf-8')


def save_field(field, path):
    """Persist one row's structured field output to ``path`` as a ``.npz`` and
    return the stored handle (the path, with a ``.npz`` suffix ensured).

    ``field`` is the structured dict a solver module exposes for the just-run
    evaluation — an S3P spectrum (``{'Frequency': array, 'S(0,0)': array, ...,
    'IndexMap': {...}}``) or a Geant4 voxel grid bundle (``{'dose': {'indices':
    [...], 'values': array}, ...}``). Each leaf is stored so :func:`load_field`
    reconstructs it: numeric arrays / index lists as arrays, nested dicts as
    JSON. Returns ``None`` for a ``None``/empty field (so a row with no field
    gets an empty handle rather than a bogus file)."""
    if field is None or (hasattr(field, '__len__') and len(field) == 0):
        return None
    if not path.endswith('.npz'):
        path = path + '.npz'
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    arrays = {}
    kinds = {}
    for key, value in field.items():
        if isinstance(value, dict):
            kinds[key] = 'json'
            arrays['v:' + key] = _encode_str(json.dumps(value, default=_json_default))
        else:
            kinds[key] = 'array'
            arrays['v:' + key] = np.asarray(value)
    arrays['__kinds__'] = _encode_str(json.dumps(kinds))
    np.savez(path, **arrays)
    return path


def load_field(handle):
    """Load a field artifact saved by :func:`save_field` back to the same
    arrays. Returns ``None`` for an empty/absent handle (``None``, ``''`` or a
    NaN placeholder), so a row with no stored field loads cleanly."""
    if handle is None or handle == '':
        return None
    if isinstance(handle, float) and np.isnan(handle):
        return None
    with np.load(handle, allow_pickle=False) as npz:
        kinds = json.loads(_decode_str(npz['__kinds__']))
        field = {}
        for key, kind in kinds.items():
            arr = npz['v:' + key]
            if kind == 'json':
                field[key] = _rehydrate(json.loads(_decode_str(arr)))
            else:
                field[key] = arr
    return field


def _json_default(obj):
    """Fallback for json.dumps on numpy scalars/arrays inside a nested dict
    leaf (e.g. an S3P IndexMap cutoff stored as a numpy float)."""
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f'cannot serialize field leaf of type {type(obj)!r}')


def _rehydrate(value):
    """Recursively turn any nested list back into an ndarray on load, leaving
    dicts/scalars as-is. Keeps a JSON-stored numeric leaf comparable as an
    array."""
    if isinstance(value, list):
        return np.asarray(value)
    if isinstance(value, dict):
        return {k: _rehydrate(v) for k, v in value.items()}
    return value
