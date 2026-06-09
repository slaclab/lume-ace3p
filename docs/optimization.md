# Optimization

`lume-ace3p` is configured with [Xopt](https://github.com/xopt-org/Xopt) to
allow single-batch-job optimization. Optimization problems for the S3P module
can be run directly from a `lume-ace3p` configuration file. To run an
optimization with Omega3P, a Python file is required.

## Optimization with S3P

To set up an S3P optimization problem, no additional files beyond those
needed for a typical `lume-ace3p` problem are required. The configuration
file must include:

- `workflow_parameters` — file names, HPC settings, and other configuration
  (same as parameter sweeping).
- `vocs_parameters` — variables (required), objectives (required), constants
  (optional), and constraints (optional) for the optimization problem.
  - Within `objectives`, specify `s_parameter` (e.g. `S(0,0)`), `frequency`
    (e.g. `11.324e+09`), and `optimization` (`MINIMIZE` or `MAXIMIZE`). For
    multi-objective optimization, specify each parameter as a list (e.g.
    `s_parameter: S(0,0), S(1,1)`). Optionally include `tolerance` with a
    stopping criterion for each objective.
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

Running `lume-ace3p` with Xopt produces two files:

- `sim_output.txt` — all parameter tuples reached and the corresponding
  values of the parameter(s) being optimized.
- `sim_output_all_values.txt` — all parameter tuples reached and the
  corresponding values of *all* output parameters.

### S3P Nelder–Mead example

This example (based on the 90-degree bend from the ACE3P tutorials) sets up
an optimization over the scattering parameter `S(0,0)` at 12 GHz, with input
parameters of waveguide width and chamfer length.

```yaml
workflow_parameters :
    'mode' : 'scalar_optimize'
    'module' : 's3p'
    'cubit_input': 'bend-90degree.jou'
    'ace3p_input': 'bend-90degree.s3p'
    'ace3p_tasks': 16
    'ace3p_cores': 16
    'ace3p_opts' : '--cpu-bind=cores'
    'workdir': 'lume-ace3p_xopt_workdir'
```

This is identical to the workflow parameters for the 90-degree bend
parameter-sweep example, except `mode` is set to `scalar_optimize`.

VOCS:

```yaml
vocs_parameters :
    'variables' :
        'cornercut': [14,17]
        'rcorner1': [0.5,2.5]
    'objectives' :
        's_parameter' : 'S(0,0)'
        'frequency' : 12.0e+09
        'optimization' : 'MINIMIZE'
```

The variable names `cornercut` and `rcorner1` must match the variable names
in the Cubit file. Each input variable has a range to explore. Objectives
for optimization with S3P take the form of a particular S-parameter
optimized at a particular frequency. To configure a multi-objective problem,
keep the same format and supply lists:

```text
    'objectives' :
        's_parameter' : 'S(0,0)', 'S(0,1)'
        'frequency' : 12.0, 10.424e+09
        'optimization' : 'MINIMIZE', 'MINIMIZE'
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

This example optimizes `S(0,0)` at 12 GHz with input parameters of
waveguide width and chamfer length:

```yaml
workflow_parameters :
    'mode' : 'scalar_optimize'
    'module' : 's3p'
    'cubit_input': 'bend-90degree_mf.jou'
    'ace3p_input': 'bend-90degree_mf.s3p'
    'ace3p_tasks': 16
    'ace3p_cores': 16
    'ace3p_opts' : '--cpu-bind=cores'
    'workdir': 'lume-ace3p_xopt_workdir'
```

The Cubit journal file must be configured for multifidelity optimization by
specifying a variable that controls model fidelity. Here, fidelity is
controlled by a parameter that changes mesh size.

```yaml
vocs_parameters :
    'variables' :
        'cornercut': [12.5,13.5]
        'wgwidth': [21,22]
    'objectives' :
        's_parameter' : 'S(1,1)'
        'frequency' : 12.0e+09
        'optimization' : 'MINIMIZE'
        'tolerance' : 1e-03
```

A `tolerance` is included: if `S(1,1)` at 12 GHz falls below 0.001, the
optimization terminates.

```yaml
xopt_parameters :
    'generator' : 'MultiFidelityGenerator'
    'fidelity_variable' : 'mesh_fidelity'
    'cost_function' : 'exponential'
    'alotted_time' : 00:30:00
    'num_random' : 3
```

The `fidelity_variable` parameter must match exactly the name of the
variable in the Cubit file that controls fidelity. The `cost_function`
expresses the relationship between fidelity and cost. `alotted_time` (here
30 minutes) is a stopping criterion: if the run is close to the allotted
time, the algorithm terminates. The algorithm starts with three random
steps to seed its internal GP model.

## Optimization with Omega3P

To set up an Omega3P optimization problem, an Xopt VOCS object and two
dicts are required: a workflow dict and an output dict. Additionally, a
`sim` function must be written that uses an ACE3P workflow class object.

- VOCS object — variables, objectives, optionally constraints and observables.
- workflow dict — file names, HPC settings, and other configuration.
- output dict — output quantities to track for optimization.
- `sim` function — a Python wrapper that Xopt calls to evaluate a candidate.

Once provided, optimization is run via Xopt object methods (e.g. `.step()`).

### Omega3P optimization example

This example, based on the rounded-top pillbox from the ACE3P tutorials,
runs an optimization loop over cavity radius and cavity wall ellipticity to
maximize R/Q with a target-frequency constraint.

```python
import numpy as np
from xopt.vocs import VOCS
from xopt.evaluator import Evaluator
from xopt.generators.bayesian import ExpectedImprovementGenerator
from xopt import Xopt
from lume_ace3p.workflow import Omega3PWorkflow
from lume_ace3p.tools import WriteXoptData

workflow_dict = {'cubit_input': 'pillbox-rtop.jou',
                 'ace3p_input': 'pillbox-rtop.omega3p',
                 'ace3p_tasks': 16,
                 'ace3p_cores': 16,
                 'ace3p_opts' : '--cpu-bind=cores',
                 'rfpost_input': 'pillbox-rtop.rfpost',
                 'workdir': 'lume-ace3p_xopt_workdir'}
```

In this example the workflow folder is overwritten on each evaluation;
`workdir_mode` can also be set to write to separate folders automatically.

Output dict:

```python
output_dict = {'R/Q': ['RoverQ', '0', 'RoQ'],
               'mode_freq': ['RoverQ', '0', 'Frequency']}
```

This dict is used to parse the ACE3P workflow output for use with Xopt.

VOCS:

```python
vocs = VOCS(
    variables={"cav_radius": [95, 105], "ellipticity": [0.5, 1.2]},
    objectives={"R/Q": "MAXIMIZE"},
    constraints={"freq_error" : ["LESS_THAN", 0.0001]},
    observables=["mode_freq"]
)
```

`variables` are the workflow input parameters and their bounds.
`objectives` selects the quantity (defined in the output dict) to maximize
or minimize. `constraints` is optional and specifies an inequality
constraint. `observables` is optional and is tracked by Xopt but not used
in optimization.

:::{note}
While `R/Q` and `mode_freq` are defined in the output dict, `freq_error` is
not. This is intentional and is addressed in the `sim` function.
:::

The goal is to optimize R/Q with a constraint that `mode_freq` is within
1% of a specified `target_freq`. The `sim` function calculates `freq_error`
on the fly:

```python
target_freq = 1.3e9

def sim_function(input_dict):
    workflow = Omega3PWorkflow(workflow_dict, input_dict, output_dict)
    output_data = workflow.run()
    output_data['freq_error'] = (output_data['mode_freq'] - target_freq)**2 / target_freq**2
    return output_data
```

The `input_dict` Xopt hands in is a plain `{var_name: scalar}` mapping
of Cubit variable values; the workflow constructor coerces it into a
{py:class}`~lume_ace3p.inputs.WorkflowInputs` with that mapping in the
`cubit` bucket. The ACE3P input file specified by `ace3p_input` is
copied through to each working directory unchanged.

The Xopt object is built with a chosen optimizer, the VOCS, and the `sim`
function:

```python
evaluator = Evaluator(function=sim_function)
generator = ExpectedImprovementGenerator(vocs=vocs)
X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)
```

To run the optimizer, call `.random_evaluate()` to seed Xopt's internal
model and `.step()` to optimize:

```python
for i in range(5):
    X.random_evaluate()
    WriteXoptData('sim_output.txt', X)

for i in range(15):
    X.step()
    WriteXoptData('sim_output.txt', X)
```

In this example Xopt calls the ACE3P workflow 5 times with random inputs
and then optimizes the objective over 15 more workflow evaluations. The
output file `sim_output.txt` is user-provided and contains the Xopt data
structure.

## Viewing S3P optimization output

See [](plotting.md) for the optimization-output visualization tools.
