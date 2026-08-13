# Acdtool Rework + Output-Spec Migration — Implementation Plan

**Status: NOT STARTED.** Planned 2026-08-13 from a cross-reference of the CW23
ACE3P tutorial archive against the current module layer. No code written yet.

This plan reworks how `acdtool` is invoked and parsed, and migrates
`output_parameters` off the positional `['section', 'mode_id', 'column']` list
form onto the explicit mapping form already used by S3P/T3P objectives.

---

## Motivation

The `acdtool` wrapper was built for exactly one command against exactly one
Omega3P section. CW23 shows the tool is much broader, and three of the gaps are
live bugs rather than missing features.

### Reference data: what acdtool actually is

CW23 (`/home/dbizzoze/CW23`, `examples/` and `exercises/` — near-duplicate
trees; ignore the `a3pi/`, `A3PI_config_single`, `workflow_test_single/`
subtrees, which belong to a defunct ancestor project) uses **six** acdtool
commands. The wrapper supports one.

| Command | Input form | Consumes | Supported today |
|---|---|---|---|
| `meshconvert <f>.gen` | positional | genesis mesh | yes, but inside `cubit.py`, not `acdtool.py` |
| `postprocess rf <f>.rfpost` | `.rfpost` file (`=` dialect) | Omega3P **or** S3P results | yes |
| `postprocess transwake <dir> x1 y1 x2 y2` | positional | T3P results | **no** |
| `postprocess coaxsignal <dir>` | positional | T3P results | **no** |
| `postprocess volmontomode <dir>` | positional | T3P results | **no** |
| `postprocess track3p <f>.acdtool <level>` | `.acdtool` file (`:` dialect) + field level | Track3P results | **no** |
| `mesh deform <in>.ncdf <out>.ncdf <scale>` | positional | TEM3P deformed mesh | **no** |

Call sites, for a fresh session that wants to see the real scripts:

- `postprocess rf` — `examples/omega3p/*/run-acdtool.batch`, `examples/s3p/window/run-acdtool.batch`
- `postprocess transwake` — `examples/t3p/cavity-half/run-t3p.batch` (and `half-model/`, `multi-beam/`)
- `postprocess coaxsignal` — `examples/t3p/BPM/run-t3p.batch`
- `postprocess volmontomode` — `examples/t3p/SIBC/run-t3p.batch`
- `postprocess track3p` — `examples/track3p/Pillbox/run-acdtool.batch` + `Pillbox.acdtool`
- `mesh deform` — `examples/tem3p/RfGun-Coupler/run-scale-deformed-mesh.sh`

### The `.rfpost` format has 19 blocks; the parser implements 3

Every Omega3P `.rfpost` in CW23 is the same 19-block template gated by `ionoff`
flags. `acdtool.py::output_parser` implements `RoverQ`, `kickFactor`,
`maxFieldsOnSurface`; the other 16 print `"parsing not implemented"`.

The 19 blocks collapse into **four shapes**, which is what makes the parser
rework tractable:

| Shape | Index axis | Blocks | Written to |
|---|---|---|---|
| Mode-indexed table | `ModeID` | `RoverQ`, `RoverQT`, `kickFactor`, `VFFT`, `ALLFieldAtPoint`, `coaxPort` | `rfpost.out` |
| Surface-indexed scalars | `surfaceID` | `maxFieldsOnSurface`, `powerThroughSurface` | `rfpost.out` |
| Column curve files | position / phase | `FieldOnLine`, `ALLFieldOnLine`, `Multipole`, `GBZFFT`, `Track`, `TrackScan` | **separate files** |
| Grid / mesh | — | `FieldMap`, `fieldOnSurface`, `fieldOn2DBoundary` | separate files |
| Run-level scalars | — | `[scaling]` (always emitted, never declared) | `rfpost.out` |

The six **curve** blocks are the easy ones despite looking hardest: a block with
a `filename` key writes plain `#`-commented column tables with a header row, one
file set per mode. See `examples/s3p/window/` → `field1_0`, `field1_0.ec`,
`field1_0.bc` (`.ec`/`.bc` carry complex E/B with amplitude+phase columns). This
is the same shape `ace3p.py::parse_wakefield` already handles, so one
header-driven column reader covers all six.

The genuinely fiddly parsing is confined to `rfpost.out`.

### Confirmed defects (all reproduced against real CW23 files)

1. **Multi-line brace values are silently dropped.** `coaxPort` is the one block
   designed to hold lists (`portID`, `porta`, `portb`). CW23 ships them empty
   (`portID = {  }`), which round-trips. Filled in the natural multi-line way,
   `input_parser` stores `portID = '{'`, discards the contents, and the stray `}`
   closes the block early — no error raised.

2. **`.acdtool` files use a different dialect and parse to nothing.**
   `.rfpost` is `key = value`; `.acdtool` (for `postprocess track3p`) is KVC
   `key : value`, the same dialect as solver input files. `Acdtool` splits on
   `=`, so `Pillbox.acdtool` parses to
   `{'EnhancementCounter:': {}, 'Trajectory:': {}}` — two empty blocks, silently.
   `ace3p.py::parse_ace3p` reads the same file correctly.

3. **`[scaling]` ships unclosed in the S3P case.** In
   `examples/s3p/window/rfpost.out` the `[scaling]` block has no closing `}`,
   which makes the `startswith('}')` end-detection in `output_parser` unreliable
   for anything following it.

4. **`--nodes=1 --ntasks=1` is srun-only.** `acdtool.py` hardcodes it. `ace3p.py`
   guards `--cpu-bind` against non-srun callers; `Acdtool` has no equivalent, so a
   non-srun `MPI_CALLER` breaks the step. CW23 itself uses `srun -n 1 -c 1`.

5. **Two of the three implemented parsers are unvalidated.** Grepping every
   `.out` in CW23 for section headers yields only `[scaling]` (15×) and
   `[RoverQ]` (11×) — no CW23 run ever enabled `kickFactor` or
   `maxFieldsOnSurface`. Current tests use hand-written fixtures
   (`tests/test_modules.py::RFPOST_OUTPUT`). `examples/omega3p_sweep` depends on
   `maxFieldsOnSurface` for `E_max`, so that path rests on assumed format.

### `acdtool` requiring `em_solution` blocks the standard T3P workflow

`AcdtoolModule.requires = {EM_SOLUTION}`, so `[cubit, t3p, acdtool]` is a
`WorkflowValidationError`. But every CW23 T3P batch script runs t3p and acdtool
in the same script, and `transwake`/`coaxsignal`/`volmontomode` are *precisely*
time-domain postprocessors. The rule is too coarse: it is really
"`postprocess rf` needs a frequency-domain solution," not "acdtool does."

`transwake` matters specifically because it is how CW23 gets the transverse wake
out of a half/quarter model. `T3PModule` reads `wakefield.out` directly, which
covers the built-in monitor but not this path.

**Verified: this needs no framework change.** `_resolve_order` reads
`requires`/`provides` off *instances*, and modules are instantiated before
resolution, so setting `self.requires` in `__init__` from the declared command is
sufficient. A `[cubit, t3p, acdtool]` chain with an instance-level
`requires = {TD_SOLUTION}` resolves cleanly today.

### Omega3P eigenmode results are already parseable by existing code

`omega3p_results/omega3p.out` is written in KVC syntax — the same format as input
files — and `parse_ace3p` reads it **unmodified**:

```
pillbox:            Mode → {Frequency, QualityFactor, TotalEnergy, PowerLoss, File}
pillbox-rtop+coax:  Mode → {Frequency: '1313756106.9 , 641.33', ExternalQ, ...}
```

`Omega3P.output_parser()` is `pass` and `Omega3PModule` has no `extract`, so a
mode frequency today requires running acdtool with `RoverQ` enabled — which is
why `examples/omega3p_sweep` spells frequency as `['RoverQ', '0', 'Frequency']`.
Note the lossy/port case returns `Frequency` as a `"real , imag"` pair and adds
`ExternalQ`, and top-level section order differs between runs (search by name,
never by position).

### S3P discards phase

`S3P.output_parser` reads `s3p_results/Reflection.out`, which holds **|S|**
magnitudes. The sibling `SParameter.out` holds the same matrix as
`(real, imag)` pairs. Also unparsed: `PortRef<n>_<m>.out` (port mode field
profiles). Separately, S3P **hardcodes** `s3p_results/` while T3P correctly
reads `JobName` from the input tree (`T3P.results_dir()`), so a `.s3p` that sets
`JobName` makes `output_parser` raise `FileNotFoundError`.

---

## Target design

### Command dispatch

The command becomes explicit in YAML rather than inferred from a file
extension:

```yaml
workflow :
  - module : acdtool
    command : 'postprocess rf'        # default when an input file is given
    input   : 'pillbox-rtop.rfpost'

  - module : acdtool
    name    : 'transwake'
    command : 'postprocess transwake'
    args    : [0.0, 0.0, 0.0, 0.0125]  # results dir is injected from the artifact
```

`requires` is derived from `command`: `postprocess rf` → `em_solution`;
`transwake` / `coaxsignal` / `volmontomode` → `td_solution`;
`postprocess track3p` → `track3p_particles`.

### Output spec: mapping form, with the index as an axis

The positional list's middle element is not a selector — it is an **index axis**.
`['RoverQ', '0', 'RoQ']` hardcodes "mode 0", but the dominant Omega3P need is
*all* modes (dispersion curves, HOM catalogs, mode spectra), and for an
eigensolve you often do not know N in advance.

The machinery already exists: `Module.field_index()` returns
`('Frequency', array)` for S3P and `('s', array)` for T3P, and `modes.py`
explodes that into a long-format row-per-index table. RoverQ over modes is
structurally identical — `('ModeID', array)`. So this is implementing
`field_index`/`field` on `AcdtoolModule`, not new infrastructure.

```yaml
output_parameters :
  'R/Q'       : {module: acdtool, section: RoverQ, quantity: RoQ}            # row per mode
  'Mode_freq' : {module: acdtool, section: RoverQ, quantity: Frequency}      # row per mode
  'f0'        : {module: acdtool, section: RoverQ, quantity: Frequency, at: {mode: 0}}   # scalar
  'E_max'     : {module: acdtool, section: maxFieldsOnSurface, quantity: Emax, at: {surface: 6}}
  'P_out'     : {module: acdtool, section: powerThroughSurface, quantity: Power, at: {surface: 6}}
```

`at:` is the same narrowing form S3P and T3P already use for optimizer
objectives, so `_parse_spec` generalizes rather than forks.

This also retires shape-sniffing for new specs. There are three spec dialects
today — bare list (acdtool, geant4), bare string (t3p, s3p, particles), mapping
(s3p/t3p objectives) — and `_infer_output_module` is 35 lines whose only job is
telling them apart. Adding 16 sections makes that worse: `['Track', ...]` and
`['Multipole', ...]` begin colliding with T3P quantity names, and the docstring
already has to explain that acdtool's `kickFactor` and T3P's `kick_factor` are
"distinct spellings on purpose."

### Design decisions

1. **Deprecated-alias migration, not a clean break.** Unlike the module refactor,
   keep `['RoverQ', '0', 'RoQ']` working as an alias that rewrites to the mapping.
   The surface is 6 example YAMLs, 6 docs pages, 2 test modules, and 4 baseline
   artifacts including `tests/baseline/omega3p_sweep/manifest.json`. Breaking it
   forces baseline regeneration, which muddies the diff for a change that should
   be additive.

2. **One table axis per workflow, resolved by rule — no `index:` config key.**
   `Workflow.field_index()` returns the *first* non-`None` across modules, so
   making acdtool mode-indexed creates two distinct collisions. Both are settled
   by fixed rules rather than by new YAML surface; an optional `index:` key can be
   added later without breaking anything if a real need appears.

   **Cross-module** (`s3p + acdtool` — the CW23 `window` case): `Frequency` vs
   `ModeID`. Rule: **first producer in resolved DAG order wins**, which is what
   `field_index()` already does (`self.modules` is stored topologically ordered).
   For `[cubit, s3p, acdtool]` that is S3P's `Frequency`. This is not merely the
   back-compatible answer, it is the correct one — the `window` case is a
   frequency scan postprocessed at `FreqScanID = 2`, so a frequency-indexed table
   is what you would have declared anyway. It also keeps every existing example's
   table shape byte-identical, which Phase 4 requires.

   **Intra-module** (one acdtool module supplying both `RoverQ` and
   `maxFieldsOnSurface` — the shape `examples/omega3p_sweep` already has): DAG
   order cannot disambiguate, since `field_index()` returns one axis per module.
   Rule: **acdtool's only table axis is `ModeID`. Surface-indexed sections
   (`maxFieldsOnSurface`, `powerThroughSurface`) require `at: {surface: n}` and
   always resolve to scalars.** This follows the data rather than compromising:
   the `.rfpost` input block itself pins one surface
   (`maxFieldsOnSurface { surfaceID = 6 }`), so surfaces are few and enumerable,
   while modes are many and unknown before the eigensolve. Raise a clear error if
   a surface-indexed section is requested without `at:`, naming the surface IDs
   found in the output.

   Any axis that is not the table axis routes to a field artifact via
   `Module.field()`. This keeps the flat table single-indexed — which is what
   makes it a valid DataFrame — without pretending the other axis does not exist.
   Generalizing to a MultiIndex is deferred until a concrete need appears.

3. **TEM3P / named artifacts are OUT OF SCOPE.** CW23's `tem3p/RfGun-Coupler`
   chain (`cubit ×2 → omega3p(vacuum) → tem3p(body) → acdtool mesh deform →
   omega3p(deformed) → Δf`) needs two meshes, two `omega3p` modules, and acdtool
   *producing* a mesh. All three violate the one-producer-per-artifact rule in
   `_resolve_order`. Making artifact identity per-instance rather than per-kind is
   a separate, foundational effort. **Do not attempt it inside this plan.** Where
   this plan touches `mesh deform`, stop at making the command invocable; do not
   wire it as a mesh producer.

4. **Keep DataFrames scoped to result tables** — same rule as the module
   refactor. Never DataFrame-ify the `Section` tree or the curve/grid outputs.

---

# Phase 0 — Freeze real acdtool fixtures (do this first)

**Objective:** Capture CW23's *real* acdtool inputs and outputs as test fixtures
before the parser is rewritten, and record explicitly which sections have no
real-output coverage. Defect 5 above means the current `kickFactor` /
`maxFieldsOnSurface` parsers rest on assumed format; rewriting them on top of the
same assumptions would bake the assumption in deeper.

### Approach

1. Add `tests/fixtures/acdtool/` and copy in, with provenance recorded in a
   `SOURCES.md` (CW23 is outside the repo and not version-controlled).

   **`tests/fixtures/` is a new directory** — a deliberate departure from the two
   existing conventions, not an existing one to slot into. Today real inputs are
   inlined as module-level string constants (`tests/test_modules.py::RFPOST_INPUT`)
   and `tests/baseline/` holds frozen *example outputs*. Neither fits multi-KB
   real solver files: the curve files are 302 lines × 16 columns and cannot sanely
   be inlined, and they are inputs to a parser rather than golden outputs of a
   run. Leave the existing inline constants alone; do not migrate them.

   All paths below were verified present on 2026-08-13. Sizes are the originals:
   - `.rfpost` inputs: the 19-block Omega3P template
     (`examples/omega3p/pillbox-rtop+coax/`) and the 2-block S3P one
     (`examples/s3p/window/`).
   - `rfpost.out` outputs: all five that exist — `omega3p/pillbox+recWG`,
     `pillbox+recWG+load`, `pillbox-rtop+coax`, `pillbox-rtop`, `s3p/window`.
     These cover `[RoverQ]` single- and multi-mode, both `[scaling]` variants
     (gradient-normalized and `gradient = -1` point-scaled), and the **unclosed**
     `[scaling]` case.
   - Curve files: `examples/s3p/window/field1_0`, `field1_0.ec`, `field1_0.bc`
     (and the `_1` set) — the `ALLFieldOnLine` per-mode output. These are 302 data
     rows each and 312 KB for the six, which is too much to commit wholesale.
     **Keep `field1_0.ec` in full** — it is the richest (16 columns: real, imag,
     amplitude, phase, magnitude) and the only one that needs a real row count
     asserted. **Truncate the other five to the header plus ~20 rows**, and record
     the truncation and each original row count in `SOURCES.md` so a later session
     does not mistake a short file for a parser bug. Total should land near 20 KB.
   - `.acdtool` input: `examples/track3p/Pillbox/Pillbox.acdtool` (colon dialect).
   - Solver outputs for Phases 1 and 5: `omega3p_results/omega3p.out` from
     `omega3p/pillbox` (real eigenvalues) and `pillbox-rtop+coax` (complex
     eigenvalues + `ExternalQ`); `s3p_results/Reflection.out`,
     `SParameter.out`, `PortRef7_0.out` from `s3p/90DegreeBend`.
2. Add characterization tests that assert what the **current** code produces from
   each fixture, including the wrong answers (the `coaxPort = '{'` truncation, the
   two empty `.acdtool` blocks). These are regression anchors, not endorsements —
   label them so, and invert them in the phase that fixes each defect.
3. Write `tests/fixtures/acdtool/COVERAGE.md`: per rfpost block, whether a real
   output fixture exists. Record that `kickFactor` and `maxFieldsOnSurface` have
   **none**, and that `examples/omega3p_sweep` depends on the latter.

### Verification (Phase 0 done when)

- [ ] `tests/fixtures/acdtool/` holds the files above with provenance in `SOURCES.md`.
- [ ] Characterization tests pass against unmodified `src/`.
- [ ] `COVERAGE.md` names every block with no real-output fixture.
- [ ] `python -m pytest tests/` still green.
- [ ] `SOURCES.md` records which curve files were truncated and their original
  row counts.
- [ ] `du -sh tests/fixtures/` is under ~50 KB.

**No ACE3P environment needed for this phase** — it is fixture copying plus
characterization tests against files already on disk, and makes no `src/` changes.
It runs fully locally.

### Deliverables

- [ ] `tests/fixtures/acdtool/` + `SOURCES.md` + `COVERAGE.md`.
- [ ] `tests/test_acdtool_fixtures.py`. No `src/` changes in this phase.

**Blocking note for Phase 3:** obtain one real acdtool run with `kickFactor` and
`maxFieldsOnSurface` enabled (any Omega3P example plus those two `ionoff = 1`)
before rewriting those two parsers. If a cluster run is not available, Phase 3
must keep the existing parsers byte-compatible and route only the *new* sections
through the new shape readers — do not "clean up" unvalidated parsers blind.

---

# Phase 1 — Omega3P eigenmode parsing

**Objective:** Give `Omega3P` a real `output_parser` and `Omega3PModule` an
`extract`, so mode frequency / Q come from the solver's own output instead of
requiring an acdtool `RoverQ` step. Independent of every other phase — no
acdtool and no spec changes.

### Approach

1. `Omega3P.output_parser()`: parse `<JobName>/omega3p.out` with the existing
   `parse_ace3p`, then walk top-level `Mode` sections into
   `output_data['Modes']` — a list of `{Frequency, QualityFactor, ExternalQ,
   TotalEnergy, PowerLoss, File}`. Resolve `JobName` from the input tree the way
   `T3P.results_dir()` does (default `omega3p_results`); do not hardcode.
2. Handle the lossy/port case: `Frequency` and `TotalEnergy` arrive as
   `"real , imag"` pairs. Split into `Frequency` / `Frequency_imag` (keep
   `Frequency` real-valued so it stays a plottable table column) and set
   `ExternalQ` only when present. Search sections **by name** — top-level order
   differs between the two fixtures.
3. `Omega3PModule.extract` + `field_index` + `field`, mirroring `S3PModule`:
   index axis `('ModeID', arange(n_modes))`, `at: {mode: n}` narrowing, and the
   dry-run NaN sentinel the other modules return.
4. Do **not** touch `examples/omega3p_sweep` yet — its `['RoverQ', ...]` specs
   keep working, and migrating examples is Phase 6.

### Verification (Phase 1 done when)

- [ ] Both Phase-0 `omega3p.out` fixtures parse: real-eigenvalue case yields 2
  modes with `QualityFactor`; complex case yields 1 mode with `ExternalQ` and a
  nonzero `Frequency_imag`.
- [ ] The copyright/banner text inside `Version` does not break parsing (it
  currently does not — a garbage first key is produced and ignored; assert the
  `Mode` sections are still found).
- [ ] `extract` returns a scalar under `at: {mode: 0}` and a full array without it.
- [ ] Dry-run returns the NaN sentinel rather than raising.
- [ ] `python -m pytest tests/` green; baselines unchanged (no example migrated).

### Deliverables

- [ ] `Omega3P.output_parser` + `results_dir` in `src/lume_ace3p/ace3p.py`.
- [ ] `Omega3PModule.extract` / `field_index` / `field` in `src/lume_ace3p/modules.py`.
- [ ] Tests in `tests/test_ace3p.py` + `tests/test_modules.py`.

---

# Phase 2 — Command dispatch, `requires` split, defect fixes

**Objective:** Make every CW23 acdtool command invocable and unblock
`[cubit, t3p, acdtool]`. No parsing changes beyond the input-dialect routing —
output parsing is Phase 3.

### Approach

1. `Acdtool.run()`: take the command from an explicit argument instead of
   inferring from the file extension. Support both input-file forms
   (`postprocess rf <f>.rfpost`, `postprocess track3p <f>.acdtool <level>`) and
   the positional forms (`postprocess transwake <dir> x1 y1 x2 y2`,
   `postprocess coaxsignal <dir>`, `postprocess volmontomode <dir>`,
   `mesh deform <in> <out> <scale>`). Keep extension inference as the default
   when only an input file is given, so existing configs work untouched.
2. Route the input dialect by extension (**defect 2**): `.rfpost` → the existing
   `=` parser; `.acdtool` → `parse_ace3p`. Keep them in separate methods; do not
   try to unify the two dialects.
3. Fix **defect 1**: multi-line `{ ... }` values. Track brace depth in
   `input_parser` and accumulate the value across lines. `write_input` must
   round-trip it. Invert the Phase-0 characterization test.
4. Fix **defect 4**: drop the hardcoded `--nodes=1 --ntasks=1`; follow the
   `ace3p.py` pattern (`-n 1 -c 1`, with the same non-srun guard).
5. `AcdtoolModule`: add `command` and `args` config. Set `self.requires` in
   `__init__` from the command — `rf` → `EM_SOLUTION`, `transwake` /
   `coaxsignal` / `volmontomode` → `TD_SOLUTION`, `track3p` →
   `TRACK3P_PARTICLES`. Inject the results directory for the positional
   commands from the consumed artifact rather than making the user repeat it.
   Raise a clear error on an unknown command, listing the known ones.
6. Fix S3P's hardcoded results dir while in the neighborhood: give `S3P` a
   `results_dir()` reading `JobName` like `T3P`, and have `output_parser` use it.
7. Leave `mesh deform` invocable but **not** wired as a mesh producer (design
   decision 3).

### Verification (Phase 2 done when)

- [ ] `[cubit, t3p, acdtool(command: postprocess transwake)]` validates and
  orders as `cubit → t3p → acdtool`.
- [ ] `[cubit, t3p, acdtool(command: postprocess rf)]` still raises
  `WorkflowValidationError` — the `em_solution` guard must survive for `rf`.
- [ ] `Pillbox.acdtool` parses to populated `EnhancementCounter` / `Trajectory`
  blocks (Phase-0 characterization test inverted).
- [ ] Multi-line `portID = {\n 7\n 8\n}` parses and round-trips through
  `write_input` without loss (characterization test inverted).
- [ ] A `.s3p` fixture with `JobName: custom_results` parses from that directory.
- [ ] Existing `postprocess rf` configs run unchanged with no `command` key.
- [ ] `python -m pytest tests/` green including baselines.

### Deliverables

- [ ] Rewritten dispatch + dialect routing + brace fix in `src/lume_ace3p/acdtool.py`.
- [ ] `command` / `args` / per-command `requires` in `AcdtoolModule`.
- [ ] `S3P.results_dir()` in `src/lume_ace3p/ace3p.py`.
- [ ] Tests in `tests/test_workflow_graph.py` (DAG cases) + `tests/test_modules.py`.

---

# Phase 3 — Shape-driven output parsing

**Objective:** Replace the per-section `if` ladder in `output_parser` with four
readers keyed on the shapes in the Motivation table, and add the curve-file
reader.

### Approach

1. **Mode-indexed reader.** Generalize the near-identical `RoverQ` / `kickFactor`
   bodies into one reader driven by the `ModeID` header row: read the column
   names from the header, then parse rows into
   `{section: {ModeID: {column: value}}}` plus a `ModeIDs` list. This removes the
   hand-indexed `modeline[3]` positional access, which is what makes the current
   code fragile. Covers `RoverQ`, `RoverQT`, `kickFactor`, `VFFT`,
   `ALLFieldAtPoint`. Respect the Phase-0 blocking note: if no real fixture for a
   section exists, keep its current behavior rather than guessing its header.
2. **Surface-indexed reader** for `maxFieldsOnSurface` and
   `powerThroughSurface`. Same blocking note applies.
3. **Curve-file reader.** A header-driven column reader for the `filename` blocks
   — parse the `#`-comment header into column names, then load the numeric block
   into `{column: array}`. Handle the per-mode suffix (`field1_0`, `field1_1`) and
   the `.ec` / `.bc` complex variants. Model it on `parse_wakefield`; return these
   through `Module.field()` as field artifacts, never as table columns.
4. **`[scaling]` reader.** Always emitted, two variants: gradient-normalized
   (`V`, `ga`, `E,B m_factor`) and point-scaled when `gradient < 0` (`Ez from O3P`,
   `Ez scaled to`, `m_factor`). Expose `m_factor` — it is the normalized→physical
   field conversion, and nothing else provides it.
5. **Fix defect 3** as part of this: replace `startswith('}')` end-detection with
   brace-depth tracking, or bound each section at the next `[section]` header, so
   the unclosed `[scaling]` in the S3P fixture cannot swallow what follows.
6. Grid blocks (`FieldMap`, `fieldOnSurface`, `fieldOn2DBoundary`) — record the
   produced filenames as artifacts; **defer** binary/mesh parsing. Note the
   deferral in the docstring so it does not read as an oversight.

### Verification (Phase 3 done when)

- [ ] All five Phase-0 `rfpost.out` fixtures parse; `[RoverQ]` values match the
  Phase-0 characterization values exactly (this is a refactor of a working
  parser — the numbers must not move).
- [ ] The unclosed-`[scaling]` fixture parses without corrupting the following
  section.
- [ ] `field1_0` / `.ec` / `.bc` load with correct column names and array lengths.
- [ ] `[scaling]` `m_factor` extracted from both variants.
- [ ] Sections still genuinely unimplemented raise or warn with the section name,
  never silently return empty.
- [ ] `python -m pytest tests/` green including baselines.

### Deliverables

- [ ] Shape readers in `src/lume_ace3p/acdtool.py` (consider splitting the curve
  reader into a module-level function, as `parse_wakefield` is, so it is testable
  without an `Acdtool` instance).
- [ ] `AcdtoolModule.field()` returning curve/grid artifacts.
- [ ] Tests in `tests/test_acdtool_fixtures.py`.
- [ ] Updated `COVERAGE.md`.

---

# Phase 4 — Output-spec migration

**Objective:** Introduce the mapping form with `at:` narrowing, make acdtool's
mode axis a real field index, and keep the list form as a deprecated alias.

### Approach

1. `AcdtoolModule.extract`: accept `{section, quantity, at: {mode|surface: n}}`.
   Without `at:`, return the full index-aligned array; with it, the scalar.
2. `AcdtoolModule.field_index()`: return `('ModeID', array)` when a mode-indexed
   section is among the declared outputs, and `None` otherwise. Honor design
   decision 2 — **no `index:` config key**. Cross-module collisions fall out of
   DAG order for free; intra-module, `ModeID` is the only axis acdtool ever
   offers, and surface-indexed sections require `at: {surface: n}`. Raise a clear
   error listing the available surface IDs if `at:` is missing on one.
3. Deprecated alias: rewrite `['RoverQ', '0', 'RoQ']` →
   `{section: RoverQ, quantity: RoQ, at: {mode: 0}}` in one adapter, with a
   `DeprecationWarning` naming the replacement. Same for `kickFactor` and
   `maxFieldsOnSurface`. Keep `_infer_output_module`'s list branch delegating to
   the adapter so there is exactly one translation site.
4. Pin both index collisions with tests (the case most likely to regress
   silently): an `s3p + acdtool` chain keeps S3P's `Frequency` as the table axis
   with acdtool mode data as a field artifact, and an `omega3p + acdtool` chain
   requesting `RoverQ` plus `maxFieldsOnSurface` puts `ModeID` on the table with
   `Emax` as an `at:`-narrowed scalar column.
5. Do **not** migrate example YAMLs here — Phase 6.

### Verification (Phase 4 done when)

- [ ] Mapping and list forms produce identical values for the same quantity on
  the same fixture.
- [ ] List form emits `DeprecationWarning` naming the mapping replacement.
- [ ] A mode-indexed acdtool output with no `at:` yields one table row per mode.
- [ ] An `s3p + acdtool` workflow indexes its table on S3P's `Frequency` (first in
  DAG order), exactly as it does today, with acdtool mode data as a field artifact.
- [ ] An `omega3p + acdtool` workflow requesting both `RoverQ` and
  `maxFieldsOnSurface` indexes on `ModeID`, with `Emax` an `at:`-narrowed scalar.
- [ ] Requesting a surface-indexed section **without** `at:` raises an error that
  names the available surface IDs.
- [ ] `python -m pytest tests/` green; **baselines byte-identical** — no example
  migrated yet, so any baseline movement here is a bug.

### Deliverables

- [ ] Mapping-form `extract` + `field_index` + `field` in `AcdtoolModule`.
- [ ] Single list→mapping adapter, reachable from `_infer_output_module`.
- [ ] Tests in `tests/test_modules.py` + `tests/test_workflow_graph.py`.

---

# Phase 5 — S3P output completion

**Objective:** Stop discarding S-parameter phase, and read the port mode files.
Independent of Phases 2–4 except for the `results_dir` fix landed in Phase 2.

### Approach

1. Parse `SParameter.out` (complex `(real, imag)` pairs) alongside
   `Reflection.out` (magnitudes). Keep the existing `S(m,n)` magnitude keys
   exactly as they are — the baselines depend on them — and add complex data
   under distinct keys (e.g. `S(m,n)_complex`, or paired `_re`/`_im`). Do not
   redefine `S(m,n)`.
2. Replace the `eval()` calls in `output_parser` with `float()` while touching
   this code. `eval()` on solver output is both slow and needless here.
3. Parse `PortRef<n>_<m>.out` (columns `x y Ex Ey Hx Hy`) through the Phase-3
   curve reader and expose via `Module.field()`.
4. If `SParameter.out` is absent (older ACE3P builds), fall back to
   `Reflection.out` alone with a warning rather than raising.

### Verification (Phase 5 done when)

- [ ] `S(m,n)` magnitudes from the `90DegreeBend` fixture are unchanged to full
  precision; baselines unaffected.
- [ ] Complex values satisfy `abs(S_complex) == S_magnitude` within tolerance
  across the fixture — this cross-check is the real test that both files are read
  consistently.
- [ ] `PortRef7_0.out` loads with correct column names.
- [ ] A fixture directory lacking `SParameter.out` warns and still parses.
- [ ] `python -m pytest tests/` green including baselines.

### Deliverables

- [ ] Extended `S3P.output_parser` in `src/lume_ace3p/ace3p.py`.
- [ ] `S3PModule.field()` including port profiles.
- [ ] Tests in `tests/test_ace3p.py`.

---

# Phase 6 — Migrate examples, add new ones, docs

**Objective:** Move the shipped examples onto the mapping form, add the CW23
workflows the rework unlocks, and document the whole surface.

### Approach

1. Migrate `output_parameters` to the mapping form in
   `examples/omega3p_sweep`, `examples/omega3p_ace3p_param_sweep`,
   `examples/omega3p_optimization`, `examples/geant4_dose_single`,
   `examples/geant4_track3p_beta`. Regenerate baselines **only** where the change
   is intentional (e.g. `Mode_freq` now sourced from Omega3P rather than acdtool
   `RoverQ`) and record each regeneration and its reason in the manifest.
2. New example — **`dlwg-pbc` dispersion sweep.** Source:
   `CW23/examples/omega3p/dlwg-pbc`. Sweep the periodic-boundary `Theta` (phase
   advance) as an `input_parameters.ace3p` variable and plot frequency vs `Theta`.
   CW23 ships `-m0`/`-m1` as two hand-run points; this makes it a real sweep.
   Depends on Phase 1 (frequency without acdtool).
3. New example — **`window` S3P + acdtool.** Source: `CW23/examples/s3p/window`.
   The only CW23 case running `postprocess rf` against S3P results
   (`ResultDir = s3p_results`, `FreqScanID = 2`). Exercises the Phase-4 index
   collision on a real workflow. Note the default rfpost template in
   `acdtool.py::make_default_input` hardcodes `omega3p_results` — fix or
   parameterize it here.
4. New example — **T3P transwake.** Source: `CW23/examples/t3p/cavity-half`.
   A `[cubit, t3p, acdtool(transwake)]` chain — the workflow Phase 2 unblocks.
5. Docs: update `docs/yaml_reference.md` (mapping form, `command`/`args`,
   deprecation), `docs/parameter_sweep.md`, `docs/optimization.md`,
   `docs/configuration_by_mode.md`. Add an acdtool reference page carrying the
   command table and the 19-block shape table from this plan's Motivation, so the
   reference data outlives the plan.
6. Add a README to each new example, matching the existing example READMEs.

### Verification (Phase 6 done when)

- [ ] No shipped example YAML uses the list form; `pytest -W error::DeprecationWarning`
  over the examples is clean.
- [ ] All three new examples run in dry-run mode and are frozen into
  `tests/baseline/`.
- [ ] Every baseline regeneration is recorded with a reason in its manifest.
- [ ] Docs contain no stale list-form spec; the acdtool reference page lists all
  six commands and all 19 blocks with their implementation status.
- [ ] `python -m pytest tests/` green.

### Deliverables

- [ ] Migrated example YAMLs + regenerated baselines with recorded reasons.
- [ ] `examples/omega3p_dispersion_sweep/`, `examples/s3p_window_rfpost/`,
  `examples/t3p_transwake/` (+ READMEs).
- [ ] `docs/acdtool_reference.md`; updates to the four docs pages above.
- [ ] Status line in this file set to COMPLETE.

---

## Execution notes for fresh-context sessions

- **Read this file first.** Execute **one phase per session**; do not start a
  phase before its predecessor's verification passes. **Phase 0 must run before
  Phase 3** — the characterization fixtures cannot be captured once the parser
  starts changing.
- Phases 1 and 5 are **independent** of the acdtool work and of each other. If a
  session is short, either is a safe standalone unit. Phase 1 is the highest
  value-per-effort item in the plan.
- Update the **Status** line and check off verification bullets as they pass;
  note deviations inline, as `workflow_module_refactor_plan.md` does.
- **This plan is additive, not a clean break** — the opposite of the module
  refactor's policy. Keep the list-form spec and extension-inferred `postprocess
  rf` working throughout. Baselines should stay byte-identical until Phase 6,
  which is the only phase permitted to regenerate them.
- **`/home/dbizzoze/CW23` is outside the repo and not version-controlled.** Copy
  what you need into `tests/fixtures/` in Phase 0 rather than reading from it at
  test time. Ignore `a3pi/`, `A3PI_config_single/`, `workflow_test_single/`.
- **Do not attempt TEM3P or named artifacts** (design decision 3). If a phase
  seems to need two producers of the same artifact kind, stop and write down the
  case rather than loosening `_resolve_order`.
- The user runs all pushes and merges manually — hand over commands, do not offer
  to push. `dev` is long-lived; PRs into it use merge commits only.
- Verify before recommending: this plan names specific functions
  (`_infer_output_module`, `_resolve_order`, `parse_wakefield`,
  `T3P.results_dir`) that were current as of 2026-08-13.
