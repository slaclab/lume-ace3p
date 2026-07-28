# geant4_track3p_beta

A beta *sweep* of the full downstream-dose pipeline on the declarative
module/mode schema:

```
workflow:  track3p_source -> particles -> geant4
mode:      parameter_sweep
```

This runs the same runnable multi-step chain as [`../geant4_dose_single`](../geant4_dose_single)
— an externally-produced Track3P particle dump is field-emission-weighted into a
Geant4-format source file (`particles.data`), which a Geant4 run turns into
dose/edep voxel grids — but under `parameter_sweep`: it executes one Geant4 run
per swept `beta` value. The sweep declared under `input_parameters` steps `beta`
from 40 to 60 in 5 points (40, 45, 50, 55, 60), one workdir per value.

There is **no in-pipeline Track3P/T3P solver** — particle tracking is done
externally and the dump is supplied to the `track3p_source` module. Unlike
`geant4_dose_single` (which uses a fixed per-bin `beta` vector and runs once),
here the `particles` module's `beta_input: beta` broadcasts the single swept
scalar to all `num_bins` bins (run 1 → `[40]*8`, run 2 → `[45]*8`, …). Unlike
[`../geant4_beta_surrogate`](../geant4_beta_surrogate) (which scatters a DOE over
an 8-D per-bin `beta` vector), this is a one-axis tensor sweep of a single knob.

## Assets

The large *shared* inputs live in [`../assets/`](../assets) and are referenced
by relative path from this example's YAML:

- `sample_track3p_particles.txt` — the external Track3P dump
- `7cell_solid_whole.stl`, `7cell_cavity_whole.stl` — geometry

The Geant4 input file `input_7cell.geant4` is *not* shared — it lives in this
example directory (each Geant4 example carries its own). It names its STL
geometry by bare filename; because those STLs live in `../assets/` rather than
alongside the input, the YAML lists them under `geant4_geometry_files` so the
module stages them into each per-run workdir. Run from this directory so the
`../assets/` paths resolve.

## Running

```bash
run-lume-ace3p geant4_track3p_beta.yaml
```

On S3DF (SLAC), submit the batch script instead:

```bash
sbatch run_lume-ace3p_geant4_track3p_beta_s3df.batch
```

This example runs Geant4, which is only installed on S3DF — there is no
Perlmutter batch script. Each Geant4 step launches as a nested
`srun -n 1 -c <geant4_threads> <geant4-app> <input>` with `geant4_threads: 120`,
so the allocation reserves a full milano node: `--cpus-per-task=120` MUST be
`>= geant4_threads` or the nested `srun` cannot allocate its cores. The swept
points run sequentially in the single allocation.

With the Geant4 binary absent the run is a **dry run**: the particle-weighting
step still executes for real and writes `particles.data` per point, but the dose
scalars are `NaN` until a real Geant4 run produces the scoring files.
