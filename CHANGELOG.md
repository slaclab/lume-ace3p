# Changelog

All notable changes to `lume-ace3p` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases before 0.4.0 are reconstructed from git history and are summarized at a
coarser grain than the entries above them.

## [0.5.0] — 2026-09-01

**Resume.** A campaign cut off by a batch wall clock used to be lost
entirely — the next run rebuilt its mesh and re-solved from scratch, so a 2-hour
allocation that died at 90% cost the whole 2 hours. It is now recoverable: every
evaluation records what it did, and `mode: {resume: true}` picks a campaign up
where it stopped — by two mechanisms, because a sweep and an optimization have
different notions of "already done": a sweep point is picked up from the manifest in
its own workdir, while an optimization restores the optimizer's whole state. Getting
there took removing the per-evaluation state that lived on shared objects and giving
a sweep point a stable identity, which is also the groundwork concurrent evaluation
will stand on. The designs and their verification are in
[`plans/evaluation_isolation_resume_plan.md`](plans/evaluation_isolation_resume_plan.md)
and
[`plans/xopt_resume_workdir_plan.md`](plans/xopt_resume_workdir_plan.md).

**Alongside it, the Xopt modes stopped being quiet about being misconfigured** — a
silently shared workdir, a typo'd key, an optimization with nothing that would ever
terminate it. See
[`plans/xopt_config_validation_plan.md`](plans/xopt_config_validation_plan.md).

⚠️ **Behaviour change:** `run-lume-ace3p` now exits **non-zero** when a config
cannot run (an unsupported generator, no termination criterion, an unsupported
`cost_function`, MOBO without a `reference_point`). It previously printed a message
and exited 0, so a batch job that did nothing reported success. No shipped batch
script branches on the exit status, but a local wrapper that does will start seeing
failures it did not see before — which is the point.

### Added

- **`mode: {resume: true}` in the Xopt modes** continues an interrupted
  optimization from `xopt_state.yml` instead of starting over. An optimization killed
  at evaluation 190 of 200 used to throw away all 190, which is worse than losing a
  sweep — in an optimization the *evaluations* are the expensive part.

  A **different mechanism** from the table modes', not an extension of it: an
  optimization has no fixed set of points to have finished part of, so there is
  nothing for a per-point manifest to key on. What is restored instead is the
  optimizer's whole state — the trajectory *and* the generator's own internal state.
  That distinction is load-bearing rather than thorough: replaying just the recorded
  table into a fresh generator is nearly equivalent for a Bayesian generator (the GP
  is refit from data either way) but not for `NelderMeadGenerator`, where the simplex
  *is* the state, so a data-only restore restarts the search on top of old data and
  re-proposes points it already has.

  **The promise is narrower than the sweep's**, and stated that way in the docs: no
  evaluation is repeated and the search continues from the same data — *not* that a
  resumed run reproduces the trajectory an uninterrupted one would have taken. An
  equally informed generator is not the same generator, and the torch/numpy RNG
  streams alone break reproduction.

  Every iteration budget (`num_random`, `num_step`, `max_iterations`, `max_steps`,
  `cost_budget`) is read as a total for the **campaign** rather than for this
  process, so a resumed run continues to the same finish line and resuming a finished
  optimization does nothing. It works under any `workdir_mode`, and under
  `auto`/`indexed` continues the per-evaluation workdir numbering rather than
  overwriting the inherited evaluations' directories.

  **A state file that disagrees with the config is refused, and what it refused is
  kept.** Four disagreements are caught: a different generator, a flipped
  `MINIMIZE`/`MAXIMIZE`, moved variable bounds, and a changed **workflow** — the
  module chain, the `output_parameters` spec, or any input the optimizer is not
  driving. The last is the check the VOCS cannot make: the same variables and
  objective over a different mesh or solver input is a different campaign, and its
  recorded evaluations describe a different model. Editing the *nominal* value of an
  optimized variable is not a change, since the optimizer overrides it every
  evaluation. On a refusal the existing state and run log are renamed to `.rejected`
  (then `.rejected.1`, …) before the fresh campaign takes their place, so declining
  to continue a campaign does not also destroy it. Resuming with a *smaller* budget
  does nothing and says so. See
  [the reference](docs/yaml_reference.md#xopt-resume).
- **`xopt_state.yml`,** written beside the mode's `output_file` after every
  evaluation of an Xopt run, holding the full optimizer state. Written whether or not
  `resume` is set, for the same reason the run manifest is: the decision to resume is
  made *after* the interruption, so the record has to already exist. Written to a
  temporary and renamed, so a run killed mid-write leaves the previous state rather
  than the truncated file a resume would then read — and an unreadable or truncated
  one degrades to "no state" (start over) rather than to a crash or a misread.
- **`mode: {resume: true}`** (the table modes) drives each point through the run
  manifest in its own workdir: a point that already finished contributes its row
  without launching a solver, one that died partway restarts at its first
  unfinished step, and one that never started runs normally. **The result table is
  identical to an uninterrupted run's** — resuming is the same sweep minus the work
  already done, not a different kind of run.

  A resumed module re-runs its *parser* and skips only its subprocess, which is
  what makes that identity hold with no special cases: an S3P point has to re-read
  its frequency axis to know how many rows it contributes. A module that does have
  to re-run makes every later module re-run too, since its outputs are their
  inputs — which is what stops a re-solved `t3p` from being paired with a skipped
  `acdtool postprocess transwake` and reporting the longitudinal loss factor as a
  kick factor.

  Opt-in on purpose (a sweep that silently adopted a stale workdir would be worse
  than no resume), keyed on the manifest's `config_hash` so a workdir written for
  another configuration is re-run and says so, and refused under
  `workdir_mode: manual`, where every point shares one directory and so cannot
  carry per-point state. A completed step whose output files have since been
  deleted is re-run rather than trusted (`Module.verify`), and a resumed point's
  re-extracted outputs are cross-checked against the recorded ones. See
  [the reference](docs/yaml_reference.md#resume).
- **`run-lume-ace3p --status`** now covers the Xopt modes too, reporting the
  evaluations recorded and the best objective so far rather than a per-point table —
  an optimization's points were chosen as it went, so "point 5 of 8" does not exist
  for it. Reads only `xopt_state.yml`; runs nothing.
- **`run-lume-ace3p --status <config.yaml>`** prints a per-point completion table —
  `complete` / `partial` / `failed` / `stale` / `absent`, how much of each point's
  chain is recorded, and the step a resume would start from. It reads only the
  manifests already on disk, runs nothing and writes nothing, so it is safe to
  poll while a campaign is still going.
- `collect_training_data` accepts `resume:` too, so a DOE sample interrupted
  *midway through the chain* restarts at its first unfinished step. Its existing
  behavior is unchanged: a sample whose `field.npz` is already stored is still
  skipped outright, whether or not `resume` is set, so training stores collected
  before this release stay valid.
- **Unrecognized configuration keys are reported.** Every block with a fixed key
  set — the top-level blocks, `mode:`, `workflow_parameters`, `vocs_parameters`,
  `xopt_parameters` — is compared against the keys the code actually reads, and an
  unrecognized one warns, names the near miss (`num_steps` → "did you mean
  `num_step`?") and lists what is recognized there. Nothing used to make that
  comparison, so a typo was silent: `num_steps` produced a run with no termination
  criterion, a `resume:` in a `train_surrogate` block did nothing, `output_parameter`
  (singular) meant a run that extracted nothing.

  A warning, never an error — a config with a harmless extra key still runs — and the
  sets are **per mode**, so the same key can be right in one block and reported in
  another. `input_parameters` / `output_parameters` are never inspected; their keys
  are the user's own names. A separate warning covers the one silent *misroute* in the
  config surface: an `input_parameters` block that mixes bucket names with a
  misspelled one (`qubit:`) is read wholesale as the legacy flat cubit block, dropping
  every real parameter, and now says so.
- **A run manifest, `lume_ace3p_state.json`,** written into every evaluation's
  workdir and updated **after each module rather than once at the end** — so the
  file a wall-clock-killed run leaves behind says how far it got: which modules
  completed, what each produced, which one failed and with what error, and the
  extracted outputs. It also records a `config_hash` over the resolved per-point
  configuration (module entries, materialized input point, `output_parameters`)
  and deliberately not over `paths`, `dry_run`, `workdir` or comments, so the same
  workdir is recognizable on another machine and reformatting a config does not
  invalidate a half-finished campaign. No YAML key: it is always written, and it is
  what `resume` and `--status` read — see
  [the reference](docs/yaml_reference.md#run-manifest).
- **`Module.verify(ctx)`**, answering `True` / `False` / `None` (unknown) for "is
  this module's output still on disk" — the check that stops a recorded-complete
  point whose results were deleted from being trusted. It is deliberately
  *unknown* for an `acdtool` command that overwrites its producer's file
  (`postprocess transwake` writes over `wakefield.out`): that file's presence is
  no evidence the step ran, and treating it as evidence would report T3P's
  longitudinal wake as a kick factor.
- **`workflow_parameters: {workdir_mode: indexed}`** names each evaluation's
  folder `<workdir>_0`, `<workdir>_1`, … by its position in the sweep, alongside
  the existing `manual` and `auto`. `auto` names by swept scalar value, which is
  usually unique but can collide and grows with every axis added; an index is
  bounded and collision-free, which is what identifying a point across runs
  needs. The two produce identical result tables — only the names differ.
- **`workflow_parameters: {capture_output: true}`** (the new default) tees each
  module's Cubit / solver / `acdtool` / Geant4 output to
  `<workdir>/<module name>.log`. Teed, not redirected: everything still appears
  on the terminal, `stderr` stays on `stderr`, and it streams line by line rather
  than appearing when the process exits — so a solver failure cannot become
  invisible and a long solve does not go silent. Without this, one sweep point's
  output was interleaved with every other point's on one terminal and gone as
  soon as it scrolled, which is exactly the state a wall-clock-killed run leaves
  behind. Set it to `false` for the previous inherited-stream behavior.
- **One directory per evaluation in the Xopt modes.** Under
  `workflow_parameters: {workdir_mode: auto}` (or `indexed`) `scalar_optimize` and
  `gp_parameter_sweep` now run each evaluation in `<workdir>_0`, `<workdir>_1`, …
  numbered by iteration in evaluation order, so the mesh, solver input, results and
  log behind row *n* of `sim_output.txt` are the ones in `<workdir>_n`. `auto`
  numbers rather than naming by input value because an optimizer's proposals are
  full-precision floats — `lume-ace3p_workdir_14.724999999999998_1.5750000000000002`
  is not a name anyone looks a run up by — and because an index cannot collide, so
  two evaluations at the *same* proposed point (which a Nelder–Mead simplex does
  produce) still get two directories. A sweep's `auto` naming is unchanged: there
  the swept values *are* the point's identity. The four shipped Xopt examples set
  `workdir_mode: auto`.
- **A warning when a multi-evaluation run shares one workdir.** `workdir_mode`
  still defaults to `manual`, which is legal and occasionally deliberate but
  silently destructive the rest of the time: N evaluations in one directory means
  each overwrites the previous one's mesh, input files, results, logs and run
  manifest. A sweep or optimization about to do that now says so once, and names
  the fix. The default is unchanged — flipping it would silently relocate the
  output of every config that omits the key.

### Changed

- **`Workflow.evaluate` returns `(outputs, ctx)`** rather than `outputs` alone,
  and accepts an explicit `workdir=` that overrides `workdir_mode` naming for one
  call. The returned `RunContext` is the per-evaluation carrier: pass it to
  `Workflow.field(ctx)` / `Workflow.field_index(ctx)` to read *that* evaluation's
  results. Both still work with no argument, resolving to the most recent
  evaluation. This is a breaking change only for code calling `evaluate`
  directly; no YAML changes.
- **Module instances are built per evaluation.** They hold run state (a solver's
  parsed results, `acdtool`'s parsed output), so the chain now lives on
  `ctx.modules` and `Workflow.modules` is a never-run prototype list used only to
  inspect configuration. Previously one shared list meant `field()` and `extract`
  answered for whichever point ran last — correct only because the sweep loop is
  serial, and wrong data rather than a crash the moment it is not.
- The sweep table's field-index label is resolved from the run it came from
  instead of re-derived after the loop, and `collect_training_data` drives each
  sample through `evaluate(workdir=...)` instead of assigning
  `workflow.baseworkdir` inside its loop.
- **`parameter_sweep` assembles rows by point index rather than in completion
  order.** Each point's contribution is collected as it finishes and the frame is
  built from that list, sorted, once the loop is done. Today the loop is serial
  and in order, so nothing changes; it is what keeps the frame identical when the
  loop resumes or (later) parallelizes, instead of quietly making every result
  table depend on which point finished first.
- **Two modules may no longer share a `name:`.** A module's name identifies its log
  file and its entry in the run manifest, and resume identifies a step by it, so a
  duplicate is now a validation error rather than two steps overwriting each
  other's record. Reachable only with an explicit `name:` on two entries of
  different types — two modules of one type already collided on their artifact, and
  that remains the error reported for them.
- A solver's results subdirectory is now a class attribute
  (`ACE3P.results_subdir`, `'OUTPUT'` for T3P only) rather than a `results_dir()`
  override, so a caller holding the class — the module layer asking where results
  *would* be, without instantiating a solver — gets the same answer as an
  instance. Same paths as before.
- **`gp_parameter_sweep` logs after each seeding evaluation**, not only after the
  first optimization step. A run killed during its `num_random` seeding previously
  left nothing behind at all.
- **An invalid `workdir_mode` is rejected when the `Workflow` is built**, naming the
  valid values, rather than when the name happens to be resolved — the same point
  `stage_mode` has always failed at. A typo (`'index'`, `'Auto'`) used to construct
  fine and die partway through the first evaluation.
- **Per-evaluation directories with no `workdir:` configured land inside the
  working directory**, named from `lume-ace3p_workflow_output`
  (`lume-ace3p_workflow_output_0`, …). `workdir` defaults to the working directory
  and a per-evaluation name *appends* to its base, so previously they would have
  been named as siblings of the working directory (`/path/to/run_0` beside
  `/path/to`). Only `manual` runs in the working directory itself. Affects `auto`
  and `indexed` sweeps as well as the Xopt modes; every shipped example sets
  `workdir`, so nothing shipped changes location.

### Fixed

- **A misconfigured optimization no longer exits 0.** An unsupported `generator`, a
  missing termination criterion, an unsupported `cost_function`, and
  `ExpectedHypervolumeImprovementGenerator` without a `reference_point` each used to
  print a message and return `None`, which the CLI ignored — so the process reported
  success having done nothing. In a batch queue that is a job that consumes its
  allocation, writes no output, and is indistinguishable from one still queued. All
  four now raise, and `run-lume-ace3p` prints `Error: …` and exits non-zero. A solver
  crash or a bug still produces a traceback: the handler covers configuration errors
  only. No shipped batch script branches on the exit status, so none needed updating.
- **The "no termination criteria" message named criteria that do not work.** It listed
  `tolerance` as one — and `tolerance` is only a stopping *test* applied inside the
  `num_step` / `cost_budget` loop, so a user who had supplied exactly that was sent
  round in a circle. `max_iterations` was not listed at all, although
  `docs/optimization.md` presented it as a criterion; it is read only alongside
  `num_step` and is ignored without it. The message now separates the three real
  criteria (`num_step`, `cost_budget`, `alotted_time`) from the two refinements, names
  whichever refinement the config supplied, and says what it actually does. The docs
  were corrected to match. `max_iterations` alone is still ignored — making it work
  standalone would silently start running configs that today run nothing.
- **An optimization over an ACE3P or Geant4 knob no longer runs every evaluation
  in one directory.** Under `workdir_mode: auto` the directory name came from the
  input *values*, but only from the `cubit` and `particles` buckets — so an
  optimization whose variable lived in `ace3p:` or `geant4:` produced a single
  unchanging name (`lume-ace3p_workdir_100.0`) that every evaluation overwrote,
  while the layout still *looked* per-point. Whatever the last write left behind
  was reported as the run's output. The per-iteration naming above fixes it for
  every bucket. Shipped examples all optimize Cubit variables, so none of them hit
  this.

## [0.4.0] — 2026-08-20

Two reworks land together: `acdtool` postprocessing gained a real command
surface and shape-driven output parsing, and T3P gained multi-monitor reading.
Both were built against the SLAC ACE3P command references and against real CW23
tutorial output frozen as fixtures, rather than against assumed formats.

Implementation records — what was built, how it deviated from the design, and
what each left owed — are in
[`plans/acdtool_rework_plan.md`](plans/acdtool_rework_plan.md) and
[`plans/t3p_monitor_plan.md`](plans/t3p_monitor_plan.md).

### Added

- **`acdtool` command dispatch.** The `acdtool` module takes a `command:` key
  naming one of the 19 documented commands, instead of assuming
  `postprocess rf`. Each command declares its own argument form (input file,
  positional, or input-plus-jobname), argument count, and the artifact it
  consumes, so `[cubit, t3p, acdtool(command: postprocess transwake)]` now
  validates where it previously could not.
- **Shape-driven `.rfpost` output parsing.** The 24 `.rfpost` blocks collapse to
  six output shapes, each with one reader driven by what the file actually says —
  the header row for a column table, the `key = value` lines for a scalar block —
  replacing a three-branch ladder over hand-counted column positions. A block
  whose output cannot be read now warns naming itself.
- **Omega3P eigenmode results.** `omega3p.out` is parsed directly, so
  `Frequency`, `QualityFactor` and `ExternalQ` are available from the solver
  without an acdtool step. Handles complex eigenvalues (`'real , imag'`) and
  top-level sections appearing in any order.
- **S3P phase.** `S(m,n)_real`, `S(m,n)_imag` and `S(m,n)_phase_deg` are read
  from `SParameter.out`. The existing `S(m,n)` magnitude key is unchanged. An
  older ACE3P build that writes no `SParameter.out` warns and returns the
  magnitudes rather than failing.
- **T3P multi-monitor reading.** A `.t3p` file may declare any number of
  `Monitor` blocks of six documented `Type`s, and all of them are read. `Name`
  is the selector (a run may declare several monitors of one type) and `Type`
  supplies the output shape. A run with no `WakeField` monitor is no longer a
  dead end — its series monitors are read.
- **The output-spec mapping form**, `{module, section, quantity, at, component}`,
  which expresses what the positional list form could not: dropping `at:` asks
  for a whole array rather than one narrowed scalar.
- **`results_dir:`** on the solver modules, naming the directory a run writes
  into.
- New examples: `t3p_power_balance` (three `Power` monitors on one run, swept
  over an ACE3P coating thickness), `t3p_transwake`, `omega3p_dispersion_sweep`,
  `s3p_window_rfpost`.
- New documentation: [`docs/acdtool_reference.md`](docs/acdtool_reference.md)
  (19 commands, 24 `.rfpost` blocks, and what is implemented here) and
  [`docs/t3p_reference.md`](docs/t3p_reference.md) (the six monitor types, what
  each writes, and which have real output behind them).
- `references/` — the eight SLAC ACE3P command-syntax PDFs, now the authoritative
  spec the parsers are written against.

### Changed

- **`results_dir:` now reaches the solver.** It is passed to `omega3p`, `s3p` and
  `track3p` as the second positional argument, the way the CW23 batch scripts
  select a results directory. Previously it told `lume-ace3p` where to *look*
  without telling the solver where to *write*, so any non-default value pointed
  the reader at a directory the run never created.

  The `t3p` module is the documented exception and remains read-only: no ACE3P
  reference describes a solver command line, and none of the T3P invocations in
  CW23 passes a second positional argument, so nothing establishes that `t3p`
  accepts one. See `T3P.accepts_results_dir_arg`.
- `acdtool`'s `requires` is per-command rather than fixed: `postprocess rf` needs
  an `em_solution`, while `transwake` / `coaxsignal` / `volmontomode` need a
  `td_solution` and so chain after T3P.
- `acdtool` command lines no longer hardcode `--nodes=` / `--ntasks=`, and
  `--cpu-bind` is dropped for a non-`srun` MPI caller. (The `acdtool meshconvert`
  call inside the `cubit` module still pins `--nodes=1 --ntasks=1`.)
- The shipped examples were migrated to the output-spec mapping form.
- Implementation plans moved from `docs/` to `plans/`. They are development
  history rather than user documentation, and in the Sphinx tree they were
  building into the site while belonging to no toctree.

### Deprecated

Both forms below still work and produce the same values; each warns naming its
replacement.

- The **positional acdtool output spec** (`['RoverQ', '0', 'RoQ']`) in favor of
  the mapping form (`{module: acdtool, section: RoverQ, quantity: RoQ, at: {mode: 0}}`).
  The `geant4` list form is *not* deprecated.
- The flat top-level `*_input_parameters:` keys (`cubit_input_parameters`,
  `ace3p_input_parameters`, `geant4_input_parameters`, …) in favor of the nested
  `input_parameters:` sub-blocks introduced in 0.2.1.

### Fixed

- A multi-line `portID = {\n 7\n 8\n}` in a `.rfpost` file parses and round-trips
  instead of being lost.
- An unknown `.rfpost` block round-trips untouched rather than silently parsing
  to two empty blocks.
- An unsupported `acdtool` command raises an error naming the command instead of
  failing later and obscurely.
- The docs build is clean — no warnings and no errors. Fixed a malformed reST
  table, three undefined-substitution errors from `|S|` in docstrings, duplicated
  attribute descriptions on `Command` / `Section` / `Monitor`, a broken
  cross-reference to `input_parameters.ace3p`, and a docstring definition-list
  break.

### Testing

- Real CW23 output frozen as fixtures under `tests/fixtures/acdtool/`, with
  provenance in `SOURCES.md` and the gaps named in `COVERAGE.md` — including
  which shapes have **no** real-output fixture and are therefore implemented from
  the reference and marked unvalidated.
- `tests/baseline/` now accounts for every shipped example: it is asserted that
  the frozen registry and the `NOT_FROZEN` record partition `examples/` between
  them, so a coverage gap cannot be mistaken for a decision.
  `omega3p_optimization`, `geant4_dose_single` and `geant4_beta_surrogate` were
  such a gap and are now recorded with reasons.

## [0.3.5] — 2026-08-11

- Removed the botorch GP-fitting tests and the pytest `slow` tier; plain
  `pytest` runs everything.
- Added T3P as a runnable time-domain wakefield solver module.

## [0.3.4] — 2026-08-03

- Dose-surrogate inversion: `invert_optimize`, identifiability analysis, and
  `invert_bayesian`.
- Surrogate fit viewer improvements (mm axes, locked dose ranges, colorbar).
- Corrected the Geant4 executable path.

## [0.3.3] — 2026-07-28

- Surrogate fit viewer, log-space training, and parallel GP fitting.

## [0.3.0] – [0.3.2] — 2026-07-22 to 2026-07-27

- The `collect_training_data` mode and the dose-surrogate DOE.

## [0.2.1] — 2026-07-13

- Standardized the nested `input_parameters:` schema and cross-code VOCS
  routing, deprecating the flat `*_input_parameters:` keys.

## [0.2.0] — 2026-07-13

- The module / workflow / mode architecture: one adapter per pipeline step, a
  declarative YAML-defined module DAG, and workflow-agnostic modes. See
  [`plans/workflow_module_refactor_plan.md`](plans/workflow_module_refactor_plan.md).
