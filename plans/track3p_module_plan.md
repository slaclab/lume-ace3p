# Track3P as a Workflow Module — Implementation Plan

**Status: IN PROGRESS — Phase 0 done 2026-09-14** (see the status notes at the
end of each phase). Written 2026-09-14 on S3DF. Decision taken
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

- `ParticlesModule.run` (`modules.py:1806`) treats `ctx.artifacts[TRACK3P_PARTICLES]`
  as **a file path** and stages it.
- `acdtool postprocess track3p` (`acdtool.py:252`) wants
  `ctx.job_names[TRACK3P_PARTICLES]` = the results directory name.

Contract: the module always sets `ctx.job_names[TRACK3P_PARTICLES] = solver.job_name()`.
For the artifact it sets **the path of one `ImpactsInfo_<level>` file** when the
run produced exactly one, or when `field_level:` names one; otherwise it sets
the results directory. `ParticlesModule` learns to handle a directory: exactly
one `ImpactsInfo_*` inside → use it; several → raise listing the levels and the
two ways to choose (`field_level:` on `track3p`, or a single-level
`FieldScales`). `Track3PSourceModule` is unchanged.

### 3.5 Build-time validation

In `Workflow.__init__` (where `WorkflowValidationError`s already fire for
unroutable specs and missing producers), add: if a `particles` module is
downstream of `track3p` and the Track3P input file, after the module's own
injection, has no `OutputImpactsInfo` container with `Type: Initials-Impacts`,
raise naming the exact two lines to add or the `impacts_format:
initials-impacts` key. This is the "fail at build, not after a 45-minute
solve" rule the plan inherits from `xopt_config_validation_plan.md`.

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
- **Detector outputs.** `extract` quantities `detector_edep_MeV` and
  `detector_gammas` from `<prefix>_detector_dose.csv`, `at: {detector: n}`
  narrowing, or the 8-vector without `at:`; `detector` becomes the module's
  index axis when a detector quantity is declared (the `ModeID`/`Frequency`
  rule). `<prefix>_detector_gamma_spectrum.csv` rides on `field()`.
- Examples `geant4_dose_single`, `geant4_track3p_beta`, `geant4_beta_surrogate`
  move to the polycone app path and the `nb_wall_profile.dat` geometry key.
  `geant4_app_path` stays a YAML override, so nothing hardcodes Lixin's path in `src/`.

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

### Phase 3 — `track3p -> particles` chain (use case B, in-pipeline)

1. `impacts_format: initials-impacts` injection (§3.2) and the build-time
   validator (§3.5).
2. Artifact contract (§3.4): single-file vs directory, `field_level:`,
   `ParticlesModule` directory handling with the enumerating error.
3. Example `examples/track3p_geant4_chain/` (or extend
   `geant4_track3p_beta` with a commented alternative head): `mesh_source ->
   omega3p -> track3p -> particles -> geant4` on the Pillbox with `Emitter
   Type: 7, N: 1` so `InitialNormalField` is populated. Dry-run only in CI;
   one S3DF validation run. Note in the README that for cryomodule-scale
   meshes this head costs ~50 node-minutes per evaluation and the intended
   pattern is `track3p_source` over pre-run dumps.
4. Hybrid launch: check whether `tasks: 2, cores: 60` plus `opts:` can
   reproduce Lixin's `srun -n 2 -c 60` with `OMP_NUM_THREADS=60`, or whether
   the module needs an `env:` mapping. Document the working recipe in the
   example batch script.

### Phase 4 — Geant4 module for the polycone app

1. `seed:` handling (§3.7); `extract` detector quantities; `detector` index
   axis; spectrum on `field()`.
2. Move the three Geant4 examples to the polycone app path and geometry key;
   add `passes`, `seed: auto`, `detectors = on` to `input_7cell.geant4` or a
   new `input_lcls.geant4` with `nb_wall_profile.dat` copied to assets.
3. One S3DF validation per example (`passes` small), baselines refrozen.

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
| 2 | Keep `sample_track3p_particles.txt` or swap for the 1 000-row C3 excerpt | swap; note provenance (overnight session: keep unless told otherwise) |
| 1 | `mp_onset_level` default threshold | 1.0 (enhancement ≥ 1 = growth) |
| 4 | Which Geant4 app path the examples name | Lixin's polycone package, via `geant4_app_path` in YAML |

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
- Baselines that move: `geant4_track3p_beta` (Phase 2), the Geant4 examples
  (Phase 4). New: `track3p_multipacting` (Phase 1). All others must not move.
- Nothing under `tests/fixtures/` is generated by the suite; `SOURCES.md`
  records where each byte came from and how it was truncated.
