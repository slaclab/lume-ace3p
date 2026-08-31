# Xopt Config Validation — Implementation Plan

**Status: PLANNED.** Three items, independent of each other. All three are about the
same thing: a misconfigured Xopt run currently *looks like a successful one*.

Follow-on to `plans/xopt_resume_workdir_plan.md`, which hardened the resume surface
(items 1, 2, 6 and 7 of that review). These are the three that were deliberately
deferred because they change behaviour and messages **outside** the resume feature.

Every claim marked **verified** was checked against this repository during planning;
the evidence is quoted inline so it does not have to be rediscovered.

---

## Motivation

### One: a misconfigured optimization exits 0

**Verified** against a config whose only fault is a typo in the generator name
(`NelderMeedGenerator`):

```console
$ run-lume-ace3p bad.yaml
lume-ace3p 0.3.4
That generator is not supported. Ensure that the generator name specified in the
yaml file matches exactly with the Xopt generator name of choice. Exiting the program.
main() returned normally      shell exit=0
```

The message says "Exiting the program"; the process exits **0**. `scalar_optimize`
returns `None`, `run_mode` returns it, `_run_declarative` returns it, and `main()`
ignores the return entirely. In a batch script that is a job that reports success,
consumed its allocation, and produced no output file. A `--status` check afterwards
reports "has not recorded any evaluation yet", which is indistinguishable from a job
still queued.

Four paths in `scalar_optimize` / `_build_generator` return `None` this way:

* an unsupported/misspelled `generator`
  ([`modes.py`](../src/lume_ace3p/modes.py), `_build_generator`'s fallthrough),
* no termination criterion (the `else` branch of the criteria chain),
* an unsupported `cost_function`,
* `ExpectedHypervolumeImprovementGenerator` without `reference_point`.

`gp_parameter_sweep` has no equivalent — it constructs its generator directly.

### Two: the "no termination criteria" message names criteria that do not work

**Verified**, both of these produce the identical message and run nothing:

```console
$ # xopt_parameters: {generator: NelderMeadGenerator, tolerance: 1.0e-3}
No termination criteria specified for Xopt. Provide a criterion such as 'num_step',
'tolerance', or 'cost_budget' (for multi-fidelity).

$ # xopt_parameters: {generator: NelderMeadGenerator, max_iterations: 10}
No termination criteria specified for Xopt. Provide a criterion such as 'num_step',
'tolerance', or 'cost_budget' (for multi-fidelity).
```

`tolerance` is named as a criterion by the message and **is not one**: it is only a
stopping *test* layered on the `num_step` or `cost_budget` loops (`check_tols`).
`max_iterations` is not named at all, and is also not a criterion: it is read only
*inside* the `if 'num_step' in xopt_dict` branch, so `max_iterations` without
`num_step` is ignored entirely.

`docs/optimization.md` lists both as if they stood alone:

> - `max_iterations` (optional): maximum number of steps after which optimization
>   must end, regardless of other stopping criteria.

So a user following the docs gets a message telling them to supply the thing they
just supplied.

### Three: unrecognized keys are silent

**Verified**: `num_steps` (plural) produces the same generic message with no mention
of the key it did not recognise. The same silence covers `Resume`, `resume_from`, a
`resume:` misplaced into `train_surrogate`'s mode block, `sweep_output` for
`sweep_output_file`, and any other near-miss. Nothing in the pipeline compares a
config's keys against the set it reads.

This is the cheapest of the three to fix and probably catches the most real mistakes.

---

## Design decisions

1. **Items 1–3 are independent and should land as separate commits.** Item 1 changes
   exit status (the only one with a compatibility question), item 2 is message-and-docs
   only, item 3 is purely additive. Do not entangle them.

2. **⚠️ Item 1 is a behaviour change for anything parsing stdout or checking `$?`.**
   The shipped batch scripts (`examples/*/run_lume-ace3p_*.batch`) must be read before
   changing this — if any of them branches on the exit status, the change is still
   right but the scripts need updating with it. "Exits 0 having done nothing" is not a
   contract worth preserving, but it is a contract someone may have built on.

3. **Raise, don't `sys.exit`, in the mode layer.** `modes.py` already raises
   `ValueError` for `_mc_noise_guards` and `_require_resumable`; the CLI is what turns
   an exception into an exit status. Adding `sys.exit` calls inside `modes.py` would
   make the modes unusable as a library (they are — `tests/test_run_xopt_compat.py`
   and the surrogate tests drive them directly).

   That means `main()` needs a top-level handler that prints the message and exits
   non-zero, rather than letting a traceback out. Check what it does today for a
   `ValueError` from `_mc_noise_guards` before adding a second path.

4. **The unrecognized-key check must WARN, not raise.** A config with a harmless extra
   key runs today; making it fail would break working setups for no safety gain, and
   the whole point is to catch typos, not to police the schema. Warn, name the
   unrecognized keys, and list the recognized ones.

5. **Scope the key check to blocks with a closed key set.** `mode:`,
   `xopt_parameters:`, `vocs_parameters:` and `workflow_parameters:` are enumerable.
   `input_parameters` / `output_parameters` are user-namespaced and must be left
   alone. `workflow:` entries are per-module and would need each module's key set —
   worth doing eventually, but not in this pass.

6. **The recognized-key sets belong beside the code that reads them,** not in one
   central table that will rot. `xopt_parameters` is read only by `modes.py`'s Xopt
   functions, so its set lives there next to them; `workflow_parameters` is read by
   `Workflow.__init__`, so its set lives there. A single shared checker function takes
   `(block_name, config, recognized)`.

---

# Item 1 — A misconfigured run must not exit 0

### Approach

`src/lume_ace3p/modes.py`:

1. `_build_generator` raises `ValueError` naming the unsupported generator **and
   listing the supported ones** instead of printing and returning `None`. Same for the
   missing `reference_point` case (which is a MOBO-specific requirement worth naming
   as such).
2. `scalar_optimize`'s "no termination criteria" and "cost function not supported"
   branches raise `ValueError` (see item 2 for the message).
3. `scalar_optimize` no longer has a `None` return path, so the `if generator is None:
   return None` guard goes with it.

`src/lume_ace3p/run_lume_ace3p.py`:

4. `main()` catches `ValueError` (and `WorkflowValidationError`) around
   `_run_declarative`, prints `Error: <message>`, and `sys.exit(1)` — mirroring what
   `--status` already does for a `ValueError` from `_report_status`. Do **not** catch
   bare `Exception`: a solver crash or a bug should still produce a traceback.

`examples/*/run_lume-ace3p_*.batch`: read first (decision 2); update only if one
branches on the exit status.

### Verification (item 1 done when)

- A config with a misspelled `generator` exits **non-zero** and the message lists the
  supported generator names. Asserted on the exit status, not the message.
- Same for: no termination criterion, an unsupported `cost_function`, and MOBO without
  `reference_point`.
- A *valid* config still exits 0.
- A solver failure still raises with a traceback rather than being flattened into
  `Error: …` — the handler is for configuration errors, not for everything.
- `tests/test_run_xopt_compat.py`'s `test_mc_noise_guard_requires_bin_edges` still
  passes: it asserts `pytest.raises(ValueError)` from the mode layer, which is the
  shape being extended here.

### Deliverables

`modes.py`, `run_lume_ace3p.py`, possibly the batch scripts, a new test module or a
section in `tests/test_modes.py`, `CHANGELOG.md` (**Changed**, flagged as a
behaviour change).

---

# Item 2 — Say which criteria actually terminate a run

### Approach

`src/lume_ace3p/modes.py`: the message becomes accurate about the three-way
distinction —

* **criteria** (one is required): `num_step`, `cost_budget`, `alotted_time`;
* **refinements** (need a criterion to refine): `max_iterations`, `tolerance`;
* and name whichever refinement the config supplied, since "you gave me
  `max_iterations` but no `num_step`" is the actual mistake and is worth saying.

`docs/optimization.md`: correct the `xopt_parameters` list so `max_iterations` and
`tolerance` are documented as refinements rather than as standalone criteria, and say
that `max_iterations` counts the *campaign's* evaluations (which is what it does, and
now also across a resume).

**Decide explicitly**: should `max_iterations` alone start working — i.e. should the
`max_iterations` loop move out of the `num_step` branch? It is a one-line change and
arguably what the docs promised. *Not chosen here* on purpose: it would silently start
running configs that currently run nothing, and someone may have `max_iterations` set
as a guard alongside `cost_budget` where it is presently inert. Fixing the message is
the honest minimum; making the key work is a separate decision.

### Verification (item 2 done when)

- `tolerance` alone and `max_iterations` alone each fail with a message that names the
  key supplied and the criteria that would work, and does **not** list the supplied
  key as a criterion.
- The docs and the message agree, checked by a test that greps neither — assert the
  message content in a test and read the docs by hand.

### Deliverables

`modes.py`, `docs/optimization.md`, a test, `CHANGELOG.md` (**Fixed**).

---

# Item 3 — Warn on unrecognized configuration keys

### Approach

New shared helper (`inputs.py` is where config loading lives; put it there or in a
small `config.py`):

```python
def warn_unrecognized(block_name, config, recognized):
    """One warning naming the keys in ``config`` that nothing reads."""
```

Call sites, each owning its own key set (decision 6):

1. `modes.run_mode` — the `mode:` block. Recognized: `type`, `mode` (legacy alias),
   `output_file`, `sweep_output_file`, `resume`, plus the per-mode keys the
   store-consuming and `collect_training_data` modes read (`store`, `num_samples`,
   `sampler`, `seed`, `fidelity`, `variables`, `model_dir`, `target`, `holdout`,
   `variance`, `num_components`, `dose_transform`, `floor`, `n_jobs`, `bounds`,
   `num_starts`, `identifiability`, `identifiability_file`, `num_warmup`,
   `num_chains`, `dose_sigma`, `summary_file`, `output_dir`). **Read the mode
   functions to build this set — do not copy it from here**, since these are
   documented in `docs/yaml_reference.md` and the list above may already be stale.
   A per-mode set is better than one union: `resume` in a `train_surrogate` block
   should warn, and a union cannot say that.
2. `scalar_optimize` / `gp_parameter_sweep` — `xopt_parameters`. Recognized:
   `generator`, `generator_options`, `num_random`, `num_step`, `max_iterations`,
   `max_steps`, `tolerance`, `cost_budget`, `alotted_time`, `cost_function`,
   `fidelity_variable`, `save_model`, `mc_noisy_objective`, `bin_edges`,
   `improvement_threshold`, `patience`.
3. `Workflow.__init__` — `workflow_parameters`. Recognized: `workdir`,
   `workdir_mode`, `stage_mode`, `capture_output`, `dry_run`, `paths`.
4. `_make_vocs` — `vocs_parameters`. Recognized: `variables`, `objectives`,
   `constraints`, `observables`, `constants`.

### Verification (item 3 done when)

- `num_steps` (plural) warns, names the key, and lists `num_step` among the
  recognized ones. A **near-miss suggestion** ("did you mean `num_step`?") via
  `difflib.get_close_matches` is worth it here — the whole class of bug is typos.
- A `resume:` in a `train_surrogate` mode block warns; a `resume:` in a
  `parameter_sweep` block does not.
- **Every shipped example produces no warning.** This is the load-bearing test: it is
  what proves the recognized sets were built from the code rather than guessed, and
  the one most likely to fail on the first attempt. Assert it over the whole
  `examples/` directory, `incomplete/` included.
- A warning, not an error: the run continues and produces its output.
- `input_parameters` / `output_parameters` are never inspected.

### Deliverables

`inputs.py` (or a new `config.py`), `modes.py`, `workflow_graph.py`,
`tests/test_config_validation.py` (new), `CHANGELOG.md` (**Added**).

---

## Notes for whoever implements this

* **Order: 3, then 2, then 1.** Item 3 is additive and will surface any recognized-key
  set that is wrong while nothing depends on it yet. Item 1 last, because it is the
  one that changes what a job's exit status means and is best landed on its own.
* Item 3's shipped-examples test is the one that will find real work. Expect the
  first run to name keys that *are* read, in places the plan above did not list.
* The four `return None` paths in item 1 all predate the module/mode refactor — they
  are legacy-driver behaviour carried forward, not decisions made recently, so there
  is no design intent to preserve.
* Adjacent and deliberately **not** in scope: per-module key validation on the
  `workflow:` entries (needs each module's key set, and the modules accept legacy
  aliases), and making `max_iterations` work standalone (item 2's open decision).

## What this leaves for later

- **`max_concurrent`** — still the next feature after the resume work. Unaffected by
  any of this.
- **Named artifacts** — unrelated, and still what TEM3P is blocked on.
