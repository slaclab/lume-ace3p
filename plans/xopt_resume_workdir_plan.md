# Xopt Resume + Per-Evaluation Workdirs — Implementation Plan

**Status: PLANNED.** Two phases, independent of each other and both small.
Phase A changes where an Xopt run's files land and fixes a silent-overwrite bug;
Phase B lets an interrupted optimization continue instead of starting over.

Follow-on to `plans/evaluation_isolation_resume_plan.md`, which delivered resume
for the **table** modes and deliberately left the Xopt modes out: their points are
chosen by the generator as the run proceeds, so there is no fixed set of points to
have finished part of. That reasoning still holds — and it is why Phase B is a
*different mechanism* rather than an extension of the completion manifest.

Every claim below marked **verified** was checked against the installed
`xopt 3.0.0` and this repository during planning; the evidence is quoted inline so
it does not have to be rediscovered.

---

## Motivation

### One: an Xopt run's per-evaluation directories are unusable, and one mode of them is a bug

`workdir_mode` has three values, and for a run driven by *variable overrides* (the
Xopt modes and `collect_training_data`, which pass `evaluate(input_dict)` rather
than axis scalars) all three are unsatisfactory:

* **`manual`** — every evaluation shares one directory, so each one overwrites the
  previous evaluation's mesh, input files, results, logs and run manifest. This is
  the **default**, and it is what all four shipped Xopt examples use.
* **`auto`** — names by input *value*, giving directories like
  `lume-ace3p_workdir_14.724999999999998_1.5750000000000002`. Unbounded in length
  and unreadable.
* **`indexed`** — needs a point index, and the Xopt objective has none to give.

Worse, **`auto` is silently broken on this path**. `_getworkdir`'s no-sweep branch
([`workflow_graph.py:370`](../src/lume_ace3p/workflow_graph.py#L370)) walks only the
`cubit` and `particles` buckets — not `ace3p`, not `geant4`:

```python
for value in (*inputs.cubit.values(), *inputs.particles.values()):
```

**Verified** against a real config optimizing an ACE3P leaf:

```
Start=1e+09 -> workdir=wd_100.0
Start=2e+09 -> workdir=wd_100.0
Start=3e+09 -> workdir=wd_100.0
```

Every point of that optimization runs in one directory while *looking* per-point.
The four shipped Xopt examples all optimize `cubit` variables, so none of them hits
this today — but an optimization over an ACE3P or Geant4 knob does, and reports
whatever the last write left behind.

### Two: an interrupted optimization is lost entirely

The table modes can now resume; the Xopt modes cannot. A 200-evaluation
optimization killed at evaluation 190 throws away all 190 — which is worse than the
sweep case it replaced, because the *evaluations* are the expensive part and they
are all still on disk in `sim_output.txt`.

`_log_xopt` already writes the full trajectory after every step. What is missing is
reading it back, and xopt has a better mechanism than that table (see decision 3).

### Three: `manual` with many evaluations is a hazard, not a setting

Under `manual`, N evaluations share one workdir. That is legal, occasionally
deliberate, and silently destructive the rest of the time. Nothing says so today.

---

## Target design

```yaml
workflow_parameters :
  'workdir' : 'lume-ace3p_opt_workdir'
  'workdir_mode' : 'auto'          # -> _0, _1, _2, … for an optimization

mode :
  type : scalar_optimize
  resume : True                    # continue from xopt_state.yml
```

```console
$ run-lume-ace3p --status s3p_optimization.yaml
 - scalar_optimize: 37 evaluation(s) recorded in 'xopt_state.yml'
 - best 'reflection' = 0.00042 at cornercut=14.03, rcorner1=1.19
```

### Design decisions

1. **On the override path, `auto` means "one directory per evaluation, named by
   iteration".** Not a new key and not a default change: `auto` already means "let
   lume-ace3p name it, one directory per evaluation", and what the natural name *is*
   differs by what is driving. For a sweep the point's values **are** its identity,
   so they name it. For an optimizer the values are 17-digit floats that nobody
   looks a directory up by, so the iteration number is the identity. `indexed`
   behaves identically here, and `manual` still shares one directory for anyone who
   wants that.

   This fixes the collision above as a side effect — an index cannot collide,
   whatever bucket the variable lives in.

   *Not chosen:* fixing `auto`'s no-sweep branch to include the `ace3p`/`geant4`
   buckets and keeping value-naming. It removes the collision but keeps the
   unreadable names, and it makes them *longer*. Fix the naming and the collision
   goes with it. (If value-naming for a `single` run under `auto` is ever wanted,
   that branch is still there and still only sees two buckets — worth a comment
   rather than a change, since no shipped `single` example uses `auto`.)

2. **⚠️ Per-point naming with no configured `workdir` must not use the cwd as its
   base.** `baseworkdir` defaults to `os.getcwd()`
   ([`workflow_graph.py:271`](../src/lume_ace3p/workflow_graph.py#L271)), and
   `point_workdir` appends `_<n>` to it — so a config with no `workdir:` key would
   write to **siblings of the working directory** (`/path/to/run_0`, `/path/to/run_1`).
   All four shipped Xopt examples omit `workdir:`, so this is the common case, not
   the corner.

   `point_workdir` already has the right fallback (`DEFAULT_WORKDIR_BASE` when
   `baseworkdir is None`) but it is dead code, because `baseworkdir` is never `None`.
   Make the per-point base fall back to `DEFAULT_WORKDIR_BASE` *inside* the cwd when
   `workdir` was not configured. Note this affects `auto` sweeps with no `workdir:`
   too, which have the same latent behavior today — decide explicitly whether to fix
   both (recommended) or only the new path.

3. **Xopt resume restores full state by reconstructing `Xopt` around a live
   evaluator — not via `Xopt.from_file`, and not via `add_data` alone.** Three
   things were verified:

   * `X.dump()` **works** with our closure evaluator and writes everything needed:
     `data` (the whole trajectory) *and* the generator's own state. For
     `NelderMeadGenerator` that includes `current_state` (the simplex: `N`, `fsim`,
     `astg`, …) and `is_active`.
   * `Xopt.from_file()` **cannot** be used. It re-imports the evaluator function
     from the dotted path in the dump, and ours is a closure:

     ```
     from_file FAILED: ModuleNotFoundError No module named
       '__main__.make_objective'; '__main__' is not a package
     ```

   * Swapping the live callable into the loaded dict **does** work, restoring data
     *and* generator state:

     ```python
     d = yaml.safe_load(open('xopt_state.yml'))
     d['evaluator']['function'] = sim_function      # the live closure
     X = Xopt(**d)
     ```
     ```
     reconstructed: rows = 4 | generator is_active = True | simplex N = 1
     after 3 more steps: rows = 7 | new evaluations = 3
     x trajectory: [0.5, 0.525, 0.475, 0.425, 0.35, 0.2, 0.275]
     ```

     Three new steps cost exactly three objective calls, and the trajectory is a
     coherent Nelder-Mead *continuation* rather than a restart. Also verified to
     work for a Bayesian generator (`ExpectedImprovementGenerator`), whose GP is
     refit from data — so `serialize_torch` can stay `False`.

   *Not chosen:* `X.add_data(table)` into a freshly built generator, the route the
   existing `sim_output.txt` would allow. It restores the *data* but not the
   generator, which for a Bayesian generator is nearly equivalent (the model is
   refit) and for `NelderMeadGenerator` is **not** — the simplex *is* the state, so
   a data-only restore silently restarts the search on top of old data. Verified
   diverging, and it re-proposed a point it already had:

   ```
   full-state continuation : 0.35, 0.2, 0.275
   data-only continuation  : 0.275, 0.275, 0.35     # duplicate proposal
   ```

   Keep `add_data` as the documented fallback for a run whose state file is gone but
   whose table survives, and say what it costs.

4. **⚠️ A resumed optimization does not reproduce the uninterrupted trajectory.**
   The table modes promise an *identical* table; that promise cannot be made here
   and must not be implied. Restoring history makes the generator propose from an
   equally-informed state, not the same state a straight-through run would have been
   in (torch/numpy RNG streams alone break it). The honest promise: **no evaluation
   is repeated, and the search continues from the same data.** Say it in the docs
   next to the key, or someone will diff two `sim_output.txt` files and file a bug.

5. **Iteration counts and cost budgets are totals, so resume is idempotent.**
   `num_random` / `num_step` / `max_iterations` / `cost_budget` are read as budgets
   for the *campaign*, not for this process — so a resumed run continues until the
   total is reached, and resuming an already-finished optimization does nothing.
   This matches the sweep, where a completed campaign resumed re-runs nothing. It
   means `iteration_index` starts at `len(X.data)` and the random-seeding block is
   skipped when the data already covers it.

6. **We write the state file, not xopt's `dump_file` auto-dump.** Setting
   `dump_file` makes xopt dump inside `evaluate_data` (verified), which is the right
   *granularity* but the wrong *robustness*: it writes in place, and the file is
   rewritten after every evaluation, so a kill mid-write leaves a truncated YAML —
   precisely what a resume would then read. Call `X.dump(tmp)` + `os.replace` from
   `_log_xopt`, which already runs after every step, and mirror
   `state.read_state`'s contract: an unreadable or truncated state file degrades to
   "no state" (start over) rather than to a crash or a misread.

   This also fixes a small existing gap: `gp_parameter_sweep` logs after each
   `step()` but **not** after its initial `random_evaluate()` loop, so a run killed
   during seeding currently loses all of it.

7. **The interrupted candidate's workdir is abandoned, deliberately.** When the job
   dies mid-evaluation, that candidate's partial directory (mesh built, solve killed)
   is never reused: after the restore the generator proposes a *different* point, so
   its `config_hash` matches nothing. One wasted mesh per interruption is a bounded,
   acceptable loss, and chasing it would mean reconciling a proposal against a
   directory the generator will not propose again. The completion manifest still
   describes that directory for anyone debugging it.

8. **Warn when a multi-evaluation run shares one workdir; do not change the global
   default.** `manual` stays the default for `workflow_parameters`, because flipping
   it silently relocates the output of every config that omits the key while changing
   *nothing* for any shipped sweep (all eight already set `auto` explicitly) — and
   the only shipped configs relying on the default are the four Xopt ones, where the
   fix is decision 1, not a different default. What the default costs is that the
   hazard is silent, so make the hazard loud instead: one warning when a run that
   will perform more than one evaluation is about to do them all in one directory.

   The four shipped Xopt examples switch to `workdir_mode: auto`, so nothing shipped
   warns.

---

# Phase A — Per-evaluation workdirs on the override path

### Approach

`src/lume_ace3p/modes.py`:

1. `_objective_from_workflow` counts its evaluations and passes the per-iteration
   workdir, reusing the helpers Phase 2/4 already added:

   ```python
   def _objective_from_workflow(workflow, vocs, xopt_dict, first_index=0):
       ...
       index = itertools.count(first_index)
       def sim_function(input_dict):
           ...
           outputs, _ctx = _evaluate(workflow, input_dict,
                                     workdir=_iteration_workdir(workflow, next(index)))
   ```

   `_iteration_workdir(workflow, n)` returns `workflow.point_workdir(n)` when
   `getattr(workflow, 'workdir_mode', None)` is `'auto'` or `'indexed'`, else
   `None` — and `_evaluate` already omits the keyword when it is `None`.

   ⚠️ **That `getattr` gate is load-bearing for the test doubles.** Both
   `baseline_utils.SyntheticWorkflow` and `test_run_xopt_compat.SynthWorkflow`
   define `evaluate(self, input_dict)` with no `workdir` parameter and no
   `workdir_mode` attribute. The gate keeps them valid unedited (and keeps the
   frozen `s3p_optimization` baseline, which is produced *through* that double,
   untouched). The new naming therefore needs its own test against a real
   `Workflow`.

2. `_require_own_workdirs(workflow, evaluations)` — the decision-8 warning, called
   from `parameter_sweep` and from the two Xopt modes: when `workdir_mode` is
   `'manual'` and more than one evaluation is coming, print one line naming what
   will be overwritten and the fix (`auto` / `indexed`).

`src/lume_ace3p/workflow_graph.py`:

3. `point_workdir` falls back to `DEFAULT_WORKDIR_BASE` when `workdir` was not
   configured (decision 2). Requires knowing whether the key was *set*, so record it
   in `__init__` (`self._workdir_configured = 'workdir' in self.workflow_params`)
   rather than comparing against `os.getcwd()` after the fact.

4. Comment `_getworkdir`'s no-sweep branch to say it names from `cubit`/`particles`
   only, that it is now reached only by a `single` run under `auto`, and that the
   override path no longer uses it.

`examples/`: `workdir_mode: 'auto'` on `omega3p_optimization`, `s3p_optimization`,
`s3p_mf_optimization`, `s3p_bayesian_sweep`, each with a one-line comment saying
what it names.

### Verification (Phase A done when)

- An optimization over an **ACE3P** variable puts each evaluation in its own
  directory. This is the bug from the motivation; assert on the directory list, not
  on a message.
- Iteration workdirs are `<workdir>_0`, `<workdir>_1`, … in evaluation order, and
  a `NelderMeadGenerator` run that proposes the *same point twice* still gets two
  directories (the property value-naming cannot have).
- With no `workdir:` configured, per-evaluation directories land **under** the cwd
  (`lume-ace3p_workflow_output_0`), not as siblings of it.
- `workdir_mode: manual` + a multi-evaluation run prints exactly one warning naming
  the overwrite; a single-evaluation run prints none; a sweep under `auto` prints
  none.
- Both Xopt test doubles still work unedited, and `tests/test_run_xopt_compat.py`
  passes untouched.
- **Baselines byte-identical**, including `s3p_optimization` (produced through the
  synthetic double, so the naming change cannot reach it — confirm rather than
  assume).

### Deliverables

`modes.py`, `workflow_graph.py`, four example YAMLs,
`docs/yaml_reference.md` (the `workdir_mode` table gains the override-path row),
`docs/optimization.md`, `tests/test_resume.py` or a new
`tests/test_workdirs.py`, `CHANGELOG.md`.

---

# Phase B — Resume an interrupted optimization

### Approach

New `src/lume_ace3p/xopt_state.py` (small, mirroring `state.py`'s shape and
contract):

```python
STATE_FILE = 'xopt_state.yml'

def write_xopt_state(path, X) -> str | None      # X.dump(tmp) + os.replace
def read_xopt_state(path) -> dict | None         # None on absent/unreadable/truncated
def restore_xopt(state, sim_function, generator=None) -> Xopt | None
def evaluation_count(state) -> int
def best_point(state, objective) -> tuple | None  # for --status
```

`restore_xopt` is decision 3's swap: replace `state['evaluator']['function']` with
the live `sim_function` and validate. It returns `None` (rather than raising) when
the state cannot be used — a different `vocs`, a different generator name, a
validation failure — after saying which, so the run starts fresh instead of dying.
**A `vocs`/generator mismatch must not be silently accepted:** resuming a
`MINIMIZE` campaign into a `MAXIMIZE` config would optimize against the old data.
That check is this phase's `config_hash`.

`src/lume_ace3p/modes.py`:

1. `_log_xopt(filename, xopt_obj, state_path=None)` also writes the state file, so
   persistence happens wherever logging already does. Add the missing
   `_log_xopt` call after `gp_parameter_sweep`'s `random_evaluate` seeding loop
   (decision 6).
2. `scalar_optimize(..., resume=False)` and `gp_parameter_sweep(..., resume=False)`:
   when resuming, `restore_xopt` first; on success skip the random-seeding block
   that the restored data already covers and set `iteration_index = len(X.data)`
   (decision 5). `run_mode` passes `resume` through — it already reads the key.
3. The objective's iteration counter starts at `len(X.data)` (`first_index=` from
   Phase A), so a resumed run does not overwrite earlier iterations' workdirs.

`src/lume_ace3p/run_lume_ace3p.py`: `--status` accepts the Xopt modes, reporting
evaluations recorded and the best objective so far rather than the per-point table
(`STATUS_MODES` grows; the table walk and this one are two branches of
`_report_status`).

### Verification (Phase B done when)

- **An optimization stopped after k evaluations and resumed performs no repeated
  evaluation**, and its objective is called exactly (budget − k) more times. Counted
  on the objective itself — the load-bearing assertion, and the one the
  `sim_output.txt` row count alone does not make.
- **A resumed `NelderMeadGenerator` run continues its simplex** rather than
  restarting: its next proposals differ from those of a fresh generator handed the
  same data via `add_data` (the two verified trajectories in decision 3 are the
  fixture for this). This is the test that distinguishes full-state restore from
  data-only, so it is the one that must exist.
- A resumed Bayesian run (`ExpectedImprovementGenerator` and
  `BayesianExplorationGenerator`) restores and continues, with no torch
  serialization.
- Resuming a **finished** optimization performs zero evaluations (decision 5's
  idempotence).
- A **truncated** state file (write killed mid-dump) resumes as "no state": the run
  starts over, says so, and does not raise. Same for absent and for a state file
  whose `vocs` disagrees with the config — that one names the disagreement.
- A resumed run's iteration workdirs continue the numbering (`_k`, `_k+1`, …) and
  do not overwrite `_0…_k-1`.
- `--status` on a half-finished optimization reports the evaluation count and the
  best objective; on a finished one, the same numbers as the final log.
- **Baselines byte-identical with `resume` unset**, and the state file is excluded
  from baseline comparison the way the run manifest is (`BASELINE_EXCLUDED` in
  `tests/baseline_utils.py`) — it carries timestamps and an absolute evaluator path.
- The docs state decision 4 (no trajectory reproduction) next to the key.

### Deliverables

`xopt_state.py` (new), `modes.py`, `run_lume_ace3p.py`,
`tests/baseline_utils.py` (`BASELINE_EXCLUDED`), `tests/test_xopt_resume.py` (new),
`docs/yaml_reference.md`, `docs/optimization.md`, `CHANGELOG.md`.

---

## Notes for whoever implements this

* **Order.** A before B: B's iteration counter depends on A's naming, and A is
  independently worth landing (it fixes a silent overwrite).
* **B works under any `workdir_mode`,** including `manual` — it restores from the
  xopt state file, not from per-point manifests. A is about *where files go*; B is
  about *not repeating evaluations*. Do not couple them beyond the counter.
* **`collect_training_data` also takes the override path** and already passes an
  explicit `workdir=` per sample, so A does not touch it. Its `resume:` (from the
  previous plan) is unrelated to B.
* The experiments behind decision 3 are ~30 lines each and worth re-running first as
  a sanity check against the installed xopt version; they are quoted above with
  their outputs so a mismatch is obvious.
* `xopt 3.0.0` is what these were verified against (`pyproject.toml` pins
  `xopt>=3.0.0`). `Xopt.model_fields` there is
  `['generator', 'evaluator', 'strict', 'dump_file', 'data', 'serialize_torch',
  'serialize_inline', 'stopping_condition']`; `stopping_condition` may be a cleaner
  home for decision 5's budget accounting than our own loop counters — worth a look
  before writing the loop changes, but not a prerequisite.

## What this leaves for later

- **`max_concurrent`** (from the previous plan) — still the next thing after these.
  A's iteration naming is a prerequisite for it on the Xopt path, since concurrent
  evaluations cannot share a workdir. Note `Evaluator(function=...)` currently has
  `max_workers = 1` (verified), which is what makes A's counter deterministic today;
  concurrency will need the index assigned at *proposal* time rather than at call
  time.
- **Named artifacts** — unrelated, and still what TEM3P is blocked on.
