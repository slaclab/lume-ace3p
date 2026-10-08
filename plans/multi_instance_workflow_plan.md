# Module-Scoped Inputs and Per-Instance Artifacts — Implementation Plan

**Status: IN PROGRESS — Phase 0, Phase D and Phase 1 done 2026-10-08**, Phase 2 next. Written
2026-10-07 on S3DF. `derived_parameters` (Phase D) added the same day at
David's request; every §6 decision that gates a phase was taken the same day.
Follows `plans/track3p_module_plan.md` (COMPLETE) and lifts the restriction that
`plans/acdtool_rework_plan.md` design decision 3 put out of scope: artifact
identity is per *kind* today, and this plan makes it per *instance*. It reuses
the existing machinery deliberately — the `_SolverModule` hook pattern, the
build-time `WorkflowValidationError` that names the fix, the per-config
`warn_unrecognized` warning, the `config_hash` guard on resume — and invents one
new thing: a `from:` binding between a consumer and the producer it reads.

Every claim below about current behavior was verified 2026-10-07 by reading the
code at the cited lines and by throwaway probes against `lume-ace3p-dev`
(§2.4). Where a probe and the docs disagree, the probe wins and the
disagreement is recorded.

---

## 1. Motivation and scope

The user's question: *can arbitrary groups of ACE3P modules be chained in one
workflow, and how does the YAML keep their parameters apart?* Today the answer
is "one-of-each-kind only, and it does not keep them apart". Two limits, with
different roots:

**A. One `ace3p:` tree is merged into every solver.** `WorkflowInputs` holds a
single `ace3p` Section ([inputs.py:33](../src/lume_ace3p/inputs.py#L33)); every
`_SolverModule` calls `solver.set_value(ctx.inputs.ace3p)` with the whole of it
([modules.py:631](../src/lume_ace3p/modules.py#L631)); and `merge_overrides`
*appends* a container the target file lacks rather than skipping it
([ace3p.py:224-234](../src/lume_ace3p/ace3p.py#L224-L234)). So in a
`cubit → omega3p → t3p` chain, `EigenSolver.NumEigenvalues: 5` lands in the
`.t3p` file as a brand-new `EigenSolver` block (probe 1), and there is *no way
at all* to say "`FiniteElement.Order` is 1 for the eigensolve and 2 for the
time-domain run". The same is true of the `cubit:` bucket across two `cubit`
modules and the `geant4:` bucket across two `geant4` modules; nothing exercises
those today only because the DAG rule (B) forbids the second instance.

The bug is **latent, not theoretical**: every multi-solver example shipped
(`omega3p → track3p`, `omega3p → track3p → field_emission`) has an empty
`ace3p:` block. The first user to parameterize one will hit it.

**B. One producer per artifact kind.** `_resolve_order` rejects a second
producer of any kind
([workflow_graph.py:289-299](../src/lume_ace3p/workflow_graph.py#L289-L299)).
Verified rejections (probe 2): `omega3p + s3p` (both `em_solution`), two
`omega3p`, `acdtool(rf) + acdtool(transwake)` (both `rf_post`). Verified
acceptances: `cubit → omega3p → t3p → track3p → acdtool(rf)` and two disjoint
sub-chains in one `workflow:` list. So "arbitrary groups" works exactly when
the group is one-of-each-kind.

Everything downstream of (B) is also keyed by kind: `ctx.artifacts[kind]`,
`ctx.job_names[kind]`, `ctx.reparse[kind]`, `ctx.field_levels[kind]`
([modules.py:95-166](../src/lume_ace3p/modules.py#L95-L166)); consumers find
their producer with `next(m for m in ctx.modules if KIND in m.provides)`
([modules.py:1410](../src/lume_ace3p/modules.py#L1410),
[modules.py:2392](../src/lume_ace3p/modules.py#L2392)); and `output_parameters`
routes `module:` by **type** and takes `candidates[-1]`
([workflow_graph.py:898-909](../src/lume_ace3p/workflow_graph.py#L898-L909)).

**C. No quantity can be computed from the extracted ones.** `output_parameters`
names what to *extract*; an optimization objective like "distance of the
fundamental from 1.3 GHz" cannot be spelled, and arithmetic over columns is
pushed to a script after the fact (`examples/t3p_power_balance/power_balance.py`
says so in its docstring). Added 2026-10-07 at David's request; it is
independent of A and B and is its own phase (Phase D).

**In scope.** All three, plus the table-axis consequence (§3.5) that two solvers
in one chain already expose today. Two new examples on existing assets,
validated on S3DF.

**Out of scope.** TEM3P and `acdtool mesh deform` as a mesh producer (this plan
removes the *structural* blocker; wiring them is their own plan). PIC3P / Gun3P.
Concurrent evaluation (`max_concurrent`). Changing any shipped example's table.

---

## 2. Verified facts

### 2.1 How an override reaches a solver

| Step | Where | What it does |
|---|---|---|
| Load | [inputs.py:176-196](../src/lume_ace3p/inputs.py#L176-L196) | `input_parameters.ace3p` is cut out of the YAML **textually** and parsed as `(key, value)` pairs so duplicate keys (two `Port:` blocks) survive. Everything else is safe-YAML. |
| Build | [inputs.py:231-271](../src/lume_ace3p/inputs.py#L231-L271) | One `WorkflowInputs(cubit, ace3p, macro, particles)`; `build_inputs(yaml_data)` knows nothing about the module list. |
| Axes | [inputs.py:39-68](../src/lume_ace3p/inputs.py#L39-L68) | Labels are `name` (cubit/geant4/particles) or `ace3p:Sec.Leaf` (`Port[1].ReferenceNumber` for the second sibling). No module dimension. |
| VOCS | [inputs.py:87-126](../src/lume_ace3p/inputs.py#L87-L126) | Qualified labels are `cubit:x` / `ace3p:Sec.Leaf` / `geant4:x` / `particles:x`; bare names route when unique across buckets. |
| Apply | [modules.py:499-500](../src/lume_ace3p/modules.py#L499-L500), [modules.py:631](../src/lume_ace3p/modules.py#L631), [modules.py:2590](../src/lume_ace3p/modules.py#L2590) | Cubit, every ACE3P solver, and Geant4 each take the **whole** bucket. |
| Merge | [ace3p.py:206-234](../src/lume_ace3p/ace3p.py#L206-L234) | Positional by sibling index; a missing container or leaf is **created**. This is what the no-input-file case (`examples/s3p_sweep_no_s3p_file`) relies on, so it cannot simply be made strict. |
| Hash | [state.py:284-288](../src/lume_ace3p/state.py#L284-L288), [state.py:310-335](../src/lume_ace3p/state.py#L310-L335) | `config_hash` renders `WorkflowInputs` from `vars()` keyed by class name. **Any new attribute on the class changes every existing manifest's hash**, which would make every resumable campaign on disk read as `stale`. |

Module entries pass unknown keys through untouched — `totally_bogus_key: 42` on
an `omega3p` entry is accepted silently (probe 3;
[workflow_graph.py:938-954](../src/lume_ace3p/workflow_graph.py#L938-L954)).
No per-module recognized-key set exists today.

### 2.2 How a consumer finds its producer

| Consumer | Lookup | Line |
|---|---|---|
| `track3p` | first module providing `em_solution`; `ctx.job_names[em_solution]` | [modules.py:1407-1419](../src/lume_ace3p/modules.py#L1407-L1419) |
| `acdtool` | `ctx.job_names.get(spec.requires)`; `ctx.reparse[spec.mutates]()` | [modules.py:1888-1896](../src/lume_ace3p/modules.py#L1888-L1896), [modules.py:1948](../src/lume_ace3p/modules.py#L1948) |
| `field_emission` | `ctx.artifacts[track3p_particles]`, `ctx.job_names.get(...)`, `ctx.field_levels.get(...)`, first module providing the kind | [modules.py:2342-2393](../src/lume_ace3p/modules.py#L2342-L2393) |
| `geant4` | `ctx.artifacts[particle_source]` | [modules.py:2589](../src/lume_ace3p/modules.py#L2589) |
| manifest | `ctx.artifacts[kind]` / `ctx.job_names[kind]` for `kind in module.provides` | [workflow_graph.py:792-813](../src/lume_ace3p/workflow_graph.py#L792-L813) |
| build check | `producer[TRACK3P_PARTICLES]` | [workflow_graph.py:222-226](../src/lume_ace3p/workflow_graph.py#L222-L226) |

Module *names* are already per-instance in three places — the log file
([modules.py:287-299](../src/lume_ace3p/modules.py#L287-L299)), the manifest
entry, and the resume decision — and are validated unique
([workflow_graph.py:320-328](../src/lume_ace3p/workflow_graph.py#L320-L328)).
That is the identity this plan builds on. Nothing restricts the characters in a
name.

### 2.3 What two instances of one solver would collide on, on disk

- **Results directory.** Both default to `<type>_results`
  ([ace3p.py:528,672,1068,1670](../src/lume_ace3p/ace3p.py#L528)); `results_dir:`
  moves it for every solver but `t3p` (read-only there, see
  `docs/yaml_reference.md` "t3p module").
- **Input file.** `write_input` writes `<workdir>/<input basename>`
  ([ace3p.py:442-452](../src/lume_ace3p/ace3p.py#L442-L452)); two instances
  sharing `a.omega3p` with different overrides would overwrite each other. The
  method already takes an explicit filename.
- **Mesh.** `cubit` names the mesh from the journal's `export` statement
  ([modules.py:436-456](../src/lume_ace3p/modules.py#L436-L456)); two journals
  exporting the same name collide. `mesh` source modules stage by basename.
- **Log.** Already per name. **Manifest entry.** Already per name.

### 2.4 Probe runs (2026-10-07, `lume-ace3p-dev`, `/tmp/ace3p_probe`)

1. `merge_overrides` of `EigenSolver: {NumEigenvalues: 5}` into a `.t3p` tree
   appends an `EigenSolver` block. Leakage confirmed.
2. `Workflow(...)` on five chains — the acceptance/rejection table in §1.B.
3. An `omega3p` entry with an unknown key builds without a warning and keeps the
   key in `module.config`.

### 2.5 Where an extracted output goes, and why a derived one is cheap

`Workflow.evaluate` builds a flat `{name: value}` dict after the chain runs
([workflow_graph.py:636-649](../src/lume_ace3p/workflow_graph.py#L636-L649)),
records it in the manifest, and returns it. Everything downstream reads **that
dict by name**: the Xopt objective picks `vocs.output_names` out of it
([modes.py:1693-1705](../src/lume_ace3p/modes.py#L1693-L1705)); the sweep table
takes its columns from `workflow.output_spec.keys()`
([modes.py:1472](../src/lume_ace3p/modes.py#L1472),
[modes.py:1504](../src/lume_ace3p/modes.py#L1504)); a resume re-extracts and
compares against the recorded values
([workflow_graph.py:707-725](../src/lume_ace3p/workflow_graph.py#L707-L725)).
So a value computed *inside* `evaluate` from the extracted outputs is a
first-class output for free — VOCS objective or constraint, table column,
manifest entry, resume check — with one new seam and no change to the modes.

### 2.6 What the table layer does with two index axes today

`Workflow.field_index` ranks modules by `index_precedence` then DAG order and
returns the **first** axis ([workflow_graph.py:830-853](../src/lume_ace3p/workflow_graph.py#L830-L853));
`_rows_for_point` samples **every** array output at that axis's positions
([modes.py:1482-1490](../src/lume_ace3p/modes.py#L1482-L1490)) and `_sample`
passes a too-short array through unchanged rather than raising
([modes.py:1524-1532](../src/lume_ace3p/modes.py#L1524-L1532)). The
`omega3p + t3p` chain validates today, so a config declaring a whole-axis
output from each already produces a table whose T3P column is sampled by mode
index. Not a regression this plan introduces — a hole it must close before a
second S3P or Track3P instance makes it common.

---

## 3. Design decisions

### 3.1 Scoping lives under the bucket, keyed by module name

```yaml
workflow :
  - module : cubit
    journal : 'pillbox-rtop.jou'
  - module : omega3p
    name : coarse
    input : 'pillbox-rtop.omega3p'
    results_dir : 'coarse_results'
  - module : omega3p
    name : fine
    input : 'pillbox-rtop.omega3p'
    results_dir : 'fine_results'

input_parameters :
  cubit :
    'cav_radius' : {min: 0.095, max: 0.105, num: 3}
  ace3p :
    'ModelInfo' :                     # not a module name -> SHARED, as today
      'SurfaceMaterial' : { 'ReferenceNumber' : 6, 'Sigma' : 5.8e7 }
    coarse :                          # a declared module name -> SCOPED
      'FiniteElement' : { 'Order' : 1 }
    fine :
      'FiniteElement' : { 'Order' : 2 }
```

A key directly under `cubit:` / `ace3p:` / `geant4:` that **equals a declared
module name** and whose value is a mapping is a scope; every other key is
shared and behaves exactly as today. A module's effective inputs are
`shared ⊕ scoped[name]`, scoped winning at the same path.

Why under the bucket and not on the module entry:

- Swept inputs and VOCS variables are *the input space*, and the input space is
  `input_parameters`. Putting a `{min, max, num}` on a `workflow:` entry would
  split the space across two blocks and would land swept values inside
  `config_hash`'s `entries`, where a nominal value is not supposed to matter.
- The duplicate-key-preserving loader is textual and only covers
  `input_parameters.ace3p`. An `ace3p:` mapping on a module entry would go
  through safe YAML and lose its second `Port:` block silently.
- It generalizes for free: the same rule serves `cubit:` (two meshes) and
  `geant4:` (two dose runs). `particles:` is not scoped — `field_emission`
  reads its variables *by name* (`beta_input` / `beta_inputs`), so two such
  modules simply name different variables.

The ambiguity is a module whose name equals an ACE3P container (`ModelInfo`).
Default names are the lower-case types, the solver vocabulary is CamelCase, and
the rule is *module names win*. Validation adds a hard error for a module name
that matches a top-level container of its own input file, and names must match
`[A-Za-z0-9_-]+` (new; every shipped name passes).

### 3.2 Shared containers still merge everywhere; leakage is diagnosed, not forbidden

Making shared containers merge only into files that already have them would fix
leakage silently — and break the no-input-file case and the legitimate "add a
`Port` block" case. Instead, at build time, in a workflow with **two or more**
ACE3P modules: a shared top-level container absent from at least one of their
input files warns, naming the solvers it would be appended to and the scoped
spelling that targets one. With one ACE3P module nothing changes. (The strict
variant is decision 2 in §6.)

### 3.3 Labels gain a scope segment; unscoped labels do not change

| Today | After (scoped) |
|---|---|
| `cav_radius` | `cubit:fine/cav_radius` |
| `ace3p:FiniteElement.Order` | `ace3p:fine/FiniteElement.Order` |
| `nthreads` | `geant4:dose2/nthreads` |

`/` is forbidden in module names (3.1) and does not occur in ACE3P keys, which
the parser delimits on `:` and braces. Shared axes keep their current labels
**byte for byte**, so no existing baseline column, `point.axes` record, or VOCS
file moves. The VOCS registry (`_route_registry`) adds the scoped labels and
lets a bare leaf name route when it is unique across buckets *and* scopes.

### 3.4 Artifacts are keyed by `(kind, producer name)`; a consumer binds with `from:`

```yaml
  - module : track3p
    input : 'Pillbox-fe.track3p'
    from : fine                       # shorthand: one required kind
  - module : acdtool
    name : rf_coarse
    input : 'pillbox-rtop.rfpost'
    from : { em_solution : coarse }   # full form
```

- **Resolution.** For each `kind` a consumer requires: an explicit `from:`
  names the producer (must exist and must provide the kind); otherwise, exactly
  one producer of that kind in the list is taken implicitly; several with no
  `from:` is a `WorkflowValidationError` listing them and the key. DAG edges
  come from the *resolved* producer. Two producers of one kind with **no**
  consumer is legal (two eigensolves tabulated side by side).
- **Carrier.** `RunContext.artifacts`, `job_names`, `reparse`, `field_levels`
  become instances of one small `ByProducer` table. It is dict-like for the
  82 hand-built contexts in `tests/test_modules.py`: `table[kind]` returns the
  entry when exactly one producer recorded that kind, raises `KeyError` on
  none, and raises an ambiguity error naming the producers on several;
  `kind in table` and `.get` follow. The exact form is
  `table.of(kind, producer)`. Modules record with `ctx.provide(self, kind, value)`
  (and the job-name/reparse/level equivalents); a bare `table[kind] = value`
  still works and records an anonymous producer, for tests.
- **Consumers ask through a binding.** `_resolve_order` stores
  `module.sources = {kind: producer_name}` on every instance (prototypes and
  live lists resolve identically, since validation is deterministic). A new
  `Module.upstream(ctx, kind)` returns `(producer module or None, artifact
  path)` via `sources`, falling back to the single anonymous entry when the
  context was hand-built. The six lookups in §2.2 move onto it; no module keeps
  a `next(m for m in ctx.modules …)`.
- **`output_parameters` `module:` accepts a name.** Names default to types, so
  `module: omega3p` keeps working for every shipped config. Resolution: a name
  match first; else a type match; a type that matches several instances is an
  error listing their names. The bare forms (`_infer_output_module`) resolve to
  a type and follow the same rule.
- **Disk collisions become validation errors** (§2.3): two ACE3P modules of one
  type must resolve distinct results directories; two `cubit` modules must
  export distinct mesh names. Two ACE3P modules sharing an input basename is
  *allowed* — it is the p-refinement use case — and the module writes its copy
  as `<stem>_<name><ext>` whenever another ACE3P module in the chain shares the
  basename, otherwise as today.

Why not drop kinds and use names only: kinds are what let `acdtool` refuse to
run RF postprocessing on a T3P result. The kind stays the *type check*; the name
becomes the *identity*.

### 3.5 One table axis, declared by its owner; off-axis arrays are an error

The table's index axis is chosen as today (precedence, then DAG order). New: an
array output extracted from a module whose own `field_index` is not the table's
owner raises, naming the two modules, both axes, and `at:`. `_sample`'s
pass-through of a too-short array is removed in favor of that check. This also
closes the existing `omega3p + t3p` hole (§2.5). Two instances of one solver
(two `Frequency` axes) are compared by module identity, not label.

### 3.6 `derived_parameters`: expressions over outputs, evaluated in the workflow

```yaml
output_parameters :
  'mode_freq' : {module: omega3p, quantity: Frequency, at: {mode: 0}}
  'R/Q'       : {module: acdtool, section: RoverQ, quantity: RoQ, at: {mode: 0}}

derived_parameters :
  'f_target' : 1.3e9                              # a constant
  'f_error'  : 'abs(mode_freq - f_target)'        # expression over outputs
  'f_ppm'    : '1e6 * f_error / f_target'         # earlier derived names usable
  'RoQ_norm' : '`R/Q` / 100'                      # backticks quote a non-identifier name
  'balance'  : {python: 'power_balance:balance'}  # escape hatch: module:callable

vocs_parameters :
  objectives : { 'f_error' : MINIMIZE }
```

- **A new top-level block, not a spec shape inside `output_parameters`.** The
  two blocks answer different questions (*extract what* vs *compute what*), the
  extraction router (`_infer_output_module`) would otherwise have to learn to
  recognize a string that is an expression rather than a quantity, and a
  separate block keeps `output_parameters` exactly what the hash and the modules
  already treat it as.
- **Evaluated inside `Workflow.evaluate`**, after the extraction loop and
  before `record_outputs`, so derived names land in `ctx.outputs`, the manifest,
  the table and the VOCS lookup with no change to the mode layer (§2.5). A
  derived name that collides with an `output_parameters` name is a validation
  error.
- **An `ast`-based evaluator, never `eval`.** Allowed: numeric literals,
  names, `+ - * / ** %`, unary minus, comparisons, `and/or/not`, calls to an
  allowlisted table — `abs, sqrt, exp, log, log10, min, max, sum, mean, where,
  clip, nan` — implemented over numpy so an array output (a whole mode axis, a
  spectrum) works elementwise and reduces with `min`/`max`/`mean`. Everything
  else (attribute access, subscripts, lambdas, imports, any other call) is
  rejected at build time naming the node. Backticks quote names that are not
  Python identifiers (`R/Q`, `S(0,0)`), the pandas `query` convention.
- **Resolved at `Workflow.__init__`.** Every expression is parsed and every name
  checked against `output_parameters ∪ earlier derived names ∪ constants`; a
  misspelling or forward reference fails before the first mesh is built. Order of
  declaration is evaluation order; no forward references, no cycles possible.
- **Constants** are scalar leaves; they are hashed, which is what makes
  "retarget the frequency" a *different* campaign rather than one that resumes
  into a stale `xopt_state.yml` (§3.7).
- **`{python: 'module:callable'}`** is the escape hatch for what an expression
  cannot say. The callable's signature is pinned to `(outputs: dict) -> value`
  and it sees **nothing else** — no `ctx`, workdir or input values — so it stays
  a pure function of the outputs and its *source text* is a sufficient identity
  to hash; an edited function invalidates the campaign the way an edited
  expression does. The module is imported from `sys.path` plus the config
  file's directory. It is a documented second-class citizen: no build-time name
  checking is possible, and a `--status` walk never imports it. The expression
  grammar is kept small on purpose; a function is promoted into it only once the
  hatch has absorbed the same need twice.
- **Table placement.** Derived columns follow the extracted ones, in declaration
  order. An array-valued derived output rides the table axis of the output(s)
  it was computed from (§3.5's owner check applies) and a scalar one is a
  scalar; `_table_index`'s "every output narrowed" rule counts derived outputs.
- **Resume.** `_compare_recorded_outputs` compares derived values too; they are
  deterministic over the re-extracted inputs, so drift there is the same
  nondeterminism signal it is today.
- **Not a plotting or post-run tool.** `power_balance.py` keeps its plot; its
  `add_balance` becomes a one-line `derived_parameters` entry and the script
  reads the column instead of computing it.

### 3.7 Hash stability for every configuration that exists today

`config_hash` must not move for an unscoped config, or every resumable campaign
on disk reads as `stale` after upgrading. `_canonical` gains one hook — an
object with a `canonical()` method is rendered by it — and
`WorkflowInputs.canonical()` returns the pre-plan shape when no scopes are
declared. Phase 0 pins literal hashes for three shipped examples so this is
enforced, not hoped for. `entries` already carry `name:` and will carry `from:`,
which is right: adding a binding *is* a different campaign.

The same rule holds for `derived_parameters`: the payload gains a `derived` key
**only when the block is declared**, so a config without one hashes exactly as
before; with one, both `config_hash` and `campaign_config_hash` cover the
expressions, the constants and the source text of any `python:` callable.

### 3.8 What this does not change

Mode layer loops, Xopt driving, resume semantics (per name already), `stage_mode`,
`capture_output`, the field artifact, and the `field_emission` / `geant4` /
`track3p_source` / `particle_source` behavior for single-instance chains. Every
shipped baseline stays byte-identical (Phase 4 verifies).

---

## 4. Phases

Each phase lands green on the full suite and alone is shippable. Phases 1 and 2
are independent and may be done in either order; 3 needs 2; 4 needs 1–3.
Phase D (derived parameters) depends on nothing but Phase 0's hash pins and may
be done first; its own example is in Phase 4.

### Phase 0 — Characterization and pins (no `src/` changes)

**Objective.** Record today's behavior where this plan changes it, and pin what
must not move.

1. `tests/test_state.py`: pin the literal `config_hash` of point 0 of
   `omega3p_ace3p_param_sweep`, `s3p_sweep` and `t3p_power_balance` (staged via
   `baseline_utils._stage_example`, `dry_run`), plus the `campaign_config_hash`
   of `s3p_optimization`. Computed once on current `dev`, committed as literals,
   with a comment saying which plan they guard.
2. `tests/test_inputs.py`: a test showing the leak — one `ace3p:` override,
   two solvers, the `.t3p` tree gains the container. Marked
   `xfail(strict=True)` with the reason naming Phase 1, so it flips to a failure
   when fixed and is rewritten then.
3. `tests/test_modes.py`: the §2.5 misalignment — `omega3p + t3p` dry chain with
   fake parsed output, whole-axis outputs from both, assert the T3P column is
   sampled on `ModeID`. Same `xfail(strict=True)`, naming Phase 3.
4. `tests/test_workflow_graph.py`: the five probe-2 chains as a parametrized
   acceptance/rejection table, so Phase 2 rewrites a visible truth table rather
   than individual messages.

**Acceptance.** Suite green; nothing under `src/` touched.

### Phase D — `derived_parameters`

**Objective.** An optimization can target a computed quantity; a sweep table
can carry arithmetic over its columns. No shipped table changes.

1. New `src/lume_ace3p/derived.py`
   - `parse_expression(text) -> Expr`: `ast.parse(mode='eval')` after
     backtick-quoted names are rewritten to safe placeholders; a whitelist
     walker rejects any node outside §3.6's set, naming the node and the
     expression. `Expr.names` is the set of free names.
   - `Expr.evaluate(namespace) -> value`, numpy-backed; the function table is a
     module-level dict so a test can enumerate it and the docs can cite it.
   - `DerivedSpec.from_config(block, output_names)`: constants, expressions and
     `python:` callables in declaration order; validates names, collisions with
     `output_parameters`, forward references and non-identifier names without
     backticks; loads a `python:` target and keeps its source text for the hash.
   - `DerivedSpec.compute(outputs) -> dict` and `.canonical()`.
2. `workflow_graph.py`: `Workflow.__init__` takes `derived_spec`;
   `from_config` reads `yaml_data.get('derived_parameters')`; `evaluate`
   computes it after the extraction loop; `_route_output` is untouched.
   `TOP_LEVEL_KEYS` gains the block name ([inputs.py:168](../src/lume_ace3p/inputs.py#L168)).
3. `state.py`: `config_hash` / `campaign_hash` take an optional `derived`
   payload, omitted from the payload when `None` (§3.7).
4. `modes.py`: the table column list reads `output_spec` **then**
   `derived_spec`; `_table_index` and the §3.5 owner check count derived outputs;
   `_objective_from_workflow`'s "declare them in output_parameters" message
   mentions `derived_parameters` too. `--status` needs nothing (it never
   evaluates).
5. Tests (`tests/test_derived.py`): every allowed node and every rejected one
   (attribute, subscript, lambda, import, unknown call, walrus); backtick names;
   array elementwise + reduction; constant-only block; forward reference error;
   collision error; the `python:` hatch including a source-text hash change;
   the three Phase-0 pinned hashes **unchanged** with no block, **changed** when
   a constant moves; a dry-run `single` whose derived column is NaN; a
   `scalar_optimize` with a derived objective on the fake-solver fixture.
6. Docs: `yaml_reference.md` gains a `derived_parameters` section (grammar,
   function table, the hatch, hashing), `optimization.md` "Optimizing other
   workflows" shows the frequency-targeting objective, `power_balance.py`'s
   docstring and `examples/t3p_power_balance/README.md` lose the "cannot be
   expressed" sentence.

**Acceptance.** Baselines byte-identical; pinned hashes unchanged; the
power-balance example's table unchanged *until* Phase 4 adds the column.

**Done 2026-10-08.** As planned, with these deviations:
- `Workflow.output_names` (output_parameters then derived_parameters) is the one
  list `_rows_for_point` and `_frame` read. `_table_index` needed no change:
  derived values are in `outputs`, so an array-valued one goes long like an
  extracted one. The §3.5 owner check is Phase 3's.
- The `python:` hatch's source is read with `ast` from the file `PathFinder`
  locates, **without importing**; the import happens on the first `compute`, and
  the module is not registered in `sys.modules`. A target bound other than by
  `def`/`class` hashes the whole file.
- An empty `derived_parameters: {}` is treated as absent (hash unchanged).
- `from_config(yaml_data, config_dir=None)`: the CLI passes the config file's
  directory for the hatch's lookup.
- `power_balance.py` / README now point at the one-line entry, but the example
  config still does not declare it (Phase 4).

### Phase 1 — Module-scoped input parameters

**Objective.** `cubit → omega3p → t3p → track3p` is parameterizable per module;
no behavior changes for any config without scopes.

1. `inputs.py`
   - `WorkflowInputs(…, scoped=None)` where `scoped = {module_name: {'cubit':
     dict, 'ace3p': Section, 'macro': dict}}`; `for_module(name)` returns a
     `WorkflowInputs` whose three scoped buckets are `shared ⊕ scoped[name]`
     and whose `particles` is shared. `materialize`, `apply_overrides`, the
     setters and `_clone_section` extend to scoped leaves.
   - `sweep_axes` / `_route_registry` emit the §3.3 labels for scoped leaves;
     shared labels unchanged.
   - `build_inputs(yaml_data, module_names=())` splits scopes from shared;
     called with `()` it behaves exactly as today. A scope-shaped key under
     `particles:` warns (not scoped, §3.1).
   - `canonical()` per §3.6.
2. `workflow_graph.py`
   - `from_config` passes the entry names. Module-name validation (§3.1).
   - The §3.2 leakage warning, built from each ACE3P module's input tree (the
     `_validate_impacts_layout` precedent for opening inputs at build time;
     unreadable file → skip).
   - `_point_record` and `_getworkdir`'s `auto` suffix include scoped cubit /
     particles values.
3. `modules.py`: `CubitModule`, `_SolverModule`, `Geant4Module` read
   `ctx.inputs.for_module(self.name)` instead of `ctx.inputs`. The dry-run
   marker prints the per-module view.
4. `state.py`: the `canonical()` hook in `_canonical`.
5. Tests: the Phase-0 xfail flips and is rewritten as the positive test;
   scoped-vs-shared precedence at the same path; a scoped block for a module
   that is not an ACE3P/cubit/geant4 consumer warns; scoped sweep axis label
   and table column; VOCS routing of `ace3p:fine/Path` and of a bare leaf
   unique across scopes; the hash pins still pass; `--status` column names for
   a scoped sweep.
6. Docs: `yaml_reference.md` (`input_parameters`, `input_parameters.ace3p`,
   `vocs_parameters`), `workflow_inputs.md` ("ACE3P input files"),
   `parameter_sweep.md` where the label grammar is shown
   ([line 145](../docs/parameter_sweep.md#L145)).

**Acceptance.** All baselines byte-identical (`tests/test_baseline_selfcheck.py`);
the three pinned hashes unchanged; the leak test passes positively.

**Done 2026-10-08.** As planned, with these deviations:
- `for_module(name)` returns the object itself when the module has no scope, so
  an unscoped chain hands every module the very `WorkflowInputs` it did before.
- `canonical()` is §3.7's hook (the §3.6 reference in item 1 was a typo); it adds
  a `scoped` key only when a scope is declared.
- A `{min, max, num}` mapping named like a module is a shared range, not a scope.
- The scope checks run in `Workflow.__init__` (`_validate_scopes`), not in
  `_resolve_order`, because they need the inputs. The container-name check
  covers ACE3P solvers only, since only they have an input tree to collide with.
- `_point_record` records scoped `cubit` / `geant4` scalars; the `auto` workdir
  suffix adds scoped `cubit` values (the shared rule names by cubit + particles).

### Phase 2 — Per-instance artifact identity and `from:`

**Objective.** Two instances of any module type coexist; consumers bind
explicitly or unambiguously.

1. `modules.py`
   - `ByProducer` table (§3.4); `RunContext` uses it for the four side tables;
     `ctx.provide(module, kind, path)`, `ctx.set_job_name`, `ctx.set_reparse`,
     `ctx.set_field_level`. Every `ctx.<table>[kind] = …` in `src/` moves to
     the recording methods (12 sites, listed in §2.2 plus the source modules
     and `Geant4Module`).
   - `Module.sources` (default `{}`), `Module.upstream(ctx, kind)`. The six
     consumer lookups move onto it. `AcdtoolModule._resolve_jobname` and the
     `mutates` reparse use the bound producer.
   - `_SolverModule`: per-instance input-file name when a basename is shared
     (§3.4), passed to `write_input`.
2. `workflow_graph.py`
   - `_build_entry` pops `from:` (string or mapping) onto the module.
   - `_resolve_order`: producers become `{kind: [indices]}`; the
     duplicate-producer error is replaced by the §3.4 resolution; bindings are
     stored on the instances; the name-uniqueness check moves **first** and its
     message gains "two `<type>` entries need distinct `name:` keys"; the
     impacts-layout check uses the consumer's binding; new disk-collision
     checks (§2.3/3.4).
   - `_route_output` by name, then type (§3.4); `_module_record` reads the
     module's own entries.
3. `acdtool.py`: `_MESH_PRODUCER_NOTE` and the `mesh deform` note no longer
   cite the one-producer rule; they say the producer wiring is a follow-on.
4. Tests: the Phase-0 truth table flips (`omega3p + s3p` → accepted; two
   `omega3p` without names → name error; with names and a `track3p` lacking
   `from:` → ambiguity error listing both; with `from:` → accepted; two acdtool
   steps → accepted); `from:` naming a module that does not provide the kind;
   `from:` with a shorthand string on a two-requirement module → error; the
   `ByProducer` dict-compat contract; per-instance manifest `job_name`; a resume
   of a two-instance chain restarting at the second instance; output routing by
   name and the type-ambiguity error; per-instance input-file naming; the
   results-dir and mesh-export collision errors. The four existing tests whose
   messages change (`test_two_acdtool_steps_are_rejected`,
   `test_two_t3p_solvers_rejected`, `test_two_mesh_sources`,
   `test_the_duplicate_producer_diagnosis_wins_over_the_duplicate_name`) are
   rewritten against the new rule, not deleted.
5. Docs: `yaml_reference.md` "`workflow:`" (the one-producer sentence, the
   `from:` key, the module table's Requires column), the `acdtool`/`t3p`
   paragraphs that say "two `t3p` entries is a duplicate-producer error",
   `track3p_reference.md` [line 231](../docs/track3p_reference.md#L231), the
   `_SolverModule`/`workflow_graph` module docstrings that describe the rule.

**Acceptance.** Baselines byte-identical; hash pins unchanged (no shipped config
declares `from:`); every shipped example builds without `from:`.

### Phase 3 — One table axis, declared and enforced

1. `workflow_graph.py` / `modes.py`: `Workflow.field_index` also returns the
   owner; `_rows_for_point` raises on an array output whose extracting module
   is not the owner (§3.5); `_sample` loses its pass-through for arrays.
2. Tests: the Phase-0 misalignment xfail flips to the error; two S3P instances
   with every output narrowed stay wide and persist **both** spectra in the
   field artifact (keyed by module name); two S3P instances with one whole-axis
   output go long on that instance's axis.
3. Docs: `yaml_reference.md` "Results" and the T3P "One index axis per module"
   note generalize to "one index axis per table".

### Phase 4 — Example, baseline, S3DF validation

1. New `examples/omega3p_p_refinement/`: `cubit → omega3p(coarse, Order 1) +
   omega3p(fine, Order 2)` on the existing `pillbox-rtop` assets, a 3-point
   `cav_radius` sweep, outputs `f_coarse` / `f_fine` / `Q_coarse` / `Q_fine`
   narrowed with `at: {mode: 0}`, plus a `track3p`-free second variant is **not**
   added (decision 5). README states the point: the scoped block *is* the
   example. Dry-run baseline frozen (Omega3P has no axis under dry run, so the
   table is wide and freezable); one real S3DF run (milano, `rfar:regular`,
   `--constraint=OS_VER:8.10`, `source ~/ace3p.sh` before `conda activate
   lume-ace3p-dev`) before freezing.
2. New `examples/omega3p_frequency_target/`: `cubit → omega3p` on
   `pillbox-rtop`, `scalar_optimize` over `cav_radius` with
   `derived_parameters: {f_target: 1.3e9, f_error: 'abs(mode_freq - f_target)'}`
   and `f_error: MINIMIZE`. The optimization counterpart of
   `omega3p_dispersion_sweep` (no acdtool). Not frozen (Xopt tables are not
   baselines; `omega3p_optimization` sets the precedent); one real S3DF run.
3. `examples/t3p_power_balance`: `P_balance` moves into `derived_parameters`;
   `power_balance.py` reads the column. **Its baseline moves** — one new column
   appended; the existing columns are byte-identical. Refrozen from the existing
   validated run's table plus the computed column, no new S3DF run needed.
4. Full `pytest` on milano (not `iana`), per the standing rule. (A cross-solver
   `omega3p → t3p` example was considered and declined — §6.)

### Phase 5 — Docs, changelog, plan status

`CHANGELOG.md` [Unreleased] → 0.7.0 entry; `docs/configuration_by_mode.md`
line 88; `docs/troubleshooting.md` gets "my override went into the wrong
solver" and "two instances need `name:` and `from:`"; this plan's status
header; the `acdtool_rework_plan.md` decision-3 paragraph gets a pointer here.

---

## 5. Order and dependencies

```
Phase 0 ──► Phase D ──┐
       ├──► Phase 1 ──┼──► Phase 3 ──► Phase 4 ──► Phase 5
       └──► Phase 2 ──┘
```

Phase D is the smallest and the most immediately useful (a frequency-targeting
optimization is a real study today), so do it first. Phase 1 alone is also
worth shipping: it fixes the live leak for the chains that validate today.
Phase 2 is of little use without 1 (two instances with identical inputs). Order:
D, 1, 2, 3, 4, 5.

---

## 6. Decisions needed from David before the phase that uses them

| Phase | Decision | Recommendation |
|---|---|---|
| 1 | Where scopes live: under the bucket (`ace3p: {fine: …}`) or on the module entry (`- module: omega3p; ace3p: {…}`) | **DECIDED 2026-10-07: under the bucket** — §3.1; the entry form loses duplicate `Port:` blocks and puts swept values in `entries` |
| 1 | Shared container absent from a solver's file in a multi-ACE3P chain: warn, or merge only into files that have it | **DECIDED 2026-10-07: warn** — strict merging breaks the no-input-file case; the scoped form is the precise tool |
| 1 | Scoped label grammar | `ace3p:fine/FiniteElement.Order`, `cubit:fine/radius` — unscoped labels unchanged |
| 2 | Binding key spelling | `from:` — reads as prose in the YAML (`from: fine`); `source:` collides with the `*_source` module names |
| D | Derived quantities: a separate `derived_parameters` block, or an expression-shaped spec inside `output_parameters` | **Separate block** — §3.6; keeps the extraction router and the hash's `output_parameters` payload untouched |
| D | Ship the `python:` escape hatch in Phase D, or expressions only | **DECIDED 2026-10-07: ship it**, signature pinned to `(outputs: dict) -> value` — no `ctx`, workdir or inputs, so the source-text hash stays a sufficient identity. The grammar stays small on purpose; a function is promoted into it only once the hatch has absorbed the same need twice |
| 4 | Examples: p-refinement + frequency target, or also a cross-solver `omega3p → t3p` case | **DECIDED 2026-10-07: the two** — two real runs, existing assets, every mechanism exercised; the cross-solver case needs a new `.omega3p` nobody has validated |
| 1 | Hash stability for unscoped configs is a hard requirement (pinned literals) | **Yes** — a user mid-campaign must not see every point go `stale` on upgrade |

---

## 7. Test and validation policy

- Unit tests run without ACE3P (dry run, fake solvers, the hand-built
  `RunContext` convention), as for every module.
- **One shipped baseline moves, by one appended column.** `t3p_power_balance`
  gains `P_balance` in Phase 4; every other baseline is asserted byte-identical
  in every phase, and the three pinned hashes are the second guard. The one new
  frozen baseline is `omega3p_p_refinement` (Phase 4), after one real run;
  `omega3p_frequency_target` is an Xopt example and is not frozen.
- Full `pytest` goes to milano via sbatch, one job at a time, with the
  `threadpool_limits(1)` fixture already in `tests/conftest.py`.
- The §2.4 probes are not tests; the Phase-0 characterizations are their
  permanent form.
