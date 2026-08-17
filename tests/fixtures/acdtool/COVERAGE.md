# Real-output fixture coverage, per `.rfpost` block

Frozen 2026-08-13 for Phase 0 of `docs/acdtool_rework_plan.md`; **"Parser today"
updated 2026-08-17 for Phase 3**, which replaced the three hand-written section
parsers with one reader per output *shape*. Provenance for every file named here
is in `SOURCES.md`.

This file answers one question, block by block: **is there a real acdtool output
in `tests/fixtures/` that shows what this block prints?** Where the answer is
*no*, there is no ground truth, and Phase 3 did not invent one: every reader is
driven by what the file itself says — the header row for a column table, the
`name = value` lines for a scalar block — and a block whose output cannot be read
warns naming itself (`AcdtoolOutputWarning`) rather than silently yielding
nothing. See `src/lume_ace3p/acdtool.py::SECTIONS`, whose `validated` flag is the
machine-readable form of the **Real output** column below and is asserted against
this file by `test_section_table_covers_the_documented_block_surface`.

Legend for **Real output**:

- **yes** — a real `rfpost.out` or curve file here contains this block's output.
- **input only** — the block appears in the real `.rfpost` template
  (`ionoff = 0`), so its *input* keys are covered, but no run ever enabled it and
  nothing here shows its output.

---

## Mode-indexed table → `rfpost.out`

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `RoverQ` | **yes** (5 files) | `read_mode_table`, validated by these files | `rfpost_outputs/{pillbox+recWG,pillbox+recWG+load,pillbox-rtop,pillbox-rtop+coax,dlwg-pbc}.rfpost.out` |
| `RoverQT` | input only | `read_mode_table` (header-driven) | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `kickFactor` | **input only** | `read_mode_table`; column names now come from the file's own header rather than an assumed order, but the layout is still **UNVALIDATED** | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `VFFT` | input only | `read_mode_table`, and only with `printGroup = ModeID`; `nterm` grouping is rejected naming the key | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `ALLFieldAtPoint` | input only | `read_mode_table` (header-driven) | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `coaxPort` | input only | `read_mode_table` (header-driven) | `rfpost_inputs/pillbox-rtop+coax.rfpost` (empty lists), `rfpost_inputs/coaxport-multiline.rfpost` (synthetic, filled) |

## Surface-indexed scalars → `rfpost.out`

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `maxFieldsOnSurface` | **input only** | `read_surface_scalars`; reads the assignments wherever they appear instead of at fixed line offsets, but still **UNVALIDATED** | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `powerThroughSurface` | input only | `read_surface_scalars`, with the complex-power real/imag split | `rfpost_inputs/pillbox-rtop+coax.rfpost` |

## Column curve files → separate files

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `ALLFieldOnLine` | **yes** | `parse_column_file`, validated by these files | `curves/field1_{0,1}{,.ec,.bc}` (from `s3p/window`) |
| `FieldOnLine` | input only | `parse_column_file`; **different filename scheme** (`.e`/`.b` split, no mode suffix) | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `Multipole` | input only | `parse_column_file` | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `GBZFFT` | input only | `parse_column_file` | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `Track` | input only | `parse_column_file` | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `TrackScan` | input only | `parse_column_file` (two files: `filename` + `scanfilename`) | `rfpost_inputs/pillbox-rtop+coax.rfpost` |

`ALLFieldOnLine` is the only curve block with real output, but it is the richest
one and the plan's design has all six going through a single header-driven
column reader — so it is reasonable ground truth for the shape. It is **not**
ground truth for the other five blocks' column *names*.

## Grid / mesh → separate files

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `FieldMap` | input only | filenames recorded, not parsed | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `fieldOnSurface` | input only | filenames recorded, not parsed | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `fieldOn2DBoundary` | input only | filenames recorded, not parsed | `rfpost_inputs/pillbox-rtop+coax.rfpost` |

Phase 3 defers binary/mesh parsing for these regardless of coverage.

## Run-level

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `[scaling]` | **yes**, both variants | `read_scaling`, both variants, read outside the `ionoff` loop | gradient-normalized: the 5 omega3p `rfpost_outputs/`; point-scaled (`gradient < 0`): `rfpost_outputs/window.rfpost.out` |
| `RFField` | n/a — configuration, emits no `[...]` section | input keys only | both real `rfpost_inputs/` files |

---

## The two gaps — still open after Phase 3

**`kickFactor` and `maxFieldsOnSurface` have no real-output fixture.** No CW23
run enabled either one — grepping every `.out` in the archive for section
headers yields only `[scaling]` and `[RoverQ]`. Both nevertheless have a parser,
originally written against an assumed format and tested only with hand-written
fixtures (`tests/test_modules.py::RFPOST_OUTPUT`, whose header rows are invented
— e.g. `Emax = 1.500000e6 at (0.1, 0.2, 0.3)`).

**`examples/omega3p_sweep` depends on `maxFieldsOnSurface`** for its `E_max`
output parameter (`['maxFieldsOnSurface', '6', 'Emax']`), and
`tests/baseline/omega3p_sweep/` freezes that path. So a shipped example and a
frozen baseline both rest on an unverified format.

**How Phase 3 handled this, and what is still owed.** No cluster run was
available, so neither parser was "cleaned up" against its assumed layout. Both
were instead made *less* dependent on one: `kickFactor` takes its column names
from the file's own `ModeID` header row rather than from a fixed order, and
`maxFieldsOnSurface` reads `name = value [at (x,y,z)]` lines wherever they appear
rather than at a fixed offset below the `surfaceID`. Both produce **byte-identical
values** on the synthetic fixture (pinned by `test_modules.py::test_acdtool_extract`
and `test_acdtool_field_returns_curves_not_table_columns`). That widens the range
of real formats they would read correctly but does not verify either one — a real
run with those two blocks at `ionoff = 1` is still the only thing that closes this,
and remains a prerequisite for treating their column names as documented.

## Blocks with no fixture at all

Revised 2026-08-13 against `references/acdtool-commands.pdf`, the acdtool user
guide, which was not available when the fixtures were frozen.

The guide documents **five blocks that do not appear in CW23's `.rfpost`
template**, so there is no fixture here for them in either direction — not even
an input example:

| Block | Shape | Note |
|---|---|---|
| `pointRoverQ` | mode-indexed | R/Q from the field at a single point |
| `dFSlater` | mode-indexed | frequency offset from a geometry error (Slater perturbation) |
| `RoverQRoverQT` | mode-indexed | longitudinal + transverse R/Q together; documented in the guide's body but **absent from its own functionality list** |
| `IMPACTMap` | grid | writes `EBfield-map-<filename>.dat` in IMPACT format |
| `OpenPMD_IMPACT` | grid | writes **HDF5** (`E_Real.h5`, `E_Imag.h5`, `B_Real.h5`, `B_Imag.h5`) |

Conversely, three blocks in CW23's template are **absent from the guide**:
`Track`, `TrackScan`, `coaxPort`. CW23's template is from an older acdtool build.

**Neither list is a superset.** The union is 24 blocks (`RFField` + 23
postprocess). The input parser must therefore tolerate unknown blocks in both
directions rather than enumerate a fixed set.

`FieldAtPoint`, which the plan's original shape table omitted, is **input only**
here. Per the guide it is its own shape: no `modeID1`/`modeID2`, so it evaluates
only the single mode named in `RFField` and has no index axis — distinct from the
mode-indexed `ALLFieldAtPoint`. Phase 3 reads it with `read_point_scalars`, the
same `name = value` reader the surface blocks use minus the surface axis; the
layout is unverified for the same reason as theirs.

All five guide-only blocks nevertheless have a shape assigned in `SECTIONS`, so a
build that ships them is read (mode-indexed) or recorded (grid) rather than
warned about as unknown.

## Solver outputs (Phases 1 and 5)

Not `.rfpost` blocks, listed for completeness — details in `SOURCES.md`.

| Target | Real output | Parser today |
|---|---|---|
| Omega3P `Mode` sections, real eigenvalues | **yes** — `solver_outputs/omega3p/pillbox.omega3p.out` | `parse_omega3p_output` (Phase 1) |
| Omega3P `Mode` sections, complex eigenvalues + `ExternalQ` | **yes** — `solver_outputs/omega3p/pillbox-rtop+coax.omega3p.out` | same |
| S3P \|S\| magnitudes | **yes** — `solver_outputs/s3p_90DegreeBend/Reflection.out` | implemented and validated by these fixtures |
| S3P complex S-parameters | **yes** — `solver_outputs/s3p_90DegreeBend/SParameter.out` | none — Phase 5 |
| S3P port mode profiles | **yes** — `solver_outputs/s3p_90DegreeBend/PortRef7_0.out` | none — Phase 5 |

## Non-`rf` command outputs (added in Phase 2)

The other `postprocess` commands do not write to `rfpost.out` at all, so they sit
outside the block table above. Coverage is now complete for the three wired
time-domain commands; `postprocess volmontomode` produces only ParaView `.mod`
files, which nothing in this package reads.

| Command | Output | Real output | Parser today |
|---|---|---|---|
| `postprocess transwake` | `<jobname>/OUTPUT/wakefield.out`, **overwriting** T3P's own | **yes** — `t3p_outputs/cavity-half.wakefield.out` | `ace3p.parse_wakefield`, reached through `T3PModule` — acdtool parses nothing here |
| `postprocess wake_new` / `wake_direct` | same path, longitudinal, with a loss factor | no | same reader would apply; commands unwired |
| `postprocess coaxsignal` | `<jobname>/OUTPUT/signal.out`, columns `t V I`, **no header row** | **yes** — `t3p_outputs/BPM.signal.out` | `parse_column_file(..., columns=SIGNAL_COLUMNS)` — the names come from the reference, not the file |
| `postprocess volmontomode` | `<jobname>/OUTPUT/*.mod` | no | n/a — ParaView visualization only |
| `postprocess track3p` (`EnhancementCounter`) | `<jobname>/<OutputFile>`, 7 columns **with** a header row (**uncommented**) | **yes** — `track3p_outputs/Pillbox-2.3MV.en` | `parse_column_file` reads it (the header need not be `#`-commented); the *command* is still table-row-only, needing the `:` dialect |
| `postprocess track3p` (`Trajectory`) | per-`ParticleID` trajectory files | no | none — the only capability unique to the acdtool route |

`t3p_outputs/cavity-half.postprocess.in` is not a command output but the KVC echo
T3P writes of its own resolved input; it is the evidence behind the jobname
resolution order. See `SOURCES.md`.
