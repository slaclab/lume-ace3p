# geant4_beta_surrogate

A four-stage build of a dose surrogate: collect a design-of-experiments (DOE) of
Geant4 runs, fit a forward model, then invert it as a point estimate and as a
posterior. The stages are separate YAMLs sharing one store:

```
geant4_beta_surrogate.yaml     geant4_beta_surrogate_train.yaml  geant4_beta_surrogate_invert.yaml
  workflow: track3p_source ->    (no workflow: needed)             (no workflow: needed)
            particles -> geant4
  mode: collect_training_data    mode: train_surrogate             mode: invert_optimize
                                                                   + geant4_beta_surrogate_invert_bayesian.yaml
                                                                     mode: invert_bayesian
```

Only the first stage runs Geant4. `train_surrogate`, `invert_optimize` and
`invert_bayesian` are **store-consuming** modes: they read the store or saved
model, never drive the module chain, and so carry **no `workflow:` block**, only
`mode:`. The store's `manifest.json` records the pinned `bin_edges` and
scoring-mesh invariants.

**`geant4_beta_surrogate.yaml` (`mode: collect_training_data`)** drives the same
`track3p_source -> particles -> geant4` chain as
[`../geant4_track3p_beta`](../geant4_track3p_beta), but scatters a DOE (Sobol
by default, `num_samples: 16`, `seed: 0`) over the 8-D per-bin field-enhancement
vector `beta = (beta0 … beta7)`. The `particles` module maps one input variable
per bin via `beta_inputs: [beta0 … beta7]` rather than broadcasting a scalar with
`beta_input: beta`. `bin_edges` is **required and fixed** (length `num_bins + 1
= 9`) so the beta→dose binning is stationary across the campaign. Each sample
persists a `(beta, dose_grid)` training pair into the resumable store
`geant4_beta_surrogate_store`. This is the Geant4-heavy stage.

**`geant4_beta_surrogate_train.yaml` (`mode: train_surrogate`)** is CPU-only. It
reads the collected `(beta, dose)` pairs and fits a reduced-basis PCA-GP forward
surrogate `beta ∈ R^8 -> dose profile`: stack the dose grids, subtract the mean,
SVD to the leading POD modes (`variance: 0.99` cumulative energy), then fit one
Gaussian Process per retained coefficient. The model is saved under `model_dir`
(`geant4_beta_surrogate_store/surrogate`) with a 20% holdout accuracy report.

**`geant4_beta_surrogate_invert.yaml` (`mode: invert_optimize`)** takes a target
dose profile (a stored `field.npz` or a raw Geant4 dose file) and estimates the
beta that produced it: the target is projected into the surrogate's coefficient
space and the misfit minimized over beta with bounded multi-start L-BFGS-B.
CPU-only; it queries the surrogate, not Geant4.

> **The inverse is non-unique.** The surrogate reaches beta only through its `k`
> retained POD coefficients, so the dose constrains **at most `k` combinations of
> beta**. With `k < 8` (`k = 3` is typical) some beta directions are invisible to
> the dose and many different beta reproduce it equally well. The run writes
> `identifiability.txt` reporting how many directions are pinned down and which
> combinations are flat. The `rank` column in `inversion_result.txt` orders the
> minima by misfit but is **not a ranking by evidence** when those misfits are
> all ~0; the minima are samples from one degenerate surface. A unique answer
> needs narrower `bounds` on physical grounds, or the Bayesian mode below.

**`geant4_beta_surrogate_invert_bayesian.yaml` (`mode: invert_bayesian`)** returns
a **posterior over beta** instead, sampled with NUTS (gradient-based MCMC via
numpyro, using a JAX re-expression of the fitted GP's prediction; the fit itself
stays scikit-learn). The likelihood is Gaussian in the surrogate's coefficient
space (GP predictive variance + an assumed `dose_sigma`) under a uniform prior on
the training box. The posterior width says what the data determines:

| direction | posterior width vs prior | meaning |
|---|---|---|
| the `k` constrained combinations | ~0.01–0.08× | the dose pinned these down |
| the `D − k` flat combinations | ~1.1–1.25× | the dose says nothing; the value comes from `bounds` |

A prior-wide flat direction is the **correct** result, not a sampling failure.
`posterior_summary.txt` reports the ratio per direction alongside per-beta
credible intervals and convergence diagnostics.

> **Always check `r_hat`** in `posterior_summary.txt`; above ~1.05 the chains did
> not mix and the intervals are untrustworthy. A stuck chain explores one slice
> of the degenerate manifold and reports the flat directions as *narrow*, as if
> the dose constrained beta. One chain measured `r_hat = 1.61` and flat widths
> ~0.04–0.10× prior (wrong); the default four gave `r_hat ≈ 1.01` and ~1.1×
> (right). Do not lower `num_chains` from its default of 4 casually.

## Assets

The large *shared* inputs live in [`../assets/`](../assets) and are referenced by
relative path from the **collection** YAML (the only stage that runs the chain):

- `sample_track3p_particles.txt` — the external Track3P dump
- `7cell_solid_whole.stl`, `7cell_cavity_whole.stl` — geometry

The Geant4 input file `input_7cell.geant4` is *not* shared; each Geant4 example
carries its own. It names its STL geometry by bare filename, so the collection
YAML lists the `../assets/` STLs under `geant4_geometry_files` and the module
stages them into each per-sample workdir. Run from this directory so the
`../assets/` paths resolve.

## Running

Collect the training data, fit the surrogate, then invert a dose profile:

```bash
run-lume-ace3p geant4_beta_surrogate.yaml         # Geant4-heavy DOE collection
run-lume-ace3p geant4_beta_surrogate_train.yaml   # CPU-only fit (no Geant4)
run-lume-ace3p geant4_beta_surrogate_invert.yaml  # CPU-only inversion (no Geant4)
run-lume-ace3p geant4_beta_surrogate_invert_bayesian.yaml   # posterior over beta
```

Inspect the fit interactively with
`python ../../plotting/surrogate_fit_plot.py geant4_beta_surrogate_store`.

On S3DF (SLAC), submit the collection batch script:

```bash
sbatch run_lume-ace3p_geant4_beta_surrogate_s3df.batch
```

The batch script runs **only the collection YAML**; run the train step afterward
with `run-lume-ace3p geant4_beta_surrogate_train.yaml`. Geant4 is only installed
on S3DF, so there is no Perlmutter batch script. Each Geant4 step launches as a
nested `srun -n 1 -c <geant4_threads> <geant4-app> <input>` with
`geant4_threads: 120`, so the allocation reserves a full milano node;
`--cpus-per-task=120` MUST be `>= geant4_threads`. The 16 samples run
sequentially in one allocation, and the store is resumable: a timed-out job can
be resubmitted to finish the remaining samples.

With the Geant4 binary absent the collection run is a **dry run**: the
particle-weighting step still executes for real and writes `particles.data` per
sample, but the dose grids are `NaN` until a real Geant4 run produces the
scoring files.
