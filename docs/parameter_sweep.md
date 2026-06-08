# Parameter sweeping

`lume-ace3p` has two main use-cases: parameter sweeping and optimization. For
both, ACE3P workflows are evaluated many times according to parameters set by
Python dictionaries (defined in YAML). The examples below are intended as
templates.

To set up a parameter sweep, two to four dictionaries must be provided in the
`lume-ace3p` input file:

- `workflow_parameters` — filenames, HPC settings, and other configuration
  used for the parameter sweep.
- `cubit_input_parameters` (or, equivalently, `input_parameters`) — input
  names and corresponding vector values to sweep through, for parameters
  pertaining to the geometry. The two keys are aliases; the Omega3P
  example below uses `cubit_input_parameters` for clarity, while the S3P
  example uses `input_parameters`.
- `ace3p_input_parameters` — input names and corresponding vector values to
  sweep through, for parameters pertaining to ACE3P settings.
- `output_parameters` — output quantities to store in an output array for
  printing (Omega3P only).

Once these dicts are defined in the `.yaml` file, the parameter sweep can be
run with the `run_lume_ace3p.py` entry point.

## Omega3P parameter sweep example

This example (based on the rounded-top pillbox from the
[ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23))
configures `lume-ace3p` to run a parameter sweep over cavity radius and cavity
wall ellipticity. The goal is to automate the entire mesh-generation,
Omega3P calculation, and mode postprocessing pipeline into a single Python
script submitted to HPC resources.

The script begins with the workflow parameters:

```yaml
workflow_parameters :
  'mode' : 'parameter_sweep'
  'module' : 'omega3p'
  'cubit_input' : 'pillbox-rtop.jou'
  'ace3p_input' : 'pillbox-rtop.omega3p'
  'rfpost_input' : 'pillbox-rtop.rfpost'
  'ace3p_tasks' : 16
  'ace3p_cores' : 16
  'ace3p_opts' : '--cpu-bind=cores'
  'workdir' : 'lume-ace3p_omega3p_workdir'
  'workdir_mode' : 'auto'
  'sweep_output' : True
  'sweep_output_file' : 'omega3p_sweep_output.txt'
```

This dictionary contains input file paths (assumed to be in the same
directory), working-directory settings, and HPC-specific commands for ACE3P.
For this example: workflows are run in separate sub-directories (auto-named
from input values), and Omega3P uses 16 MPI tasks × 16 cores/task with
`--cpu-bind=cores`. `sweep_output` enables file output to `sweep_output_file`.
See [](yaml_reference.md) for full details.

Next, Cubit input parameters:

```yaml
cubit_input_parameters :
  'cav_radius' :
    'min' : 90.0
    'max' : 120.0
    'num' : 4
  'ellipticity' :
    'min' : 0.5
    'max' : 1.25
    'num' : 4
```

`cubit_input_parameters` is a key-value mapping where each key is the
**exact** name of a variable defined in the Cubit journal file, and each
value is either a list of numeric inputs or a nested dict with `min`, `max`,
`num` (linearly spaced).

Then, ACE3P input parameters:

```yaml
ace3p_input_parameters :
'ModelInfo' :
    'SurfaceMaterial' :
        'ReferenceNumber' : 6
        'Sigma' : [5.8e7, 1.04e7]
```

`ace3p_input_parameters` is a nested mapping organized by ACE3P file
hierarchy. Here the swept parameter is the conductivity of the surface with
`ReferenceNumber` 6. Values can use `min/max/num`, a list, or a single value
if not swept.

In this example `cav_radius` and `ellipticity` are length-4 vectors, and
`Sigma` has two values, giving 4 × 4 × 2 = 32 workflow evaluations. Because
`workdir_mode` is `auto`, each evaluation creates a folder named
`lume-ace3p_demo_workdir_X_Y_Z` for a total of 32 folders.

Then, output parameters:

```yaml
output_parameters :
  'R/Q' : ['RoverQ', '0', 'RoQ']
  'Mode_freq' : ['RoverQ', '0', 'Frequency']
  'E_max' : ['maxFieldsOnSurface', '6', 'Emax']
  'loc_x' : ['maxFieldsOnSurface', '6', 'Emax_location', 'x']
  'loc_y' : ['maxFieldsOnSurface', '6', 'Emax_location', 'y']
  'loc_z' : ['maxFieldsOnSurface', '6', 'Emax_location', 'z']
```

`output_parameters` maps user-chosen labels to a list specifying the section
id (`'RoverQ'`), mode/surface id string (`'0'`), and entry name (`'RoQ'`)
extracted from `rfpost.out`. The `sweep_output_file` is a tab-delimited file
with one column per input or output, one row per workflow evaluation.

In this example the first row contains 8 text entries (`cav_radius`,
`ellipticity`, `R/Q`, `mode_freq`, `E_max`, `loc_x`, `loc_y`, `loc_z`); each
subsequent row holds the input values and the corresponding 6 outputs. See
[](yaml_reference.md) for the full list of supported output sections.

If no output dict is specified, the parameter sweep still runs but
`rfpost.out` data will not be parsed or tabulated (useful when only the
per-combination output folders are wanted).

`lume-ace3p` does not currently support checkpointing, and each workflow
evaluation is run serially. Future versions may allow concurrent evaluations.

## S3P parameter sweep example

This example (based on a 90-degree bend from the
[ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23))
runs a parameter sweep over outer corner cut radius and inner corner rounding
radius. The S-parameter results are stored in a text file with all
combinations of parameters and frequencies.

```yaml
workflow_parameters :
  'mode' : 'parameter_sweep'
  'module' : 's3p'
  'cubit_input' : 'bend-90degree.jou'
  'ace3p_input' : 'bend-90degree.s3p'
  'ace3p_tasks' : 32
  'ace3p_cores' : 4
  'ace3p_opts' : '--cpu-bind=cores'
  'workdir' : 'lume-ace3p_s3p_workdir'
  'workdir_mode' : 'auto'
  'sweep_output' : True
  'sweep_output_file' : 's3p_sweep_output.txt'
```

```yaml
input_parameters :
  'cornercut' :
    'min' : 12.0
    'max' : 16.0
    'num' : 5
  'rcorner2' :
    'min' : 4.0
    'max' : 16.0
    'num' : 3
```

:::{note}
Frequencies to scan with S3P are not "inputs" set here — they are set in the
`.s3p` input file directly.
:::

In this example `cornercut` and `rcorner2` are length 5 and 3, giving 5 × 3 =
15 workflow evaluations and 15 distinct folders.

Unlike Omega3P, the S3P parameter sweep does not use any output dict — only
the `s3p_results/Reflection.out` file inside each workflow directory. Those
contents are collected for each S3P run and combined into a single
`sweep_output_file`, with extra columns for all combinations of inputs.

In the example, S3P scans 13 frequencies for each of the 15 workflow
evaluations, giving 195 lines of data in `sweep_output_file`. Each line has
`cornercut`, `rcorner2`, and `frequency`, followed by the four S-parameters
of the 2-port system (`S(0,0)`, `S(0,1)`, `S(1,0)`, `S(1,1)`).

### S3P parameter sweep with no separate ACE3P file

Identical to the previous example, except no `.s3p` file is submitted. All
S3P parameters are specified in `ace3p_input_parameters`. Modify the S3P
sweep `.batch` file to run `s3p_sweep_no_s3p_file.yaml`.

```yaml
ace3p_input_parameters :
'ModelInfo' :
  'File' : './bend-90degree.ncdf'

  'BoundaryCondition' :
    'Exterior' : 6
    'Waveguide' : 7,8

'FiniteElement' :
  'Order' : 2
  'CurvedSurfaces' : 'on'

'FrequencyScan':
  'Start' : 9.424e+9
  'End' : 12.424e+9
  'Interval' : 0.25e+9

'Port':
  'ReferenceNumber' : 7
  'NumberOfModes' : 1

'Port' :
  'ReferenceNumber': 8
  'NumberOfModes' : 1
```

This functions exactly the same as the previous example. Errors may arise if
a necessary ACE3P input parameter is missing.

## Viewing S3P parameter sweep output

A simple plotting tool is included with `lume-ace3p` which reads
`sweep_output_file` from an S3P workflow and plots the results in an
interactive plot. To use it, run `s3p_sweep_plot.py` and load the appropriate
S3P `sweep_output_file` from the file prompt. Try `s3p_demo_sweep_output.txt`
in the `plotting` folder for an interactive demo. See [](plotting.md) for
details.

## Gaussian-process (low-fidelity) parameter sweep

For S3P, `lume-ace3p` also supports a low-fidelity sweep mode that fits a
Gaussian Process to the simulator output and then samples the GP on a
tensor grid — useful for cheaply exploring parameter space without
running every grid point through S3P. The mode is selected with
`mode: 'gp_parameter_sweep'` and `module: 's3p'`.

Three sections must be supplied in addition to `workflow_parameters`:

- `sweep_parameters` — the tensor grid the trained GP is evaluated on.
- `vocs_parameters` — Xopt VOCS for the exploration phase. The
  `objectives` block uses `'explore'` as the optimization keyword.
- `xopt_parameters` — Xopt driver settings. `num_step` controls the
  number of GP-guided exploration steps; `num_random` (default 5)
  controls the random-seeding phase; `improvement_threshold` (default
  0.01) and `patience` (default 5) configure early stopping.

A complete example is shipped as
[examples/s3p_bayesian_sweep/s3p_bayesian_sweep.yaml](https://github.com/slaclab/lume-ace3p/blob/main/examples/s3p_bayesian_sweep/s3p_bayesian_sweep.yaml):

```yaml
workflow_parameters :
    'mode' : 'GP_parameter_sweep'
    'module' : 's3p'
    'cubit_input': 'bend-90degree_mf.jou'
    'ace3p_input': 'bend-90degree_mf.s3p'
    'ace3p_tasks': 16
    'ace3p_cores': 4

sweep_parameters :
    'cornercut' :
        min : 12.5
        max : 13.5
        num : 10
    'wgwidth' :
        min : 21
        max : 22
        num : 10

vocs_parameters :
    'variables' :
        'cornercut': [12.5, 13.5]
        'wgwidth':   [21, 22]
    'objectives' :
        'S(1,1)_12.0e+09': 'explore'

xopt_parameters :
    num_step : 3
```

The GP-sampled grid is written to `sweep_output.txt`; the actual S3P
evaluations performed during exploration are written to
`sim_output.txt` and `sim_output_all_values.txt`.

## Track3P particle weighting

The `mode: 'particle_weight'` workflow is a standalone post-processing
step that reads a Track3P particle dump, filters by impact order /
face id, bins by axial position, and writes a weighted-particle file
suitable as a Geant4 source. No ACE3P invocation occurs.

```yaml
workflow_parameters :
  'mode' : 'particle_weight'
  'module' : 'track3p'
  'particle_input' : 'sample_track3p_particles.txt'
  'particle_output' : 'track3p_particles_weighted.txt'

particle_parameters :
  'impact_order'   : 1
  'impact_face_id' : 4
  'work_function'  : 4.5
  'dt'             : 1.0e-10
  'num_bins'       : 8
  'beta'           : [50, 55, 60, 65, 65, 60, 55, 50]
```

See [](yaml_reference.md#particle_parameters) for the full key list.

## Geant4 dose-calculation workflow

The `mode: 'geant4'` workflow drives a Geant4 application using a macro
file, optional geometry files, and a particle-source file. The source
file can either be supplied directly via `geant4_particle_file` or
generated on the fly from a Track3P dump by also providing a
`particle_parameters` section (chaining the same logic as
`mode: 'particle_weight'`).

A minimal YAML skeleton:

```yaml
workflow_parameters :
  'mode' : 'geant4'
  'module' : 'geant4'
  'geant4_input'          : 'dose.mac'
  'geant4_geometry_files' : ['cavity.gdml']
  'geant4_particle_file'  : 'track3p_particles_weighted.txt'
  'geant4_scoring_output' : 'dose_scoring.txt'
  'geant4_threads'        : 4

geant4_input_parameters :
  '/gun/energy' : 1.0
  # additional macro overrides…

output_parameters :
  'total_dose' : ['scoring', 'total']
  'peak_dose'  : ['scoring', 'peak']
```

Geant4 paths are resolved through the same precedence chain as ACE3P —
see [](installation.md#executable-paths). If `GEANT4_APP_PATH` /
`GEANT4_APP_EXE` (or YAML / site-default equivalents) are unset,
dry-run mode is auto-enabled.
