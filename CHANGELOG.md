# Changelog

All notable changes to `lume-ace3p` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases before 0.4.0 are reconstructed from git history and are summarized at a
coarser grain than the entries above them.

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
