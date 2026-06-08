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
| `mode`              | `str`          | *(required)*   | Workflow mode. One of `'parameter_sweep'`, `'scalar_optimize'`, `'gp_parameter_sweep'`, `'particle_weight'`, or `'geant4'`. See [](#workflow-modes). |
| `module`            | `str`          | *(required)*   | ACE3P (or downstream) module driving the workflow. One of `'omega3p'`, `'s3p'`, `'track3p'`, or `'geant4'`. Valid combinations with `mode` are listed in [](#workflow-modes). |
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
| `skip_cubit`        | `bool`         | `False`        | If `True`, skip the Cubit mesh-generation step for every workflow run. Per-call overrides via `.run(skip_cubit=...)` take precedence. |
| `skip_solver`       | `bool`         | `False`        | If `True`, skip the ACE3P solver step (Omega3P, S3P, …). |
| `skip_acdtool`      | `bool`         | `False`        | If `True`, skip the acdtool postprocessing step (Omega3P workflows only). |
| `skip_meshconvert`  | `bool`         | `False`        | If `True`, skip the Cubit-internal mesh-conversion step. |
| `dry_run`           | `bool`         | `False`        | If `True`, run the full Python pipeline but skip Cubit/solver/acdtool calls (writes a `DRY_RUN.txt` marker). Auto-enabled when the relevant tool path cannot be resolved — see [](installation.md#dry-run-mode). |
| `paths`             | `dict`         | `None`         | Mapping of executable-path overrides. Recognized keys: `ace3p`, `cubit`, `mpi`, `geant4_app_path`, `geant4_app_exe`. Each value takes highest precedence in path resolution — see [](installation.md#executable-paths). |

(workflow-modes)=
### Workflow modes

The `mode` and `module` keys together select which workflow the
`run_lume_ace3p` entry point dispatches to. Only the combinations below
are recognized; other combinations are silently ignored.

| `mode`                | `module`   | Required sections (besides `workflow_parameters`) | Behavior |
|-----------------------|------------|---------------------------------------------------|----------|
| `parameter_sweep`     | `omega3p`  | `cubit_input_parameters` and/or `ace3p_input_parameters`, `output_parameters` | Tensor-product sweep over Cubit + ACE3P inputs; acdtool postprocessing extracts `output_parameters`. |
| `parameter_sweep`     | `s3p`      | `input_parameters` and/or `ace3p_input_parameters` | Tensor-product sweep; S-parameter output written to `sweep_output_file`. |
| `scalar_optimize`     | `s3p`      | `vocs_parameters`, `xopt_parameters`              | Drives Xopt with `S3PWorkflow` as the evaluator. |
| `gp_parameter_sweep`  | `s3p`      | `sweep_parameters`, `vocs_parameters`, `xopt_parameters` | Bayesian-exploration low-fidelity sweep — fits a Gaussian Process to S3P observables, then samples it on a tensor grid defined by `sweep_parameters`. See [examples/s3p_bayesian_sweep/s3p_bayesian_sweep.yaml](https://github.com/slaclab/lume-ace3p/blob/main/examples/s3p_bayesian_sweep/s3p_bayesian_sweep.yaml). |
| `particle_weight`     | `track3p`  | `particle_parameters`                             | Standalone post-processing step (no ACE3P invocation). Reads a Track3P particle dump, filters/bins particles, computes per-particle field-emission weights, and writes a weighted-particle file. See [](#particle_parameters). |
| `geant4`              | `geant4`   | `geant4_input_parameters` (optional), `output_parameters` (optional), `particle_parameters` (optional) | Drives `Geant4Workflow`. Optionally chains a `Particles` pre-step to generate the particle source from Track3P output. |

### Geant4-workflow keys

Used only when `module: 'geant4'` is selected (the workflow class is
{py:class}`~lume_ace3p.workflow.Geant4Workflow`).

| Keyword                   | Type   | Default                | Description |
|---------------------------|--------|------------------------|-------------|
| `geant4_input`            | `str`  | `None`                 | Path to the Geant4 macro file (`.mac`) used as the simulation input. |
| `geant4_threads`          | `int`  | `1`                    | Number of threads passed to `/run/numberOfThreads` in the Geant4 macro. |
| `geant4_opts`             | `str`  | `''`                   | Additional `mpirun`/`srun` arguments when launching the Geant4 application. |
| `geant4_particle_cmd`     | `str`  | `'/lume/particleFile'` | Macro command used to inject the particle-source file path into the Geant4 macro. |
| `geant4_geometry_files`   | `list` | `[]`                   | List of geometry/auxiliary files copied into the working directory before the Geant4 run. |
| `geant4_particle_file`    | `str`  | `None`                 | Path to a pre-existing particle-source file. Ignored when `particle_parameters` is provided (in which case the file is generated from Track3P output). |
| `geant4_scoring_output`   | `str`  | `None`                 | Filename (relative to the working directory) of the Geant4 scoring output read by `evaluate()`. |
| `particle_input`          | `str`  | `None`                 | Path to the Track3P particle-data file consumed by `Particles` when generating a Geant4 source. |
| `particle_output`         | `str`  | `None`                 | Output filename for the generated Geant4 source produced by `Particles`. |

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

## `ace3p_input_parameters`

A nested mapping organized by ACE3P input-file hierarchy, used to override
or sweep over values inside the `.omega3p` / `.s3p` / `.t3p` / `.track3p`
input files (or to supply them inline when no separate ACE3P input file is
provided). The same `min`/`max`/`num` and list conventions as
`input_parameters` apply to leaf values; non-list scalars are written
through unchanged.

Two scalar attributes have special meaning when present in a sub-mapping:

- `Attribute` — disambiguates blocks that share a name; rendered as
  `BlockName|<value>|` in the patched ACE3P input.
- `ReferenceNumber` — likewise, rendered as `BlockName?<value>?`.

See the S3P-without-separate-file example in [](parameter_sweep.md) for a
complete usage pattern.

(sweep_parameters)=
## `sweep_parameters`

Used only with `mode: 'gp_parameter_sweep'`. Defines the tensor-product
grid on which the trained Gaussian Process is sampled after the Xopt
exploration phase. Same shape as `input_parameters`: each key is a
variable name, each value is a `min`/`max`/`num` mapping (linearly
spaced).

(geant4_input_parameters)=
## `geant4_input_parameters`

Used only with `mode: 'geant4'`. Supplies overrides for Geant4 macro
commands. Each key is a Geant4 macro command (typically beginning with
`/`, e.g. `/run/numberOfThreads`, `/gun/energy`); each value is either a
scalar to write through unchanged or a `min`/`max`/`num` mapping for a
parameter sweep. Non-macro keys (those not starting with `/`) are
filtered out before the macro is patched.

(particle_parameters)=
## `particle_parameters`

Used with `mode: 'particle_weight'` (Track3P post-processing) and
optionally with `mode: 'geant4'` (to generate the Geant4 particle source
from a Track3P dump). Configures the `Particles` helper.

| Keyword          | Type               | Default               | Description |
|------------------|--------------------|-----------------------|-------------|
| `impact_order`   | `int` or `list`    | *(required)*          | Track3P `ImpactOrder` value(s) to retain. Single int or list of ints. |
| `impact_face_id` | `int` or `list`    | *(required)*          | Track3P `ImpactFaceID` value(s) to retain. |
| `work_function`  | `float`            | *(required)*          | Surface work function (eV) used in the Fowler-Nordheim weighting. |
| `dt`             | `float`            | *(required)*          | Time step (s) used to convert current density to particles per emission event. |
| `beta`           | `list[float]`      | *(required)*          | Field-enhancement factor per axial bin. Length must equal `num_bins`. |
| `num_bins`       | `int`              | `len(beta)`           | Number of axial (`Initial_z`) bins applied to the filtered particles. |
| `bin_edges`      | `list[float]`      | `None` (auto-spaced)  | Explicit bin edges. If supplied, must have length `num_bins + 1`; otherwise edges are linearly spaced between the min and max `Initial_z` of the filtered particles. |

The output file (path set by `workflow_parameters.particle_output`, or
`<input>_modified.txt` if unset) contains the filtered Track3P columns
plus a `Bin` column and a `ParticleWeight` column.

## `xopt_parameters`

Controls the Xopt optimization driver invoked by `run_xopt`. Most keys are
optional; the required key is `generator`. At least one termination
criterion (`num_step`, `cost_budget`, `alotted_time`, or — for `run_lf_sweep`
— `max_steps`) must also be supplied.

| Keyword                 | Type    | Default         | Description |
|-------------------------|---------|-----------------|-------------|
| `generator`             | `str`   | *(required)*    | Xopt generator name. Supported: `'NelderMeadGenerator'`, `'ExpectedImprovementGenerator'`, `'UpperConfidenceBoundGenerator'`, `'MultiFidelityGenerator'`, `'ExpectedHypervolumeImprovementGenerator'`. |
| `generator_options`     | `dict`  | `{}`            | Keyword arguments forwarded verbatim to the chosen generator's constructor. Required for `ExpectedHypervolumeImprovementGenerator` (must include `reference_point`); also used for UCB tuning. |
| `num_random`            | `int`   | `0` (or `2` for multi-fidelity, `5` for `run_lf_sweep`) | Number of initial random evaluations used to seed the model. |
| `num_step`              | `int`   | `None`          | Number of optimization steps after the random-seeding phase. |
| `max_iterations`        | `int`   | `None`          | Total iteration cap (random + step). When set together with `tolerance` (in `vocs_parameters.objectives`), optimization stops as soon as all objectives meet the tolerance or the cap is hit. |
| `max_steps`             | `int`   | `None`          | Used by `run_lf_sweep` only; analogue of `num_step` for the low-fidelity sweep mode. |
| `improvement_threshold` | `float` | `0.01`          | Used by `run_lf_sweep`. Relative-improvement threshold for the early-stopping check. |
| `patience`              | `int`   | `5`             | Used by `run_lf_sweep`. Number of consecutive iterations without improvement before stopping. |
| `cost_budget`           | `float` | `None`          | Multi-fidelity termination criterion; total cost (in `xopt_runtime` units) at which optimization stops. |
| `alotted_time`          | `str`   | `None`          | Alternative multi-fidelity criterion in `'HH:MM:SS'` format; converted to a cost budget in seconds. |
| `cost_function`         | `str`   | `'exponential'` | Multi-fidelity cost-function model. One of `'exponential'` or `'gaussian_process'`. |
| `fidelity_variable`     | `str`   | `'s'`           | Multi-fidelity only. Name of the input variable interpreted as the fidelity coordinate; the column is renamed from `'s'` in the input dict. |
| `save_model`            | `bool`  | `False`         | If `True`, save the trained generator's GP model state to `Binary_gp_model.pt` and a human-readable summary to `gp_parameters.txt`. |

## Workflow classes

`lume-ace3p` exposes one workflow class per supported module:

- {py:class}`~lume_ace3p.workflow.Omega3PWorkflow` — Omega3P + Cubit + acdtool.
- {py:class}`~lume_ace3p.workflow.S3PWorkflow` — S3P + Cubit (no acdtool).
- {py:class}`~lume_ace3p.workflow.Geant4Workflow` — Geant4 dose calculation,
  optionally driven by Track3P particle output via the `Particles` helper.

All three subclass `ACE3PWorkflow` and share the core constructor
signature shown below. Each can be instantiated using only a workflow
dict — additional input/output dicts are optional, but no workflow input
files will be adjusted without them.

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
