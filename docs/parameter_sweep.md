# Parameter sweeping

`lume-ace3p` has two main use-cases: parameter sweeping and optimization. For
both, an ACE3P workflow is evaluated many times according to parameters set in
a YAML file. A parameter sweep is a **mode** (`type: parameter_sweep`) that
drives the declarative `workflow:` chain over the full tensor product of the
swept input axes. The examples below are intended as templates.

To set up a parameter sweep, provide in the `lume-ace3p` input file:

- a `workflow:` list — the ordered module chain to run (e.g. `cubit → omega3p →
  acdtool`, or `cubit → s3p`). Solver settings (`tasks`, `cores`, `opts`, the
  input file) live on the module entries.
- `mode:` with `type: parameter_sweep` and an `output_file` for the result
  table.
- `cubit_input_parameters` (or, equivalently, `input_parameters`) — input
  names and corresponding vector values to sweep through, for geometry
  parameters. The two keys are aliases; the Omega3P example below uses
  `cubit_input_parameters` for clarity, while the S3P example uses
  `input_parameters`.
- `ace3p_input_parameters` (optional) — input names and vector values to sweep,
  for parameters inside the ACE3P input file.
- `output_parameters` (optional) — output quantities to extract into the result
  table.

Once these are defined in the `.yaml` file, the parameter sweep is run with the
`run-lume-ace3p` entry point.

## Omega3P parameter sweep example

This example (based on the rounded-top pillbox from the
[ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23),
shipped as
[`examples/omega3p_sweep`](https://github.com/slaclab/lume-ace3p/blob/main/examples/omega3p_sweep/omega3p_sweep.yaml))
configures `lume-ace3p` to run a parameter sweep over cavity radius and cavity
wall ellipticity. The goal is to automate the entire mesh-generation,
Omega3P calculation, and mode postprocessing pipeline into a single job
submitted to HPC resources.

The script begins with the workflow-level settings, the module chain, and the
mode:

```yaml
workflow_parameters :
  'workdir' : 'lume-ace3p_omega3p_workdir'
  'workdir_mode' : 'auto'

workflow :
  - module : cubit
    journal : 'pillbox-rtop.jou'
  - module : omega3p
    input : 'pillbox-rtop.omega3p'
    tasks : 16
    cores : 16
    opts : '--cpu-bind=cores'
  - module : acdtool
    input : 'pillbox-rtop.rfpost'

mode :
  type : parameter_sweep
  output_file : 'omega3p_sweep_output.txt'
```

`workflow_parameters` holds only directory settings here: workflows are run in
separate sub-directories (`workdir_mode: auto`, auto-named from input values).
Each module entry names its own input file and, for the solver, its MPI settings
(16 tasks × 16 cores/task with `--cpu-bind=cores`). The `mode` block enables the
result table written to `output_file`. See [](yaml_reference.md) for full
details.

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

`output_parameters` maps user-chosen labels to a bare-form list specifying the
section id (`'RoverQ'`), mode/surface id string (`'0'`), and entry name
(`'RoQ'`); the workflow routes each to the `acdtool` module, which extracts it
from `rfpost.out`. The `output_file` is a tab-delimited table with one column
per input or output, one row per workflow evaluation.

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
  'workdir' : 'lume-ace3p_s3p_workdir'
  'workdir_mode' : 'auto'

workflow :
  - module : cubit
    journal : 'bend-90degree.jou'
  - module : s3p
    input : 'bend-90degree.s3p'
    tasks : 32
    cores : 4
    opts : '--cpu-bind=cores'

mode :
  type : parameter_sweep
  output_file : 's3p_sweep_output.txt'
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

S3P exposes a frequency field index, so its sweep table is emitted in
**long format**: one row per `(grid-point, frequency)` rather than one row per
grid point. `output_parameters` are optional here — even with none declared, the
per-frequency S-parameters are tabulated because the frequency index alone
drives the long-format rows.

In the example, S3P scans 13 frequencies for each of the 15 workflow
evaluations, giving 195 rows in `output_file`. Each row has `cornercut`,
`rcorner2`, and `Frequency`, followed by the four S-parameters of the 2-port
system (`S(0,0)`, `S(0,1)`, `S(1,0)`, `S(1,1)`).

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

Note the two `'Port'` blocks at the same indentation level. ACE3P
allows duplicate-named sibling sections (one per port, surface,
boundary condition, …), and the `ace3p_input_parameters` parser
preserves them verbatim — entries are matched positionally with the
ACE3P input file rather than collapsed into a Python `dict`. See
[](yaml_reference.md#ace3p_input_parameters) for details.

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

`lume-ace3p` also supports a Bayesian-exploration sweep mode that fits a
Gaussian Process to the simulator output and then samples the GP posterior mean
on a tensor grid — useful for cheaply exploring parameter space without running
every grid point through the solver. The mode is selected with
`mode: {type: gp_parameter_sweep}`.

Three sections must be supplied in addition to the `workflow:` chain:

- `sweep_parameters` — the tensor grid the trained GP is evaluated on.
- `vocs_parameters` — Xopt VOCS for the exploration phase. The `objectives`
  block maps an `output_parameters` name to `'explore'`.
- `xopt_parameters` — Xopt driver settings. `max_steps` caps the GP-guided
  exploration steps; `num_random` (default 5) controls the random-seeding
  phase; `improvement_threshold` (default 0.01) and `patience` (default 5)
  configure early stopping.

A complete example is shipped as
[examples/s3p_bayesian_sweep/s3p_bayesian_sweep.yaml](https://github.com/slaclab/lume-ace3p/blob/main/examples/s3p_bayesian_sweep/s3p_bayesian_sweep.yaml):

```yaml
workflow_parameters :
    'workdir' : 'lume-ace3p_mf_workdir'

workflow :
  - module : cubit
    journal : 'bend-90degree_mf.jou'
  - module : s3p
    input : 'bend-90degree_mf.s3p'
    tasks : 16
    cores : 4
    opts : '--cpu-bind=cores'

mode :
    type : gp_parameter_sweep
    output_file : 'sim_output.txt'
    sweep_output_file : 'sweep_output.txt'

output_parameters :
    'S(1,1)_12.0e+09' : { module: s3p, quantity: 'S(1,1)', at: { frequency: 12.0e+09 } }

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
    max_steps : 3
```

The explored objective (`S(1,1)_12.0e+09`) is an `output_parameters` name, so
the driver pulls it from the workflow generically. The GP posterior-mean grid is
written to the `sweep_output_file`; the actual S3P evaluations performed during
exploration are logged to the `output_file` trajectory table.

## Track3P particle weighting

Field-emission particle weighting is the `particles` module — a
post-processing step that reads a Track3P particle dump, filters by impact order
/ face id, bins by axial position, and writes a weighted-particle file suitable
as a Geant4 source. There is no ACE3P solver in this chain; the external dump is
supplied by a `track3p_source` module and the whole thing runs in `single` mode
(the weighting is pure Python). This is
[`examples/track3p_particle_weight`](https://github.com/slaclab/lume-ace3p/blob/main/examples/track3p_particle_weight/track3p_particle_weight.yaml):

```yaml
workflow_parameters :
  'workdir' : 'lume-ace3p_track3p_workdir'
  'workdir_mode' : 'manual'

workflow :
  - module : track3p_source
    file : 'sample_track3p_particles.txt'
  - module : particles
    impact_order : 1
    impact_face_id : 4
    work_function : 4.5
    dt : 1.0e-10
    num_bins : 8
    beta : [50, 55, 60, 65, 65, 60, 55, 50]
    output_format : 'track3p'
    output : 'track3p_particles_weighted.txt'

mode :
  type : single
```

`output_format: 'track3p'` writes the weighted-Track3P dump (all filtered
columns plus `Bin` and `ParticleWeight`); the module default `'geant4'` instead
writes the 10-column Geant4 source file. See
[](yaml_reference.md#particles-module-keys) for the full key list.

## Geant4 dose-calculation workflow

The `geant4` module drives a Geant4 application using a single plain-text input
file (`key = value` lines, `#` comments) that names its own geometry STL files,
scoring mesh, thread count, and output files. The particle source is supplied by
an upstream module in the chain — either a `particles` weighting step (fed by a
`track3p_source`) or a `particle_source` module naming a prebuilt Geant4-format
file directly. The `geant4` module writes the source filename and the matching
`beam_on` particle count into the input file, copies the STL files it names into
each working directory, and reads the dose / energy-deposit output files after
the run.

The full runnable chain (`track3p_source → particles → geant4`) is shipped as
[`examples/geant4_track3p_beta`](https://github.com/slaclab/lume-ace3p/blob/main/examples/geant4_track3p_beta/geant4_track3p_beta.yaml)
(a `beta` sweep) and
[`examples/geant4_dose_single`](https://github.com/slaclab/lume-ace3p/blob/main/examples/geant4_dose_single/geant4_dose_single.yaml)
(a single evaluation). A minimal skeleton:

```yaml
workflow_parameters :
  'workdir' : 'lume-ace3p_dose_workdir'
  'workdir_mode' : 'manual'

workflow :
  - module : track3p_source
    file : 'sample_track3p_particles.txt'
  - module : particles
    impact_order : 1
    impact_face_id : 6
    work_function : 4.5
    dt : 1.0e-10
    num_bins : 8
    beta : [50, 55, 60, 65, 65, 60, 55, 50]
    output_format : 'geant4'
    output : 'particles.data'      # must match the 'particles = ...' line in the Geant4 input
  - module : geant4
    geant4_input : 'input_7cell.geant4'

mode :
  type : single
  output_file : 'dose_single_output.txt'

output_parameters :
  'total_dose' : ['dose', 'total']
  'peak_dose'  : ['dose', 'peak']
  'total_edep' : ['edep', 'total']
```

Optional Geant4 input-file overrides go in `geant4_input_parameters` (plain
keys, no `/`); a swept override there becomes an additional sweep axis. Geant4
paths are resolved through the same precedence chain as ACE3P — see
[](installation.md#executable-paths). If `GEANT4_APP_PATH` / `GEANT4_APP_EXE`
(or YAML / site-default equivalents) are unset, dry-run mode is auto-enabled
(the `particles` weighting still runs for real; only the Geant4 binary is
skipped).
