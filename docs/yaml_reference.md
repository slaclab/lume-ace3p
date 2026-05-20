# YAML configuration reference

`lume-ace3p` uses Python `dict` objects to control ACE3P workflows, defined
in YAML configuration files. For a parameter sweep:
`workflow_parameters`, `input_parameters`, and `output_parameters` configure
the workflow tasks. For an optimization problem, a workflow dict is used
together with an output dict and Xopt objects. The class objects for
workflow control are initialized with these dictionaries.

## `workflow_parameters`

Controls the workflow task chain (by specifying related input files),
directory management, and other settings.

| Keyword             | Type           | Default        | Description |
|---------------------|----------------|----------------|-------------|
| `cubit_input`       | `str`          | `None`         | Path to the Cubit journal file (`.jou`) used for the workflow. |
| `ace3p_input`       | `str`          | `None`         | Path to the ACE3P input file (e.g. `.omega3p`) used for the workflow. |
| `ace3p_cores`       | `int`          | `1`            | Number of cores per task to use with ACE3P modules. |
| `ace3p_opts`        | `str`          | `''`           | Additional `mpirun`/`srun` arguments when calling ACE3P modules. |
| `ace3p_tasks`       | `int`          | `1`            | Number of MPI tasks to use with ACE3P modules. |
| `rfpost_input`      | `str`          | `None`         | Path to the acdtool rfpost file (`.rfpost`) used for the workflow. |
| `sweep_output`      | `bool`         | `False`        | Toggle writing parameter-sweep output to a text file. |
| `sweep_output_file` | `str`          | `None`         | Path for writing parameter-sweep output. |
| `workdir`           | `str` / `Path` | `os.getcwd()`  | Path to the working directory in which `lume-ace3p` runs. |
| `workdir_mode`      | `str`          | `'manual'`     | `'manual'` (single workflow folder) or `'auto'` (automatic folder generation). |

## `input_parameters` / `cubit_input_parameters`

The `input_parameters` (and equivalently `cubit_input_parameters`) keywords
and values are user-defined. The structure is:

- `input_parameter`: `list`, or `dict` with `min`, `max`, and `num` defined.

If any input keyword's value is a vector-like object (a list), the workflow
can only be run as a parameter sweep — not a single evaluation.

:::{important}
Input dictionary keywords must **exactly** match the variable names in
Cubit journal files.
:::

During parameter sweeping, all possible combinations of the parameters are
evaluated (the full tensor product of all input-parameter vectors). For
example, if three input parameters are provided with lists of lengths 10,
12, and 15, the workflow runs 10 × 12 × 15 = 1800 times.

## `output_parameters`

The `output_parameters` keywords are user-defined, but the values are lists
containing specific strings corresponding to specific outputs in the
acdtool `rfpost.out` file. The structure is:

- `output_name`: list of strings of the form
  `['section', string1, string2, ...]`, with section-specific strings
  following the section name.
  - The `output_name` keyword is an arbitrary string used for column
    headers in parameter-sweep output files or for optimization routines.

Currently supported values:

- `['RoverQ', string1, string2]` — corresponding to the `[RoverQ]` data
  block in `rfpost.out`.
  - `string1`: the mode ID number to be processed (usually starting from
    `'0'`).
  - `string2`: the data column name of the corresponding mode. Must be one
    of `'Frequency'`, `'Qext'`, `'V_r'`, `'V_i'`, `'AbsV'`, or `'RoQ'`.
- `['maxFieldsOnSurface', string1, string2, string3]` — corresponding to
  the `[maxFieldsOnSurface]` data block in `rfpost.out`.
  - `string1`: the surface ID number (defined by the sideset in the Cubit
    journal file).
  - `string2`: the data column name. Must be one of `'Emax'`,
    `'Emax_location'`, `'Hmax'`, or `'Hmax_location'`.
  - `string3`: `'x'`, `'y'`, or `'z'` — the component of the
    `'Emax_location'` or `'Hmax_location'` vector.

More sections and entries will be added in future updates.

## Workflow classes

The {py:class}`~lume_ace3p.workflow.Omega3PWorkflow` class can be
instantiated using only a workflow dict. An `Omega3PWorkflow` object can
run as-is, but no workflow input files will be adjusted in that case.

### Constructor

```python
workflow_object = Omega3PWorkflow(workflow_dict, input_dict=None, output_dict=None)
```

Creates a workflow object from `workflow_dict` and sets input/output dicts
(both optional).

### Methods

- `workflow_object.run(input_dict=None, output_dict=None)` — run the
  workflow using `input_dict` parameter values (overriding the initial
  `input_dict`) and return an output data dict (overriding the initial
  `output_dict`). The `.run()` method does **not** work with input dicts
  containing lists or vectors — use `.run_sweep()` for multi-valued inputs.
- `workflow_object.run_sweep(input_dict=None, output_dict=None)` — run the
  workflow as a parameter sweep using the optionally provided input/output
  dicts (defaults to the initially provided dicts).
- `workflow_object.evaluate(output_dict=None)` — evaluate quantities
  referenced in `output_dict` (defaults to the initially provided dict)
  and return an output data dict.
- `workflow_object.print_sweep_output(filename=None)` — write quantities
  from a parameter sweep to the provided filename (defaults to the
  `sweep_output_file` value provided in the workflow dict).

### Output

- `output_data = workflow_object.evaluate()` — returns a dict with the same
  keywords as the output dict, but with values replaced by numeric
  quantities from evaluation.
- `output_data = workflow_object.run()` — same shape as `.evaluate()`.
- `output_sweep_data = workflow_object.run_sweep()` — returns a nested dict
  whose outer keys are tuples of inputs and whose inner values are
  evaluated output dicts for each input combination.

For full class- and method-level documentation, see the
[API reference](api/index).
