# Failure Mitigation — Implementation Plan

**Status: COMPLETE — Phases 1–5 done 2026-10-15, released as 0.8.0** (tag
`v0.8.0`, deployed on S3DF and Perlmutter). Written 2026-10-13 on S3DF against
`dev` at `1ce2cea` (0.7.1).

Follow-on to `plans/evaluation_isolation_resume_plan.md` (the completion
manifest and `resume`) and `plans/multi_instance_workflow_plan.md` (the
`config_hash` that must not move). Resume exists and works when the
configuration is unchanged; this plan is about what happens around it — a tool
that crashes, a workdir that already holds files, a configuration that changed
in part — and about letting a campaign be built up in stages on one set of
directories.

Every claim below about current behavior was verified 2026-10-13 by reading the
code at the cited lines and by probes against `lume-ace3p-dev` using the
fake-binary harness of `tests/test_resume.py` with failing variants of `cubit`
and `omega3p`. The probe script and its output are at
`/sdf/scratch/users/d/dbizzoze/failure_probe/` (not in the repo). Where a probe
and a docstring disagree, the probe wins and the disagreement is recorded.

---

## 1. The four questions, answered for the current code

### 1.1 A Cubit or ACE3P run crashes inside an evaluation

**A nonzero exit status is never treated as a failure.**
[`run_logged`](../src/lume_ace3p/logs.py#L64-L68) says so by design: "an ACE3P
failure surfaces when the output parser finds no results". Every wrapper
follows it — [`Cubit.run`](../src/lume_ace3p/cubit.py#L113-L126) discards the
return value of both the mesher and `meshconvert`,
[`ACE3P.run`](../src/lume_ace3p/ace3p.py#L372-L383) prints
`exit_status_note()` to stderr and carries on,
[`Geant4.run`](../src/lume_ace3p/geant4.py#L192-L198) and
[`Acdtool.run`](../src/lume_ace3p/acdtool.py#L1046-L1049) discard it.

That premise holds only when the crash leaves **nothing** behind. Probed:

| Scenario | Manifest | Table |
|---|---|---|
| Cubit exits 3, writes no mesh | `cubit: complete` — [`CubitModule.run`](../src/lume_ace3p/modules.py#L644-L654) records the journal's export name without checking the file exists | Solver ran against no mesh; no error raised |
| Cubit exits 3; a mesh from an earlier run of that point sits in the workdir | `cubit: complete`, `omega3p: complete` | **Row `radius=250` holds `f0=200`** — the old mesh's answer, no warning |
| Omega3P exits 7, writes nothing | `omega3p: complete` (see below) | `ValueError` from `extract`; sweep aborts |
| Omega3P writes its output, then exits 9 | `omega3p: complete` | Accepted; one stderr line |

The second row is the one that matters: **wrong data, silently.** Nothing in
the package removes a module's previous output before it runs again (the only
`os.remove` in `src/` is acdtool's sample cleanup), so a crash that writes
nothing leaves the previous run's files to be read as this run's.

**An extraction failure is not recorded.** The module loop in
[`evaluate`](../src/lume_ace3p/workflow_graph.py#L842-L863) catches an
exception from `module.run` and records `failed`; the extraction loop at
[L865–871](../src/lume_ace3p/workflow_graph.py#L865-L871) is outside that
protection. `Omega3P.output_parser` and `T3P.output_parser` deliberately do not
raise on a missing results file ("the module layer raises when a workflow
actually asks"), so a solver that produced nothing is recorded `complete`, and
the `ValueError` naming the missing file comes from `extract`. The manifest
then says every module is complete and `outputs` is `{}`; `--status` reports
the point **complete**. Resume recovered the probed case only because
`_SolverModule.verify` found `omega3p_results/omega3p.out` missing — the
manifest's own record was wrong. (S3P and Track3P parsers *do* raise, so those
land as `failed`; the record depends on which solver, which is the problem.)

**One failed point ends the sweep, and no table is written.** The exception
passes through [`parameter_sweep`](../src/lume_ace3p/modes.py#L289-L305)
untouched; `run_mode` writes `output_file`
[after the loop returns](../src/lume_ace3p/modes.py#L226-L234). A 32-point
sweep that fails at point 20 leaves 19 complete workdirs and no table. The docs
([troubleshooting.md](../docs/troubleshooting.md)) already tell users this and
point them at `resume`. In the Xopt modes `Xopt(strict=True)` is the default
(verified, `xopt 3.0.1`), so one raising evaluation ends the campaign;
`xopt_state.yml` is written after every step, so resume works there.

**Real-run evidence** (S3DF validation campaign under
`/sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/`, 1081 module logs): the only
"exited with status" lines are the EL9 `libgsl.so.23` failures (exit 127,
nothing ran) and one Track3P exit 255 — genuine failures both. No successful
solver run exited nonzero. Cubit, `meshconvert` and Geant4 do not print their
exit status, so their behavior on success is **unverified** (Phase 1 checks it).

### 1.2 Restarting a failed run with `resume: true`

Works as documented when the configuration is unchanged. Probed: a sweep
killed at point 1 by a solver crash, re-run with the binary fixed, launched
Cubit once per point in total and produced the correct table. The rules in
[`_reusable`](../src/lume_ace3p/workflow_graph.py#L909-L940) are sound.

Its weakness is inherited from 1.1: resume trusts `complete`, and `complete`
is recorded for a module whose tool crashed but left files. `verify()` is the
only backstop, and it checks presence, not validity.

### 1.3 Restarting after the YAML or an input file changed

There is **one hash per point**, over the whole chain
([`config_hash`](../src/lume_ace3p/workflow_graph.py#L811)). Probed:

- **Any YAML change** — `tasks: 4` added to the `omega3p` entry — made every
  point "written for a different configuration" and **re-ran Cubit** at every
  point. Correct, and wasteful: the mesh did not depend on what changed.
- **Changing the contents of an input file** (`cavity.omega3p`, the journal,
  an SEY table under `files:`) with the YAML untouched is **invisible**. The
  hash covers the module entry's *mapping* — the filename as a string — and
  the materialized `input_parameters`, never file contents. Resume skipped the
  solver and returned the old results. A second silent-staleness path.
- **A staged file is never re-staged.**
  [`_stage_file`](../src/lume_ace3p/modules.py#L342-L347) returns the existing
  workdir copy whenever one is present, so even a fresh (non-resume) run over
  an existing workdir reads the *old* copy of a `files:` entry or a
  `mesh: {file: …}`. A third path, independent of the hash.
- **Without `resume`**, a re-run over existing workdirs overwrites everything
  in place and says nothing — `_require_own_workdirs` warns about `manual`
  only.

### 1.4 A Cubit-only sweep, inspect the meshes, then the full workflow

Not possible today. The Cubit-only manifest hashes differently from the full
chain's, so the full run with `resume: true` printed "different configuration"
at every point and **rebuilt every mesh** (probed). `mesh: {file: …}` is one
fixed file for every point, so it cannot stand in for a swept Cubit step.

---

## 2. Target design

Five changes, in dependency order. Phases 1–2 close the silent-wrong-data
paths and are small. Phase 3 is the substantive one and is what answers 1.3
and 1.4 together. Phase 4 is independent of 3. Phase 5 is docs and release.

### 2.1 A nonzero exit is a failure (Phase 1)

Each wrapper raises on a nonzero exit, from `run()`, so the module loop
records `failed` with the exit status and command in `error`. The text is
`ACE3P.exit_status_note()`'s, which already names both causes ("the launcher
refused the step or the solver crashed") and the log to read.
`run_logged` itself stays as it is — it is a seam `test_evaluation_isolation.py`
tests directly, and "does not check" is the right contract for the function
that *reports* the status.

`CubitModule.run` additionally fails when the exported mesh named by the
journal does not exist after the run — the one case a clean exit does not
cover (a journal whose `export` line is wrong writes nothing and exits 0).

Extraction is brought inside the recorded region: a failure in the
`output_parameters` / `derived_parameters` loop is recorded on the manifest as
a top-level `"error"` and `point_status` reports such a point `failed`. The
module entries stay as they are (they *did* complete); what failed is the
point. Additive — `SCHEMA` stays 1, a reader without the key sees what it saw.

### 2.2 A module clears its own output before it runs fresh (Phase 2)

`Module.clean(ctx)` removes what *this module's tool writes* — and nothing
else — and `evaluate` calls it before any `run` that is not `skip_execution`
and not dry-run:

| Module | Removes |
|---|---|
| `cubit` | the journal's exported mesh, and the `meshconvert` output beside it |
| `omega3p` / `s3p` / `track3p` | `_written_results_dir()` |
| `t3p` | `_written_results_dir()` (the override is read-only for T3P — see `_written_results_dir`'s docstring) |
| `acdtool` | its `output_file`, **unless** `spec.mutates` — a mutating command's "output" is its producer's file, which it overwrites anyway |
| `geant4` | every name in `_output_files()` |
| `field_emission` | its reweighted particle file |
| source modules | nothing — they stage inputs |

Never the workdir, never `<name>.log` (which appends by design, with a `$`
header per invocation), never the manifest, never another module's files, and
never anything that resolves outside `ctx.workdir` (`state.relative()` already
knows how to ask). The guard is structural: `clean` is given the workdir and
builds paths inside it; it does not take a path from the caller.

`_stage_file` re-stages when the module is running fresh: the staged copy is
an input, not an output, so `clean` does not touch it — but a fresh run must
read the *current* source. Concretely, when `skip_execution` is false the
existing destination is removed before `_link_or_copy`. Under `symlink` /
`hardlink` modes the link already follows the source, so this changes only
`copy` mode in effect, but the rule is stated once for all three.

### 2.3 Per-module hashes, chained (Phase 3)

Each manifest module entry gains `"hash": "sha256:…"`, over:

1. the module's own entry (`type` + config mapping, as `config_hash`
   canonicalizes it today),
2. the **input buckets this module reads**, from `ctx.inputs.for_module(name)`
   — declared per class as `input_buckets`: `cubit → {cubit}`; the solvers
   → `{ace3p}`; `geant4 → {macro, particles}`; `field_emission →
   {particles, cubit}` (legacy β under `cubit`); `acdtool` and the sources
   → none,
3. the **contents** (sha256 of bytes) of every input file the module names —
   declared per class as `input_files()`: the journal; the solver input and
   `files:`; the rfpost input; the Geant4 input and `geant4_geometry_files`;
   a source's `file`,
4. the `hash` of each upstream producer it is bound to (`self.sources`).

(4) is what makes it a chain: a changed journal changes the Cubit hash, which
changes every downstream hash, with no rule needed to say so. (2) is per
bucket so an edit to a `particles` value does not invalidate a mesh. (3) is
what 1.3 was missing. `output_parameters` and `derived_parameters` are in
**no** module hash: changing what is extracted re-runs parsers only.

Resume then asks, per module, *is the recorded hash this hash, and does the
output verify?* — in place of the all-or-nothing `config_hash` gate. The
existing rule "once one module re-runs, everything after it re-runs" stays:
chaining covers a changed upstream, but not an upstream re-run for a
non-hash reason (its output was deleted), and the `transwake` case (design
decision 3 of the resume plan) needs exactly that.

**`config_hash` does not move.** It stays computed, recorded and pinned
(`tests/test_state.py` pins four shipped configs); its role narrows to the
point's identity line and the fallback below. The per-module `hash` is a new
field beside it.

**A manifest without module hashes** (written by ≤ 0.7.1) resumes by the old
rule — whole-point `config_hash` equality — so no running campaign is
invalidated by upgrading. Once that point writes a new manifest it carries
hashes.

**`--status`** reports `next` as the first module that is not reusable under
the new rule, and `stale` only when that is the first module. A point whose
mesh is reusable but whose solver is not shows `partial`, `next = omega3p`,
which is the honest answer and what the user wants to know before resubmitting.

**1.4 falls out.** A Cubit-only workflow's `cubit` entry, inputs, journal and
(absent) upstream are identical to the full workflow's, so its hash is; the
full run under `resume: true` reuses every mesh and runs the solvers.

Cost: hashing a 137 MB Track3P dump is ~0.3 s per evaluation. Cached per
process on `(path, size, mtime)` so a sweep hashes each file once.

### 2.4 A failed point does not end the sweep (Phase 4)

`mode: {on_error: raise | continue}`, default `raise` — today's behavior,
byte-for-byte, so no baseline moves. Under `continue` the sweep loop catches
the exception, prints one line naming the point, its workdir and the error,
appends a `_PointResult` with NaN outputs and no field index, and goes on.
The table is written with an added `status` column (`complete` / `failed`) —
added **only** under `continue`, so a default-path table's columns are
unchanged. `run-lume-ace3p` exits **1** when any point failed, after writing
the table: a batch queue reads the exit code, and a finished-with-failures
sweep is not a success. `resume: true` then re-runs exactly the failed points.

Applies to `single`, `parameter_sweep` and `collect_training_data`. The Xopt
modes have their own switch in `Xopt(strict=False)`, which records
`xopt_error` and a NaN objective and continues; whether each generator
tolerates a NaN row is **not verified** (see §4 D6) and is deferred.

### 2.5 Running over an existing workdir says so (Phase 5)

A run without `resume` over a workdir that holds a manifest prints one summary
line before the first point — how many of the implied workdirs hold a
previous run's manifest, and that `resume: true` would reuse them — rather
than refusing. Refusing would break every shipped example's re-run (they all
use `workdir_mode: manual` in one directory), and a warning is the same shape
`_require_own_workdirs` already uses for the same reason.

---

## 3. Design decisions

1. **Raise from the wrapper, not from `run_logged`.** The wrapper knows what
   it ran and what it expected; `run_logged` is a logging seam with tests of
   its own. `ACE3P.exit_status_note()` is already the right message.
2. **`clean` is per module and names only what the module writes.** The
   alternative — wipe the workdir before a fresh point — destroys logs, the
   manifest, staged inputs and anything the user put there, and cannot
   coexist with per-module reuse (Phase 3), where Cubit's mesh must survive
   the solver's re-run.
3. **Hash file contents, not mtimes.** A copy, a `git checkout`, a `touch`
   all move mtimes without changing meaning; contents are what the tool reads.
4. **Per-module `input_buckets` rather than hashing `for_module()` whole.**
   Whole-object hashing would re-run Cubit for a `particles` change. The
   table is six entries and lives beside `requires` / `provides`, which
   already declare the same kind of fact.
5. **Keep the "downstream of a re-run re-runs" rule alongside chaining.**
   They answer different questions (configuration changed vs. output gone),
   and the second is what protects `transwake`.
6. **Extraction failure is a point-level `error`, not a module `failed`.**
   The modules did complete; marking one `failed` would make a later resume
   re-run a solver whose results are fine. The point is what needs attention.
7. **`on_error` defaults to `raise`.** Silently continuing past a failure is a
   change in what a finished run means, and some sweeps (a frequency target
   feeding a later step) must not continue.
8. **Warn, do not refuse, on an existing workdir.** See §2.5.
9. **`config_hash` is untouched.** The pinned-hash tests stay green; a
   campaign in flight on 0.7.x resumes by the old rule.

---

## 4. Decisions to confirm before the phase that needs them

- **D1 (Phase 1).** Default on, no escape hatch, *unless* the S3DF check finds
  a tool that exits nonzero after success. If one does, add a per-module
  `ignore_exit_status: true` and document which tool needs it.
  **Recommendation:** on, no hatch until needed.
- **D2 (Phase 2).** Should a fresh run also truncate `<name>.log`? Today it
  appends with a `$ <command>` header per invocation, which keeps the history
  of a point that was retried. **Recommendation:** keep appending.
  **Resolved (Phase 2): appending kept;** `clean` never touches a log.
- **D3 (Phase 3).** Should `files:` contents be hashed even at 137 MB?
  **Recommendation:** yes, with the per-process cache.
  **Resolved (Phase 3): no — only input files are hashed** (the journal, the
  solver input, the `.rfpost`, the Geant4 input). Data files (`files:`, Geant4
  geometry, a source module's `file`) count by path only, through the config
  mapping.
- **D4 (Phase 4).** Exit code under `on_error: continue` with failures: 1
  (recommended) or 0 with the `status` column as the only signal.
  **Resolved (Phase 4): the user chose the default `skip` and exit 1 only when
  every point failed.** A single bad parameter value must not fail the batch
  job; a run with no successes is almost always a configuration problem.
  Decision 7 is reversed by the user: `on_error` defaults to `skip`, and
  configuration errors (including missing input files) are raised before the
  first point.
- **D5 (Phase 5).** Warn (recommended) vs. refuse on an existing manifest.
  **Resolved (Phase 5): a one-line notice**, as recommended.
- **D6 (deferred).** Expose `xopt: {strict: false}`. Needs a probe of each
  shipped generator (`NelderMead`, `ExpectedImprovement`, `UCB`, the
  multi-fidelity one) with a NaN objective row before it is offered.
- **D7 (Phase 5).** Version. Phase 1 changes what a nonzero exit does, which
  is a behavior change a user could notice → **0.8.0**.
- **D8 (Phase 3).** Exclude the launch keys — `tasks` / `cores` / `opts` and
  their legacy aliases `ace3p_tasks` / `ace3p_cores` / `ace3p_opts`,
  `geant4_threads` / `geant4_opts` — from the **module** hash. Results do not
  depend on them, and resizing a solve for a different allocation is exactly
  the kind of edit a resumed campaign makes. `config_hash` keeps them (it must
  not move). **Recommendation:** exclude, declared per class as
  `_launch_keys` beside `input_buckets`.
  **Resolved (Phase 3): excluded** — also acdtool's `tasks` / `cores` / `opts`.

---

# Phase 1 — A nonzero exit is a failure

### Approach

`src/lume_ace3p/logs.py`: add `class ToolExitError(RuntimeError)` carrying
`command`, `returncode`, `log_file`. `run_logged` unchanged.

`src/lume_ace3p/ace3p.py` `ACE3P.run`: replace the `print(exit_status_note())`
with `raise ToolExitError(self.exit_status_note(), …)`. Keep
`exit_status_note()` — the "no results" errors still append it.

`src/lume_ace3p/cubit.py` `Cubit.run` / `meshconvert`: check the
`CompletedProcess` of each and raise. `src/lume_ace3p/geant4.py` and
`src/lume_ace3p/acdtool.py`: same, one line each.

`src/lume_ace3p/modules.py` `CubitModule.run`: after `cubit.run(...)`, if the
journal names an export and the file is absent, raise naming the path and
the log.

`src/lume_ace3p/workflow_graph.py` `evaluate`: wrap the extraction and
derived loops; on exception, `state['error'] = f'{type}: {exc}'`,
`write_state`, re-raise. `src/lume_ace3p/state.py` `point_status`: a state
with `error` is `FAILED`; `pending` is `None` for it (nothing to resume into
— the modules are complete; the user must fix the config or the files).

**S3DF check before merging:** one job (milano, `--constraint=OS_VER:8.10`)
that runs `cubit` + `meshconvert`, each solver, `acdtool postprocess rf`, and
the Geant4 app on a shipped example and prints `$?` after each. Record the
result here. If anything exits nonzero on success, D1's hatch is added in
this phase.

**Result (job 40901886, sdfmilan146, EL 8.10, 2026-10-15):** every tool exits
**0** on success — `cubit`, `acdtool meshconvert`, `omega3p`, `acdtool
postprocess rf`, `s3p`, `t3p`, `acdtool postprocess transwake`, `track3p`, and
the Geant4 `dose-npass/sim` app, each re-run in a copy of a September
validation workdir and each leaving its output. A control (`omega3p` on a
missing input) exited 255, so `srun` passes a failure through. **D1 resolved:
on, no hatch.** Script and log in
`/sdf/scratch/users/d/dbizzoze/failure_probe/exit_check/`.

**As built.** `logs.check_exit(tool, completed, log_file)` raises
`ToolExitError` and is what Cubit, `meshconvert`, Geant4 and acdtool call;
`ACE3P.run` raises it itself with `exit_status_note()` as the message, before
`output_parser`. The missing-mesh check raises `RuntimeError`. Extraction moved
into `Workflow._extract_outputs`. Tests: `tests/test_tool_failures.py`. Full suite on
milano (job 40902677, 2026-10-15): 980 passed, 2 skipped, baseline self-check
green; its one failure was a test stub of `subprocess.run` returning `None`,
fixed in the follow-up commit.

### Verification (Phase 1 done when)

- [x] Fake `cubit` exiting 3 → point recorded `cubit: failed` with the exit
  status in `error`; `omega3p` absent; `ToolExitError` raised.
- [x] Fake `cubit` exiting 0 but writing no mesh → `cubit: failed`, error
  names the missing mesh path.
- [x] Fake `omega3p` exiting nonzero after writing output → `omega3p:
  failed` (the exit wins over the file).
- [x] Fake `omega3p` exiting 0 writing nothing → `omega3p: complete`,
  manifest `error` set, `--status` says `failed`, `next` empty.
- [x] `test_resume.py` test 2–3 (`transwake`) still injects its failure by
  monkeypatch; its docstring paragraph about "a fake binary exiting 1 would
  be recorded complete" is rewritten to say the opposite.
- [x] Every frozen baseline byte-identical (dry-run baselines launch nothing;
  this phase cannot move them, but the self-check is the proof).
- [x] S3DF check recorded above; D1 resolved.

# Phase 2 — Clean before a fresh run; re-stage inputs

### Approach

`Module.clean(ctx)` default no-op; the seven implementations per the §2.2
table. `_SolverModule.clean` uses `_written_results_dir()`;
`CubitModule.clean` uses `_journal_export()` and the `.ncdf` sibling
(**verify** the meshconvert output name against a real run — the docs say
`<stem>.ncdf`); `Geant4Module.clean` uses `_output_files()` (names resolvable
before the run only from the YAML override or the input file — both are);
`AcdtoolModule.clean` uses `spec.resolve_output(jobname)` and returns early
when `spec.mutates`.

`evaluate`: `if not reusing and not ctx.dry_run: module.clean(ctx)` before
`module.run`. Not under dry-run — that path writes markers, not outputs, and
a dry-run over a real workdir must not delete real results.

`_stage_file(ctx, src, fresh=False)`: when `fresh`, remove an existing
destination before linking/copying. Callers pass `fresh=not skip_execution`.

**As built.** `clean` is on every module, as in the §2.2 table. Every removal
goes through `modules._remove_output(ctx, name)`. It joins `name` onto the
workdir, refuses anything that resolves outside it or to the workdir itself
(including a path through a symlinked directory), and removes a symlink as a
link, never its target. Specifics:

- The meshconvert name was checked on a September validation run:
  `pillbox-rtop4.gen` → `pillbox-rtop4.ncdf` beside it. Cubit removes the
  `.ncdf` only under `meshconvert: true`.
- T3P's clean removes the whole job directory (`t3p_results`), not only
  `OUTPUT/`, since the monitor files beside it are T3P's too.
- `Geant4Module.clean` runs before the wrapper exists, so it resolves names
  from the source input file with the point's `geant4:` inputs laid over it
  (`_output_files` was split into `_output_names(values)` for this).

`_stage_file(..., fresh=)` takes `modules._fresh(ctx, skip_execution)` (not
resumed, not dry-run). Under `fresh` it removes the destination unless it
already *is* the source (same file, or a link that follows it), so symlink and
hardlink mode see no churn.

**Beyond the plan:** the same staleness hit the two wrappers that copy their
own input into the workdir only when no copy is there, and then read that
copy — `Acdtool` (the `.rfpost`) and `Geant4` (the `.geant4`). On a fresh run
their old copy is the *previous run's merged input*, so an edited source file
was ignored. The modules now `_unstage` it first. Cubit and the ACE3P solvers
read the source and were not affected.

Tests: `tests/test_clean.py`. Full suite on milano (job
40905686, sdfmilan, EL 8.10, 2026-10-15): 1010 passed, 2 skipped, baseline
self-check green.

### Verification (Phase 2 done when)

- [x] The §1.1 stale-mesh probe (Cubit crashes, old mesh present) now records
  `cubit: failed` **and** the old mesh is gone — the table row cannot hold
  the old answer.
- [x] A fresh run over a complete workdir removes the results directory
  before the solver writes; `<name>.log` has two `$` headers; the manifest
  and the staged journal copy survive.
- [x] A resumed (skipped) module's output is **not** removed.
- [x] Editing a `files:` SEY table in the config directory and re-running
  (no resume, `copy` mode) → the workdir copy matches the new source.
- [x] A `transwake` acdtool re-run removes nothing (mutating command).
- [x] Every frozen baseline byte-identical.

# Phase 3 — Per-module hashes

### Approach

`src/lume_ace3p/state.py`: `module_hash(entry_config, inputs_subset,
file_digests, upstream_hashes)` → `'sha256:…'`; `file_digest(path)` with the
`(path, size, mtime)` cache. `record_module` gains `hash=`.

`src/lume_ace3p/modules.py`: `input_buckets` class attribute (per §2.3) and
`input_files()` method on each module; a `Module.hash_inputs(ctx)` helper that
assembles (1)–(4) so `Workflow` does not know module internals.

`src/lume_ace3p/workflow_graph.py`: `evaluate` computes each module's hash
just before its reuse decision (upstream hashes are then known — the same
lazy ordering `_reusable` already relies on). `_resume_state` no longer
rejects on `config_hash` mismatch; `_reusable` compares the entry's `hash`
when present, else falls back to `previous['config_hash'] == current_hash`.
The "different configuration" message becomes per module: "`omega3p` re-runs:
its configuration or an input it depends on changed".

`src/lume_ace3p/modes.py` `status`: builds the hashes without running (the
same ordering, no subprocess) and reports per §2.3.

**As built.** `state.module_hash(entry, inputs, files, upstream)` and
`state.file_digest(path)` (cached on `(abspath, size, mtime_ns)`).
`Module.module_hash(inputs, hashes)` assembles (1)–(4) from the class's
`input_buckets`, `_launch_keys` and `input_files()`; `hashes` is `{name: hash}`
of the modules before it, and `upstream` is keyed by kind through
`self.sources`. Per D3, `input_files()` lists only the journal, the solver
input, the `.rfpost` and the Geant4 input; source modules list none. Per D8,
`_launch_keys` is `tasks`/`cores`/`opts` + `ace3p_*` on the solvers,
`tasks`/`cores`/`opts` on acdtool, `geant4_threads`/`geant4_opts` on Geant4.

`evaluate` computes each live module's hash just before its reuse decision and
records it with the entry. `_resume_state` returns a manifest with module
hashes whatever its `config_hash`; one without them keeps the old gate and
message. `_reusable` adds the hash check between "recorded complete" and
`verify`. The recorded-vs-re-extracted drift warning runs only when
`config_hash` matches, since a changed `output_parameters` may reuse a name for
another quantity.

`point_status(state, config_hash, names, module_hashes)`: `pending` is the
first module not complete-under-its-hash, `complete` the count before it.
`stale` = the first module is complete but its hash moved (or, without module
hashes, `config_hash` differs). A point-level extraction `error` is `failed`
under the same `config_hash`, `partial` (nothing to launch) under another.
`Workflow.point_module_hashes(point)` computes the hashes from the prototypes
for `--status`.

The `--status` table's `modules` column now counts the modules a resume would
*reuse*, not the ones merely recorded complete.

Tests: `tests/test_module_hashes.py`; two tests in `test_resume.py` changed
meaning (a changed `results_dir:` now re-runs only the solver; the `stale`
row of the status test is made by moving Cubit's hash, not `config_hash`). Full suite on milano (job 40910788,
sdfmilan146, EL 8.10, 2026-10-15): 1029 passed, 2 skipped, baseline self-check
green.

### Verification (Phase 3 done when)

- [x] `tests/test_state.py` pinned `config_hash` values unchanged.
- [x] **1.4:** Cubit-only sweep, then full workflow with `resume: true` →
  Cubit launched once per point, solvers run, table correct.
- [x] Solver-only YAML change (`tasks:`) → Cubit skipped, solver re-run.
  **Changed by D8:** a `tasks:` change re-runs *nothing*; the solver-only YAML
  change tested is `results_dir:`.
- [x] Journal **contents** change, YAML untouched → Cubit and solver re-run.
- [x] Solver input **contents** change → Cubit skipped, solver re-run.
- [x] `files:` SEY table contents change → that solver re-runs.
  **Changed by D3:** contents are not hashed, so an in-place edit is reused;
  naming a different file re-runs the solver.
- [x] `output_parameters` change only → nothing launched, table rebuilt.
- [x] A `particles` value change → Cubit skipped, `field_emission` /
  `geant4` re-run (hash-level test; no fake Geant4 binary).
- [x] A 0.7.1-style manifest (no `hash` fields; write one by hand in the
  test) resumes by whole-point `config_hash` exactly as before.
- [x] `transwake`: T3P output deleted → T3P and acdtool both re-run though
  acdtool's hash is unchanged (`test_a_re_run_solver_drags_its_consumers_with_it`,
  unchanged).
- [x] `--status` on a mesh-reusable / solver-stale point reports `partial`,
  `next = omega3p`.
- [x] Every frozen baseline byte-identical (manifests are excluded).

# Phase 4 — `on_error: continue`

### Approach

`src/lume_ace3p/modes.py`: `on_error` added to `MODE_KEYS` for `single`,
`parameter_sweep`, `collect_training_data`; `run_mode` validates the value
and passes it down. In the sweep loop, `try/except Exception` around
`_evaluate_point` → print, `_PointResult(i, scalars, {name: nan}, None, None,
status='failed')`. `_PointResult` gains `status` (default `'complete'`).
`_frame` appends `status` only when any point was built under `continue`
(i.e. when the mode asked for the column — not when a failure happened to
occur, so a `continue` run's columns are stable whether or not anything
failed). `run_mode` returns the frame and a `failed` count;
`run_lume_ace3p` exits 1 when it is nonzero.

**As built** (the user changed the design: `on_error: skip | raise`, default
`skip`; see D4). `modes.on_error_of` validates the value in `run_mode` and
`collect_training_data`, before anything runs. `modes._run_point` wraps one
point of `single` / `parameter_sweep`. Under `skip`, it prints `Error: point i
(axis=value) failed in <workdir>` and the error to stderr, adding a traceback
only for an exception that is not `RuntimeError` / `ValueError` / `OSError`.
It then returns a `_PointResult` with NaN outputs and `status='failed'`.
`KeyboardInterrupt` is not caught. After the loop, a summary line lists the
failed indices. `_assemble` adds `results.STATUS_COLUMN` (`'status'`) **only
when a point failed**, so a clean run's columns, and every baseline, are
unchanged. A failed long-format point is one row with a NaN index cell.
`run_lume_ace3p.main` exits 1 when the returned frame's `status` is `failed`
on every row.

Before the first point, `Workflow.check_files()` (called through
`modes._check_files`; skipped under dry-run) raises `WorkflowValidationError`
listing every missing `Module.required_files()`: `input_files()` plus a
solver's `files:`. A mistyped path would otherwise fail every point under
`skip`. A source module's `file:` and Geant4 geometry are not checked, because
the run falls back to the copy already in the workdir, or skips them.

`collect_training_data`: under `skip` a failed sample gets no `field.npz` and
`status: failed`. The status column is written only when a sample failed.
`surrogate_data.load_training_store` drops `failed` rows. The next run retries
them, since the `field.npz` check already decides what is done. The
mid-campaign scoring-mesh check still raises.

Eight Phase 1–3 tests that expect the exception to reach the caller now pass
`on_error='raise'`. Tests: `tests/test_on_error.py`. Full suite on milano (job
40913554, sdfmilan181, EL 8.10, 2026-10-15): 1045 passed, 2 skipped, baseline
self-check green.

**Beyond the plan:** `baseline_utils._stage_example` never removed its temp
copies (~0.5 GB each). About 500 of them had filled iana's 300 GB `/lscratch`.
It now records each root, and a session fixture in `tests/conftest.py` removes
them.

### Verification (Phase 4 done when)

- [x] Default path with no failure: no `status` column; every frozen baseline
  unchanged.
- [x] `skip`, fake `cubit` failing at point 1 of 3 → three rows, row 1 NaN +
  `failed`, exit 0, `Error:` line naming the point and workdir; every point
  failing → exit 1, table still written.
- [x] `skip` then `resume: true` → only point 1 re-runs; no NaN, no `status`
  column.
- [x] Long-format (S3P): the failed point is one row with NaN index and
  outputs.
- [x] `on_error: raise` → exception propagates, no table, later points not
  started.
- [x] `on_error: continue` (typo) → `ValueError` naming the two values.
- [x] A missing solver input or `files:` entry → `WorkflowValidationError`
  before any workdir is created.
- [x] `collect_training_data`: a failed sample is marked, excluded from the
  store, and retried on the next run.

# Phase 5 — Existing-workdir notice, docs, changelog, release

### Approach

`modes.py`: before the loop, count implied workdirs holding a manifest
(`read_state` on each `resolved_workdir`); if nonzero and not `resume`,
print one line. Skipped under `manual` (one directory; `_require_own_workdirs`
already covers it).

Docs: `yaml_reference.md` — manifest example gains `hash` and `error`;
`(resume)` section rewritten around per-module reuse (the per-point table
becomes per-module); `on_error` in the mode-keys table; a short "staging a
campaign" paragraph under `(resume)` for the Cubit-first workflow.
`parameter_sweep.md` resume section: one paragraph on partial reuse.
`troubleshooting.md`: the "restart a sweep" FAQ loses its `:::{important}`
workaround (superseded by `on_error: continue` + `resume`), and a new FAQ
"a step exited nonzero". `CHANGELOG.md` `[0.8.0]`; `pyproject.toml` version.

**As built.** `modes._note_existing_runs` runs in `single` and
`parameter_sweep` when `resume` is off and `workdir_mode` is not `manual`. It
prints `Note: <n> of <N> workdir(s) hold a previous run's manifest. This run
starts them over; mode: {resume: true} would reuse what is still valid.` The
docs went as planned. The troubleshooting FAQ's `:::{important}` workaround
was replaced by a short paragraph, since a failed point no longer loses the
table, and the new FAQ is "A step exited nonzero". Version 0.8.0, changelog
`[0.8.0] — 2026-10-15`. Tests: three in `tests/test_on_error.py`. Full suite
on milano (job 40914602, sdfmilan181, EL 8.10): 1048 passed, 2 skipped.

**S3DF validation (job 40914294, sdfmilan133, EL 8.10, 4:01).** In
`lume-ace3p-tests/phase5_staging/`, `omega3p_sweep` under `indexed` was run
Cubit-only (`workflow:` cut to `cubit`, no `output_parameters`), then in full
with `resume: true`. All 16 points printed "re-runs from 'omega3p'".
`cubit.log` holds one Cubit invocation per point (two `$` lines: the mesher
and `acdtool meshconvert`), the manifests record `cubit` as `resumed`,
`omega3p` ran once per point, and the table is complete.

**Found, not fixed:** `acdtool.log` has no `$` header after the run. ACDTool
writes its own `acdtool.log` in its working directory, truncating the log the
module tees into. This predates the plan (the September validation logs show
the same). The tool's own messages survive, but the `$` command line
lume-ace3p wrote is lost. Cubit's `meshconvert` tees into `cubit.log` and is
not affected. The fix is a module-log name that cannot collide,
such as `acdtool.lume.log`, which renames a file users may look for. Deferred.

### Verification (Phase 5 done when)

- [x] Docs build clean (`docs/Makefile`), no refactor history in prose.
- [x] Full pytest on milano (never iana), `--constraint=OS_VER:8.10`.
- [x] S3DF validation: `omega3p_sweep` run Cubit-only (a copy of the YAML
  with the solver entry removed), then full with `resume: true`; Cubit logs
  show one invocation per point. Job 40914294, above.
- [x] `scripts/tag_release.sh` / `deploy_release.sh` per `docs/releasing.md`:
  tag `v0.8.0` on `main` at `a3e0a60`, deployed on S3DF and Perlmutter.

---

## 5. Out of scope

- Concurrent evaluation (`max_concurrent`) — Phases 3–4 keep the seams the
  resume plan established for it; nothing here depends on it.
- Solver-internal restart (T3P checkpointing) — resume is at step
  granularity, as `docs/t3p_reference.md` states.
- `acdtool` as a mesh producer (`_MESH_PRODUCER_NOTE`).
- Validating output *contents* beyond presence (a truncated `omega3p.out`
  that parses) — Phase 1's exit check and the existing parsers are the line.
