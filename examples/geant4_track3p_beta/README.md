# geant4_track3p_beta

A beta *sweep* of the full downstream-dose pipeline on the declarative
module/mode schema:

```
workflow:  track3p_source -> particles -> geant4
mode:      parameter_sweep
```

This runs the same chain as [`../geant4_dose_single`](../geant4_dose_single): an
externally-produced Track3P particle dump is field-emission-weighted into a
Geant4-format source file (`particles.data`), which a Geant4 run turns into
dose/edep voxel grids. Under `parameter_sweep` it executes one Geant4 run per
swept `beta` value. The sweep declared under `input_parameters` steps `beta`
from 40 to 60 in 5 points (40, 45, 50, 55, 60), one workdir per value.

The weighting uses the plain Fowler–Nordheim form of the LCLS-II reference
converter (`fn_model: fn`, the default): work function 4.2 eV, an emission time
of one RF period at 1.2999 GHz, impacts below 1 keV dropped, real-valued
weights, and macroparticles standing for less than one electron left out of
`particles.data`. With those settings the `particles` module reproduces that
study's shipped particle files.

**Why 40–60 and not the study's own 100–150.** The weight depends on the
*enhanced* field `β·E`, and this shared dump is not the study's cavity: its
`InitialNormalField` averages 3.7e7 V/m, roughly 4× Lixin Ge's cryomodule, so the
`β·E` the study reaches at β = 120 is reached here near β = 30. Swept over
100–150, every macroparticle clears the one-electron cut at every point and the
sweep writes a flat 144 732 rows — nothing varies. Across 40 → 60 the surviving
count climbs 2055 → 60 736 → 124 428 → 144 732 → 144 732 and the summed weight
spans six decades, which is the onset this example exists to show. Transplanting a
β range between dumps does not work; size it against the dump's own fields.

Particle tracking is done externally and the dump is supplied to the
`track3p_source` module (a `track3p` module can run Track3P in the pipeline, but a
cryomodule-scale run costs about 50 node-minutes per field level, so studies
over pre-run dumps start here). Unlike
`geant4_dose_single`, which uses a fixed per-bin `beta` vector and runs once,
here the `particles` module's `beta_input: beta` broadcasts the single swept
scalar to all `num_bins` bins (run 1 → `[35]*8`, run 2 → `[41.25]*8`, …). Unlike
[`../geant4_beta_surrogate`](../geant4_beta_surrogate), which scatters a DOE over
an 8-D per-bin `beta` vector, this is a one-axis tensor sweep of a single knob.

## Assets

The large *shared* inputs live in [`../assets/`](../assets) and are referenced
by relative path from this example's YAML:

- `sample_track3p_particles.txt` — the external Track3P dump
- `7cell_solid_whole.stl`, `7cell_cavity_whole.stl` — geometry

The Geant4 input file `input_7cell.geant4` is *not* shared; each Geant4 example
carries its own. It names its STL geometry by bare filename, so the YAML lists
the `../assets/` STLs under `geant4_geometry_files` and the module stages them
into each per-run workdir. Run from this directory so the `../assets/` paths
resolve.

## Running

```bash
run-lume-ace3p geant4_track3p_beta.yaml
```

On S3DF (SLAC), submit the batch script instead:

```bash
sbatch run_lume-ace3p_geant4_track3p_beta_s3df.batch
```

Geant4 is only installed on S3DF, so there is no Perlmutter batch script. Each
Geant4 step launches as a nested
`srun -n 1 -c <geant4_threads> <geant4-app> <input>` with `geant4_threads: 120`,
so the allocation reserves a full milano node: `--cpus-per-task=120` MUST be
`>= geant4_threads` or the nested `srun` cannot allocate its cores. The swept
points run sequentially in the single allocation.

With the Geant4 binary absent the run is a **dry run**: the particle-weighting
step still executes for real and writes `particles.data` per point, but the dose
scalars are `NaN` until a real Geant4 run produces the scoring files.
