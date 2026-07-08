# Workflow Modularization Refactor — Implementation Plan

**Status:** Not started (next: Phase 0.5 — freeze golden baseline). Supersedes
the near-term sequencing of `geant4_surrogate_inversion_plan.md` (that project is
**shelved until this refactor lands** — its Phase 1 "decouple Xopt from
S3PWorkflow" is absorbed into Phase 4 here). Phases: 0.5 baseline → 1 modules →
2 workflow-DAG → 3 single/sweep modes → 4 Xopt modes → 5 data consolidation →
6 migrate/cleanup.
**Owner:** dbizzoze
**Created:** 2026-07-08

## Motivation

The step wrappers in `src/lume_ace3p/` are already modular units
(`Cubit`, `Omega3P`/`S3P`/`T3P`/`Track3P`, `Acdtool`, `Geant4`, `Particles`),
but their **composition is hardcoded**:

- Each workflow subclass in `workflow.py` bakes its chain into `run()`:
  `Omega3PWorkflow` = cubit→omega3p→acdtool, `S3PWorkflow` = cubit→s3p,
  `Geant4Workflow` = [particles]→geant4. The three `run()` / `run_sweep()`
  bodies are ~90% duplicated.
- `run_lume_ace3p.py` dispatches on a hardcoded **(mode × module) matrix** and
  most cells are empty: `scalar_optimize` / `gp_parameter_sweep` only work for
  `s3p`; Geant4 can't be optimized; the architecture cannot *express* a long
  chain such as `cubit→omega3p→particles→geant4` (fields→emission→dose) as one
  declarative pipeline.
- `run_xopt.py` is hardwired to `S3PWorkflow` + S-parameter/frequency parsing.

> **Scope note on Track3P / T3P.** `T3P` and `Track3P` in `ace3p.py` are **bare
> stubs** (they set `module_name`/`output_file` only — no `set_value`, parser,
> or `make_default_input`) and are **out of scope** for this refactor. Today the
> particle tracking is run *externally* by the user; the only "track3p" thing in
> the codebase is the `Particles` **post-processing** step, which reads an
> externally-produced Track3P dump and computes emission weights. So this
> refactor makes the module/mode architecture *able to express* solver chains,
> but a runnable in-pipeline Track3P/T3P solver is a **separate future effort**
> (when implemented, those solvers will require a `mesh` like Omega3P/S3P). No
> `Track3PModule`/`T3PModule` solver is built here.

### Target architecture

Three layers, cleanly separated:

1. **Modules** — one per pipeline step, each declaring what artifact kinds it
   `requires` and `provides`. A thin adapter over the existing step wrappers.
2. **Workflow** — a declarative, YAML-defined *list of modules* validated into a
   runnable DAG (dependency + ordering rules). Exposes a single black-box
   `evaluate(input_dict) -> output_dict`.
3. **Modes** — how the workflow is *driven*: `single`, `parameter_sweep`,
   `scalar_optimize`, `gp_parameter_sweep` (and later the Geant4 surrogate
   modes). Modes are workflow-agnostic; they call `evaluate` and own the outer
   loop (tensor product, Xopt generators, termination).

### Confirmed design decisions (user, 2026-07-08)

- **Sequencing:** rework first; Geant4 surrogate project shelved until after.
- **DataFrames — hybrid:** result tables (`sweep_data`, Xopt logs) become
  pandas DataFrames (aligns with Xopt's internal `X.data`, collapses the manual
  row-builders in `tools.py` into `to_csv`). The **ACE3P `Section` input tree**
  (duplicate-keyed, nested) and **per-run field outputs** (S-params vs
  frequency, 3-D dose/edep voxel grids) stay structured objects/arrays,
  referenced from the table — they are ragged/nested and do not fit one flat row.
- **Clean break (no back-compat):** adopt the new declarative module-list YAML
  schema and `DataFrame.to_csv` output files; **rewrite the example YAMLs**. The
  old byte-for-byte S3P output contract and old `module:` dispatch are dropped.
  No compatibility shim.

---

## New YAML schema (clean break)

Referenced by all phases. Finalized in Phase 1, consumed from Phase 2 on.

```yaml
workflow_parameters:
  workdir: 'lume-ace3p_workdir'
  workdir_mode: 'auto'          # 'auto' | 'manual'
  paths: { ... }                # optional executable-path overrides
  dry_run: false                # optional force

# Ordered list of pipeline steps. Order is a hint; the real execution order
# comes from artifact dependency resolution (Phase 2). Each entry: a 'module'
# type plus that module's config.
workflow:
  - module: cubit
    journal: 'bend-90degree.jou'      # OR provide a prebuilt mesh below
  # - module: mesh                    #   alternative source: skip cubit
  #   file: 'prebuilt.ncdf'
  - module: s3p
    input: 'bend-90degree.s3p'
    tasks: 16
    cores: 8
    opts: '--cpu-bind=cores'

# How to drive the workflow.
mode:
  type: scalar_optimize             # single | parameter_sweep |
                                    # scalar_optimize | gp_parameter_sweep
  # ... mode-specific keys (vocs, xopt, sweep) live here or in sibling blocks

# Input variable space (the sweep/optimization knobs). Same three buckets as
# today's WorkflowInputs: cubit (unprefixed input_parameters), ace3p, macro
# (geant4_input_parameters).
input_parameters:
  cubit:
    cornercut: [14, 17]             # {min,max,num} or list => sweep/vocs axis
# ace3p_input_parameters: ...       # duplicate-key block, parsed as pairs
# geant4_input_parameters: ...

# What scalars to pull out of which module's artifacts, exposed to the mode.
# Replaces the S-parameter-hardcoded extraction in run_xopt.py.
output_parameters:
  reflection: { module: s3p, quantity: 'S(0,0)', at: { frequency: 12.0e9 } }

vocs_parameters:   { ... }          # for optimize / gp modes
xopt_parameters:   { ... }
sweep_parameters:  { ... }          # for gp_parameter_sweep
particle_parameters: { ... }        # for particles module
```

Key changes vs. today: `workflow` is an explicit module list (not implied by
`module:`); `mode` replaces the `(mode, module)` pair; output extraction is a
declarative per-module spec instead of S-parameter parsing in code.

---

## Core abstractions (finalized Phase 1)

```python
# Artifact kinds — the vocabulary modules glue on:
#   'journal'            Cubit journal file
#   'mesh'               genesis/ncdf mesh (cubit+meshconvert, or provided)
#   'em_solution'        Omega3P/S3P solver output dir
#   'rf_post'            acdtool postprocess results
#   'track3p_particles'  raw Track3P particle dump (produced EXTERNALLY today;
#                        supplied as a source file, not by an in-pipeline solver)
#   'particle_source'    Geant4-format particle file (Particles output)
#   'dose_grid','edep_grid'  Geant4 scoring outputs

class RunContext:
    workdir: str
    inputs: WorkflowInputs        # materialized (scalars) for THIS eval point
    artifacts: dict[str, str]     # kind -> path (populated as modules run)
    outputs: dict                 # collected scalar/structured outputs
    dry_run: bool
    paths: dict

class Module:
    type: str
    name: str                     # instance label (defaults to type)
    requires: set[str]            # artifact kinds needed upstream
    provides: set[str]            # artifact kinds produced
    def run(self, ctx: RunContext) -> None: ...
    def extract(self, ctx, spec) -> object: ...   # scalar/field for output_parameters

class Workflow:
    modules: list[Module]         # topologically ordered after validation
    def evaluate(self, input_scalars) -> dict:    # the black box modes call
        # materialize inputs -> RunContext(workdir) -> run modules in order
        # -> collect output_parameters -> return output dict
    def sweep_axes(self): ...     # delegates to WorkflowInputs
```

`Workflow.evaluate(input_dict) -> output_dict` is the single seam every mode
uses. `WorkflowInputs` (from `inputs.py`) is reused unchanged as the input model.

---

# Phase 0.5 — Freeze a golden baseline (do this first)

**Objective:** Capture the *current* behavior of every example as frozen
fixtures **before any code changes**, so every later phase can diff against a
stable reference. "Clean break" changes output *file formats*, so equivalence is
checked on **numeric content**, not bytes — that requires a captured baseline
now, while the legacy code path is still intact.

### Approach

1. Run all example YAMLs in dry-run mode (ACE3P/Geant4 env is absent locally, so
   dry-run is the reachable baseline) and save their produced output files
   (`sim_output*.txt`, `sweep_output.txt`, `DRY_RUN.txt`, GP dumps) under a
   `tests/baseline/` fixtures tree, one dir per example.
2. For the paths that need a real solver's numbers, capture instead the parsed
   values from a **synthetic** solver output fixture (the pattern already used in
   `tests/test_run_xopt_compat.py`) so the optimization-trajectory / GP checks in
   Phases 3–4 have a deterministic reference independent of the cluster.
3. Record, per example, which quantities are numerically checkable (sweep
   values, Xopt trajectory, extracted scalars) vs. which are only
   pipeline-reachability (dry-run marker files).
4. Add a small helper to load a baseline fixture and compare (with tolerance) to
   a freshly-produced result table.

### Verification (Phase 0.5 done when)

- `tests/baseline/` contains reproducible fixtures for all current examples.
- A "baseline self-check" test re-runs the *current* code and confirms it still
  matches its own captured fixtures (guards against flaky/nondeterministic
  captures before they're used as a reference).

### Deliverables

- `tests/baseline/` fixtures + a comparison helper. No `src/` changes.

---

# Phase 1 — Module layer + core abstractions

**Objective:** Introduce `Module`, `RunContext`, the artifact vocabulary, and a
module registry. Wrap each existing step wrapper as a `Module`. No workflow/mode
changes yet — the old `run_lume_ace3p.py` path still works alongside.

### Approach

1. New `src/lume_ace3p/modules.py`: `RunContext`, `Module` base, a
   `MODULE_REGISTRY` (type-string → class), the artifact-kind constants.
2. One module class per step, each a thin adapter that constructs the existing
   wrapper and translates ctx.artifacts ↔ wrapper I/O:
   - `CubitModule` (provides `mesh` from `journal`; runs meshconvert unless
     `meshconvert: false`), `MeshSourceModule` (provides `mesh` from a given
     file — this is the declarative replacement for `skip_cubit` + a supplied
     mesh),
   - `Omega3PModule` / `S3PModule` (requires `mesh`, provides `em_solution`),
   - `AcdtoolModule` (requires `em_solution`, provides `rf_post`),
   - `Track3PSourceModule` (provides `track3p_particles` from an externally
     supplied dump file — **source module, no solver**; the runnable
     Track3P/T3P *solver* modules are the separate future effort noted above and
     will `require mesh`),
   - `ParticlesModule` (requires `track3p_particles`, provides
     `particle_source`; owns the `beta`/`beta_input(s)` resolution currently in
     `Geant4Workflow._resolve_beta`),
   - `ParticleSourceModule` (provides `particle_source` directly from a
     Geant4-format file — declarative replacement for today's
     `geant4_particle_file` bypass in `workflow.py:445`),
   - `Geant4Module` (requires `particle_source`, provides `dose_grid`/`edep_grid`).
3. Move `_resolve_beta`, `_geometry_files`, `_read_scoring_output`,
   `_output_files` from `Geant4Workflow` into `Geant4Module`/`ParticlesModule`.
4. **Retire the skip-flags** (`skip_cubit`, `skip_solver`, `skip_acdtool`,
   `skip_meshconvert`) and the `geant4_particle_file` bypass: in a declarative
   module list, "skip X" is simply "don't list module X," and a prebuilt
   artifact is a source module (`MeshSourceModule` / `ParticleSourceModule`).
   The one exception is meshconvert, which is a sub-step *inside* `CubitModule`
   and stays as a per-module `meshconvert:` bool. Do not carry the skip-flags
   into the new schema.
5. Each module implements `dry_run` behavior consistent with today's
   per-workflow dry-run blocks and `extract(ctx, spec)` for its own quantities
   (S-param@freq, dose total/peak, RoverQ/kickFactor/maxFields).

### Verification (Phase 1 done when)

- Each module runs in isolation in dry-run mode from a hand-built `RunContext`,
  producing/consuming the expected artifact keys.
- `extract` reproduces the same scalar values the current `evaluate()` methods
  return for equivalent inputs (unit test with a synthetic solver output file).
- No change to `run_lume_ace3p.py` dispatch yet; existing examples still run.

### Deliverables

- `src/lume_ace3p/modules.py` + `tests/test_modules.py`.

---

# Phase 2 — Declarative Workflow + DAG validation

**Objective:** Build a `Workflow` from the YAML `workflow:` list, validate it
into a runnable ordered DAG, and execute it to produce artifacts. Reproduce the
three legacy chains as declared workflows.

### Approach

1. New `src/lume_ace3p/workflow_graph.py` (or extend `workflow.py`): parse the
   `workflow:` list into `Module` instances via the registry.
2. **Dependency resolution + validation:** topologically order modules so every
   `requires` is met by an upstream `provides` or a source module. Enforce rules
   that emerge naturally from `requires`/`provides` plus a few explicit checks:
   - a `mesh` must come from exactly one source (cubit journal XOR mesh file),
   - solver modules (`omega3p`/`s3p`) require `mesh`; `acdtool` requires
     `em_solution`; `particles` requires `track3p_particles`; `geant4` requires
     `particle_source`,
   - `track3p_particles` and `particle_source` may only come from their source
     modules (`Track3PSourceModule` / `ParticleSourceModule`) in this refactor —
     there is no in-pipeline Track3P solver yet. **Future:** when the Track3P/T3P
     solver modules land, they will `require em_solution` (tracking consumes the
     EM field solution, not just the mesh) and `provide track3p_particles`; the
     DAG rules should be written so adding them is purely additive.
   - clear error messages naming the missing/duplicate artifact.
3. Implement `Workflow.evaluate(input_scalars)`: materialize `WorkflowInputs`,
   build a `RunContext` with the resolved workdir (reuse `_getworkdir` naming),
   run modules in order, then collect `output_parameters` via each module's
   `extract`. Returns the per-eval output dict (structured — DataFrame framing
   is added at the mode layer, Phase 3+).
4. Keep `Workflow` decoupled from any mode/loop.

### Verification (Phase 2 done when)

- The three legacy chains, expressed as `workflow:` lists, run end-to-end
  (dry-run where solver env absent) and produce the **same artifacts and the
  same extracted output values** as the current `Omega3PWorkflow` /
  `S3PWorkflow` / `Geant4Workflow` single `run()`.
- A new multi-step chain using only *runnable* modules validates and orders
  correctly — e.g. `track3p_source→particles→geant4` (external Track3P dump →
  emission weights → dose), and `cubit→s3p→acdtool`. Dry-run reaches the final
  step with all required artifacts present.
- Invalid graphs fail validation with a clear message: missing mesh source,
  acdtool before solver, `particles` with no `track3p_particles` source,
  `geant4` with no `particle_source`, two mesh sources.

### Deliverables

- Workflow builder/validator module + `tests/test_workflow_graph.py`.

---

# Phase 3 — Mode layer: `single` + `parameter_sweep` (DataFrame results)

**Objective:** Introduce the mode abstraction and reimplement `single` and
`parameter_sweep` as workflow-agnostic modes returning pandas DataFrames.

### Approach

1. New `src/lume_ace3p/modes.py`: `run_mode(mode_cfg, workflow, output_spec)`
   dispatch. Modes call only `workflow.evaluate` and `workflow.sweep_axes`.
2. `single`: one `evaluate`, one-row result DataFrame.
3. `parameter_sweep`: reuse the tensor-product logic (currently `_run_sweep`)
   over `sweep_axes()`; one row per grid point. **Result container = DataFrame**
   with columns = input variable names + extracted scalar outputs. Per-run field
   outputs (S-param vectors, dose grids) are stored as referenced objects /
   files, not exploded into the scalar table — except the S3P long-format case,
   which is emitted as a tidy `(inputs..., Frequency, S(m,n)...)` DataFrame.
4. `to_csv`-based writers replace `WriteOmega3PDataTable` / `WriteS3PDataTable`
   for the sweep path (those manual writers are removed in Phase 6).

### Verification (Phase 3 done when)

- `parameter_sweep` over each legacy chain produces a DataFrame whose numeric
  content matches the old sweep output (column layout may differ — clean break).
- Geant4 β-broadcast sweep (`beta_input`) runs through the mode and yields the
  expected per-point dose scalars.
- `single` mode round-trips one evaluation.

### Deliverables

- `src/lume_ace3p/modes.py` + tests. `run_lume_ace3p.py` gains a new dispatch on
  `mode.type` over a built `Workflow` for `single` / `parameter_sweep`. **Leave
  the legacy `(mode,module)` matrix and the `Omega3P/S3P/Geant4Workflow`
  subclasses in place** — the Xopt cells still depend on them until Phase 4.
  Deletion happens in Phase 6, after all modes exist. (A temporarily
  dual-dispatch `run_lume_ace3p.py` on the `dev` branch is fine; no `master`
  merge until the whole refactor is tested — so the intermediate state is
  acceptable and does not need a compatibility shim.)

---

# Phase 4 — Xopt modes: `scalar_optimize` + `gp_parameter_sweep`

**Objective:** Make the Xopt driver workflow-agnostic — **this absorbs Geant4
surrogate-project Phase 1.** Any workflow (S3P, Geant4, the full chain) can be
optimized/swept via generic output extraction.

### Approach

1. Move `run_xopt` / `run_lf_sweep` into the mode layer, replacing the
   hardcoded `S3PWorkflow` construction and S-parameter parsing with:
   `workflow.evaluate(input_dict)` + the declarative `output_parameters` spec to
   pull the objective scalar(s) from the returned outputs.
2. Preserve all generators (NelderMead, ExpectedImprovement, MultiFidelity, UCB,
   MOBO/EHVI, BayesianExploration) and the fidelity-variable / cost-function
   logic. These become generator config under `xopt_parameters`, unchanged.
3. Xopt logging uses `X.data` (already a DataFrame) → `to_csv`; drop the
   `WriteXoptData` string-dump and `WriteS3PDataTable` xopt-append path.
4. Honor the Geant4 correctness constraints for when Geant4 becomes an objective
   (genuine GP noise — do **not** force `use_low_noise_prior` for MC-noisy dose;
   require explicit `bin_edges`). Document these as mode-config guards.

### Verification (Phase 4 done when)

- The S3P optimization + GP-sweep examples reproduce the same optimization
  trajectory / GP predictions as today (numeric, not file-format, equality),
  driven through the generic mode.
- A Geant4 workflow can be selected as the `scalar_optimize` objective (dry-run
  proof that `evaluate`→objective wiring works with no S3P-specific code).
- All six generators construct and step under the generic driver.

### Deliverables

- Xopt modes folded into `modes.py`; `run_xopt.py` reduced to generator
  construction helpers or removed. Tests extend `tests/test_run_xopt_compat.py`
  to drive the generic path with a synthetic workflow. After this phase all four
  modes exist on the new architecture; the legacy dispatch matrix is now
  unreferenced by the CLI but the `Omega3P/S3P/Geant4Workflow` subclasses stay
  **importable/callable** so Phase 5 equivalence tests can construct old vs. new
  on the same input. Actual deletion is Phase 6.

---

# Phase 5 — Result/data consolidation (hybrid DataFrames)

**Objective:** Finalize the hybrid data model and remove the last dict-based
result plumbing.

### Approach

1. A single result-table representation (DataFrame) shared by `parameter_sweep`,
   `scalar_optimize`, `gp_parameter_sweep`: one row per evaluation, input columns
   + scalar output columns, plus an optional column referencing the stored field
   artifact (path/handle) for that row.
2. Field outputs keep their structured form: S3P `{Frequency, S(m,n)...}` arrays
   and Geant4 `{indices, values}` voxel grids. Provide small accessors to load a
   row's field artifact on demand.
3. `WorkflowInputs` / ACE3P `Section` tree unchanged (explicitly *not*
   DataFrame-ified).

### Verification (Phase 5 done when)

- All three modes emit their result DataFrame via one shared code path.
- Field artifacts for a given row load back to the same arrays.
- No remaining callers of the old dict `sweep_data` tuple-keyed structure.

### Deliverables

- Consolidated result module; `tools.py` writers deleted or reduced to `to_csv`
  helpers.

---

# Phase 6 — Migrate examples, docs, cleanup

**Objective:** Move the repo fully onto the new schema and remove dead code.

### Approach

1. Convert **a representative few** example YAMLs to the new
   `workflow:` + `mode:` + `output_parameters:` schema — at minimum one per mode
   and one per solver family (e.g. `s3p_optimization`, `s3p_sweep`, an
   `omega3p_*`, `geant4_track3p_beta`, `track3p_particle_weight`). The full set
   of 11 does not need migrating in this phase; add the rest incrementally
   later. Add one new example exercising a runnable multi-step chain
   (`track3p_source→particles→geant4`).
2. Delete the legacy `Omega3PWorkflow`/`S3PWorkflow`/`Geant4Workflow` subclasses
   and the old dispatch matrix once modes cover them and the Phase-5 equivalence
   tests have run against them.
3. Update `README` / in-repo docs and the memory notes (`project_overview`,
   `ace3p_modules_t3p_track3p`) to the module/mode architecture.
4. Re-point `geant4_surrogate_inversion_plan.md`: mark Phase 1 as delivered by
   this refactor's Phase 4; its Phases 2–4 (collect_training_data,
   train_surrogate, invert_*) become **new modes** on the new architecture.

### Verification (Phase 6 done when)

- The migrated examples run (dry-run where env absent) under the new schema and
  match their Phase-0.5 baselines on numerically-checkable quantities.
- No references to removed workflow subclasses / writers remain in `src/`.
- Docs + memory reflect the new architecture.

---

## Execution notes for fresh-context sessions

- Read this file first. Execute **one phase per session**; do not start a phase
  before its predecessor's verification passes. **Phase 0.5 (freeze the golden
  baseline) must run before Phase 1** — the baseline can't be captured once the
  legacy path starts changing.
- Update the **Status** line and check off verification bullets as they pass;
  note deviations inline.
- "Clean break" is in force: do not add back-compat shims for the old YAML or
  output formats. Do preserve **numeric** equivalence where a legacy example has
  a checkable result (optimization trajectory, sweep values), diffed against the
  Phase-0.5 fixtures.
- **Branch policy:** all work stays on `dev`; a broken/dual-dispatch
  intermediate state on `dev` is acceptable. No `master` merge until the whole
  refactor is complete and tested — so no phase needs to keep the CLI fully
  working in isolation, only to keep its own tests green.
- **Track3P/T3P are out of scope.** They are stubs today and particle tracking
  is run externally; only the `Particles` post-processing + an external-dump
  source module are built. The runnable Track3P/T3P *solver* modules (which will
  `require mesh`/`em_solution`) are a separate future effort — keep the DAG
  rules additive so they slot in later.
- Keep DataFrames scoped to result tables; never DataFrame-ify the ACE3P
  `Section` tree or the field (S-param / voxel) outputs.
- The Geant4 surrogate/inversion project (`geant4_surrogate_inversion_plan.md`)
  stays shelved until Phase 6; its Phase-1 goal is delivered here in Phase 4.
```