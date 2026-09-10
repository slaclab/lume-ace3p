# YAML configuration reference

`lume-ace3p` is driven by a YAML configuration file. A top-level **`workflow:`**
list declares the module chain and a **`mode:`** block drives it.
`input_parameters` and `output_parameters` declare the swept inputs and the
extracted scalars. An optimization adds `vocs_parameters` and `xopt_parameters`.
`workflow_parameters` holds directory and path settings; solver and file settings
live on the `workflow:` module entries.

## `workflow:`

A list of module entries, each a mapping with a `module` key naming the module
type plus that module's own keys. List order is only a tiebreaker: run order
comes from resolving each module's artifact dependencies into a DAG (mesh before
solver, solver before acdtool, particle source before Geant4, …). Two producers
of the same artifact, or a requirement nothing provides, is a validation error.

| Module type       | Provides            | Requires           | Key config keys |
|-------------------|---------------------|--------------------|-----------------|
| `cubit`           | mesh                | —                  | `journal:` (Cubit `.jou`); `meshconvert:` (bool, default `True`). |
| `mesh`            | mesh                | —                  | `file:`, a prebuilt mesh file. |
| `omega3p`         | em_solution         | mesh               | `input:` (`.omega3p`); `tasks:`, `cores:`, `opts:` (MPI settings); `results_dir:`. Eigensolver; see [](#omega3p-module). |
| `s3p`             | em_solution         | mesh               | `input:` (`.s3p`); `tasks:`, `cores:`, `opts:`; `results_dir:`. S-parameter (frequency-scan) solver; see [](#s3p-module). |
| `t3p`             | td_solution         | mesh               | `input:` (`.t3p`); `tasks:`, `cores:`, `opts:`; `results_dir:`. Time-domain (wakefield) solver; see [](#t3p-module). |
| `acdtool`         | rf_post             | *depends on `command:`* | `command:`, `input:` (`.rfpost`), `args:`, `jobname:`; `tasks:`, `cores:`, `opts:`. Postprocessor (`RoverQ`, `kickFactor`, `maxFieldsOnSurface`, …); see [](#acdtool-module). |
| `track3p_source`  | track3p_particles   | —                  | `file:`, an externally produced Track3P dump. There is no in-pipeline Track3P solver. |
| `particles`       | particle_source     | track3p_particles  | Field-emission weighting keys; see [](#particles-module-keys). |
| `particle_source` | particle_source     | —                  | `file:`, a prebuilt Geant4-format source file. Bypasses the `particles` weighting step. |
| `geant4`          | dose_grid, edep_grid| particle_source    | `geant4_input:` and related keys; see [](#geant4-module-keys). |

An optional `name:` labels the instance (default: the module type). It names
the step's log file (`<workdir>/<name>.log`) and its run-manifest entry, which is
how a resume identifies the step. Two modules with the same `name:` is a
validation error.

To skip a step, omit its module. To supply a prebuilt artifact, use a source
module (`mesh`, `track3p_source`, `particle_source`). The old `skip_cubit` /
`skip_solver` / `skip_acdtool` / `geant4_particle_file` flags are not read.

## `mode:`

Selects how the workflow is driven. One `type` is required.

| `type`                | Extra sections required | Behavior |
|-----------------------|-------------------------|----------|
| `single`              | —                       | Run the workflow once; base inputs must be scalar-valued. Returns a one-row result table, or one row per field index for a field-indexed solver like S3P. |
| `parameter_sweep`     | `input_parameters` (any of its `cubit:`/`ace3p:`/`geant4:`/`particles:` sub-blocks) | Tensor-product sweep over every array-valued input leaf; one row per grid point. |
| `scalar_optimize`     | `vocs_parameters`, `xopt_parameters` | Xopt optimization loop. The objective is an `output_parameters` name referenced from the VOCS. |
| `gp_parameter_sweep`  | `sweep_parameters`, `vocs_parameters`, `xopt_parameters` | Bayesian-exploration sweep: fits a Gaussian Process to the explored objective(s), then samples the GP posterior mean on the `sweep_parameters` tensor grid. |
| `collect_training_data` | mode `variables:` | Scatters a design-of-experiments (Sobol/LHS) over the per-bin field-enhancement vector and persists a `(beta, dose_grid)` training pair per sample into a resumable store. Requires a `workflow:`. See [](#surrogate-modes). |
| `train_surrogate`     | *(none; reads a store)* | Fits the reduced-basis PCA-GP forward surrogate `beta -> dose profile` from a collected store. Needs no `workflow:`. |
| `invert_optimize`     | *(none; reads a store/model)* | Inverts a target dose profile to estimate the beta that produced it, against the saved surrogate. Needs no `workflow:`. |
| `invert_bayesian`     | *(none; reads a store/model)* | Same inversion, returning a posterior over beta (NUTS) instead of a point estimate. Needs no `workflow:`. |

### Store-consuming modes

`train_surrogate`, `invert_optimize` and `invert_bayesian` read an on-disk store
or saved model and never drive the module chain, so their configs omit
`workflow:`, `workflow_parameters` and `input_parameters`; the store's
`manifest.json` carries the pinned `bin_edges` and scoring-mesh invariants. Every
other mode, including `collect_training_data`, requires a `workflow:` list. See
`examples/geant4_beta_surrogate/` for both shapes.

Additional `mode:` keys:

| Keyword             | Applies to                          | Default            | Description |
|---------------------|-------------------------------------|--------------------|-------------|
| `output_file`       | `single`, `parameter_sweep`         | *(none; not written)* | Path for the tab-delimited result table (`DataFrame.to_csv`). For the Xopt modes it names the run log (default `sim_output.txt`). |
| `sweep_output_file` | `gp_parameter_sweep`                | `'sweep_output.txt'` | Path for the GP posterior-mean sweep table. |
| `resume`            | `single`, `parameter_sweep`, `collect_training_data` | `False` | Pick each point up from the run manifest in its workdir instead of re-running it; see [](#resume). |
| `resume`            | `scalar_optimize`, `gp_parameter_sweep` | `False` | Continue an interrupted optimization from `xopt_state.yml`; see [](#xopt-resume). A different mechanism with a weaker promise. |

The modes are workflow-agnostic: any chain (S3P, Geant4, a multi-step pipeline)
can be swept or optimized, since the objective is an `output_parameters` name.

:::{note}
**A key nothing reads is reported.** Every block with a fixed key set (the
top-level blocks, `mode:`, `workflow_parameters`, `vocs_parameters`,
`xopt_parameters`) is checked against the keys the code consumes. An unrecognized
key prints a warning naming it, the nearest match, and the recognized keys:

```console
Warning: 'xopt_parameters' has key that nothing reads: 'num_steps' (did you mean
'num_step'?). Ignored. Recognized here: alotted_time, bin_edges, cost_budget, …
```

The run continues. The recognized sets are per block and per mode, so a
`resume:` in a `train_surrogate` block is reported while the same key in a
`parameter_sweep` block is not. `input_parameters` and `output_parameters` are
never checked: their keys are your own variable and output names.
:::

## `workflow_parameters`

Directory and executable-path settings. The old solver/file keys
(`mode`, `module`, `cubit_input`, `ace3p_input`, `rfpost_input`, `ace3p_tasks`,
`sweep_output`, the `skip_*` flags, …) are not read here; use the `workflow:`
module entries and the `mode:` block.

| Keyword             | Type           | Default        | Description |
|---------------------|----------------|----------------|-------------|
| `workdir`           | `str` / `Path` | `os.getcwd()`  | Working directory in which `lume-ace3p` runs. |
| `workdir_mode`      | `str`          | `'manual'`     | How each evaluation's folder is named: `'manual'`, `'auto'`, or `'indexed'`; see [](#workdir-mode). |
| `stage_mode`        | `str`          | `'copy'`       | How large static input files (prebuilt meshes, Track3P dumps, Geant4 STL geometry, prebuilt particle sources) are placed in each workdir: `'copy'`, `'symlink'`, or `'hardlink'`; see [](#stage-mode). |
| `capture_output`    | `bool`         | `True`         | Tee each module's Cubit/solver/acdtool/Geant4 output to `<workdir>/<module name>.log` as well as the terminal. `False` inherits the parent's streams and writes nothing to disk; see [](#capture-output). |
| `dry_run`           | `bool`         | `False`        | Run the full Python pipeline but skip the Cubit/solver/acdtool/Geant4 binary calls, writing a `DRY_RUN.txt` marker. Auto-enabled when the relevant tool path cannot be resolved; see [](installation.md#dry-run-mode). |
| `paths`             | `dict`         | `None`         | Executable-path overrides. Recognized keys: `ace3p`, `cubit`, `mpi`, `geant4_app_path`, `geant4_app_exe`. Each takes highest precedence in path resolution; see [](installation.md#executable-paths). |

(workdir-mode)=
### `workdir_mode` — naming each evaluation's folder

| Value | Folder per evaluation | Use when |
|---|---|---|
| `'manual'` | `workdir` itself, shared by every evaluation. | A single run, or a sweep whose points may overwrite each other's files. Cannot be resumed (see [](#resume)). |
| `'auto'`   | **Sweep:** `<workdir>_<value>_<value>…`, suffixed with the swept scalar values. **Optimization:** `<workdir>_0`, `<workdir>_1`, … by iteration (see below). | You want to read a point's inputs off its directory name. |
| `'indexed'` | `<workdir>_0`, `<workdir>_1`, … by the point's position in the sweep, or its iteration in an optimization. | You want a stable, collision-free point identity. Resume keys on this. |

`'auto'` names are not guaranteed unique (two axes can render to the same string)
and grow with every axis. `'indexed'` is bounded and collision-free but does not
show the input values. Both produce identical result tables. The point index is
the sweep's row order (tensor product of the swept axes, first axis slowest),
matching the output table.

A `single` run under `'indexed'` is point 0, so it writes `<workdir>_0`.

If `workdir` is not set, the per-evaluation directories are named
`lume-ace3p_workflow_output_0`, … inside the working directory. Only `'manual'`
runs in the working directory itself.

#### In an optimization, `'auto'` numbers by iteration

The Xopt modes (`scalar_optimize`, `gp_parameter_sweep`) have no sweep grid; the
generator proposes each point as the run proceeds. Under `'auto'` or `'indexed'`
each evaluation gets its own directory numbered in evaluation order
(`<workdir>_0`, `<workdir>_1`, …), matching the row order of `sim_output.txt`.

`'auto'` numbers rather than names by value here: optimizer proposals are
full-precision floats (`lume-ace3p_workdir_14.724999999999998_1.5750000000000002`),
and two evaluations at the same proposed point (which Nelder-Mead does produce)
still get separate directories.

:::{warning}
Leaving `workdir_mode` at its `'manual'` default in an optimization runs every
evaluation in one directory. Each overwrites the previous one's mesh, input
files, solver results, logs and run manifest; only the last survives. The run
prints a warning. Set `workdir_mode: 'auto'` unless you want one shared directory.
:::

(stage-mode)=
### `stage_mode` — storage-efficient staging

Source modules (`mesh`, `track3p_source`, `particle_source`) and the `geant4`
module stage externally supplied files into each run's workdir under their bare
basename (the tool runs with `cwd=workdir`). By default they are copied, which duplicates large static assets (a
~60 MB Track3P dump, multi-MB STL meshes) into every workdir, once per evaluation
or DOE sample. `stage_mode` chooses the strategy:

| Value        | Behavior | Use when |
|--------------|----------|----------|
| `'copy'`     | Independent copy in each workdir (default). | Workdirs must be self-contained/archival, or may live on a different filesystem than the source. |
| `'symlink'`  | Absolute symlink to the source file. | The source files stay in place for the run's lifetime. Works across filesystems. |
| `'hardlink'` | Hard link sharing the source's bytes. Falls back to a copy, with a warning, when the link fails (e.g. cross-device `EXDEV`). | Deduplication that survives the source being moved; each workdir must be on the same filesystem as the source. |

Staged files are read-only: `symlink`/`hardlink` share bytes with the source, so
an in-place edit would corrupt the original. The pipeline never writes back to
staged inputs; modules that mutate an input file (Cubit / ACE3P / Geant4
parameter merges) rewrite their own copies and are unaffected by `stage_mode`.
With `'symlink'`, deleting or moving a source file after a run leaves dangling
links in the workdirs.

(capture-output)=
### `capture_output` — per-evaluation logs

Each module's external tool invocations (Cubit and its `meshconvert`, the ACE3P
solvers, `acdtool`, the Geant4 application) write their output to
`<workdir>/<module name>.log`, named after the module's instance name, so two
`acdtool` steps with different `name:` keys get separate logs.

Output is teed, not redirected: everything still appears on the terminal, and
`stderr` stays on `stderr`, so `2>errors` keeps working. The log is appended to,
with a `$ <command line>` header before each invocation, so a shared (`'manual'`)
workdir or a re-run keeps the earlier record.

`capture_output: false` turns it off; the child processes then inherit the
parent's file descriptors and nothing is written to disk.

(run-manifest)=
### The run manifest — `lume_ace3p_state.json`

Every evaluation writes one JSON file into its workdir recording what it did. It
is always written (there is no YAML key); `resume` and `--status` read it back.

```json
{
  "schema": 1,
  "point": {"axes": {"cav_radius": 100.0}},
  "config_hash": "sha256:1f3a…",
  "started": "2026-08-24T14:02:11",
  "updated": "2026-08-24T14:07:56",
  "modules": [
    {"name": "cubit",   "type": "cubit",   "status": "complete",
     "artifacts": {"mesh": "pillbox-rtop4.gen"}},
    {"name": "omega3p", "type": "omega3p", "status": "complete",
     "job_name": "omega3p_results"},
    {"name": "acdtool", "type": "acdtool", "status": "failed",
     "error": "ValueError: acdtool reported no 'RoverQ' section."}
  ],
  "outputs": {"R/Q": 108.4, "Mode_freq": 1313756106.86}
}
```

It is updated after each module, so a half-finished run's file says how far it
got. Modules are listed in the order they ran; one that never started is absent,
which is distinct from `"failed"`. `config_hash` covers the module entries, the
materialized input point, and the `output_parameters` spec. It does not cover
`paths`, `dry_run`, `workdir`, or comments, so a workdir stays recognizable on a
different machine and reformatting a config does not invalidate a campaign.

Artifact paths are relative to the workdir. A solver's `job_name` is the results
directory it resolved (see [](#omega3p-module)). A module whose external tool was
skipped on a resume also carries `"resumed": true`.

The manifest carries wall-clock timestamps, so it is not comparable run-to-run
and is excluded from the frozen test baselines.

(resume)=
### `resume` — picking a campaign up where it stopped

With `resume: true` in the `mode:` block, each point is driven through the
manifest above instead of restarting from the mesh.

```yaml
workflow_parameters :
  'workdir' : 'lume-ace3p_t3p_workdir'
  'workdir_mode' : 'indexed'      # required: resume needs per-point directories

mode :
  type : parameter_sweep
  resume : True
  output_file : 't3p_sweep_output.txt'
```

Per point:

| Manifest state | What happens |
|---|---|
| absent | The point runs normally. |
| `config_hash` differs | The point runs from the start and the mismatch is printed: that workdir was written for a different configuration. |
| every module `complete`, outputs present | No external tool runs. Every module re-reads its existing output and the row is rebuilt. |
| partial, or some module `failed` | Execution restarts at the first module that is not `complete`; the ones before it re-read their output. |
| `complete` but an output is missing | A warning names the module and what is gone, then execution restarts there. |

Two consequences:

- **A resumed module re-runs its parser and skips only its subprocess**, so a
  resumed run's table is identical to an uninterrupted one (an S3P point re-reads
  its frequency axis; a `t3p` point re-reads `wakefield.out`).
- **A module that re-runs makes every later module re-run too.** This stops a
  `t3p` re-solve from being paired with a skipped `acdtool postprocess transwake`
  step, which would report the longitudinal loss factor as a kick factor
  (`transwake` writes over T3P's own `wakefield.out`).

Requirements and limits:

- **`workdir_mode` must not be `manual`.** One shared directory has one manifest,
  describing whichever point ran last. `resume: true` with `manual` is refused,
  naming `indexed` as the fix.
- **It is opt-in.** `config_hash` guards against adopting a stale workdir from a
  different study.
- **The Xopt modes resume by a different mechanism.** Their points are chosen by the
  generator as the run proceeds, so there is no per-point manifest to key on.
  `resume: true` there restores the optimizer's state instead; see [](#xopt-resume).
- **`collect_training_data`** skips a sample whose `field.npz` is already stored
  whether or not `resume` is set. `resume: true` additionally lets a sample that
  stopped midway through the chain restart at its first non-complete module.
- A nonzero exit status from a solver is not by itself recorded as a failure; no
  ACE3P wrapper raises on one (see [](#capture-output)). Such a run is caught by
  the missing-output check above, which covers every module that can name its own
  output.

(status)=
### `run-lume-ace3p --status` — reading a campaign without running it

```console
$ run-lume-ace3p --status t3p_sweep.yaml
 - 9 point(s) implied by this configuration: 4 complete, 1 failed, 4 absent
 point  cell_radius  iris_radius    status modules    next                        workdir
     0        0.045        0.020  complete     2/2          lume-ace3p_t3p_workdir_0
     1        0.045        0.025  complete     2/2          lume-ace3p_t3p_workdir_1
     ...
     4        0.050        0.025    failed     1/2     t3p  lume-ace3p_t3p_workdir_4
     5        0.050        0.030    absent     0/2   cubit  lume-ace3p_t3p_workdir_5
```

One row per point the config implies: verdict, how much of the chain is
complete, the module a resume would start from, and the workdir. Nothing is
executed and no manifest is written, so it is safe to run against a campaign that
is still going.

The verdicts are `complete`, `partial`, `failed`, `stale` (a manifest for a
different resolved configuration; that point will re-run from the start) and
`absent`.

`--status` covers every mode `resume` applies to. The table above is for `single`
and `parameter_sweep`. An optimization has no fixed set of points, so it reports
what is banked:

```console
$ run-lume-ace3p --status s3p_optimization.yaml
 - scalar_optimize: 37 evaluation(s) recorded in 'xopt_state.yml'
 - best 'reflection' = 0.00042 at cornercut=14.0312, rcorner1=1.18745
 - a resumed run continues from this data and repeats no evaluation; it does not
   reproduce the trajectory an uninterrupted run would have taken.
```

The store-consuming modes (`train_surrogate`, `invert_optimize`,
`invert_bayesian`) run no points, so `--status` declines them.

(xopt-resume)=
### `resume` in an optimization — continuing an interrupted search

`resume: true` in an Xopt `mode:` block continues an interrupted optimization from
a state file the run writes all along.

```yaml
workflow_parameters :
  'workdir' : 'lume-ace3p_opt_workdir'
  'workdir_mode' : 'auto'          # per-evaluation directories: _0, _1, _2, …

mode :
  type : scalar_optimize
  resume : True                    # continue from xopt_state.yml
```

`xopt_state.yml` is written beside the mode's `output_file` (next to
`sim_output.txt` by default) and updated after every evaluation, whether or not
`resume` is set. It is written to a temporary file and renamed, so a run killed
mid-write leaves the previous state rather than half a file.

:::{important}
**A resumed optimization does not reproduce the trajectory an uninterrupted run
would have taken.** No evaluation is repeated, and the search continues from the
same data, but the generator proposes from an equally informed state, not the
same state; the torch/numpy RNG streams alone break that. Do not expect two
`sim_output.txt` files to diff clean.
:::

Both the trajectory and the generator's internal state are restored. Replaying
`sim_output.txt` alone into a fresh generator is nearly equivalent for a Bayesian
generator (the GP is refit from data) but not for `NelderMeadGenerator`, whose
simplex is the state; a data-only restore re-proposes points it already has.

Other points:

- **Every iteration budget is a total for the campaign**: `num_random`, `num_step`,
  `max_iterations`, `max_steps` and `cost_budget` all measure the whole
  optimization. A resumed run continues to the same finish line, and resuming a
  finished optimization does nothing.
- **It works under any `workdir_mode`,** including `manual`, because it restores
  from the state file rather than per-evaluation manifests. Under `auto`/`indexed`
  a resumed run continues the workdir numbering (`_k`, `_k+1`, …).
- **A state file that disagrees with the config is refused, not adopted**, and the
  refused file is kept. Four kinds of disagreement are caught: a different
  generator, a flipped `MINIMIZE`/`MAXIMIZE`, moved variable bounds, and a changed
  workflow (the module chain, the `output_parameters` spec, or any input value the
  optimizer is not driving). Editing the nominal value of an optimized variable is
  not a change, since the optimizer overrides it every evaluation.

  On refusal, the existing `xopt_state.yml` and run log are renamed to
  `.rejected` (then `.rejected.1`, …) and the message names them. An absent,
  unreadable or truncated state file starts fresh without raising, and moves
  nothing aside.
- **Resuming with a smaller budget does nothing, and says so.** `num_step: 10`
  against a 25-evaluation record is already satisfied.
- **The interrupted evaluation's workdir is abandoned.** The generator proposes a
  different point after the restore, so the half-finished directory is not reused.
- **The convergence test in `gp_parameter_sweep`** (`improvement_threshold` /
  `patience`) is a window over recent steps and is not carried across the
  interruption. A resumed run gets at least `patience` more steps before it can
  stop on it.
- If the state file is gone but `sim_output.txt` survives, the data can still be
  replayed into a fresh generator with `Xopt.add_data`, at the cost described above.

(omega3p-module)=
### `omega3p` module

Omega3P is the ACE3P eigensolver. It requires only a `mesh`, so the minimal
workflow is `cubit → omega3p`.

A run writes `<results_dir>/omega3p.out`, whose top-level `Mode` sections carry
one eigenmode each. These are parsed directly, so a mode frequency or Q needs no
`acdtool` step; `examples/omega3p_dispersion_sweep` is `cubit → omega3p` alone.
An `acdtool` `RoverQ` block returns the same frequency; the shipped examples use
acdtool only for what it alone produces, such as R/Q and peak surface fields.

`output_parameters` quantities are the `Mode` leaf names Omega3P writes. Because
those names overlap other modules', name the module explicitly:
`{module: omega3p, quantity: Frequency}`.

| Quantity | Shape | Meaning |
|---|---|---|
| `Frequency` | array over `ModeID` | Mode frequency, Hz. On a lossy/port run this is the **real part** of the complex eigenvalue. |
| `Frequency_imag` | array over `ModeID` | Imaginary part, Hz. Present only when the run reported complex eigenvalues. |
| `QualityFactor` | array over `ModeID` | Intrinsic Q. |
| `ExternalQ` | array over `ModeID` | External Q. Present only on a run with a port. |
| `TotalEnergy` | array over `ModeID` | Stored energy, J (plus `TotalEnergy_imag` on a complex run). |
| `PowerLoss` | array over `ModeID` | Surface power loss, W. |
| `ModeID` | array | The mode index itself. |

`at: {mode: <n>}` reduces an array to that mode's scalar, the form an Xopt
objective needs, mirroring S3P's `at: {frequency: …}`. Without it you get the
full array, for a dispersion curve or an HOM catalog.

`ModeID` is Omega3P's field index. When an output asks for the whole mode axis
(no `at:`), a `parameter_sweep` emits a long-format table with one row per
`(grid point, mode)`. When every declared output is narrowed with
`at: {mode: n}`, the table stays wide (one row per grid point) and the per-mode
arrays are persisted as that row's field artifact (see [](#results)). A dry run
has no modes, so its table is wide as well.

`results_dir:` names the directory the run writes into (default
`omega3p_results`). `lume-ace3p` passes it to the solver as the second positional
argument, as a batch script would (`omega3p SRFCell.omega3p omega3p_results`), so
it moves both where the solver writes and where `lume-ace3p` reads.

A top-level `JobName` in the `.omega3p` file is honored as a fallback, but no
ACE3P reference documents that key for any solver, so do not rely on it. It is
not forwarded on the command line.

```{warning}
The `t3p` module is the exception: its `results_dir:` steers only where
`lume-ace3p` reads. No ACE3P reference documents a solver command line, and none
of the T3P invocations in the CW23 tutorials passes a second positional argument,
so nothing establishes that `t3p` accepts one. T3P also writes to
`<results_dir>/OUTPUT` rather than into the directory itself. Set `results_dir:`
on a `t3p` module only when the run is already writing there via a `JobName` leaf
in the `.t3p` file. See [](t3p_reference.md).
```

A missing `omega3p.out` (a failed or interrupted run) raises only if a workflow
asks for a mode quantity; the error names the path searched.

(s3p-module)=
### `s3p` module

S3P is the ACE3P S-parameter (frequency-scan) solver. It requires only a `mesh`,
so the minimal workflow is `cubit → s3p`.

Its results are read from three files in `<results_dir>` (default `s3p_results`,
overridable with `results_dir:` as for `omega3p`). None is documented by the
ACE3P S3P reference, so the formats come from frozen real fixtures.

| Quantity | Shape | Meaning |
|---|---|---|
| `Frequency` | array | The frequency scan, in Hz. Every S-parameter aligns to it. |
| `S(m,n)` | array over `Frequency` | The S-parameter magnitude \|S\|, from `Reflection.out`. |
| `S(m,n)_real`, `S(m,n)_imag` | array over `Frequency` | Real and imaginary parts, from `SParameter.out`. |
| `S(m,n)_phase_deg` | array over `Frequency` | Phase in degrees, in `(-180, 180]`. |

`m` and `n` are S-matrix indices, not port numbers; the output's `IndexMap` maps
each index to its `(Port, Mode, Type, Cutoff)`. For S3P, `ModeID` means a port
mode (excitation), ordered by port then mode.

`at: {frequency: <f>}` reduces any of these arrays to the scalar at that
frequency. `<f>` must be a point of the scan (matched to 1e-9 relative); an
off-grid frequency raises, naming the scan's range and the nearest points, so an
optimization stops at its first evaluation instead of spending its budget on
`NaN`. `Frequency` is S3P's field index, so a `parameter_sweep` emits a
long-format table with one row per `(grid point, frequency)` whenever an output
spans the scan, or when no outputs are declared (the table then carries the swept
inputs and `Frequency` only). If every declared output is narrowed with
`at: {frequency: …}`, the table is wide and the spectrum is persisted as the row's
field artifact.

Older ACE3P builds write no `SParameter.out`. That is a warning, not an error: the
magnitudes are still read, and asking for a `_real` / `_imag` / `_phase_deg`
quantity then fails naming the key. A missing `Reflection.out` raises.

Port mode field profiles (`PortRef<n>_<m>.out`, columns `x y Ex Ey Hx Hy`) are
read too, keyed by the file's stem (`PortRef7_0`). They are indexed by position,
not frequency, so they are not `output_parameters` quantities; they ride in the
per-run field artifact, and asking for one as a table column raises.

(t3p-module)=
### `t3p` module

T3P is the ACE3P time-domain (wakefield) solver. It takes the same MPI keys as
`omega3p`/`s3p` (`input:`, `tasks:`, `cores:`, `opts:`) and requires only a
`mesh`, so the minimal workflow is `cubit → t3p`. See `examples/t3p_sweep`.

It provides `td_solution`, not `em_solution`, so listing `acdtool`'s
`postprocess rf` (which requires `em_solution`) after a T3P solver is a validation
error. acdtool's time-domain commands (`postprocess transwake` / `coaxsignal` /
`volmontomode`) require `td_solution` and chain after T3P; see
[](#acdtool-module). A workflow may list both `s3p` and `t3p`; two `t3p` entries
is a duplicate-producer error.

T3P writes under `<results_dir>/OUTPUT` (default `t3p_results`) and names each
monitor's files after that monitor's `Name`, read from the parsed `.t3p`.

`results_dir:` is accepted but, uniquely among the solver modules, read-only: it
tells `lume-ace3p` where to look without telling `t3p` where to write (see the
warning under [](#omega3p-module)). Leave it unset unless a `JobName` leaf in the
`.t3p` file already moves the run's output.

#### Monitors: `Name` selects, `Type` supplies the shape

A `.t3p` file may declare any number of `Monitor` blocks of six documented
`Type`s, and all of them are read. What each type writes, and which have real
output behind them, is in [](t3p_reference.md). The quantity names are:

| `Monitor` `Type` | Quantities | Axis | Units |
|---|---|---|---|
| `WakeField` | `loss_factor`, `kick_factor`, `W`, `I_bunch`, `s` | `s` | V/pC; `I_bunch` C/m; `s` m |
| `Point` | `t`, `Hx`, `Hy`, `Hz`, `Ex`, `Ey`, `Ez` | `t` | SI |
| `Power` | `t`, `P` | `t` | s, W |
| `SurfacePowerLoss` | `t`, `P` | `t` | s, W |
| `ModeVoltage` | `t`, `V` | `t` | s, V |
| `Volume` | none (netCDF field dumps) | — | — |
| — `Bunch0` | `t`, `I` | `t` | s, A |

`Bunch0` is not a monitor: T3P writes `Bunch0.out` on every run and no input block
declares it. It is addressable by that name like any other series.

A run may declare several monitors of one type, so `Name` is the selector; it is
also the output filename stem:

```yaml
output_parameters :
  'P_in'   : {module: t3p, monitor: inputPower,   quantity: P}
  'P_out'  : {module: t3p, monitor: outputPower,  quantity: P}
  'P_wall' : {module: t3p, monitor: wallossPower, quantity: P}
  'Ez_gap' : {module: t3p, monitor: point, quantity: Ez, at: {t: 1.0e-9}}
```

A `monitor:` key routes the spec to `t3p` on its own, so `module: t3p` is
optional alongside it. See `examples/t3p_power_balance` (three `Power` monitors
on one run).

`monitor:` may be omitted when exactly one monitor provides the named quantity,
or when the quantity is one of the five wakefield names above. Wakefield specs
keep their short form; none is deprecated:

```yaml
  'k_loss'    : {module: t3p, quantity: loss_factor}
  'W_at_10cm' : {module: t3p, quantity: 'W', at: {s: 0.10}}
  'K'         : kick_factor           # bare form; routes to t3p by name
```

Where several monitors could answer a bare quantity, the error names all the
candidates. The monitor quantities (`P`, `V`, `t`) are too generic to route bare,
so write `module: t3p` or `monitor:` for those.

A wake run reports either a loss factor (longitudinal) or a kick factor
(transverse), depending on the beam offset and the monitor contour. Asking for the
wrong one raises an error naming what is available.

#### One index axis per module, `s` before `t`

T3P exposes a field index, so a sweep over a T3P workflow emits a long-format
table (one row per `(grid point, index)`) as long as at least one declared output
is an array over that index. A sweep declaring only `loss_factor` stays wide, with
the wake persisted as each row's field artifact. Which index:

* `s` when the run produced a wake;
* `t` otherwise, from the first time-series monitor;
* under dry-run, a single-row sentinel whose label is read from the input file
  (a `WakeField` monitor means `s`), so a swept table still gets one row per grid
  point.

The two axes are incompatible (tens of wake samples against thousands of
timesteps), so a run declaring both keeps `s`, and everything on the `t` axis
must be narrowed to a scalar with `at:`. Requesting an off-axis array raises an
error naming both axes. The full arrays remain in the per-run field artifact (see
[](plotting.md)), together with a `Volume` monitor's filenames.

`at: {s: <position>}` and `at: {t: <seconds>}` both take the nearest sample, since
both T3P grids follow from `TimeStepping: DT`. Per-run scalars like `loss_factor`
repeat down each run's block of rows.

:::{note}
**Volume monitors are written as your input file asks.** A `Volume` monitor
writes a full field dump per sampled timestep: tens to hundreds of MB per run,
multiplied by every point in a sweep. LUME-ACE3P does not prune or rewrite your
monitors; widen the monitor's `TimeStep` or remove the block if you do not need
the dumps. It is netCDF despite the `.out` extension, so its filenames are
recorded and never parsed, and asking a `Volume` monitor for a quantity raises.

**A declared monitor that wrote nothing warns, naming itself.** One monitor
failing to write does not fail the run (`T3POutputWarning`, naming the monitor
and the path looked for).

**`CheckPoint` is passed through but restarts are not orchestrated.** T3P will
write `t3p_results/CHECKPOINT`, but LUME-ACE3P will not detect an existing
checkpoint or set `Action: restart`. A sweep point that exceeds its wall time
restarts from scratch on re-run.
:::

(acdtool-module)=
### `acdtool` module

`acdtool` is ACE3P's shared postprocessing utility and exposes 19 commands. Which
command runs is explicit:

```yaml
workflow :
  - module : acdtool                      # 'postprocess rf' inferred from .rfpost
    input  : 'pillbox-rtop.rfpost'

  - module  : acdtool
    name    : 'transwake'
    command : 'postprocess transwake'
    args    : [0.0, 0.0, 0.0, 0.0125]     # jobname is injected, not repeated
```

| Key | Meaning |
|---|---|
| `command:` | The acdtool command. Omitting it infers `postprocess rf` from a `.rfpost` `input:`. |
| `input:` | The input file, for the commands that take one (`postprocess rf` takes a `.rfpost`). |
| `args:` | The command's positional arguments, excluding the jobname. `postprocess transwake` takes `[x1, y1, x2, y2]`; `coaxsignal` / `volmontomode` take none. |
| `jobname:` | Override the injected results-directory name (see below). Rarely needed. |
| `tasks:`, `cores:`, `opts:` | MPI settings, as for the solvers. Only `postprocess rf` and `postprocess volmontomode` run in parallel; every other command is pinned to one rank with a warning. `cores:` is not pinned (the tutorial runs the serial `transwake` as `srun -n 1 -c 256`). |

Commands usable as a workflow step, and what each requires:

| `command:` | Requires | Notes |
|---|---|---|
| `postprocess rf` | `em_solution` | RF parameters from an Omega3P/S3P solution, driven by a `.rfpost` file. The default. |
| `postprocess transwake` | `td_solution` | Transverse wakefield from a T3P run, via Panofsky-Wenzel. `args: [x1, y1, x2, y2]`. |
| `postprocess coaxsignal` | `td_solution` | Coaxial-port signal from a beam-current excitation. Writes `<jobname>/OUTPUT/signal.out`. |
| `postprocess volmontomode` | `td_solution` | Converts T3P volume-monitor dumps to ParaView `.mod` files. Produces no extractable quantity. |

The three time-domain commands chain after `t3p`; see
`examples/t3p_transwake` for the `transwake` case and
`examples/s3p_window_rfpost` for `postprocess rf` against an S3P solution.

The other 15 commands are recognized (an unknown command raises listing the known
ones) but not available as a workflow step; each raises an error naming why:
`postprocess track3p` needs the KVC `:` input dialect this wrapper does not parse,
`mesh deform` / `mesh fix` / `meshconvert*` would make acdtool a second mesh
producer, and `pic3pstats` / `pic3pconvert` / `project` have no PIC3P or TEM3P
module to attach to. The dispatchable ones can still be invoked directly through
`lume_ace3p.acdtool.Acdtool`. [](acdtool_reference.md) has the full 19-command
table with argument forms and status.

The jobname is injected, not configured. Every positional `postprocess` command's
first argument is the producing solver's job name (`t3p_results`,
`omega3p_results`, …), taken from what that solver module resolved, so a `t3p`
step with `results_dir: custom_results` moves acdtool's argument with it.

:::{important}
**`postprocess transwake` overwrites T3P's own wakefield output** at
`<jobname>/OUTPUT/wakefield.out`, and the transverse result is read by `t3p`, not
by `acdtool`. So the output spec for a `[cubit, t3p, acdtool(transwake)]` chain
names `t3p`:

```yaml
output_parameters :
  'K' : {module: t3p, quantity: kick_factor}
```

Because acdtool rewrites a file its producer already parsed, the `acdtool` step
makes `t3p` re-read it afterwards; otherwise the workflow would report the
longitudinal loss factor computed before acdtool ran. The same applies to
`wake_new` / `wake_direct` when they land. `coaxsignal` writes a new file and is
unaffected.
:::

`output_parameters` for `postprocess rf` are documented under
[](#output-specs-for-postprocess-rf) below.

#### What `postprocess rf` reads out of its output

The `.rfpost` format has 24 blocks in a handful of output shapes.
[](acdtool_reference.md) lists all 24 with output filenames, real-output coverage,
and input semantics; the summary below is enough to write an output spec. A block
is reported when `ionoff = 1` in the input file, and read by the reader for its
shape:

| Shape | Blocks | Lands in |
|---|---|---|
| Mode-indexed table | the `modeID1`/`modeID2` blocks: `RoverQ`, `RoverQT`, `kickFactor`, `VFFT`, `ALLFieldAtPoint`, `coaxPort`, … | `output_data[block][mode_id][column]`, plus a `ModeIDs` list |
| Surface-indexed scalars | `maxFieldsOnSurface`, `powerThroughSurface` | `output_data[block][surface_id][name]`, plus `SurfaceIDs` |
| Single-mode scalars | `FieldAtPoint` (no index axis; evaluates only `RFField`'s `ModeID`) | `output_data[block][name]` |
| Column curves | the `filename` blocks: `ALLFieldOnLine`, `FieldOnLine`, `Multipole`, `GBZFFT`, … | separate files, read into `{filename: {column: array}}` |
| Field maps | `FieldMap`, `IMPACTMap`, `OpenPMD_IMPACT`, `fieldOnSurface`, `fieldOn2DBoundary` | separate files; filenames recorded, contents not parsed |

Column names come from the file (a column table's header row, a scalar block's
`name = value` lines), so a build that adds or reorders a column is still read
correctly. A complex value (`powerThroughSurface`'s power, in W) is split into
`name` and `name_imag`.

Two further points:

- **`[scaling]` is always read**, even though no input block declares it. It
  carries `m_factor`, the normalized-to-physical field conversion, which nothing
  else in ACE3P's output reports. `FieldOnLine` output is scaled to `RFField`'s
  `gradient`, while `ALLFieldOnLine` output carries the raw eigenmode
  normalization. Its `Variant` is `gradient` normally and `point` when
  `gradient = -1` selects "no scaling".
- **Curve and grid output is a field artifact, not a table column.** Curves are
  per-position arrays, exposed through the module's `field()` rather than
  `output_parameters`. The same applies to `postprocess coaxsignal`'s
  `signal.out`, whose three columns (`t`, `V`, `I`) are unlabeled in the file and
  named from the reference.

A block whose output cannot be read warns naming itself
(`lume_ace3p.acdtool.AcdtoolOutputWarning`): an unknown block from a newer build,
a curve block that wrote no files, or `VFFT` with `printGroup = nterm`, which
groups results by multipole component instead of by mode.

:::{note}
`kickFactor` and `maxFieldsOnSurface` have no real acdtool output behind them: no
tutorial run enabled either block, and the reference documents inputs only. Their
readers are driven by the file, but the layouts remain unverified; see
`tests/fixtures/acdtool/COVERAGE.md`.
:::

(output-specs-for-postprocess-rf)=
#### Output specs for `postprocess rf`

An acdtool output spec names the block, the quantity (a column or `name = value`
scalar of that block), and, for the indexed shapes, which index:

```yaml
output_parameters :
  'R/Q'       : {module: acdtool, section: RoverQ, quantity: RoQ}
  'Mode_freq' : {module: acdtool, section: RoverQ, quantity: Frequency}
  'f0'        : {module: acdtool, section: RoverQ, quantity: Frequency, at: {mode: 0}}
  'E_max'     : {module: acdtool, section: maxFieldsOnSurface, quantity: Emax,
                 at: {surface: 6}}
  'loc_x'     : {module: acdtool, section: maxFieldsOnSurface,
                 quantity: Emax_location, component: x, at: {surface: 6}}
  'm_factor'  : {module: acdtool, section: scaling, quantity: m_factor}
```

| Key | Meaning |
|---|---|
| `section:` | The `.rfpost` block, spelled as acdtool spells it (`RoverQ`, `kickFactor`, `maxFieldsOnSurface`, `powerThroughSurface`, `FieldAtPoint`, `scaling`, …). Naming a block routes the spec to `acdtool`, so `module: acdtool` is optional. |
| `quantity:` | The column or scalar name as it appears in the output: `RoQ`, `Frequency`, `Qext`, `V_r`, `V_i`, `absV` for `RoverQ`; `Ks` and the same complex-voltage set for `kickFactor`; `Emax` / `Hmax` / `Emax_location` / `Hmax_location` for `maxFieldsOnSurface`; `m_factor` for `scaling`. An unknown name raises listing what the run did report. |
| `at:` | Which index. `{mode: n}` for a mode-indexed block, `{surface: n}` for a surface-indexed one. |
| `component:` | `x` / `y` / `z` of a location vector (`Emax_location`). |

Omitting `at:` on a mode-indexed block asks for every mode: the result table
carries one row per mode with `ModeID` as its index column, for a dispersion
curve, HOM catalog or mode spectrum (`modeID2 = -1` in the `.rfpost` input already
means "every mode the solver produced"). `at: {mode: n}` gives the scalar for one
mode.

`ModeID` is acdtool's only table axis. Surface-indexed blocks require
`at: {surface: n}` and always resolve to a scalar; omitting it raises an error
naming the surfaces the run reported. The input block pins the surface it
evaluates (`maxFieldsOnSurface { surfaceID = 6 }`).

When another module in the chain owns the table axis (`[cubit, s3p, acdtool]`,
where S3P's `Frequency` comes first in resolved DAG order), a per-mode array is
exposed as a field artifact instead of a table column (see [](#results)).

:::{note}
**The positional list form is deprecated.** `['RoverQ', '0', 'RoQ']` still works
and returns the same value, but emits a `DeprecationWarning` naming its mapping
replacement:

| List form | Mapping form |
|---|---|
| `['RoverQ', '0', 'RoQ']` | `{module: acdtool, section: RoverQ, quantity: RoQ, at: {mode: 0}}` |
| `['kickFactor', '0', 'Ks']` | `{module: acdtool, section: kickFactor, quantity: Ks, at: {mode: 0}}` |
| `['maxFieldsOnSurface', '6', 'Emax']` | `{module: acdtool, section: maxFieldsOnSurface, quantity: Emax, at: {surface: 6}}` |
| `['maxFieldsOnSurface', '6', 'Emax_location', 'x']` | `{module: acdtool, section: maxFieldsOnSurface, quantity: Emax_location, component: x, at: {surface: 6}}` |

The list cannot express the whole-axis case (no `at:`).
:::

(particles-module-keys)=
### `particles` module keys

The `particles` module (field-emission weighting) accepts the keys documented
under [](#particle_parameters) directly on its `workflow:` entry: `impact_order`,
`impact_face_id`, `work_function`, `dt`, `beta` / `beta_input` / `beta_inputs`,
`num_bins`, `bin_edges`, `output_format`, and `output` (default
`<input>_modified.txt`). `output_format` defaults to `'geant4'` (the 10-column
Geant4 source file); set `'track3p'` explicitly for the weighted-Track3P dump.

(geant4-module-keys)=
### `geant4` module keys

Used on a `geant4` `workflow:` entry.

| Keyword                   | Type   | Default                | Description |
|---------------------------|--------|------------------------|-------------|
| `geant4_input`            | `str`  | `None`                 | Path to the Geant4 input file (plain `key = value` text, `#` comments). |
| `geant4_threads`          | `int`  | `None`                 | If set, overrides the `nthreads` key in the input file; otherwise the file's value is left untouched. |
| `geant4_opts`             | `str`  | `''`                   | Additional `mpirun`/`srun` arguments when launching the Geant4 application. |
| `geant4_particle_cmd`     | `str`  | `'particles'`          | Input-file key that receives the particle-source filename. The executable derives the event count from the particle file. |
| `geant4_geometry_files`   | `list` | `[]`                   | Extra geometry/auxiliary files copied into the working directory, in addition to the STL files named by `*_stl` keys in the input file. The two sets are unioned and de-duplicated by basename. |
| `geant4_dose_output`      | `str`  | `None`                 | Overrides the `output_dose` filename read for the `dose` output section (default: the input file's `output_dose` value). `geant4_scoring_output` is a back-compat alias. |
| `geant4_edep_output`      | `str`  | `None`                 | Overrides the `output_edep` filename read for the `edep` output section (default: the input file's `output_edep` value). |

To supply a prebuilt Geant4 source file directly instead of generating one with a
`particles` module, use a `particle_source` module with a `file:` key. The old
`geant4_particle_file` / `particle_input` / `particle_output` keys are not read.

(input_parameters)=
## `input_parameters`

`input_parameters` declares the input variable space, grouped into per-code
sub-blocks:

```yaml
input_parameters :
  cubit :                       # Cubit journal knobs (-> cubit bucket)
    cornercut : {min: 12.0, max: 16.0, num: 5}
  ace3p :                       # values inside the ACE3P input file
    FrequencyScan : {Start: 9.5e9}
  geant4 :                      # Geant4 input-file overrides
    nthreads : 8
  particles :                   # particles-module knobs (e.g. field-enhancement β)
    beta : {min: 40.0, max: 60.0, num: 5}
```

Each leaf value is a scalar, a `list`, or a `dict` with `min`, `max`, and `num`.
If any leaf is vector-like, the workflow can only be run as a parameter sweep. The
four sub-blocks map to the four [`WorkflowInputs`](workflow_inputs.md) buckets
(`geant4:` is the *macro* bucket); see [](#ace3p_input_parameters) (duplicate-key
aware) and [](#geant4_input_parameters). The `particles:` bucket holds the
field-enhancement variables read by the `particles` module's `beta_input` /
`beta_inputs`; see [](#particle_parameters).

:::{important}
`cubit:` keys must exactly match the variable names in the Cubit journal file.
:::

A parameter sweep evaluates the full tensor product of the array-valued leaves
across all sub-blocks. Three swept leaves with lists of lengths 10, 12, and 15,
in any sub-blocks, run the workflow 10 × 12 × 15 = 1800 times.

:::{note}
**Deprecated flat aliases.** The flat keys `cubit_input_parameters`,
`ace3p_input_parameters`, `geant4_input_parameters`,
`particles_input_parameters`, and a bare `input_parameters` (treated as the cubit
block) are still accepted, but the nested notation above is the standard. A cubit
knob literally named `cubit`, `ace3p`, `geant4`, or `particles` collides with the
reserved sub-block names and must be declared with the flat
`cubit_input_parameters` key.
:::

## `output_parameters`

Each entry maps a user-chosen name (a result-table column header or a VOCS
objective name) to an extraction spec, which the workflow routes to the module
that can satisfy it.

(two-spec-syntaxes)=
### Two spec syntaxes

- **Mapping form** (preferred): `{module: <type>, quantity: <name>, at: {...}}`,
  with `section:` and `component:` where the module needs them. The `module` key
  is stripped and the rest is handed to that module's `extract`. It is required
  for S3P/T3P scalar objectives, which need a keyed lookup (`quantity` +
  `at: {frequency}` / `at: {s}`), and is the form every acdtool quantity should
  use; see [](#output-specs-for-postprocess-rf).
- **Bare form**: a positional list `['section', string1, string2, ...]` or a bare
  quantity string, with no `module` key. The shape of the spec identifies the
  module. `dose`/`edep`/`scoring` → `geant4` (see [](#geant4-output-specs));
  `count`/`total_weight` → `particles`; a `monitor:` key or a T3P wakefield
  quantity (`loss_factor`/`kick_factor`/`W`/`I_bunch`/`s`) → `t3p`; a `.rfpost`
  block name (`RoverQ`, `kickFactor`, `maxFieldsOnSurface`, …) → `acdtool`
  (deprecated; use the mapping form); a bare S-parameter string or any other
  mapping → `s3p`.

  acdtool's `kickFactor` section and T3P's `kick_factor` quantity are distinct
  spellings, so the two never collide. T3P's monitor quantities (`P`, `V`, `t`,
  `Ez`, …) are too generic to route bare, so name `module: t3p` or a `monitor:`
  for those; see [](#t3p-module).

The `module:` key is optional whenever the spec's shape identifies its module: a
`section:` for `acdtool` and `geant4`, a `monitor:` for T3P's non-wake monitors.
Spelling it out is never wrong and is clearer in a mixed workflow.

Every shipped example uses the mapping form. The bare forms stay supported; only
acdtool's is deprecated (it cannot express the whole-axis case).

:::{note}
**Older configs may use the list form.** `['RoverQ', '0', 'RoQ']` is block, mode,
column, the nesting of the postprocess result dict. The middle element is an
index axis, so the mapping form expresses the same scalar and can also ask for
the whole axis by dropping `at:`. `particles` specs are a single bare quantity
name and have no positional form.
:::

(geant4-output-specs)=
### Geant4 output specs

For a `geant4` module, `section:` names a scoring-mesh output file and
`quantity:` the reduction over its bins. Naming a section routes the spec to
`geant4`, so `module: geant4` is optional:

```yaml
output_parameters :
  'total_dose' : {module: geant4, section: dose, quantity: total}
  'peak_dose'  : {module: geant4, section: dose, quantity: peak}
  'total_edep' : {module: geant4, section: edep, quantity: total}
```

- `section: dose` reads the `output_dose` file (the dose-deposit grid).
- `section: edep` reads the `output_edep` file (the energy-deposit grid).
- `section: scoring` is a back-compat alias for `dose`.
- `quantity:` is one of `total` (sum over all mesh bins), `peak` (maximum bin
  value), or `peak_index` (the `(ix, iy, iz)` index of the peak bin).

The positional form `['dose', 'total']` returns the same value and is not
deprecated: a Geant4 spec is a `(grid, reduction)` pair with no index axis, so the
list expresses everything the mapping does. The shipped examples use the mapping
for consistency.

Both output files use the Geant4 box-mesh scorer format: three `#`-comment header
lines followed by comma-separated rows
`iX, iY, iZ, total(value), total(val^2), entry`. The fourth column
(`total(value)`) is read as the per-bin scored quantity.

More sections and entries will be added in future updates.

(ace3p_input_parameters)=
## `input_parameters.ace3p`

The `ace3p:` sub-block of [`input_parameters`](#input_parameters) is a nested
mapping following the ACE3P input-file hierarchy. It overrides or sweeps values
inside the `.omega3p` / `.s3p` / `.t3p` / `.track3p` input files, or supplies them
inline when no separate ACE3P input file is provided. Leaf values take the same
`min`/`max`/`num` and list conventions as the other sub-blocks; scalars are
written through unchanged. The deprecated top-level `ace3p_input_parameters:` key
is equivalent.

This block is parsed as an ordered list of key/value pairs, so same-named
sibling sections are preserved. Two `Port:` blocks at the same level are merged
positionally into the matching pair of `Port` sections in the ACE3P input file:

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

The same applies to repeated `SurfaceMaterial`, `BoundaryCondition`, etc.
entries. Use a `ReferenceNumber:` (or other discriminating leaf) inside each block
to keep the YAML readable.

Fast path: when the solver module's `input:` names a separate ACE3P input file and
the `ace3p:` block does not override or sweep any values inside it, the file is
copied to each working directory unchanged.

See the S3P-without-separate-file example in [](parameter_sweep.md).

(sweep_parameters)=
## `sweep_parameters`

Used only with `mode: {type: gp_parameter_sweep}`. Defines the tensor-product
grid on which the trained Gaussian Process is sampled after the Xopt exploration
phase. Each key is a variable name matching one declared in `input_parameters`;
each value is a `min`/`max`/`num` mapping (linearly spaced).

(geant4_input_parameters)=
## `input_parameters.geant4`

The `geant4:` sub-block of [`input_parameters`](#input_parameters) overrides
settings in the Geant4 input file. Each key is an input-file key (e.g.
`nthreads`, `world_z`, `scale_factor`); each value is a scalar written through
unchanged or a `min`/`max`/`num` mapping (or list) for a parameter sweep. A swept
key becomes a sweep axis alongside any `cubit:`/`ace3p:` axes. Keys not present in
the input file are appended. The deprecated top-level `geant4_input_parameters:`
key is equivalent.

(particle_parameters)=
## `particle_parameters`

The keys accepted by a `particles` module entry (see [](#particles-module-keys)).
They are set directly on the module's `workflow:` entry, not in a separate
top-level block.

| Keyword          | Type               | Default               | Description |
|------------------|--------------------|-----------------------|-------------|
| `impact_order`   | `int` or `list`    | *(required)*          | Track3P `ImpactOrder` value(s) to retain. Single int or list of ints. |
| `impact_face_id` | `int` or `list`    | *(required)*          | Track3P `ImpactFaceID` value(s) to retain. |
| `work_function`  | `float`            | *(required)*          | Surface work function (eV) used in the Fowler-Nordheim weighting. |
| `dt`             | `float`            | *(required)*          | Time step (s) used to convert current density to particles per emission event. |
| `beta`           | `list[float]`      | *(required)*          | Field-enhancement factor per axial bin. Length must equal `num_bins`. Not needed when `beta_input`/`beta_inputs` supplies the values. |
| `num_bins`       | `int`              | `len(beta)`           | Number of axial (`Initial_z`) bins applied to the filtered particles. |
| `bin_edges`      | `list[float]`      | `None` (auto-spaced)  | Explicit bin edges. If supplied, must have length `num_bins + 1`; otherwise edges are linearly spaced between the min and max `Initial_z` of the filtered particles. |
| `beta_input`     | `str`              | `None`                | Name of one input-space variable (declared under `input_parameters.particles`; a `cubit:` declaration is also honored) whose scalar value is broadcast to all `num_bins` bins, so a `parameter_sweep` or Xopt can drive `beta` uniformly. Mutually exclusive with `beta_inputs`. |
| `beta_inputs`    | `list[str]`        | `None`                | Names of `num_bins` input-space variables (declared under `input_parameters.particles`), one per bin, for independent per-bin `beta` exploration (e.g. an 8-dimensional Xopt run). Length must equal `num_bins`. Mutually exclusive with `beta_input`. |
| `output_format`  | `str`              | `'geant4'` (module default) | Particle-file layout. `'track3p'` writes all filtered Track3P columns plus `Bin` and `ParticleWeight`, with a `#`-commented header. `'geant4'` writes the 10-column source file consumed by the Geant4 `/lume/particleFile` reader (see below). |
| `output`         | `str`              | `<input>_modified.txt` | Output filename for the generated particle file, written into the workdir. |

With `output_format: 'geant4'` (the module default) the file contains 10
whitespace-separated columns and no header, one primary per row:

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

Declares the Xopt VOCS for the `scalar_optimize` and `gp_parameter_sweep` modes:
a `variables` mapping of name → `[low, high]` bounds, plus `objectives` (name →
`MINIMIZE`/`MAXIMIZE`/`explore`) and optional `constraints`. Objective names are
`output_parameters` names.

**Variable routing.** Each Xopt variable is written into the
[`input_parameters`](#input_parameters) bucket where it is declared (cubit /
ace3p / geant4 / particles), so one optimization can drive several codes.

- A bare variable name (`cornercut`) routes to its declaring bucket when that
  name is unique across all buckets.
- If the same bare name is declared in more than one bucket (a `cubit:` knob and
  an `ace3p:` leaf both named `start`), a bare reference is a hard error. Qualify
  it with its bucket label: `cubit:start`, `ace3p:FrequencyScan.Start`,
  `geant4:nthreads`, or `particles:beta0`. The ACE3P label is the dotted section
  path, matching the sweep-table column label.
- A variable not declared in any `input_parameters` bucket falls back to the
  cubit bucket, so a config that only lists `vocs_parameters.variables` works.

## `xopt_parameters`

Controls the Xopt driver used by the `scalar_optimize` and `gp_parameter_sweep`
modes. The only required key is `generator`. For `scalar_optimize`, at least one
termination criterion (`num_step`, `cost_budget`, or `alotted_time`) must also be
supplied; `gp_parameter_sweep` uses `max_steps` plus the early-stopping keys.

| Keyword                 | Type    | Default         | Description |
|-------------------------|---------|-----------------|-------------|
| `generator`             | `str`   | *(required)*    | Xopt generator name: `'NelderMeadGenerator'`, `'ExpectedImprovementGenerator'`, `'UpperConfidenceBoundGenerator'`, `'MultiFidelityGenerator'`, or `'ExpectedHypervolumeImprovementGenerator'`. `gp_parameter_sweep` uses `BayesianExplorationGenerator` internally. |
| `generator_options`     | `dict`  | `{}`            | Keyword arguments forwarded verbatim to the generator's constructor. Required for `ExpectedHypervolumeImprovementGenerator` (must include `reference_point`); also used for UCB tuning. |
| `num_random`            | `int`   | `0` (or `2` for multi-fidelity, `5` for `gp_parameter_sweep`) | Number of initial random evaluations used to seed the model. |
| `num_step`              | `int`   | `None`          | Number of optimization steps after the random-seeding phase. |
| `max_iterations`        | `int`   | `None`          | Total iteration cap (random + step). With `tolerance`, optimization stops when all objectives meet the tolerance or the cap is hit. |
| `tolerance`             | `float` / `dict` | `None`     | Per-objective stopping threshold. A scalar applies to every objective; a mapping is keyed by objective name. Optimization stops when all objectives are at or below their tolerance. |
| `max_steps`             | `int`   | `None`          | `gp_parameter_sweep` only; caps the number of GP-guided exploration steps. |
| `improvement_threshold` | `float` | `0.01`          | `gp_parameter_sweep` only. Relative-improvement threshold for the early-stopping check. |
| `patience`              | `int`   | `5`             | `gp_parameter_sweep` only. Number of consecutive iterations without improvement before stopping. |
| `cost_budget`           | `float` | `None`          | Multi-fidelity termination criterion; total cost (in `xopt_runtime` units) at which optimization stops. |
| `alotted_time`          | `str`   | `None`          | Alternative multi-fidelity criterion in `'HH:MM:SS'` format; converted to a cost budget in seconds. |
| `cost_function`         | `str`   | `'exponential'` | Multi-fidelity cost-function model. One of `'exponential'` or `'gaussian_process'`. |
| `fidelity_variable`     | `str`   | `'s'`           | Multi-fidelity only. Input variable interpreted as the fidelity coordinate; the column is renamed from `'s'` in the input dict. |
| `mc_noisy_objective`    | `bool`  | `False`         | Declare the objective Monte-Carlo-noisy (e.g. a Geant4 dose). Suppresses the low-noise GP prior on the MultiFidelity path and requires an explicit `bin_edges`. |
| `save_model`            | `bool`  | `False`         | Save the trained generator's GP model state to `Binary_gp_model.pt` and a readable summary to `gp_parameters.txt`. |

(surrogate-modes)=
## Surrogate modes

Three modes build and use a reduced-basis surrogate of a Geant4 dose profile as
a function of the per-bin field-enhancement vector `beta = (beta0 … betaN)`. All
keys below live in the `mode:` block.

### `collect_training_data`

Drives the full `track3p_source -> particles -> geant4` chain once per
design-of-experiments sample, persisting a `(beta, dose_grid)` pair each time.
Requires a `workflow:` list.

| Keyword       | Type   | Default | Description |
|---------------|--------|---------|-------------|
| `store`       | `str`  | `'training_store'` | Store directory (result table + per-sample field artifacts + `manifest.json`). |
| `num_samples` | `int`  | `8`     | DOE size. A power of two is ideal for Sobol. |
| `sampler`     | `str`  | `'sobol'` | `'sobol'` or `'lhs'`. Not a tensor grid; a full 8-D grid is infeasible. |
| `seed`        | `int`  | `0`     | Reproducible design; a resumed run reproduces the same points. |
| `fidelity`    | `float`| `None`  | Recorded Geant4 primary count per sample, for later multi-fidelity work. |
| `variables`   | `dict` | *required* | Per-beta `[lo, hi]` (or `{min, max}`) DOE bounds, one entry per `beta_inputs` name. |
| `resume`      | `bool` | `False` | Restart a sample that stopped midway through the chain at its first non-complete module. A sample whose `field.npz` is already stored is skipped regardless; see [](#resume). |

Two constraints are enforced and hard-fail otherwise: the `particles` module
must fix `bin_edges` explicitly (length `num_bins + 1`) and declare per-bin
`beta_inputs`, and the `geant4` input file's scoring mesh must be readable and
unchanged for the whole campaign (it is fingerprinted into the manifest and
re-checked per sample).

### `train_surrogate`

Fits the PCA-GP forward model from a store: stack the dose grids, subtract the
mean, SVD to the leading POD modes, then fit one Gaussian Process per retained
coefficient, each with a fitted noise term. No `workflow:` needed.

| Keyword          | Type    | Default | Description |
|------------------|---------|---------|-------------|
| `store`          | `str`   | *required* | The `collect_training_data` store to fit. |
| `variance`       | `float` | `0.99`  | Cumulative POD energy to retain; picks the mode count `k`. |
| `num_components` | `int`   | `None`  | Pin `k` explicitly (overrides `variance`). |
| `seed`           | `int`   | `0`     | Reproducible GP restart search. |
| `model_dir`      | `str`   | `<store>/surrogate` | Where the model is saved (`basis.npz`, `gps.joblib`, `surrogate.json`). |
| `holdout`        | `float` / `int` | `None` | Hold out a fraction (0<f<1) or count of samples for an accuracy report written to `train_report.txt`. |
| `dose_transform` | `str`   | `'linear'` | `'linear'` or `'log10'`. Dose spans ~9 orders of magnitude, so a linear fit is dominated by the peak voxels; `'log10'` fits the shape far better, and accuracy is then reported in log space. |
| `floor`          | `float` | smallest positive training dose | Positive offset for `'log10'`, keeping zero voxels finite. |
| `n_jobs`         | `int`   | `1`     | Parallelize the per-coefficient GP fits over cores (`-1` = all). Result-invariant. |

### `invert_optimize`

Estimates the beta that produced a target dose profile by projecting the target
into the surrogate's coefficient space and minimizing
`‖project(target) − c_GP(beta)‖²` over beta with bounded multi-start L-BFGS-B.
Runs against the surrogate, not Geant4. No `workflow:` needed.

| Keyword                | Type    | Default | Description |
|------------------------|---------|---------|-------------|
| `target`               | `str`   | *required* | The dose profile to invert: a stored field `.npz` (e.g. a held-out sample's `field.npz`) or a raw Geant4 dose file. Rows are reordered onto the training voxel order before projection. |
| `model_dir`            | `str`   | `<store>/surrogate` | The saved surrogate to invert. |
| `store`                | `str`   | `None`  | The store the model was fit from. Supplies the voxel order for models saved before it was recorded, and the default output location. |
| `num_starts`           | `int`   | `32`    | Multi-start count. Each start costs microseconds; more starts give a more thorough non-uniqueness report. |
| `seed`                 | `int`   | `0`     | Reproducible start scatter and `beta*`. |
| `bounds`               | `dict`  | model's training range | Optional per-beta `[lo, hi]` search box. Outside the training range the GP extrapolates, so only narrow it. |
| `identifiability`      | `bool`  | `True`  | Analyse which beta directions the dose constrains; writes `identifiability.txt`. Costs `2·D` GP evaluations. |
| `identifiability_file` | `str`   | `identifiability.txt` beside the result table | Override that path. |
| `output_file`          | `str`   | `<store>/inversion_result.txt` | One row per distinct minimum: `rank`, `misfit`, `relative_l2`, then the betas. |

**On non-uniqueness.** The surrogate reaches beta only through its `k` retained
POD coefficients, so the dose can constrain at most `k` combinations of beta. When
`k < D` the inverse problem is rank-deficient: some beta directions are invisible
to the dose, and many different beta reproduce it equally well. The multi-start
search reports every distinct minimum, but when their misfits are all numerically
zero the minima are samples from one degenerate surface and the `rank` column
reflects solver convergence, not evidence. `identifiability.txt` reports how many
directions are pinned down and which combinations are flat. To get a unique
answer, add information: narrow `bounds` on physical grounds, regularize, or use
`invert_bayesian` below.

### `invert_bayesian`

The same inversion, returning a posterior over beta rather than a point estimate.
NUTS (gradient-based MCMC via numpyro) samples a Gaussian likelihood in the
surrogate's coefficient space (the GP's predictive variance plus an assumed
`dose_sigma`) under a uniform prior on the training box. Gradients come from a JAX
re-expression of the fitted GP's prediction; fitting stays scikit-learn. No
`workflow:` needed.

| Keyword           | Type    | Default | Description |
|-------------------|---------|---------|-------------|
| `target`          | `str`   | *required* | Dose profile to invert (stored `.npz` or a raw Geant4 dose file), reordered onto the training voxel order automatically. |
| `model_dir` / `store` | `str` | — | As for `invert_optimize`. |
| `num_warmup`      | `int`   | `1000`  | Warmup draws per chain. |
| `num_samples`     | `int`   | `2000`  | Kept draws per chain (total = `num_samples × num_chains`). |
| `num_chains`      | `int`   | `4`     | Do not lower casually; see the warning below. Chains run in parallel across CPU devices. |
| `seed`            | `int`   | `0`     | Reproducible draws. |
| `dose_sigma`      | `float` | model's predictive std at the box center | Assumed target-noise scale in coefficient space. Raise to loosen the likelihood, lower to demand closer agreement. |
| `bounds`          | `dict`  | model's training range | The uniform prior. Along the flat directions the posterior equals it. |
| `identifiability` | `bool`  | `True`  | Compute the constrained/flat split; the summary then reports posterior width per direction. |
| `output_file`     | `str`   | `<store>/posterior_samples.txt` | Raw draws, one row per sample. |
| `summary_file`    | `str`   | `posterior_summary.txt` beside it | Per-beta mean/median/credible interval + `r_hat`/`n_eff`, plus the per-direction width table. |

**How to read the result.** The posterior is tight along the beta combinations
the dose constrains and as wide as the prior along the flat ones (measured
~0.01–0.08× vs ~1.1–1.25× prior width on the synthetic fixture). A prior-wide
flat direction is the correct result, not a sampling failure: the data says
nothing about that combination, so its value comes from `bounds`. The summary
reports the ratio per direction.

```{warning}
**Always check `r_hat`** in `posterior_summary.txt`; values above ~1.05 mean the
chains did not mix and the credible intervals are not trustworthy. A stuck chain
explores only a slice of the degenerate manifold and reports the flat directions
as narrow, as if the dose constrained beta. Measured with one chain:
`r_hat = 1.61` and flat widths ~0.04–0.10× prior (wrong); with four:
`r_hat ≈ 1.01` and ~1.1× (right).
```

## The Workflow object

The `workflow:` list is built into a
{py:class}`~lume_ace3p.workflow_graph.Workflow`: a validated, topologically
ordered chain of modules with a single `evaluate` seam. The `run_lume_ace3p`
entry point calls `Workflow.from_config(yaml_data)` and hands the result to the
mode layer; you rarely construct one directly.

Its public seams are:

- `Workflow.evaluate(input_scalars=None, workdir=None, resume=False)` runs the
  module chain once for one input point and returns `({output_name: value}, ctx)`:
  the extracted `output_parameters` values plus the `RunContext` that produced
  them. `input_scalars` may be `None` (use the base inputs), a list aligned with
  `sweep_axes()` (that grid point), or a `{var: scalar}` mapping (variable
  overrides routed to their declaring bucket, the shape Xopt passes; see
  [](#vocs_parameters)). An explicit `workdir` overrides `workdir_mode` naming for
  that call. Each call writes the run manifest ([](#run-manifest)); `resume=True`
  reads the existing one first and skips the external tool of every module it
  records as complete (see [](#resume)).
- `Workflow.sweep_axes()` returns the array-valued input leaves a sweep iterates
  over.
- `Workflow.point_workdir(point_index)` returns the `'indexed'` name for one
  sweep point. `evaluate` takes no point index: the mode layer owns sweep
  ordering, resolves the name here, and passes it as `workdir=`.
- `Workflow.resolved_workdir(input_scalars=None, point_index=None)` and
  `Workflow.point_config_hash(input_scalars=None)` answer where a point would run
  and the hash its manifest must carry to be resumable, without running anything.
  `--status` uses these to find and judge each point's manifest.
- `Workflow.field_index(ctx)` / `Workflow.field(ctx)` return the shared field
  index (e.g. S3P's `('Frequency', array)`) and the structured per-run field
  output (S3P spectra, Geant4 voxel grids) kept out of the flat table. Both read
  the evaluation `ctx` describes, defaulting to the most recent
  (`Workflow.last_context`) when omitted.

The `ctx` is the per-evaluation carrier: that run's workdir, artifacts, outputs
and live module instances. `Workflow.modules` is a separate list of never-run
prototypes, useful only for inspecting configuration.

### Input data model

`WorkflowInputs(cubit, ace3p, macro, particles)` is the structured
representation the workflow consumes, built by `inputs.build_inputs` from the
YAML. The four buckets correspond to the four `input_parameters` sub-blocks:

| Bucket  | YAML source (nested)          | Deprecated flat alias      | Type                |
|---------|-------------------------------|----------------------------|---------------------|
| `cubit` | `input_parameters.cubit`      | `cubit_input_parameters` / bare `input_parameters` | `dict[str, scalar \| ndarray]` |
| `ace3p` | `input_parameters.ace3p`      | `ace3p_input_parameters`   | ordered tree of `(name, child)` pairs (`Section`); duplicates preserved |
| `macro` | `input_parameters.geant4`     | `geant4_input_parameters`  | `dict[str, scalar \| ndarray]` |
| `particles` | `input_parameters.particles` | `particles_input_parameters` | `dict[str, scalar \| ndarray]` |

Array-valued leaves in any bucket become sweep axes; scalar leaves are written
through to the matching input file unchanged. During optimization, each VOCS
variable is routed to the bucket where it is declared (see [](#vocs_parameters)).

(results)=
### Results

The table modes (`single`, `parameter_sweep`) return a pandas `DataFrame` with
one row per evaluation, or one row per `(grid-point, index)` when a field-indexed
solver's axis (S3P's `Frequency`, Omega3P's `ModeID`, T3P's `s`/`t`) is spanned
by at least one declared output or no outputs are declared at all. When
`mode.output_file` is set it is written by
{py:func}`~lume_ace3p.results.write_table` (a tab-delimited `to_csv`).
Structured field outputs of a wide row (an Omega3P run with all outputs narrowed
to one mode, a Geant4 dose grid) are persisted separately as `.npz` and
referenced by a `field_artifact` column; load one with
{py:func}`~lume_ace3p.results.load_field`. The Xopt modes return the
{py:class}`xopt.Xopt` object and log its `X.data` table through the same writer.

For full class- and method-level documentation, see the
[API reference](api/index).
