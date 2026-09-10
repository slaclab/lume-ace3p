# T3P power balance — three `Power` monitors on one run

A `cubit → t3p` sweep that measures where the energy in a pulse goes: **in** at
the excitation port, **out** at the far port, and **dissipated** on a lossy
coated wire, all from one time-domain run per sweep point. See
[](../../docs/t3p_reference.md) for T3P's six monitor types and
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

Dropping `monitor:` here raises an error naming all three candidates. Where only
one monitor can answer (every wakefield workflow, for instance), it can be
omitted.

**The index axis is time.** This run declares no `WakeField` monitor, so the
result table goes long-format over `t`, one row per `(coating thickness, t)`, as
a wake run goes long over `s` and an S3P sweep over `Frequency`. A run with both
a wake and a time-series monitor is indexed on `s` (one axis per module); its
time series ride in the per-run field artifact.

**The swept axis is an ACE3P input parameter.** The coating thickness lives in
`SIBC.t3p`, not in the journal, so it is addressed by its path there:

```yaml
ace3p :
  'ModelInfo' :
    'SurfaceMaterial' :
      'Coating' :
        'Thickness' : [0.5e-3, 1.0e-3, 2.0e-3]
```

The mode iterates it like a geometry axis.

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
coated wire, 4 = the excitation port, 5 = the far port). Renumbering them means
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

Only swept axes become columns, so the fixed `meshsize` appears in each
workdir's `DRY_RUN.txt` rather than in the table.

`power_balance.py` then writes `power_balance_output_balanced.txt` with

```
P_balance = P_in - P_out - P_wall
```

appended. `output_parameters` names quantities to extract and does not evaluate
expressions, so this arithmetic lives in a script.

While the pulse is inside the structure the difference is energy in flight; the
three should only balance after it has cleared. A balance that stays large
afterwards points at a monitor on the wrong reference surface, an absorbing
boundary reflecting, or a mesh too coarse for the coating.

T3P's own output lands under each workdir in `t3p_results/OUTPUT/`:
`inputPower.out`, `outputPower.out` and `wallossPower.out` (two columns each,
time and power, **no header row**; the column names come from
`references/t3p-commands.pdf`), `Bunch0.out`, the `fieldts_t*ps.out` volume
dumps, and the log `t3p.out`, which echoes the input T3P actually parsed.

## Adapting this to your own model

* **`SurfacePowerLoss` instead of `Power` on the wire.** T3P documents a
  `SurfacePowerLoss` monitor type for this measurement. This example follows the
  tutorial and uses `Power` on the impedance surface, so `SurfacePowerLoss` has
  no real output behind it in this package: it is implemented and marked
  unvalidated. If you run one, report the output format back. See
  `tests/fixtures/acdtool/COVERAGE.md`.
* **A `Point` monitor alongside these.** Add one and its fields ride in the field
  artifact. To put a field component in the table, narrow it to an instant with
  `at: {t: <seconds>}`; the nearest sample is taken, since the time grid follows
  from `TimeStepping: DT`.
* **Optimization.** `P_wall_at_5ns` is the scalar-at-an-instant form an Xopt
  objective needs. Point a `scalar_optimize` mode's VOCS at it to minimize
  dissipation over coating thickness, at one full time-domain run per evaluation.
* **Disk.** The `Volume` monitor in `SIBC.t3p` writes a field dump every 0.5 ns,
  20 dumps per run for ParaView. Widen its `TimeStep` or delete the block if you
  do not need them; the power balance does not depend on it.
* **Checkpoint/restart.** `SIBC.t3p` carries the tutorial's `CheckPoint` block, so
  T3P will write `t3p_results/CHECKPOINT`. LUME-ACE3P does **not** orchestrate
  restarts (no checkpoint detection, no `Action: restart`), so a sweep point that
  runs out of wall time restarts from scratch.
