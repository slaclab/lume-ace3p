# Golden baseline fixtures (Phase 0.5)

Frozen capture of the **current, pre-refactor** behavior of every shipped
example, so later phases of the workflow-modularization refactor can diff
against a stable reference. See `docs/workflow_module_refactor_plan.md`,
Phase 0.5.

The refactor is a **clean break** on output *file formats* (declarative
module-list YAML + `DataFrame.to_csv` outputs), so equivalence in later phases
is checked on **numeric content**, not bytes. This tree captures that numeric
content now, while the legacy code path is still intact.

## How it was produced

- **Dry-run paths** — ACE3P/Geant4 aren't installed locally, so `dry_run` is
  forced and the reachable baseline is the dry-run marker/table output. The
  pure-Python steps that genuinely run (Cubit input mutation recorded in the
  marker; the `Particles` field-emission weighting) produce real numbers and
  are captured for real.
- **Xopt paths** — driven through the generic modes with a deterministic
  **synthetic workflow** (`SyntheticWorkflow` in `../baseline_utils.py`, same
  pattern as `../test_run_xopt_compat.py`) under a fixed seed, so the optimizer
  trajectory / GP predictions are cluster-independent and reproducible.

Regenerate (only against the current code, intentionally):

```
python tests/freeze_baseline.py
```

Verify current code still matches (the self-check the plan requires):

```
python -m pytest tests/test_baseline_selfcheck.py -v
```

## Fixture formats

- **tables** (`*_output.txt`, `sim_output.txt`, `sweep_output.txt`) — verbatim
  whitespace-delimited files; compared as DataFrames with tolerance
  (`atol=rtol=1e-6`), timing columns (`xopt_runtime`, `xopt_error`) dropped.
- **markers** (`dry_run_marker.txt`) — free-form dry-run text; compared by the
  numeric tokens they contain (absolute temp paths legitimately vary run-to-run).
- **digests** (`*.digest.json`) — large numeric arrays (weighted particle dumps,
  Geant4 source files) frozen as row/col counts + per-column
  sum/min/max/mean, enough to catch a numeric regression without committing
  multi-MB arrays.

Each example dir also carries a `manifest.json` (kind, source YAML, fixture
list, and the `checkable` note). `not_frozen.json` records the examples that are
intentionally *not* frozen as numeric baselines, with the reason.

## Per-example checkability

| Example | Path / producer | Numerically checkable | Reachability only |
|---|---|---|---|
| `s3p_sweep` | s3p sweep, dry-run | swept input grid (cornercut × rcorner2), Frequency column | S-parameter values (no solver) |
| `s3p_sweep_no_s3p_file` | s3p sweep, dry-run | swept input grid + Frequency; ACE3P `Section` leaves in marker | solver step |
| `omega3p_sweep` | omega3p sweep, dry-run | swept grid (cav_radius × ellipticity) | R/Q, Mode_freq, E_max, loc_* (NaN without acdtool) |
| `omega3p_ace3p_param_sweep` | omega3p sweep, dry-run | swept cubit grid **and** the ACE3P `Sigma` list, which becomes a 3rd sweep axis (4×4×2 = 32 runs); ACE3P leaves in marker | solver outputs |
| `track3p_particle_weight` | **real** `Particles` compute | field-emission `ParticleWeight` + all track columns (digest) | — |
| `geant4_track3p_beta` | Geant4 sweep, dry-run + **real** `Particles` pre-step | the generated Geant4 source `particles.data` per beta (digest); swept beta grid | Geant4 solver (marker records input/particle/geometry/output files) |
| `s3p_optimization` | scalar_optimize, synthetic solver (seeded) | full NelderMead trajectory (cornercut, rcorner1, objective) | — |
| `s3p_bayesian_sweep` | gp_parameter_sweep, synthetic solver (seeded) | 10×10 GP posterior-mean sweep + exploration trajectory | — |
| `MOBO_ExpectedHypervolume_Example` | scalar_optimize (MOBO/EHVI), synthetic solver (seeded) | MOBO trajectory (R1, L1, r10, three S(0,0) objectives) | — |

### Intentionally not frozen (see `not_frozen.json`)

- **`s3p_mf_optimization`** (MultiFidelity) — the cost-budget path divides by
  wall-clock `xopt_runtime` and loops on `alotted_time`, so trajectory length
  and values are timing-dependent: reachability-only, not numerically
  checkable. Generator construction/stepping is smoke-tested in
  `../test_run_xopt_compat.py::test_generic_multifidelity`.
- **`UCB_Example`** — the shipped YAML declares three objectives, but xopt
  3.0.0's `UpperConfidenceBoundGenerator` rejects multi-objective VOCS
  (`VOCSError`). Not runnable as-is under the pinned xopt; recorded as a
  known-error rather than a numeric baseline.
