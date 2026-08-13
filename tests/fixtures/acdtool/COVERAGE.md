# Real-output fixture coverage, per `.rfpost` block

Frozen 2026-08-13 for Phase 0 of `docs/acdtool_rework_plan.md`. Provenance for
every file named here is in `SOURCES.md`.

This file answers one question, block by block: **is there a real acdtool output
in `tests/fixtures/` that shows what this block prints?** Phase 3 rewrites
`Acdtool.output_parser`; where the answer is *no*, Phase 3 has no ground truth
and must not guess at a format.

Legend for **Real output**:

- **yes** — a real `rfpost.out` or curve file here contains this block's output.
- **input only** — the block appears in the real `.rfpost` template
  (`ionoff = 0`), so its *input* keys are covered, but no run ever enabled it and
  nothing here shows its output.

---

## Mode-indexed table → `rfpost.out`

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `RoverQ` | **yes** (5 files) | implemented | `rfpost_outputs/{pillbox+recWG,pillbox+recWG+load,pillbox-rtop,pillbox-rtop+coax,dlwg-pbc}.rfpost.out` |
| `RoverQT` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `kickFactor` | **input only** | implemented, **UNVALIDATED** | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `VFFT` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `ALLFieldAtPoint` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `coaxPort` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` (empty lists), `rfpost_inputs/coaxport-multiline.rfpost` (synthetic, filled) |

## Surface-indexed scalars → `rfpost.out`

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `maxFieldsOnSurface` | **input only** | implemented, **UNVALIDATED** | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `powerThroughSurface` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |

## Column curve files → separate files

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `ALLFieldOnLine` | **yes** | none | `curves/field1_{0,1}{,.ec,.bc}` (from `s3p/window`) |
| `FieldOnLine` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `Multipole` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `GBZFFT` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `Track` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `TrackScan` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |

`ALLFieldOnLine` is the only curve block with real output, but it is the richest
one and the plan's design has all six going through a single header-driven
column reader — so it is reasonable ground truth for the shape. It is **not**
ground truth for the other five blocks' column *names*.

## Grid / mesh → separate files

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `FieldMap` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `fieldOnSurface` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |
| `fieldOn2DBoundary` | input only | none | `rfpost_inputs/pillbox-rtop+coax.rfpost` |

Phase 3 defers binary/mesh parsing for these regardless of coverage.

## Run-level

| Block | Real output | Parser today | Fixture |
|---|---|---|---|
| `[scaling]` | **yes**, both variants | none | gradient-normalized: the 5 omega3p `rfpost_outputs/`; point-scaled (`gradient < 0`): `rfpost_outputs/window.rfpost.out` |
| `RFField` | n/a — configuration, emits no `[...]` section | input keys only | both real `rfpost_inputs/` files |

---

## The two gaps that block Phase 3

**`kickFactor` and `maxFieldsOnSurface` have no real-output fixture.** No CW23
run enabled either one — grepping every `.out` in the archive for section
headers yields only `[scaling]` and `[RoverQ]`. Both nevertheless have a parser
in `Acdtool.output_parser` today, written against an assumed format and tested
only with hand-written fixtures (`tests/test_modules.py::RFPOST_OUTPUT`, whose
header rows are invented — e.g. `Emax = 1.500000e6 at (0.1, 0.2, 0.3)`).

**`examples/omega3p_sweep` depends on `maxFieldsOnSurface`** for its `E_max`
output parameter (`['maxFieldsOnSurface', '6', 'Emax']`), and
`tests/baseline/omega3p_sweep/` freezes that path. So a shipped example and a
frozen baseline both rest on an unverified format.

Consequence for Phase 3, restating the plan's blocking note: either obtain one
real acdtool run with `kickFactor` and `maxFieldsOnSurface` enabled (any Omega3P
example plus those two set to `ionoff = 1`), or keep both existing parsers
byte-compatible and route only the genuinely new sections through the new shape
readers. Do not "clean up" an unvalidated parser blind — there is nothing here
that would catch it going wrong.

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
here with no parser today. Per the guide it is its own shape: no
`modeID1`/`modeID2`, so it evaluates only the single mode named in `RFField` and
has no index axis — distinct from the mode-indexed `ALLFieldAtPoint`.

## Solver outputs (Phases 1 and 5)

Not `.rfpost` blocks, listed for completeness — details in `SOURCES.md`.

| Target | Real output | Parser today |
|---|---|---|
| Omega3P `Mode` sections, real eigenvalues | **yes** — `solver_outputs/omega3p/pillbox.omega3p.out` | `Omega3P.output_parser` is `pass` |
| Omega3P `Mode` sections, complex eigenvalues + `ExternalQ` | **yes** — `solver_outputs/omega3p/pillbox-rtop+coax.omega3p.out` | same |
| S3P \|S\| magnitudes | **yes** — `solver_outputs/s3p_90DegreeBend/Reflection.out` | implemented and validated by these fixtures |
| S3P complex S-parameters | **yes** — `solver_outputs/s3p_90DegreeBend/SParameter.out` | none |
| S3P port mode profiles | **yes** — `solver_outputs/s3p_90DegreeBend/PortRef7_0.out` | none |
