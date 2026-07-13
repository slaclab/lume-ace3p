# geant4_dose_single

A single-evaluation run of the full downstream-dose pipeline on the declarative
module/mode schema:

```
workflow:  track3p_source -> particles -> geant4
mode:      single
```

This is the runnable multi-step chain the module architecture is built to
express: an externally-produced Track3P particle dump is field-emission-weighted
into a Geant4-format source file (`particles.data`), which a Geant4 run turns
into dose/edep voxel grids; `output_parameters` then pulls scalar dose/edep
totals out of that output into a one-row result table.

There is **no in-pipeline Track3P/T3P solver** — particle tracking is done
externally and the dump is supplied to the `track3p_source` module. Unlike
[`../geant4_track3p_beta`](../geant4_track3p_beta) (which *sweeps* a broadcast
`beta` scalar), this example uses a fixed per-bin `beta` vector and runs once.

## Assets

The large shared inputs are **symlinks** into `../geant4_track3p_beta/`:

- `sample_track3p_particles.txt` — the external Track3P dump
- `input_7cell.geant4` — the Geant4 `key = value` input file
- `7cell_solid_whole.stl`, `7cell_cavity_whole.stl` — geometry

Run from this directory so the symlinks resolve.

## Running

```bash
run-lume-ace3p geant4_dose_single.yaml
```

With the Geant4 binary absent the run is a **dry run**: the particle-weighting
step still executes for real and writes `particles.data`, but the dose scalars
are `NaN` until a real Geant4 run produces the scoring files.
