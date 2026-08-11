# T3P wakefield parameter sweep

A `cubit → t3p` workflow that sweeps two pillbox-cavity geometry parameters and
reports the longitudinal wakefield of each geometry.

T3P is the ACE3P **time-domain** solver. Where S3P sweeps frequency and produces
S-parameters, T3P steps through time with a bunch traversing the structure and
produces a wake potential `W(s)` against the wake coordinate `s`, plus a
per-geometry figure of merit — the **loss factor** (longitudinal) or **kick
factor** (transverse).

Model and input files are adapted from the ACE3P tutorial `t3p/cavity-quarter`
example: a quarter model of an S-band pillbox cavity with a waveguide slot.

## Files

| File | Role |
| --- | --- |
| `pillboxwg.jou` | Cubit journal — builds the quarter model and exports `pillboxwg.gen` |
| `pillboxwg-closed.t3p` | T3P input: bunch definition, time stepping, monitors |
| `t3p_sweep.yaml` | The sweep configuration |
| `run_lume-ace3p_t3p_sweep_perlmutter.batch` | NERSC Perlmutter job script |
| `run_lume-ace3p_t3p_sweep_s3df.batch` | SLAC S3DF job script |

The mesh is not checked in: Cubit writes `pillboxwg.gen` and `acdtool
meshconvert` converts it to the `pillboxwg.ncdf` that the `.t3p` file
references.

## Running

```bash
run-lume-ace3p t3p_sweep.yaml            # or sbatch one of the .batch scripts
```

Without an ACE3P environment the workflow auto-enables dry-run: each grid
point's workdir gets a `DRY_RUN.txt` describing the step that would have run, and
the result table is produced with the solver columns as `NaN`.

## Output

`t3p_sweep_output.txt`, tab-delimited, **long format** — one row per
`(cell_radius, iris_radius, s)`:

| Column | Meaning |
| --- | --- |
| `cell_radius`, `iris_radius` | the swept geometry, in metres |
| `s` | wake coordinate, in metres (the field index) |
| `loss_factor` | per-run scalar, V/pC — repeats down each run's rows |
| `W` | wake potential at this `s`, V/pC |
| `W_at_10cm` | per-run scalar: `W` at the grid point nearest `s = 0.10 m` |

T3P's own output lands under each workdir in `t3p_results/OUTPUT/`:
`wakefield.out` (the parsed file: header figure of merit, then `s`, `W`,
`I_bunch` columns), `wakefield.z*.dat` (per-contour wake data), `Bunch0.out`
(the bunch current), the `mymonts_t*ps.out` volume dumps, and `t3p.out` (the log,
which echoes the input T3P actually parsed — the first place to look when a
result is surprising).

## Two things to know before scaling this up

**Disk.** The `.t3p` file declares a `Volume` monitor writing a full field dump
every 0.2 ns — about 60 MB per grid point for this model, so the 3×3 sweep here
costs roughly 0.5 GB. Larger models are much heavier (the tutorial's BPM case is
~470 MB per run). LUME-ACE3P writes whatever monitors your input file declares
and does not prune them. To cut the cost, widen `TimeStep` in the `Volume`
monitor block or delete that block from `pillboxwg-closed.t3p`; the wakefield
result does not depend on it.

**Checkpoint/restart.** A `CheckPoint` section in the `.t3p` file is passed
through like any other input section, and T3P will write `t3p_results/CHECKPOINT`
if asked. LUME-ACE3P does **not** orchestrate restarts — it will not detect an
existing checkpoint or set `Action: restart` for you, so a sweep point whose job
runs out of wall time restarts from scratch on re-run. Size the allocation for a
full run.

## Adapting this to your own model

* **Transverse wakes.** Offset the bunch (`LoadingInfo.StartPoint`) and set the
  `WakeField` monitor's contour accordingly; T3P then reports a kick factor
  instead of a loss factor. Ask for `quantity: kick_factor` — requesting
  `loss_factor` on a transverse run raises an error naming what is actually
  available, rather than silently returning `NaN`.
* **Sweeping T3P parameters** rather than geometry: put them under
  `input_parameters: ace3p:` addressed by their path in the input file, e.g.
  `LoadingInfo: {Bunch: {Sigma: {min: 0.005, max: 0.02, num: 4}}}`. Note that
  T3P input keys may contain spaces (`Number of sigmas`) and must be spelled
  exactly as they appear in the file.
* **Optimization.** The `W_at_10cm` output shows the scalar-at-a-position form an
  Xopt objective needs; point a `scalar_optimize` mode's VOCS objective at that
  name. Bear in mind each evaluation is a full time-domain run.
