# T3P power balance — three `Power` monitors on one run

A `cubit → t3p` sweep that measures where the energy in a pulse goes: **in** at
the excitation port, **out** at the far port, and **dissipated** on a lossy
coated wire — all three from one time-domain run per sweep point.

This is the example that could not be written before. LUME-ACE3P used to read
exactly one of T3P's six monitor types (`WakeField`) and ignore the rest without
saying so, which meant a run declaring three `Power` monitors produced three
files and reported nothing from any of them. See
[](../../docs/t3p_reference.md) for the six types and
[](../../docs/yaml_reference.md#t3p-module) for the `monitor:` key.

Model and input files are adapted from the ACE3P tutorial `t3p/SIBC` example: a
quarter model of a coaxial line whose centre wire carries a thin lossy dielectric
coating, driven by a 5 GHz Gaussian pulse through a surface-impedance boundary.

## What makes this example the demonstration

**`Name` is the selector, not `Type`.** All three monitors are `Type: Power`, so
the type cannot address one of them:

```yaml
'P_in'   : {module: t3p, monitor: inputPower,   quantity: P}
'P_out'  : {module: t3p, monitor: outputPower,  quantity: P}
'P_wall' : {module: t3p, monitor: wallossPower, quantity: P}
```

Dropping `monitor:` here raises an error naming all three candidates rather than
silently picking the first. (Where only one monitor can answer — every wakefield
workflow, for instance — `monitor:` is omittable.)

**The index axis is time.** This run declares no `WakeField` monitor, so there is
no wake coordinate: the result table goes long-format over `t`, one row per
`(coating thickness, t)`, exactly as a wake run goes long over `s` and an S3P
sweep over `Frequency`. A run with *both* a wake and a time-series monitor is
indexed on `s` — one axis per module — and its time series ride in the per-run
field artifact instead.

**The swept axis is an ACE3P input parameter.** The coating thickness lives in
`SIBC.t3p`, not in the journal, so it is addressed by its path there:

```yaml
ace3p :
  'ModelInfo' :
    'SurfaceMaterial' :
      'Coating' :
        'Thickness' : [0.5e-3, 1.0e-3, 2.0e-3]
```

The mode iterates it exactly like a geometry axis.

## Files

| File | Role |
| --- | --- |
| `coating.jou` | Cubit journal — builds the quarter model and exports `test-sibc.gen` |
| `SIBC.t3p` | T3P input: pulse loading, surface material + coating, the three `Power` monitors |
| `t3p_power_balance.yaml` | The sweep configuration |
| `power_balance.py` | Adds the `P_balance` column to the result table and plots it |
| `run_lume-ace3p_t3p_power_balance_perlmutter.batch` | NERSC Perlmutter job script |
| `run_lume-ace3p_t3p_power_balance_s3df.batch` | SLAC S3DF job script |

The mesh is not checked in: Cubit writes `test-sibc.gen` and `acdtool
meshconvert` converts it to the `test-sibc.ncdf` that `SIBC.t3p` references. The
journal's sideset IDs are what `SIBC.t3p`'s `ReferenceNumber`s refer to (3 = the
coated wire, 4 = the excitation port, 5 = the far port), so renumbering them means
editing both files.

## Running

```bash
run-lume-ace3p t3p_power_balance.yaml     # or sbatch one of the .batch scripts
python power_balance.py                   # adds P_balance, writes power_balance.png
```

Without an ACE3P environment the workflow auto-enables dry-run: each sweep
point's workdir gets a `DRY_RUN.txt` describing the step that would have run, and
the result table is produced with the power columns as `NaN`. `power_balance.py`
says so rather than plotting an empty figure.

## Output

`power_balance_output.txt`, tab-delimited, **long format**:

| Column | Meaning |
| --- | --- |
| `ace3p:ModelInfo.SurfaceMaterial.Coating.Thickness` | the swept coating thickness, m |
| `t` | time, s (the field index) |
| `P_in` | power through the excitation port, W |
| `P_out` | power leaving the far port, W |
| `P_wall` | power dissipated on the coated wire, W |
| `P_wall_at_5ns` | per-run scalar: `P_wall` at the sample nearest `t = 5 ns` |

Only *swept* axes become columns, so the fixed `meshsize` appears in each
workdir's `DRY_RUN.txt` rather than in the table.

`power_balance.py` then writes `power_balance_output_balanced.txt` with

```
P_balance = P_in - P_out - P_wall
```

appended. This is arithmetic over columns that are already in the table, done in
a script because `output_parameters` names quantities to extract and does not
evaluate expressions over them.

Read the balance with the sign convention the monitors use: while the pulse is
still inside the structure the difference is energy in flight, and it is only
after the pulse has cleared that the three should account for each other. A
balance that stays large afterwards points at a monitor on the wrong reference
surface, an absorbing boundary reflecting, or a mesh too coarse for the coating.

T3P's own output lands under each workdir in `t3p_results/OUTPUT/`:
`inputPower.out`, `outputPower.out` and `wallossPower.out` (two columns each,
time and power, **no header row** — the column names come from
`references/t3p-commands.pdf`, not from the file), `Bunch0.out`, the
`fieldts_t*ps.out` volume dumps, and `t3p.out` (the log, which echoes the input
T3P actually parsed — the first place to look when a result is surprising).

## Adapting this to your own model

* **`SurfacePowerLoss` instead of `Power` on the wire.** T3P documents a
  `SurfacePowerLoss` monitor type for exactly this measurement, and this example
  deliberately uses `Power` on the impedance surface because that is what the
  tutorial does — which means `SurfacePowerLoss` has no real output behind it
  anywhere in this package. It is implemented and marked unvalidated; if you run
  one, the output format is worth reporting back. See
  `tests/fixtures/acdtool/COVERAGE.md`.
* **A `Point` monitor alongside these.** Add one and its fields ride in the field
  artifact; to put a field component in the table, narrow it to an instant with
  `at: {t: <seconds>}`. The nearest sample is taken, since the time grid is a
  consequence of `TimeStepping: DT`.
* **Optimization.** `P_wall_at_5ns` is the scalar-at-an-instant form an Xopt
  objective needs — point a `scalar_optimize` mode's VOCS at that name to, say,
  minimize dissipation over coating thickness. Each evaluation is a full
  time-domain run.
* **Disk.** The `Volume` monitor in `SIBC.t3p` writes a field dump every 0.5 ns,
  which is 20 dumps per run for ParaView. Widen its `TimeStep` or delete the block
  if you do not need them; the power balance does not depend on it.
* **Checkpoint/restart.** `SIBC.t3p` carries the tutorial's `CheckPoint` block, so
  T3P will write `t3p_results/CHECKPOINT`. LUME-ACE3P does **not** orchestrate
  restarts — it will not detect an existing checkpoint or set `Action: restart` for
  you, so a sweep point that runs out of wall time restarts from scratch.
