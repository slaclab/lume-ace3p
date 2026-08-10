# Testing

The whole test suite runs in one go — there is deliberately **no
excluded-by-default tier**.

## Running the tests

From the repo root (the package must be installed, ideally `pip install -e .`):

```bash
pytest        # the whole suite (~6 min), which is the correctness gate
```

## What the suite covers

- `tests/test_modules.py` — every module's dry-run + `requires`/`provides`
  edges, and `extract` against synthetic solver-output fixtures.
- `tests/test_workflow_graph.py` — declarative `Workflow` build, DAG ordering,
  validation errors, and the three chains' dry-run `evaluate` (the Geant4
  `particles.data` digest is a real-compute equivalence check).
- `tests/test_modes.py` — the `single` / `parameter_sweep` modes, matched
  numerically against the Phase-0.5 baselines.
- `tests/test_results.py` — the single shared result writer + field-artifact
  round-trip.
- `tests/test_baseline_selfcheck.py` — re-runs each frozen example through the
  declarative module/mode path and checks it still reproduces its
  `tests/baseline/` fixtures (the numeric-equivalence gate).
- `tests/test_run_xopt_compat.py` — the generic Xopt modes: the NelderMead
  trajectory match against the frozen baseline, a Geant4 chain as the objective,
  and the MC-noise config guards.
- `tests/test_surrogate.py`, `tests/test_surrogate_data.py`,
  `tests/test_inversion.py`, `tests/test_bayesian.py` — the dose-surrogate
  project: training store, PCA-GP forward fit, point inversion + identifiability,
  and the NUTS posterior. `test_bayesian.py` is the slowest file (~3 min: real
  4-chain MCMC runs) but still runs by default, since it is the only coverage of
  the Bayesian inversion path.

## Removed: the botorch "slow" tier

There used to be a `slow` marker with `addopts = -m 'not slow'`, holding tests
that drove real botorch GP fits (ExpectedImprovement, MOBO/EHVI, MultiFidelity,
UpperConfidenceBound, and the BayesianExploration GP sweep). **These were deleted
in 2026-08.** The reasoning:

- They cost minutes-to-hours. One run of `test_generic_multifidelity` did not
  finish in **2 hours** at ~1000% CPU — its `cost_budget` loop terminates on
  *measured wall-clock runtimes*, not an iteration count, so its duration scales
  with machine speed. (Verified against unmodified `HEAD`, so this was not a
  regression.)
- Because of that cost they were never actually run, so they gated nothing.
- What they asserted was mostly `len(X.data) == 3` after a generator stepped —
  i.e. xopt/botorch internals, which this repo does not edit.

The part that *is* ours — generator selection and the MC-noise prior guard — is
now covered by `test_mc_noise_guard_skips_low_noise_prior`, which asserts on
`modes._build_generator` directly in under a second. The GP-sweep and MOBO
numeric baselines were de-registered; see `tests/baseline_utils.NOT_FROZEN` for
exactly what is no longer checked.

The **baseline self-check** is the correctness gate: the Phase-0.5
fixtures were frozen from the pre-refactor code, and the declarative path must
still reproduce them on the numerically-checkable quantities (sweep tables,
optimization trajectories, particle-weighting digests).

## Regenerating the baseline fixtures

The frozen fixtures under `tests/baseline/` are the reference; regenerate them
only intentionally (e.g. after a deliberate numeric change), from the current
code:

```bash
python tests/freeze_baseline.py
```
