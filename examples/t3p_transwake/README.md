# T3P transverse wakefield (acdtool `postprocess transwake`)

A `cubit → t3p → acdtool` workflow that computes the **transverse** wake of a
cavity from a half model, using acdtool's `postprocess transwake`.

```
workflow:  cubit → t3p → acdtool ('postprocess transwake')
mode:      single
```

Model and input files are adapted from the ACE3P tutorial `t3p/cavity-half`
example: a half model of an S-band pillbox cavity with a waveguide slot, driven by
a Gaussian bunch offset 6.25 mm off axis.

## What makes this example different

**It is the chain that used to be rejected.** `acdtool` declared a blanket
requirement on a frequency-domain solution, so `[cubit, t3p, acdtool]` was a
`WorkflowValidationError` — even though `transwake`, `coaxsignal` and
`volmontomode` are precisely *time-domain* postprocessors. The requirement now
comes from the command: `postprocess rf` still requires an `em_solution` (so
listing *that* after a T3P solver is still an error, and should be), while the
time-domain commands require a `td_solution` and chain after `t3p` normally.

**The figure of merit is read by `t3p`, not by `acdtool`.** `transwake` writes its
result *over* `t3p_results/OUTPUT/wakefield.out` — the file T3P itself wrote — so
there is exactly one wakefield parser and one module to ask, whether or not
acdtool ran:

```yaml
output_parameters :
  'K' : {module: t3p, quantity: kick_factor}
```

This is not an oversight in the example. Asking the `acdtool` module for a
quantity here raises an error that says so and names the `t3p` spec to use
instead.

**Ordering matters, and is handled.** In DAG order T3P runs, parses
`wakefield.out` (the *longitudinal* result), and only then does acdtool overwrite
it with the transverse one. Left alone, the workflow would report the longitudinal
loss factor — a wrong-but-plausible number. So the acdtool step asks the producing
module to re-read its output after a command that rewrites it, and `t3p` reports
the transverse result. If you ever see a `loss_factor` come back from a chain like
this, that re-read is what has gone wrong.

**Why a transverse wake needs a postprocessing step at all.** A half model cannot
carry a genuinely off-axis beam on both sides of the symmetry plane, so the
transverse wake is recovered from the on-contour longitudinal wake via the
Panofsky-Wenzel theorem. `args` gives the two transverse points that define the
offset direction:

```yaml
  - module : acdtool
    command : 'postprocess transwake'
    args : [0.0, 0.0, 0.0, 0.0125]     # (x1, y1) and (x2, y2), in metres
```

The jobname acdtool takes as its *first* argument is **injected** from the `t3p`
module — the results directory that module actually resolved — so it is not
repeated in the YAML and it follows a `results_dir:` override automatically.

## Files

| File | Role |
| --- | --- |
| `pillboxwg2.jou` | Cubit journal — builds the half model and exports `pillboxwg2.gen` |
| `pillboxwg2-closed.t3p` | T3P input: offset Gaussian bunch, time stepping, volume + wakefield monitors |
| `t3p_transwake.yaml` | The run configuration |
| `run_lume-ace3p_t3p_transwake_perlmutter.batch` | NERSC Perlmutter job script |
| `run_lume-ace3p_t3p_transwake_s3df.batch` | SLAC S3DF job script |

The mesh is not checked in: Cubit writes `pillboxwg2.gen` and `acdtool
meshconvert` converts it to the `pillboxwg2.ncdf` the `.t3p` file references.
Unlike most of the tutorial journals this one leaves its `transform mesh output
scale` line commented out, so the mesh is already in metres — which is why the
bunch `StartPoint` and the `transwake` arguments are metres too.

## Running

```bash
run-lume-ace3p t3p_transwake.yaml    # or sbatch one of the .batch scripts
```

Without an ACE3P environment the workflow auto-enables dry-run: the workdir gets a
`DRY_RUN.txt` with one block per step (including the acdtool command, its `args`
and the injected jobname), and the result table is produced with the solver
columns as `NaN`.

## Output

`transwake_output.txt`, tab-delimited, **long format** — one row per `s`:

| Column | Meaning |
| --- | --- |
| `cell_radius`, `iris_radius` | the nominal journal geometry, in metres |
| `s` | wake coordinate, m (the field index) |
| `K` | transverse kick factor, V/pC — a per-run scalar, so it repeats down every row |
| `W_trans` | transverse wake potential at this `s`, V/pC |
| `W_at_1m` | per-run scalar: `W_trans` at the sample nearest `s = 1 m` |

T3P's own output lands in `lume-ace3p_transwake_workdir/t3p_results/OUTPUT/`.
After the acdtool step, `wakefield.out` carries the transverse header:

```
# T3P transverse wakefield result using transverse points:
# (0.00000000000000e+00,0.00000000000000e+00) and
# (0.00000000000000e+00,1.25000000000000e-02)
# with offset 1.25000000000000e-02 m
# Kick factor = 9.64058337896157e-02 V/pC
#          s[m]        W_trans(s)[V/pC]     I_bunch(s)[C/m]
```

Asking for `loss_factor` against that file raises an error naming what the run
*does* report, rather than returning `NaN` — a transverse run has no loss factor.
Alongside it are `wakefield.z*.dat` (per-contour data), `Bunch0.out`, the
`mymon_t*ps.out` volume dumps, and `t3p.out` (the log, which echoes the input T3P
actually parsed — the first place to look when a result is surprising).

## Two things to know before scaling this up

**Disk.** The `.t3p` file declares a `Volume` monitor writing a full field dump
every 0.2 ns — roughly 60 MB for this model, and multiplied by every point if you
turn this into a sweep. LUME-ACE3P writes whatever monitors your input file
declares and does not prune them. To cut the cost, widen the monitor's `TimeStep`
or delete that block; the wakefield result does not depend on it.

**Checkpoint/restart.** The `CheckPoint` section is passed through like any other
input section and T3P will write `t3p_results/CHECKPOINT` if asked, but LUME-ACE3P
does **not** orchestrate restarts — it will not detect an existing checkpoint or
set `Action: restart`. Size the allocation for a full run.

## Adapting this to your own model

* **Turning this into a sweep.** Change `mode.type` to `parameter_sweep`, give a
  journal variable a `{min, max, num}` range, and set `workdir_mode: 'auto'`. Each
  point is a full time-domain run plus a postprocess pass, so cost scales
  linearly and the volume dumps scale with it.
* **A longitudinal wake instead.** Drop the acdtool step: T3P's own `WakeField`
  monitor reports a loss factor for an on-axis beam, which is what
  [`../t3p_sweep`](../t3p_sweep) does. `postprocess wake_new` / `wake_direct` are
  the longitudinal counterparts of `transwake` and write the same file; they are
  recorded in the command table but not yet available as workflow steps.
* **A coaxial port signal.** `command: 'postprocess coaxsignal'` needs no `args`
  and writes a *new* file (`<jobname>/OUTPUT/signal.out`, columns `t V I`), so it
  does not overwrite T3P's output and needs no re-read. Its result is a column
  table, exposed as a field artifact rather than as table columns.
* **Optimization.** `K` is already a per-run scalar, so it can be a VOCS
  objective directly under `scalar_optimize` — bearing in mind each evaluation is
  a full time-domain run plus a postprocess pass.
