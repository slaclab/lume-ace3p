# geant4_beta_surrogate

A two-stage build of a dose surrogate on the declarative module/mode schema:
collect a design-of-experiments (DOE) of Geant4 runs, then fit a forward model.
The two stages are separate YAMLs sharing one store:

```
geant4_beta_surrogate.yaml         geant4_beta_surrogate_train.yaml
  workflow: track3p_source ->        workflow: (carried for schema symmetry;
            particles -> geant4                 not executed)
  mode:     collect_training_data    mode:     train_surrogate
```

**`geant4_beta_surrogate.yaml` (`mode: collect_training_data`)** drives the same
`track3p_source -> particles -> geant4` chain as
[`../geant4_track3p_beta`](../geant4_track3p_beta), but instead of sweeping one
scalar it scatters a DOE (Sobol by default, `num_samples: 16`, `seed: 0`) over
the 8-D per-bin field-enhancement vector `beta = (beta0 … beta7)`. The
`particles` module maps one input variable per bin via
`beta_inputs: [beta0 … beta7]` (rather than `beta_input: beta` broadcasting a
single scalar), and `bin_edges` is **required and fixed** (length `num_bins + 1
= 9`) so the beta→dose binning is stationary across the whole campaign. Each
sample persists a `(beta, dose_grid)` training pair into a resumable store
(`geant4_beta_surrogate_store`). This is the Geant4-heavy stage — it runs the
full Monte-Carlo chain once per sample.

**`geant4_beta_surrogate_train.yaml` (`mode: train_surrogate`)** is the CPU-only
follow-up. It is a store-consuming mode: it does **not** run the Geant4 chain
(its `workflow:` block is carried only for schema symmetry), it reads the
already-collected `(beta, dose)` pairs and fits a reduced-basis PCA-GP forward
surrogate `beta ∈ R^8 -> dose profile` — stack the dose grids, subtract the
mean, SVD to the leading POD modes (`variance: 0.99` cumulative energy), then
fit one Gaussian Process per retained coefficient. The model is saved under
`model_dir` (`geant4_beta_surrogate_store/surrogate`) with a 20% holdout
accuracy report.

## Assets

The large *shared* inputs live in [`../assets/`](../assets) and are referenced
by relative path from both YAMLs:

- `sample_track3p_particles.txt` — the external Track3P dump
- `7cell_solid_whole.stl`, `7cell_cavity_whole.stl` — geometry

The Geant4 input file `input_7cell.geant4` is *not* shared — it lives in this
example directory (each Geant4 example carries its own). It names its STL
geometry by bare filename; because those STLs live in `../assets/` rather than
alongside the input, the collection YAML lists them under `geant4_geometry_files`
so the module stages them into each per-sample workdir. Run from this directory
so the `../assets/` paths resolve.

## Running

Collect the training data, then fit the surrogate:

```bash
run-lume-ace3p geant4_beta_surrogate.yaml         # Geant4-heavy DOE collection
run-lume-ace3p geant4_beta_surrogate_train.yaml   # CPU-only fit (no Geant4)
```

On S3DF (SLAC), submit the collection batch script:

```bash
sbatch run_lume-ace3p_geant4_beta_surrogate_s3df.batch
```

The batch script runs **only the collection YAML**; the train step is a quick
CPU-only follow-up you run afterward with
`run-lume-ace3p geant4_beta_surrogate_train.yaml`. This example runs Geant4,
which is only installed on S3DF — there is no Perlmutter batch script. Each
Geant4 step launches as a nested `srun -n 1 -c <geant4_threads> <geant4-app>
<input>` with `geant4_threads: 120`, so the allocation reserves a full milano
node: `--cpus-per-task=120` MUST be `>= geant4_threads`. The 16 samples run
sequentially in the single allocation, and the store is resumable — a timed-out
job can be resubmitted to finish the remaining samples.

With the Geant4 binary absent the collection run is a **dry run**: the
particle-weighting step still executes for real and writes `particles.data` per
sample, but the dose grids are `NaN` until a real Geant4 run produces the
scoring files.
