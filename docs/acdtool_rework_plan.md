# Acdtool Rework + Output-Spec Migration — Implementation Plan

**Status: PHASES 0–4 COMPLETE** (Phases 0–1 2026-08-13, Phases 2–4 2026-08-17).
Phases 5–6 not started. Planned 2026-08-13 from a cross-reference of the CW23
ACE3P tutorial archive against the current module layer, then **revised
2026-08-13 against the acdtool user guide** (see below), which corrected several
assumptions — read "Revision: the acdtool user guide" before starting any phase.
Phase 0 made no `src/` changes; all four originally confirmed defects were
reproduced against real files and are now pinned by characterization tests in
`tests/test_acdtool_fixtures.py`. Phase 1 is the first phase to touch `src/`:
Omega3P now parses its own eigenmode output, so a mode frequency or Q no longer
requires an acdtool `RoverQ` step. It changed no example and no baseline.
Phase 2 opened the command surface: all 19 acdtool commands are in one
declarative table, four are wired as workflow steps, `[cubit, t3p, acdtool]` now
validates, and defects 1, 2 (narrowed), 4 and 6 are fixed. It changed no example
and no baseline either.
Phase 3 did the same for the *output* side: all 24 `.rfpost` blocks are in a
second declarative table keyed on output *shape*, one reader per shape replaces
the three hand-written section parsers, `[scaling]` and the curve files are read
for the first time, and defect 3 is fixed. Every `[RoverQ]` number is unchanged
and no baseline moved.
Phase 4 turned acdtool's mode index into a real axis: output specs take the
mapping form with `at:` narrowing, a mode-indexed section with no `at:` now
returns every mode (and a `ModeID` field index to align it), surface-indexed
sections require `at: {surface: n}`, and the positional list form is a deprecated
alias that rewrites to the mapping in one place. No example was migrated
(Phase 6) and no baseline moved.

This plan reworks how `acdtool` is invoked and parsed, and migrates
`output_parameters` off the positional `['section', 'mode_id', 'column']` list
form onto the explicit mapping form already used by S3P/T3P objectives.

---

## Revision: the ACE3P command references

**Primary references: `references/*-commands.pdf`** — eight SLAC ACD documents
covering `acdtool` (32 pp), `omega3p` (15), `s3p` (13), `t3p` (25), `track3p`
(16), `pic3p` (16), `gun3p` (17) and `TEM3P` (24). **Committed to the repo
2026-08-13** (publicly available material); see `references/README.md` for
provenance and the text-extraction recipe. None were available when this plan was
first written — the original Motivation was reverse-engineered from CW23 batch
scripts and input files alone, which is why the corrections below exist.

**The whole document set validates this rework's premise.** Every solver
reference ends with the same line: *"Note: Refer to acdtool command syntax for
postprocessing capabilities."* — omega3p, s3p, t3p, track3p, pic3p and TEM3P all
say it, and none of them document their own output formats. acdtool is *the*
postprocessing layer for all of ACE3P, so it is more central than this plan
claimed, not less. **No phase is invalidated and none needs restructuring.**

### What the acdtool guide changes, in order of consequence

1. **The command surface is 19 commands, not 6** — see the table below. CW23
   itself uses **9**, two of which (`postprocess pic3pconvert`,
   `postprocess pic3pstats`) this plan missed entirely.
2. **`postprocess track3p`'s second argument was misread.** It is `<jobname>`,
   not a field level. See defect 6.
3. **`transwake` overwrites the artifact T3P already provides**, and
   `parse_wakefield` already reads the result. See defect 7 — this makes the
   transwake work *smaller* than planned, but reorders it.
4. **The `.rfpost` format has 24 blocks, not 19**, in five shapes rather than
   four, and several output filenames are not what this plan assumed. See "The
   `.rfpost` format" below.
5. **Only `postprocess rf` and `postprocess volmontomode` run in parallel;** the
   other 17 are serial. This is the authoritative answer to defect 4.
6. **The guide documents inputs, not outputs.** It gives the complete input
   schema for every block — so Phase 2/3 need no longer guess at input keys — but
   shows almost no output samples. **The Phase 0 blocking note therefore still
   stands unchanged:** the guide does *not* tell us what `kickFactor` or
   `maxFieldsOnSurface` print, which remains Phase 3's largest risk.

### What the solver guides change

7. **`JobName` is undocumented for every solver this plan touches** — the
   highest-consequence finding of the second pass. See "JobName is not an input
   key" below. It affects Phase 1 and Phase 2.
8. **Defect 7 is confirmed from the T3P side**, and extends to `wake_new` /
   `wake_direct`: the T3P reference says the acdtool wake commands write
   `t3p_results/OUTPUT/wakefield.out`, "where the file name 'wakefield' has been
   specified in Monitor". The overwrite is by design.
9. **`postprocess track3p` largely duplicates Track3P's own `Postprocess`
   container**, which has the same `EnhancementCounter` with the same keys. Only
   the `Trajectory` block (explicit `ParticleID` selection) and `OutputFile` are
   unique to the acdtool route. This *shrinks* Phase 2 — see the scope table.
10. **`mesh deform` duplicates TEM3P's `MeshDump`**, which has `MeshDeformScale`
    and writes the deformed vacuum mesh straight into `EMMeshInputDir`. The
    acdtool guide itself calls `mesh deform` a visualization convenience for small
    deformations. Reinforces design decision 3 and gives a cleaner route if the
    TEM3P chain is ever attempted.
11. **S3P's reference documents no output files at all** — no `Reflection.out`,
    `SParameter.out` or `PortRef<n>_<m>.out`. Phase 5 rests entirely on the
    Phase-0 fixtures. Not a blocker, but they are the only source.
12. **PIC3P and Gun3P are now fully specified but remain OUT OF SCOPE**
    (user decision, 2026-08-13: record as future work only, no phase committed).
    PIC3P is structurally close to T3P (`ModelInfo`, `FiniteElement`, `PRegion`,
    `Loading`, `TimeStepping`, `Monitor`, `LinearSolver`, `CheckPoint`), so a PIC3P
    module would be a small lift and would give `pic3pstats` / `pic3pconvert`
    somewhere to live — the natural next module after this plan. Gun3P is
    structurally different (`DCGunProblem`, `ElectrostaticProblem`,
    `MagnetostaticProblem`, `Tracker`, `Gun3pOutputConverter`) and a larger lift.
    Note both in `docs/acdtool_reference.md` (Phase 6) so the gap is visible; do
    not start either here.

### `JobName` is not an input key (affects Phases 1 and 2)

**No solver reference documents a `JobName` container.** The complete top-level
command lists are:

| Solver | Top-level containers |
|---|---|
| Omega3P | `ModelInfo`, `FiniteElement`, `PRegion`, `EigenSolver`, `Port`, `PostProcess` |
| S3P | `ModelInfo`, `FiniteElement`, `FrequencyScan`, `Port`, `Loading`, `LinearSolver`, `PostProcess` |
| T3P | `ModelInfo`, `FiniteElement`, `PRegion`, `LoadingInfo`, `Loading`, `TimeStepping`, `Monitor`, `LinearSolver`, `CheckPoint` |
| Track3P | `TotalTime`, `ParticlesTrajectories`, `FieldScales`, `NormalizedField`, `Emitter`, `Domain`, `Material`, `OutputImpacts`, `SingleParticleTrajectory`, `Postprocess` |
| PIC3P | `ModelInfo`, `FiniteElement`, `PRegion`, `Loading`, `TimeStepping`, `Monitor`, `LinearSolver`, `CheckPoint` |

`JobName` appears in exactly one reference — **gun3p**, and there it sits *inside*
the `Tracker` container (`JobName: ./gun3p_results/OUTPUT`) with the note *"Make
sure it's the same name used in the job submission batch file."* The acdtool guide
agrees: `ResultDir` is *"the 'Jobname' specified in the batch job submission
script."*

Corroborating from the data: **no CW23 input file of any type sets `JobName`** —
verified across every `.omega3p`, `.s3p`, `.t3p` and `.track3p` in `examples/`.
Every one relies on the per-solver default.

So `T3P.results_dir()`'s `get_leaf('JobName')` lookup at
`src/lume_ace3p/ace3p.py:424` **has never been exercised by real data.** It works
because it falls through to `default_job_name = 't3p_results'`. There is one piece
of counter-evidence — `t3p_results/OUTPUT/postprocess.in`, the KVC echo T3P writes
of its own resolved input, does contain `JobName : t3p_results` — which suggests
the solver has an internal JobName a `.t3p` file might be able to set. That is
inference, not documentation.

**Consequences:**

- Phase 1 should *not* frame Omega3P's `results_dir()` as "resolve `JobName` from
  the input tree the way T3P does". The **default is the authoritative path**; an
  input-tree `JobName` is a best-effort override.
- Phase 2's verification bullet "a `.s3p` fixture with `JobName: custom_results`
  parses from that directory" would pin behavior that may not match the solver. If
  s3p ignores an input-file `JobName`, a real run writes to `s3p_results/` while
  our code looks in `custom_results/` and raises `FileNotFoundError`.
- **The reliable mechanism is a module-level YAML key** (`results_dir:` or
  `job_name:`), because that is how the directory is really chosen — in the batch
  script, outside the input file. Add it alongside the `JobName` lookup rather
  than instead of it, and keep the lookup as a harmless fallback.
- Do not "fix" this by deleting the `JobName` lookup; it costs nothing and may be
  real. Fix the *framing* and the *test*.

---

## Motivation

The `acdtool` wrapper was built for exactly one command against exactly one
Omega3P section. The tool is far broader, and several of the gaps are live bugs
rather than missing features.

### Reference data: what acdtool actually is

Sources: `acdtool-commands.pdf` for the command surface; CW23
(`/home/dbizzoze/CW23`, `examples/` and `exercises/` — near-duplicate trees;
ignore the `a3pi/`, `A3PI_config_single`, `workflow_test_single/` subtrees, which
belong to a defunct ancestor project) for real call sites and data.

acdtool exposes **19 commands**: three top-level, five `mesh` subtasks and eleven
`postprocess` subtasks. The wrapper supports one. "CW23" counts invocations
across every batch script in `examples/` and `exercises/`.

| Command | Input form | Consumes | CW23 | Before Phase 2 | After Phase 2 |
|---|---|---|---|---|---|
| `meshconvert <f>.gen [out.ncdf]` | positional | genesis mesh | 50 | yes, but inside `cubit.py`, not `acdtool.py` | invocable; still produced by `cubit.py` |
| `meshconvertdirect <f>.gen [out.ncdf]` | positional | genesis mesh | — | **no** | invocable; unwired (mesh producer) |
| `resource <f>.omega3p` | positional | Omega3P input | — | **no** | invocable; unwired (stdout only) |
| `mesh stats <f>.ncdf` | positional | mesh | (implicit) | **no** | invocable; unwired (stdout only) |
| `mesh check <f>.ncdf` | positional | mesh | (implicit) | **no** | invocable; unwired (stdout only) |
| `mesh fix <in>.ncdf <out>.ncdf` | positional | mesh | — | **no** | invocable; unwired (mesh producer) |
| `mesh deform <in>.ncdf <out>.ncdf <scale>` | positional | TEM3P deformed mesh | 2 | **no** | invocable; unwired (mesh producer) |
| `mesh warpsurface <warp.in>` | `warp.in` file (**third dialect**) | mesh | — | **no** | invocable; unwired (`warp.in` passed opaquely) |
| `postprocess rf <f>.rfpost` | `.rfpost` file (`=` dialect) | Omega3P **or** S3P results | 16 | yes | **wired** (`em_solution`) |
| `postprocess eigentomode <jobname>` | positional | Omega3P/S3P results | — | **no** | invocable; unwired (`.mod` files only) |
| `postprocess volmontomode <jobname>` | positional | T3P/PIC3P results | 2 | **no** | **wired** (`td_solution`) |
| `postprocess wake_new <jobname> <x y>` | positional | T3P results | — | **no** | invocable; unwired (no fixture) |
| `postprocess wake_direct <jobname> <x y>` | positional | T3P results | — | **no** | invocable; unwired (no fixture) |
| `postprocess transwake <jobname> <x1 y1> <x2 y2>` | positional | T3P results | 2 | **no** | **wired** (`td_solution`) |
| `postprocess coaxsignal <jobname>` | positional | T3P results | 2 | **no** | **wired** (`td_solution`) |
| `postprocess pic3pstats <f>.ncdf <symmetry factor>` | positional | PIC3P particles | 1 | **no** | invocable; unwired (no PIC3P module) |
| `postprocess pic3pconvert <f>` | positional | PIC3P particles | 2 | **no** | invocable; unwired (no PIC3P module) |
| `postprocess track3p <f>.acdtool <jobname>` | `.acdtool` file (`:` dialect) + jobname | Track3P results | 2 | **no** | **not invocable** (needs the `:` dialect) |
| `postprocess project <eigenmodes> [displacements]` | positional | TEM3P results | — | **no** | invocable; unwired (TEM3P, stdout only) |

Notes that matter for dispatch:

- **`<jobname>` is a name, not a path.** Every positional `postprocess` command
  takes the solver's `JobName` (defaulting per solver: `omega3p_results`,
  `s3p_results`, `t3p_results`, `pic3p_results`, `track3p_results`). This is the
  same resolution `T3P.results_dir()` already does, and the reason Phase 2 also
  gives `S3P` a `results_dir()`.
- **`mesh stats` and `mesh check` are run internally by `meshconvert`** — CW23
  never calls them directly, but their output appears in `meshconvert` logs.
- **`mesh warpsurface` introduces a third input dialect**: flat `Key: value`
  lines with no braces (`File:` / `ExteriorBoundary:` / `WarpFile:`), distinct
  from both `.rfpost` (`=`) and KVC (`:` with braces). Recommend scoping it out.
- **`resource` and `project` write to stdout / `acdtool.log`**, not to a
  structured output file.

Call sites, for a fresh session that wants to see the real scripts:

- `postprocess rf` — `examples/omega3p/*/run-acdtool.batch`, `examples/s3p/window/run-acdtool.batch`
- `postprocess transwake` — `examples/t3p/cavity-half/run-t3p.batch` (and `half-model/`, `multi-beam/`)
- `postprocess coaxsignal` — `examples/t3p/BPM/run-t3p.batch`
- `postprocess volmontomode` — `examples/t3p/SIBC/run-t3p.batch`
- `postprocess track3p` — `examples/track3p/Pillbox/run-acdtool.batch` + `Pillbox.acdtool`
- `postprocess pic3pconvert`, `postprocess pic3pstats` — `examples/pic3p/LCLSGun/run-post.sl`
- `mesh deform` — `examples/tem3p/RfGun-Coupler/run-scale-deformed-mesh.sh`

### Serial vs parallel

The guide states: *"acdtool submodules run serially with the exception of acdtool
postprocess volmontomode and acdtool postprocess rf."* CW23's own invocations
match — `srun -n 1 -c 1` for `rf`, `srun -n 1 -c 256` for `transwake`, `srun -n
1` for `volmontomode` and `track3p`. So the launcher is always one rank in
practice, and only `rf`/`volmontomode` could ever use more. Phase 2's defect-4
fix should therefore emit one rank for every command and treat `rf` /
`volmontomode` as the only candidates for a configurable rank count.

### The `.rfpost` format has 24 blocks; the parser implements 3

`acdtool.py::output_parser` implements `RoverQ`, `kickFactor`,
`maxFieldsOnSurface`; every other block prints `"parsing not implemented"`.

**Two block sets, and they disagree.** The CW23 `.rfpost` template has 19
sections (`RFField` + 18 postprocess blocks). The guide lists 20 functionalities
and documents a 21st (`RoverQRoverQT`) in its body without listing it. Neither is
a superset:

- In CW23 but **absent from the guide**: `Track`, `TrackScan`, `coaxPort`.
- In the guide but **absent from CW23's template**: `pointRoverQ`, `dFSlater`,
  `RoverQRoverQT`, `IMPACTMap`, `OpenPMD_IMPACT`.

The union is **24 blocks** (`RFField` + 23 postprocess). CW23's template is from
an older acdtool build than the guide, and CW23 shipped blocks the guide dropped.
**Consequence for Phase 2: the input parser must tolerate unknown blocks rather
than enumerate a fixed list**, in both directions — a newer template will carry
blocks we have never seen, and an older one carries blocks the guide forgot.

`acdtool postprocess rf` with **no arguments** writes a `sample.rfpost` template
for the installed build. That is the correct source for
`make_default_input` — see Phase 6 item 3.

The 24 blocks collapse into **five shapes**:

| Shape | Index axis | Blocks | Written to |
|---|---|---|---|
| Mode-indexed table | `ModeID` | `RoverQ`, `RoverQT`, `RoverQRoverQT`, `kickFactor`, `pointRoverQ`, `dFSlater`, `VFFT`, `ALLFieldAtPoint`, `coaxPort` | `rfpost.out` |
| Surface-indexed scalars | `surfaceID` | `maxFieldsOnSurface`, `powerThroughSurface` | `rfpost.out` |
| Single-mode scalars | — (uses `RFField`'s `ModeID`) | `FieldAtPoint` | `rfpost.out` |
| Column curve files | position / phase | `FieldOnLine`, `ALLFieldOnLine`, `Multipole`, `GBZFFT`, `Track`, `TrackScan` | **separate files** |
| Grid / mesh | — | `FieldMap`, `IMPACTMap`, `OpenPMD_IMPACT`, `fieldOnSurface`, `fieldOn2DBoundary` | separate files |
| Run-level scalars | — | `[scaling]` (always emitted, never declared) | `rfpost.out` |

`FieldAtPoint` is its own shape: it is a valid container, but unlike
`ALLFieldAtPoint` it has no `modeID1`/`modeID2` and evaluates only the single
mode named in `RFField`. It has no index axis at all, so it resolves to scalars
directly — the same treatment design decision 2 gives surface-indexed sections,
but without needing an `at:`.

Blocks with **`modeID1`/`modeID2`** are the mode-indexed ones, and the guide
pins the semantics this plan's `at:` design depends on: `modeID1 = -1` means mode
0, `modeID2 = -1` means *all modes the solver produced*. So the CW23 default
`-1 / -1` already means "every mode" — which is exactly the case the mapping form
without `at:` is for, and confirms the plan's read that the middle element of
`['RoverQ', '0', 'RoQ']` is an index axis rather than a selector.

**Curve/grid output filenames are not uniform.** The plan originally assumed
"a block with a `filename` key writes `<filename>` per mode". Per the guide:

| Block | Writes |
|---|---|
| `FieldOnLine` | `<filename>.e` and `<filename>.b` (real fields at `rfphase`), `<filename>.ec` / `<filename>.bc` (complex) |
| `ALLFieldOnLine` | `<filename>_<modeID>` (E **and** B together, plus `Sz`), `<filename>_<modeID>.ec` / `.bc` |
| `FieldMap` | **fixed** names `Efield-map.dat` / `Bfield-map.dat` — no `filename` key at all |
| `IMPACTMap` | `EBfield-map-<filename>.dat`, IMPACT format |
| `OpenPMD_IMPACT` | `E_Real.h5`, `E_Imag.h5`, `B_Real.h5`, `B_Imag.h5` — **HDF5**, not text |

So `FieldOnLine` and `ALLFieldOnLine` use *different* naming schemes and
different column sets: the Phase-0 fixture `curves/field1_0` is the
ALLFieldOnLine form (10 columns, `x y z Ex Ey Ez Bx By Bz Sz` — no separate
`.e`/`.b`), while `FieldOnLine` splits E and B into two files. Phase 3 needs both
and cannot infer one from the other.

They also differ in **scaling**, which matters for interpreting the numbers:
`FieldOnLine` fields are scaled to `RFField`'s `gradient`; `ALLFieldOnLine`
fields come straight from the eigenmode, normalized to total stored energy. That
is the same distinction `[scaling]`'s `m_factor` records.

The curve blocks remain the easy ones — a `#`-commented column table with a
header row, the shape `ace3p.py::parse_wakefield` already handles — but one
reader covers all six only if it is driven by the header row rather than by an
assumed filename pattern.

The genuinely fiddly parsing is confined to `rfpost.out`.

### Other input semantics the guide pins down

- **`z1`/`z2`/`gz1`/`gz2` above `1e6` are sentinels**, not coordinates: they mean
  "use the minimum/maximum z of the computational domain". This is why CW23
  writes `z1 = 100000000.00000` — it is not a 100 m integration path. Anything
  that validates, sweeps or rescales these values must not treat them as lengths.
- **`powerThroughSurface` output is complex** (unit W), real part being the
  average power flow from the complex Poynting vector. The target design's
  `'P_out': {..., quantity: Power, at: {surface: 6}}` therefore needs the same
  real/imag treatment as Omega3P's `Frequency`, not a plain scalar.
- **`VFFT` has a `printGroup` key** (`nterm` | `ModeID`) that changes how its
  results are *grouped in the output* — by multipole component or by mode. A
  mode-indexed reader for `VFFT` must handle both groupings or reject the one it
  does not implement, naming `printGroup`.
- **`gradient = -1` means "no scaling"**, which is what selects the point-scaled
  `[scaling]` variant seen in the `s3p/window` fixture.
- **`ModeID` in `RFField` means different things per solver**: eigenmode index for
  Omega3P, port-mode (excitation) index for S3P, ordered by port then mode.

### Confirmed defects (all reproduced against real CW23 files)

1. **Multi-line brace values are silently dropped.** `coaxPort` is the one block
   designed to hold lists (`portID`, `porta`, `portb`). CW23 ships them empty
   (`portID = {  }`), which round-trips. Filled in the natural multi-line way,
   `input_parser` stores `portID = '{'`, discards the contents, and the stray `}`
   closes the block early — no error raised. **The write side is broken too:**
   `write_input` emits the truncated `portID = {` verbatim, producing a file with
   unbalanced braces that acdtool cannot read. Both halves are pinned by
   `test_defect1_multiline_brace_value_is_truncated` and
   `test_defect1_roundtrip_writes_unbalanced_braces`; Phase 2 must fix the writer
   so it always emits a structurally valid file, not only the reader.

2. **`.acdtool` files use a different dialect and parse to nothing.**
   `.rfpost` is `key = value`; `.acdtool` (for `postprocess track3p`) is KVC
   `key : value`, the same dialect as solver input files. `Acdtool` splits on
   `=`, so `Pillbox.acdtool` parses to
   `{'EnhancementCounter:': {}, 'Trajectory:': {}}` — two empty blocks, silently.
   `ace3p.py::parse_ace3p` reads the same file correctly.

   **Revised treatment (2026-08-13):** with `postprocess track3p` demoted to
   table-row-only, Phase 2 does **not** route the KVC dialect. The fix becomes
   *"fail loudly instead of silently"* — detect a `.acdtool` input and raise a
   clear error naming the unsupported command, rather than parsing to two empty
   blocks and proceeding. The silent-empty-parse behavior is the actual defect;
   the dialect support is a feature that can land when
   `Trajectory`/`ParticleID` extraction is wanted. `parse_ace3p` is ready when it
   is. The Phase-0 characterization test stays a characterization test — do not
   invert it; add a new test for the raised error.

3. **`[scaling]` ships unclosed in the S3P case.** In
   `examples/s3p/window/rfpost.out` the `[scaling]` block has no closing `}`,
   which makes the `startswith('}')` end-detection in `output_parser` unreliable
   for anything following it.

4. **`--nodes=1 --ntasks=1` is srun-only.** `acdtool.py` hardcodes it. `ace3p.py`
   guards `--cpu-bind` against non-srun callers; `Acdtool` has no equivalent, so a
   non-srun `MPI_CALLER` breaks the step. CW23 itself uses `srun -n 1 -c 1`.
   Worse for the unsupported commands: `run()` with a `.acdtool` input launches no
   subprocess **and never sets `self.output_file`**, so a later `load_output()`
   raises `AttributeError` rather than reporting a missing output
   (`test_run_rejects_unknown_extension`).

5. **Two of the three implemented parsers are unvalidated.** Grepping every
   `.out` in CW23 for section headers yields only `[scaling]` (15×) and
   `[RoverQ]` (11×) — no CW23 run ever enabled `kickFactor` or
   `maxFieldsOnSurface`. Current tests use hand-written fixtures
   (`tests/test_modules.py::RFPOST_OUTPUT`). `examples/omega3p_sweep` depends on
   `maxFieldsOnSurface` for `E_max`, so that path rests on assumed format.
   **The user guide does not close this gap** — it specifies inputs, not output
   formats. Phase 3's blocking note stands.

### Defects found in this plan itself (from the user guide, 2026-08-13)

6. **`postprocess track3p`'s second argument is `<jobname>`, not a field level.**
   This plan's command table read `postprocess track3p <f>.acdtool <level>` and
   glossed it as "`.acdtool` file + field level". The guide gives
   `acdtool postprocess track3p <inputfile.acdtool> <jobname>`, and CW23's call
   site settles it: `acdtool postprocess track3p Pillbox.acdtool 2.3MV`, where
   `2.3MV` is a **directory** — `examples/track3p/Pillbox/2.3MV/` holds
   `ImpactsInfo_2.3e+07`, `InputParameters`, `OUTPUT/`, `PARTICLES/` and `en`
   (the `EnhancementCounter` output, which the guide says is "dumped under
   `./jobname/`"). The example simply names its jobname after the field level it
   was run at, which is what made the misreading plausible.

   Had Phase 2 been implemented from the old table it would have added an `args`
   entry for a field level that does not exist, and passed the results directory
   nowhere. Instead `track3p` takes the same injected-jobname treatment as the
   other positional commands.

7. **`transwake` overwrites the artifact T3P already provides, and
   `parse_wakefield` already reads it.** This plan claimed `T3PModule` "reads
   `wakefield.out` directly, which covers the built-in monitor but not this path".
   That is wrong. `transwake` writes its result *to the same file*
   (`<jobname>/OUTPUT/wakefield.out`) with a transverse header
   (`# Kick factor = ...`, columns `s`, `W_trans(s)[V/pC]`, `I_bunch(s)[C/m]`),
   and `ace3p.py::_FACTOR_KEYS` already handles `'kick factor'` — verified against
   `examples/t3p/cavity-half/`, which yields
   `{KickFactor, WakeType: 'transverse', TransversePoints, Offset, s, W, I_bunch}`.

   Two consequences, both making the work smaller but reordering it:

   - **`AcdtoolModule` needs to parse nothing at all for `transwake`.** It runs
     the command; `T3PModule` reads the result. The same likely holds for
     `wake_new` / `wake_direct`, which the guide says also write
     `<jobname>/OUTPUT/wakefield.out` with a loss factor.
   - **But T3P's `output_parser` must then run *after* acdtool**, which inverts
     the normal producer→consumer order: the consumer mutates the producer's
     output in place. A `[cubit, t3p, acdtool(transwake)]` chain resolved in DAG
     order will have T3P parse `wakefield.out` *before* transwake overwrites it,
     and silently return the longitudinal result. **Phase 2 must decide this
     explicitly** — either re-parse the producer after a mutating consumer, or
     have `AcdtoolModule` own the post-transwake parse and provide it as a
     distinct artifact. Do not leave it to ordering luck. Note `coaxsignal`
     is *not* affected: it writes a new file (`<jobname>/OUTPUT/signal.out`,
     columns `t V I`, **no header row**).

   The T3P reference confirms this independently and extends it to `wake_new` /
   `wake_direct`, which write the same path. One inconsistency to leave alone:
   the T3P reference gives the wakefield monitor's columns as `W(s)` in **V/C**
   and `I(s)` in **A/m**, while the real file header says `V/pC` and `C/m`. The
   file header is authoritative and `parse_wakefield` reads it, so no code change
   — but do not "correct" the units to match the document.

8. **`postprocess track3p` mostly duplicates Track3P's own `Postprocess`
   container.** The Track3P reference documents
   `Postprocess: {Toggle, ResonantParticles: {...}, EnhancementCounter: {...}}`
   with the same `EnhancementCounter` keys (`Token`, `SEYFileName1/2`,
   `BoundarySurfaceID1/2`, `SolidVolID`, `MinimumEC`) that the `.acdtool` file
   carries. CW23's Pillbox example declares it **both ways** — in
   `Pillbox2.3MV.track3p` *and* in `Pillbox.acdtool` — so `2.3MV/en` could have
   been produced by either path.

   Unique to the acdtool route: `OutputFile` (naming the output) and the
   `Trajectory` block's explicit `ParticleID` list. The solver's own
   `SingleParticleTrajectory: on` dumps *every* trajectory to per-ID files, with no
   way to select. So selected-particle trajectory extraction is the only capability
   `postprocess track3p` genuinely adds. Weigh that against the cost of a second
   input dialect before committing it to the implement tier.

### `acdtool` requiring `em_solution` blocks the standard T3P workflow

`AcdtoolModule.requires = {EM_SOLUTION}`, so `[cubit, t3p, acdtool]` is a
`WorkflowValidationError`. But every CW23 T3P batch script runs t3p and acdtool
in the same script, and `transwake`/`coaxsignal`/`volmontomode` are *precisely*
time-domain postprocessors. The rule is too coarse: it is really
"`postprocess rf` needs a frequency-domain solution," not "acdtool does."

`transwake` matters specifically because it is how CW23 gets the transverse wake
out of a half/quarter model. ~~`T3PModule` reads `wakefield.out` directly, which
covers the built-in monitor but not this path.~~ **Corrected (defect 7):**
`T3PModule` already parses the transwake result correctly, because transwake
overwrites the same `wakefield.out` and `parse_wakefield` handles the transverse
header. The blocker is purely that the DAG will not let acdtool run after t3p —
plus the ordering hazard in defect 7.

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
    args    : [0.0, 0.0, 0.0, 0.0125]  # jobname is injected from the artifact
```

**These two entries illustrate the two forms; they are not one runnable
workflow.** Both provide `rf_post`, so declaring both is a duplicate-producer
error — see Phase 2 deviation 7.

`requires` is derived from `command`: `postprocess rf` → `em_solution`;
`transwake` / `coaxsignal` / `volmontomode` / `wake_new` / `wake_direct` →
`td_solution`; `postprocess track3p` → `track3p_particles`.

**Scope the 19 commands explicitly** rather than implementing dispatch for all of
them. With the surface tripled, a hand-written `if`/`elif` ladder in `run()` is
the wrong shape; use one declarative table keyed on command name, carrying: the
argument form (input file / positional / input file + jobname), whether a jobname
is injected, the required artifact, and whether the command is parallel. Then
"unknown command" and "wrong argument count" are one check each, and adding a
command later is a table row. Recommended split:

| Tier | Commands | Rationale |
|---|---|---|
| **Implement in Phase 2** | `postprocess rf`, `transwake`, `coaxsignal`, `volmontomode` | CW23-exercised and mapping to existing artifacts. |
| **Table row, no dialect support** | `postprocess track3p` | **Demoted 2026-08-13** (user decision). Track3P's own `Postprocess: {EnhancementCounter}` already covers the duplicated capability, so the acdtool route buys only selected-particle `Trajectory` extraction — not worth a second input dialect yet. See defect 2's revised treatment: raise a clear error on a `.acdtool` input rather than routing it. |
| **Table row, no module wiring** | `mesh deform`, `meshconvert`, `meshconvertdirect`, `mesh stats`, `mesh check`, `mesh fix` | Invocable, but producing a mesh violates one-producer-per-artifact (design decision 3). `meshconvert` also already lives in `cubit.py` — do not duplicate it. `mesh deform` additionally duplicates TEM3P's own `MeshDump: {MeshDeformScale, EMMeshInputDir}`, which writes the deformed vacuum mesh directly — prefer that route if the TEM3P chain is ever attempted. |
| **Out of scope, recorded** | `wake_new`, `wake_direct`, `eigentomode`, `pic3pstats`, `pic3pconvert`, `project`, `resource`, `mesh warpsurface` | No PIC3P or TEM3P module exists to hang them on (both stay out of scope — decision 12); `warpsurface` needs a third input dialect; `resource`/`project` write only to stdout. Write these down in `docs/acdtool_reference.md` so the gap is visible rather than forgotten. |

`wake_new` / `wake_direct` are the most likely next additions — they are the
longitudinal counterparts of `transwake` and share its output file and its
defect-7 ordering hazard.

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

- [x] `tests/fixtures/acdtool/` holds the files above with provenance in `SOURCES.md`.
- [x] Characterization tests pass against unmodified `src/`.
- [x] `COVERAGE.md` names every block with no real-output fixture.
- [x] `python -m pytest tests/` still green — 264 passed (38 new).
- [x] `SOURCES.md` records which curve files were truncated and their original
  row counts.
- [x] `du -sh tests/fixtures/` within budget: **131 KB of fixture data
  (134,697 B)**, 146 KB with `SOURCES.md` + `COVERAGE.md`. The "~50 KB" figure was
  unreachable as written; budget revised to **100–150 KB** (user, 2026-08-13).

### Deviations found while executing Phase 0

1. **The ~50 KB size gate contradicted the "keep `field1_0.ec` in full"
   instruction.** That one file is 57,973 B. The plan's other figure ("total
   should land near 20 KB") counted only the truncated curve files and omitted
   the 32 KB of solver outputs the same list asks for. Resolution: the
   substantive instructions were followed (one full-length curve file, real
   `omega3p.out` banners, complete S-parameter tables) and the budget was raised
   to **100–150 KB**, which the measured 131 KB sits inside. Breakdown is in
   `SOURCES.md`. For reference, `tests/baseline/` is 19 KB, so this is the largest
   fixture set in the repo — all plain numeric/KVC text.

2. **Six `rfpost.out` files were copied rather than the five listed.**
   `omega3p/dlwg-pbc/rfpost.out` was added (3.2 KB): it is the only fixture with
   a **negative** `Qext` (`-8.58381e+16`), and Phase 6 plans a `dlwg-pbc` example.
   Not every CW23 example runs rfpost, so the plan's list was a reasonable
   selection — this is an addition, not a correction to it.

3. **`FieldAtPoint` was missing from the shape table in the Motivation** — it is
   a valid acdtool container, distinct from the mode-indexed `ALLFieldAtPoint`.
   **Fixed 2026-08-13:** the table (now 24 blocks) carries it as its own shape,
   *single-mode scalars* — no index axis, since it evaluates only the `ModeID`
   named in `RFField`. Recorded in `COVERAGE.md`.

4. **Defect 1 needed a synthetic fixture.** CW23 only ever ships `coaxPort`
   lists empty (`portID = {  }`), which round-trips cleanly — that is why the
   defect went unnoticed. `rfpost_inputs/coaxport-multiline.rfpost` is
   hand-written (marked SYNTHETIC in both the file header and `SOURCES.md`) and
   is the minimum fixture that exposes it. It also showed a *second* half of the
   defect the plan did not name: `write_input` writes the truncated `portID = {`
   back verbatim, emitting a file with unbalanced braces. Phase 2's round-trip
   fix must cover that, not just the read side.

5. **Defect 4 is worse than "breaks the step" for non-`rf` commands.** `run()`
   with a `.acdtool` input launches no subprocess *and never sets
   `self.output_file`*, so a subsequent `load_output()` raises `AttributeError`
   rather than reporting a missing output. Pinned by
   `test_run_rejects_unknown_extension`.

All four confirmed defects reproduced exactly as described, including the
`{'EnhancementCounter:': {}, 'Trajectory:': {}}` parse of `Pillbox.acdtool` and
the unclosed `[scaling]` in the S3P output.

**No ACE3P environment needed for this phase** — it is fixture copying plus
characterization tests against files already on disk, and makes no `src/` changes.
It runs fully locally.

### Deliverables

- [x] `tests/fixtures/acdtool/` + `SOURCES.md` + `COVERAGE.md`.
- [x] `tests/test_acdtool_fixtures.py` (38 tests). No `src/` changes in this phase.

**Blocking note for Phase 3:** obtain one real acdtool run with `kickFactor` and
`maxFieldsOnSurface` enabled (any Omega3P example plus those two `ionoff = 1`)
before rewriting those two parsers. If a cluster run is not available, Phase 3
must keep the existing parsers byte-compatible and route only the *new* sections
through the new shape readers — do not "clean up" unvalidated parsers blind.
**The acdtool user guide does not lift this** — it documents input schemas, not
output formats. Nothing short of a real run closes it.

### Phase 0 addendum — fixtures the user guide revealed as missing

Phase 0 is complete as specified; these are additional fixtures the guide showed
to exist and that the phases below now depend on. They are all real files already
on disk in CW23, so this is copying, not a cluster run. **Do this at the start of
whichever phase first needs the file**, not as a separate session.

| Fixture | Source | Needed by | Size |
|---|---|---|---|
| `wakefield.out` (transwake form) | `examples/t3p/cavity-half/t3p_results/OUTPUT/wakefield.out` | **Phase 2** — the defect-7 ordering hazard cannot be tested without it. Truncate the 2,335 data rows to ~20; the header is the load-bearing part. | 2.3 KB truncated |
| `signal.out` | `examples/t3p/BPM/t3p_results/OUTPUT/signal.out` | Phase 2/3 — `coaxsignal` output, columns `t V I` with **no header row**, so it needs its own reader rather than the header-driven one. Truncate. | ~2 KB truncated |
| `en` | `examples/track3p/Pillbox/2.3MV/en` | Phase 2/3 — `EnhancementCounter` output; 7 columns with a header row (`fieldlevel ID enhancement averageEnhancement maxEnhancement maxEnhancementImpactNum totalImpactNum`), 658 rows. Truncate. | ~2 KB truncated |
| `postprocess.in` | `examples/t3p/cavity-half/t3p_results/OUTPUT/postprocess.in` | Phase 2 — shows acdtool writes a KVC echo of the T3P input into the results dir. Useful for confirming jobname resolution. | 1.5 KB |

Update `SOURCES.md` and `COVERAGE.md` when these land. The revised size budget
(100–150 KB) accommodates all four truncated.

---

# Phase 1 — Omega3P eigenmode parsing

**Objective:** Give `Omega3P` a real `output_parser` and `Omega3PModule` an
`extract`, so mode frequency / Q come from the solver's own output instead of
requiring an acdtool `RoverQ` step. Independent of every other phase — no
acdtool and no spec changes.

### Approach

1. `Omega3P.output_parser()`: parse `<results_dir>/omega3p.out` with the existing
   `parse_ace3p`, then walk top-level `Mode` sections into
   `output_data['Modes']` — a list of `{Frequency, QualityFactor, ExternalQ,
   TotalEnergy, PowerLoss, File}`. **`omega3p_results` is the authoritative
   default, not a fallback** — the Omega3P reference documents no `JobName`
   container (see "`JobName` is not an input key"), and no CW23 input sets one. Add
   a module-level `results_dir:` YAML key as the supported override, and keep an
   input-tree `JobName` lookup as a harmless best-effort fallback the way
   `T3P.results_dir()` does. Do not hardcode the directory, and do not present the
   `JobName` path as the documented mechanism.
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

- [x] Both Phase-0 `omega3p.out` fixtures parse: real-eigenvalue case yields 2
  modes with `QualityFactor`; complex case yields 1 mode with `ExternalQ` and a
  nonzero `Frequency_imag`.
- [x] The copyright/banner text inside `Version` does not break parsing (it
  currently does not — a garbage first key is produced and ignored; assert the
  `Mode` sections are still found).
- [x] `extract` returns a scalar under `at: {mode: 0}` and a full array without it.
- [x] Dry-run returns the NaN sentinel rather than raising (a **scalar** NaN —
  see deviation 2).
- [x] `python -m pytest tests/` green; baselines unchanged (no example migrated)
  — 282 passed (18 new), up from Phase 0's 264.

### Deviations found while executing Phase 1

1. **`results_dir` resolution landed on the `ACE3P` base class, not on
   `Omega3P`.** The plan scoped it to Omega3P, but T3P already had the same logic
   (`_input_tree` + a `JobName` lookup) and Phase 2 needs it for S3P, so the
   resolution order lives once in `ACE3P.job_name()` (`results_dir` argument →
   input-tree `JobName` → `default_job_name`) with `results_dir()` on top of it.
   T3P's override now just appends `OUTPUT`, which is the one thing that is
   actually T3P-specific. Every solver got its documented `default_job_name`
   (`omega3p_results`, `s3p_results`, `t3p_results`, `track3p_results`); only
   Omega3P and T3P *consult* it so far — S3P's `output_parser` still hardcodes
   `s3p_results/`, so **Phase 2 item 7 is now a one-line change**. The
   `results_dir:` module key is plumbed through `_SolverModule` for all three
   solvers, so Phase 2 inherits it for S3P for free.

2. **`Omega3PModule.field_index` returns `None` under dry-run rather than
   S3P/T3P's single-row sentinel**, and `extract` returns a **scalar** NaN rather
   than `array([nan])`. Two reasons, and they are the same reason: the sentinel
   exists so a *long-format* table keeps one row per grid point, and Omega3P has
   no dry-run axis to be long over. S3P's frequency scan and T3P's `s` range are
   declared in the input file, so their axis is known to exist before the run;
   Omega3P's mode count is a result of the eigensolve. Emitting a sentinel would
   also have added a `ModeID` column to the existing wide `omega3p → acdtool`
   sweep tables under dry-run, which is exactly the baseline movement this phase
   forbids. Consequence for Phase 4: acdtool's `field_index` faces the same
   choice, and the same answer (no sentinel) keeps `examples/omega3p_sweep`
   byte-identical. For a **real** omega3p run the axis *is* returned, so an
   omega3p+acdtool table goes long-format on `ModeID` — which is the end state
   design decision 2 specifies, reached early rather than deferred.

3. **The complex-pair split is detected from the value, not from a key list.**
   The plan named `Frequency` and `TotalEnergy`; the implementation
   (`ace3p.py::_split_pair`) treats any leaf that splits into exactly two
   float-parseable comma parts as a `real , imag` pair, so a future complex leaf
   needs no code change and a comma inside a `File` path cannot be mistaken for
   one. The two known keys are named in that function's docstring.

4. **Two absence semantics, not one.** `parse_omega3p_output` pads a missing
   `_imag` entry with `0.0` (a real eigenvalue *has* a zero imaginary part) and a
   missing `ExternalQ` with `NaN` (genuinely unknown on a portless run). An
   `_imag` array appears only if some mode reported a pair, so the lossless
   fixture yields no `Frequency_imag` key at all.

5. **`output_data` carries both forms.** `'Modes'` is the plan's readable list of
   per-mode dicts; alongside it are `ModeID` plus one index-aligned array per
   quantity, which is what `extract`/`field_index` need. `field()` drops
   `'Modes'` — a list of dicts cannot ride inside a field-artifact `.npz` without
   pickling (`results.load_field` loads with `allow_pickle=False`).

6. **Output specs must name `module: omega3p` explicitly.** `_infer_output_module`
   routes a bare `'Frequency'` string to `s3p` by shape, and Phase 1 deliberately
   does not touch it (Phase 4 retires shape-sniffing). Recorded in the
   `Omega3PModule` docstring; a mis-routed bare spec already fails with "no such
   module is in the workflow".

7. **A docs section was written now rather than deferred to Phase 6.** The plan
   puts all docs in Phase 6, but `results_dir:` and the Omega3P quantity names are
   *new user-facing surface* with no other discoverable home, so
   `docs/yaml_reference.md` gained an `omega3p` module section (quantity table,
   `at: {mode: n}`, the `ModeID` field index, the `results_dir:`/`JobName`
   distinction) alongside the existing `t3p` one, and the `t3p` section's
   output-location paragraph was updated for the shared key. This is additive and
   describes only what Phase 1 shipped — Phase 6 still owns the mapping-form
   migration and the deprecation notes.

### Deliverables

- [x] `Omega3P.output_parser` + `parse_omega3p_output` + base-class
  `job_name`/`results_dir` in `src/lume_ace3p/ace3p.py`.
- [x] `Omega3PModule.extract` / `field_index` / `field` + the `results_dir:`
  module key in `src/lume_ace3p/modules.py`.
- [x] Tests in `tests/test_ace3p.py` (9 new, against the real Phase-0 fixtures) +
  `tests/test_modules.py` (9 new, synthetic — matching that file's convention).

**No ACE3P environment needed for this phase** — the parser runs against the
frozen Phase-0 fixtures and the module tests inject a wrapper whose
`output_parser` is driven directly. It runs fully locally.

---

# Phase 2 — Command dispatch, `requires` split, defect fixes

**Objective:** Make every CW23 acdtool command invocable and unblock
`[cubit, t3p, acdtool]`. No parsing changes beyond the input-dialect routing —
output parsing is Phase 3.

### Approach

1. `Acdtool.run()`: take the command from an explicit argument instead of
   inferring from the file extension, dispatched through **one declarative command
   table** (see "Command dispatch" in the target design) rather than an `if`/`elif`
   ladder — the surface is 19 commands, not 6. Each row carries the argument form,
   whether a jobname is injected, the required artifact, and the parallel flag.
   Support the input-file form (`postprocess rf <f>.rfpost`) and the positional
   forms (`postprocess transwake <jobname> x1 y1 x2 y2`,
   `postprocess coaxsignal <jobname>`, `postprocess volmontomode <jobname>`,
   `mesh deform <in> <out> <scale>`). Keep extension inference as the default
   when only an input file is given, so existing configs work untouched. Record
   `postprocess track3p <f>.acdtool <jobname>` in the table with the **corrected**
   signature — jobname, not field level (defect 6) — but do not wire it.
2. **Defect 2, narrowed scope.** `postprocess track3p` is table-row-only, so do
   **not** route the KVC dialect. Instead detect a non-`.rfpost` input by extension
   and **raise a clear error naming the unsupported command** — the defect is the
   silent parse to two empty blocks, not the missing feature. When the dialect is
   wanted later, `.acdtool` → `parse_ace3p` in a separate method; do not unify the
   two dialects. `mesh warpsurface`'s flat-colon dialect is out of scope — do not
   add a third parser.
3. Fix **defect 1** on **both** sides. Read: track brace depth in `input_parser`
   and accumulate the value across lines. Write: `write_input` must round-trip it
   and, more generally, **must always emit a structurally valid file** — balanced
   braces, no truncated values. It currently writes `portID = {` verbatim and
   produces a file acdtool cannot read. Invert both Phase-0 characterization tests
   (`test_defect1_multiline_brace_value_is_truncated`,
   `test_defect1_roundtrip_writes_unbalanced_braces`); add a brace-balance
   assertion to the writer's tests so this cannot regress silently.
4. Fix **defect 4**: drop the hardcoded `--nodes=1 --ntasks=1`; follow the
   `ace3p.py` pattern (`-n 1 -c 1`, with the same non-srun guard). Per the user
   guide, every command except `rf` and `volmontomode` is **serial**, and CW23 runs
   even those two at one rank — so one rank is the correct default for all 19, and
   only `rf` / `volmontomode` should ever accept a configurable rank count. Also
   set `output_file` (or leave it explicitly `None`) on *every* dispatch path so a
   failed command reports a missing output instead of raising `AttributeError`.
5. `AcdtoolModule`: add `command` and `args` config. Set `self.requires` in
   `__init__` from the command — `rf` → `EM_SOLUTION`, `transwake` /
   `coaxsignal` / `volmontomode` (and later `wake_new` / `wake_direct`) →
   `TD_SOLUTION` (`track3p` → `TRACK3P_PARTICLES` as a table row, unwired).
   Inject the **jobname** for the
   positional commands from the consumed artifact rather than making the user
   repeat it — resolved from the producing solver's `JobName`, defaulting per
   solver (`t3p_results`, `track3p_results`, …). Raise a clear error on an unknown
   command, listing the known ones.
6. **Settle the defect-7 ordering hazard explicitly.** `transwake` overwrites
   `<jobname>/OUTPUT/wakefield.out`, which `T3PModule` reads. In resolved DAG
   order T3P parses that file *before* acdtool rewrites it, so the workflow would
   silently report the longitudinal wake. Pick one and write down why: (a) re-parse
   the producer after a mutating consumer, (b) have `AcdtoolModule` own the
   post-transwake parse and expose it as a distinct artifact, or (c) defer
   `T3P.output_parser` until all consumers have run. **Do not** ship transwake
   without resolving this — a wrong-but-plausible number is worse than the current
   `WorkflowValidationError`. `parse_wakefield` itself needs no change; it already
   reads both header forms.
7. Fix S3P's hardcoded results dir while in the neighborhood: give `S3P` a
   `results_dir()` and have `output_parser` use it. **Landed in Phase 1** —
   `ACE3P.job_name()`/`results_dir()`, `S3P.default_job_name = 's3p_results'` and
   the `results_dir:` module key all exist already (Phase 1 deviation 1), so what
   remains here is replacing the literal `'s3p_results/Reflection.out'` in
   `S3P.output_parser` with `self.results_dir()`. **Not "reading `JobName` like
   `T3P`"** — the S3P reference documents no `JobName` container, so `s3p_results`
   is the authoritative default and a module-level `results_dir:` YAML key is the
   supported override. Keep the input-tree `JobName` lookup as a fallback for
   symmetry with `T3P`, but do not assert it as solver behavior.
8. Leave `mesh deform` invocable but **not** wired as a mesh producer (design
   decision 3). Same for the other `mesh` / `meshconvert*` rows — table entries
   only.

### Verification (Phase 2 done when)

- [x] `[cubit, t3p, acdtool(command: postprocess transwake)]` validates and
  orders as `cubit → t3p → acdtool`
  (`test_workflow_graph.py::test_order_cubit_t3p_acdtool_transwake`).
- [x] `[cubit, t3p, acdtool(command: postprocess rf)]` still raises
  `WorkflowValidationError` — the `em_solution` guard must survive for `rf`
  (`test_acdtool_rf_after_t3p_is_rejected`, asserted both with and without an
  explicit `command:`).
- [x] A `[cubit, t3p, acdtool(transwake)]` chain against the Phase-0-addendum
  transwake `wakefield.out` reports the **transverse** result (`KickFactor`,
  `WakeType == 'transverse'`), not the longitudinal one. This is the defect-7
  regression test and the one most likely to pass by accident — assert the wake
  type, not just that a number came out.
  (`test_modules.py::test_transwake_reparses_the_producer` asserts the wake type
  both *before* and *after* the acdtool step, so it fails if the re-parse is
  dropped.)
- [x] A `.acdtool` input raises a clear error naming the unsupported command,
  instead of silently parsing to two empty blocks (defect 2, narrowed). The
  Phase-0 characterization test stays as-is — this is a **new** test, not an
  inversion.
  (`test_acdtool_input_raises_naming_the_unsupported_command`;
  `test_defect2_acdtool_dialect_parses_to_empty_blocks` untouched.)
- [x] Multi-line `portID = {\n 7\n 8\n}` parses and round-trips through
  `write_input` without loss (characterization test inverted), and every
  `write_input` output has balanced braces
  (`test_defect1_multiline_brace_value_is_parsed`,
  `test_defect1_roundtrip_writes_balanced_braces`,
  `test_write_input_always_balances_braces`).
- [x] An unknown block in a `.rfpost` input round-trips untouched rather than
  raising — newer acdtool builds ship blocks we have not seen
  (`test_unknown_rfpost_block_roundtrips_untouched`, including an unknown block
  with a multi-line list value).
- [x] An S3P module configured with `results_dir: custom_results` parses from that
  directory, and one with no such key parses from `s3p_results`. **Do not** make
  the primary assertion an input-file `JobName: custom_results` — that key is
  undocumented for S3P and may be ignored by the solver. Test it only as a
  fallback, with a comment saying it is unverified against a real run.
  (`test_s3p_results_dir_override_is_honored` /
  `test_s3p_default_results_dir_is_s3p_results` are the primary pair;
  `test_s3p_input_tree_jobname_is_a_fallback_only` carries the caveat.)
- [x] Existing `postprocess rf` configs run unchanged with no `command` key
  (`test_run_infers_postprocess_rf_from_extension`,
  `test_omega3p_chain_evaluate`, and every baseline example).
- [x] No command line contains `--nodes=` or `--ntasks=`; a non-srun
  `MPI_CALLER` produces a runnable command
  (`test_defect4_non_srun_caller_gets_a_runnable_command`,
  `test_no_mpi_caller_omits_the_rank_flags`,
  `test_cpu_bind_opts_guarded_against_non_srun`).
- [x] `python -m pytest tests/` green including baselines — **323 passed**
  (41 new), up from Phase 1's 282. No baseline file touched.

### Deliverables

- [x] Declarative command table + dispatch + dialect routing + brace fix (read
  **and** write) in `src/lume_ace3p/acdtool.py`.
- [x] `command` / `args` / per-command `requires` + jobname injection in `AcdtoolModule`.
- [x] `S3P.results_dir()` in `src/lume_ace3p/ace3p.py` — the one-line change
  Phase 1 left, plus a class docstring recording why `s3p_results` is
  authoritative.
- [x] A written decision on the defect-7 ordering hazard, inline in this plan
  (see "Decision: the defect-7 ordering hazard" below).
- [x] Tests in `tests/test_workflow_graph.py` (DAG cases) + `tests/test_modules.py`
  + `tests/test_acdtool_fixtures.py` (dispatch, brace fix, the four
  Phase-0-addendum fixtures).
- [x] The four Phase-0-addendum fixtures copied in, with `SOURCES.md` and
  `COVERAGE.md` updated.
- [x] A `docs/yaml_reference.md` `acdtool` module section (the `command:` /
  `args:` / `jobname:` surface, the wired-command table, the transwake ordering
  note), following Phase 1's precedent of documenting new user-facing surface as
  it lands rather than deferring all docs to Phase 6.

### Decision: the defect-7 ordering hazard

**Chosen: option (a) — re-parse the producer after a mutating consumer.**

`postprocess transwake` writes its result *over* `<jobname>/OUTPUT/wakefield.out`,
which `T3PModule` has already parsed by the time acdtool runs. The mechanism is
two per-artifact side tables on `RunContext`, both registered by the producer:

- `ctx.job_names[artifact]` — the results directory the solver actually resolved,
  which is what acdtool's positional commands take as their first argument;
- `ctx.reparse[artifact]` — the producer's `output_parser`, which a consumer calls
  after overwriting its output.

The command table marks *which* artifact a command rewrites (`mutates`), so
`AcdtoolModule.run` fires the hook declaratively rather than special-casing
transwake. `wake_new` / `wake_direct` carry the same marker and get the behavior
for free when they are wired.

Why not the alternatives:

- **(b) `AcdtoolModule` owns the post-transwake parse as a distinct artifact.**
  This would give `wakefield.out` two readers and make the *output spec* depend on
  whether transwake ran — `{module: t3p, quantity: kick_factor}` for a plain T3P
  run, something else for a transwake chain — which is exactly the kind of
  incidental coupling the module layer exists to remove. It also needs a new
  artifact kind for a file that already has an owner.
- **(c) defer `T3P.output_parser` until all consumers have run.** `ACE3P.run`
  calls `output_parser` itself, so this means either restructuring the wrapper's
  run/parse contract or having the `Workflow` reach into module internals to
  sequence parsing. Both are framework changes for a two-command problem, and
  neither is needed once the mutation is declared.

Consequence to keep in mind: an artifact's *parsed* state can now change after its
producer's `run` returned. That is made explicit by the `mutates` field and by the
docstrings on both sides, rather than left to ordering luck — which was the
failure mode this decision exists to close. `parse_wakefield` itself needed no
change; it already reads both header forms.

### Deviations found while executing Phase 2

1. **`postprocess track3p` is the only command held back from `Acdtool.run`
   itself.** The plan's tier table has one "table row, no dialect support" row and
   one "table row, no module wiring" tier, which reads as a single
   invocable/not-invocable split. Two flags were needed, not one: `dispatch`
   (can `Acdtool.run` invoke it) and `wired` (will `AcdtoolModule` accept it as a
   workflow step). Only `postprocess track3p` is `dispatch=False`, because its
   input file goes *through* this wrapper's parser and writer — a `.acdtool` input
   would be re-emitted as garbage. `mesh warpsurface` turned out **not** to need
   the same treatment: its `warp.in` is named positionally, so the filename passes
   through as an opaque argument and no third parser is needed to invoke it. The
   plan's "recommend scoping it out" applies to *parsing* it, which we do not.
   Every held-back row carries a `note` explaining why, and the note is what the
   error message prints — so the reason reaches the user rather than living only
   here.

2. **The parallel/serial split constrains *ranks*, not CPUs.** The plan says one
   rank is correct for all 19 commands and only `rf`/`volmontomode` should accept
   a configurable rank count, which is right. But it would have been wrong to pin
   `-c` as well: CW23 runs the *serial* transwake as `srun -n 1 -c 256` and
   coaxsignal likewise, i.e. one rank over many threads. So `tasks` is forced to 1
   for the 17 serial commands (with a warning rather than a silent override) and
   `cores` stays configurable for all 19.

3. **The rank flags are omitted entirely when there is no MPI caller.** The plan
   says to follow the `ace3p.py` pattern, which emits `-n N -c N` unconditionally
   and produces ` -n 1 -c 1 acdtool ...` — a leading-space command with no
   launcher — when `MPI_CALLER` is empty. Since the verification bullet asks for "a
   non-srun `MPI_CALLER` produces a runnable command", the empty caller is
   included: `_command_line` emits the flags only when there is a caller to consume
   them. `ace3p.py` was left alone; that is a separate, pre-existing case.

4. **A bare `acdtool` module entry still means `postprocess rf`.** The first cut
   raised on a module with neither `input:` nor `command:`, which broke
   `test_registry_edges_match_plan` — it builds every module bare to read its
   edges. That is not merely a test artifact: `AcdtoolModule({})` has always meant
   "`postprocess rf` over the generated default `.rfpost` template", via
   `Acdtool.make_default_input`. The additive policy applies, so the inference
   defaults to `.rfpost` when there is no input file at all, and only a *non*-rfpost
   input with no `command:` raises.

5. **Two adjacent latent bugs in the default-input path were fixed while there.**
   `Acdtool.__init__` called `shutil.copy` on the fabricated `default_input.rfpost`
   before it existed on disk (`make_default_input` builds `input_data` in memory
   only), and `write_input` would `os.path.join` a `None` `original_input_file` on a
   second call. Neither was reachable from any shipped config, and neither is in
   the plan; they are in the blast radius of making `input_file` genuinely optional
   for the positional commands, so leaving them would have turned a latent bug into
   a reachable one.

6. **`load_output` now reports a missing output file instead of raising
   `FileNotFoundError` from inside the parser.** The plan's defect-4 item asks only
   that `output_file` be set (or explicitly `None`) on every dispatch path, which it
   is. The adjacent half is that a *set but absent* output — the normal shape of a
   failed acdtool run — reached `output_parser` and raised from an `open()` deep in
   the parse. It now prints the resolved path and returns, which is the same
   "report, don't crash" contract `Omega3P`/`T3P` output parsing already follows.

7. **The target design's own YAML example is a duplicate-producer error, and
   stays one.** "Command dispatch" shows two `acdtool` entries in one `workflow:`
   list (an `rf` step and a named `transwake` step). Both provide `rf_post`, so
   `_resolve_order` rejects it — *"artifact 'rf_post' is provided by more than one
   module"*. This is not a Phase-2 regression; it is the same
   one-producer-per-artifact rule that design decision 3 declines to loosen, and
   the fix is per-instance artifact identity, which that decision puts out of
   scope for this whole plan. Pinned by
   `test_workflow_graph.py::test_two_acdtool_steps_are_rejected` so it reads as a
   known boundary rather than a surprise. **Treat the plan's two-entry snippet as
   illustrating the two *forms*, not a runnable workflow** — every real chain
   Phases 2–6 need has exactly one acdtool step, including Phase 6's
   `examples/t3p_transwake`. If a workflow ever genuinely needs both, that is the
   concrete need that would justify reopening artifact identity.

8. **A `pytest` gotcha worth recording.** The transwake ordering test fakes
   `subprocess.run` and branches on the command line to decide which wakefield form
   to write. Matching the bare word `transwake` matched T3P's *own* command line
   too, because `tmp_path` is named after the test function
   (`test_transwake_reparses_the_0/...`) and the input path is on the command line.
   Match `'acdtool postprocess transwake'`. This produced a test that failed for
   the right-looking wrong reason — it reported the transverse result too *early*,
   which is the inverse of the defect being tested.

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
   code fragile. Covers `RoverQ`, `RoverQT`, `RoverQRoverQT`, `kickFactor`,
   `pointRoverQ`, `dFSlater`, `VFFT`, `ALLFieldAtPoint`, `coaxPort` — the blocks
   carrying `modeID1`/`modeID2`. Respect the Phase-0 blocking note: if no real
   fixture for a section exists, keep its current behavior rather than guessing its
   header. **`VFFT` needs care**: its `printGroup` key (`nterm` | `ModeID`) changes
   the output grouping, so either handle both or reject the unimplemented one by
   name.
2. **Surface-indexed reader** for `maxFieldsOnSurface` and
   `powerThroughSurface`. Same blocking note applies. **`powerThroughSurface`
   returns a complex power** (unit W, real part = average flow from the complex
   Poynting vector) — give it the same real/imag split Phase 1 gives Omega3P's
   `Frequency`, not a plain float.
3. **Single-mode scalar reader** for `FieldAtPoint` — no index axis at all, since
   it evaluates only `RFField`'s `ModeID`. Distinct from `ALLFieldAtPoint`.
4. **Curve-file reader.** A header-driven column reader for the `filename` blocks
   — parse the `#`-comment header into column names, then load the numeric block
   into `{column: array}`. Model it on `parse_wakefield`; return these through
   `Module.field()` as field artifacts, never as table columns. **The filename
   schemes are not uniform** (see the table in the Motivation): `ALLFieldOnLine`
   writes `<filename>_<modeID>` plus `.ec`/`.bc`, but `FieldOnLine` writes
   `<filename>.e` / `.b` / `.ec` / `.bc` with **no** mode suffix and E/B split
   across two files. Derive the expected names from the block type, not from a
   single assumed pattern. The Phase-0 `curves/` fixtures cover only the
   `ALLFieldOnLine` form.
5. **`[scaling]` reader.** Always emitted, two variants: gradient-normalized
   (`V`, `ga`, `E,B m_factor`) and point-scaled when `gradient < 0` — the guide
   confirms `gradient = -1` means "no scaling" — (`Ez from O3P`, `Ez scaled to`,
   `m_factor`). Expose `m_factor` — it is the normalized→physical field conversion,
   and nothing else provides it. It also reconciles the two curve-block scalings:
   `FieldOnLine` output is gradient-scaled, `ALLFieldOnLine` output is raw
   eigenmode normalization.
6. **Fix defect 3** as part of this: replace `startswith('}')` end-detection with
   brace-depth tracking, or bound each section at the next `[section]` header, so
   the unclosed `[scaling]` in the S3P fixture cannot swallow what follows.
7. Grid blocks (`FieldMap`, `IMPACTMap`, `OpenPMD_IMPACT`, `fieldOnSurface`,
   `fieldOn2DBoundary`) — record the produced filenames as artifacts; **defer**
   binary/mesh parsing. Note the deferral in the docstring so it does not read as
   an oversight. Filenames are block-specific: `FieldMap` writes **fixed**
   `Efield-map.dat` / `Bfield-map.dat` and has no `filename` key at all;
   `IMPACTMap` writes `EBfield-map-<filename>.dat`; `OpenPMD_IMPACT` writes four
   **HDF5** files (`E_Real.h5`, `E_Imag.h5`, `B_Real.h5`, `B_Imag.h5`) — do not
   assume text.
8. **Headerless outputs need their own reader.** `coaxsignal`'s `signal.out` is
   three columns (`t`, `V`, `I`) with **no header row at all**, so the
   header-driven reader cannot handle it; the column names come from the user
   guide. `EnhancementCounter`'s `en` output *does* have a header row and goes
   through the normal reader.

### Verification (Phase 3 done when)

- [x] All six Phase-0 `rfpost.out` fixtures parse; `[RoverQ]` values match the
  Phase-0 characterization values in
  `test_acdtool_fixtures.py::ROVERQ_EXPECTED` exactly (this is a refactor of a
  working parser — the numbers must not move). **Unchanged to full precision**;
  the only movement in that test is `set(output_data)` gaining `scaling` (see
  deviation 1).
- [x] The unclosed-`[scaling]` fixture parses without corrupting the following
  section (`test_defect3_unclosed_scaling_does_not_swallow_the_next_block`,
  which asserts both that `m_factor` is read and that none of the
  `ALLFieldOnLine` echo's keys leaked in).
- [x] `field1_0` / `.ec` / `.bc` load with correct column names and array lengths
  (300 rows for `field1_0.ec`, 20 for the truncated ones — see `SOURCES.md`)
  — `test_curve_files_are_read_when_present`, driven off `CURVE_SHAPES`.
- [x] `[scaling]` `m_factor` extracted from both variants
  (`test_scaling_block_is_parsed_from_both_variants`, including
  `m_factor_amplitude` / `m_factor_phase_deg` and the `Variant` label).
- [x] Sections still genuinely unimplemented raise or warn with the section name,
  never silently return empty — one `AcdtoolOutputWarning` per case: unknown
  block, no `ModeID` header, no `surfaceID`, no `name = value` lines, a curve
  block that wrote no files, and `VFFT` with `printGroup = nterm`. A section
  merely *absent* from the output keeps the old stdout report (deviation 8).
- [x] `python -m pytest tests/` green including baselines — **338 passed**
  (15 net: 18 added, 3 characterization tests replaced by their inverted forms),
  up from Phase 2's 323. No baseline file touched.

### Deliverables

- [x] Shape readers in `src/lume_ace3p/acdtool.py`, all module-level as
  `parse_wakefield` is — `read_mode_table`, `read_surface_scalars`,
  `read_point_scalars`, `read_scaling`, `parse_column_file`, plus
  `split_output_sections` (the defect-3 fix) and the `SECTIONS` table that maps
  each of the 24 blocks to its shape and output filenames.
- [x] `AcdtoolModule.field()` returning curve/grid artifacts, via
  `acdtool.field_sections`.
- [x] Tests in `tests/test_acdtool_fixtures.py` (17 new; 4 characterization tests
  inverted or updated) + `tests/test_modules.py` (1 new, plus a coaxsignal case
  added to the existing wrong-module test).
- [x] Updated `COVERAGE.md` — per-block "Parser today" for all 24 blocks, and
  the two-gaps section rewritten to say how Phase 3 handled the missing fixtures
  and what a real run is still owed for.
- [x] A `docs/yaml_reference.md` subsection on what `postprocess rf` reads out of
  its output (the shape table, `[scaling]`/`m_factor`, curves as field artifacts,
  the warning behavior), continuing Phases 1–2's practice of documenting new
  user-facing surface as it lands.

### Deviations found while executing Phase 3

1. **`[scaling]` is now always in `output_data`, which moved one characterization
   assertion.** It is emitted by every run and declared by no input block, so the
   `ionoff` loop could never reach it — reading it means reading it *outside* that
   loop, and so `set(acd.output_data)` grows from `{'RoverQ'}` to
   `{'RoverQ', 'scaling'}` in `test_roverq_values_from_real_output`. No
   `[RoverQ]` value moved and no baseline moved: the baselines are dry-run and
   every output spec names its section explicitly, so an extra key in the parsed
   dict is invisible to the tables. Three other Phase-0 characterization tests
   were inverted as the phase that fixes them should
   (`test_scaling_block_is_never_parsed` → `..._is_parsed_from_both_variants`,
   `test_curve_block_output_is_not_parsed` → `test_curve_files_are_read_when_present`
   plus a warning test, and `test_unimplemented_section_reports_and_yields_nothing`
   → `test_unreadable_section_warns_naming_itself`).

2. **The blocking note was honored by making the unvalidated parsers *less*
   layout-dependent, not by freezing them.** No cluster run was available, so
   neither `kickFactor` nor `maxFieldsOnSurface` was "cleaned up" against its
   assumed format. But the plan's own instruction — generalize the near-identical
   `RoverQ`/`kickFactor` bodies into one header-driven reader — *is* a change to
   an unvalidated parser, and refusing it would have left the positional
   `modeline[3]` access the plan calls the fragile part. Resolution: `kickFactor`
   now takes its column names from the file's own `ModeID` header row rather than
   from a hardcoded order, and `maxFieldsOnSurface` reads `name = value [at
   (x,y,z)]` lines wherever they appear rather than at a fixed two and three lines
   below the `surfaceID`. Both are **strictly weaker assumptions** than before and
   both produce byte-identical values on the synthetic fixture
   (`test_modules.py::test_acdtool_extract`, untouched). What is still owed is
   unchanged: only a real run with those blocks at `ionoff = 1` verifies their
   column names, and `COVERAGE.md` now says so in those words.

3. **A second declarative table (`SECTIONS`) was added, mirroring Phase 2's
   command table.** The plan described the shapes in prose and the filename
   schemes in a Motivation table; encoding them — shape, output-file patterns, a
   `validated` flag, a note — makes "which reader" a lookup rather than a branch,
   makes the 24-block surface assertable
   (`test_section_table_covers_the_documented_block_surface`), and puts
   `COVERAGE.md`'s real-output column in the code where a reader will see it. It
   also settles where the filename schemes live: in the table, not in the reader.

4. **Curve and grid filenames are globbed, not predicted.** `modeID2 = -1` means
   "every mode the solver produced", so `ALLFieldOnLine`'s per-mode suffix is not
   knowable before the run. The table carries `{filename}_*` and the reader globs
   it, which also picks up the `.ec`/`.bc` siblings in one pass. Predicting names
   would have needed the mode count the eigensolve only reveals afterwards — the
   same asymmetry Phase 1 deviation 2 recorded for the dry-run field index.

5. **`parse_column_file` also accepts an *uncommented* header row.** Found while
   covering the Phase-0-addendum fixtures: `postprocess track3p`'s `en` names its
   seven columns on a bare first line, not a `#` comment. Treating any
   non-numeric line before the first data row as a header costs one `try` and
   means the same reader covers `en` for free — even though the *command* stays
   unwired for want of the `:` input dialect.

6. **`Command.parses` became a property over a new `reader` field.** Phase 2
   needed only a boolean; Phase 3 needs to know *which* reader, because
   `coaxsignal`'s headerless `signal.out` is now read too (plan item 8). So the
   table carries `reader` (`'rfpost'` / `'signal'` / `None`) and `parses` is
   derived from it, keeping `AcdtoolModule`'s call site working.
   `AcdtoolModule.extract`'s guard moved to `reader != RFPOST`, so asking a
   coaxsignal step for a quantity still raises — now naming the field-artifact
   route rather than claiming its output is unread.

7. **`eval()` is gone from the whole output path**, three sites: the `ionoff`
   flag, every number in a mode table, and the `at (x, y, z)` coordinate tuples.
   Phase 5 lists this as a chore for `S3P.output_parser`; here it came free,
   since every value now goes through `float()` or a float regex. The `.rfpost`
   *input* parser never used `eval`.

8. **An absent section and an unreadable one are treated differently.** The
   verification bullet asks for a warning on an unimplemented section, but a
   section simply not present in `rfpost.out` is a normal outcome — the run may
   have been configured with that block off, or `postprocess rf` may have been
   pointed at a different results directory — so that case keeps the original
   stdout report (`test_absent_section_still_reports_to_stdout`) and the warning
   is reserved for output that exists in a shape no reader knows. Warning on both
   would have made the common case noisy and the real one indistinguishable.

9. **Grid parsing is deferred as planned, and the deferral is visible in three
   places**: the `SECTIONS` note per block, `Acdtool._read_files`' docstring, and
   `output_data[block] = {'files': [...]}` — which records what was produced
   without pretending to have read it. Two of the five write binary or HDF5, so
   this is not a shortcut that a later session should "finish" without a use case.

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

- [x] Mapping and list forms produce identical values for the same quantity on
  the same fixture (`test_acdtool_mapping_and_list_forms_agree`, over all five
  spellings the shipped examples use — including the location component).
- [x] List form emits `DeprecationWarning` naming the mapping replacement
  (`test_acdtool_list_form_warns_naming_its_mapping_replacement`, which also
  pins that it warns *once* per spec and that the mapping form never warns).
- [x] A mode-indexed acdtool output with no `at:` yields one table row per mode
  (`test_acdtool_mode_section_without_at_returns_the_whole_axis` for the array
  and the axis; `test_omega3p_acdtool_table_indexes_on_modeid` for the rows the
  mode layer builds from them).
- [x] An `s3p + acdtool` workflow indexes its table on S3P's `Frequency` (first in
  DAG order), exactly as it does today, with acdtool mode data as a field artifact
  (`test_s3p_acdtool_table_indexes_on_s3p_frequency`, which asserts acdtool's own
  axis *is* `ModeID` so the test fails if the collision is won by accident).
- [x] An `omega3p + acdtool` workflow requesting both `RoverQ` and
  `maxFieldsOnSurface` indexes on `ModeID`, with `Emax` an `at:`-narrowed scalar
  (`test_omega3p_acdtool_table_indexes_on_modeid`).
- [x] Requesting a surface-indexed section **without** `at:` raises an error that
  names the available surface IDs
  (`test_acdtool_surface_section_without_at_names_the_surface_ids`).
- [x] `python -m pytest tests/` green — **349 passed** (11 new), up from Phase 3's
  338; **no baseline file touched**.

### Deliverables

- [x] Mapping-form `extract` + `_value` + `field_index` + broadened `field` in
  `AcdtoolModule`, plus `mode_ids` / `table_mode_ids` / `mode_table_arrays` in
  `src/lume_ace3p/acdtool.py`.
- [x] Single list→mapping adapter (`modules.acdtool_spec`), reachable from
  `_infer_output_module`.
- [x] Tests in `tests/test_modules.py` (8 new) + `tests/test_workflow_graph.py`
  (3 new).
- [x] A `docs/yaml_reference.md` "Output specs for `postprocess rf`" subsection
  (the mapping form, the whole-axis rule, the surface rule, the list→mapping
  translation table), and a rewritten "Two spec syntaxes" — which claimed the
  acdtool module *requires* the positional list.

### Deviations found while executing Phase 4

1. **`field_index` keys on the parsed output, not on "the declared outputs".**
   A module never sees `output_parameters` — `Workflow._route_output` resolves
   specs to modules, not the reverse — so "return `('ModeID', array)` when a
   mode-indexed section is among the declared outputs" has no seam to read. The
   data-driven equivalent is the same set: a block appears in `output_data` only
   when its `.rfpost` input set `ionoff = 1`, which is what "declared" meant. It
   also matches `Omega3PModule.field_index`, which asks its own parsed output the
   same question, and keeps the dry-run answer `None` (Phase 1 deviation 2) for
   free.

2. **The mapping form needed a `component:` key.** The target design in this plan
   sketches `at:` and `quantity:` only, but the list form has a *fourth* element
   for the location vectors — `['maxFieldsOnSurface', '6', 'Emax_location', 'x']`
   — which two shipped examples use for `loc_x`/`loc_y`/`loc_z`. Since Phase 3
   parses a location into a `{x, y, z}` dict, the mapping needs somewhere to name
   the part: `component: x`. The alternative (a dotted `quantity:
   Emax_location.x`) would have introduced a path mini-language for one case.
   Without this the alias would not have been lossless and Phase 6 could not
   migrate `examples/omega3p_sweep`.

3. **`field()` broadened to carry the mode-indexed sections as arrays.** Phase 3
   left `field()` as curves and grids. But design decision 2 says any axis that is
   *not* the table axis routes to a field artifact, and the `s3p + acdtool` case is
   exactly that: a per-mode array cannot be a column of a frequency-indexed table.
   So `field()` now also returns `{section: {ModeID, column, ...}}`
   (`acdtool.mode_table_arrays`), and one Phase-3 assertion moved with it —
   `test_acdtool_field_returns_curves_not_table_columns` asserted `field() is
   None` for a rfpost-sections-only run. Surface-indexed sections are *not*
   included: they always resolve to an `at:`-narrowed scalar column.

4. **The deprecation warns at the translation site only, once per spec.**
   `_infer_output_module` also has to ask the adapter "is this acdtool's?", and
   warning there would double every message; `extract` runs once per evaluation,
   so warning unconditionally would print N copies for an N-point sweep. So
   routing calls the adapter with `warn=False` and `AcdtoolModule` keeps a
   `_warned` set of specs it has already reported. The plan's "exactly one
   translation site" is preserved — the warning is a parameter of that site, not a
   second one.

5. **Routing recognizes all 24 block names, not the three the old router listed.**
   `_infer_output_module` hardcoded `RoverQ` / `kickFactor` /
   `maxFieldsOnSurface`; the adapter now routes anything whose head (or `section:`)
   is a key of `acdtool.SECTIONS`. This is a strict superset — none of the 24
   collides with a T3P quantity, a Geant4 section or a Particles name (the
   `kickFactor` / `kick_factor` distinction the Motivation calls out is the closest
   pair) — and it means a spec naming a block that exists but has no reader fails
   with *"writes its own file … through field()"* rather than being mis-routed to
   `s3p`.

6. **`module: acdtool` is optional in the mapping form, and a wrong-axis `at:`
   raises.** Naming a `section:` is itself the routing signal, so
   `{section: RoverQ, quantity: RoQ}` reaches acdtool with no `module` key —
   which is what makes the mapping form no more verbose than the list it replaces.
   Conversely `{section: RoverQ, at: {surface: 6}}` is an error naming the axis the
   section does take, rather than silently ignoring the `at:` and returning the
   whole mode axis.

7. **The per-section column whitelists went with the list form.** `extract` used
   to `assert entry in {'Frequency', 'Qext', 'V_r', ...}` per section — a
   hardcoded set that Phase 3 already made wrong in principle, since column names
   now come from the output file's own header row. The mapping form's errors report
   what the run *did* produce instead (`AcdtoolModule._value`), which is both
   accurate for a build that adds a column and more useful than naming the set the
   code expected.

8. **The synthetic `RFPOST_OUTPUT` fixture gained a second `RoverQ` mode.** With
   one mode, "the whole axis" and "mode 0" are indistinguishable, so the fixture
   could not tell a working whole-axis read from a broken one. `[kickFactor]`
   deliberately keeps its single mode — the blocks are narrowed independently by
   their own `modeID1`/`modeID2`.

---

# Phase 5 — S3P output completion

**Objective:** Stop discarding S-parameter phase, and read the port mode files.
Independent of Phases 2–4 except for the `results_dir` fix landed in Phase 2.

**The S3P reference is no help here.** It documents `ModelInfo`, `FiniteElement`,
`FrequencyScan`, `Port`, `Loading`, `LinearSolver` and `PostProcess` — and **not a
single output file**. `Reflection.out`, `SParameter.out` and `PortRef<n>_<m>.out`
are undocumented, so the Phase-0 fixtures from `s3p/90DegreeBend` are the only
specification of their format. Treat the `abs(S_complex) == S_magnitude`
cross-check below as the load-bearing test: it is the only way to confirm the two
files are being read consistently without a documented format to check against.

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
   `acdtool.py::make_default_input` hardcodes `omega3p_results` **and is a
   2-block hand-written subset of a 24-block format**. The guide says
   `acdtool postprocess rf` with no arguments writes a `sample.rfpost` for the
   installed build — prefer generating the default from the tool (falling back to
   the hardcoded template when no binary is present) over maintaining a Python
   copy of a format that varies by build.
4. New example — **T3P transwake.** Source: `CW23/examples/t3p/cavity-half`.
   A `[cubit, t3p, acdtool(transwake)]` chain — the workflow Phase 2 unblocks.
   The figure of merit is `KickFactor`, read by `T3PModule`, not by acdtool
   (defect 7). The README must say so, or the example will read as though acdtool
   parses nothing by oversight.
5. Docs: update `docs/yaml_reference.md` (mapping form, `command`/`args`,
   deprecation), `docs/parameter_sweep.md`, `docs/optimization.md`,
   `docs/configuration_by_mode.md`.
6. **`docs/acdtool_reference.md`.** Transcribe the parts of the surface this
   codebase depends on, from this plan's Motivation and from
   `references/acdtool-commands.pdf`:
   - all **19 commands** with their argument forms, which are implemented, which
     are table-only, and which are out of scope with the reason;
   - all **24 `.rfpost` blocks** with shape, index axis, output destination and
     implementation status — including the CW23-only (`Track`, `TrackScan`,
     `coaxPort`) and guide-only (`pointRoverQ`, `dFSlater`, `RoverQRoverQT`,
     `IMPACTMap`, `OpenPMD_IMPACT`) sets, and the fact that the two disagree;
   - the input semantics that are not guessable from the files: the `>1e6`
     domain-bound sentinel, `gradient = -1`, `modeID2 = -1` meaning all modes,
     per-solver `ModeID` meaning, complex `powerThroughSurface`, `VFFT`'s
     `printGroup`;
   - the curve/grid **filename schemes**, which differ per block;
   - the serial/parallel split;
   - which commands are unimplemented and why (PIC3P/Gun3P/TEM3P out of scope).

   **Done 2026-08-13:** all eight `*-commands.pdf` are committed under
   `references/` with a `README.md` covering provenance, per-file page counts, the
   inputs-not-outputs caveat, the `JobName` finding and the text-extraction recipe.
   So `docs/acdtool_reference.md` is now a convenience digest rather than the only
   surviving copy — but still write it, since it is what a reader of the code will
   reach for.
7. Add a README to each new example, matching the existing example READMEs.

### Verification (Phase 6 done when)

- [ ] No shipped example YAML uses the list form; `pytest -W error::DeprecationWarning`
  over the examples is clean.
- [ ] All three new examples run in dry-run mode and are frozen into
  `tests/baseline/`.
- [ ] Every baseline regeneration is recorded with a reason in its manifest.
- [ ] Docs contain no stale list-form spec; `docs/acdtool_reference.md` lists all
  **19 commands** and all **24 blocks** with their implementation status, and
  carries the input semantics listed in approach item 6.
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
  what you need into `tests/fixtures/` rather than reading from it at test time
  (Phase 0 did this; the Phase-0 addendum lists four more files to copy as the
  phases that need them come up). Ignore `a3pi/`, `A3PI_config_single/`,
  `workflow_test_single/`.
- **`references/*-commands.pdf` are the authoritative references for the command
  surface, every `.rfpost` block, and every solver's input containers** — consult
  them before inferring anything about an ACE3P interface from CW23 files alone.
  They corrected several assumptions in the original plan (see "Revision: the
  ACE3P command references"). They document inputs thoroughly and outputs barely,
  so output formats still have to come from real runs or frozen fixtures. Fixture
  budget for `tests/fixtures/` is **100–150 KB**.
- **Do not attempt TEM3P or named artifacts** (design decision 3). If a phase
  seems to need two producers of the same artifact kind, stop and write down the
  case rather than loosening `_resolve_order`.
- The user runs all pushes and merges manually — hand over commands, do not offer
  to push. `dev` is long-lived; PRs into it use merge commits only.
- Verify before recommending: this plan names specific functions
  (`_infer_output_module`, `_resolve_order`, `parse_wakefield`,
  `T3P.results_dir`) that were current as of 2026-08-13.
