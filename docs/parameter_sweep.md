# Parameter sweeping

`lume-ace3p` has two main use-cases: parameter sweeping and optimization. In
both, an ACE3P workflow is evaluated many times with parameters set in a YAML
file. A parameter sweep is a **mode** (`type: parameter_sweep`) that runs the
`workflow:` chain over the full tensor product of the swept input axes. The
examples below are intended as templates.

A parameter sweep input file needs:

- `workflow:`, the ordered module chain to run (e.g. `cubit → omega3p →
  acdtool`, or `cubit → s3p`). Solver settings (`tasks`, `cores`, `opts`, the
  input file) live on the module entries.
- `mode:` with `type: parameter_sweep` and an `output_file` for the result
  table.
- `input_parameters`, the swept input space, organized into per-code
  sub-blocks:
  - `cubit:` names and vector values for Cubit journal (geometry) knobs.
  - `ace3p:` (optional) parameters inside the ACE3P input file.
  - `geant4:` (optional) overrides for the Geant4 input file.
  - `particles:` (optional) knobs of the `particles` module.

  A single sweep can span all four sub-blocks; every array-valued leaf across
  them multiplies into the tensor product. The old flat keys
  (`cubit_input_parameters`, `ace3p_input_parameters`,
  `geant4_input_parameters`, `particles_input_parameters`, and a bare
  `input_parameters` treated as the cubit block) are still accepted, but the
  nested notation is the standard and is used throughout the examples below.
- `output_parameters` (optional), the output quantities to extract into the
  result table.

Once these are defined, run the sweep with the `run-lume-ace3p` entry point.

## Omega3P parameter sweep example

This example (based on the rounded-top pillbox from the
[ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23),
shipped as
[`examples/omega3p_sweep`](https://github.com/slaclab/lume-ace3p/blob/main/examples/omega3p_sweep/omega3p_sweep.yaml))
sweeps cavity radius and cavity wall ellipticity, running the whole
mesh-generation, Omega3P, and mode-postprocessing pipeline as a single HPC job.

The file begins with the workflow-level settings, the module chain, and the
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
    tasks : 12
    cores : 8
    opts : '--cpu-bind=cores'
  - module : acdtool
    input : 'pillbox-rtop.rfpost'

mode :
  type : parameter_sweep
  output_file : 'omega3p_sweep_output.txt'
```

`workflow_parameters` holds only directory settings here: `workdir_mode: auto`
runs each evaluation in its own sub-directory, named from the input values.
Each module entry names its own input file and, for the solver, its MPI
settings: 12 tasks × 8 cores/task with `--cpu-bind=cores`. `tasks × cores` must
fit inside the job allocation (e.g. 120 CPUs on an S3DF milano node). The
`mode` block enables the result table written to `output_file`. See
[](yaml_reference.md) for full details.

Next, Cubit input parameters:

```yaml
input_parameters :
  cubit :
    'cav_radius' :
      'min' : 90.0
      'max' : 120.0
      'num' : 4
    'ellipticity' :
      'min' : 0.5
      'max' : 1.25
      'num' : 4
```

In the `cubit:` sub-block each key is the **exact** name of a variable defined
in the Cubit journal file. Each value is either a list of numbers or a nested
dict with `min`, `max`, `num` (linearly spaced).

ACE3P input parameters live in the `ace3p:` sub-block of the same
`input_parameters` mapping:

```yaml
input_parameters :
  ace3p :
    'ModelInfo' :
        'SurfaceMaterial' :
            'ReferenceNumber' : 6
            'Sigma' : [5.8e7, 1.04e7]
```

The `ace3p:` sub-block is a nested mapping that follows the ACE3P file
hierarchy. Here the swept parameter is the conductivity of the surface with
`ReferenceNumber` 6. Values can use `min/max/num`, a list, or a single value if
not swept. In practice the `cubit:` and `ace3p:` sub-blocks are written under
one `input_parameters:` header; see
[`examples/omega3p_ace3p_param_sweep`](https://github.com/slaclab/lume-ace3p/blob/main/examples/omega3p_ace3p_param_sweep/omega3p_ace3p_param_sweep.yaml).

Here `cav_radius` and `ellipticity` are length-4 vectors and `Sigma` has two
values, giving 4 × 4 × 2 = 32 workflow evaluations. With `workdir_mode: auto`
each evaluation gets a folder named from the `workdir` base plus the swept
scalar values (e.g. `lume-ace3p_omega3p_workdir_90.0_0.5_58000000.0`), 32 in
total. The full `cubit → omega3p → acdtool` chain, including the in-`cubit`
meshconvert, re-runs in each folder.

Then, output parameters:

```yaml
output_parameters :
  'R/Q' : {module: acdtool, section: RoverQ, quantity: RoQ, at: {mode: 0}}
  'Mode_freq' : {module: omega3p, quantity: Frequency, at: {mode: 0}}
  'E_max' : {module: acdtool, section: maxFieldsOnSurface, quantity: Emax, at: {surface: 6}}
  'loc_x' : {module: acdtool, section: maxFieldsOnSurface, quantity: Emax_location, component: x, at: {surface: 6}}
  'loc_y' : {module: acdtool, section: maxFieldsOnSurface, quantity: Emax_location, component: y, at: {surface: 6}}
  'loc_z' : {module: acdtool, section: maxFieldsOnSurface, quantity: Emax_location, component: z, at: {surface: 6}}
```

`output_parameters` maps user-chosen labels to an extraction spec naming the
module, the quantity wanted, and, for an indexed result, which index. `R/Q`
and `E_max` come from `acdtool`, which reads them out of `rfpost.out`.
`Mode_freq` comes from Omega3P's own eigenmode output and needs no
postprocessing block. Dropping `at:` from a mode-indexed acdtool spec returns
*every* mode, for a dispersion curve or an HOM catalog. The full spec grammar
is in [](yaml_reference.md#output_parameters); the `.rfpost` block surface is
in [](acdtool_reference.md).

The `output_file` is a tab-delimited table with one column per input or output
and one row per workflow evaluation. Here it has 9 columns: one per swept axis
(`cav_radius`, `ellipticity`, and the swept ACE3P leaf, labeled by its path
`ace3p:ModelInfo.SurfaceMaterial.Sigma`) followed by the 6 declared outputs
(`R/Q`, `Mode_freq`, `E_max`, `loc_x`, `loc_y`, `loc_z`). See
[](yaml_reference.md) for the full list of supported output sections.

Without `output_parameters` the sweep still runs, but nothing from
`rfpost.out` is parsed or tabulated. Use this when only the per-combination
output folders are wanted.

Each workflow evaluation is run serially. Future versions may allow concurrent
evaluations.

(resuming-a-sweep)=
## Resuming a sweep that was cut off

A sweep of long solves rarely fits in one allocation. Every evaluation records
what it did in a [run manifest](#run-manifest) in its own workdir, and
`mode: {resume: true}` reads that record back.

```yaml
workflow_parameters :
  'workdir' : 'lume-ace3p_omega3p_workdir'
  'workdir_mode' : 'indexed'      # per-point directories: _0, _1, _2, …

mode :
  type : parameter_sweep
  resume : True
  output_file : 'omega3p_sweep_output.txt'
```

Re-run the same command after a job dies and the sweep picks up where it
stopped. Finished points contribute their rows without launching a solver, a
point that died partway restarts at the step that did not finish, and points
that never started run normally. The result table is the same table an
uninterrupted run would have produced.

Before re-running, `--status` shows what is there:

```console
$ run-lume-ace3p --status omega3p_sweep.yaml
 - 32 point(s) implied by this configuration: 19 complete, 1 failed, 12 absent
```

Two requirements: `workdir_mode` must not be `manual`, since each point needs
its own directory to have its own state (`resume: true` with `manual` is
refused, naming `indexed`), and `resume` is opt-in. Each point's manifest also
carries a hash of the resolved configuration, so a point whose module settings
or input values have changed is re-run, and says so.

The full per-point rules, including what happens when a completed point's
output files have been deleted, are in [](#resume).

## S3P parameter sweep example

This example (based on a 90-degree bend from the
[ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23))
sweeps the outer corner cut radius and the inner corner rounding radius. The
S-parameter results are stored in a text file with all combinations of
parameters and frequencies.

```yaml
workflow_parameters :
  'workdir' : 'lume-ace3p_s3p_workdir'
  'workdir_mode' : 'auto'

workflow :
  - module : cubit
    journal : 'bend-90degree.jou'
  - module : s3p
    input : 'bend-90degree.s3p'
    tasks : 16
    cores : 4
    opts : '--cpu-bind=cores'

mode :
  type : parameter_sweep
  output_file : 's3p_sweep_output.txt'
```

```yaml
input_parameters :
  cubit :
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
Frequencies to scan with S3P are not "inputs" set here. They are set in the
`.s3p` input file directly.
:::

Here `cornercut` and `rcorner2` have lengths 5 and 3, giving 5 × 3 = 15
workflow evaluations and 15 distinct folders.

S3P exposes a frequency field index, so its sweep table is emitted in **long
format**: one row per `(grid-point, frequency)` rather than one per grid point.
Each declared output such as `{module: s3p, quantity: 'S(0,0)'}` becomes a
column sampled at that row's frequency. `output_parameters` are optional: with
none declared the table carries only the swept inputs and `Frequency`, one row
per scan point, and the S-parameters stay in each workdir's `s3p_results/`.

The example declares the four `S(m,n)` spectra and one scalar,
`reflection_12GHz`, which is `S(0,0)` picked `at: {frequency: 12.0e+09}`. S3P
scans 13 frequencies (9.5 to 12.5 GHz) for each of the 15 evaluations, giving
195 rows in `output_file`. A scalar output repeats down its point's rows, and
an `at:` frequency that is not a scan point raises.

If every declared output is narrowed to a scalar (`at: {frequency: …}` for
S3P, `at: {mode: n}` for Omega3P), the table is wide again: one row per grid
point, with the solver's arrays persisted as that row's field artifact.

### S3P parameter sweep with no separate ACE3P file

Identical to the previous example, except no `.s3p` file is submitted: all
S3P parameters go in the `ace3p:` sub-block of `input_parameters`. Modify the
S3P sweep `.batch` file to run `s3p_sweep_no_s3p_file.yaml`.

```yaml
input_parameters :
  ace3p :
    'ModelInfo' :
      'File' : './bend-90degree.ncdf'

      'BoundaryCondition' :
        'Exterior' : 6
        'Waveguide' : 7,8

    'FiniteElement' :
      'Order' : 2
      'CurvedSurfaces' : 'on'

    'FrequencyScan':
      'Start' : 9.5e+9
      'End' : 12.5e+9
      'Interval' : 0.25e+9

    'Port':
      'ReferenceNumber' : 7
      'NumberOfModes' : 1

    'Port' :
      'ReferenceNumber': 8
      'NumberOfModes' : 1
```

Note the two `'Port'` blocks at the same indentation level. ACE3P allows
duplicate-named sibling sections (one per port, surface, boundary condition,
…) and the `ace3p:` parser preserves them verbatim, matching entries
positionally with the ACE3P input file rather than collapsing them into a
Python `dict`. See [](ace3p_input_parameters) for details.

The run is identical to the previous example. Errors may arise if a necessary
ACE3P input parameter is missing.

## Viewing S3P parameter sweep output

`lume-ace3p` includes a simple plotting tool that reads the S3P sweep result
table (the `mode.output_file`) and shows it in an interactive plot. Run
`s3p_sweep_plot.py` and load the S3P `output_file` from the file prompt. Try
`s3p_demo_sweep_output.txt` in the `plotting` folder for a demo. See
[](plotting.md) for details.

## Gaussian-process (low-fidelity) parameter sweep

The `gp_parameter_sweep` mode (`mode: {type: gp_parameter_sweep}`) fits a
Gaussian Process to the simulator output during a Bayesian-exploration phase,
then samples the GP posterior mean on a tensor grid. This explores parameter space cheaply without
running every grid point through the solver.

Three sections must be supplied in addition to the `workflow:` chain:

- `sweep_parameters`: the tensor grid the trained GP is evaluated on.
- `vocs_parameters`: Xopt VOCS for the exploration phase. The `objectives`
  block maps an `output_parameters` name to `'explore'`.
- `xopt_parameters`: Xopt driver settings. `max_steps` caps the GP-guided
  exploration steps; `num_random` (default 5) sets the random-seeding phase;
  `improvement_threshold` (default 0.01) and `patience` (default 5) configure
  early stopping.

A complete example is shipped as
[examples/s3p_bayesian_sweep/s3p_bayesian_sweep.yaml](https://github.com/slaclab/lume-ace3p/blob/main/examples/s3p_bayesian_sweep/s3p_bayesian_sweep.yaml):

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
    type : gp_parameter_sweep
    output_file : 'sim_output.txt'
    sweep_output_file : 'sweep_output.txt'

output_parameters :
    'S(1,1)_12.0e+09' : { module: s3p, quantity: 'S(1,1)', at: { frequency: 12.0e+09 } }

input_parameters :
    cubit :
        'cornercut' : 13.0
        'wgwidth' : 21.5

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

The explored objective (`S(1,1)_12.0e+09`) is an `output_parameters` name.
The GP posterior-mean grid is written to `sweep_output_file`; the S3P
evaluations made during exploration are logged to the `output_file` trajectory
table.

## Track3P particle weighting

Field-emission particle weighting is the `particles` module, a post-processing
step that reads a Track3P particle dump, filters by impact order and face id,
bins by axial position, and writes a weighted-particle file usable as a Geant4
source. There is no ACE3P solver in this chain: a `track3p_source` module
supplies the external dump, and the pure-Python weighting runs in `single`
mode. This is
[`examples/track3p_particle_weight`](https://github.com/slaclab/lume-ace3p/blob/main/examples/track3p_particle_weight/track3p_particle_weight.yaml):

```yaml
workflow_parameters :
  'workdir' : 'lume-ace3p_track3p_workdir'
  'workdir_mode' : 'manual'

workflow :
  - module : track3p_source
    file : '../assets/sample_track3p_particles.txt'
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

`output_format: 'track3p'` writes the weighted Track3P dump (all filtered
columns plus `Bin` and `ParticleWeight`); the module default `'geant4'` writes
the 10-column Geant4 source file. See
[](yaml_reference.md#particles-module-keys) for the full key list.

## Geant4 dose-calculation workflow

The `geant4` module drives a Geant4 application using a single plain-text input
file (`key = value` lines, `#` comments) that names its own geometry STL files,
scoring mesh, thread count, and output files. The particle source comes from an
upstream module: either a `particles` weighting step (fed by a
`track3p_source`) or a `particle_source` module naming a prebuilt Geant4-format
file. The module writes the source filename into the input file (the executable
derives the event count from the particle file), stages the STL files it names
into each working directory, and reads the dose and energy-deposit output files
after the run. STLs named in the input file are located next to it by default.
When they live elsewhere (e.g. a shared `assets/` directory), list them under
`geant4_geometry_files` so the module can find and stage them.

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
    file : '../assets/sample_track3p_particles.txt'
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
  'total_dose' : {module: geant4, section: dose, quantity: total}
  'peak_dose'  : {module: geant4, section: dose, quantity: peak}
  'total_edep' : {module: geant4, section: edep, quantity: total}
```

Optional Geant4 input-file overrides go in the `geant4:` sub-block of
`input_parameters` (plain keys, no `/`); a swept override there is one more
sweep axis alongside any `cubit:`/`ace3p:` axes. Geant4 paths are resolved
through the same precedence chain as ACE3P; see
[](installation.md#executable-paths). If `GEANT4_APP_PATH` / `GEANT4_APP_EXE`
(or YAML / site-default equivalents) are unset, dry-run mode is auto-enabled:
the `particles` weighting still runs for real and only the Geant4 binary is
skipped.
