# Testing

The test suite is split into a **fast default gate** and a small set of **slow,
botorch-backed tests** that are excluded by default.

## Running the tests

From the repo root (the package must be installed, ideally `pip install -e .`):

```bash
# Fast default run — the routine correctness gate (seconds).
pytest

# Full run including the slow botorch GP-fitting tests (minutes) — do this
# before a master merge.
pytest -m slow      # only the slow tests
pytest -m ""        # everything (clears the default -m 'not slow' filter)
```

`pyproject.toml` sets `addopts = -m 'not slow'`, so a bare `pytest` skips the
slow tests. Passing `-m slow` or `-m ""` on the command line overrides that.

## What the fast suite covers

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
  `tests/baseline/` fixtures (the numeric-equivalence gate). The two
  botorch-backed examples are marked slow.
- `tests/test_run_xopt_compat.py` — the generic Xopt modes. The NelderMead
  trajectory match, the Geant4-objective dry-run, and the MC-noise config guard
  stay fast; the GP-fitting generators are marked slow.

## The slow tests

The slow tests do real botorch GP optimization (ExpectedImprovement, MOBO/EHVI,
MultiFidelity, UpperConfidenceBound constructing + stepping a model, and the
BayesianExploration GP sweep). They take minutes each and the MOBO/EHVI one is
nondeterministic. They rarely regress from changes in this repo (the xopt
internals are not what we edit), so they are excluded from the routine run and
should be run explicitly before a master merge.

The fast **baseline self-check** is the default correctness gate: the Phase-0.5
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
