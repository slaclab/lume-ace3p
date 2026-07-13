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
**Owner:** dbizzoze
**Created:** 2026-07-08

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
   `Particles.assign_bins` (`src/lume_ace3p/particles.py`) are data-driven
   (`z_vals.min() .. max()`) and drift per run, making the β→dose map
   non-stationary and poisoning the GP. Every generated training YAML / run
   config must carry an explicit, shared `bin_edges` (length `num_bins + 1`).
   **Note:** the existing `bin_edges` enforcement in
   `modes._mc_noise_guards` fires only for the Xopt modes (`scalar_optimize` /
   `gp_parameter_sweep`). The new `collect_training_data` mode (Phase 2) is a DOE
   mode, not an Xopt mode, so it does **not** inherit that guard — it must call
   `_mc_noise_guards` (or replicate the check) to enforce explicit `bin_edges`
   itself.
2. **Geant4 dose is Monte-Carlo noisy.** The surrogate GPs must include a genuine
   noise term. Do **not** reuse S3P's low-noise / interpolating prior
   (`use_low_noise_prior`). `modes._build_generator` already leaves it at its
   default (`False`) when `mc_noisy_objective` is set — mirror that in the
   surrogate GPs; default to a fitted noise/likelihood.

## Repository orientation (updated 2026-07-12, post-refactor)

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
  `ParticlesModule._resolve_beta` (`src/lume_ace3p/modules.py:488`): set
  `beta_inputs: [beta0 … beta7]` on the `particles` **module** entry (one per
  bin) with `beta0..beta7` declared in `input_parameters`, or `beta_input: beta`
  to broadcast one scalar to all bins. The single-run path handles an arbitrary
  8-vector; no changes to the Geant4/particle plumbing are needed to evaluate
  training points. See `examples/geant4_track3p_beta/geant4_track3p_beta.yaml`
  (ships the scalar-broadcast form; documents the 8-D upgrade inline).
- Xopt driver: now the generic modes `modes.scalar_optimize` /
  `modes.gp_parameter_sweep` — workflow-agnostic (objective pulled from
  `Workflow.evaluate()` + declarative `output_parameters`), not S3P-specific.
- **Full dose grids are already captured**, not just extracted scalars:
  `Geant4Module.field(ctx)` (`modules.py:714`) returns
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

- **YAML is byte-for-byte unchanged.** `examples/s3p_optimization/`,
  `examples/s3p_mf_optimization/`, `examples/s3p_bayesian_sweep/`,
  `examples/UCB_Example.yaml`, `examples/MOBO_ExpectedHypervolume_Example.yaml`
  all still run with no edits.
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
5. Enforce the two cross-cutting constraints: **explicitly require `bin_edges`**
   in this mode (call `modes._mc_noise_guards` or replicate it — Phase 2 is not
   an Xopt mode and does not inherit that guard automatically); make fidelity
   (particle count) an explicit recorded field for later.

### Verification (Phase 2 done when)

- A small N (e.g. 8–16) DOE run produces a training store loadable in one call
  returning aligned `β` matrix and dose-grid tensor with consistent shapes.
- Re-running the mode skips already-computed points (resumability).
- Dry-run mode works without the Geant4 app environment (mirrors the existing
  `Geant4Module` / `Workflow` dry-run behavior) for pipeline testing.

### Deliverables

- New example under `examples/` (e.g. `geant4_beta_surrogate/`) with a YAML
  declaring `beta0..beta7` bounds, explicit `bin_edges`, DOE size, and the store
  path. Base it on `examples/geant4_track3p_beta/` (switch `beta_input` →
  `beta_inputs: [beta0..beta7]`, add the 8 bounds, uncomment `bin_edges`).
- Loader utility (new module, e.g. `src/lume_ace3p/surrogate_data.py`) — a thin
  wrapper over `results.load_field` + the result table, not a new on-disk format.

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

### Verification (Phase 3 done when)

- Held-out β points: reconstructed dose matches Geant4 dose within a reported
  error metric (e.g. relative L2), and predicted variance is calibrated (not
  ~0 — sanity check against constraint #2).
- Round-trip: `project(predict_dose(β))` ≈ `c_GP(β)`.
- Model saves and reloads to identical predictions.

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
