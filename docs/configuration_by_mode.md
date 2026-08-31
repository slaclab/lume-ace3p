# Configuration by mode

Every LUME-ACE3P run needs the same two things: a **`workflow:`** list of
modules and a **`mode:`** block that selects how the workflow is driven.
*Which additional blocks are required* depends entirely on the mode. This page
is a checklist — for each mode, what you **must** supply, what is **optional**,
and a minimal skeleton to copy from.

For the full meaning of each block, see the [](yaml_reference.md); for a
walkthrough of a sweep or an optimization, see [](parameter_sweep.md) and
[](optimization.md).

## Always required

Regardless of mode, two top-level blocks are mandatory:

- **`workflow:`** — an ordered list of module entries (`cubit`, `omega3p`,
  `s3p`, `acdtool`, `particles`, `geant4`, …). The run order is resolved from
  artifact dependencies, not list order.
- **`mode:`** — a block with a `type:` key naming one of the modes below.

`workflow_parameters:` (workdir and executable paths) is always *optional* — it
defaults to running in the current directory with auto-resolved tool paths.

## Requirements matrix

A ✓ means required for that mode; **○** means optional; a blank means ignored.

| Block                | `single` | `parameter_sweep` | `scalar_optimize` | `gp_parameter_sweep` |
|----------------------|:--------:|:-----------------:|:-----------------:|:--------------------:|
| `workflow:`          |    ✓     |         ✓         |         ✓         |          ✓           |
| `mode:`              |    ✓     |         ✓         |         ✓         |          ✓           |
| `input_parameters`   |    ○¹    |        ✓²         |        ○³         |         ○³           |
| `output_parameters`  |    ○     |         ○         |        ✓⁴         |         ✓⁴           |
| `vocs_parameters`    |          |                   |         ✓         |          ✓           |
| `xopt_parameters`    |          |                   |         ✓         |          ✓           |
| `sweep_parameters`   |          |                   |                   |          ✓           |
| `workflow_parameters`|    ○     |         ○         |        ○          |         ○            |

¹ Optional, and if present **every leaf must be scalar** — a vector-valued leaf
  (a list or a `min/max/num` range) makes the config a sweep, not a single run.
² Required in the sense that a *real* sweep needs at least one **array-valued**
  leaf (a list or `min/max/num`) to iterate over. With no array leaf the run
  degenerates to a single evaluation.
³ Optional for routing, but **strongly recommended**: it declares each VOCS
  variable's home bucket (cubit / ace3p / geant4 / particles). Omit it and every
  variable silently falls back to the cubit bucket — fine for a pure-Cubit
  problem, but it masks typos and mis-routes any non-Cubit knob. See
  [](yaml_reference.md#vocs_parameters).
⁴ Required *indirectly*: the VOCS `objectives` (and `constraints` /
  `observables`) reference `output_parameters` **by name**, so every name used
  in the VOCS must be declared in `output_parameters`. A run whose VOCS names an
  undeclared output fails at evaluation.

## `single`

Run the workflow once and write a one-row result table (or one row per
field-index for a field-indexed solver such as S3P).

**Required:** `workflow:`, `mode:` only.
**Optional:** `input_parameters` (scalar leaves only — nominal overrides),
`output_parameters` (the scalars to extract into the table), `mode.output_file`.

```yaml
workflow :
  - module : cubit
    journal : 'pillbox-rtop.jou'
  - module : omega3p
    input : 'pillbox-rtop.omega3p'
  - module : acdtool
    input : 'pillbox-rtop.rfpost'

mode :
  type : single
  output_file : 'single_output.txt'

output_parameters :
  'R/Q' : {module: acdtool, section: RoverQ, quantity: RoQ, at: {mode: 0}}
```

## `parameter_sweep`

Evaluate the tensor product of every array-valued input leaf, one table row per
grid point.

**Required:** `workflow:`, `mode:`, and an `input_parameters` block containing at
least one array-valued leaf (a list or a `min/max/num` mapping).
**Optional:** `output_parameters`, `mode.output_file`, `mode.resume`. Array leaves
across different sub-blocks (`cubit:`, `ace3p:`, `geant4:`, `particles:`) all
multiply into the same tensor grid.

`mode.resume: True` (with `workflow_parameters: {workdir_mode: indexed}`) re-runs
only the points and steps a previous run did not finish — see
[](#resuming-a-sweep).

```yaml
workflow :
  - module : cubit
    journal : 'pillbox-rtop.jou'
  - module : omega3p
    input : 'pillbox-rtop.omega3p'
  - module : acdtool
    input : 'pillbox-rtop.rfpost'

mode :
  type : parameter_sweep
  output_file : 'omega3p_sweep_output.txt'

input_parameters :
  cubit :
    'cav_radius'  : {min: 90.0, max: 120.0, num: 4}
    'ellipticity' : {min: 0.5,  max: 1.25,  num: 4}

output_parameters :
  'R/Q'       : {module: acdtool, section: RoverQ, quantity: RoQ, at: {mode: 0}}
  'Mode_freq' : {module: omega3p, quantity: Frequency, at: {mode: 0}}
```

## `scalar_optimize`

Drive an Xopt optimization loop over the workflow.

**Required:** `workflow:`, `mode:`, `vocs_parameters`, `xopt_parameters`, and the
`output_parameters` entries named by the VOCS objectives/constraints/observables.
Within `xopt_parameters`, `generator` is required and — for `scalar_optimize` —
at least one termination criterion (`num_step`, `cost_budget`, or `alotted_time`).
**Optional but recommended:** `input_parameters` (declares variable routing; see
note ³ above).
**Optional:** `mode.output_file` (the Xopt run log, default `sim_output.txt`),
`mode.resume`, and `workflow_parameters.workdir_mode` — set it to `'auto'` so each
evaluation gets its own directory rather than overwriting the previous one's files.

`mode.resume: True` continues an interrupted optimization from the `xopt_state.yml`
written beside the run log, instead of starting over. Unlike a resumed sweep it does
**not** reproduce the trajectory an uninterrupted run would have taken — it promises
only that no evaluation is repeated and the search continues from the same data. See
[](#xopt-resume).

```yaml
workflow :
  - module : cubit
    journal : 'pillbox-rtop.jou'
  - module : omega3p
    input : 'pillbox-rtop.omega3p'
  - module : acdtool
    input : 'pillbox-rtop.rfpost'

mode :
  type : scalar_optimize

input_parameters :          # recommended: gives each VOCS variable a home bucket
  cubit :
    'cav_radius'  : 100.0
    'ellipticity' : 0.5

output_parameters :
  'R/Q'       : {module: acdtool, section: RoverQ, quantity: RoQ, at: {mode: 0}}
  'mode_freq' : {module: omega3p, quantity: Frequency, at: {mode: 0}}

vocs_parameters :
  'variables' :
    'cav_radius'  : [95, 105]
    'ellipticity' : [0.5, 1.2]
  'objectives' :
    'R/Q' : 'MAXIMIZE'
  'observables' :
    - 'mode_freq'

xopt_parameters :
  'generator' : 'NelderMeadGenerator'
  'num_random' : 0
  'num_step' : 25
```

:::{note}
`NelderMeadGenerator` works with `num_random: 0`. When no random seeding is
requested, LUME-ACE3P seeds the optimizer's initial point at the **midpoint of
each variable's bounds** (xopt requires a starting point), so the nominal values
in `input_parameters` are not themselves the starting simplex origin.
:::

## `gp_parameter_sweep`

Fit a Gaussian Process during an Xopt exploration phase, then sample the GP
posterior mean on a dense grid.

**Required:** everything `scalar_optimize` requires (`vocs_parameters`,
`xopt_parameters`, referenced `output_parameters`), **plus** a `sweep_parameters`
block defining the posterior-mean grid.
**Optional:** `input_parameters` (routing), `mode.output_file` (Xopt run log),
`mode.sweep_output_file` (posterior-mean table, default `sweep_output.txt`) and
`mode.resume` (as for `scalar_optimize`; its `improvement_threshold`/`patience`
convergence window is not carried across the interruption).

```yaml
mode :
  type : gp_parameter_sweep
  sweep_output_file : 'sweep_output.txt'

sweep_parameters :
  'cav_radius'  : {min: 95, max: 105, num: 25}
  'ellipticity' : {min: 0.5, max: 1.2, num: 25}

# vocs_parameters, xopt_parameters, output_parameters as in scalar_optimize
```

## Surrogate-pipeline modes

Two additional modes support the offline surrogate workflow; they are configured
differently from the modes above (their keys live directly on the `mode:` block
rather than in separate top-level sections):

- **`collect_training_data`** — samples the input space (Sobol/random) and writes
  a training store. Key `mode:` fields: `variables` (per-variable bounds),
  `store`/`output_dir`, `num_samples`, `sampler`, `seed`, optional `fidelity`.
- **`train_surrogate`** — fits a PCA-GP surrogate from a training store. Key
  `mode:` fields: `store` (required), `variance`, `num_components`, `seed`.

See [](optimization.md) and the surrogate examples under `examples/` for full
usage.
