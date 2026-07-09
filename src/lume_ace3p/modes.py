"""Mode layer for the workflow-modularization refactor (Phase 3).

A *mode* is how a validated :class:`~lume_ace3p.workflow_graph.Workflow` is
*driven*: ``single`` runs it once, ``parameter_sweep`` runs it over a tensor
product of the swept input axes. Modes are deliberately **workflow-agnostic** —
they touch the workflow only through its public seams
(:meth:`Workflow.evaluate`, :meth:`Workflow.sweep_axes`,
:meth:`Workflow.field_index`) and never reach into any solver-specific code. A
new solver family becomes sweepable for free once its module implements
``extract`` / ``field_index``.

Result container = a pandas ``DataFrame`` (the hybrid data model from the plan):
one row per evaluation, columns = swept input variable names + the extracted
scalar outputs. Two shapes:

* **wide / scalar** (Omega3P, Geant4, ...) — one row per grid point; each
  ``output_parameters`` entry is a scalar column. This replaces the legacy
  ``WriteOmega3PDataTable`` path.
* **long / tidy** (S3P) — a module that exposes a shared field index
  (:meth:`Module.field_index`, e.g. ``('Frequency', array)``) emits one row per
  ``(grid-point, frequency)``; each S-parameter output becomes a column aligned
  to that index. This replaces the legacy ``WriteS3PDataTable`` path.

Per-run *field* outputs (S-parameter vectors, dose/edep voxel grids) are NOT
exploded into the scalar table — they stay as structured objects / files
referenced from ``workflow.last_context`` — except the S3P long-format case
above, which the plan calls out as the one tidy-frame exception.

The scalar-table writer is ``DataFrame.to_csv`` (tab-delimited); the manual
``tools.py`` writers are removed in Phase 6, not here.
"""

import numpy as np
import pandas as pd


def run_mode(mode_cfg, workflow, output_spec=None):
    """Dispatch on ``mode_cfg`` type and drive ``workflow``.

    ``mode_cfg`` is the mode configuration mapping. Its type is read from a
    ``type`` key (target schema) or a legacy ``mode`` key. An output-table path
    may be given as ``output_file`` (target schema) or ``sweep_output_file``
    (legacy); when present, the result DataFrame is written there via
    :func:`write_table`.

    ``output_spec`` is accepted for API symmetry but is informational only — the
    workflow already carries its ``output_parameters`` (``workflow.output_spec``)
    and does the extraction inside :meth:`Workflow.evaluate`.

    Returns the result :class:`pandas.DataFrame`."""
    mode_type = str(mode_cfg.get('type') or mode_cfg.get('mode')).lower()
    if mode_type == 'single':
        df = single(workflow)
    elif mode_type == 'parameter_sweep':
        df = parameter_sweep(workflow)
    else:
        raise ValueError(
            f"mode '{mode_type}' is not handled by the Phase-3 mode layer "
            "(single | parameter_sweep).")

    output_file = mode_cfg.get('output_file') or mode_cfg.get('sweep_output_file')
    if output_file:
        write_table(df, output_file)
    return df


def single(workflow):
    """Run the workflow once and return a one-row (or, for a field-indexed
    solver, one-row-per-index) result DataFrame.

    The base ``inputs`` must already be scalar-valued (no swept axes). Input
    columns are the scalar cubit knobs; output columns are the extracted
    ``output_parameters``."""
    input_names = list(workflow.inputs.cubit.keys())
    scalars = [workflow.inputs.cubit[name] for name in input_names]
    outputs = workflow.evaluate(None)
    rows = _rows_for_point(workflow, input_names, scalars, outputs)
    return _frame(workflow, input_names, rows)


def parameter_sweep(workflow):
    """Run the workflow over the tensor product of its swept axes, one row per
    grid point (or per ``(grid-point, field-index)`` for a field-indexed
    solver). Returns the result DataFrame."""
    axes = workflow.sweep_axes()
    input_names = [label for label, _values, _setter in axes]
    tensor = _input_tensor(axes)

    rows = []
    for i in range(tensor.shape[0]):
        scalars = tensor[i].tolist()
        outputs = workflow.evaluate(scalars if axes else None)
        rows.extend(_rows_for_point(workflow, input_names, scalars, outputs))
    return _frame(workflow, input_names, rows)


# --------------------------------------------------------------------------- #
# Row / frame construction — shared by single + parameter_sweep.
# --------------------------------------------------------------------------- #


def _rows_for_point(workflow, input_names, scalars, outputs):
    """Build the result row(s) for one evaluation.

    Wide case: a single row of ``{input: scalar, ..., output: scalar}``.
    Long case (a module exposes a field index, e.g. S3P frequency): one row per
    index value, each output array sampled at that index — the tidy
    ``(inputs..., Frequency, S(m,n)...)`` frame the plan calls out."""
    output_names = list(workflow.output_spec.keys())
    base = dict(zip(input_names, scalars))
    index = workflow.field_index()
    if index is None:
        row = dict(base)
        for name in output_names:
            row[name] = outputs[name]
        return [row]

    label, values = index
    rows = []
    for j in range(len(values)):
        row = dict(base)
        row[label] = values[j]
        for name in output_names:
            row[name] = _sample(outputs[name], j)
        rows.append(row)
    return rows


def _frame(workflow, input_names, rows):
    """Assemble the ordered-column DataFrame. Column order is: swept inputs,
    then the field-index label (long case only), then outputs — matching the
    left-to-right layout of the legacy sweep tables."""
    output_names = list(workflow.output_spec.keys())
    index = workflow.field_index()
    columns = list(input_names)
    if index is not None:
        columns.append(index[0])
    columns += output_names
    return pd.DataFrame(rows, columns=columns)


def _input_tensor(axes):
    """Tensor product of the swept axis grids as an (N, n_axes) array. No axes
    -> a single empty-row (1, 0) tensor (one run with the base inputs)."""
    if not axes:
        return np.zeros((1, 0))
    grids = [values for _label, values, _setter in axes]
    mesh = np.meshgrid(*grids, indexing='ij')
    return np.stack([m.ravel() for m in mesh], axis=1)


def _sample(value, j):
    """Sample the j-th element of a field-indexed output array; pass a scalar
    through unchanged (so a mis-declared scalar output still lands in the row
    rather than raising)."""
    if isinstance(value, np.ndarray):
        return value[j] if value.ndim and value.shape[0] > j else value
    if isinstance(value, (list, tuple)):
        return value[j] if len(value) > j else value
    return value


# --------------------------------------------------------------------------- #
# Writer — the DataFrame.to_csv replacement for the manual tools.py writers.
# --------------------------------------------------------------------------- #


def write_table(df, filename):
    """Write a result DataFrame to a tab-delimited text file. NaNs are rendered
    as ``nan`` (not blank) so the file round-trips through a whitespace reader
    without column drift."""
    df.to_csv(filename, sep='\t', index=False, na_rep='nan')
