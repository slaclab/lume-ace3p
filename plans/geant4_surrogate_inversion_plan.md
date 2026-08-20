# Geant4 Dose Surrogate & Inversion — Implementation Plan

**Status:** UNSHELVED (2026-07-10). The workflow modularization refactor is
complete (see `plans/workflow_module_refactor_plan.md`), so this project resumes
on the new module/workflow/mode architecture.
**Phase 0 (xopt 3.0.0 compat) done. Phase 1 (decouple Xopt from S3PWorkflow) is
DELIVERED** by the refactor's Phase 4: `scalar_optimize` / `gp_parameter_sweep`
in `src/lume_ace3p/modes.py` are now generic over any `Workflow` (objective
pulled from `evaluate()` + declarative `output_parameters`), so the S3P-hardwired
`run_xopt.py` and its hard-no-change contract below are gone (the refactor was a
deliberate clean break — the legacy `S3PWorkflow`/`run_xopt` no longer exist).
The MC-noise correctness constraints (constraint #2) are already wired as mode
config (`mc_noisy_objective`, required explicit `bin_edges`) in
`modes._mc_noise_guards` / `_build_generator`.
**Next: Phases 2–4 (collect_training_data, train_surrogate, invert_*) become
NEW MODES** on the new architecture — each a workflow-agnostic mode in
`modes.py` (or a sibling module) driving the existing Geant4 `Workflow`, exactly
as `parameter_sweep`/`scalar_optimize` do today.
**Phase 2 (`collect_training_data`) DELIVERED (2026-07-20)** — DOE sampler +
resumable training store + dry-run pipeline, all local. See the Phase 2 section
below for the verification checklist (all bars met).
**Constraint-#3 mesh-pinning hardening DELIVERED (2026-07-21)** — dose scoring
mesh is now fingerprinted, validated at collection time (up-front + per-sample),
recorded in the manifest, and re-checked bin-for-bin at load time. See the Phase 2
"Follow-up hardening" section.
**Phase 3 (`train_surrogate`) DELIVERED (2026-07-21)** — PCA-GP forward dose
surrogate (SVD → top-k POD modes → one sklearn GP per coefficient with a genuine
fitted noise term) built + validated locally against a synthetic analytic β→dose
fixture per the synthetic-first strategy. See the Phase 3 section for the
verification checklist (all bars met).
**Phase 4a (`invert_optimize`) DELIVERED (2026-08-06)** — dose → β point estimate
by bounded multi-start L-BFGS-B on the coefficient-space misfit, with the dose
parser unified, target voxel-alignment enforced (constraint #3, inversion side),
and non-uniqueness reported as all distinct minima. `invert_bayesian` is Phase 4b.
See the Phase 4a section.
**Identifiability reporting + YAML cleanup DELIVERED (2026-08-10)** — the inverse
is rank-deficient **by construction** (`rank ≤ k` retained POD modes, so `k < D`
leaves `D−k` β directions invisible to the dose); the multiple minima are samples
of one *continuous degenerate surface*, not rival hypotheses, and are **not**
rankable by evidence. `invert_optimize` now reports which β directions are
constrained vs. flat. Separately, the store-consuming modes no longer require a
`workflow:` block, shrinking their YAMLs to ~7 lines. See the Phase 4a
"Follow-up" section.
**Phase 4b (`invert_bayesian`) DELIVERED (2026-08-10)** — posterior over β via
NUTS (numpyro, over a JAX re-expression of the fitted GP's prediction). It
quantifies the degeneracy: tight along the constrained β combinations
(~0.01–0.08× prior width), prior-wide along the flat ones (~1.1–1.25×). Two
empirical findings became hard defaults — `num_chains=4` and `dense_mass=True`
(one chain / diagonal mass silently *under-report* the flat directions, which
reads as a false constraint). **Phases 2–4 are now complete; only Phase 5
(multi-fidelity) and the real-Geant4 validation remain.**
**Owner:** dbizzoze
**Created:** 2026-07-08

## Re-scope (2026-07-20): synthetic-first, trustworthy-forward-surrogate-first

The original phase order is linear and treats each phase as cluster-gated behind
a real Geant4 training campaign. Two facts change the strategy:

- **Geant4 runs are cluster-only** (no ACE3P/Geant4 env locally). The expensive,
  blocking part of Phase 2 is the *campaign* (producing real `(β, dose)` pairs),
  not the *mode code* (sampler + `Workflow.evaluate` loop + `save_field`/
  `load_field` reuse + dry-run), which is buildable and testable locally now.
- **Phases 3–4 do not need real Geant4 data to validate the machinery.** A known
  analytic β→dose map with injected MC-style noise exercises the full PCA-GP fit,
  held-out accuracy, variance calibration, save/reload, and even the inversion
  recovery test — all locally. Real dose files validate the *science*, not the
  *plumbing*.

**Adopted strategy (user-confirmed 2026-07-20):**

1. **Build on synthetic first.** Implement the Phase-2 `collect_training_data`
   mode (+ resumable store + dry-run pipeline test) AND develop Phases 3–4
   against a synthetic analytic β→dose fixture with injected noise. Decouples all
   ML/inversion machinery from the cluster.
2. **Trustworthy forward surrogate is the primary near-term deliverable.** The
   Phase-3 PCA-GP must hit its verification bar first — held-out reconstruction
   accuracy *and* calibrated (non-zero) predictive variance per constraint #2 —
   before inversion (Phase 4) is treated as done. Get the forward map right;
   inversion rides on it.
3. The real Geant4 training campaign (cluster) and the science-level recovery
   tests slot in after the synthetic machinery is validated — they swap the
   synthetic fixture for real `(β, dose)` pairs without reworking the GP/inversion
   core (that separation is exactly what constraint #1/#2 and the PCA-GP interface
   are designed to preserve).

This does not delete any phase or change the verification bars below — it reorders
*when* they run and lets Phase 3 lead on synthetic data instead of waiting on a
cluster campaign. The MCMC library for `invert_bayesian` (emcee vs numpyro vs
pymc) is still an open one-time decision to make when Phase 4 starts.

## Phase 0 — xopt 3.0.0 compatibility (unplanned prerequisite, done 2026-07-08)

The repo was pinned `xopt>=2.2.2` but 3.0.0 was installed (needed for Bayesian
exploration). 3.0.0 broke the shipped S3P examples, so the Phase-1
before/after example diff could not be run. Fixed in `run_xopt.py` before
starting Phase 1:

- `run_lf_sweep`: build VOCS from `objectives` (the `explore` dict the example
  already declares) instead of the empty `observables`; 3.0.0's
  `BayesianExplorationGenerator` requires `ExploreObjective` objectives.
- NelderMead: seed `initial_point` from the midpoint of each variable's bounds
  when `num_random` is absent/0 (3.0.0 no longer infers a start point).
- `VOCS.random_inputs(n)` → module-level `xopt.vocs.random_inputs(vocs, n)`
  (used by the multi-fidelity cost-budget path).
- Pin bumped to `xopt>=3.0.0` in `pyproject.toml` / `requirements.txt`.

EI, MOBO, and MultiFidelity generators construct/step fine under 3.0.0 — their
apparent failures were dry-run artifacts (dry-run S3P returns NaN under
output-name keys, never the real S-parameter keys the optimizer parses), not
version breakage. Env-independent proof: `tests/test_run_xopt_compat.py`
monkeypatches `S3PWorkflow` with a synthetic solver and drives all five
paths (NelderMead / EI / MOBO / MultiFidelity / gp_parameter_sweep) to
file-writing; 5/5 pass. Real before/after S3P example diffs still require the
cluster (ACE3P absent locally).

## Goal

Build a surrogate model for the `geant4_track3p_beta` workflow that maps an
8-dimensional field-enhancement vector `β = (β₀ … β₇)` (one per axial bin) to a
Geant4 dose-deposit profile, then **invert** it: given a measured/target dose
file, estimate the β profile that produced it — i.e. *"given dosage data, predict
the field-enhancement profile."*

A full 8-D tensor sweep is combinatorially infeasible, so the surrogate is
trained from a modest number of Geant4 evaluations at scattered design points,
then queried cheaply. Inversion is done against the cheap surrogate, not the
real solver.

## Confirmed design decisions

- **Surrogate output = full dose profile** via PCA/POD + one GP per retained
  component (a "PCA-GP" / reduced-basis surrogate). Not scalar reductions —
  inverting from a scalar is under-determined.
- **Future alternate input modes** for inversion: reduced "profile summaries"
  (e.g. per-z-slice dose totals) as *separate* modes. Design the surrogate and
  inversion interfaces so this can be added without reworking the GP core.
- **Two inversion methods, both implemented:**
  - `invert_optimize` — minimize `‖project(target) − c_GP(β)‖²` using existing
    Xopt generators. Point estimate, reuses all current infrastructure.
  - `invert_bayesian` — posterior over β via MCMC on the cheap surrogate. For
    predictive analysis with low data; exposes non-uniqueness and uncertainty.
  - **No** direct `dose → β` regressor — too ill-posed.
- **Multi-fidelity** (on Geant4 particle count) is deferred to the last phase and
  only pursued after single-fidelity accuracy is validated.

## Cross-cutting correctness constraints (apply to every phase)

1. **Fix `bin_edges` explicitly** on the `particles` module entry (in the
   `workflow:` list) for all training and inversion runs. The default edges in
   `Particles.assign_bins` (`src/lume_ace3p/particles.py:49`) are data-driven
   (`z_vals.min() .. max()`) and drift per run, making the β→dose map
   non-stationary and poisoning the GP. Every generated training YAML / run
   config must carry an explicit, shared `bin_edges` (length `num_bins + 1`).
   **Note — the existing guard checks the WRONG place for this purpose.**
   `modes._mc_noise_guards` (`modes.py:278`) only asserts that `'bin_edges'` is
   a key in the **mode / xopt config dict**, and it fires only for the Xopt modes
   (`scalar_optimize` / `gp_parameter_sweep`) when `mc_noisy_objective` is set.
   But the `bin_edges` that actually governs the β→dose binning is read off the
   **`particles` module entry** (`particles.py:31`, consumed at `:51`), and
   *nothing plumbs the mode-dict value into the particles module* — they are
   disconnected keys. So calling `_mc_noise_guards` alone would NOT guarantee the
   binning is pinned. The new `collect_training_data` mode (Phase 2) must instead
   **validate `bin_edges` on the resolved `particles` module config** (inspect the
   built `Workflow`'s particles module and hard-fail if `bin_edges` is absent or
   not length `num_bins + 1`). Do not rely on `_mc_noise_guards` for this.
2. **Geant4 dose is Monte-Carlo noisy.** The surrogate GPs must include a genuine
   noise term. Do **not** reuse S3P's low-noise / interpolating prior
   (`use_low_noise_prior`). `modes._build_generator` already leaves it at its
   default (`False`) when `mc_noisy_objective` is set — mirror that in the
   surrogate GPs; default to a fitted noise/likelihood.
3. **Fix the dose scoring mesh explicitly** — the *output-side* analogue of
   constraint #1 (added 2026-07-21). PCA/POD stacks every run's dose grid into one
   matrix `Y (N × M)` and runs SVD in that shared `ℝ^M`. That is only meaningful
   if **row `i`, column `j` is the same physical voxel for all `i`** — i.e. the
   scoring mesh (bin counts `nx·ny·nz`, physical extent/origin, and value units)
   is identical across the whole campaign. A drifting mesh silently misaligns the
   basis exactly the way drifting `bin_edges` misaligns the input map. The mesh is
   defined in the Geant4 input file (`mesh_nx/ny/nz`, `mesh_cx/cy/cz`,
   `mesh_x/y/z`) and is static per input file, so pinning it is a *contract +
   validation* job, not a computation:
   - **Record a real mesh fingerprint in the manifest**, not just the flat voxel
     count. `mesh_shape: [M]` (a single integer count, `modes._mesh_shape`,
     `modes.py:374`) is too weak — two physically different meshes with equal
     total voxel count pass it. Capture the full geometry via
     `plotting/geant4_deposit_common.read_mesh_geometry` (returns `bins`,
     `center`, `half`, `spacing`, `origin` + units) — it only parses the Geant4
     input file, so it works even under dry-run (no dose grid needed).
   - **Validate every sample against that fingerprint at collection time** and
     hard-fail on drift, mirroring `modes._require_fixed_bin_edges` for the input
     side. Do not rely on the load-time layout check alone.
   - **The existing load-time guard is necessary but not sufficient.**
     `surrogate_data._check_indices` / `_stack_rows` (`surrogate_data.py:225`,
     `:242`) only compare voxel-index/value **shapes** and only when the store is
     read back. Strengthen `load_training_store` to also assert the stored voxel
     `indices` array matches the manifest fingerprint bin-for-bin (identical
     indices, not merely equal length), so a same-count / different-geometry mesh
     is caught rather than silently misaligned.
   - **Heterogeneously-binned runs cannot be stacked directly** — there is no
     common `ℝ^M`. The only supported way to reuse them is an explicit, opt-in,
     **lossy** resample onto a fixed reference mesh before stacking (injects
     interpolation error on top of MC noise; can bias the POD basis). This is a
     salvage path for data that cannot be regenerated, never the default; if used,
     `log()`/record that resampling occurred and to which reference mesh.

## Repository orientation (updated 2026-07-12, re-verified 2026-07-20)

*(2026-07-20 review: the only substantive change since the 07-12 reconciliation
is commit `3d0916d` — nested `input_parameters` + cross-code VOCS routing, v0.2.1
— which confirms rather than contradicts the notation below. All file:line
citations here re-checked against `dev`.)*

The module/workflow/mode refactor has landed on `dev`. Legacy `workflow.py`
(Omega3P/S3P/Geant4Workflow subclasses), `run_xopt.py`, and the `tools.py`
writers are **deleted** — do not reference them. The three live layers are
`src/lume_ace3p/modules.py`, `workflow_graph.py`, and `modes.py`, with results
consolidated through `results.py`.

- Package lives under `src/lume_ace3p/` (a stale `build/lib/lume_ace3p/` copy may
  exist — ignore it).
- Entry point: `src/lume_ace3p/run_lume_ace3p.py` — declarative-only:
  `Workflow.from_config(yaml)` builds/validates the module DAG, then
  `run_mode(mode_cfg, workflow, ...)` dispatches on `mode.type`.
- Forward map `β∈ℝ⁸ → dose` is **already callable today** via a declarative
  `track3p_source → particles → geant4` workflow. The β resolution now lives in
  `ParticlesModule._resolve_beta` (`src/lume_ace3p/modules.py:488`, verified
  current 2026-07-20): set
  `beta_inputs: [beta0 … beta7]` on the `particles` **module** entry (one per
  bin) with `beta0..beta7` declared under `input_parameters.particles` (the
  field-enhancement bucket; a legacy `input_parameters.cubit` declaration is
  still honored), or `beta_input: beta`
  to broadcast one scalar to all bins. The single-run path handles an arbitrary
  8-vector; no changes to the Geant4/particle plumbing are needed to evaluate
  training points. See `examples/geant4_track3p_beta/geant4_track3p_beta.yaml`
  (ships the scalar-broadcast form; documents the 8-D upgrade inline).
- Xopt driver: now the generic modes `modes.scalar_optimize` /
  `modes.gp_parameter_sweep` — workflow-agnostic (objective pulled from
  `Workflow.evaluate()` + declarative `output_parameters`), not S3P-specific.
- **Full dose grids are already captured**, not just extracted scalars:
  `Geant4Module.field(ctx)` (`modules.py:744`) returns
  `{'dose': {indices, values}, 'edep': {...}}` as arrays; `Workflow` exposes
  `field()` / `field_index()`; and `results.save_field` / `results.load_field`
  (`results.py:74` / `:107`) round-trip those grids to `.npz`, referenced from an
  opt-in `field_artifact` column in the result table. Phases 2–3 should build on
  this rather than a bespoke store (see Phase 2).
- Dose scoring-file parsers: **two exist with different output shapes** — pick
  one canonically for the surrogate (see Phase 4):
  - `Geant4Module._read_scoring_output` (`modules.py`) — whitespace-or-comma,
    returns flat `{'indices': [(ix,iy,iz)…], 'values': ndarray}`. This is what
    `field()` / `save_field` persist, so it is the natural training/inversion
    parser.
  - `plotting/geant4_deposit_common.parse_deposit_file` — comma-separated
    `iX,iY,iZ,total(value),total(val^2),entry`; also returns mesh/scorer/units
    and reshaped grids. Good for the target-file header metadata, but its shape
    differs from the stored field arrays.

---

# Phase 1 — Decouple the Xopt driver from S3PWorkflow — DELIVERED

**Delivered by the modularization refactor's Phase 4 (2026-07-10).** The Xopt
driver is now workflow-agnostic: `modes.scalar_optimize` / `modes.gp_parameter_sweep`
pull the objective scalar(s) from `Workflow.evaluate(input_dict)` + the
declarative `output_parameters` spec, so a Geant4 (or any) workflow plugs in with
no S3P-specific code. Verified by
`tests/test_run_xopt_compat.py::test_generic_geant4_objective_dry_run` (a Geant4
chain as the objective) and the S3P numeric-equivalence tests.

> **The hard-no-change contract below is VOID.** It predates the decision to
> make the refactor a clean break (new declarative YAML + `DataFrame.to_csv`
> outputs, example YAMLs rewritten). It is kept here only as a record of the
> original Phase-1 intent; do not treat it as a live requirement.

### Hard no-change contract (superseded — historical, do NOT enforce)

For the S3P paths (`mode: scalar_optimize` and `mode: gp_parameter_sweep`,
`module: s3p`), after this refactor:

- The runnable optimization examples `examples/s3p_optimization/`,
  `examples/s3p_mf_optimization/`, and `examples/s3p_bayesian_sweep/` exercise
  the Xopt path. (The old `UCB_Example` / `MOBO_ExpectedHypervolume_Example`
  configs are non-runnable legacy references under `examples/incomplete/`.)
  Note: example YAMLs were later migrated to the nested `input_parameters`
  notation — see `docs/yaml_reference.md`.
- **Public function signatures unchanged:** `run_xopt(workflow_dict, vocs_dict,
  xopt_dict)` and `run_lf_sweep(workflow_dict, sweep_dict, vocs_dict, xopt_dict)`
  keep their names and arguments (they are called from
  `run_lume_ace3p.py:43-60`).
- **Output files identical** (names, columns, formatting, append-vs-overwrite):
  `sim_output.txt`, `sim_output_all_values.txt`, `sweep_output.txt`,
  `Binary_gp_model.pt`, `gp_parameters.txt`. Produced via `WriteXoptData` /
  `WriteS3PDataTable` in `tools.py` — do not touch those writers.
- All generators still supported: `NelderMeadGenerator`,
  `ExpectedImprovementGenerator`, `MultiFidelityGenerator`,
  `UpperConfidenceBoundGenerator`, `ExpectedHypervolumeImprovementGenerator`,
  `BayesianExplorationGenerator`. Fidelity-variable rename (`s` →
  `fidelity_variable`) and cost-function logic preserved.

### Approach

Extract the S3P-specific pieces into an injectable object, leaving the Xopt
orchestration (generator selection, random/step loops, termination criteria,
model saving) generic:

1. Define a small interface — a `sim_function` factory that takes
   `(workflow, output-extraction spec)` and returns the `input_dict → output_dict`
   closure Xopt expects, plus a hook for the per-iteration data logging.
2. Move the S3P body (build `S3PWorkflow`, run, parse `Frequency` / `S(m,n)`,
   call `WriteS3PDataTable`) into an S3P-specific implementation of that
   interface. Behaviorally identical — ideally a pure code move.
3. `run_xopt` / `run_lf_sweep` keep their signatures but delegate solver-specific
   work to the injected implementation. The Geant4 implementation is **not**
   added in this phase — only the seam that will accept it.

### Verification (Phase 1 done when)

- Run `examples/s3p_optimization/s3p_optimization.yaml` and at least one of
  `s3p_mf_optimization` / `s3p_bayesian_sweep` **before and after** the refactor
  (dry-run acceptable where ACE3P env is absent) and diff the produced
  `sim_output*.txt` / `sweep_output.txt` — they must match.
- No YAML or example file edited.
- `run_xopt` / `run_lf_sweep` signatures unchanged; `run_lume_ace3p.py` untouched
  except (optionally) internal wiring that does not alter dispatch behavior.

### Out of scope for Phase 1

Any Geant4 objective, any new mode, any surrogate/PCA code. This phase is a
pure, behavior-preserving refactor.

---

# Phase 2 — `collect_training_data` mode (DOE sampler over β)

**Objective:** Generate and persist `(β, dose_grid)` training pairs by evaluating
the declarative `track3p_source → particles → geant4` workflow at scattered
points in the 8-D β space.

### Approach

1. New `mode: collect_training_data`, `module: geant4` in `run_lume_ace3p.py`.
2. Design-of-experiments sampler over `beta0..beta7`: Latin Hypercube or Sobol
   (prefer `scipy.stats.qmc`), N points across per-dimension bounds declared in
   the YAML. **Not** a tensor grid.
3. For each sample: materialize the 8-vector and drive the declarative
   `track3p_source → particles → geant4` workflow via `Workflow.evaluate`
   (set `beta_inputs: [beta0..beta7]` on the `particles` module). Capture the
   full grid with `Workflow.field()` (backed by `Geant4Module.field`), not just
   extracted scalars.
4. **Reuse the existing field-persistence machinery** rather than a bespoke
   store: write the β rows to the result table via `results.write_table` with a
   `field_artifact` column, and persist each dose grid with `results.save_field`
   (`.npz`); reload with `results.load_field`. Add a small manifest alongside
   (shared `bin_edges`, mesh shape, units, fidelity) — but the `(β, dose_grid)`
   pairing itself falls out of the table + field artifacts, so the Phase-2 loader
   is mostly a thin wrapper over `load_field` + the table, not a new format.
   Must be **resumable** — skip β points whose workdir/dose file already exists
   (workdir naming encodes the swept scalars).
5. Enforce the cross-cutting constraints: **explicitly require `bin_edges`**
   in this mode (call `modes._mc_noise_guards` or replicate it — Phase 2 is not
   an Xopt mode and does not inherit that guard automatically); **pin + fingerprint
   the dose scoring mesh** (constraint #3 — see the hardening note below); make
   fidelity (particle count) an explicit recorded field for later.

### Verification (Phase 2 done when) — ALL MET (2026-07-20)

- [x] A small N (e.g. 8–16) DOE run produces a training store loadable in one
  call returning aligned `β` matrix and dose-grid tensor with consistent shapes.
  → `surrogate_data.load_training_store` returns aligned `(N,D)` β + `(N,M)`
  dose; `test_full_store_roundtrip_and_alignment` checks the β↔dose alignment.
- [x] Re-running the mode skips already-computed points (resumability).
  → each sample runs in `<store>/sample_NNNNN`; a persisted `field.npz` is
  skipped. `test_resume_skips_computed_points` proves only missing points
  re-evaluate; `test_resume_reproduces_same_design` proves the seeded DOE
  reproduces identical β on resume.
- [x] Dry-run mode works without the Geant4 app environment.
  → `test_dry_run_pipeline_produces_store` + the CLI runs the shipped example
  end-to-end under auto-enabled dry-run.

### Deliverables — DONE

- Example `examples/geant4_beta_surrogate/geant4_beta_surrogate.yaml` —
  `beta0..beta7` bounds (mode `variables:`), explicit fixed `bin_edges`, DOE
  size/sampler/seed, store path. Large shared geometry/particle files (STLs +
  Track3P dump) live in `examples/assets/` and are referenced by relative path
  (`../assets/...`), so the example carries no multi-MB duplicates; the Geant4
  input file itself lives with the example.
- Loader utility `src/lume_ace3p/surrogate_data.py` — DOE sampler
  (`sample_beta_doe`, scipy `qmc` Sobol/LHS) + `load_training_store`, a thin
  wrapper over `results.load_field` + the result table (+ a `manifest.json`
  recording the fixed `bin_edges`/`num_bins`, β order, mesh shape, DOE
  provenance). Not a new on-disk format.

### Implementation notes / deviations

- **DOE bounds live in the `mode:` block** (`variables: {beta0: [lo,hi], ...}`)
  rather than `input_parameters` — the β values under `input_parameters.particles`
  are placeholders the DOE overrides per sample. This keeps the sampled design
  in the mode config (where inversion Phase 4 will also declare VOCS bounds) and
  the `input_parameters` block declaring only that the 8 β knobs exist.
- **`bin_edges` guard** (`modes._require_fixed_bin_edges`) inspects the resolved
  **`particles` module** config (constraint #1), hard-failing on missing /
  wrong-length `bin_edges`, a scalar `beta_input` (must be per-bin
  `beta_inputs`), or `len(beta_inputs) != num_bins`. It does NOT reuse
  `_mc_noise_guards` (which only checks a disconnected mode-dict key), per the
  constraint-#1 note above.
- **`fidelity`** is a recorded mode-config field written to every table row +
  the manifest, ready for Phase 5 multi-fidelity filtering.
- Under dry-run there is no dose grid (the Geant4 binary is skipped), so the
  dry-run store carries β + fidelity rows but no `field_artifact` column; the
  field-persistence / resume / loader-alignment paths are exercised with real
  arrays via a synthetic-dose fake workflow in the tests.

### Follow-up hardening — pin & fingerprint the dose mesh (constraint #3, 2026-07-21) — DELIVERED

Phase 2 pinned the **input-side** binning (`_require_fixed_bin_edges`) but left the
**output-side** mesh under-protected. This hardening pass closed that gap
(2026-07-21); the delivered DOE/store/resume work was not reopened. What landed
is described below, followed by the original gap analysis for the record.

- **Gap.** The manifest records only a flat voxel count
  (`mesh_shape: [M]` via `modes._mesh_shape`, `modes.py:374`), and the sole
  cross-run check is `surrogate_data._check_indices` / `_stack_rows`
  (`surrogate_data.py:225` / `:242`) — shape-only, and only at load time. Two
  physically different meshes with the same total voxel count pass every current
  check, silently misaligning the PCA basis. Nothing validates the mesh at
  *collection* time.
- **Fix (collection side, `modes.collect_training_data`).**
  1. Add `_require_fixed_mesh(workflow)` mirroring `_require_fixed_bin_edges`:
     resolve the `geant4` module's input file and read the scoring-mesh geometry
     with `geant4_deposit_common.read_mesh_geometry` (`bins`, `center`, `half`,
     `spacing`, `origin`, units). Hard-fail if the mesh keys are absent. This
     parses the input file only, so it runs under dry-run too.
  2. Compute a canonical **mesh fingerprint** (e.g. a dict/hash of
     `bins + center + half + units`) once, write it into `manifest.json` as
     `mesh` (replacing / augmenting the weak `mesh_shape`), and — defensively —
     re-read it per sample and assert it is unchanged, so a mid-campaign edit to
     the Geant4 input file is caught immediately rather than at train time.
- **Fix (load side, `surrogate_data.load_training_store`).** After stacking,
  assert the shared voxel `indices` array matches the manifest fingerprint
  bin-for-bin (identical indices, not just equal length). Keep `_check_indices`
  as the cheap first line of defense.
- **Explicitly out of scope here (documented, not built):** resampling
  heterogeneously-binned runs onto a common reference mesh. Direct stacking of
  differently-shaped grids stays a hard error; the lossy resample salvage path
  (constraint #3) is deferred until a real need for un-regenerable off-mesh data
  appears, and must announce itself via `log()` when added.

### What landed (2026-07-21)

- **`surrogate_data.read_mesh_fingerprint(geant4_input_path)`** — self-contained
  `key = value` parse (no dependency on the non-importable `plotting/` scripts)
  returning `{'bins': [nx,ny,nz], 'center': [...], 'half': [...]}`, or `None` if
  the file is missing/unreadable or any of the nine `mesh_*` keys is absent /
  non-numeric / non-positive. Works under dry-run (input-file only).
  **`surrogate_data.mesh_fingerprints_match(a, b)`** — exact bin-count compare +
  `atol=1e-9` on center/half so a `60` vs `60.0` reformat is not read as drift.
- **`modes._require_fixed_mesh(workflow)`** — mirrors `_require_fixed_bin_edges`:
  resolves the (single) `geant4` module's input file, hard-fails with a
  `constraint #3` message if `geant4_input` is unset or the fingerprint is
  unreadable, and returns the fingerprint. Returns `None` when the workflow has
  no `geant4` module (synthetic test doubles that emit grids directly) — the
  load-side checks are the backstop there. `collect_training_data` calls it up
  front **and** re-reads per fresh sample, hard-failing if the mesh changed
  mid-campaign.
- **Manifest** now carries the full `mesh` fingerprint alongside the (retained,
  back-compat) flat `mesh_shape`.
- **`load_training_store` hardening:** `_check_indices` now compares voxel
  `(ix,iy,iz)` arrays **bin-for-bin** (`np.array_equal`), not just by shape, so a
  same-count / different-layout mesh is caught; `_check_indices_against_manifest`
  additionally asserts the stacked voxel count equals `prod(manifest['mesh']['bins'])`.
- **Tests** (`tests/test_surrogate_data.py`, all local, no Geant4 env; 20 pass):
  fingerprint read from the shipped example + missing/incomplete/non-positive
  cases; `mesh_fingerprints_match` equal/changed-bins/changed-extent/`None`
  cases; collection-time guard on an unreadable mesh; manifest records the full
  fingerprint; load-time detection of same-count/different-layout drift
  (`_DriftingMeshWorkflow`); and load-time rejection of a manifest whose
  fingerprint bin-product disagrees with the stored voxel count.

### Verification (hardening pass done when) — ALL MET (2026-07-21)

- [x] A sample whose Geant4 input file lacks / changes the scoring-mesh keys
  hard-fails at collection time with a constraint-#3 message — not silently, and
  not only at load time. → `test_guard_rejects_unreadable_mesh`; per-sample
  re-check in `collect_training_data`.
- [x] The manifest carries the full mesh fingerprint (`bins`, extent), and
  `load_training_store` rejects a store whose stored voxel indices disagree with
  it, even when the flat voxel count matches. →
  `test_manifest_records_mesh_fingerprint`,
  `test_load_detects_same_count_different_layout`,
  `test_load_rejects_manifest_mesh_count_mismatch`.
- [x] Both checks are exercised by local tests using the synthetic-dose fake
  workflow (no Geant4 env), consistent with the synthetic-first strategy.

### Original gap analysis (for the record)

---

# Phase 3 — `train_surrogate` mode (PCA-GP forward model)

**Objective:** Build the reduced-basis surrogate from collected data and expose
cheap `predict_dose(β)` and `project(dose)`.

### Approach

1. New `mode: train_surrogate`. Load the Phase-2 store (result table +
   `results.load_field`).
2. Stack dose grids → matrix `Y (N × M)`, `M` = flattened voxel count. Subtract
   mean, SVD/PCA → retain top-`k` components (choose `k` by cumulative variance,
   e.g. ≥99%; dose fields are smooth and low-rank). Store mean + basis `Φ`.
3. Fit one independent GP per PCA coefficient: `β ∈ ℝ⁸ → cᵢ`. Use a genuine
   noise term (constraint #2) — do not force a low-noise prior. Reuse Xopt/BoTorch
   GP machinery where convenient, but a direct `botorch`/`gpytorch` or
   `sklearn` GP per component is acceptable if cleaner.
4. Expose an API object:
   - `predict_dose(β) → (mean_grid, var_grid)` (reconstruct
     `mean + Σ cᵢ(β)·φᵢ`),
   - `project(dose_grid) → coefficient vector` in the same basis,
   - `save()` / `load()` following the existing `torch.save` +
     hyperparameter-dump pattern in `modes._save_model`.
5. Keep the coefficient space the *single* interface the inversion phase talks to,
   so the future "profile summaries" input mode can supply an alternate
   `project()` without changing the GPs.

### Verification (Phase 3 done when) — ALL MET (2026-07-21, synthetic)

- [x] Held-out β points: reconstructed dose matches truth within a reported
  error metric (relative L2), and predicted variance is calibrated (not ~0 —
  sanity check against constraint #2). → `test_holdout_reconstruction_accuracy`
  (mean rel-L2 < 0.10 on held-out β) + `test_predicted_variance_is_positive_and_calibrated`
  (var strictly > 0, predicted std on the order of the injected noise, coverage
  check). Against the real Geant4 store the numeric threshold is re-tuned to the
  campaign's signal-to-noise; the machinery is unchanged.
- [x] Round-trip: `project(predict_dose(β))` ≈ `c_GP(β)`. →
  `test_roundtrip_project_of_predict` (exact by construction, atol 1e-8).
- [x] Model saves and reloads to identical predictions. →
  `test_save_reload_identical_predictions` (allclose atol 1e-10 on predict_dose +
  project).

### What landed (2026-07-21)

- **`src/lume_ace3p/surrogate.py` — `DoseSurrogate`.** Pure numpy + sklearn, no
  workflow coupling. `fit(beta, dose, *, variance=0.99|k, seed)` does mean-center
  → economy SVD → `_choose_k` by cumulative energy (or explicit `k`) → coeffs
  `C = centered @ Φ^T` → one `GaussianProcessRegressor` per column. Kernel =
  `ConstantKernel * RBF(ARD) + WhiteKernel` (the WhiteKernel is the genuine
  fitted noise term, constraint #2 — no low-noise prior). β is min/max-normalized
  to a unit cube before the GPs. API: `predict_dose(β) → (mean_grid, var_grid)`
  (var propagated as `Σ Var[c_i]·φ_i²`, strictly > 0), `project(dose) → coeffs`
  (the single coefficient-space seam Phase 4 inverts against),
  `predicted_coeffs(β) → (mean, var)` (raw GP outputs, so the round-trip holds by
  construction), `save(dir)`/`load(dir)`.
- **Persistence** = three artifacts in the model dir: `basis.npz` (PCA arrays,
  `allow_pickle=False`), `gps.joblib` (fitted GPs — a *trusted local* artifact,
  unlike the untrusted field `.npz`), `surrogate.json` (provenance: k, variance
  target, kept energy, per-GP fitted kernel string — the hyperparameter dump the
  plan asked for, analogous to `_save_model`'s `gp_parameters.txt`).
- **`modes.train_surrogate(mode_cfg, workflow=None)`** — store-consuming mode
  (does not sweep the workflow). Loads via `surrogate_data.load_training_store`
  (inherits the constraint #1/#3 guarantees), hard-fails on a grid-less dry-run
  store, fits, optionally reports held-out accuracy (`holdout:` fraction/count →
  refit-on-train, write `train_report.txt`), then fits on ALL samples and saves.
  Config: `store` (req), `variance` (0.99) / `num_components` (k), `seed`,
  `model_dir` (default `<store>/surrogate`), `holdout`. Dispatched in
  `run_mode` + allowed in `run_lume_ace3p`.
- **Dependency:** `scikit-learn` added to `pyproject.toml` / `requirements.txt`
  (was only an optional import in a cost-function branch; Phase 3 hard-requires it).
- **Example** `examples/geant4_beta_surrogate/geant4_beta_surrogate_train.yaml` —
  documented `train_surrogate` config pointing at the sibling collection store.
- **Tests** `tests/test_surrogate.py` (15, all local/fast, no Geant4 env): a
  synthetic low-rank + noisy analytic β→dose fixture (3 Gaussian-in-z spatial
  modes × smooth nonlinear β amplitudes + Gaussian noise) driven through the real
  `collect_training_data` mode via a synthetic-dose fake workflow, then all
  Phase-3 bars above + shape/dispatch/error-path coverage.

### Deviations / notes

- **`workflow` argument is accepted but unused** by `train_surrogate` (dispatch
  symmetry). The store already holds the `(β, dose)` pairs; the example's
  `workflow:` block is carried only for schema symmetry.
- **sklearn `ConvergenceWarning`s** appear on the tiny synthetic fixture (kernel
  hyperparameters hitting their bounds) — benign, expected for small-N GP fits;
  not silenced so real-data fits surface the same signal if it matters.
- **Real-data swap:** the numeric held-out threshold (0.10 rel-L2) is tuned to the
  synthetic fixture's SNR; against a real Geant4 store it is re-tuned to the
  campaign, but the GP/PCA core and API are unchanged (exactly the separation the
  synthetic-first strategy is designed to preserve).

---

# Phase 4 — `invert_optimize` and `invert_bayesian` modes

**Objective:** Given a target dose file, estimate β. Two modes sharing the same
surrogate and target-loading code, differing only in what they return.

### Approach

Common: load trained surrogate, load target dose file, `project` it into
coefficient space. **Parse the target the same way training grids were stored**
— via `Geant4Module._read_scoring_output` / `results.load_field` shape — so the
projection lines up bin-for-bin with the PCA basis. `plotting/
geant4_deposit_common.parse_deposit_file` returns a *different* shape (reshaped
grid + mesh/units metadata); use it only for header metadata, not as the vector
fed to `project()`. Pick one canonical parser and factor it into a shared helper.

- **`invert_optimize`:** minimize `‖project(target) − c_GP(β)‖²` (+ optional
  regularization / bounds) over β using the Phase-1 generic Xopt driver
  (NelderMead for a point estimate; Bayesian generators optional). Returns β\*
  and reconstruction diagnostics. This reuses the decoupled driver directly —
  the "workflow" here is the cheap surrogate, not Geant4.
- **`invert_bayesian`:** define a likelihood in coefficient space (using the GP
  predictive variance + assumed dose-noise) and sample the posterior over β via
  MCMC (`emcee` or `numpyro`/`pymc` — pick one, add as optional dependency).
  Returns posterior samples / credible intervals and surfaces non-uniqueness
  (multiple β modes explaining one dose).

### Verification (Phase 4 done when)

- **Recovery test:** generate a dose from a known β (held out or fresh Geant4
  run), invert, and confirm recovered β (optimize) / posterior mass (bayesian)
  is consistent with the truth *and* with reconstruction error.
- Bayesian mode produces sensible uncertainty that widens with fewer training
  points / noisier targets.
- Both modes read a real Geant4 dose file end-to-end.

### Deliverables

- Example YAMLs for both inversion modes pointing at a saved surrogate + a
  target dose file.

## Phase 4a — `invert_optimize` DELIVERED (2026-08-06, synthetic)

Split from `invert_bayesian` per the one-phase-per-session rule; the Bayesian mode
is Phase 4b and reuses everything below (target seam, coefficient misfit, bounds).
The MCMC library is still undecided and **none is installed** — deliberately not
forced by this pass.

### Decisions taken

- **Optimizer = scipy multi-start L-BFGS-B** (bounded), not the Xopt driver. The
  surrogate costs microseconds per evaluation, so thousands are free, whereas
  Xopt's Bayesian generators are built to be *frugal with expensive* objectives —
  the wrong tradeoff, plus a workflow-shim would be awkward. Multi-start also
  directly surfaces non-uniqueness. No new dependency (scipy already required).
- **Misfit space = the model's own fit space.** The target is projected through
  the surrogate's transform, so a `log10` model inverts in log space — consistent
  with the Phase-3 finding that log space is where the fit is meaningful (a linear
  residual is dominated by a few peak voxels).
- **Target sources:** a raw Geant4 dose file *and* a stored `field.npz`.
- **Output:** β\* **plus every distinct local minimum**, plus diagnostics.

### What landed

- **Canonical dose parser unified** (the plan's "pick one canonical parser and
  factor it into a shared helper"). `surrogate_data.read_dose_file(path)` is now
  the single parser producing the `{'indices' (M,3), 'values' (M,)}` shape that
  `save_field` persists and the PCA basis is built on;
  `Geant4Module._read_scoring_output` **delegates to it** (keeping its
  `ctx.workdir` join). `plotting/geant4_deposit_common.parse_deposit_file` stays
  for header metadata only. Note `indices` is now a `(M,3)` array rather than a
  list of tuples — `extract(['dose','peak_index'])` still returns a comparable
  tuple, and `field()` no longer needs its own `np.asarray`.
- **Voxel alignment (constraint #3, inversion side).** `project()` is a plain
  `(dose - mean) @ Φᵀ`, so column *j* of the input must be the same physical voxel
  as column *j* of the training grids. Added
  `surrogate_data.align_to_indices(values, indices, reference_indices)` which
  reorders a target by `(ix,iy,iz)` key and **hard-fails** if the target does not
  cover the reference voxel set exactly (missing/extra/duplicate voxels ⇒ a
  different mesh). `load_target_dose(spec)` dispatches `.npz` → `results.load_field`
  vs. raw file → `read_dose_file`.
- **`DoseSurrogate` now persists `voxel_indices`** — the `(M,3)` order its basis
  columns correspond to (`fit(..., voxel_indices=)`, written into `basis.npz`,
  read back by `load`, `None` for older models). `train_surrogate` passes
  `ts.indices`. The mode resolves the order model → store → **hard-fail with a
  fix**; it never guesses.
- **Inversion core on `DoseSurrogate`:** `coeff_misfit(β, target_coeffs)` (the
  coefficient-space objective, automatically in fit space) and
  `invert(target_coeffs, *, bounds, num_starts, seed, cluster_tol)` — bounded
  multi-start L-BFGS-B from a reproducible Sobol scatter (reusing
  `sample_beta_doe`) plus the box center, then **clustering** converged solutions
  into distinct minima (dedupe radius `cluster_tol`, default 2% of box span).
  Default bounds = the model's own training box (outside it a GP extrapolates and
  β\* is not trustworthy). Returns `InversionResult` (`beta`, `misfit`, `minima`,
  `num_distinct`, `beta_dict()`, `relative_l2(surrogate, target, space='fit')`).
- **`modes.invert_optimize(mode_cfg, workflow=None)`** — store/model-consuming
  mode (`workflow` unused, dispatch symmetry). Config: `target` (req),
  `model_dir` / `store`, `num_starts` (32), `seed`, `bounds` (reuses `_doe_bounds`),
  `output_file`. Writes `inversion_result.txt` with one row per distinct minimum
  (`rank, misfit, relative_l2, beta0..betaN`) and prints a non-uniqueness note
  pointing at `invert_bayesian` when >1 minimum is found. Dispatched in `run_mode`
  + allowed in `run_lume_ace3p`.
- **Example** `examples/geant4_beta_surrogate/geant4_beta_surrogate_invert.yaml`.

### Verification (Phase 4a) — ALL MET (2026-08-06, synthetic)

`tests/test_inversion.py`, 21 tests, all local (no Geant4 env), reusing the
Phase-3 synthetic β→dose fixture.

- [x] **Recovery:** a dose from a known β inverts to β\* reproducing it
  (fit-space rel-L2 < 0.05) and recovers the *identifiable* combinations of β. →
  `test_recovery_of_known_beta`. **Important:** the fixture's amplitudes depend on
  β only through group means, so individual components inside a group are
  genuinely non-identifiable — the honest bar is dose-space recovery + recovery of
  the identifiable combinations, *not* naive per-component β equality. That is the
  real physics too, not a test weakness.
- [x] Perfect-target sanity: noiseless target ⇒ misfit < 1e-6. →
  `test_perfect_target_drives_misfit_to_zero`.
- [x] Alignment: a row-shuffled target file gives a bit-identical β\*; a
  different-mesh / duplicate-voxel target hard-fails. →
  `test_align_reorders_shuffled_target`, `test_align_rejects_different_mesh`,
  `test_align_rejects_duplicate_voxels`,
  `test_mode_inverts_raw_dose_file_identically`.
- [x] Both target sources agree. → `test_mode_inverts_npz_target` +
  `test_mode_inverts_raw_dose_file_identically`.
- [x] `log10` model inverts correctly in log space. → `test_log10_model_inverts`.
- [x] Non-uniqueness reported: >1 distinct minimum, one table row each, all
  explaining the target. → `test_reports_multiple_distinct_minima`,
  `test_mode_inverts_npz_target` (row count + misfit ordering).
- [x] Mode + `run_mode` dispatch + fixed-seed reproducibility + bounds respected.
  → `test_run_mode_dispatches_invert_optimize`,
  `test_invert_is_reproducible_and_bounded`, `test_invert_respects_custom_bounds`,
  `test_mode_custom_bounds_and_output_file`.
- [x] Back-compat: a model without `voxel_indices` loads; inversion uses a
  store-supplied order or fails clearly. → `test_legacy_model_without_voxel_order`,
  `test_trained_model_records_voxel_order`.

**Still cluster-gated (unchanged from the plan):** reading a *real* Geant4 dose
file end-to-end and science-level recovery against real data. The parser, the
alignment guard, and the mode all run on real files by construction — what is
untested locally is real data, not the plumbing.

### Follow-up: identifiability reporting + YAML cleanup (2026-08-10) — DELIVERED

Prompted by "can the non-unique solutions be ranked probabilistically?" The
measured answer is **usually no**, and the reason is structural:

- **The degeneracy is a continuous surface, not competing hypotheses.** The
  Jacobian `∂c_GP/∂β` at β\* has **rank 3 on D=8** (unit-β coords: singular values
  `1.8e2, 2.9e1, 1.2e1`, then 5 at machine zero). Since the surrogate reaches β
  only through its `k` retained POD coefficients, **rank ≤ k**, so `k < D` makes
  the inverse rank-deficient *by construction* — `D − k` β directions are
  invisible to the dose. The real trained model also uses ~3 modes.
- **The minima are all equally good.** A χ² (GP-variance-weighted) likelihood
  gives the worst-vs-best minimum relative weight `1.0000`; every misfit is ~1e-10.
  So ranking them sorts solver convergence noise, and presenting it as an evidence
  ranking would imply a preference the data does not support.
- The zero-set is **curved**: the straight line between the two most distant
  minima shows a misfit hump (2.3e+01), but re-descending from that midpoint
  restores ~8.5e-11 after moving only `‖Δβ‖=0.51`. Straight-line interpolation
  simply leaves the manifold. (Worth recording — the hump initially *looks* like a
  barrier separating genuine modes.)

**What landed.** `DoseSurrogate.identifiability(beta)` finite-differences the GP
coefficient mean in **unit-box coordinates** (dimensionless, so singular values are
comparable across β with different physical ranges), SVDs the `(k, D)` Jacobian,
and splits the right singular vectors at the numpy rank tolerance into the β
combinations the dose constrains vs. cannot see. Returns an `Identifiability`
(`rank`, `num_flat`, `singular_values`, `identifiable`, `null_space`,
`sensitivity`, `summary()`, `describe_direction()`). On the synthetic fixture it
correctly recovers the group *sums* as identifiable and the within-group
*differences* as flat — from the GP alone.
`InversionResult.minima_are_distinguishable()` gates the reporting language, and
`invert_optimize` prints the headline, writes `identifiability.txt` (with a
how-to-read preamble), and no longer claims equal-misfit minima are "ranked".
Keys: `identifiability` (default true), `identifiability_file`.

**YAML cleanup (a real fix, not cosmetic).** `train_surrogate` / `invert_optimize`
reference `workflow` only in their signature, yet a `workflow:` block was
*structurally required*: `_resolve_order` rejects an empty list and `main()` exited
on a missing key. Added `modes.STORE_CONSUMING_MODES` (+ `mode_type_of` /
`is_store_consuming`); `_run_declarative` now skips `Workflow.from_config` for
those modes and `main()`'s gate is conditional. The two example YAMLs dropped
their `workflow:` / `workflow_parameters:` / `input_parameters:` blocks — from
~100 lines to **6–7 lines of actual config** each. `collect_training_data` is
deliberately *not* store-consuming (it drives the chain). Both shipped examples
verified end-to-end through the real CLI. Also documented all three surrogate
modes in `docs/yaml_reference.md` (previously absent there) and corrected the
example README, which described the `workflow:` block as "carried for schema
symmetry".

**Tests:** `tests/test_inversion.py` now 30 (was 21). New bars: rank/flat counts;
**null space is genuinely flat** (stepping along it keeps misfit < 1e-3 while the
same step along the best-constrained direction is >100× larger — the assertion
that proves the basis is meaningful); `rank ≤ k` via a pinned `k=1` model;
report artifacts + skip flag; minimal-YAML end-to-end with no `workflow:` key;
shipped examples carry none; and a regression that chain-driving modes still
require one. Full suite **175 passed** / 8 deselected.

---

# Phase 4b — `invert_bayesian` — DELIVERED (2026-08-10, synthetic)

A posterior over β, which is the *correct* object for this problem: the
identifiability finding showed the target is not a set of separated modes but a
**curved, `D−k`-dimensional flat manifold** (5-D on the synthetic fixture). The
posterior therefore comes out tight along the `rank` constrained directions and
prior-wide along the flat ones — the honest answer to "how should these be
ranked".

### Decisions taken

- **numpyro / NUTS as a core dependency** (user's call; `jax` + `jaxlib` come
  transitively). Gradient-based sampling is the point: an ensemble sampler
  explores a curved degenerate manifold poorly.
- **Differentiable GP = a JAX re-expression of PREDICTION only.** Fitting stays
  scikit-learn. A fitted GP's predictive mean/variance is closed form, so the
  re-expression is elementwise RBF + one triangular solve, reading `X_train_`,
  `alpha_`, `L_` and the fitted kernel hyperparameters.

### What landed

- **`src/lume_ace3p/surrogate_jax.py`** — all JAX isolated here, so
  `surrogate.py` stays numpy/sklearn-only and the import cost is paid only by the
  Bayesian path. `gp_params_from_sklearn` (with a **kernel-structure guard** —
  reading `ConstantKernel * RBF + WhiteKernel` positionally would otherwise
  silently mis-read a differently-fitted model into a wrong posterior),
  `coeff_mean_var_fn` (jitted `β → (mean, var)`), `enable_x64`,
  `sample_posterior_nuts`.
- **`DoseSurrogate.sample_posterior(...)` + `PosteriorResult`** — model:
  `β ~ Uniform(training box)`, `c_target ~ Normal(μ_GP(β), sqrt(Var_GP(β) + dose_sigma²))`
  in the model's fit space. `dose_sigma` defaults to the model's own predictive std
  at the box center (a scale set by the fit, not a magic constant).
  `PosteriorResult` exposes `mean/median/std`, `credible_interval`, `prior_std`,
  `max_r_hat`, and **`direction_widths(identifiability)`** — the payoff table of
  posterior-vs-prior width per constrained/flat direction.
- **`modes.invert_bayesian`** — store-consuming; shares the entire target seam
  with `invert_optimize` via a new `_load_inversion_target` helper (so the two
  cannot drift apart on the alignment-critical part). Writes
  `posterior_samples.txt` + `posterior_summary.txt` (per-β summary, `r_hat`/`n_eff`,
  and the direction-width table with a preamble stating that a prior-wide flat
  direction is the *correct* outcome). Warns loudly when `max r_hat > 1.05`.
- **Example** `geant4_beta_surrogate_invert_bayesian.yaml` (minimal, `mode:` only),
  plus `docs/yaml_reference.md` and the example README.

### Two empirical findings that became defaults

1. **`num_chains` defaults to 4, and lowering it is dangerous.** One short chain
   gave `r_hat = 1.61` and reported the flat directions at **~0.04–0.10× prior
   width** — i.e. it looked as though the dose constrained β when it does not.
   That is a *wrong scientific conclusion*, not merely a noisy one. Four chains:
   `r_hat = 1.006`, flat widths ~1.1× (correct). `enable_x64(num_host_devices=)`
   requests CPU devices so chains run in parallel (~31s vs ~112s).
2. **`dense_mass=True` on the NUTS kernel is essential, not a tuning nicety.** The
   flat directions are *correlated* combinations of β; a diagonal mass matrix
   cannot represent that geometry, and more warmup does not fix it (`r_hat` stayed
   1.1–1.4 at 2000 warmup, `n_eff` ≈ 4). Dense: `r_hat = 1.002`, `n_eff` ≈ 2060,
   and faster.

### Verification (Phase 4b) — ALL MET (2026-08-10, synthetic)

`tests/test_bayesian.py`, 17 tests (9 algebraic + 8 real 4-chain NUTS runs, ~3 min
total; all run in the default gate).

- [x] **JAX ≡ sklearn** to `atol=1e-8` (measured 1.6e-9 mean / 1.3e-10 var) on a
  β batch — the load-bearing guard on the re-expression, and it will catch a
  future change in scikit-learn's internals. → `test_jax_prediction_matches_sklearn`.
- [x] Gradients finite, non-zero, right shape. → `test_gradients_flow_through_the_jax_gp`.
- [x] Unexpected / unfitted kernel raises clearly. →
  `test_unexpected_kernel_is_rejected`, `test_unfitted_gp_is_rejected`.
- [x] **Posterior recovers the identifiability split** — constrained directions
  < 0.2× prior (measured 0.013–0.078), flat directions > 0.8× (measured
  1.11–1.25). → `test_posterior_recovers_identifiability_split`.
- [x] Truth's *identifiable combinations* inside the 90% CI (all 3). Deliberately
  not per-component β — the flat directions are unconstrained by construction, so
  a per-component bar would be testing the prior. →
  `test_truth_identifiable_combinations_are_covered`.
- [x] Convergence reported and healthy (`r_hat < 1.05`, `n_eff > 50`). →
  `test_posterior_converges_and_reports_diagnostics`.
- [x] Fixed-seed reproducible, prior box respected, custom bounds narrow the
  posterior. → three tests.
- [x] Mode + dispatch + both artifacts + minimal YAML with no `workflow:`. →
  four tests. Shipped example also verified end-to-end through the real CLI.

### Phase 4 overall verification — status

- [x] **Recovery test** (both modes) — see 4a and 4b bars above.
- [x] **Bayesian mode produces sensible uncertainty** that reflects what the data
  constrains; `test_custom_bounds_narrow_the_posterior` shows it responds to prior
  width. *(The original "widens with fewer training points" phrasing is subsumed
  by the sharper, structural statement: width tracks identifiability.)*
- [ ] **Both modes read a real Geant4 dose file end-to-end** — still cluster-gated.
  The parser, alignment guard and both modes run on real files by construction;
  what is untested locally is real data, not the plumbing.

---

# Phase 5 — Multi-fidelity (deferred)

**Objective:** Speed up training by mixing cheap/noisy and expensive/accurate
Geant4 evaluations, using number of primaries (`beam_on` / particle count) —
and/or scoring-mesh resolution — as the fidelity axis.

**Precondition:** Only start after Phase 3 single-fidelity accuracy is validated.

### Approach

- Reuse the existing `MultiFidelityGenerator` plumbing and cost-function logic in
  `modes.py` (the generic `gp_parameter_sweep` / `_build_generator` path), now
  workflow-agnostic rather than tied to S3P's fidelity variable `s`.
- Fidelity ↔ particle count mapping; cost model from measured runtimes.
- Integrate multi-fidelity data into the PCA-GP (fidelity-aware GPs on
  coefficients). Handle the interaction between MC noise level and fidelity
  (lower particle count = higher noise) — this is the trickiest part and the
  reason it is last.

### Verification (Phase 5 done when)

- Multi-fidelity training reaches comparable held-out accuracy to single-fidelity
  at lower total cost, with the cost accounting reported.

---

## Execution notes for fresh-context sessions

- Read `plans/geant4_surrogate_inversion_plan.md` (this file) and the memory note
  `geant4-surrogate-inversion-project` first.
- Execute **one phase per session.** Do not start a phase before its precondition
  phase's verification passes.
- Update the **Status** line at the top and check off the phase's verification
  bullets as they pass; note any deviations from the plan inline.
- Keep the two cross-cutting correctness constraints (fixed `bin_edges`, genuine
  GP noise) in force in every phase.
