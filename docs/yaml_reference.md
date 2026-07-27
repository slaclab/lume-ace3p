# YAML configuration reference

`lume-ace3p` is driven by a YAML configuration file. The task chain is declared
by a top-level **`workflow:`** list of modules and driven by a **`mode:`**
block; `input_parameters` and `output_parameters` configure the swept knobs and
the extracted scalars. For an optimization problem, `vocs_parameters` and
`xopt_parameters` are added. The `workflow_parameters` block holds only
directory- and path-level settings — the solver/file settings that used to live
there now sit on the individual `workflow:` module entries.

## `workflow:`

An ordered list of module entries; each entry is a mapping with a `module` key
naming the module type plus that module's own keys. The list order is only a
tiebreaker — the real run order is computed by resolving each module's
artifact dependencies into a DAG (mesh before solver, solver before acdtool,
particle source before Geant4, …). Declaring two producers of the same artifact,
or a module whose requirement nothing provides, is a validation error.

| Module type       | Provides            | Requires           | Key config keys |
|-------------------|---------------------|--------------------|-----------------|
| `cubit`           | mesh                | —                  | `journal:` (Cubit `.jou`); `meshconvert:` (bool, default `True`). |
| `mesh`            | mesh                | —                  | `file:` — a prebuilt mesh file (declarative replacement for the old `skip_cubit` + supplied mesh). |
| `omega3p`         | em_solution         | mesh               | `input:` (`.omega3p`); `tasks:`, `cores:`, `opts:` (MPI settings). |
| `s3p`             | em_solution         | mesh               | `input:` (`.s3p`); `tasks:`, `cores:`, `opts:`. |
| `acdtool`         | rf_post             | em_solution        | `input:` (`.rfpost`). Owns extraction of the `RoverQ`/`kickFactor`/`maxFieldsOnSurface` scalars. |
| `track3p_source`  | track3p_particles   | —                  | `file:` — an externally-produced Track3P dump (there is no in-pipeline Track3P solver). |
| `particles`       | particle_source     | track3p_particles  | Field-emission weighting keys — see [](#particles-module-keys). |
| `particle_source` | particle_source     | —                  | `file:` — a prebuilt Geant4-format source file (bypasses the `particles` weighting step). |
| `geant4`          | dose_grid, edep_grid| particle_source    | `geant4_input:` and related keys — see [](#geant4-module-keys). |

An optional `name:` on any entry gives the instance a label (it defaults to the
module type); this only affects validation-error messages and workdir naming.

Skipping a step is expressed by simply *not listing* its module; a prebuilt
artifact is expressed by a source module (`mesh`, `track3p_source`,
`particle_source`). The old `skip_cubit` / `skip_solver` / `skip_acdtool` /
`geant4_particle_file` flags no longer exist.

## `mode:`

Selects how the workflow is driven. One `type` is required.

| `type`                | Extra sections required | Behavior |
|-----------------------|-------------------------|----------|
| `single`              | —                       | Run the workflow once (base inputs must be scalar-valued). Returns a one-row result table (or one-row-per-field-index for a field-indexed solver like S3P). |
| `parameter_sweep`     | `input_parameters` (any of its `cubit:`/`ace3p:`/`geant4:`/`particles:` sub-blocks) | Tensor-product sweep over every array-valued input leaf; one row per grid point. |
| `scalar_optimize`     | `vocs_parameters`, `xopt_parameters` | Drives an Xopt optimization loop. The objective is a name in `output_parameters` referenced from the VOCS. |
| `gp_parameter_sweep`  | `sweep_parameters`, `vocs_parameters`, `xopt_parameters` | Bayesian-exploration sweep — fits a Gaussian Process to the explored objective(s), then samples the GP posterior mean on the `sweep_parameters` tensor grid. |

Additional `mode:` keys:

| Keyword             | Applies to                          | Default            | Description |
|---------------------|-------------------------------------|--------------------|-------------|
| `output_file`       | `single`, `parameter_sweep`         | *(none — not written)* | Path for the tab-delimited result table (written via the shared `DataFrame.to_csv` writer). For the Xopt modes it names the run log (default `sim_output.txt`). |
| `sweep_output_file` | `gp_parameter_sweep`                | `'sweep_output.txt'` | Path for the GP posterior-mean sweep table. |

The modes are workflow-agnostic: because the objective is pulled from
`output_parameters` and the workflow is driven only through its `evaluate` seam,
*any* chain (S3P, Geant4, a multi-step pipeline) can be swept or optimized by the
same code.

## `workflow_parameters`

Directory management and executable-path settings. The solver/file settings
(`mode`, `module`, `cubit_input`, `ace3p_input`, `rfpost_input`, `ace3p_tasks`,
`sweep_output`, the `skip_*` flags, …) that lived here before the refactor are
gone — they now sit on the `workflow:` module entries and the `mode:` block.

| Keyword             | Type           | Default        | Description |
|---------------------|----------------|----------------|-------------|
| `workdir`           | `str` / `Path` | `os.getcwd()`  | Path to the working directory in which `lume-ace3p` runs. |
| `workdir_mode`      | `str`          | `'manual'`     | `'manual'` (single workflow folder) or `'auto'` (one auto-named folder per evaluation, suffixed with the swept scalar values). |
| `stage_mode`        | `str`          | `'copy'`       | How large static input files (prebuilt meshes, Track3P dumps, Geant4 STL geometry, prebuilt particle sources) are placed in each workdir: `'copy'`, `'symlink'`, or `'hardlink'` — see [](#stage-mode) below. |
| `dry_run`           | `bool`         | `False`        | If `True`, run the full Python pipeline but skip the Cubit/solver/acdtool/Geant4 binary calls (writes a `DRY_RUN.txt` marker). Auto-enabled when the relevant tool path cannot be resolved — see [](installation.md#dry-run-mode). |
| `paths`             | `dict`         | `None`         | Mapping of executable-path overrides. Recognized keys: `ace3p`, `cubit`, `mpi`, `geant4_app_path`, `geant4_app_exe`. Each value takes highest precedence in path resolution — see [](installation.md#executable-paths). |

(stage-mode)=
### `stage_mode` — storage-efficient staging

Source modules (`mesh`, `track3p_source`, `particle_source`) and the `geant4`
module bring externally-supplied files into each run's workdir under their bare
basename, so the tool resolves them with `cwd=workdir`. By default these files
are **copied**, which duplicates large static assets (e.g. a ~60 MB Track3P dump,
multi-MB STL meshes) into every workdir — once per evaluation in an `'auto'`
sweep, and once per DOE sample in `collect_training_data`. `stage_mode` chooses
the staging strategy instead:

| Value        | Behavior | Use when |
|--------------|----------|----------|
| `'copy'`     | Independent copy in each workdir (default; unchanged legacy behavior). | Workdirs must be self-contained/archival, or may live on a different filesystem than the source. |
| `'symlink'`  | Absolute symlink to the source file. | You want the storage savings and the source files stay in place for the run's lifetime. Works across filesystems. |
| `'hardlink'` | Hard link sharing the source's bytes; falls back to a copy (with a warning) when the link fails (e.g. cross-device `EXDEV`). | You want deduplication that survives the source being moved, **and** each workdir is on the same filesystem as the source. |

Staged files are treated as **read-only** — `symlink`/`hardlink` share bytes with
the source, so an in-place edit would corrupt the original. The pipeline never
writes back to staged inputs (modules that mutate an input file, such as Cubit /
ACE3P / Geant4 parameter merges, copy and rewrite their own input files
separately and are unaffected by `stage_mode`). With `'symlink'`, deleting or
moving a source file after a run leaves dangling links in the workdirs that
referenced it.

(particles-module-keys)=
### `particles` module keys

The `particles` module (field-emission weighting) accepts the keys documented
under [](#particle_parameters) directly on its `workflow:` entry — `impact_order`,
`impact_face_id`, `work_function`, `dt`, `beta` / `beta_input` / `beta_inputs`,
`num_bins`, `bin_edges`, `output_format`, and `output` (the output filename;
defaults to `<input>_modified.txt`). Note the module defaults `output_format` to
`'geant4'` (the 10-column Geant4 source file); set `output_format: 'track3p'`
explicitly for the weighted-Track3P dump.

(geant4-module-keys)=
### `geant4` module keys

Used on a `geant4` `workflow:` entry.

| Keyword                   | Type   | Default                | Description |
|---------------------------|--------|------------------------|-------------|
| `geant4_input`            | `str`  | `None`                 | Path to the Geant4 input file (plain `key = value` text, `#` comments) used as the simulation input. |
| `geant4_threads`          | `int`  | `None`                 | If set, overrides the `nthreads` key in the input file. When unset (the default) the input file's own `nthreads` value is left untouched. |
| `geant4_opts`             | `str`  | `''`                   | Additional `mpirun`/`srun` arguments when launching the Geant4 application. |
| `geant4_particle_cmd`     | `str`  | `'particles'`          | Input-file key that receives the particle-source filename (the executable auto-derives the event count from the particle file). |
| `geant4_geometry_files`   | `list` | `[]`                   | Extra geometry/auxiliary files copied into the working directory, *in addition to* the STL files named by `*_stl` keys in the input file. The two sets are unioned and de-duplicated by basename. |
| `geant4_dose_output`      | `str`  | `None`                 | Overrides the `output_dose` filename read for the `dose` output section. Defaults to the `output_dose` value in the input file. (`geant4_scoring_output` is accepted as a back-compat alias.) |
| `geant4_edep_output`      | `str`  | `None`                 | Overrides the `output_edep` filename read for the `edep` output section. Defaults to the `output_edep` value in the input file. |

To supply a prebuilt Geant4 source file directly (instead of generating one with
a `particles` module), use a `particle_source` module with a `file:` key. The
old `geant4_particle_file` / `particle_input` / `particle_output` keys no longer
exist.

(input_parameters)=
## `input_parameters`

`input_parameters` declares the input variable space, grouped into per-code
sub-blocks so every variable's home is explicit:

```yaml
input_parameters :
  cubit :                       # Cubit journal knobs (-> cubit bucket)
    cornercut : {min: 12.0, max: 16.0, num: 5}
  ace3p :                       # values inside the ACE3P input file
    FrequencyScan : {Start: 9.424e9}
  geant4 :                      # Geant4 input-file overrides
    nthreads : 8
  particles :                   # particles-module knobs (e.g. field-enhancement β)
    beta : {min: 40.0, max: 60.0, num: 5}
```

Each leaf value is a single scalar, a `list`, or a `dict` with `min`, `max`,
and `num` defined. If any leaf is vector-like (a list, or a `min/max/num`
range), the workflow can only be run as a parameter sweep — not a single
evaluation. The four sub-blocks map to the four
[`WorkflowInputs`](workflow_inputs.md) buckets (`geant4:` → the *macro*
bucket); their per-block conventions are detailed in
[](#ace3p_input_parameters) (duplicate-key aware) and
[](#geant4_input_parameters). The `particles:` bucket holds the
field-enhancement variables the `particles` module's `beta_input` / `beta_inputs`
read (the post-Track3P Fowler-Nordheim weighting step) — see
[](#particle_parameters).

:::{important}
`cubit:` keys must **exactly** match the variable names in the Cubit journal
file.
:::

During parameter sweeping, all combinations of the array-valued leaves across
**all** sub-blocks are evaluated (the full tensor product). For example, three
swept leaves — whether in one sub-block or spread across `cubit:`, `ace3p:`,
and `geant4:` — with lists of lengths 10, 12, and 15 run the workflow
10 × 12 × 15 = 1800 times.

:::{note}
**Deprecated flat aliases.** The pre-standardization keys
`cubit_input_parameters`, `ace3p_input_parameters`, `geant4_input_parameters`,
`particles_input_parameters`, and a bare `input_parameters` (treated as the
cubit block) are still accepted so existing configs keep running, but the nested
notation above is the standard. A cubit knob literally named `cubit`, `ace3p`,
`geant4`, or `particles` (which would collide with the reserved sub-block names)
must be declared with the flat `cubit_input_parameters` key.
:::

## `output_parameters`

Each `output_parameters` entry maps a user-chosen name (used as a result-table
column header or a VOCS objective name) to an extraction spec. The workflow
routes each spec to the module that can satisfy it and calls that module's
`extract`.

### Two spec syntaxes

There are two ways to write a spec. They are **both fully supported** and differ
only in how the target module is identified — pick whichever reads best for the
quantity you are extracting:

- **Explicit form** — a mapping with a `module` key naming the target module,
  e.g. `{module: s3p, quantity: 'S(0,0)', at: {frequency: 12.0e+09}}`. The
  `module` key is stripped and the rest of the mapping is handed to that module's
  `extract`. This form is **required** for S3P scalar objectives, because an
  S-parameter needs a keyed lookup (`quantity` + `at: {frequency}`) that no
  positional list can express.
- **Bare form** — a positional list `['section', string1, string2, ...]` (or a
  bare S-parameter string). No `module` key: the *shape* of the spec identifies
  the module. The head string routes it — `RoverQ`/`kickFactor`/
  `maxFieldsOnSurface` → `acdtool`, `dose`/`edep`/`scoring` → `geant4`,
  `count`/`total_weight` → `particles`, and a bare S-parameter string or mapping
  → `s3p`. This form mirrors the nested structure of the acdtool/Geant4 result
  and is the convention used throughout the Omega3P and Geant4 examples.

:::{note}
**Why the S3P and Omega3P examples look different.** The two syntaxes model
genuinely different extraction shapes. An S3P objective is a *keyed lookup* — a
named S-parameter at a specific frequency — which is why it uses the explicit
`{module, quantity, at}` mapping. An acdtool objective is a *positional index
path* into the postprocess result dict (`['RoverQ', '0', 'RoQ']`), so it uses the
bare list. The difference is deliberate, not an inconsistency; each form is the
natural fit for its module.

The two forms are **not interchangeable per module**: the `acdtool`, `geant4`,
and `particles` modules index their spec positionally (`spec[0]`, `spec[1]`, …),
so they require the bare list; only `s3p` consumes a mapping. In practice, use
the explicit mapping for S3P quantities and the bare list for everything else —
which is exactly what the examples do.
:::

The acdtool bare-form values are:

- `['RoverQ', string1, string2]` — corresponding to the `[RoverQ]` data
  block in `rfpost.out`.
  - `string1`: the mode ID number to be processed (usually starting from
    `'0'`).
  - `string2`: the data column name of the corresponding mode. Must be one
    of `'Frequency'`, `'Qext'`, `'V_r'`, `'V_i'`, `'absV'`, or `'RoQ'`.
- `['kickFactor', string1, string2]` — corresponding to the `[kickFactor]`
  data block in `rfpost.out`.
  - `string1`: the mode ID number.
  - `string2`: the data column name. Must be one of `'Frequency'`, `'Qext'`,
    `'Ks'`, `'V_r'`, `'V_i'`, or `'absV'`.
- `['maxFieldsOnSurface', string1, string2, string3]` — corresponding to
  the `[maxFieldsOnSurface]` data block in `rfpost.out`.
  - `string1`: the surface ID number (defined by the sideset in the Cubit
    journal file).
  - `string2`: the data column name. Must be one of `'Emax'`,
    `'Emax_location'`, `'Hmax'`, or `'Hmax_location'`.
  - `string3`: `'x'`, `'y'`, or `'z'` — the component of the
    `'Emax_location'` or `'Hmax_location'` vector.

When the workflow includes a `geant4` module, the output sections instead refer
to the Geant4 scoring-mesh output files (routed to the `geant4` module
automatically):

- `['dose', entry]` — reads the `output_dose` file (the dose-deposit grid).
- `['edep', entry]` — reads the `output_edep` file (the energy-deposit grid).
- `['scoring', entry]` — back-compat alias for `'dose'`.
  - `entry` is one of `'total'` (sum over all mesh bins), `'peak'` (maximum
    bin value), or `'peak_index'` (the `(ix, iy, iz)` index of the peak bin).

Both output files use the Geant4 box-mesh scorer format: three `#`-comment
header lines followed by comma-separated rows
`iX, iY, iZ, total(value), total(val^2), entry`. The fourth column
(`total(value)`) is read as the per-bin scored quantity.

More sections and entries will be added in future updates.

(ace3p_input_parameters)=
## `input_parameters.ace3p`

The `ace3p:` sub-block of [`input_parameters`](#input_parameters) is a nested
mapping organized by ACE3P input-file hierarchy, used to override or sweep over
values inside the `.omega3p` / `.s3p` / `.t3p` / `.track3p` input files (or to
supply them inline when no separate ACE3P input file is provided). The same
`min`/`max`/`num` and list conventions as the other sub-blocks apply to leaf
values; non-list scalars are written through unchanged. (The deprecated
top-level `ace3p_input_parameters:` key is equivalent.)

Internally, this block is parsed as an *ordered list of key/value pairs*
rather than a Python dict, which means **same-named sibling sections are
preserved**. For example, two `Port:` blocks at the same level are kept
as two distinct entries and merged positionally into the matching pair of
`Port` sections in the ACE3P input file:

```yaml
input_parameters :
  ace3p :
    'Port' :
      'ReferenceNumber' : 7
      'NumberOfModes' : 1
    'Port' :
      'ReferenceNumber' : 8
      'NumberOfModes' : 1
```

The same applies to repeated `SurfaceMaterial`, `BoundaryCondition`,
etc. entries; each block lines up positionally with its counterpart in
the ACE3P input. Use a `ReferenceNumber:` (or other discriminating leaf)
inside each block to keep the mapping unambiguous when reading the YAML.

Fast path: when a separate ACE3P input file is provided via the solver
module's `input:` key and the `ace3p:` block does not override (or sweep) any
values inside it, the file is copied to each working directory unchanged — no
parse/rewrite round-trip occurs.

See the S3P-without-separate-file example in [](parameter_sweep.md) for a
complete usage pattern.

(sweep_parameters)=
## `sweep_parameters`

Used only with `mode: {type: gp_parameter_sweep}`. Defines the
tensor-product grid on which the trained Gaussian Process is sampled after
the Xopt exploration phase. Each key is a variable name (matching a name
declared in `input_parameters`), each value is a `min`/`max`/`num` mapping
(linearly spaced).

(geant4_input_parameters)=
## `input_parameters.geant4`

The `geant4:` sub-block of [`input_parameters`](#input_parameters) is used when
the workflow includes a `geant4` module. It supplies overrides for settings in
the Geant4 input file. Each key is an input-file key (e.g. `nthreads`,
`world_z`, `scale_factor`); each value is either a scalar to write through
unchanged or a `min`/`max`/`num` mapping (or list) for a parameter sweep. A
swept key becomes an additional sweep axis alongside any `cubit:`/`ace3p:`
axes. Keys not present in the input file are appended. (The deprecated
top-level `geant4_input_parameters:` key is equivalent.)

(particle_parameters)=
## `particle_parameters`

These are the keys accepted by a `particles` module entry (they configure the
field-emission weighting; see [](#particles-module-keys)). They are set directly
on the module's `workflow:` entry, not in a separate top-level block.

| Keyword          | Type               | Default               | Description |
|------------------|--------------------|-----------------------|-------------|
| `impact_order`   | `int` or `list`    | *(required)*          | Track3P `ImpactOrder` value(s) to retain. Single int or list of ints. |
| `impact_face_id` | `int` or `list`    | *(required)*          | Track3P `ImpactFaceID` value(s) to retain. |
| `work_function`  | `float`            | *(required)*          | Surface work function (eV) used in the Fowler-Nordheim weighting. |
| `dt`             | `float`            | *(required)*          | Time step (s) used to convert current density to particles per emission event. |
| `beta`           | `list[float]`      | *(required)*          | Field-enhancement factor per axial bin. Length must equal `num_bins`. Not needed when `beta_input`/`beta_inputs` supplies the values from the input space. |
| `num_bins`       | `int`              | `len(beta)`           | Number of axial (`Initial_z`) bins applied to the filtered particles. |
| `bin_edges`      | `list[float]`      | `None` (auto-spaced)  | Explicit bin edges. If supplied, must have length `num_bins + 1`; otherwise edges are linearly spaced between the min and max `Initial_z` of the filtered particles. |
| `beta_input`     | `str`              | `None`                | Name of a single input-space variable (declared under `input_parameters.particles`; a legacy `cubit:` declaration is still honored) whose scalar value is broadcast to all `num_bins` bins. Lets a `parameter_sweep` (or Xopt) drive `beta` uniformly. Mutually exclusive with `beta_inputs`. |
| `beta_inputs`    | `list[str]`        | `None`                | Names of `num_bins` input-space variables (declared under `input_parameters.particles`), one per bin — enables independent per-bin `beta` exploration (e.g. an 8-dimensional Xopt run). Length must equal `num_bins`. Mutually exclusive with `beta_input`. |
| `output_format`  | `str`              | `'geant4'` (module default) | Particle-file layout. `'track3p'` writes all filtered Track3P columns plus `Bin` and `ParticleWeight` (commented header). `'geant4'` writes the 10-column source file consumed by the Geant4 `/lume/particleFile` reader (see below). The `particles` module defaults this to `'geant4'`; set `'track3p'` explicitly for the weighted-Track3P dump. |
| `output`         | `str`              | `<input>_modified.txt` | Output filename for the generated particle file (written into the workdir). |

With `output_format: 'track3p'` the file contains the filtered Track3P
columns plus a `Bin` column and a `ParticleWeight` column, with a
`#`-commented header.

With `output_format: 'geant4'` (the module default) the file contains 10
whitespace-separated columns and no header — one primary per row:

| Col | Field         | Unit  | Source Track3P column   |
|-----|---------------|-------|-------------------------|
| 1   | `x`           | m     | `Impact_x`              |
| 2   | `y`           | m     | `Impact_y`              |
| 3   | `z`           | m     | `Impact_z`              |
| 4   | `phase`       | rad   | `ImpactPhaseinRFcycle`  |
| 5   | `energy`      | eV    | `ImpactEnergy`          |
| 6   | `n_electrons` | -     | `ParticleWeight` (event weight; written as an integer) |
| 7   | `px`          | -     | `momentum_x`            |
| 8   | `py`          | -     | `momentum_y`            |
| 9   | `pz`          | -     | `momentum_z`            |
| 10  | `face_id`     | -     | `ImpactFaceID`          |

(vocs_parameters)=
## `vocs_parameters`

Declares the Xopt VOCS for the `scalar_optimize` and `gp_parameter_sweep`
modes: a `variables` mapping of name → `[low, high]` bounds, plus `objectives`
(name → `MINIMIZE`/`MAXIMIZE`/`explore`) and optional `constraints`. Objective
names are `output_parameters` names, so no solver-specific parsing lives in the
driver.

**Variable routing.** Each Xopt variable is written into the input bucket where
it is declared in [`input_parameters`](#input_parameters) (cubit / ace3p /
geant4 / particles), so a single optimization can drive parameters across
multiple codes at once. Resolution rule:

- A **bare** variable name (`cornercut`) routes to its declaring bucket when
  that name is unique across all buckets.
- If the same bare name is declared in more than one bucket (e.g. a `cubit:`
  knob and an `ace3p:` leaf both named `start`), a bare reference is a hard
  error; **qualify** it with its bucket label — `cubit:start`,
  `ace3p:FrequencyScan.Start`, `geant4:nthreads`, or `particles:beta0` (the
  ACE3P label is the dotted section path, matching the sweep-table column label).
- A variable not declared in any `input_parameters` bucket falls back to the
  cubit bucket (so a config that only lists `vocs_parameters.variables` keeps
  working).

## `xopt_parameters`

Controls the Xopt driver used by the `scalar_optimize` and
`gp_parameter_sweep` modes. Most keys are optional; the required key is
`generator`. For `scalar_optimize`, at least one termination criterion
(`num_step`, `cost_budget`, or `alotted_time`) must also be supplied; the
`gp_parameter_sweep` mode uses `max_steps` plus the early-stopping keys.

| Keyword                 | Type    | Default         | Description |
|-------------------------|---------|-----------------|-------------|
| `generator`             | `str`   | *(required)*    | Xopt generator name. Supported: `'NelderMeadGenerator'`, `'ExpectedImprovementGenerator'`, `'UpperConfidenceBoundGenerator'`, `'MultiFidelityGenerator'`, `'ExpectedHypervolumeImprovementGenerator'`. (`gp_parameter_sweep` uses `BayesianExplorationGenerator` internally.) |
| `generator_options`     | `dict`  | `{}`            | Keyword arguments forwarded verbatim to the chosen generator's constructor. Required for `ExpectedHypervolumeImprovementGenerator` (must include `reference_point`); also used for UCB tuning. |
| `num_random`            | `int`   | `0` (or `2` for multi-fidelity, `5` for `gp_parameter_sweep`) | Number of initial random evaluations used to seed the model. |
| `num_step`              | `int`   | `None`          | Number of optimization steps after the random-seeding phase. |
| `max_iterations`        | `int`   | `None`          | Total iteration cap (random + step). When set together with `tolerance`, optimization stops as soon as all objectives meet the tolerance or the cap is hit. |
| `tolerance`             | `float` / `dict` | `None`     | Per-objective stopping threshold. A scalar applies to every objective; a mapping is keyed by objective name. Optimization terminates when all objectives are at or below their tolerance. |
| `max_steps`             | `int`   | `None`          | Used by `gp_parameter_sweep` only; caps the number of GP-guided exploration steps. |
| `improvement_threshold` | `float` | `0.01`          | Used by `gp_parameter_sweep`. Relative-improvement threshold for the early-stopping check. |
| `patience`              | `int`   | `5`             | Used by `gp_parameter_sweep`. Number of consecutive iterations without improvement before stopping. |
| `cost_budget`           | `float` | `None`          | Multi-fidelity termination criterion; total cost (in `xopt_runtime` units) at which optimization stops. |
| `alotted_time`          | `str`   | `None`          | Alternative multi-fidelity criterion in `'HH:MM:SS'` format; converted to a cost budget in seconds. |
| `cost_function`         | `str`   | `'exponential'` | Multi-fidelity cost-function model. One of `'exponential'` or `'gaussian_process'`. |
| `fidelity_variable`     | `str`   | `'s'`           | Multi-fidelity only. Name of the input variable interpreted as the fidelity coordinate; the column is renamed from `'s'` in the input dict. |
| `mc_noisy_objective`    | `bool`  | `False`         | Declare the objective Monte-Carlo-noisy (e.g. a Geant4 dose). Suppresses the low-noise GP prior on the MultiFidelity path and requires an explicit `bin_edges` to be set. |
| `save_model`            | `bool`  | `False`         | If `True`, save the trained generator's GP model state to `Binary_gp_model.pt` and a human-readable summary to `gp_parameters.txt`. |

## The Workflow object

The declarative `workflow:` list is built into a
{py:class}`~lume_ace3p.workflow_graph.Workflow` — a validated, topologically
ordered chain of modules with a single black-box `evaluate` seam. The
`run_lume_ace3p` entry point calls `Workflow.from_config(yaml_data)` and hands
the result to the mode layer; you rarely construct one directly.

Its public seams (all called by the workflow-agnostic modes, never by
solver-specific code) are:

- `Workflow.evaluate(input_scalars=None)` — run the ordered module chain once
  for one input point and return `{output_name: value}` for the
  `output_parameters` spec. `input_scalars` may be `None` (use the base inputs
  as-is), a list aligned with `sweep_axes()` (materialize that grid point), or a
  `{var: scalar}` mapping (variable overrides routed to their declaring bucket —
  the shape Xopt passes; see [](#vocs_parameters)).
- `Workflow.sweep_axes()` — the array-valued input leaves a sweep iterates over.
- `Workflow.field_index()` / `Workflow.field()` — the shared field index (e.g.
  S3P's `('Frequency', array)`) and the structured per-run field output (S3P
  spectra, Geant4 voxel grids) that the hybrid result model keeps out of the
  flat table.

### Input data model

`WorkflowInputs(cubit, ace3p, macro, particles)` is the structured
representation the workflow consumes internally, built by `inputs.build_inputs`
from the YAML. The four buckets correspond to the four `input_parameters`
sub-blocks:

| Bucket  | YAML source (nested)          | Deprecated flat alias      | Type                |
|---------|-------------------------------|----------------------------|---------------------|
| `cubit` | `input_parameters.cubit`      | `cubit_input_parameters` / bare `input_parameters` | `dict[str, scalar \| ndarray]` |
| `ace3p` | `input_parameters.ace3p`      | `ace3p_input_parameters`   | ordered tree of `(name, child)` pairs (`Section`) — duplicates preserved |
| `macro` | `input_parameters.geant4`     | `geant4_input_parameters`  | `dict[str, scalar \| ndarray]` |
| `particles` | `input_parameters.particles` | `particles_input_parameters` | `dict[str, scalar \| ndarray]` |

Array-valued leaves in any bucket become sweep axes; scalar leaves are
written through to the matching input file unchanged. During optimization,
each VOCS variable is routed to the bucket where it is declared (see
[](#vocs_parameters)).

### Results

The table modes (`single`, `parameter_sweep`) return a pandas `DataFrame` — one
row per evaluation (or one row per `(grid-point, frequency)` for a field-indexed
solver like S3P) — routed through the single shared writer
{py:func}`~lume_ace3p.results.write_table` (a tab-delimited `to_csv`) when
`mode.output_file` is set. Structured field outputs are persisted separately as
`.npz` and referenced by a field-artifact column. The Xopt modes return the
{py:class}`xopt.Xopt` object and log its `X.data` table through the same writer.

For full class- and method-level documentation, see the
[API reference](api/index).
