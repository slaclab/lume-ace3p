# Optimization

`lume-ace3p` uses [Xopt](https://github.com/xopt-org/Xopt) to run an
optimization as a single batch job, driven from a `lume-ace3p` configuration
file. Optimization is a **mode** (`type: scalar_optimize`) that drives the
`workflow:` chain. The objective is declared in `output_parameters` and
referenced by name in the VOCS, so any workflow (S3P, Geant4, a multi-step
chain) can be optimized.

## Optimization with S3P

An S3P optimization needs no files beyond those of a typical `lume-ace3p`
problem. Its configuration file must include:

- `workflow:`, the module chain to drive (e.g. `cubit → s3p`).
- `mode:` with `type: scalar_optimize`.
- `output_parameters`, the scalar the objective pulls out of the workflow. For
  an S3P reflection objective this is
  `{module: s3p, quantity: 'S(0,0)', at: {frequency: 12.0e+09}}`. The `at:`
  frequency must be a point of the `.s3p` file's `FrequencyScan`; an off-grid
  value raises at the first evaluation, naming the scan range and the nearest
  scan points.
- `vocs_parameters`: variables (required), objectives (required), constants
  (optional), and constraints (optional).
  - `objectives` has the plain Xopt shape: it maps an `output_parameters`
    name to `'MINIMIZE'` or `'MAXIMIZE'`. Declare more than one pair for
    multi-objective optimization. A per-objective stopping threshold goes in
    `xopt_parameters.tolerance`, not inside the objective.
- `xopt_parameters`: the optimization algorithm and its parameters.

### `xopt_parameters` options

- `generator` (required): the optimization algorithm. Currently supported:
  - Nelder–Mead: `NelderMeadGenerator`
  - Expected Improvement: `ExpectedImprovementGenerator`
  - Expected Hypervolume Improvement: `ExpectedHypervolumeImprovementGenerator`
  - Upper Confidence Bound: `UpperConfidenceBoundGenerator`
  - Multifidelity Bayesian: `MultiFidelityGenerator`
- `num_random` (optional): number of random exploratory steps before
  optimization begins.

**Exactly one termination criterion** is required; a config with none of them
does nothing and says so:

- `num_step`: fixed number of optimization steps.
- `cost_budget`: total time, in seconds, allowed for optimization.
- `alotted_time`: the same budget as `HH:MM:SS`. `cost_budget` and
  `alotted_time` select the multi-fidelity cost-limited loop.

Two further keys refine a criterion and do nothing on their own:

- `max_iterations` (optional): caps the total steps a `num_step` run may take.
  It is read only alongside `num_step` and is ignored without it.
- `tolerance` (optional): a stopping test applied inside whichever criterion's
  loop is running. The run ends early once every objective is at or below it.

All of these count the **campaign**, not this process, so they mean the same
thing to a run continued with `mode.resume` (see
[](#resuming-an-interrupted-optimization)).
- `save_model` (optional): for algorithms that train a GP (e.g. multifidelity
  Bayesian), `True` writes the trained GP parameters to `gp_parameters.txt` for
  later re-loading.

Multifidelity Bayesian optimization adds:

- `fidelity_variable` (required): the name of the Cubit-file parameter that
  controls fidelity.
- `cost_function` (optional): the relationship between cost and fidelity,
  either `exponential` (the default: an explicit exponential relationship
  between max- and min-fidelity cost) or `gaussian_process` (implicit, learned
  relationship).

Upper-confidence-bound and expected-hypervolume-improvement also support:

- `generator_options` (optional): additional algorithm parameters, such as
  `beta` for upper confidence bound. Expected hypervolume improvement requires
  a `reference_point` here.

### Output files

An Xopt mode logs the full run trajectory to a single file: `sim_output.txt`
by default, or the path given as `mode.output_file`. It is the Xopt data table
(every parameter tuple reached and its output values), overwritten each step so
it always holds the complete trajectory.

### Resuming an interrupted optimization

Add `resume: True` to the `mode:` block and an optimization killed by a batch
wall clock continues instead of starting over:

```yaml
mode :
    type : scalar_optimize
    resume : True          # continue from xopt_state.yml
```

`xopt_state.yml` is written beside `sim_output.txt` after every evaluation,
whether or not `resume` is set. It holds the trajectory *and* the generator's
internal state, so a Nelder–Mead simplex carries on rather than restarting on
top of old data. `run-lume-ace3p --status <config.yaml>` reports what it holds
without running anything.

:::{important}
A resumed optimization **does not reproduce the trajectory** an uninterrupted
run would have taken. The promise is that no evaluation is repeated and the
search continues from the same data, not that two `sim_output.txt` files will
diff clean. This is weaker than the sweep modes' promise of an identical table.
:::

Iteration budgets (`num_random`, `num_step`, `max_iterations`, `cost_budget`)
are campaign totals, so a resumed run continues to the same finish line and
resuming a finished optimization does nothing. See [](#xopt-resume) for the
refusal cases: a state file written for a different generator, objective
direction or variable bounds is reported and discarded rather than adopted.

### One directory per evaluation

Set `workflow_parameters: {workdir_mode: 'auto'}` (as the shipped examples do)
and each evaluation runs in its own directory, numbered by iteration:
`<workdir>_0`, `<workdir>_1`, … matching the rows of `sim_output.txt`. The
mesh, solver input, results and log of evaluation 7 are in `<workdir>_7`, so
any row of the trajectory can be traced back to the files that produced it.

Without it (`workdir_mode` defaults to `'manual'`) every evaluation runs in
the one `workdir`, overwriting the previous evaluation's mesh, input files,
results, logs and run manifest; only the last evaluation survives on disk. The
run warns when that is about to happen. See [](#workdir-mode) for the full
table, including why `'auto'` numbers by iteration here instead of naming by
input value.

### S3P Nelder–Mead example

This example (based on the 90-degree bend from the ACE3P tutorials, shipped as
[`examples/s3p_optimization`](https://github.com/slaclab/lume-ace3p/blob/main/examples/s3p_optimization/s3p_optimization.yaml))
optimizes the scattering parameter `S(0,0)` at 12 GHz over the corner chamfer
length (`cornercut`) and a corner rounding radius (`rcorner1`).

```yaml
workflow_parameters :
    'workdir' : 'lume-ace3p_xopt_workdir'
    'workdir_mode' : 'auto'      # one directory per evaluation: _0, _1, _2, …

workflow :
  - module : cubit
    journal : 'bend-90degree.jou'
  - module : s3p
    input : 'bend-90degree.s3p'
    tasks : 16
    cores : 4
    opts : '--cpu-bind=cores'

mode :
    type : scalar_optimize
```

The `workflow:` chain is the same `cubit → s3p` pipeline as the 90-degree bend
parameter sweep; only the `mode` differs.

`input_parameters` gives each variable its home bucket. The objective is
declared in `output_parameters` and referenced by name in the VOCS:

```yaml
input_parameters :
    cubit :
        'cornercut' : 15.0
        'rcorner1' : 1.0

output_parameters :
    'reflection' : { module: s3p, quantity: 'S(0,0)', at: { frequency: 12.0e+09 } }

vocs_parameters :
    'variables' :
        'cornercut': [14,17]
        'rcorner1': [0.5,2.5]
    'objectives' :
        'reflection' : 'MINIMIZE'
```

`cornercut` and `rcorner1` must match the variable names in the Cubit file;
each has a range to explore. The objective is an `output_parameters` name
mapped to `MINIMIZE`/`MAXIMIZE`; the Xopt driver never parses S-parameters
itself. For a multi-objective problem, add more `output_parameters` entries and
list each in `objectives`:

```yaml
output_parameters :
    'reflection'    : { module: s3p, quantity: 'S(0,0)', at: { frequency: 12.0e+09 } }
    'transmission'  : { module: s3p, quantity: 'S(0,1)', at: { frequency: 10.5e+09 } }

vocs_parameters :
    'objectives' :
        'reflection'   : 'MINIMIZE'
        'transmission' : 'MINIMIZE'
```

Xopt parameters:

```yaml
xopt_parameters :
    'generator' : 'NelderMeadGenerator'
    'num_random' : 0
    'num_step' : 25
```

`generator` selects the optimization algorithm; `num_random` is the number of
initial random parameter-space guesses; `num_step` is the number of iterations.

### S3P multifidelity Bayesian example

This example (shipped as
[`examples/s3p_mf_optimization`](https://github.com/slaclab/lume-ace3p/blob/main/examples/s3p_mf_optimization/s3p_mf_optimization.yaml))
optimizes `S(1,1)` at 12 GHz over waveguide width and chamfer length:

```yaml
workflow_parameters :
    'workdir' : 'lume-ace3p_mf_workdir'
    'workdir_mode' : 'auto'      # one directory per evaluation: _0, _1, _2, …

workflow :
  - module : cubit
    journal : 'bend-90degree_mf.jou'
  - module : s3p
    input : 'bend-90degree_mf.s3p'
    tasks : 8        # the coarsest fidelity's ~2.6k-element mesh crashes S3P over 16 ranks
    cores : 4
    opts : '--cpu-bind=cores'

mode :
    type : scalar_optimize
```

The Cubit journal file must define a variable that controls model fidelity.
Here that variable changes the mesh size. It is declared in `input_parameters`
alongside the optimization variables:

```yaml
output_parameters :
    'reflection' : { module: s3p, quantity: 'S(1,1)', at: { frequency: 12.0e+09 } }

input_parameters :
    cubit :
        'cornercut' : 13.0
        'wgwidth' : 21.5
        'mesh_fidelity' : 0.0

vocs_parameters :
    'variables' :
        'cornercut': [12.5,13.5]
        'wgwidth': [21,22]
    'objectives' :
        'reflection' : 'MINIMIZE'
```

`tolerance` is a stopping criterion set in `xopt_parameters`: the optimization
terminates once the objective is at or below 0.001.

```yaml
xopt_parameters :
    'generator' : 'MultiFidelityGenerator'
    'fidelity_variable' : 'mesh_fidelity'
    'cost_function' : 'exponential'
    'alotted_time' : '00:30:00'
    'num_random' : 3
    'tolerance' : 1.0e-03
```

`fidelity_variable` must exactly match the name of the Cubit variable that
controls fidelity. `cost_function` is the fidelity-to-cost relationship.
`alotted_time` (here 30 minutes) is a stopping criterion: the run terminates
once the accumulated evaluation time reaches the budget. `num_random: 3` seeds
the GP with three random points, followed by three more spread along the
fidelity ladder.

## Optimizing other workflows

Because the objective is pulled from `output_parameters`, `scalar_optimize`
optimizes any chain: change the `workflow:` list and point the objective at a
different module's output. No custom `sim` function or workflow subclass is
needed.

For an **Omega3P R/Q optimization** (shipped as
[`examples/omega3p_optimization`](https://github.com/slaclab/lume-ace3p/blob/main/examples/omega3p_optimization/omega3p_optimization.yaml)),
the objective is an acdtool spec routed to the `acdtool` module. This is the
optimization counterpart of the `omega3p_sweep` example: the same
`cubit → omega3p → acdtool` pipeline and `pillbox-rtop.*` inputs, with
`mode: scalar_optimize` in place of the sweep:

```yaml
workflow :
  - module : cubit
    journal : 'pillbox-rtop.jou'
  - module : omega3p
    input : 'pillbox-rtop.omega3p'
    tasks : 16
    cores : 4
    opts : '--cpu-bind=cores'
  - module : acdtool
    input : 'pillbox-rtop.rfpost'

mode :
    type : scalar_optimize

input_parameters :
    cubit :
        'cav_radius' : 100.0
        'ellipticity' : 0.5

output_parameters :
    'R/Q'       : {module: acdtool, section: RoverQ, quantity: RoQ, at: {mode: 0}}
    'mode_freq' : {module: omega3p, quantity: Frequency, at: {mode: 0}}

vocs_parameters :
    'variables' :
        'cav_radius' : [95, 105]
        'ellipticity' : [0.5, 1.2]
    'objectives' :
        'R/Q' : 'MAXIMIZE'
    'observables' :
        - 'mode_freq'
```

`variables` are the workflow input parameters and their bounds; `objectives`
selects an `output_parameters` name to maximize or minimize; `observables` are
tracked by Xopt but not optimized. `constraints` (optional) are inequality
constraints on any declared output. To constrain a derived quantity such as a
target-frequency error, declare the underlying quantity (`mode_freq`) as an
observable and constrain it.

A VOCS `variables` entry declares only a **name and bounds**;
`input_parameters` routes that name to a bucket (`cubit` / `ace3p` / `geant4` /
`particles`) and, for Cubit, to the matching `name = …` line in the journal
file. As in the S3P examples, the scalar values are nominal starting points
that Xopt overrides each step.

:::{note}
If `input_parameters` is omitted, every VOCS variable name silently falls back
to the cubit bucket. That works when all variables are Cubit journal variables
(as above), but it masks typos (a misspelled VOCS name becomes a junk Cubit
variable that no-ops) and mis-routes any non-Cubit knob. Declare
`input_parameters` so the routing is explicit and checked.
:::

## Viewing S3P optimization output

See [](plotting.md) for the optimization-output visualization tools.
