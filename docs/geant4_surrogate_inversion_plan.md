# Geant4 Dose Surrogate & Inversion — Implementation Plan

**Status:** UNSHELVED (2026-07-10). The workflow modularization refactor is
complete (see `docs/workflow_module_refactor_plan.md`), so this project resumes
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
  bin) with `beta0..beta7` declared under `input_parameters.cubit`, or `beta_input: beta`
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
  size/sampler/seed, store path. Large geometry/particle files are **symlinked**
  to the sibling `examples/geant4_track3p_beta/` (tracked as git symlinks, no
  multi-MB duplicates).
- Loader utility `src/lume_ace3p/surrogate_data.py` — DOE sampler
  (`sample_beta_doe`, scipy `qmc` Sobol/LHS) + `load_training_store`, a thin
  wrapper over `results.load_field` + the result table (+ a `manifest.json`
  recording the fixed `bin_edges`/`num_bins`, β order, mesh shape, DOE
  provenance). Not a new on-disk format.

### Implementation notes / deviations

- **DOE bounds live in the `mode:` block** (`variables: {beta0: [lo,hi], ...}`)
  rather than `input_parameters` — the β values under `input_parameters.cubit`
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

- Read `docs/geant4_surrogate_inversion_plan.md` (this file) and the memory note
  `geant4-surrogate-inversion-project` first.
- Execute **one phase per session.** Do not start a phase before its precondition
  phase's verification passes.
- Update the **Status** line at the top and check off the phase's verification
  bullets as they pass; note any deviations from the plan inline.
- Keep the two cross-cutting correctness constraints (fixed `bin_edges`, genuine
  GP noise) in force in every phase.
