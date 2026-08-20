# Optimization

`lume-ace3p` is configured with [Xopt](https://github.com/xopt-org/Xopt) to
allow single-batch-job optimization, run directly from a `lume-ace3p`
configuration file. Optimization is a **mode** (`type: scalar_optimize`) that
drives the declarative `workflow:` chain: the objective is declared in
`output_parameters` and referenced by name in the VOCS, so the Xopt driver is
workflow-agnostic — **any** workflow (S3P, Geant4, a multi-step chain) can be
optimized with the same code, not just S3P.

## Optimization with S3P

To set up an S3P optimization problem, no additional files beyond those
needed for a typical `lume-ace3p` problem are required. The configuration
file must include:

- a `workflow:` list — the module chain to drive (e.g. `cubit → s3p`).
- `mode:` with `type: scalar_optimize`.
- `output_parameters` — declares the scalar the objective pulls out of the
  workflow. For an S3P reflection objective this is the explicit form
  `{module: s3p, quantity: 'S(0,0)', at: {frequency: 12.0e+09}}`.
- `vocs_parameters` — variables (required), objectives (required), constants
  (optional), and constraints (optional) for the optimization problem.
  - `objectives` is the plain Xopt shape: it maps an **`output_parameters`
    name** to `'MINIMIZE'` or `'MAXIMIZE'`. For multi-objective optimization,
    declare more than one output/objective pair. A per-objective stopping
    threshold is supplied via `xopt_parameters.tolerance` (not inside the
    objective).
- `xopt_parameters` — choice of optimization algorithm and algorithm
  parameters.

### `xopt_parameters` options

- `generator` (required): the optimization algorithm. Currently supported:
  - Nelder–Mead — `NelderMeadGenerator`
  - Expected Improvement — `ExpectedImprovementGenerator`
  - Expected Hypervolume Improvement — `ExpectedHypervolumeImprovementGenerator`
  - Upper Confidence Bound — `UpperConfidenceBoundGenerator`
  - Multifidelity Bayesian — `MultiFidelityGenerator`
- `num_random` (optional): number of random exploratory steps before
  optimization begins.
- `num_step` (optional): fixed number of optimization steps.
- `max_iterations` (optional): maximum number of steps after which
  optimization must end, regardless of other stopping criteria.
- `cost_budget` (optional): total time, in seconds, allowed for optimization
  (used as a stopping criterion).
- `alotted_time` (optional): total wall-clock time, expressed as
  `HH:MM:SS`, allowed for the problem (used to determine a stopping
  criterion).
- `save_model` (optional): for algorithms that train a GP (e.g.
  multifidelity Bayesian), `True` writes a `gp_parameters.txt` file
  containing the trained GP parameters so that it can be re-loaded later.

Multifidelity Bayesian optimization adds:

- `fidelity_variable` (required): the name of the parameter in the Cubit
  file that controls fidelity.
- `cost_function` (optional): the relationship between cost and fidelity.
  Options are `exponential` (explicit, exponential relationship between
  max- and min-fidelity cost) and `gaussian_process` (implicit, learned
  relationship). Defaults to `exponential`.

Upper-confidence-bound and expected-hypervolume-improvement also support:

- `generator_options` (optional): list additional algorithm parameters,
  such as `beta` for upper confidence bound.

### Output files

Running `lume-ace3p` with an Xopt mode logs the full run trajectory to a single
file — `sim_output.txt` by default, or the path given as `mode.output_file`.
The file is the Xopt data table (all parameter tuples reached and the
corresponding output values), overwritten each step so it always holds the
complete trajectory.

### S3P Nelder–Mead example

This example (based on the 90-degree bend from the ACE3P tutorials, shipped as
[`examples/s3p_optimization`](https://github.com/slaclab/lume-ace3p/blob/main/examples/s3p_optimization/s3p_optimization.yaml))
sets up an optimization over the scattering parameter `S(0,0)` at 12 GHz, with
input parameters of waveguide width and chamfer length.

```yaml
workflow_parameters :
    'workdir' : 'lume-ace3p_xopt_workdir'

workflow :
  - module : cubit
    journal : 'bend-90degree.jou'
  - module : s3p
    input : 'bend-90degree.s3p'
    tasks : 16
    cores : 8
    opts : '--cpu-bind=cores'

mode :
    type : scalar_optimize
```

The `workflow:` chain is the same `cubit → s3p` pipeline used for the 90-degree
bend parameter sweep; only the `mode` differs.

The objective is declared in `output_parameters` and referenced by name in the
VOCS:

```yaml
output_parameters :
    'reflection' : { module: s3p, quantity: 'S(0,0)', at: { frequency: 12.0e+09 } }

vocs_parameters :
    'variables' :
        'cornercut': [14,17]
        'rcorner1': [0.5,2.5]
    'objectives' :
        'reflection' : 'MINIMIZE'
```

The variable names `cornercut` and `rcorner1` must match the variable names
in the Cubit file. Each input variable has a range to explore. The objective is
an `output_parameters` name mapped to `MINIMIZE`/`MAXIMIZE` — the Xopt driver
never parses S-parameters itself, so to configure a multi-objective problem you
add more `output_parameters` entries and list each in `objectives`:

```yaml
output_parameters :
    'reflection'    : { module: s3p, quantity: 'S(0,0)', at: { frequency: 12.0e+09 } }
    'transmission'  : { module: s3p, quantity: 'S(0,1)', at: { frequency: 10.424e+09 } }

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

`generator` selects the optimization algorithm; `num_random` is the number
of initial random parameter-space guesses; `num_step` is the number of
iterations.

### S3P multifidelity Bayesian example

This example optimizes `S(1,1)` at 12 GHz with input parameters of
waveguide width and chamfer length:

```yaml
workflow_parameters :
    'workdir' : 'lume-ace3p_xopt_workdir'

workflow :
  - module : cubit
    journal : 'bend-90degree_mf.jou'
  - module : s3p
    input : 'bend-90degree_mf.s3p'
    tasks : 16
    cores : 8
    opts : '--cpu-bind=cores'

mode :
    type : scalar_optimize
```

The Cubit journal file must be configured for multifidelity optimization by
specifying a variable that controls model fidelity. Here, fidelity is
controlled by a parameter that changes mesh size.

```yaml
output_parameters :
    'reflection' : { module: s3p, quantity: 'S(1,1)', at: { frequency: 12.0e+09 } }

vocs_parameters :
    'variables' :
        'cornercut': [12.5,13.5]
        'wgwidth': [21,22]
    'objectives' :
        'reflection' : 'MINIMIZE'
```

The `tolerance` (a stopping criterion) is set in `xopt_parameters`: if the
objective falls below 0.001, the optimization terminates.

```yaml
xopt_parameters :
    'generator' : 'MultiFidelityGenerator'
    'fidelity_variable' : 'mesh_fidelity'
    'cost_function' : 'exponential'
    'alotted_time' : 00:30:00
    'num_random' : 3
    'tolerance' : 1e-03
```

The `fidelity_variable` parameter must match exactly the name of the
variable in the Cubit file that controls fidelity. The `cost_function`
expresses the relationship between fidelity and cost. `alotted_time` (here
30 minutes) is a stopping criterion: if the run is close to the allotted
time, the algorithm terminates. The algorithm starts with three random
steps to seed its internal GP model.

## Optimizing other workflows

Because the objective is pulled from `output_parameters` and the workflow is
driven only through its `evaluate` seam, the same `scalar_optimize` mode
optimizes any chain — you change the `workflow:` list and point the objective at
a different module's output. No custom `sim` function or workflow subclass is
needed (the pre-refactor `Omega3PWorkflow` / `S3PWorkflow` classes and the
hand-rolled Xopt loop no longer exist).

For an **Omega3P R/Q optimization** (shipped as
[`examples/omega3p_optimization`](https://github.com/slaclab/lume-ace3p/blob/main/examples/omega3p_optimization/omega3p_optimization.yaml)),
the objective is an acdtool bare-form spec routed to the `acdtool` module. This
is the optimization counterpart of the `omega3p_sweep` example — same
`cubit → omega3p → acdtool` pipeline and the same `pillbox-rtop.*` inputs, with
`mode: scalar_optimize` in place of the sweep:

```yaml
workflow :
  - module : cubit
    journal : 'pillbox-rtop.jou'
  - module : omega3p
    input : 'pillbox-rtop.omega3p'
    tasks : 16
    cores : 8
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
tracked by Xopt but not optimized. `constraints` (optional) specify inequality
constraints on any declared output. Compute a derived constraint such as a
target-frequency error by declaring the underlying quantity (`mode_freq`) as an
observable and adding a constraint on it, rather than by writing a `sim`
function.

The `input_parameters` block gives each VOCS variable an explicit home. A VOCS
`variables` entry declares only a **name and bounds** — it is `input_parameters`
that routes that name to a bucket (`cubit` / `ace3p` / `geant4` / `particles`)
and, for Cubit, to the matching `name = …` line in the journal file. As with the
S3P example, the scalar values here are nominal starting points that Xopt
overrides each step.

:::{note}
If `input_parameters` is omitted, every VOCS variable name misses the routing
table and **silently falls back to the cubit bucket**. That happens to work when
all variables are Cubit journal variables (as above), but it masks typos — a
misspelled VOCS name becomes a junk Cubit variable that no-ops — and mis-routes
any non-Cubit knob. Declare `input_parameters` so the routing is explicit and
checked.
:::

## Viewing S3P optimization output

See [](plotting.md) for the optimization-output visualization tools.
