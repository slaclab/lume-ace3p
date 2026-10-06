# Track3P as a Workflow Module — Implementation Plan

**Status: IN PROGRESS — Phases 0 and 1 done 2026-09-14, Phase 2 done
2026-10-01, Phase 3 done 2026-10-05, Phase 4 steps 0–1 done 2026-10-06 (its
steps 2–3, the example migration, deferred pending an LCLS dump asset — see the
Phase 4 status note and §6); Phase 5 is next** (see the status notes at
the end of each finished phase). Written 2026-09-14 on S3DF. Decision taken
2026-09-14: the Fowler–Nordheim model is Lixin Ge's plain-FN form with `1/f`
(§3.6, §6); the `geant4_track3p_beta` baseline will move in Phase 2. Follows
`plans/t3p_monitor_plan.md` and `plans/acdtool_rework_plan.md` (both COMPLETE)
and reuses their machinery: the `_SolverModule` pattern, the header-driven column
reader, the "one index axis per module" rule, fixtures copied verbatim from real
runs, and build-time `WorkflowValidationError`s that name the fix.

Every format claim below was verified 2026-09-14 by running the S3DF Track3P
binary on the CW23 Pillbox case (probe runs listed in §2.4), by reading the
Track3P source, and by reading Lixin Ge's LCLS-II package (§2.2). Where the
reference PDF and the shipped build disagree, the build wins and the
disagreement is recorded.

---

## 1. Motivation and scope

Two use cases, one module.

**A. Track3P standalone** (multipacting, dark current, no Geant4). A workflow
ends in `track3p`, and the modes read its enhancement counter, resonant
particles, Faraday-cup crossings and log scalars as result-table columns:

```
cubit -> omega3p -> track3p          geometry sweep / optimization against MP onset
mesh_source -> s3p -> track3p        travelling-wave structures (FieldDir: ./s3p_results)
... -> track3p -> acdtool            existing 'postprocess track3p' command, now in-pipeline
```

**B. Track3P -> particles -> geant4** (the ACE3P-ML LDRD dark-current chain).
Track3P is run once per cavity and field level, its 17-column dump is
reweighted analytically for any β, and Geant4 is run per β. Today the dump
enters through `track3p_source`; after this plan it can also be produced
in-pipeline. The reference implementation of this chain is Lixin Ge's package
(§2.2); the repo's `particles.py` must reproduce its weights (§3.6).

Out of scope: the 36 `PtrackMonitor` output types (a later plan, same shape as
T3P monitors), the `Type: Geant4` dump (`particles.data`, 10 columns; the
`particles` module already writes that format itself), PIC/gun3p emitters.

---

## 2. Verified facts

### 2.1 Binaries and archives on S3DF

| What | Where |
|---|---|
| Group ACE3P (2026-08-28) | `/sdf/group/rfar/ace3p/bin/{track3p,omega3p,s3p,t3p,acdtool}`; `source ~/ace3p.sh` |
| Lixin Ge's build (2026-08-31, commit `b7f4a98f`) + **full source** | `/sdf/group/rfar/lge/sdf/ace3p/bin/track3p`, `.../multipacting/src/*.C`; env `/sdf/group/rfar/lge/sdf/env.sh` |
| CW23 tutorial archive (11 archived Track3P runs, all with `track3p.log`) | `/sdf/data/rfar/nfs/acd/u01/cw23/examples/track3p/{Pillbox,TW7Cell,Window,Muon201MHz,Coax-StaticField}` |
| Reference PDF (inputs only) | `references/track3p-commands.pdf` |
| Tutorial (outputs p.26, 34–38; FaradayCup p.118–121) | `/sdf/data/rfar/nfs/acd/u01/cw23/presentations/Track3P-Tutorial.pdf` |

Neither PDF documents `OutputImpactsInfo`, `LostParticles_*`, `PtrackMonitor`,
`Domain.Mode`, or the Emitter keys `N M Q d SuppressionFactor`. The
`InputParameters` file each run writes echoes every key the build recognises
with its default; it is the best documentation of the build that exists.

### 2.2 Lixin Ge's LCLS-II package (the reference for use case B)

`/sdf/group/rfar/geant4/example/ace3p-geant4-workflow-LCLS-polycone/` — handoff
2026-09-10. Read `README.md`, `HANDOFF_NOTE.md`, `DavidsQuestion.md` (answers
the seven Track3P questions from source), `WORK_SUMMARY.md`.

- `scripts/make_track3p_input.sh` — solo-mode Track3P input per cavity/field.
- `scripts/submit_track3p.sh` — 2 MPI × 60 OpenMP on one exclusive milano node,
  45–55 min per run for the 1.45 M-element cryomodule mesh.
- `scripts/convert_track3p.py` — the Fowler–Nordheim reweighting (§3.6).
- `data/track3p/<cav>_<field>/ImpactsInfo_*` — 13 finished dumps, ~0.7 M rows each.
- `data/particles/*.data` — 22 reweighted Geant4 inputs (cavity × field × β).
- `geant4/build/sim` — Geant4 app (G4Polycone cavity), keys `passes`, `seed`,
  `cavity_stl` (an R(Z) profile, not an STL), `detectors`; new outputs
  `<prefix>_detector_dose.csv`, `<prefix>_detector_gamma_spectrum.csv`.
- Ignore the superseded sibling `ace3p-geant4-workflow-LCLS/`.

The repo's Geant4 examples still point at `/sdf/group/rfar/geant4/example/dose-npass`.

### 2.3 What Track3P writes (current build, verified)

Results directory = 2nd positional argument, else `track3p_results`:

| File | When | Notes |
|---|---|---|
| `track3p.log` | always | version banner, input echo, per-level `scale … Field …` lines, ends `Done!` then `Total Emitted Particles = N` |
| `track3p.warn` | always | |
| `InputParameters` | always | echo of every recognised key with defaults |
| `ImpactsInfo_<level>` | `OutputImpacts: on` | one per field level; layout depends on `OutputImpactsInfo` (below) |
| `LostParticles_<level>` | `OutputImpacts: on` | new vs 2023; 2023 layout |
| `OUTPUT/enhancementCounter` | `Postprocess.EnhancementCounter.Token: on` | header: `fieldlevel ID enhancement averageEnhancement maxEnhancement maxEnhancementImpactNum totalImpactNum FinalImpactLocationX/Y/Z` |
| `OUTPUT/resonantparticles` | `Postprocess.ResonantParticles.Token: on` | header: `Field_Level ID Resonant_X/Y/Z Energy Initial_X/Y/Z Total_time Total_Num` |
| `OUTPUT/MPParticles<level>` | same | per-particle `Particle_ID: n Total_Impacts: m` blocks; not a table |
| `OUTPUT/faradaycup_<boundaryID>` | `Postprocess.FaradayCup: {Token: on  BoundaryID: …}` | 16 columns, 2023 layout minus `volID` |
| `PARTICLES/partpath_ts*.ncdf`, `emissionevents_ts*.ascii` | `ScanToken: 0` | trajectories; not read |

There is **no `track3p.out`** (the wrapper's `output_file` is wrong) and **no
`en`** — `en` is written by `acdtool postprocess track3p`, which the repo's
acdtool table already models.

**The two `ImpactsInfo_*` layouts.** Both are 17 columns; they are different files.

```
# default ("general"), header NOT commented:
InitialID ImpactNum Initial_x Initial_y Initial_z Impact_x Impact_y Impact_z InitialPhase ImpactPhase ImpactEnergy NumElectrons momentum_x momentum_y momentum_z FaceID volID

# OutputImpactsInfo: { Type: Initials-Impacts }, header '#'-commented — what particles.py reads:
#InitialID ImpactOrder Initial_x Initial_y Initial_z Impact_x Impact_y Impact_z InitialPhaseinRFcycle ImpactPhaseinRFcycle ImpactEnergy momentum_x momentum_y momentum_z ImpactFaceID InitialNormalField InitialFaceArea
```

The selector is a **container**, parsed with `findContainer` (`genptab.C:543`);
the scalar spelling `OutputImpactsInfo: Initials-Impacts` is silently ignored.
Other types (`ImpactsManager.C:286–580`): `Position` (12), `PositionField` (16),
`Geant4` (10, no header, `particles.data`, needs `BoundarySurfaceID`,
impactOrder==1 only), `Geant4-Energy` (16). `InitialPhase` and
`InitialPhaseinRFcycle` are the same quantity (`time × frequency`).
`InitialNormalField`/`InitialFaceArea` are only meaningful for `Emitter Type: 7`
(field emission); the Pillbox secondary-emission run writes 0 and a placeholder.

**Index axis.** Field level, declared pre-run in `FieldScales`
(`Minimum/Maximum/Interval` with `ScanToken: 1`, or `Scale` with `ScanToken: 0`),
and echoed in every output file name and in the `fieldlevel`/`Field_Level`
columns. S3P-shaped (known before the run), not Omega3P-shaped.

**Field emission emits nothing unless `N` is small.** `N` is "less than half
of this number, the macroparticle will not be emitted" (default 1). The Pillbox
wall box with `N: 100, Beta: 50` emitted 0 particles. Lixin uses `N: 1`.

### 2.4 Probe runs (fixture sources)

`/sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/track3p_probe{,2,3,4}/` —
CW23 Pillbox mesh + omega3p + track3p, 16 milano cores, each job < 90 s:

| Dir | Input | What it shows |
|---|---|---|
| `track3p_probe/track3p_results` | `Pillbox.track3p` (3-level scan) | default layout, enhancementCounter, resonantparticles, MPParticles, LostParticles, track3p.log |
| `track3p_probe/2.3MV` | `Pillbox2.3MV.track3p` (ScanToken 0, `EmissionOutput: 1`) | PARTICLES/, EmissionOutput has no effect on ImpactsInfo |
| `track3p_probe3/w4_type7_model2_fcup` | Type 7 + Model 2 + FaradayCup 1 2 6 | `faradaycup_*` headers (empty bodies: 0 emitted) |
| `track3p_probe4/b1_initials_impacts` | `OutputImpactsInfo: { Type: Initials-Impacts }` | the 17-column `#`-headed dump, 14 853 rows |

Scratch is not permanent. Phase 0 copies what it needs into `tests/fixtures/`.

---

## 3. Design decisions

### 3.1 Module contract

```python
class Track3PModule(_SolverModule):
    type = 'track3p'
    _wrapper = Track3P
    _label = 'Track3P'
    _artifact = TRACK3P_PARTICLES
    _input_artifact = EM_SOLUTION        # new hook, see 3.2
    _results_file = 'track3p.log'
```

`requires {em_solution}` / `provides {track3p_particles}` — exactly what
`modules.py:12-15` and `workflow_graph.py:20-26` already predict. One producer
per artifact, so a workflow has either `track3p` or `track3p_source`, never both.
`'track3p'` joins `_ACE3P_TYPES` (`workflow_graph.py:74`) so dry-run auto-enables
without an ACE3P path, and `MODULE_REGISTRY` (`modules.py:2141`).

Config keys: the `_SolverModule` set (`input`, `tasks`, `cores`, `opts`,
`results_dir`) plus:

- `impacts_format:` — `default` (leave the input alone) | `initials-impacts`
  (inject `OutputImpacts: on` and the `OutputImpactsInfo` container). Opt-in;
  multipacting users never see it. See 3.5 for the validator.
- `field_level:` — optional; which level's `ImpactsInfo_<level>` is *the*
  `track3p_particles` artifact when the scan has several (3.4).

### 3.2 Base-class hook instead of a Track3P special case

`_SolverModule.run` (`modules.py:563`) hardcodes `if MESH not in ctx.artifacts`
and builds the solver with no input-tree edits. Two small generalisations:

- `_input_artifact = MESH` class attribute; the check reads it. Omega3P/S3P/T3P
  are unchanged. Track3P sets `EM_SOLUTION`.
- `_prepare_solver(self, ctx, solver)` hook, no-op in the base, called after
  `solver.set_value(ctx.inputs.ace3p)`. Track3P's implementation:
  - `Domain.FieldDir` ← `./<ctx.job_names[EM_SOLUTION]>` via `Section.set_leaf`,
    the same resolution `AcdtoolModule._resolve_jobname` (`modules.py:1430`)
    uses. Only set when the input has no `FieldDir` of its own **or** it points
    at a directory that does not exist in the workdir; an explicit, existing
    `FieldDir` is respected (Lixin's inputs symlink `omega3p_results` by hand).
  - When the producer was S3P, `Domain.FrequencyScanID` must exist; if absent,
    raise naming the S3P `FrequencyScan` grid so the user picks one.
  - `impacts_format: initials-impacts` → `set_leaf('OutputImpacts', 'on')` and
    add the `OutputImpactsInfo` container with `Type: Initials-Impacts`. Needs a
    `Section.set_container`/`ensure_section` helper if `set_leaf` cannot create
    a block — check `ace3p.py:46` first; T3P's monitor injection may already do
    this.

Dry-run: the base already records `ctx.job_names[_artifact]` from
`results_dir or default_job_name`; Track3P's `_dry_run_axis` returns the field
levels parsed from the input file (`FieldScales`), as `T3PModule._dry_run_axis`
(`modules.py:1180`) does for monitors.

### 3.3 Wrapper corrections (`ace3p.py:1327-1340`)

- `output_file = 'track3p.log'` (the `Done!`-terminated log), not `track3p.out`.
- `Track3P.output_parser()` — currently the base no-op. Reads, cheaply:
  - `track3p.log`: banner (source date/tag/compile date), `number of all
    emitting faces`, `Total Emitted Particles`, `Survived particles`, walltime,
    and requires `Done!` (a log without it → raise: the run did not finish).
  - `OUTPUT/enhancementCounter`, `OUTPUT/resonantparticles`,
    `OUTPUT/faradaycup_*` via `parse_column_file` (already handles bare
    headers; `en` is its documented example).
  - the *list* of `ImpactsInfo_<level>` / `LostParticles_<level>` paths and
    their levels. **Not** their contents: Lixin's are 137 MB. Row count and
    max impact energy are computed lazily on first `extract` and cached.
  - `InputParameters` → the `FieldScales` block, to know the declared levels
    even when a level produced no impacts file.
- `output_data` keys: `FieldLevel` (sorted array, from file names ∪
  `fieldlevel` columns ∪ FieldScales), the three tables, the log scalars, and
  `impacts_files: {level: path}`.

### 3.4 Artifact and job-name contract for consumers

Two consumers exist today and they want different things:

- `ParticlesModule.run` (`modules.py:1806`, now `FieldEmissionModule`) treats `ctx.artifacts[TRACK3P_PARTICLES]`
  as **a file path** and stages it.
- `acdtool postprocess track3p` (`acdtool.py:252`) wants
  `ctx.job_names[TRACK3P_PARTICLES]` = the results directory name.

Contract (**revised 2026-10-01; implemented 2026-10-05**): the module always sets
`ctx.job_names[TRACK3P_PARTICLES] = solver.job_name()` and always sets the
artifact to the **results directory** — the Phase 1 behaviour, under run and
dry-run alike. The earlier single-file rule is dropped: `track3p_source` always
provides a file, so the consumer must handle both shapes regardless, and a value
whose *type* depends on how many levels a run produced is a second code path for
nothing. All resolution lives in `FieldEmissionModule` (Phase 3 step 3): a file is
used as today; a directory holds exactly one `ImpactsInfo_*` → use it; several →
raise listing the levels and the two ways to choose (`field_level:` on
`track3p`, or a single-level `FieldScales`); none → raise. A `field_level:` whose
dump is missing raises naming the levels that do have dumps — it never falls
through to another level. `Track3PSourceModule` is unchanged.

**Which of the two shapes it is follows from the producer** (Phase 4 step 0b):
the module in `ctx.modules` providing `TRACK3P_PARTICLES` is asked, the way
`_emitted_note` already asks it. The filesystem cannot answer reliably — `verify`
runs *before* staging, so a dump file that is not there yet looks like neither —
and dispatching on it is how an `isfile` variant briefly broke `verify` (job
39931202) and how a deleted results directory was silently misread as an
unstaged file. `os.path.isdir` remains the documented fallback for a context
built from artifacts alone (unit tests, a direct driver), which is what the
original regression pin asserts.

### 3.5 Build-time validation

In `_resolve_order` (where `WorkflowValidationError`s already fire for
unroutable specs and missing producers), add: if a `field_emission` module is
downstream of `track3p` and the Track3P input file, after the module's own
injection, has no `OutputImpactsInfo` container with `Type: Initials-Impacts`,
raise naming the exact two lines to add or the `impacts_format:
initials-impacts` key. This is the "fail at build, not after a 45-minute
solve" rule the plan inherits from `xopt_config_validation_plan.md`.

**Widened 2026-10-01** to the `track3p_source` head as well: read the first line
of the supplied dump and reject the default layout (`InitialID ImpactNum …
NumElectrons volID`), which `Particles.load` turns into silent garbage (Phase 0
pinned this). One `readline()`, and the higher-value check of the two — every
shipped Geant4 example enters through `track3p_source`. Since the chain example
cannot be frozen (Phase 3 step 5), this validator is the chain's only CI
coverage and should be thorough.

### 3.6 Fowler–Nordheim reconciliation (`particles.py` vs `convert_track3p.py`)

The two disagree and are not interchangeable; β inferred with one is not
comparable with the other. At β·E ≈ 2×10⁹ V/m they differ by ~2 orders of
magnitude in weight.

| | `particles.py` today | Lixin `convert_track3p.py` |
|---|---|---|
| J(βE) | Wang–Loew: `1.54e-6·10^(4.52/√φ)/φ · (βE)² · exp(−6.53e9 φ^1.5/βE)` | plain FN: `1.541434e-6/φ · (βE)² · exp(−6.830890e9 φ^1.5/βE)` |
| time factor | user `dt` (example: 1e-10 s) | `1/f`, f = 1.2999 GHz (7.69e-10 s) |
| weight | `round(J·A·dt/e)`, drop 0 | float `J·A/(f·e)`, drop < 1 |
| filters | `ImpactOrder ∈ …` and `ImpactFaceID ∈ …` (required) | `ImpactEnergy ≥ 1 keV` (default), order/face optional |
| β | per-z-bin vector (`num_bins`, `bin_edges`) | scalar |
| Geant4 col 4 / col 10 | `ImpactPhaseinRFcycle` / `ImpactFaceID` | 0 / 0 |
| direction | raw momentum | unit vector |

Geant4's `SetParticleMomentumDirection` normalises, and the reader ignores
columns 4 and 10 (`generator.cc:39-45`), so the last two rows are cosmetic.
The first four are not.

**Decision (recommended, David's call):** standardise on Lixin's constants and
`1/f` so the repo reproduces the 22 shipped particle files bit-for-bit, and keep
the per-bin β vector as the repo's addition. Concretely: `fn_model: 'fn' | 'wang-loew'`
(default `fn`), `frequency:` replaces `dt` (with `dt` accepted as an alias and
a deprecation warning), `min_energy_ev:` (default 0 to keep old behaviour;
example sets 1000), `impact_order`/`impact_face_id` become optional (absent =
no filter), weights stay float, rows with weight < 1 dropped. Acceptance test:
a 1 000-row excerpt of `data/track3p/c3_16MV/ImpactsInfo_1.6e+07` through the
new code with β = 120, φ = 4.2 equals the same rows of
`data/particles/c3_16MV_beta120.data` to 1e-6 relative.

### 3.7 Geant4 side for the polycone app

`Geant4` (`geant4.py`) is a generic `key = value` wrapper, so `passes`, `seed`,
`cavity_stl`, `detectors` pass through already. What is missing:

- **Seeds.** Every evaluation in a sweep must get a distinct `seed` or split
  parts reproduce identical histories (Lixin's bug #2). `Geant4Module` gains
  `seed: auto` (evaluation index + base) | integer | input-variable name.
  **Implemented 2026-10-06 as `geant4_seed`, and `auto` is the *config hash*,
  not an evaluation index** — no point index reaches `Workflow` by design and an
  index is not stable across a re-ordered or resumed sweep, while the hash is
  both distinct and reproducible per point. The third form needed no code: a
  swept `input_parameters: {geant4: {seed: ...}}` already reaches `set_value`,
  and wins over the module key.
- **Detector outputs.** `extract` quantities `detector_edep_MeV` and
  `detector_gammas` from `<prefix>_detector_dose.csv`, `at: {detector: n}`
  narrowing, or the 8-vector without `at:`; `detector` becomes the module's
  index axis when a detector quantity is declared (the `ModeID`/`Frequency`
  rule). `<prefix>_detector_gamma_spectrum.csv` rides on `field()`.
  **Implemented 2026-10-06.** Two corrections: the axis is reported whenever the
  run *wrote* a detector CSV, not when a detector quantity was declared — a
  module's index is a property of its output, and `modes._table_index` already
  drops an axis no output rides on — and the output filenames had to learn the
  `output_prefix` derivation first, since the polycone app names no
  `output_dose` and so resolved to nothing at all.
- Examples `geant4_dose_single`, `geant4_track3p_beta`, `geant4_beta_surrogate`
  move to the polycone app path and the `nb_wall_profile.dat` geometry key.
  `geant4_app_path` stays a YAML override, so nothing hardcodes Lixin's path in `src/`.
  **Deferred 2026-10-06** — the app ignores `solid_stl` and its geometry is the
  cryomodule, so neither the 7cell STLs nor the shared dump transfer; see the
  Phase 4 status note and §6.

---

## 4. Phases

Each phase is independently landable, changes no baseline unless it says so,
and ends with the full test suite green (run on milano via sbatch — see the
S3DF rules in memory; never on iana).

### Phase 0 — fixtures and characterization tests (no `src/` changes)

1. `tests/fixtures/track3p/` with `SOURCES.md` in the style of
   `tests/fixtures/acdtool/SOURCES.md`, verbatim copies from the probe runs
   (§2.4) and Lixin's package:
   - `pillbox_scan/`: `track3p.log`, `track3p.warn`, `InputParameters`,
     `OUTPUT/enhancementCounter`, `OUTPUT/resonantparticles`,
     `ImpactsInfo_2.3e+07` (first 200 rows, truncation noted),
     `LostParticles_2.3e+07` (first 50 rows).
   - `pillbox_initials_impacts/ImpactsInfo_2.3e+07` (first 200 rows) — the
     `#`-headed 17-column layout from the group binary.
   - `faradaycup_1` header-only file from probe3/w4 (bodies are empty).
   - `lcls_c3_16MV/ImpactsInfo_1.6e+07` first 1 000 rows **and** the matching
     first rows of `c3_16MV_beta120.data` (matched by position after the
     converter's filters — record the exact selection procedure in SOURCES.md).
   - The three Track3P inputs: `Pillbox.track3p`, `Pillbox2.3MV.track3p`, and
     a copy of Lixin's generated `track3p_input.track3p` for C3/16 MV/m
     (`make_track3p_input.sh 3 16 /tmp/x`), which exercises `Domain.Mode`,
     the Emitter extras and the `OutputImpactsInfo` container.
2. `tests/test_track3p_fixtures.py` pins: both `ImpactsInfo` headers (commented
   vs not, `NumElectrons` vs `InitialNormalField`), `enhancementCounter` and
   `resonantparticles` column names via `parse_column_file`, `track3p.log`
   terminator and scalar lines, `parse_ace3p` round-trips Lixin's input
   (containers `Domain.Mode`, `OutputImpactsInfo`) without loss, and — the
   defect — `particles.Particles.load()` on the *default* layout produces
   garbage (header eaten as a row): this pins the reason the format is opt-in.
3. Refresh `references/README.md` with the one-paragraph note that
   `track3p-commands.pdf` is inputs-only and lists none of `OutputImpactsInfo`,
   `LostParticles`, `PtrackMonitor`, `Domain.Mode`.

**Phase 0 status (2026-09-14): DONE.** 27 files, 353 KB, in
`tests/fixtures/track3p/`; 29 tests + 2 strict xfails in
`tests/test_track3p_fixtures.py`. Deviations and findings, all recorded in
`SOURCES.md`:

- `resonantparticles` is a per-level selection (first 40 rows of each level),
  not a prefix: the original is 204 KB.
- The LCLS excerpt is not a contiguous 1 000-row prefix. The first 50 080 rows
  of Lixin's dump are all `ImpactOrder 0` emission points, so the excerpt is
  the first 20 rows plus the first 1 000 `ImpactOrder 1` rows; the matched
  particle file is its 35 survivors, verified to 3e-7 relative.
- Two directories the plan did not list: `pillbox_fieldemission/` (the Type-7
  run with `Total Emitted Particles = 0` *after* `Done!`, three header-only
  Faraday-cup files, a malformed `InputParameters` echo that nests `FaradayCup`
  inside `EnhancementCounter`) and the LCLS `track3p.log` + `SurvivedParticles`
  (the `Survived particles` line and a file no Pillbox run writes).
- **Parser defect found, deferred to Phase 1:** `parse_ace3p` misreads one-line
  blocks (`Key: { A: x  B: y }`) — the value runs to end of line, swallowing
  sibling keys and the closing brace. Both `OutputImpactsInfo: { Type:
  Initials-Impacts }` and Lixin's whole generated input hit it. Two strict
  xfails pin it; Phase 1 must fix the tokenizer before `_prepare_solver` can
  inject anything into such a file.
- `parse_column_file` returns `{}` for a header-only table (no rows means no
  width to match the header against); Phase 1 needs the names.
- No Track3P log carries a wall-time line; `walltime_s` is dropped from §3.3.
  `Survived particles` appears only in the LCLS log. Lixin's c3-solo run used
  the same 08-28 build as the probes, not the 08-31 rebuild.
- In the secondary-emission `Initials-Impacts` dump the two field-emission
  columns hold uninitialized memory (denormals, negatives), not just 0 and a
  placeholder.

### Phase 1 — wrapper + standalone module (use case A)

1. `ace3p.py`: `Track3P.output_file = 'track3p.log'`; implement
   `Track3P.output_parser` per §3.3; `parse_track3p_log(text)` helper;
   `field_levels_from_input(tree)` helper reading `FieldScales`.
2. `modules.py`: `_input_artifact` and `_prepare_solver` hooks on
   `_SolverModule` (§3.2); `Track3PModule` with `extract`, `field_index`,
   `field`, `_dry_run_axis`, `verify` (log present **and** ends `Done!` — a
   killed run leaves a log). Registry entry. `workflow_graph._ACE3P_TYPES`.
3. `extract(spec)` quantities, S3P-style mapping form
   `{quantity, at: {field_level: x}}` (off-grid `at:` raises naming the grid):
   - per level, from `enhancementCounter`: `max_enhancement`,
     `mean_enhancement`, `total_impacts`, `resonant_count` (rows per level);
   - per level, from `resonantparticles`: `resonant_particles`,
     `max_resonant_energy`;
   - per level, lazily from `ImpactsInfo_<level>`: `impact_count`,
     `max_impact_energy` (both layouts; column chosen by header);
   - per boundary, from `faradaycup_<id>`: `captured_electrons`
     (`sum(NumElectrons)`), `at: {boundary: id}` required;
   - scalars (repeat down the level rows): `total_emitted`, `emitting_faces`,
     `survived`, `walltime_s`;
   - derived: `mp_onset_level` = lowest level with `max_enhancement ≥ threshold`
     (`at: {threshold: t}`, default 1.0), NaN if none — the optimizer objective.
4. `field_index` → `('FieldLevel', levels)`; dry-run → levels from the input,
   or `[0.0]` if unparsable. `field()` → the three tables + log scalars.
5. Tests (`tests/test_modules.py`, `tests/test_workflow_graph.py`,
   `tests/test_ace3p.py`): fake-solver runs off the Phase 0 fixtures; FieldDir
   injection off Omega3P and S3P job names; explicit `FieldDir` respected;
   S3P producer without `FrequencyScanID` raises; dry-run table shape;
   resume (`skip_execution`) re-parses; `[mesh_source, omega3p, track3p]` and
   `[mesh_source, omega3p, track3p, acdtool]` validate; `[track3p_source,
   track3p]` is rejected (two producers).
6. Example `examples/track3p_multipacting/` — `mesh_source -> omega3p -> track3p`,
   `mode: single`, the CW23 Pillbox mesh (2 MB, into `examples/assets/`),
   `Pillbox.track3p` 3-level scan, outputs `max_enhancement`, `total_impacts`,
   `resonant_particles`, `mp_onset_level`; long-format table, one row per
   level. README in the `s3p_sweep` style. Validated on S3DF (one job) and
   frozen as a baseline. A `parameter_sweep` variant over an ACE3P leaf
   (`ace3p:Emitter.z1` or `ace3p:Domain.InitialEnergy`) is a cheap second
   example if time allows; a Cubit-driven geometry sweep needs a journal whose
   boundary IDs match a Track3P input and is deferred.

**Phase 1 status (2026-09-14): DONE.** `Track3P` wrapper + readers in
`ace3p.py`, `Track3PModule` in `modules.py`, routing and `_ACE3P_TYPES` in
`workflow_graph.py`, `examples/track3p_multipacting` validated on milano (job
38240889, 28 s; recorded in `plans/s3df_example_validation_plan.md`) and frozen
as a dry-run baseline. Deviations from the text above:

- **Tokenizer fixed first** (the Phase-0 defect): a value now stops at `}` or at
  the next identifier-like `Key:` on its line. A key *with spaces* is still only
  recognised at the start of a line.
- **No `FrequencyScanID` check.** No CW23 input carries such a key and the
  build's `InputParameters` echo lists none; both CW23 S3P-driven cases run S3P
  at a single frequency. The module *warns* when the S3P producer's
  `FrequencyScan` has more than one point instead of demanding an undocumented
  key.
- **`walltime_s` dropped** (no log has it); `survived` kept (LCLS log only).
- **`[…, track3p, acdtool]` is not asserted**: `postprocess track3p` is
  `wired=False` in `acdtool.COMMANDS` (KVC-dialect input), so that chain fails
  validation today regardless of this module. Wiring it is a separate change.
- **Index precedence.** `Workflow.field_index` took the first module in DAG order,
  which after a real Omega3P run is `ModeID`, not `FieldLevel`. Modules may now
  set `index_precedence`; Track3P sets 1. The s3p→acdtool rule is unchanged.
- **`files:` on every solver module** — the Pillbox input names `copper.dat`
  (SEY table), which Track3P reads from its cwd and no artifact supplies.
- **Artifact = results directory** (§3.4 originally deferred a single-file rule to Phase 3; dropped 2026-10-01 — the directory is the contract).
  `impacts_format:` / `field_level:` keys are not read yet.
- Extra per-level quantity `lost_count`; `resonant_count` is rows of the
  enhancement counter and `resonant_particles` distinct IDs of
  `resonantparticles` (each resonant particle is listed at two positions).
- `docs/yaml_reference.md` got the module row and a short `track3p` section now
  rather than in Phase 5, since the old row said no in-pipeline tracker exists.
- The `parameter_sweep` variant over an ACE3P leaf was not added.

### Phase 2 — Fowler–Nordheim reconciliation (`particles.py`)

1. Implement §3.6: `fn_model`, `frequency` (+ `dt` alias with warning),
   `min_energy_ev`, optional filters, float weights.
2. Acceptance test against the `lcls_c3_16MV` fixture pair (1e-6 relative).
3. Keep `_write_output_track3p` (the 19-column "modified dump") working.
4. `examples/geant4_track3p_beta`: `work_function: 4.2`, `frequency: 1.2999e9`,
   β range 100–150, `fn_model: fn`. **Baseline `geant4_track3p_beta` moves**;
   refreeze deliberately and say why in `tests/baseline/README.md`.
5. Decide (with David; default for an unattended run: keep) whether `examples/assets/sample_track3p_particles.txt`
   (316 k rows, provenance: an early LCLS run in this format) stays or is
   replaced by the 1 000-row C3 excerpt; the example's runtime prefers small.

**Phase 2 status (2026-10-01): DONE.** `fn_model`, `frequency` (+ `dt` alias),
`min_energy_ev` and optional filters in `particles.py`; 10 tests in
`tests/test_particles.py`; `geant4_track3p_beta` refrozen; full suite green on
milano (job 39639568, 5m44s, 752 passed / 2 skipped). Deviations from the text
above:

- **The acceptance test is tighter than 1e-6 but on 8 of 10 columns.** Position,
  energy, weight and unit direction agree to `rtol=1e-6`, which is the shipped
  file's own `%.6e`. Columns 4 and 10 are excluded *by assertion*: the reference
  converter writes 0 in both and the repo writes the impact phase and the face
  ID, which the Geant4 reader ignores. The test pins that difference rather than
  papering over it.
- **`wang-loew` is bit-exact legacy, not just the old constants.** Reproducing
  the frozen `track3p_particle_weight` digest needed the old *arithmetic* too:
  direct (not log-space) evaluation, the old `Q_E = 1.60217663e-19` (kept as
  `_LEGACY_Q_E`; the 2.5e-9 relative difference from the exact charge flips a
  rounding), and `np.round` to whole electrons. `fn` keeps real weights.
- **Zero field gives `exp(-500)`, not 0.** The log-space clip the reference
  converter uses floors at ≈7e-218 rather than returning 0. Faithful to the
  reference and far below the one-electron cut either way; the docstring and
  test say so instead of claiming 0.
- **A β range is a property of the dump, not of the model — and §3.6's "β range
  100–150" does not transfer to the shipped asset.** `sample_track3p_particles.txt`
  has `InitialNormalField` averaging 3.7e7 V/m, about 4× Lixin's cryomodule, so
  the `β·E` the LCLS-II study reaches at β = 120 is reached here near β = 30. The
  first cut of this phase did move `geant4_track3p_beta` to the study's 100–150
  and that **silently broke the example**: above β ≈ 55 every macroparticle clears
  the one-electron cut, so all five sweep points wrote an identical 144 732 rows
  and the sweep demonstrated nothing (the digests were briefly frozen that way).
  The swept range is therefore **kept at 40–60**, where the surviving count climbs
  2055 → 60 736 → 124 428 → 144 732 → 144 732 and the weight spans six decades.
  Only the FN *settings* moved. Both example READMEs, the
  `docs/parameter_sweep.md` section and the baseline provenance say why.
- **`geant4_dose_single` and the `geant4_beta_surrogate` DOE also moved.**
  `dose_single` took the same `fn` settings with a per-bin β vector
  `[44,46,48,50,50,48,46,44]`, sized the same way — above ~55 the per-bin
  structure washes out, so 44–50 is where the bins still differ (98 380 primaries
  of 144 732). It is in `not_frozen.json`, so no baseline moved.
  `beta_surrogate` pins `fn_model: 'wang-loew'` instead: its 8-D β bounds
  (40–60) were chosen against the legacy weighting, and re-deriving them is the
  shelved surrogate project's business, not this phase's.
- **Both Geant4 examples need one real S3DF run** before their dose numbers are
  trustworthy again; logged as an open item in
  `plans/s3df_example_validation_plan.md`. The weighting itself is verified
  against the reference converter, so this is a sizing check, not a correctness
  one.
- **Docs updated now, not in Phase 5:** `docs/yaml_reference.md`
  `particle_parameters` (the `dt` row replaced by `frequency`/`fn_model`/
  `min_energy_ev`, filters no longer "required", the Geant4 column table's unit
  direction / real weight / sub-electron drop) and the `docs/parameter_sweep.md`
  Track3P-weighting section, whose two YAML blocks had gone stale against their
  examples.
- **Asset decision: keep** `sample_track3p_particles.txt` (David, 2026-10-01).
  Its field-emission columns are real on the 144 732 rows the examples retain
  (`InitialNormalField` 3.2e7–4.4e7 V/m, areas 9e-8–1.3e-6 m²), so the chain
  still demonstrates a realistic particle count; the 1 000-row excerpt would
  leave ~35 primaries and meaningless dose. Noted for a future cleanup: the file
  has one `-nan` row and two rows with absurd magnitudes, all outside every
  example's filter.

### Phase 3 — `track3p -> particles` chain (use case B, in-pipeline)

**Reviewed 2026-10-01** (Opus draft of the open questions, second opinion,
David's decisions). The order below is deliberate: steps 1 and 2 need none of
the module work and de-risk everything after them; step 7 is mechanical and
goes last so it is one commit on top of a working chain. Run the full suite on
milano after each landable step, as every phase does.

1. **Output-name collision fix in `particles.py` — land first, alone.**
   `Particles.output_file` defaults to `particle_file.replace('.txt',
   '_modified.txt')` (`particles.py:103`). A Track3P dump named
   `ImpactsInfo_2.3e+07` contains no `.txt`, so the default output name
   **equals the input name** and `write_output` overwrites the staged dump —
   straight through the symlink to the original under `stage_mode: symlink`,
   which is exactly the read-only invariant `_stage_file` documents. It has
   never fired only because every shipped example sets `output:`; Phase 3 is the
   first time a dump named `ImpactsInfo_*` reaches this code. Fix both ways:
   derive the default with `os.path.splitext` (`ImpactsInfo_2.3e+07` →
   `ImpactsInfo_2.3e+07_modified`; `.txt` names unchanged, so no baseline
   moves) **and** refuse at construction when the resolved output path equals
   the input path, naming the `output:` key. `ParticlesModule.verify` mirrors
   the same default. Tests for both. Independent of everything else in the
   phase.

2. **One probe job before any example (milano, < 2 min).** The plan's chain
   example assumes `Emitter Type: 7, N: 1` on the Pillbox populates
   `InitialNormalField`/`InitialFaceArea`; nothing has ever shown that. All
   three Type-7 probes used `N: 100` and emitted 0 particles, and the only
   `Initials-Impacts` fixture is a secondary-emission run whose two
   field-emission columns hold uninitialized memory. Run the Pillbox with
   `Emitter Type: 7  N: 1  WorkFunction: 4.2  Beta: 50`, `OutputImpacts: on`,
   `OutputImpactsInfo: { Type: Initials-Impacts }`, one level (`ScanToken: 0
   Scale: 23e6`), under the §2.4 probe conventions. **In the same job**, the
   hybrid-launch check for step 6: `tasks: 2, cores: 8, opts: '--overlap
   --cpu-bind=none'` with `OMP_NUM_THREADS=8` exported in the batch script, and
   confirm from `track3p.log`'s MPI/OpenMP banner that both ranks saw 8
   threads. Outcomes:
   - FE columns populated and sane (normal field ~1e7 V/m, areas ~1e-7–1e-6
     m²): copy the first 200 rows, `track3p.log` and `InputParameters` to
     `tests/fixtures/track3p/pillbox_fieldemission_n1/`, record in
     `SOURCES.md`, and size the example's β **from this dump's own
     `InitialNormalField`** — the Phase 2 lesson (a β range belongs to its
     dump), not the LCLS study's 100–150. Note the row count: Lixin's 7852
     emitting faces at `N: 1` gave 351 k macroparticles; the Pillbox emitter
     box has far fewer faces, but check before truncating.
   - Zero particles or garbage columns: the example's emitter (bounding box,
     `BoundaryID`, `Material Type: Primary`) needs rethinking, and the phase
     stops here until it is — no example is written against an unknown dump.

   **Step 2 result (2026-10-01): PASSED, but not as predicted — `N` was never
   the blocker; the emitter bounding box was.** Two milano jobs:

   - **Job 39649793** — the probe exactly as specified above (`N: 1`, the
     probe3/w4 box, `Initials-Impacts`, one level). `number of all emitting
     faces = 14`, `Done!`, **`Total Emitted Particles = 0`**: the gate's bad
     outcome. The hybrid-launch check in the same job **passed** — both ranks
     reported `OMP_NUM_THREADS=8 OMP_PROC_BIND=spread OMP_PLACES=cores` and ran
     9 LWPs each (8 OpenMP + main) under `srun -n 2 -c 8 --overlap
     --cpu-bind=none`, so step 6 needs only the three exports in the batch
     script, as written. No Track3P log line reports threads, so the team size
     was sampled from `ps -o nlwp` rather than read from the log.
   - **Why**, from Lixin's source: with `N <= 1.0`,
     `EMT_FieldEmission.C:200-202` sets `numParticles = 1` *unconditionally* —
     the `this_N < 0.5 * m_N` cut that suppressed the `N: 100` run is in the
     `else` branch, so `N: 1` cannot be what stops emission. What stops it is
     `J == 0.0`, which `continue`s before a particle exists (line 184–186).
     `FowlerLawRF` (line 488) is `C1·(βE)^2.5·exp(−C2/E)`, and at β = 50,
     φ = 4.2 the exponent underflows `exp()` to *exactly* zero below
     E ≈ 1.5e6 V/m. The probe's `x0..z1` box selects 14 of the pillbox's 9 100
     surface faces, all on the flat end-wall annulus where the 23 MV/m mode's
     normal field is below that floor.
   - **Job 39651022** — three diagnostics separating the causes: same box at
     β = 1000 emits 522 particles (so the box is legal and the mechanism is
     field magnitude); **no box at all, β = 50, emits 23 984 particles from all
     6 558 faces of boundary 6** (`d2_wholeb6`, the fixture); both at once emits
     242 958 / 1.78 M rows.
   - **The columns are real.** On the 23 984 `ImpactOrder 1` rows of
     `d2_wholeb6`, `InitialNormalField` is 3.14e7–4.53e7 V/m and
     `InitialFaceArea` 3.7e-6–1.2e-5 m², every value finite and positive —
     the first Pillbox dump for which that is true. (Rows of `ImpactOrder >= 2`
     are secondary electrons and their two FE columns are uninitialized memory,
     as in the Phase 0 dump; the chain filters to order 1 anyway.)
   - **Fixture:** `tests/fixtures/track3p/pillbox_fieldemission_n1/` (header +
     first 20 order-0 rows + first 200 order-1 rows, the same selection shape
     Phase 0 used for the LCLS excerpt and for the same reason — the first 636
     rows of the original are all emission points), plus `track3p.log`,
     `InputParameters` and `inputs/Pillbox-fieldemission-n1.track3p`. Recorded
     in `SOURCES.md`, which also corrects the old claim that `N: 100` was the
     reason `pillbox_fieldemission/` emitted nothing. Three tests pin the
     finding, the columns and the β sizing.
   - **β for the example, sized from this dump** (not the LCLS 100–150): at
     f = 1.3138172 GHz the one-electron cut bites between β = 35 and β = 50 —
     full-dump kept counts are 0 at β = 30, 51 at 35, 9 191 at 40, 23 984 (all)
     from 50 up. **The example sweeps below 50**; β ≥ 100 would saturate every
     point, the exact failure Phase 2 hit on `geant4_track3p_beta`.
   - **Deviation to carry into step 5:** the example's emitter is the
     *unboxed* boundary-6 emitter, not the plan's bounding box, and
     `impact_face_id` is 6 (the only face ID on the order-1 rows).

3. **Artifact contract (§3.4 as revised).** `Track3PModule` keeps the results
   directory as the artifact; the single-file rule is gone. In
   `ParticlesModule.run`:
   - artifact is a file (`track3p_source`) → as today;
   - artifact is a directory → `ImpactsInfo_*` under
     `ctx.job_names[TRACK3P_PARTICLES]` inside it; exactly one → use it;
     several → raise listing the levels and the two ways to choose; none →
     raise (no dump: `OutputImpacts` off, or nothing emitted — quote the log's
     `TotalEmitted` when the run wrote one);
   - `field_level:` on `track3p` (new key) names the level. How it reaches the
     consumer is the implementer's call — recording it next to the job name in
     `ctx` is the least invasive; say which in the status note. A named level
     whose dump is missing **raises** naming the levels that have one.
   - Hand `Particles` the **relative subpath** (`track3p_results/ImpactsInfo_x`,
     it joins onto `workdir`) instead of `_stage_file`: the paths differ, so
     staging would copy the dump to `workdir/ImpactsInfo_x` — a same-workdir
     copy of a 137 MB file at cryomodule scale. With step 1 the derived default
     output then lands inside the solver's results directory; the example sets
     `output: particles.data` regardless, and `verify` follows the same rule.
   - `impacts_format: initials-impacts` injection per §3.2 (`set_input_leaf`
     already creates missing containers). `Track3PSourceModule` unchanged.

4. **Build-time validation (§3.5 as widened).** Both heads: the `track3p`
   input-tree check (container present after the module's injection) and the
   `track3p_source` first-line sniff (`#InitialID ImpactOrder …
   InitialNormalField`, else raise naming the default layout and how to
   regenerate the dump). Skip silently when the source file does not exist yet:
   today no module input file is opened at build time and `files:` paths are
   checked only at run time, so a missing file is run time's error, not this
   validator's. Test: the chain validates with the container, is rejected
   without it, and a default-layout `track3p_source` is rejected with the
   Phase 0 fixture `pillbox_scan/ImpactsInfo_2.3e+07`.

5. **Example `examples/track3p_geant4_chain/` — new directory, not frozen.**
   `mesh_source -> omega3p -> track3p -> particles -> geant4` on the Pillbox,
   the step 2 emitter, `impacts_format: initials-impacts`, `mode: single`, β
   and `impact_face_id` sized from step 2. A new directory rather than a second
   head on `geant4_track3p_beta`: that example is the `track3p_source` pattern
   and its README now explains a β range that belongs to its dump; grafting a
   second head on it muddies both. **Not frozen** (`not_frozen.json`; Option B,
   David 2026-10-01): `ParticlesModule.run` has no dry-run branch — it always
   runs because the weighting is pure Python — and under dry-run the `track3p`
   artifact is a workdir with no `ImpactsInfo_*`, so the particles step would
   raise and `geant4` would have no source. A placeholder-artifact mechanism so
   two modules with nothing to compute can pass a dry run is not worth
   building; CI instead asserts that the chain **validates and orders**
   (`test_workflow_graph.py`) plus the step 4 rejections. The example's value
   is its one real S3DF run, recorded in `plans/s3df_example_validation_plan.md`.
   README: for cryomodule meshes this head costs ~50 node-minutes per
   evaluation and `track3p_source` over pre-run dumps is the intended pattern.

6. **Hybrid launch — batch script, no `env:` key.** `solver_command` builds
   `srun -n <tasks> -c <cores> <opts> …` and `run_logged` passes no
   environment, so `tasks: 2, cores: 60, opts: '--overlap --cpu-bind=none'`
   reproduces Lixin's launcher and only the OpenMP exports
   (`OMP_NUM_THREADS=60 OMP_PROC_BIND=spread OMP_PLACES=cores`) are missing —
   put them in the example batch script, where every solver in the job inherits
   them. (The `ace3p_opts.startswith('--cpu-bind')` drop at `ace3p.py:330` does
   not bite: the string starts with `--overlap`.) Document only what step 2
   verified. An `env:` mapping waits for a workflow that needs two different
   thread counts in one job.

7. **Rename the `particles` module → `field_emission` (DECIDED, David
   2026-10-01).** The module's job is the Fowler–Nordheim field-emission
   weighting of a Track3P dump, plus its filters, z-binning and Geant4
   formatting; `particles` says none of that and collides in the reader's mind
   with `particle_source` (the Geant4-input artifact and module),
   `particle_output`, `geant4_particle_cmd` and the `particles:` *input bucket*.
   Underscore names are what every other type key uses (`mesh_source`,
   `track3p_source`). **Hard rename, no alias**: David is the only user of this
   functionality, so there are no third-party YAMLs or workdirs to keep
   working, and a deprecation shim would be code that protects nobody. Scope —
   **registry key and class only**:
   - `FieldEmissionModule`, `type = 'field_emission'`; the `'particles'` registry
     entry is removed, so an old YAML fails at build with `build_module`'s
     existing "Unknown module type … Known types: […]" message, which lists the
     new key. The two `_infer_output_module` branches return the new key; the
     §3.5 validator and every error message name it. `_route_output` needs no
     alias handling since there is nothing to alias.
   - **Not renamed:** the `particles:` input bucket (`inputs.py`,
     `beta_input`/`beta_inputs`, every `input_parameters:` block — a namespace
     for β variables, not the module; David 2026-10-01); the `particle_source`
     artifact and module; the `particles.py` file and `Particles` class (the
     YAML surface is what the rename is for; the file can follow in a later
     cleanup if it ever bothers anyone).
   - The four shipped YAMLs (`geant4_dose_single`, `geant4_track3p_beta`,
     `geant4_beta_surrogate`, `track3p_particle_weight`), the five docs pages
     and the two baseline `manifest.json` prose fields that say `particles`
     move to the new key. Baseline **data** does not move (same code, same
     outputs). One test pins that `module: particles` is rejected naming
     `field_emission`.
   - A module's default `name` is its `type`, so the log file becomes
     `field_emission.log` and a workdir written before the rename will not
     resume. Acceptable (single user, no shipped baseline reads the log); one
     line in the release notes.

**Phase 3 status (2026-10-05): DONE.** Four commits, each leaving the suite
green, in the order the plan fixed: the step 1 fix alone (`f27abcf`), the step 2
probe (`538aa8b`), steps 3–6 (`fc73f79`), the step 7 rename alone (this commit).
All four closed decisions were implemented as decided; none was re-opened.

Verified on milano (one job at a time, from scratch):

| Job | What | Result |
|---|---|---|
| 39647859 | full suite, step 1 | 756 passed / 2 skipped, 25m06s |
| 39649793 | step 2 probe + the step 6 launch check | **0 particles emitted** (the gate) — but the launch check passed |
| 39651022 | three step 2 diagnostics | 522 / **23 984** / 242 958 particles |
| 39926642 | full suite, step 2 | 760 passed / 2 skipped, 6m48s |
| 39928306 | `track3p_geant4_chain` real run | COMPLETED, 16m22s — see below |
| 39932673 | full suite, steps 3–6 | 781 passed / 2 skipped, 21m05s |
| 39935147 | full suite, step 7 | **783 passed / 2 skipped, 5m36s** |

Deviations from the text above:

- **Step 2's premise was wrong, and the gate fired.** The plan assumed `N: 100`
  was why `pillbox_fieldemission/` emitted nothing and that `N: 1` would fix
  it. It did not — the `N: 1` probe also reported `Total Emitted Particles = 0`.
  With `N <= 1.0` the build skips the macroparticle threshold entirely, so `N`
  was never the blocker; the **emitter bounding box** was, selecting 14
  low-field faces whose Fowler–Nordheim current underflows to exactly zero.
  Rather than stop, three diagnostics in one job isolated it (the same box at
  β = 1000 *does* emit), and the fixture is the unboxed boundary-6 emitter:
  6 558 faces, 23 984 particles, real field-emission columns. Full detail in the
  step 2 block above. **The example's emitter is therefore unboxed**, which is
  the one place the example departs from §3.5's sketch.
- **β = 45, sized from the probe dump** as decision 3 requires: the
  one-electron cut bites between 35 and 50 here and saturates by 50, so the
  LCLS study's 100–150 would have flatlined the example exactly as it did
  `geant4_track3p_beta` in Phase 2.
- **The chain example stops at `field_emission`, not `geant4`** (David,
  2026-10-05). The dose app needs a copper body *and* a vacuum cavity as STL
  meshes and **no Pillbox geometry exists** — only 7cell STLs ship, and they are
  a different cavity, so the dose would be meaningless. The cavity surface is
  extractable from `Pillbox.ncdf` exactly (6 836 closed triangles, verified);
  the copper body is not, since the mesh is the vacuum volume and a wall
  thickness would have to be invented. Options offered were (a) derive both with
  a stated 3 mm wall, (b) stop at the weighting step, (c) wait for Phase 4's
  polycone app; **(b) chosen** — invent no geometry. The YAML carries the
  `geant4` block commented out with the two filenames it needs. Everything
  Phase 3 built is still exercised by the real run.
- **`field_level:` travels in `ctx.field_levels`**, a new per-artifact side
  table beside `job_names` (the plan left the mechanism to the implementer and
  asked which was chosen). The artifact's *type* therefore never depends on how
  many levels a run produced, which was the point of decision 1.
- **`_level_files` became public `level_files`** so the wrapper's reader and the
  consumer's resolution agree by construction on what a per-level dump is
  called, rather than by two copies of a glob.
- **Step 6 needed no code at all**, as predicted: the existing
  `solver_command` already produces Lixin's launcher shape, and the three
  `OMP_*` exports sit in the example batch script. The step 2 job confirmed both
  ranks saw `OMP_NUM_THREADS=8` and ran 8 OpenMP threads each — sampled from
  `/proc`, since no Track3P log line reports a thread count.
- **Two defects found and fixed in passing**, neither in the plan:
  `.gitignore`'s blanket `*.log` had silently excluded all five
  `tests/fixtures/track3p/*/track3p.log` fixtures since Phase 0 (the suite
  passed only because they sat untracked in the dev checkout; a fresh clone
  failed `test_fixture_inventory`), now fixed with a `!tests/fixtures/**`
  negation mirroring the `!tests/baseline/**` one. And the step 3 work briefly
  regressed `verify` by dispatching the two artifact shapes on `isfile` rather
  than `isdir` — `verify` runs *before* staging, so a recorded path that does
  not exist yet is still a file. The suite caught it; a test now pins it.
- **No baseline data moved** (§7 holds): `not_frozen.json` gained the
  `track3p_geant4_chain` entry and `geant4_track3p_beta/manifest.json` had two
  **prose** fields re-synced for the rename. No digest, table or field changed.
- **Docs updated now rather than in Phase 5**, continuing Phases 1–2's practice:
  `docs/yaml_reference.md` (the `track3p` artifact contract, `impacts_format`
  and `field_level`, the dump-resolution rules, the `output:` default) and
  `docs/parameter_sweep.md`, plus a CHANGELOG entry carrying the breaking-rename
  note the plan asked for.

Deferred, with reasons:

- **Pillbox Geant4 geometry** (above). The decision is a modelling one — what
  wall thickness — not a packaging one. Phase 4 moves the Geant4 examples to the
  polycone app, which takes an R(Z) profile rather than an STL, so the real fix
  may be to write this head against that app instead of generating STLs now.
- **`geant4_dose_single` / `geant4_track3p_beta` re-validation** after Phase 2's
  weighting change is still open in
  `plans/s3df_example_validation_plan.md`; untouched by this phase beyond the
  one renamed line each.
- **The full-suite slowdown** (5m49s → 25m06s, first seen 2026-10-01) is
  intermittent, not monotonic: across this phase's four full runs the same tree
  took 25m06s, 6m48s, 21m05s and 5m36s. It tracks neither the diff nor the test
  count (the slowest run and the fastest are 27 tests apart), which rules the
  Phase 2/3 code out as the cause and points at the node or at thread
  oversubscription. **Resolved 2026-10-06: thread oversubscription.** Pinning
  BLAS/OpenMP to one thread held sdfmilan265 — the 25:13 node — to 7:25 on the
  same node, and the fixture now in `tests/conftest.py` takes the suite to
  5:34. See `plans/pytest_slowdown_experiment_plan.md`.

### Phase 4 — Geant4 module for the polycone app

**Step 0 — Phase 3 follow-ups (land first, one commit, full suite on milano).**
Reviewed 2026-10-05 from the Phase 3 defect list. Two of the four Phase 3
defects need no further code: the output-name collision (step 1) is fixed and
tested, and the step 2 premise (the box, not `N`) is recorded in the plan,
`SOURCES.md` and memory. The other two left a gap each:

- **Fixture inventory must check git, not just the filesystem.**
  `test_fixture_inventory` (`tests/test_track3p_fixtures.py:120`) compares the
  files on disk with `SOURCES.md`, so a fixture that `.gitignore` excludes
  passes in the dev checkout and fails only on a fresh clone — which is how the
  five `track3p.log` fixtures went untracked from Phase 0 to Phase 3. Add one
  assertion: every file under `tests/fixtures/` appears in `git ls-files
  tests/fixtures` (one subprocess; `pytest.skip` when `git` is absent or the
  tree is not a checkout, since an sdist has no index to ask). A `*.log`-style
  rule then fails in the checkout it was written in.
- **`_resolve_dump` should dispatch on the producer, not on the filesystem.**
  `FieldEmissionModule._resolve_dump` (`modules.py:2290`) tells the two
  `track3p_particles` shapes apart with `os.path.isdir`, which is why the
  `isfile` version briefly regressed `verify` (job 39931202, 2026-10-05) and
  why a results directory that has vanished is still misread as an unstaged
  *file* and makes `verify` return `False` instead of explaining. Resolve the
  producer the way `_emitted_note` already does — the module in `ctx.modules`
  whose `provides` holds `TRACK3P_PARTICLES` — and branch on whether it is a
  `Track3PSourceModule` (file: stage, basename) or a `Track3PModule`
  (directory: `ImpactsInfo_*` under the job name). Keep the existing
  "told apart by `isdir`, not `isfile`" test as the regression pin and add one
  for the missing-directory case raising a `ValueError` that names the path.
  Docstring and the §3.4 note above change with it.
- ~~**Also, while in the suite:** pin BLAS/OpenMP threads for the test run
  (`threadpoolctl.threadpool_limits(1)` in a session autouse fixture, or the
  three `*_NUM_THREADS=1` exports in the pytest batch script).~~ **Done
  2026-10-06** — session autouse fixture in `tests/conftest.py`; the slowdown
  was thread oversubscription. Full suite 5:34 wall / 6:56 CPU with the fixture
  alone, against 12:14–12:25 CPU for the previously *fast* unpinned runs and
  1:14:59 for the worst. See `plans/pytest_slowdown_experiment_plan.md`.

1. `seed:` handling (§3.7); `extract` detector quantities; `detector` index
   axis; spectrum on `field()`.
2. Move the three Geant4 examples to the polycone app path and geometry key;
   add `passes`, `seed: auto`, `detectors = on` to `input_7cell.geant4` or a
   new `input_lcls.geant4` with `nb_wall_profile.dat` copied to assets.
3. One S3DF validation per example (`passes` small), baselines refrozen.

**Phase 4 status (2026-10-06): steps 0 and 1 DONE; steps 2 and 3 DEFERRED**
(David, 2026-10-06, before implementation). One commit. **No baseline moved and
none was added** — `test_baseline_selfcheck.py` passes unchanged, which is the
check that says so.

**Why steps 2–3 were deferred, and what has to be decided first.** Reading the
polycone application's own source against the repo found two facts that make the
planned migration produce meaningless physics rather than a working example:

- **The app ignores `solid_stl` entirely.** `construction.cc:18` assigns
  `fSolidSTLs` in the constructor and `Construct()` **never reads it** — the
  only geometry built is `cavity_stl`, parsed as an R(Z) table into a
  `G4Polycone`, plus the cryostat cylinders. `CADMesh.hh` is still `#include`d
  but unused, which is what makes the gap easy to miss. So
  `7cell_solid_whole.stl` / `7cell_cavity_whole.stl` cannot carry over at all,
  and the app's geometry *is* the LCLS-II cryomodule: profile Z = −111…10856 mm,
  8 detectors compiled in at z = 414…10126 mm (`construction.hh:42`).
- **The shared dump is in the wrong place for that geometry.**
  `examples/assets/sample_track3p_particles.txt` has primaries at
  r = 0.010–0.017 m, |z| ≤ 0.125 m, while the profile's `r_inner` is ≥ 0.039 m
  everywhere — so **100 %** of its primaries would start in vacuum far from the
  Nb wall, with every detector ≥ 0.4 m away in z. The same
  "geometry that does not match the dump" objection that stopped Phase 3's
  `geant4` step, arriving from the other direction.

A real polycone example therefore needs an **LCLS dump asset**. Measured this
session: a 20 000-row `ImpactOrder 1` excerpt of `data/track3p/c3_16MV` is
3.9 MB and gives 1 192 / 1 691 / 2 329 primaries at β = 100 / 120 / 140 through
the repo's own weighting, so the study's own 100–150 range *is* meaningful on
that dump (unlike on the shared one — the Phase 2 lesson, from the other side).
Whether to ship such an asset, and at what size, is the decision steps 2–3 are
waiting on; §6's "which Geant4 app path the examples name" row is updated
accordingly. The three examples stay on `dose-npass`, where their dump and their
STLs match, and nothing about them moves.

The module work needed none of it: the detector quantities are driven off **real
detector CSVs** copied in as fixtures, plus one real run.

Verified on milano (one job at a time):

| Job | What | Result |
|---|---|---|
| 40050673 | full suite, steps 0 + 1 | **820 passed / 2 skipped, 6m01s** (was 783 + 2) |
| 40050948 | polycone run, 20 k-row excerpt, `passes: 10`, whole-vector detector outputs | COMPLETED 12 s — plumbing confirmed, but **every detector value legitimately 0** and no field artifact; see below |
| 40051263 | polycone run, full `c3_16MV` dump, `passes: 30`, `at:`-narrowed outputs | COMPLETED 7m46s — **every mechanism confirmed on real output** |

The validation ran from a throwaway config in
`/sdf/scratch/users/d/dbizzoze/ph4_polycone/`, not a committed example, since
the example migration is deferred. Job 40051263 confirmed, against the real
binary: all four output filenames resolved off `output_prefix` (the input names
no `output_dose`); `det1_gammas = 2` and `det3_gammas = 4` matching rows 1 and 3
of the CSV; `det3_edep = 0.0` as a **real** zero while detectors 1, 5 and 6
deposited energy; `seed = 1808052552` written by `auto`, and a different value
(`1426628193`) for the differently-configured job 40050948; and
`gamma_spectrum` inside `field_0.npz` with 19 ragged rows
(`[2,3,3,3,3,2,3,0]` per detector) round-tripping through
`save_field`/`load_field`.

**Job 40050948 was the useful failure.** Sized at 16 910 events it produced a
header-only spectrum and eight zero detectors — ~1000× too few, since Lixin's
own 18 M-event run counts only 5–18 gammas per detector — and, because its
detector outputs were declared without an `at:`, the table went long-format and
**no field artifact was written at all**. That is pre-existing, documented mode
behaviour (`modes._persist_field` returns `None` once a table has an index axis;
an S3P long sweep loses its `PortRef` profiles the same way), not something this
phase introduced, and changing it would add a `field_artifact` column to every
frozen long-format baseline. So it is documented instead — in
`Geant4Module.field`, in `docs/yaml_reference.md`, and pinned by a new test in
`tests/test_results.py` that asserts an un-indexed field key is dropped rather
than kept beside the rows. The guidance: narrow the detector outputs with
`at: {detector: n}` when the spectrum is wanted too.

Deviations from the text above:

- **`seed: auto` derives the seed from the evaluation's config hash, not from
  "evaluation index + base"** as §3.7 proposed. No point index reaches
  `Workflow` by design (`point_workdir`'s docstring: sweep ordering belongs to
  the mode layer), and an index is not stable across a re-ordered or partially
  resumed sweep, while `config_hash(entries, inputs, output_spec)` — which
  `evaluate` already computes for the run manifest — is distinct per point *and*
  reproducible per point. It now rides on `RunContext.config_hash`. Two
  *identical* points share a seed, which is the right answer for an identical
  configuration. §3.7's third form, "input-variable name", needed **no code**: a
  swept `input_parameters: {geant4: {seed: ...}}` already reaches `set_value`
  through `ctx.inputs.macro`, and an input-space value deliberately wins over
  the module key.
- **`_output_files` had a pre-existing gap that had to be fixed first.** It read
  only `output_dose` / `output_edep`, and the polycone app writes neither — it
  derives every output name from `output_prefix` (`sim.cc:165-177`,
  `run.cc:143-160`). A prefix-only input therefore resolved to *no filenames at
  all*, so `extract` returned NaN and `verify` returned `None`, for the grids as
  much as for the detectors. The full four-step precedence (override → input-file
  key → prefix-derived → bare default) is now implemented, and the
  "before the input file has been read, name nothing" property `verify`
  documents is preserved explicitly.
- **The detector CSVs needed their own reader.** They are **comma**-separated
  with a `#` header, and `parse_column_file` — the header-driven reader every
  other table in the package goes through — splits on whitespace, so it finds no
  numeric rows and returns a single key made of the file's last line. A test
  pins that, so a later "just reuse the column reader" refactor fails loudly.
  The pair lives in `geant4.py`, not `surrogate_data.py`: `read_dose_file` is the
  canonical *voxel-grid* parser and these are not voxel grids.
- **Detector quantities carry no `section:`.** There is one detector table and
  the quantity names it, so `detector_edep_MeV` / `detector_gammas` route on the
  quantity (or on the `detector` axis an `at:` narrows) rather than on a section
  the way the grids do.
- **`Geant4Module.index_precedence = 2`**, above Track3P's 1: in a
  `track3p -> field_emission -> geant4` chain the dose at a detector is the end
  product, while the field level that produced the dump is one scalar per run.
  Nothing shipped is affected — the `dose-npass` binary supports none of
  `seed` / `detectors` / `cryostat` (confirmed from its `strings`), so it writes
  no detector CSV, `field_index` stays `None`, and all three examples keep their
  wide tables.
- **A zero detector `edep_MeV` is a result, not a gap**, and the fixture makes
  that unavoidable: 7 of its 8 detectors deposited nothing while all 8 counted
  gammas. Pinned, because a reader that dropped zeros or an `extract` that
  returned NaN for one would look correct on a busier run.
- **The gamma spectrum is not an extractable column.** The app writes only each
  detector's non-empty bins, so the rows are ragged and `detector_id` repeats
  down them; it rides out in `field()` as a nested dict, the route S3P's
  `IndexMap` already takes through `save_field`.
- **The step 0a test fired on its first real use.** The new
  `git ls-files tests/fixtures` assertion failed on the two detector CSVs this
  phase added, before they were `git add`ed — exactly the Phase 0-to-3 failure
  mode it was written for, caught in the checkout that created it rather than in
  a later fresh clone.
- **Step 0b keeps the `isdir` check as a documented fallback.** Dispatch is on
  the producer's class when `ctx.modules` names one, but a `RunContext` built
  from artifacts alone (the unit tests, a direct driver) has no producer to ask,
  and the plan's named regression pin
  (`test_particles_resolves_a_dump_path_that_is_not_staged_yet`) is exactly that
  shape. Both halves are now tested: a `track3p_source` artifact that happens to
  be a directory is still read as a file, and a `track3p` results directory that
  has vanished raises naming the path instead of being misread as an unstaged
  dump.

### Phase 5 — docs, plans, memory

1. `docs/track3p_reference.md` in the `t3p_reference.md` shape: inputs the
   module touches, the output-file table (§2.3), both `ImpactsInfo` layouts
   and the one-line selector, the `extract` quantity table, FN model choice,
   the "pre-run dumps vs in-pipeline" guidance. Add to `docs/index.md` toctree
   and the "Running Track3P?" bullet.
2. `docs/yaml_reference.md` module entries for `track3p` keys and the new
   `particles`/`geant4` keys; `docs/workflow_inputs.md` for `ace3p:` leaves on
   a Track3P input.
3. `plans/beta_localization_plan.md` §4 step 2 ("there is no in-pipeline
   tracker") and §10 Track3P questions — update, pointing here.
4. `Track3PSourceModule` docstring (`modules.py:376-383`) and the
   `workflow_graph.py:20-26` design note: the predicted module now exists.
5. Memory: mark `track3p-on-s3df` as landed; record the FN decision.

---

## 5. Order and dependencies

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 5
                            └──────► Phase 4 ──┘
```

Phase 1 is the cheapest deliverable with the widest reach (use case A, the
acdtool chain, dry-run) and depends only on Phase 0. Phase 2 does not depend on
the module at all and unblocks the LDRD modeling studies on the 13 existing
dumps. Phases 0–2 are fully specified, need only two short S3DF runs, and are
the unattended (overnight) batch; Phases 3–5 involve design and sizing choices
David wants to see and are done in a supervised session. Phase 4 follows
Phase 2 because the detector numbers only mean something once the weights match
Lixin's.

---

## 6. Decisions needed from David before the phase that uses them

| Phase | Decision | Recommendation |
|---|---|---|
| 2 | FN model default: plain FN (Lixin) or Wang–Loew (repo today) | **DECIDED 2026-09-14: plain FN**, so the 22 shipped files are reproducible |
| 2 | Keep `sample_track3p_particles.txt` or swap for the 1 000-row C3 excerpt | **DECIDED 2026-10-01: keep** — the excerpt leaves ~35 primaries and meaningless dose |
| 1 | `mp_onset_level` default threshold | 1.0 (enhancement ≥ 1 = growth) |
| 3 | Artifact: one `ImpactsInfo` file when unambiguous, or always the results directory | **DECIDED 2026-10-01: always the directory**; `FieldEmissionModule` resolves both shapes. **Implemented 2026-10-05** |
| 3 | Chain example under dry-run: placeholder artifact (A) or not frozen (B) | **DECIDED 2026-10-01: B** — CI asserts validate/order plus the §3.5 rejections. **Implemented 2026-10-05**; the example also stops at `field_emission`, since no Pillbox Geant4 geometry exists |
| 3 | Probe `N: 1` Pillbox field emission before or via the example's validation run | **DECIDED 2026-10-01: before** (step 2; < 2 min, and the answer shapes the example). **Done 2026-10-05 — and it mattered**: `N: 1` emitted nothing; the bounding box was the blocker, and β came out at 45, not 100–150 |
| 3 | Rename the `particles` module | **DECIDED 2026-10-01: `field_emission` / `FieldEmissionModule`**, hard rename (no alias — single user), `particles:` input bucket untouched. **Implemented 2026-10-05** |
| 4 | Which Geant4 app path the examples name | **DEFERRED 2026-10-06 — the migration needs an LCLS dump asset first.** The polycone app ignores `solid_stl` (`construction.cc` never reads it) and its geometry is the cryomodule, so the 7cell STLs cannot carry over *and* the shared `sample_track3p_particles.txt` has 100 % of its primaries in vacuum ≥ 0.4 m from any detector. A real example needs an excerpt of Lixin's own `c3_16MV` dump (20 k order-1 rows = 3.9 MB, 1 691 primaries at β = 120). Open question: ship such an asset, at what size? The three examples stay on `dose-npass`, which matches their dump and STLs. Phase 4's module work was validated against real detector CSVs plus one throwaway run instead |

Questions for Lixin (not blocking; the source answers most): semantics of
`Domain.Mode {Amplitude, Phase, Rz}` in solo mode and of `SuppressionFactor`;
whether the group `/sdf/group/rfar/ace3p/bin/track3p` will be refreshed to the
08-31 build; whether the detector CSV layout is stable.

---

## 7. Test and validation policy

- Unit/fixture tests run without ACE3P (fake solver, Phase 0 fixtures), as for
  every other module.
- Every new or changed example gets one real S3DF run before its baseline is
  frozen; one sbatch job at a time on `milano`/`rfar:regular`, from scratch,
  `source ~/ace3p.sh` **before** `conda activate lume-ace3p-dev`.
- Baselines that move: `geant4_track3p_beta` (Phase 2). New:
  `track3p_multipacting` (Phase 1). **Phase 3 moves none** — the output-name fix
  leaves `.txt` names alone, the rename changes the type key but not the
  weighting, and `track3p_geant4_chain` goes into `not_frozen.json` (its
  `particles` step cannot run on a dry-run `track3p` artifact). **Phase 4 moves
  none either**, which is a change from the line above: its steps 2–3 (the
  example migration that would have refrozen the Geant4 baselines) are deferred,
  and steps 0–1 touch only module internals — the detector CSVs are only read
  when an input turns detectors on, so no shipped example's table or digest
  changes. All others must not move.
- Nothing under `tests/fixtures/` is generated by the suite; `SOURCES.md`
  records where each byte came from and how it was truncated.
