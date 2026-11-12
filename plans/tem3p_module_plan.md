# TEM3P as a Workflow Module — Implementation Plan

**Status: COMPLETE 2026-11-12.** Phase 0 ran as job 44528190 (fixtures in
`tests/fixtures/tem3p/`). Phases 1 to 4 built the wrapper, parsers, module,
mesh hand-off, the `tem3p_lfd` example, the other problem types and `files:`
directories. Phase 5 wrote the docs (`docs/tem3p_reference.md`). Results are
in §4; the one follow-up is in §6. Unreleased on dev.

Written 2026-11-12 on S3DF, following the investigation in
`plans/tem3p_investigation.md` (the shareable summary; this plan assumes it).
**Scope decision taken 2026-11-12 (user):** the primary use is the linear RF
detuning chain with two Omega3P runs. TEM3P is implemented **without** a
feedback-loop construct. Users who want convergence append further
`tem3p → omega3p` pairs to the chain by hand. A dynamic "repeat until
converged" block is future work with its own plan.

The plan reuses the machinery of the Track3P plan (`plans/track3p_module_plan.md`):

- the `_SolverModule` pattern;
- upstream-directory injection, the way Track3P fills in `FieldDir`;
- the header-driven column reader;
- fixtures copied verbatim from real runs;
- build-time `WorkflowValidationError`s that name the fix.

It also relies on the per-instance artifacts and `from:` bindings from
`plans/multi_instance_workflow_plan.md`.

---

## 1. Use cases

**A. RF detuning: the primary case, and the shipped example.**

```
cubit(vacuum) ─▶ omega3p ─────────────┐ em_solution
cubit(body) ───────────────────────▶ tem3p ─▶ omega3p_deformed
                                 mesh       mesh (DeformedVacuumMesh.ncdf)
```

Columns are `frequency` from both Omega3P instances, TEM3P's displacement
scalars, and `derived_parameters: { df: …, k_l: … }`.

This covers:

- Lorentz-force detuning (SRFCell);
- thermal plus Lorentz-force detuning (RfGun-Coupler);
- sweeps and optimization over geometry or gradient. Pre-tuning against
  deformation is an Xopt objective on the deformed frequency.

**B. Appended pairs, for convergence by hand.**
`… → tem3p → omega3p_d1 → tem3p_2 → omega3p_d2`. Each pair takes its fields
from the previous deformed run. It must deform the *original* vacuum mesh,
never the already-deformed one (§3.4).

**C. TEM3P without RF: supported, not the focus.**

- Mechanical eigenmodes (TeslaCavity): a mode-indexed table.
- Pure thermal with prescribed loads.
- S3P-driven thermal (SrfCoupler), with `s3p` as the EM producer.

**Out of scope:**

- the feedback-loop construct;
- thermal transients (`Transient:`);
- `HarmonicAnalysis`;
- per-mode deformed meshes from the eigen solver (`DeformedVacuumMesh_Mode*`);
- `acdtool postprocess project`;
- `tem3pOutputConverter`;
- Gun3P and Pic3P.

## 2. Verified facts (from the investigation; Phase 0 re-verifies)

**Binary and docs.**

- The binary is `/sdf/group/rfar/ace3p/bin/tem3p`, the Rocky 9 build of
  2026-11-02.
- Usage: `tem3p <input> [jobname]`, with the results directory defaulting to
  `tem3p_results`. So `accepts_results_dir_arg = True`.
- A wrong argument count prints usage and exits 0. The wrapper must therefore
  verify outputs rather than trust the exit code.
- References: `references/TEM3P-commands.pdf`, the web docs, and Lixin's
  source in `/sdf/group/rfar/lge/sdf/ace3p/tem3p/src`.

**Which problems run.** The blocks present decide this:

- `ThermostaticProblem` and/or `ElasticProblem`;
- `EigenSolver` inside `ElasticProblem` for mechanical modes.

The `ThermoElasticProblem` / `RunId` marker is not read. Its tolerance and
`MaxIterations` keys do nothing: there is no outer iteration.

**How TEM3P reads the EM result.** It reads `<Directory>/VECTOR/postprocess.in`.
That file holds an **absolute** `ModelInfo.File` and the EM run's `JobName`.
TEM3P then loads the fields from `<JobName>/VECTOR/…` **relative to its own
working directory**.

- TEM3P must therefore run in the EM run's working directory. LUME-ACE3P's
  shared per-evaluation workdir already guarantees this.
- Only one EM directory is supported.
- Every rank loads the whole vacuum mesh plus fields: about 390 MB per rank for
  SRFCell.

**Deformed vacuum mesh.** `MeshDump: { WriteDeformedEMMesh: on,
EMMeshInputDir: <EM dir> }` writes `<job>/DeformedVacuumMesh.ncdf`.

- Only nodes on the vacuum sideset named by `Omega3PId` move. That ID is taken
  from `LFDetuning` and `Neumann` boundaries.
- Node matching uses a hard-coded tolerance of 1e-3.
- `EMMeshInputDir` is mandatory when `WriteDeformedEMMesh` is on.
- `MeshDeformScale` is not read.

**Outputs, all in `<job>/`.**

- `ResultFile`, opened in **append** mode. Its contents:
  - a thermal block: `Max T =`, `Min T =`, `P (SideSet n) =`;
  - an elastic header-plus-rows table: `u_{x,y,z} min/max`, `u_mag max`,
    `block_id`;
  - an eigen table led by `Frequency [Hz]`.
- `tem3p.log`, which holds the scaling factor, Newton history, DOFs, run time
  and `Mode n, Frequency: … Hz`.
- `tem3p.out`, which is only the input echo and carries no results.
- `tem3p.warn`, `tem3pStat.log`, `Displacement.mod`, `Temperature.mod`,
  `Stress.mod` and `Strain.mod`, `DeformedMesh.ncdf`, `DeformedVacuumMesh.ncdf`,
  `OUTPUT/`, `VECTOR/postprocess.in`.

**Silent input failures.** The binary warns about neither unknown nor misspelled
keys. The CW23 inputs carry three ignored spellings:

- `NeumanValue`;
- `AbsoluteTolerante`. `AbsoluteTolerance` is not read either; only
  `Tolerance` is;
- `Abs tolerance` / `Rel tolerance`, written for `AbsTolerance` /
  `RelTolerance`.

Separately, a `MixedType` that is not exactly 3 upper-case entries makes the
binary exit.

**Archived reference values (2023 build).**

| Case | Undeformed (Hz) | Deformed (Hz) | Δf |
|---|---|---|---|
| SRFCell, 35 MV/m | 1300957728.48 | 1300950556.38 | −7172.1 Hz |
| RfGun | 2853388398.85 | 2853435826.15 | +47427.3 Hz |

TeslaCavity mode 1 is at 62.82 Hz. SrfCoupler reaches Max T = 324.352 K after
9 Newton iterations.

## 3. Design

### 3.1 One module type, `tem3p`

TEM3P's problem types are different input files for the same binary, so there
is one `tem3p` module type. It is a `_SolverModule` subclass with:

- `_wrapper = Tem3P` (in `ace3p.py`);
- `default_job_name = 'tem3p_results'`;
- `_results_file = 'ResultFile'`;
- `requires = {MESH}` (the body mesh), plus the **optional** requirement
  `EM_SOLUTION` (§3.2);
- `provides = {TEM3P_SOLUTION, MESH}` (§3.3).

**New artifact kind `TEM3P_SOLUTION = 'tem3p_solution'`.** Nothing consumes it
yet. It exists so that `TEM3P_SOLUTION` can be the `_artifact` the base `run`
records, and so that a later `postprocess project` has something to require.
It does not touch the acdtool sync assert, which only checks kinds named by
`COMMANDS`.

### 3.2 Optional requirements (new, generic)

Today `requires` is all-mandatory, and `_bind` raises when there is no
producer. Add a class attribute `optional_requires = frozenset()`.

**How `_bind` handles an optional kind:**

- An explicit `from:` binds it, with the same checks as now.
- Otherwise, exactly one producer binds it.
- Zero producers leaves it unbound, with no error.
- Several producers without `from:` is still the "name it" error. Guessing is
  worse than asking.

**Related changes:**

- `_binding_map` accepts optional kinds in the mapping form.
- The string shorthand counts mandatory plus optional kinds.

**Plain `requires` is unchanged, so existing configs and their hashes do not
move.** `module_hash` already covers whatever lands in `sources`.

**TEM3P's build-time consistency check.** It parses the TEM3P input with
`parse_ace3p` after the scoped overrides are applied.

- The input couples to RF, but `EM_SOLUTION` is unbound → error. "Couples to
  RF" means any of:
  - a `Boundary`/`HeatSource` with `ConditionType` in
    {`RFHeating`, `RFheating`, `RFHeatingMap`, `LFDetuning`, `LFdetuning`};
  - `MeshDump.WriteDeformedEMMesh: on`.
- `EM_SOLUTION` is bound, but nothing in the input couples → warning.

### 3.3 TEM3P provides a mesh

When the merged input has `WriteDeformedEMMesh: on` and no `EigenSolver`,
`run` records:

```
ctx.provide(self, MESH, <workdir>/<job>/DeformedVacuumMesh.ncdf)
```

It also records `set_job_name(self, MESH, job)` alongside.

**Mesh provided but not written.** A downstream binding to a `tem3p` that will
not write the deformed EM mesh is a **build-time** error, and the error names
the missing keys. That is decided from the parsed input, the same way as the
check in §3.2. The case with `EigenSolver` is the same error, saying that
per-mode meshes are out of scope.

**Downstream injection (new, in `_SolverModule`).** When a solver's bound
`MESH` producer declares `mesh_output`, the solver's `_prepare_solver` and
`_prepare_dry_run` set:

```
ModelInfo.File = ./<producer job>/DeformedVacuumMesh.ncdf
```

The path is relative to the shared workdir.

- `mesh_output` is a new class attribute naming the produced file relative to
  the job directory. Today only `Tem3PModule` declares it.
- No existing producer declares `mesh_output`, so **no existing workflow
  changes**.
- The point of the injection is that both Omega3P instances can share **one
  input file**. The existing `_input_name` already gives them distinct written
  names, so the second instance needs no hand-edited copy.
- A user-written `ModelInfo.File` that already names that mesh is accepted.
  One naming anything else is overridden with a logged note.

### 3.4 Injection into the TEM3P input

This is done in `_prepare_solver`, mirroring Track3P's `FieldDir`. The leaves
are positional children of `ThermostaticProblem` and/or `ElasticProblem`.

1. **`MeshFile`.** If the bound `MESH` producer is a Cubit or `mesh` module,
   leave it alone: the file is staged by basename, as for the other solvers.
   A check confirms that `MeshFile`'s basename equals the producer's exported
   name; on a mismatch, the build fails and names both.
2. **`Directory`** in every coupled `Boundary` and every RF `HeatSource` is set
   to the bound EM job, **always** (changed at Phase 2). The Track3P rule of
   keeping a value that names an existing directory is unsafe here: the shared
   workdir holds the *other* EM runs, so an appended pair whose input still
   says `omega3p_results` would silently read the first run's fields. A
   different user value is replaced with a printed note.
   - The "two coupled boundaries name different directories" error is gone:
     with an EM run bound every entry gets the same value, and with none bound
     the coupling check in §3.2 already fails the build.
   - Leaves are injected as the bare job name (`omega3p_results`), the form
     Phase 0 ran, not `./omega3p_results`.
3. **`MeshDump.EMMeshInputDir`** is set to the *base* EM job:
   - the bound EM producer itself, if that producer's mesh did not come from a
     `tem3p`;
   - otherwise, recursively, that `tem3p`'s own base.

   This makes appended pairs (use case B) deform the original vacuum mesh
   (`orig + u₂`), not the already-deformed one (`orig + u₁ + u₂`).
   **Confirmed by P4:** TEM3P accepts `EMMeshInputDir ≠ Directory`.
   - With the original mesh, the second pass gives −7240.4 Hz against
     −7172.1 Hz for the first, a 0.95% change.
   - With the deformed mesh the result is −14412.2 Hz: the deformation
     compounds, which is exactly the hazard.
4. **`Omega3PId` guard.** If this `tem3p` provides a `MESH` that something
   consumes, and no `LFDetuning` or `Neumann` boundary in `ElasticProblem`
   sets `Omega3PId`, raise an error: the mesh would come out undeformed.
   **Confirmed by P3:** without `Omega3PId`, TEM3P exits 0, writes the mesh,
   and Δf ≈ 2e-6 Hz.
5. **Lint (build time).** These produce a `WorkflowValidationError` naming the
   correct spelling:
   - `NeumanValue` → `NeumannValue`;
   - `AbsoluteTolerante` / `AbsoluteTolerance` → "not read; use `Tolerance`";
   - `Abs tolerance` / `Rel tolerance` → `AbsTolerance` / `RelTolerance`;
   - a `MixedType` that is not exactly three upper-case entries from
     {`DIRICHLET`, `NEUMANN`};
   - `LFDetuning` without `Method`;
   - `Method: Gradient` without `StartPoint` / `EndPoint`.

   **P5 confirmed** that `NeumanValue` is silently ignored: a 1e5 Pa load
   spelled that way leaves `ResultFile` identical to the unloaded run.
   **Decided 2026-11-12 (user): every item is a build-time error, including
   an ignored value that equals the default (`NeumanValue: 0.`).** Each
   message says the key is not one TEM3P reads, that TEM3P ignores it
   silently, and names the fix: for example, "`NeumanValue` is not a key
   TEM3P reads (it is ignored without a warning); spell it `NeumannValue`".
   The CW23 inputs carry these spellings verbatim, so the shipped example's
   inputs use the corrected spellings.

### 3.5 Outputs

`Tem3P.output_parser` reads `ResultFile` and `tem3p.log`.

- **Read `ResultFile` from the last header onward.** It is opened in append
  mode (P2: a re-run gives two identical blocks; `tem3p.log` is overwritten),
  so the module's `clean` must also remove it before a fresh run.
- **The elastic table** is read with its own header splitter, not
  `parse_column_file`: the header names columns with embedded spaces
  (`u_x min [m]`), so a whitespace split cannot recover them. `u_x min [m]`
  becomes `u_x_min`, `Frequency [Hz]` becomes `frequency`. The thermal block is
  parsed by prefix (`Max T =`, `P (SideSet n) =`).
- **The elastic row is one global summary per solve, not one row per block.**
  `block_id` is the block holding the largest |u| (`ElasticProblem.C`), so the
  displacement quantities need no `at:` axis.
- **The RfGun layout** puts the elastic header before the thermal block and
  its rows after it. Fixtures cover it.

**Quantities (`QUANTITIES`, for bare routing):**

| Name | Source | Axis / `at:` |
|---|---|---|
| `max_temperature`, `min_temperature` | thermal block | — |
| `sideset_power` | `P (SideSet n)` | `at: {sideset: n}` (required) |
| `u_x_min` … `u_z_max`, `u_mag_max` | elastic table | — (global; `block_id` names where \|u\| peaks) |
| `mechanical_frequency` | eigen table. The log's `Mode n, Frequency:` lines agree with it (P6); only ARPACK's own `Frequency:` lines are off by c | `field_index` = `Mode` |
| `scaling_factor` | `The (LF )?scaling factor is` (log), per problem. Printed once per coupled boundary or heat source (5× in SrfCoupler); RfGun has one thermal and one LF factor. Default: the single problem's first value; `at: {problem: thermal\|elastic}` otherwise | — |
| `newton_iterations`, `n_dofs` | log, per problem (`n_dofs` skips the vacuum-mesh DOF lines); same default as `scaling_factor` | — |
| `run_time` | log | — |

- **Ignore the log's stress and strain min/max.** They are uninitialized
  sentinels.
- **`field()` is deferred.** The `.mod` files are viewer formats, and no
  consumer exists yet.
- **`verify`** checks that `ResultFile` exists and the log has its
  `Ending run at` line.

**Δf and the detuning coefficient come from `derived_parameters`.** `df` is
`omega3p_deformed`'s frequency minus `omega3p`'s.

- *Gap:* derived expressions read outputs only, so `k_l = df / Eacc²` needs
  `Eacc` written as a constant. That is wrong under a gradient sweep.
- **Decided 2026-11-12 (user):** derived expressions read swept *inputs* by
  label as well as outputs, so they can use any quantity a row carries. This
  goes in at Phase 3. It widens what names an expression can see; it does not
  add functions to the grammar.

### 3.6 Launch and resources

- `tasks`/`cores`/`opts` work as for the other solvers. TEM3P is pure MPI, so
  `cores: 1`.
- The docs warn that each rank holds the full vacuum mesh and fields. For
  sweep worker packing, `--mem-per-cpu` must cover that.
- **Phase 0 peak memory per rank** (sacct `MaxRSS`):

  | Case | Peak per rank |
  |---|---|
  | SRFCell | 295 MB |
  | RfGun | 552 MB |
  | TeslaCavity | 1.52 GB |
  | SrfCoupler (96 ranks) | 392 MB |

  The docs should say about 0.3–1.5 GB per rank at 16 ranks, and that an
  RF-coupled run does not shrink per-rank memory by adding ranks.
- **Exit code.** `tem3p` with a bad argument count exits 0 (confirmed), so
  `verify` must check the outputs, not the return code.

### 3.7 Relocated workdirs

`postprocess.in` carries an absolute mesh path, so a TEM3P re-run after its
evaluation directory moved would read a stale path. The resume path re-parses
results and does not re-run TEM3P, so it is unaffected. Re-running a moved
evaluation needs both the EM step and TEM3P re-run, and the EM hash already
forces that when the directory changes. **P9:** after a move, TEM3P dies with
`MPI_ABORT … errorcode -12313` (rc 137) right after
`loading RF solution for TEM3P...`, naming neither the path nor the reason.

So the module checks the condition itself before launching TEM3P. It reads
`<EM dir>/VECTOR/postprocess.in`, and if the `ModelInfo.File` named there
does not exist, it raises an error that names the file and says to re-run
the EM step.

## 4. Phases

### Phase 0: probe runs and fixtures

All probes run in one milano job, sequentially, under
`/sdf/scratch/users/d/dbizzoze/tem3p_probe/`, on Rocky 9.

| Probe | What | Decides |
|---|---|---|
| P1 | SRFCell chain verbatim (omega3p → tem3p → omega3p) | Δf against the archived −7172 Hz; 2026 output layout |
| P2 | Re-run SRFCell tem3p into the same job directory | `ResultFile` append behaviour |
| P3 | SRFCell with `Omega3PId` removed | Whether the deformed mesh comes out silently undeformed (§3.4 item 4) |
| P4 | Appended pair: fields from `omega3p_results_deformed`, with `EMMeshInputDir` = original, against `EMMeshInputDir` = deformed | Whether `EMMeshInputDir ≠ Directory` works and is needed (§3.4 item 3); how much the second-pass Δf moves |
| P5 | Neumann load spelled `NeumanValue` against `NeumannValue` | That the misspelling is silently ignored |
| P6 | TeslaCavity eigen | Eigen table, log `Mode` lines, file names |
| P7 | RfGun chain | Thermo-elastic `ResultFile` layout; Δf against the archived +47.4 kHz |
| P8 | SrfCoupler (s3p → tem3p, nonlinear) | Newton log; S3P coupling |
| P9 | Move the SRFCell directory, re-run tem3p | Relocation failure mode |

**Fixtures.** Freeze them into `tests/fixtures/tem3p/<case>/`, keeping text
outputs only and the `ResultFile`/log excerpts, with a `SOURCES.md`.
Phase 0 also records `maxRSS` per step.

**Phase 0 results (DONE 2026-11-12, job 44528190, 14 min).** All steps
except P9 exited 0. The fixtures are frozen in `tests/fixtures/tem3p/`; see
its `SOURCES.md`.

| Probe | Result |
|---|---|
| P1 | Δf = −7172.1 Hz, the archive value to 0.01 Hz. The 2026 output layout is the same as 2023's. |
| P2 | `ResultFile` appends; `tem3p.log` is overwritten. |
| P3 | Without `Omega3PId` the mesh is silently undeformed (Δf ≈ 2e-6 Hz). The guard is needed. |
| P4 | `EMMeshInputDir ≠ Directory` works. The second pass gives −7240.4 Hz (+0.95%); deforming the deformed mesh gives −14412.2 Hz, which compounds. |
| P5 | `NeumanValue` is silently ignored. |
| P6 | The eigen table and the log's `Mode` lines agree; per-mode file names use the `_Mode%03d_Freq%.2fHz` pattern. |
| P7 | Δf = +47427.3 Hz, the archive value. The thermo-elastic interleaving is as described in §2. |
| P8 | 9 Newton iterations; Max T = 324.352 K. Took 110 s at 96 ranks. |
| P9 | After the move, the step aborts with `MPI_ABORT -12313` (rc 137) and no message. The pre-flight check is in §3.7. |

**Environment note.** S3DF's slurmd upgrade (2026-11-12) left
`MpiDefault` unset, so the probes ran with `SLURM_MPI_TYPE=pmix`. This is
transient and needs no repo change (user).

### Phase 1: wrapper and parsers

- `Tem3P(ACE3P)` in `ace3p.py`.
- `parse_tem3p_results(job_dir)` returning `{thermal, elastic, eigen, log}`.
- The new kind `TEM3P_SOLUTION`.
- Characterization tests in `tests/test_tem3p_fixtures.py`.

**Phase 1 results (DONE 2026-11-12).**

- `Tem3P(ACE3P)` and three parsers in `ace3p.py`:
  - `parse_tem3p_result_file` reads the **last** run of `ResultFile`. A
    second thermal block or a second `#` header starts a new run, and `runs`
    counts them;
  - `parse_tem3p_log` reads per-problem `n_dofs`, `scaling_factors`,
    `newton_iterations` and `linear_iterations`, plus `modes`, `run_time`,
    `em_directory`, `em_mesh_input_dir` and `em_loaded`;
  - `parse_tem3p_results(job_dir)` returns
    `{thermal, elastic, eigen, units, runs, log}`. It raises when the log or
    `ResultFile` is missing or the log has no `Ending run at`. A log that
    stops while loading the EM run (P9) gets a "re-run the EM step" hint.
- `TEM3P_SOLUTION` is in `ARTIFACT_KINDS`.
- `tests/test_tem3p_fixtures.py` covers every Phase 0 probe.
- Found on the way, folded into §3.5: the elastic row is global, not one row
  per block; the header needs its own splitter; the scaling factor and DOFs
  are per problem. The `loading RF solution for TEM3P...` line is printed even
  without RF coupling, so it is no evidence of an EM load; the
  `Omega3P directory name:` line is.
- Known limit: an elastic-only run appended after a thermal-only one reads as
  one run. `clean` removing `ResultFile` (Phase 2) makes that unreachable.

### Phase 2: the module

- `optional_requires` in `Module` and `_bind`.
- `Tem3PModule`: the injections in §3.4, the build-time checks and lint, plus
  `extract`, `field_index`, `verify` and `clean`.
- Registry and `_ACE3P_TYPES`.
- A dry-run path that writes the injected leaves to the marker file.

Tests:

- extend `test_registry_edges_match_plan`;
- binding tests (optional bound, unbound, ambiguous);
- each lint and guard;
- dry run.

**Phase 2 results (DONE 2026-11-12).**

- `Module.optional_requires` and a generic `Module.check_build(modules,
  inputs)` hook. `Workflow` runs every module's `check_build` after binding and
  raises **one** error listing every problem, so a user fixes all of them in
  one pass. The ambiguity error no longer suggests the `from:` shorthand to a
  module that reads several kinds (the shorthand is refused there).
- `Tem3PModule` in `modules.py`, registered, in `_ACE3P_TYPES`, with bare
  routing for its quantities (not `run_time` or `n_dofs`, which are generic)
  and for `at: {sideset:}` / `at: {problem:}`.
- Lint (§3.4 item 5, all errors per the user's decision), plus one item found
  in the source: a `MeshDump` switch (`WriteDeformedEMMesh` and the other two)
  is on only for the exact string `on`, so `On` is reported.
- The `Omega3PId` guard is an error when a module consumes the mesh and a
  warning when the mesh is written but nothing reads it.
- `MeshFile` is checked against a `cubit` producer's export (or its `.ncdf`
  under `meshconvert`) and a `mesh` source's basename. A body mesh bound to a
  `tem3p` is an error.
- The §3.7 pre-flight is in: before a fresh run, each EM directory TEM3P reads
  must have a `VECTOR/postprocess.in` whose `ModelInfo.File` exists.
- A fresh run that should write `DeformedVacuumMesh.ncdf` and did not fails.
  `verify` checks `ResultFile`, the log's `Ending run at` and that mesh.
  `clean` removes the job directory, which is what keeps `ResultFile` to one
  run.
- `field()` is not implemented (deferred, §3.5).
- Tests: `tests/test_tem3p_module.py`, including real runs through a fake
  `tem3p` that replays the fixtures.

### Phase 3: mesh hand-off and the shipped example

- The `mesh_output` / `ModelInfo.File` injection in `_SolverModule`.
- Disk-collision validation extended to mesh producers.
- `examples/tem3p_lfd/`:
  - the SRFCell geometry, using CW23 journals and `.sat` (public material);
  - two Cubit journals → omega3p → tem3p → omega3p_deformed;
  - one shared omega3p input;
  - a small `TargetGradient` or geometry sweep;
  - `df` and `k_l` columns;
  - s3df and perlmutter batch files.
- A baseline in `tests/baseline/`.
- Validation on milano, recorded in `plans/s3df_example_validation_plan.md`.

**Phase 3 results (DONE 2026-11-12).**

- **Mesh hand-off.** `Module.mesh_output` (only `Tem3PModule` sets it) and
  `_SolverModule._mesh_leaf = ('ModelInfo', 'File')`, which is `None` on
  Track3P and TEM3P. `_inject_mesh` runs after the `ace3p:` merge and before
  `_prepare_solver`, so a resume injects too. A value that already names the
  mesh (`normpath` equality) is kept; any other is replaced, with a printed
  note. A dry run writes `Omega3P ModelInfo.File: ./tem3p_results/…` to the
  marker.
- **Disk collisions.** Solvers of *different* types may no longer share a job
  directory: each one's `clean` removes its own, and tem3p's mesh lives in
  its. Mesh files at the workdir's top level must be unique across producers:
  a cubit export, its meshconvert `.ncdf` (`a.gen` and `a.exo` both give
  `a.ncdf`), and a `mesh` source's basename. Two `mesh` sources may stage
  the same file.
- **Derived expressions read inputs** (`WorkflowInputs.named_values`). An
  input is readable by its axis label, its qualified label, and its bare
  name when unique. VOCS variables are known names at build time. Outputs
  and derived names shadow inputs. A bare name several inputs share is a
  build error naming the labels to use. The `python:` hatch still gets the
  outputs only, per the grammar policy. Hashes do not move: `canonical()` is
  unchanged.
- **`examples/tem3p_lfd`.** The CW23 `.sat` and journals (converted to LF),
  the CW23 `SRFCell.omega3p` shared by both solves, and `SRFCell.tem3p` with
  the corrected spellings and the LFDetuning boundary first. The YAML sweeps
  `TargetGradient` over 15 / 25 / 35 MV/m through a scoped
  `ace3p: tem3p:` block, and gives `df` and `k_l` columns. Baseline in
  `tests/baseline/tem3p_lfd/` (table plus marker).
- **Validation, job 44548443** (details in
  `plans/s3df_example_validation_plan.md`):
  - 4m17s, no NaNs;
  - `k_l` = −5.8310 Hz/(MV/m)² at all three gradients (constant to 1e-5);
  - `df` at 35 MV/m = −7143.0 Hz, 0.4% from Phase 0's −7172.1 Hz, because
    Cubit regenerates the meshes slightly differently from CW23's shipped
    ones.

### Phase 4: the other problem types

Fixture-driven tests:

- RfGun (thermo-elastic, Robin cooling) and SrfCoupler (s3p upstream,
  `functions/` staged via `files:`, nonlinear);
- the Tesla eigen axis (`Mode`).

No new examples unless asked.

**Phase 4 results (DONE 2026-11-12).**

- **`files:` takes a directory.** Every CW23 TEM3P case names its k(T) and
  Rs(T) tables `functions/<name>`, and `files:` staged by basename, so those
  paths could not resolve. A solver's `files:` entry may now be a directory,
  staged whole under its own name (`_stage_dir`): `files: [functions]` gives
  `<workdir>/functions/`, and the CW23 input runs as written. `stage_mode`
  applies (a symlink links the directory, a hardlink links each file). A fresh
  run re-stages a copied tree. A directory with no name of its own (`.`,
  `..`) is refused, since its "basename" would be the workdir itself.
  `check_files` accepts an existing directory.
- **Table check** (`_tem3p_unstaged_tables`, in `check_build`): a
  `Function`, `SurfaceResistance`, `SigmaFunction`,
  `DielectricConstantFunction{E,H}` or `ExtVHeatingFile` leaf with a
  non-numeric value must be put in the working directory by `files:`. A
  `dir/name` path needs a `files:` directory `dir` holding `name`; a bare
  name needs a `files:` entry of that basename. One error per missing
  directory (the CW23 SrfCoupler input names ten tables in one). Without
  the table TEM3P is not silent: job 44552664 removed `functions/NbTi` and
  got `File functions/NbTi not found. TEM3P FATAL ERROR` (rc 137), but on
  srun's stdout only, not in `tem3p.log`. The check saves the launch and the
  search.
- **Real run** (job 44552664, throwaway config in
  `/sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/tem3p_p4/`, not an
  example): `mesh → s3p → tem3p` on the CW23 SrfCoupler meshes, 96 ranks,
  `files: [functions]` under `stage_mode: symlink`, 2m12s. `t_max`
  324.352 K, 9 Newton iterations, scaling factor 437.5, sideset 4 power
  0.781339; every `ResultFile` data line equals P8's. The input had the
  ignored tolerance spellings deleted rather than corrected, so it ran P8's
  effective configuration.
- **Reordered boundary** (the open item from the Phase 3 validation): job
  44551379 ran the CW23 SRFCell input with only the LFDetuning block moved
  first, and the example's `SRFCell.tem3p` verbatim, on the CW23 meshes.
  Both `ResultFile`s and `DeformedVacuumMesh.ncdf`s are byte-identical to
  P1's, and the deformed frequency is 1300950556.377 Hz in all three. Order
  does not matter; the example's 0.4% offset is all Cubit remeshing.
- Tests: `tests/test_tem3p_problem_types.py`. RfGun runs
  `omega3p → tem3p → omega3p_deformed` through fakes that replay both real
  `omega3p.out` files, giving the archived Δf = +47427.3 Hz as a derived
  column, with per-problem `scaling_factor`/`n_dofs` and both problems'
  injected `Directory`. SrfCoupler runs `s3p → tem3p` under all three stage
  modes, with the fake `tem3p` failing when a table is not in its working
  directory. TeslaCavity sweeps a body-mesh parameter into a table long over
  `Mode` (10 rows for 2 points × 5 modes; a narrowed output repeats down the
  rows; all-narrowed stays wide), and a resume re-parses to keep the axis.

### Phase 5: docs

- `docs/tem3p_reference.md`, in the style of `track3p_reference.md`: use cases,
  injections, the "append pairs for convergence" recipe, the lint list,
  quantities and memory.
- The `docs/yaml_reference.md` module table and a `### tem3p module` section.
- `docs/index.md`.
- `CHANGELOG`.
- The `mesh deform` and `postprocess project` notes in `acdtool.py`.

**Phase 5 results (DONE 2026-11-12).**

- `docs/tem3p_reference.md`: the chain, which problems run, what a run
  writes, how TEM3P reads the EM run (and the moved-workdir abort), what the
  module sets, the deformed vacuum mesh, the appended-pair recipe with the P4
  numbers, the build-time check table, quantities, and memory per rank.
- `docs/yaml_reference.md`: a `tem3p` row in the module table, a
  `### tem3p module` section (anchor `tem3p-module`), `tem3p` in the bare
  routing list, `.tem3p` in the `input_parameters.ace3p` list. The "Several
  instances" bullets now state the Phase 3 rules: no two solvers share a job
  directory whatever their types, and mesh files must not collide across
  producers.
- `docs/index.md` (a "Running TEM3P?" pointer and the toctree), the README
  module list, and `CHANGELOG.md` `[Unreleased]` covering Phases 1 to 5.
- `acdtool.py` and `docs/acdtool_reference.md`: the `mesh deform` note no
  longer says TEM3P's `MeshDeformScale` writes the mesh (P0 found it is not
  read). The "TEM3P out of scope" paragraph is replaced by a
  `postprocess project` note.
- Found on the way: the `parse_tem3p_results` docstring broke a literal
  across its summary line, a Sphinx warning; fixed. The docs build has one
  warning left, in `surrogate_jax`, which predates this plan.

## 5. Open decisions

1. ~~Lint as error or warning~~ **Error**, with a message naming the fix
   (user, 2026-11-12; §3.4 item 5).
2. ~~Derived expressions reading swept inputs~~ **Yes**: any quantity a row
   carries, swept inputs included (user, 2026-11-12; §3.5). Lands in Phase 3.
3. ~~Whether appended pairs are supported~~ **Yes** (P4, 2026-11-12), with
   `EMMeshInputDir` resolved to the base EM run as in §3.4 item 3.

## 6. Follow-ups

1. **One swept TEM3P leaf for every pass of an appended chain.** A scoped
   `ace3p: tem3p: …` sweep reaches one module. A second scope for `tem3p_2`
   is a second axis (a tensor product), and an unscoped block is also
   appended to the Omega3P inputs. So an appended chain today sweeps Cubit
   inputs (which reach both passes) or keeps TEM3P's leaves fixed. Fixing
   it needs a way to give several scopes one axis, which is a
   multi-instance input feature rather than a TEM3P one. Documented as a
   warning in `docs/tem3p_reference.md`. **Decided 2026-11-12 (user):** the
   limitation stays documented and nothing more is built now. Other
   optimization strategies for chained loops like this are future work, to
   be investigated in their own plan.
