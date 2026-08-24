# Evaluation Isolation + Resume — Implementation Plan

**Status: PHASES 1–3 COMPLETE** (2026-08-24); Phase 4 planned. Four phases.
Phases 1–2 change no behavior and must move no baseline; Phases 3–4 add resume.

This plan does **not** add concurrent evaluation. It removes the three things
that make concurrency unsafe, and delivers resume — which needs the same three
things and is worth more on its own. `max_concurrent` becomes a small follow-on
change against the seams this plan establishes.

Prerequisites, both landed: `plans/t3p_monitor_plan.md` (T3P `monitors()` is
used by Phase 3) and `d2467cd` (`results_dir` reaches the solver command line,
so a resume check looks in the directory the run actually wrote).

---

## Motivation

### One: a sweep point lost to the wall clock is lost entirely

`examples/t3p_sweep/run_lume-ace3p_t3p_sweep_perlmutter.batch` says it outright:

> LUME-ACE3P does not orchestrate T3P restarts, so a sweep point cut off by the
> wall clock restarts from scratch; size this for the whole sweep.

A 2-hour allocation that dies at 90% loses all of it. This is the most expensive
missing feature in the package, and it costs cluster hours every time it bites.

### Two: per-evaluation state lives on shared objects

Three places, all of which happen to be correct *only because the sweep loop is
serial*:

1. **`Workflow` stashes the run context on itself.**
   [`evaluate`](../src/lume_ace3p/workflow_graph.py) sets `self.workdir` and
   `self.last_context`; [`field()`](../src/lume_ace3p/workflow_graph.py) and
   `field_index()` take no argument and read `self.last_context`.

2. **The mode layer reads that state after the fact.**
   `_persist_field(workflow, i)` calls `workflow.field()`; `_rows_for_point` and
   `_frame` both call `workflow.field_index()`. `_frame` calls it *after the
   whole loop*, so the long-format column label comes from whichever point ran
   last.

3. **Module instances are reused across evaluations and hold run state.**
   `_solver`, `_acdtool`, `_filtered`, `_cubit`, `geant4_obj` — and `extract()`
   reads them, after `run()` set them.

Plus one direct mutation: `collect_training_data` assigns
`workflow.baseworkdir = sample_dir` inside its loop and restores it in a
`finally`.

None of this is a bug today. All of it is a bug the moment two evaluations
overlap, and the failure mode is **wrong data, not a crash** — row *i* gets row
*j*'s field artifact.

### Three: there is already a resume precedent — generalize it, don't invent one

`collect_training_data` resumes:

```python
if os.path.isfile(field_path):
    handle = field_path          # already persisted; skip this β
else:
    ...
    workflow.evaluate(overrides)
```

That idiom — presence of a persisted artifact means "done" — is the right
instinct and the wrong mechanism to generalize (see design decision 3). Phase 3
replaces it with something exact and applies it to the table modes too.

---

## Target design

### The context becomes the carrier

```python
outputs, ctx = workflow.evaluate(scalars, workdir=...)   # returns both
workflow.field(ctx)                                     # explicit
workflow.field_index(ctx)
```

`last_context` stays, assigned on every `evaluate`, and `field()`/`field_index()`
keep working with no argument by falling back to it. That is not politeness —
`tests/test_workflow_graph.py` reads `wf.last_context` in a dozen places and
`wf.field_index()` with no argument in several. Keeping the zero-argument form
is what makes Phase 1 a no-baseline-movement change.

### The completion manifest

One JSON file per evaluation workdir, `lume_ace3p_state.json`:

```json
{
  "schema": 1,
  "point": {"index": 7, "axes": {"cav_radius": 100.0, "ellipticity": 0.75}},
  "config_hash": "sha256:1f3a…",
  "modules": [
    {"name": "cubit",   "type": "cubit",   "status": "complete",
     "artifacts": {"mesh": "pillbox-rtop4.gen"}},
    {"name": "omega3p", "type": "omega3p", "status": "complete",
     "job_name": "omega3p_results"},
    {"name": "acdtool", "type": "acdtool", "status": "failed",
     "error": "acdtool command exited 1"}
  ],
  "outputs": {"R/Q": 108.4, "Mode_freq": 1313756106.86}
}
```

Written incrementally — after each module, not once at the end — because the
partial state is the whole point.

### Design decisions

1. **A resumed module re-runs its parser and skips only the subprocess.** Not
   "skip the module entirely." Uniform, cheap, and it makes the long-format and
   field-artifact paths work with no special cases: `field_index` needs the
   parsed solver output, so a resumed S3P point must still have
   `output_parser()` run to know its frequency axis. Concretely, the solver
   wrappers already separate the subprocess call from `output_parser()`, so this
   is a branch, not a restructure.

2. **The manifest is authoritative for "did it run"; the module is authoritative
   for "is the output still there".** `Module.verify(ctx)` returns
   `True`/`False`/`None` (unknown, the default). Manifest says complete but
   `verify` says False → warn, re-run. This catches a workdir whose results were
   deleted or truncated.

3. **File presence alone must not decide completion.** ⚠️ This is the decisive
   argument for the manifest, and it is specific to this codebase.
   `acdtool postprocess transwake` writes its result *over*
   `<jobname>/OUTPUT/wakefield.out` — the file `T3PModule` already wrote and
   parsed (`Command.mutates`, and defect 7 of `plans/acdtool_rework_plan.md`).
   A presence check would find `wakefield.out`, declare the acdtool step
   complete, skip it, and report T3P's **longitudinal** wake as a kick factor.
   That is defect 7 reintroduced by the resume feature. Only a record that
   acdtool *ran* distinguishes the two states.

4. **`config_hash` covers the resolved per-point configuration, not the YAML
   text.** Hash the module entries (`type` + config mapping), the materialized
   input point, and the `output_parameters` spec. Deliberately excluded:
   `paths` (site-specific — the same workdir must resume on a different
   machine), `dry_run`, `workdir`, and comments. A changed hash means re-run the
   point and say why.

5. **`workdir_mode: indexed`** is added along`manual`/`auto`, naming points
   `<base>_0`, `<base>_1`, …. `auto` names by swept scalar values, which is
   *usually* unique but can collide (two axes rendering to the same string) and
   grows unboundedly long. Resume needs a stable, collision-free point identity;
   `indexed` provides it. `auto` and `manual` keep working, and resume under
   `manual` is refused with an error naming the reason — one shared workdir
   cannot carry per-point state.

6. **Row assembly is decoupled from execution order.** `parameter_sweep`
   collects into a list indexed by point, then flattens once, so the frame is
   identical whether points ran in order, out of order, or were resumed. Without
   this, enabling concurrency later silently makes every baseline
   nondeterministic.

7. **Manifests are not baseline artifacts.** They carry timestamps and absolute
   paths. Exclude them from baseline comparison explicitly in
   `tests/baseline_utils.py` rather than relying on them not being picked up.

8. **Resume is opt-in** (`mode: {resume: true}`, default `false`). A silently
   resuming sweep that picks up a stale workdir from a different study is worse
   than no resume at all.

9. **`ctx` owns the live module instances; `Workflow.modules` stays the
   never-run prototype list.** The consumers divide cleanly, so this needs no
   caller outside `workflow_graph.py` to change:

   | Needs | Sites |
   |---|---|
   | **config only** — prototypes fine | `self.module_types`; `modes.py` `_require_fixed_bin_edges` / `_geant4_input_path` / `_require_fixed_mesh` / `_particle_params`; `output_modules()` (only ever reads `m.type`); `test_workflow_graph.py` DAG-order assertions |
   | **live state** | the run loop, `field()`, `field_index()`, `_route_output` → `extract` — all four inside `workflow_graph.py` |

   `RunContext` is already the per-evaluation carrier (`artifacts`, `outputs`,
   `job_names`, `reparse`), so `ctx.modules` is that same pattern rather than a
   new one. Prototypes are still built at construction, which is what keeps a
   bad command failing in `Workflow(...)` — `AcdtoolModule.__init__` calls
   `_resolve_command()`, which raises on an unknown or unwired command.

   ⚠️ **`_route_output` must select from `ctx.modules`, never `self.modules`.**
   This is load-bearing because the failure is silent: a prototype's `extract`
   with `_solver is None` returns the *dry-run sentinel* — `float('nan')` from
   `Omega3PModule`, `np.array([nan])` from S3P/T3P — so a mis-resolution yields
   NaN rather than an exception, and a dry-run-heavy suite would not catch it.
   Sourcing only from `ctx.modules` makes reaching a prototype structurally
   impossible.

   Keep the name `self.modules`: renaming touches three tests and four
   `modes.py` sites and buys no safety the line above does not already buy.
   Instead make **"prototypes are never run"** an explicit, checkable
   invariant — assert every prototype's `_solver` / `_acdtool` / `_filtered` is
   still `None` after a sweep.

   *Not chosen:* making modules stateless by moving `_solver` / `_acdtool` /
   `_filtered` / `_cubit` / `geant4_obj` into `ctx.state[module.name]`. That is
   the better end state — genuinely reentrant, no duplicated list — but it
   touches all seven modules across `run`/`extract`/`field`/`field_index`,
   rewriting the T3P-monitor and acdtool code that landed 2026-08-19/20 for no
   behavior change. `ctx.modules` does not preclude it; if module state keeps
   growing the migration is mechanical and `ctx` is already the right home.

10. **`AcdtoolModule._warned` must be hoisted to the `Workflow`.** It is a
    per-instance set kept explicitly "so a sweep of N points warns once per spec
    rather than N times" (`modules.py`), and per-evaluation rebuilds reset it —
    a 25-point sweep over a legacy positional spec would emit 25 deprecation
    warnings.

    **No existing test catches this.** `test_modules.py`'s warn-once test
    exercises the module directly rather than through `Workflow`, so it keeps
    passing; `test_workflow_graph.py`'s "examples raise no `DeprecationWarning`"
    test passes because acdtool Phase 6 migrated every shipped example off the
    positional form. It is a pure user-facing noise regression, visible only to
    someone with a legacy config.

    Fix: `Workflow` owns `self._warned_specs = set()` and `_build_modules()`
    shares it in — the dedup is per-*config*, not per-*run*:

    ```python
    for module in modules:
        if module.type == 'acdtool':
            # Shared across evaluations: warn once per spec per config, not per
            # point. See test_modules.py "...warns once per spec...".
            module._warned = self._warned_specs
    ```

    Threading a `warned=` parameter through `build_module` / `_build_entry` also
    works but adds a parameter to a generic factory for one module's benefit.
    Moving the warning up to `_route_output` so `extract` never warns is
    conceptually cleanest but breaks the premise of the direct-module test.

---

# Phase 1 — The context becomes the carrier — **COMPLETE** (2026-08-24)

No behavior change. This phase exists so that Phases 3–4, and concurrency after
them, have somewhere to stand.

### Approach

`src/lume_ace3p/workflow_graph.py`:

1. `evaluate(input_scalars=None, workdir=None)` returns `(outputs, ctx)`. An
   explicit `workdir` overrides `_getworkdir` — which is what lets
   `collect_training_data` stop mutating `workflow.baseworkdir`.
2. `field(ctx=None)` and `field_index(ctx=None)` take the context, falling back
   to `self.last_context` when omitted.
3. Keep setting `self.workdir` and `self.last_context`. Their docstrings should
   say they are a single-run convenience and not safe to read across
   overlapping evaluations.
4. Build the module list **per evaluation** into `ctx.modules` — see design
   decision 9 below, which resolves how the prototype and live lists coexist.

`src/lume_ace3p/modes.py` — update every call site:

| Site | Change |
|---|---|
| `single` | unpack `(outputs, ctx)`; pass `ctx` onward |
| `parameter_sweep` | same |
| `_persist_field(workflow, i)` | takes `ctx` |
| `_rows_for_point` | takes `ctx`, uses `field_index(ctx)` |
| `_frame` | takes the resolved index, not a fresh `field_index()` call |
| `collect_training_data` | `evaluate(overrides, workdir=sample_dir)`; drop the `baseworkdir` save/mutate/restore |
| `_objective_from_workflow` | unpack the tuple |
| `gp_parameter_sweep` | unpack the tuple |

### Verification (Phase 1 done when) — all met 2026-08-24

- [x] **Every frozen baseline is byte-identical.** Full registry (16 entries)
  re-frozen and diffed against HEAD: the only differing bytes are the
  `xopt_runtime` wall-clock column of `s3p_optimization/sim_output.txt` (dropped
  before comparison by design) and a *pre-existing* `docs/` → `plans/`
  provenance-string drift in `t3p_power_balance/manifest.json`, neither related
  to this phase. Every result column is identical; no fixture was re-cut.
- [x] `tests/test_workflow_graph.py` passes — but **not untouched**, and the plan
  was wrong to predict it would be. `evaluate` returning `(outputs, ctx)` is a
  tuple, so every `out = wf.evaluate(...)` call site had to unpack; and the two
  index-collision tests inject a parsed solver into a module *after* `evaluate`,
  which must now be `ctx.modules` rather than `wf.modules` (injecting into a
  prototype would be invisible, which is exactly decision 9 working). The
  back-compat that *did* hold as claimed is the zero-argument
  `field()` / `field_index()` and `wf.last_context` forms.
- [x] Every prototype in `wf.modules` still has `_solver` / `_acdtool` /
  `_filtered` / `_cubit` / `geant4_obj` `None` after a full sweep — the
  design-decision-9 invariant. Pinned over a `track3p_source → particles` chain
  rather than a solver chain: under dry-run the solver and acdtool modules park
  no state at all, so a solver sweep satisfies this vacuously, whereas
  `particles` does real work with no binary.
- [x] A sweep of N points over a **deprecated positional** acdtool output spec
  emits exactly **one** `DeprecationWarning`, not N (design decision 10).
- [x] Two `evaluate` calls with different inputs; each returned `ctx` still
  reports its own `artifacts`, `field()` and `field_index()` after the other has
  run. The property Phase 1 exists to create.
- [x] `collect_training_data` never assigns to `workflow.baseworkdir` — asserted
  against the parsed AST of `modes.py` over `baseworkdir` / `workdir_mode` /
  `workdir` / `last_context`, so it also catches a mutation on a path no test
  exercises.

Each of the four new properties was confirmed load-bearing by temporarily
reverting the mechanism behind it and watching the matching test fail.

### Deliverables — as built

`workflow_graph.py`, `modes.py`, `modules.py` (`RunContext.modules`),
`tests/test_evaluation_isolation.py` (new), and the five test doubles whose
`evaluate`/`field` signatures mirror the seam (`baseline_utils.py`,
`test_results.py`, `test_run_xopt_compat.py`, `test_surrogate.py`,
`test_surrogate_data.py`). No new YAML key, no baseline change.

One deviation from "no doc change": the `## The Workflow object` API section of
`docs/yaml_reference.md` documents `evaluate`'s return and the zero-argument
`field_index()` / `field()`, so it would have been left stating something false.
Prose only; the docs still build warning-free under `-W`.

---

# Phase 2 — Point identity and deterministic assembly — **COMPLETE** (2026-08-24)

Still no behavior change for existing configs.

### Approach

1. **`workdir_mode: indexed`** in `_getworkdir`, which currently raises on
   anything but `manual`/`auto`. Needs a point index, so `evaluate` accepts
   `point_index=None` (used only by this mode) or — cleaner — the mode layer
   passes the full `workdir=` and `indexed` is implemented there. **Prefer the
   explicit `workdir=`**: it keeps `Workflow` unaware of sweep indices, which is
   the decoupling the mode layer already has.
2. **Deterministic assembly.** `parameter_sweep` collects
   `results[i] = (scalars, outputs, ctx, handle)` into a pre-sized list, then
   builds rows in index order after the loop. Same frame as today, produced in a
   way that does not depend on execution order.
3. **Per-evaluation logs.** Capture each subprocess's stdout/stderr to
   `<workdir>/<module_name>.log` instead of inheriting the parent's streams.
   Touches `ace3p.py`, `acdtool.py`, `cubit.py`, `geant4.py`.

   ⚠️ Every one of these currently uses `subprocess.run(..., shell=True)` with
   no capture. Redirecting them changes what a user sees on the terminal during
   a run — the one *intentional* user-visible change in Phases 1–2. Keep it
   opt-out (`workflow_parameters: {capture_output: false}`) and say so in the
   docs, or teed rather than redirected. Do not let solver failure messages
   become invisible.

### Verification (Phase 2 done when) — all met 2026-08-24

- [x] **Baselines byte-identical again, including under `workdir_mode: auto`.**
  Full registry (16 entries) re-frozen and diffed against HEAD: the only
  differing bytes are the same two Phase 1 recorded — the `xopt_runtime`
  wall-clock column of `s3p_optimization/sim_output.txt` (dropped before
  comparison by design; every result column matches exactly) and the pre-existing
  `docs/` → `plans/` provenance-string drift in
  `t3p_power_balance/manifest.json`. `auto` is covered by
  `geant4_track3p_beta`, whose frozen digests are per-workdir
  (`lume-ace3p_geant4_workdir_40.0`).
- [x] A sweep run with `indexed` produces the same table as the same sweep under
  `auto`, modulo workdir names — `assert_frame_equal` over a `track3p_source →
  particles` β sweep, chosen because it yields real per-point-distinct numbers
  with no binary, so the comparison is not a vacuous NaN-to-NaN match.
- [x] A deliberately-failing module leaves its error in `<workdir>/<name>.log`
  and the failure still surfaces to the caller. Driven by a fake `cubit` on a
  `paths:` override — Cubit is the one wrapper whose command line carries no
  MPI-caller prefix, so it is the only one a real subprocess can be run through
  with no ACE3P environment. "Surfaces" is precise about what the code does:
  `run_logged` returns the child's exit status, and the message is on the
  parent's **stderr** as well as in the log; nothing above it raises on a nonzero
  exit, which is the wrappers' long-standing behavior (an ACE3P failure surfaces
  when the parser finds no results).
- [x] A test asserts row order is by point index, by building rows from a
  deliberately shuffled results list — for both the wide and the long-format
  (multi-row-per-point) shape.

Two deviations from the plan as written, both in the direction it flagged:

1. **The subprocess output is teed, not redirected** — the alternative the plan
   offered — so `capture_output: true` takes nothing away from the terminal.
   stdout and stderr stay on their own streams (`2>errors` keeps working) and
   both interleave into the one log. Streaming, not buffered-until-exit: a
   multi-hour solve going silent would be the same regression as redirecting it.
2. **`RunContext.capture_output` defaults to `False`** while the
   `workflow_parameters` key defaults to `True`. A hand-built context (every
   module unit test, any direct driver) therefore keeps the exact pre-Phase-2
   inherited-stream behavior, and it is `Workflow.evaluate` that opts in. Without
   this the six `monkeypatch.setattr('subprocess.run', ...)` sites in
   `test_modules.py` / `test_acdtool_fixtures.py` would silently stop
   intercepting, since the tee path goes through `Popen`.

### Deliverables — as built

`logs.py` (new: `run_logged` / `log_path`), `workflow_graph.py`
(`point_workdir`, `WORKDIR_MODES`, `capture_output`), `modes.py` (`_PointResult`
/ `_assemble` / `_evaluate_point`; `_rows_for_point` now takes the resolved index
rather than a `ctx`, which is also what keeps the per-point contexts from being
held for the length of the sweep), `modules.py`
(`RunContext.capture_output`, `Module.log_file`), the four wrapper modules
(`log_file=` keyword; the now-dead `subprocess` imports dropped from three of
them), `docs/yaml_reference.md` (`workdir_mode` subsection, `capture_output`
subsection, `point_workdir` on the Workflow API list),
`tests/test_evaluation_isolation.py` (sections 5–7), and the one
`_rows_for_point` call site in `tests/test_workflow_graph.py`.

No baseline change, and the `acdtool postprocess rf` template probe in
`Acdtool._generate_sample_input` is deliberately left on `DEVNULL`: it is a
best-effort query whose output is silenced on purpose, not a workflow step.

---

# Phase 3 — The completion manifest — **COMPLETE** (2026-08-24)

First phase to add a real feature. Still does not change any default behavior:
the manifest is written always, read only under Phase 4's `resume: true`.

### Approach

New `src/lume_ace3p/state.py` — small, no dependencies beyond `json`/`hashlib`:

```python
SCHEMA = 1
STATE_FILE = 'lume_ace3p_state.json'

def config_hash(entries, inputs, output_spec) -> str
def read_state(workdir) -> dict | None
def write_state(workdir, state) -> None
def record_module(state, module, status, **extra) -> None
```

In `Workflow.evaluate`: initialise the state, and after each `module.run(ctx)`
record `complete` (or `failed` plus the exception text, then re-raise). Record
the extracted `outputs` at the end. Write after every module, not once — the
partial file is the feature.

Add `Module.verify(ctx)` returning `True`/`False`/`None`, default `None`.
Implement it where it is cheap and unambiguous:

| Module | `verify` checks |
|---|---|
| `cubit` | the mesh artifact file exists |
| `omega3p` | `<results_dir>/omega3p.out` exists |
| `s3p` | `<results_dir>/Reflection.out` exists |
| `t3p` | every monitor `monitors()` declares wrote its file — reuses the T3P monitor work directly |
| `acdtool` | `resolve_output(jobname)` exists **and** `spec.mutates` is None; otherwise `None` (decision 3) |
| `particles` | the output particle file exists |
| `geant4` | the dose / edep files named by `_output_files()` exist |
| source modules | `True` — staging is idempotent |

### Verification (Phase 3 done when) — all met 2026-08-24

- [x] A completed single run leaves a manifest whose `modules` list matches the
  DAG order and whose `outputs` equal the returned dict. Pinned over a chain
  declared **out of** DAG order (`particles` listed before `track3p_source`), so
  the assertion is about the order the chain ran in rather than the order the YAML
  happened to use, and over a `particles` step, whose outputs are real numbers
  under dry run rather than the NaN sentinel a solver would give.
- [x] A run whose middle module raises leaves a manifest with that module
  `failed` and the later ones absent — which is also what pins that the file is
  written *incrementally*. A second test covers the module-0 case: the manifest
  exists, carrying the point and its `config_hash`, before anything has run.
- [x] `config_hash` is stable across two identical runs and changes when a module
  config, an input value, or an `output_parameters` entry changes — and does
  **not** change when `paths`, `dry_run`, or a YAML comment changes. One test per
  clause, seven in all; the `paths` / `dry_run` clauses hold *structurally* (they
  are not passed to `config_hash` at all) rather than by a filter that could rot.
  Two further tests pin the ordering rule the hash needs to be usable: an ACE3P
  `Section`'s entry order is significant (the input file's semantics depend on it)
  while a module entry's key order is not.
- [x] `verify` returns `False` for a workdir whose results directory was deleted —
  per solver, plus the partial case (a T3P run whose second monitor's file is
  missing is not complete), and the "cannot tell" cases: dry run, a Geant4 step
  whose output names live in an input file it has not read, a run declaring no
  readable monitor.
- [x] The acdtool `mutates` case returns `None` from `verify` — pinned by a test
  that names defect 7, so nobody later "improves" it into a presence check. Its
  companion asserts `postprocess rf` *does* verify from `rfpost.out`, so the
  distinction is visible as a distinction rather than as a blanket `None`.
- [x] Manifests are excluded from baseline comparison; baselines byte-identical.
  `resolve_one` drops the manifest even when asked for it by name, and a test
  asserts no registry pattern names it today either (guard, not repair). The full
  registry (16 entries) was re-frozen and diffed against HEAD: the only differing
  bytes are the same two Phases 1–2 recorded — the `xopt_runtime` wall-clock
  column of `s3p_optimization/sim_output.txt` (dropped before comparison by
  design; a zero-tolerance table comparison of every other column passes) and the
  pre-existing `docs/` → `plans/` provenance-string drift in
  `t3p_power_balance/manifest.json`.

Three deviations from the plan as written:

1. **The manifest records no point *index*.** The schema sketched above has
   `point: {index, axes}`, but `evaluate` takes no point index by Phase 2's
   decision — the mode layer owns sweep ordering and passes the full `workdir=` —
   and threading one in would mean adding an argument to `evaluate` that four test
   doubles' signatures do not have, for a field that is decorative: `axes` already
   identifies the point, and both resume and `--status` derive a point's workdir
   themselves. `point: {axes: {...}}` is what is written, from `sweep_axes()` in a
   sweep and from the materialized scalar knobs for a single/optimizer point. If
   Phase 4 wants the index it is one line, in the layer that knows it.
2. **`ACE3P.results_subdir` replaced `T3P.results_dir`.** `verify` has to name a
   solver's results directory *without a solver instance*, and T3P's `OUTPUT`
   subdirectory lived in an instance-method override. It is now a class attribute
   (empty for every solver but T3P) read by one `ACE3P.results_dir`, so the module
   layer asks the wrapper *class* and cannot drift from it. Same paths as before,
   and `ace3p.input_job_name` was added for the same reason: `_SolverModule`
   mirrors `job_name`'s resolution order (override → `JobName` leaf → default)
   rather than assuming the default.
3. **`docs/yaml_reference.md` gained a `run manifest` section**, though Phase 3
   adds no YAML key. A file appearing in every workdir of every run is
   user-visible whether or not it is configurable, and the alternative was for
   users to find an undocumented JSON file next to their results. Docs still build
   warning-free under `-W`.

### Deliverables — as built

`state.py` (new: `SCHEMA` / `STATE_FILE` / `config_hash` / `new_state` /
`record_module` / `record_outputs` / `read_state` / `write_state`, plus
`module_entry` and `relative` for Phase 4), `workflow_graph.py` (manifest writing
in `evaluate`, `_point_record`, `_module_record`), `modules.py` (`Module.verify`
plus seven implementations, `_SourceModule`, `_journal_export`,
`_SolverModule._results_dir`, `T3PModule._input_monitors`), `ace3p.py`
(`results_path`, `input_job_name`, `ACE3P.results_subdir`),
`tests/test_state.py` (new, 32 tests), `tests/baseline_utils.py`
(`BASELINE_EXCLUDED`), `docs/yaml_reference.md`, `CHANGELOG.md`.

Two implementation notes worth keeping:

* **`KeyboardInterrupt` is not recorded as `failed`.** `evaluate` catches
  `Exception`, so an interrupted module stays *absent* from the manifest. An
  interrupted step is not a broken one, and "absent" already means "not
  complete" — which is all Phase 4 needs — while `failed` is what gets reported
  to a user as something that went wrong.
* **The manifest is written atomically** (`os.replace` over a sibling temp file).
  It is rewritten after every module, so a truncated write is a real possibility,
  and a truncated manifest is precisely what a resume would read.

---

# Phase 4 — Resume in the table modes

### Approach

`mode: {type: parameter_sweep, resume: true}`. Per point, in order:

| Manifest state | Action |
|---|---|
| absent | run every module |
| `config_hash` differs | run every module; print the point and that the config changed |
| all modules `complete`, all `verify` pass | re-run parsers only (decision 1), rebuild the row |
| partial or any `failed` | run from the first non-complete module in DAG order; earlier modules parse only |
| `complete` but a `verify` fails | warn naming the module and the missing file, then re-run from it |

Threading this through means `module.run(ctx, skip_execution=False)`, or
equivalently a `ctx.resume_from` index the modules consult. **Prefer the
explicit parameter** — a module reading a context flag to decide whether to
launch a subprocess is the kind of implicit control flow this codebase has
otherwise avoided.

Refuse `resume: true` under `workdir_mode: manual`, with an error naming
`indexed` as the fix (decision 5).

⚠️ **Per-module identity is the module's `name`, and nothing enforces that it is
unique.** Phase 3 keys manifest entries on it, and Phase 2's logs key on it too
(`<workdir>/<name>.log`), but a chain listing two `acdtool` steps with no `name:`
key gives both the default name `acdtool` — so they already share one log and
would share one manifest entry. Resuming "from the first non-complete module"
needs the names to be distinct. Either reject duplicate names in
`_resolve_order` (a new error for a config that today runs, with two steps
overwriting each other's log) or make the manifest key `(name, position)`.
Decide it in Phase 4 rather than leaving it implicit.

Cross-check the recorded `outputs` against the re-extracted ones on a resumed
point and warn on mismatch. That is a free nondeterminism detector and it costs
one comparison.

Also add **inspection**, which is nearly free once the manifest exists:
`run-lume-ace3p --status <config.yaml>` walks the points a config implies,
reads each manifest, and prints a per-point status table. This is what makes a
half-finished campaign legible, and it is the seam an agentic driver will want
to poll.

Finally, migrate `collect_training_data` onto the shared mechanism, keeping the
`field.npz`-presence check as a recognised legacy state so existing training
stores are not invalidated.

### Verification (Phase 4 done when)

- A sweep interrupted after point 3 of 8, re-run with `resume: true`, produces a
  table identical to the uninterrupted run, and its logs show points 0–3 not
  re-executing.
- A sweep whose acdtool step is made to fail, then fixed and resumed, re-runs
  **only** acdtool — the mesh and solve are not repeated. Assert on the mesh
  file's mtime.
- A resumed `t3p` + `transwake` point re-runs the acdtool step (decision 3) and
  reports the same kick factor as the uninterrupted run. This is the test that
  matters most; without it the feature can silently regress defect 7.
- Long-format (S3P) resume produces the same row count and the same
  `Frequency` column as the uninterrupted run.
- `resume: true` + `workdir_mode: manual` raises, naming `indexed`.
- `--status` on a half-finished sweep prints the right counts.
- Baselines byte-identical with `resume` unset.

### Deliverables

`modes.py`, `modules.py`, `run_lume_ace3p.py` (`--status`), `docs/yaml_reference.md`,
`docs/parameter_sweep.md`, `tests/test_modes.py` additions.

---

## What this plan leaves for the follow-on

- **`max_concurrent`.** A `ThreadPoolExecutor` over the Phase-2 results list,
  plus the `srun --exclusive -N1` step form and a warning when
  `max_concurrent × tasks` exceeds `SLURM_NTASKS`. Small against these seams.
  Cubit license contention under concurrency is an open question — measure
  before designing around it.
- **Named artifacts** in `workflow_graph` (`provides: {mesh: vacuum}`), which
  unblocks TEM3P, `mesh deform`, and two instances of one solver in a workflow.
  Independent of everything here.
- **Track3P as a module.** Independent, but see the note below.
- **`describe()` + strict config-key rejection.** Deliberately after the above,
  so the mirror is written once against a settled surface.

## Adjacent bug worth fixing regardless — `particles.py` column positions

Not part of this plan; recorded so it is not lost.

[`Particles.load`](../src/lume_ace3p/particles.py) reads with
`header=None, names=TRACK3P_COLUMNS` — columns assigned **by position**, any
header line skipped as a comment. The shipped
`examples/assets/sample_track3p_particles.txt` and the real CW23
`track3p/Pillbox/2.3MV/ImpactsInfo_2.3e+07` are **both 17 columns with different
schemas**:

| Col | `TRACK3P_COLUMNS` | CW23 `ImpactsInfo` |
|---|---|---|
| 12 | `momentum_x` | `NumElectrons` |
| 16 | `InitialNormalField` | `FaceID` |
| 17 | `InitialFaceArea` | `volID` |

So a real Track3P dump would feed a face ID (`6`) into the Fowler–Nordheim field
term and a volume ID (`1`) into the emitter area — and produce numbers, not an
error. The Track3P reference documents `OutputImpacts: on/off` and no column
layout, so neither schema is documented and the CW23 file is the only ground
truth.

Fix: read the header row by name, raise on an unrecognised schema, freeze the
CW23 file as a fixture. Half a day, and it is a prerequisite for any Track3P
solver module.
