# Changelog

All notable changes to `lume-ace3p` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases before 0.4.0 are reconstructed from git history and are summarized at a
coarser grain than the entries above them.

## [Unreleased]

**Sweep resume.** A sweep point cut off by a batch wall clock used to be lost
entirely — the next run rebuilt its mesh and re-solved from scratch, so a 2-hour
allocation that died at 90% cost the whole 2 hours. It is now recoverable: every
evaluation records what it did, and `mode: {resume: true}` picks a campaign up
where it stopped. Getting there took removing the per-evaluation state that lived
on shared objects and giving a sweep point a stable identity, which is also the
groundwork concurrent evaluation will stand on. The design and its verification are
in
[`plans/evaluation_isolation_resume_plan.md`](plans/evaluation_isolation_resume_plan.md).

### Added

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
