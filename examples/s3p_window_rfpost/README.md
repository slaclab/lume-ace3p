# S3P + acdtool postprocessing (RF window)

A `cubit → s3p → acdtool` workflow that sweeps the thickness of a ceramic RF
window and reports its S-parameters, with `acdtool postprocess rf` run against
the **S3P** solution.

```
workflow:  cubit → s3p → acdtool
mode:      parameter_sweep
```

Model and input files are adapted from the ACE3P tutorial `s3p/window` example: a
quarter model of a WR-284 waveguide with a ceramic disk brazed into a short
cavity section, driven from two waveguide ports.

## What makes this example different

**`postprocess rf` on S3P output.** Every other acdtool example here
postprocesses an Omega3P eigensolve. `window.rfpost` points at the S3P results
instead:

```
RFField
{
   ResultDir   = s3p_results   // Jobname
   FreqScanID  =      2        // For S3P only: frequency scan index
   ModeID      =      0
   ...
}
```

`FreqScanID` selects **which point of the frequency scan** to postprocess — here
the third, 2.556 GHz — and for S3P `ModeID` means a **port mode** (an excitation),
not an eigenmode. `gradient = -1` means "no field scaling", which is what selects
the point-scaled variant of the `[scaling]` block in the output.

**Two index axes, one table.** S3P's results are indexed by `Frequency` and
acdtool's by `ModeID`, and a flat result table can only carry one index. The rule
is *first producer in resolved DAG order wins*, so the table is
frequency-indexed — which is also the right answer here, since the whole point of
the run is a frequency scan. acdtool's per-mode arrays and its `ALLFieldOnLine`
curve files do not become table columns; they are read into the acdtool module's
output and are available through the module's `field()`, and the curve files stay
on disk in each workdir (`field1_0`, `field1_0.ec`, `field1_0.bc`, …).

Note that in a frequency-indexed sweep no per-row field artifact `.npz` is
written at all: the table's rows already *are* the S3P spectrum, so there is
nothing left to persist beside them. If you want the acdtool curves in a
post-processing script, read them from the workdir.

## Files

| File | Role |
| --- | --- |
| `window.jou` | Cubit journal — builds the quarter model, meshes it, exports `window.gen` |
| `window.s3p` | S3P config: ceramic/vacuum materials, two waveguide ports, a 2.356–3.856 GHz scan in 0.1 GHz steps |
| `window.rfpost` | acdtool config: `RFField` pointed at `s3p_results` + one `ALLFieldOnLine` block |
| `s3p_window_rfpost.yaml` | The sweep configuration |
| `run_lume-ace3p_s3p_window_rfpost_perlmutter.batch` | NERSC Perlmutter job script |
| `run_lume-ace3p_s3p_window_rfpost_s3df.batch` | SLAC S3DF job script |

The mesh is not checked in: Cubit writes `window.gen` and `acdtool meshconvert`
converts it to the `window.ncdf` the `.s3p` file references.

The journal **joins the tutorial's two** (`step1-Make-window.jou` builds the solid
and exports `window.sat`; `step2-Mesh-window.jou` imports it and meshes it),
because a `cubit` module plays exactly one journal. The ACIS round-trip is
dropped. What makes that safe is the `compress ids` before the meshing half: it
renumbers the surviving entities contiguously, which is the same numbering an
export/import pair produces, so the surface IDs the meshing half names still
resolve.

## Running

```bash
run-lume-ace3p s3p_window_rfpost.yaml    # or sbatch one of the .batch scripts
```

Without an ACE3P environment the workflow auto-enables dry-run: each grid point's
workdir gets a `DRY_RUN.txt` describing the steps that would have run, and the
result table is produced with the solver columns as `NaN`.

**Budget for it.** Three geometries × a 16-point frequency scan is 48 S3P solves,
each one a separate linear system — this is the heaviest of the three RF examples
here and does not fit the 30-minute debug QOS.

## Output

`window_sweep_output.txt`, tab-delimited, **long format** — one row per
`(wdwt, Frequency)`:

| Column | Meaning |
| --- | --- |
| `wdwt` | the swept ceramic thickness, mm |
| `Frequency` | the scan point, Hz (the field index) |
| `S11` | \|S(0,0)\| — reflection at port 7 |
| `S21` | \|S(0,1)\| — transmission to port 8 |
| `S21_phase` | phase of S(0,1), degrees |
| `S11_at_scan_point` | per-run scalar: \|S(0,0)\| at 2.556 GHz — repeats down each run's rows |
| `m_factor` | per-run scalar from acdtool's `[scaling]` block: the normalized-to-physical field conversion |

`S(m,n)` indices are **S-matrix indices, not port numbers.** The run's
`IndexMap` records the mapping — here index 0 is port 7 mode 0 and index 1 is
port 8 mode 0, so `S(0,0)` is the reflection and `S(0,1)` the transmission. Check
the `#Index mapping:` header of `s3p_results/Reflection.out` for your own model
rather than assuming the order.

S3P's own output lands under each workdir in `s3p_results/`: `Reflection.out`
(the \|S\| magnitudes), `SParameter.out` (the same matrix as complex pairs — the
source of `S21_phase`), `PortRef7.out` / `PortRef8.out` (port mode field
profiles), one `.mod` file per port/mode/frequency for ParaView, and
`s3p.output` / `s3p.warn`. acdtool writes `rfpost.out` plus the `ALLFieldOnLine`
curve files into the workdir itself.

## Adapting this to your own model

* **A different scan point to postprocess.** Change `FreqScanID` in
  `window.rfpost`. It is a zero-based index into the `FrequencyScan`, so it moves
  if you change `Start` / `Interval` — and it is the one number in this example
  that silently means something different when the scan changes.
* **Other rfpost blocks.** Set that block's `ionoff = 1`. The RF-parameter blocks
  (`RoverQ`, `kickFactor`, …) are eigenmode quantities and are not meaningful for
  a transmission problem; the curve and field-map blocks are. See
  [](../../docs/acdtool_reference.md) for the full block list, each block's
  output shape, and where each one writes.
* **An optimization.** `S11_at_scan_point` is the scalar-at-a-frequency form an
  Xopt objective needs: point a `scalar_optimize` mode's VOCS objective at that
  name and minimize it to match the window. Each evaluation is a full frequency
  scan, so consider narrowing `FrequencyScan` first.
* **Sweeping S3P settings** rather than geometry: put them under
  `input_parameters: ace3p:` addressed by their path in the input file. Note
  `window.s3p` has **two** `Material:` blocks; the `ace3p:` block preserves
  duplicate keys and merges them positionally, so declare both in order and use
  each one's `Attribute:` to keep the pairing readable.
