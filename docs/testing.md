# Testing

The whole test suite runs in one go; there is **no excluded-by-default tier**.

## Running the tests

From the repo root, with the package installed (ideally `pip install -e .`):

```bash
pytest        # the whole suite (~6 min), which is the correctness gate
```

## What the suite covers

- `tests/test_modules.py`: every module's dry-run and `requires`/`provides`
  edges, and `extract` against synthetic solver-output fixtures.
- `tests/test_workflow_graph.py`: declarative `Workflow` build, DAG ordering,
  validation errors, and the three chains' dry-run `evaluate` (the Geant4
  `particles.data` digest is a real-compute equivalence check).
- `tests/test_modes.py`: the `single` / `parameter_sweep` modes, matched
  numerically against the frozen baselines.
- `tests/test_results.py`: the single shared result writer and field-artifact
  round-trip.
- `tests/test_baseline_selfcheck.py`: re-runs each frozen example and checks
  it still reproduces its `tests/baseline/` fixtures (the numeric-equivalence
  gate).
- `tests/test_run_xopt_compat.py`: the generic Xopt modes: the NelderMead
  trajectory match against the frozen baseline, a Geant4 chain as the objective,
  and the MC-noise config guards.
- `tests/test_surrogate.py`, `tests/test_surrogate_data.py`,
  `tests/test_inversion.py`, `tests/test_bayesian.py`: the dose-surrogate
  project: training store, PCA-GP forward fit, point inversion and
  identifiability, and the NUTS posterior. `test_bayesian.py` is the slowest
  file (~3 min, real 4-chain MCMC runs) but still runs by default; it is the only
  coverage of the Bayesian inversion path.

## Removed: the botorch "slow" tier

There used to be a `slow` marker with `addopts = -m 'not slow'`, holding tests
that drove real botorch GP fits (ExpectedImprovement, MOBO/EHVI, MultiFidelity,
UpperConfidenceBound, and the BayesianExploration GP sweep). **These were deleted
in 2026-08**, because:

- They cost minutes-to-hours. One run of `test_generic_multifidelity` did not
  finish in **2 hours** at ~1000% CPU. Its `cost_budget` loop terminates on
  *measured wall-clock runtimes*, not an iteration count, so its duration scales
  with machine speed. (Verified against unmodified `HEAD`, so not a regression.)
- Because of that cost they were never actually run, so they gated nothing.
- What they asserted was mostly `len(X.data) == 3` after a generator stepped,
  i.e. xopt/botorch internals, which this repo does not edit.

The part that *is* ours, generator selection and the MC-noise prior guard, is
covered by `test_mc_noise_guard_skips_low_noise_prior`, which asserts on
`modes._build_generator` directly in under a second. The GP-sweep and MOBO
numeric baselines were de-registered; `tests/baseline_utils.NOT_FROZEN` lists
exactly what is no longer checked.

The **baseline self-check** is the correctness gate: the code must still
reproduce the frozen fixtures on the numerically-checkable quantities (sweep
tables, optimization trajectories, particle-weighting digests).

## Regenerating the baseline fixtures

Regenerate the fixtures under `tests/baseline/` only intentionally (e.g. after
a deliberate numeric change), from the current code:

```bash
python tests/freeze_baseline.py
```

Every fixture set carries a `frozen` note in its `manifest.json` saying **when it
was captured and why it was (re)generated**. Update the corresponding provenance
string in `tests/baseline_utils.py` as part of any deliberate regeneration, and
say what moved. A regenerated fixture with no recorded reason is
indistinguishable from an accident.
