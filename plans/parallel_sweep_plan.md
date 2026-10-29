# Parallel Sweep Evaluation — Implementation Plan

**Status: IN PROGRESS** — written 2026-10-27 on S3DF against `dev` at
`10fdfb3` (0.8.2 plus the unreleased `chi2_per_voxel`). Phase 1 done
2026-10-29 (`lume_ace3p/launch.py`, `tests/test_launch.py`); Phase 2 done
2026-10-29; Phase 3 done 2026-10-29; Phases 4–5 open.

Follow-on to `plans/evaluation_isolation_resume_plan.md`, which removed the
shared per-evaluation state and left `max_concurrent` as "a small follow-on
change against the seams this plan establishes", and to
`plans/failure_mitigation_plan.md`, whose `on_error` and per-module hashes are
what make a failed or half-finished concurrent point recoverable. This plan
delivers that follow-on: a `parameter_sweep` (and `single`-shaped) run that
evaluates **M points at once inside one Slurm allocation**, each in its own
slice of the allocation, with the result table unchanged.

Only the table modes are in scope. `scalar_optimize`, `gp_parameter_sweep` and
the inversion modes are sequential by construction and are exempt
(`Evaluator(max_workers=1)` stays).

Every claim below about current behavior was verified 2026-10-27 by reading
the code at the cited lines. The Slurm step-packing claims were verified by a
probe job on milano (job `42139699`, script and output at
`/sdf/scratch/users/d/dbizzoze/step_packing_probe/`, not in the repo).

---

## 1. Where the code stands

### 1.1 What the resume plan already delivered

The three concurrency hazards that plan named are gone:

1. **No shared run state.** [`Workflow.evaluate`](../src/lume_ace3p/workflow_graph.py#L771)
   builds a fresh `RunContext` with fresh module instances per evaluation
   (L823); the modes read `ctx.workdir` (e.g. [`_persist_field`](../src/lume_ace3p/modes.py#L581)),
   never `workflow.workdir`. `self.workdir` / `self.last_context` are still
   assigned (L811, L896) but nothing on the sweep path reads them.
2. **Order-independent table.** [`parameter_sweep`](../src/lume_ace3p/modes.py#L297)
   collects one `_PointResult` per point and [`_assemble`](../src/lume_ace3p/modes.py#L1958)
   sorts by `point_index`. The frame is identical whatever order points finish.
3. **No shared output file during the run.** The table is written once, by
   [`run_mode`](../src/lume_ace3p/modes.py#L267), after every point returns.
   Per-point manifests are written atomically (`os.replace`,
   [`write_state`](../src/lume_ace3p/state.py#L290)) into per-point workdirs.
   There is no file two workers both write.

Per-point failure (`on_error: skip` → NaN row, manifest records the failed
module) and per-step resume need no change: M partial points after a
wall-clock kill are M instances of the case resume already handles.

### 1.2 What is missing

1. **Step launch lines are built in four places**, none with step-packing
   flags: the solver at [`ACE3P.solver_command`](../src/lume_ace3p/ace3p.py#L368)
   (`<mpi> -n T -c C <opts> …`), acdtool at
   [`Acdtool._command_line`](../src/lume_ace3p/acdtool.py#L1105), meshconvert
   at [`Cubit.meshconvert`](../src/lume_ace3p/cubit.py#L126) with a hardcoded
   `--nodes=1 --ntasks=1`, and Geant4 at [`Geant4.run`](../src/lume_ace3p/geant4.py#L195).
   The Cubit mesher itself ([`Cubit.run`](../src/lume_ace3p/cubit.py#L115))
   runs **bare**, not under `srun`, so under concurrency every worker's Cubit
   would land on the allocation's first node.
2. **The sweep loop is serial** (L343–347).
3. **Subprocess output is teed to the job's stdout** by
   [`run_logged`](../src/lume_ace3p/logs.py#L71): with M workers the
   `output-%j.txt` interleaves M solvers' lines. Per-workdir `<module>.log`
   files are unaffected (one writer each).
4. **Nothing bounds a hung step.** A solver that stops making progress holds
   its point forever, serial or not.
5. **No allocation check.** Today the only check that `tasks × cores` fits
   the node is srun refusing the step ("More processors requested than
   permitted"), reported after the fact by
   [`exit_status_note`](../src/lume_ace3p/ace3p.py#L388).

Cubit license contention, the open question in the resume plan, is closed:
the S3DF Cubit (`/sdf/group/rfar/software/Cubit-16.12`) is the Sandia
build with no license server. Its wrapper appends to a shared
`cubit.log` usage file on every start; concurrent appends are harmless.

### 1.3 What the probe showed

Four concurrent `srun -n 12 -c 2 sleep 10` steps on one milano node
(120 usable CPUs, `--mem=480gb`):

| step flags | wall | behavior |
|---|---|---|
| none | 41 s | serialized |
| `--exact` | 41 s | serialized — a step takes the node's whole **memory** by default |
| `--exact --mem=0` | 10 s | packed; no per-step memory limit |
| `--exact --mem-per-cpu=4000` | 10 s | packed; each step in its own memory cgroup |
| `--overlap` | 10 s | packed; steps may share CPUs |
| 10 × `-n 12 -c 1 --exact --mem=0` | 10.5 s | 120 CPUs exactly filled |
| 11 × same (132 > 120 CPUs) | 21 s | the 11th **queued** and ran second; no failure |

Conclusions: (a) without step flags concurrency is impossible, so the flags
are the prerequisite for everything else; (b) `--exact --mem-per-cpu` is the
form to use — CPU budget enforced, per-worker memory isolation, an
out-of-memory solver kills only its own point; (c) Slurm queues an
oversubscribed step rather than failing it, so a wrong worker count degrades
to serialization — which is why §2.4 refuses rather than warns.
`--overlap` (NERSC's documented form) also packs, but with no isolation.
Perlmutter was not probed; its exclusive-node jobs set no memory limit, so
the `--mem=0` fallback (§2.4 check 5) is what runs there.

### 1.4 Orchestration libraries considered and not chosen

NERSC's workflow page recommends GNU Parallel, Parsl, FireWorks, Snakemake
and Maestro. For this problem: Parsl's `MPIExecutor` schedules whole nodes
only, so it cannot do the sub-node packing that is the main use case; its
`HighThroughputExecutor` needs the same srun step flags this plan adds, plus
a coordinator NERSC itself warns "might fail to cancel your allocation".
FireWorks needs a MongoDB provisioned by NERSC staff; S3DF offers none.
Dask has no notion of an MPI step. Flux is not installed on S3DF. None of
them is installed in either conda environment. Every one would duplicate
what the completion manifests already provide (per-point status, retry,
resume) while adding a dependency. The alternative of one sbatch per point
(a job array plus an assembling job) is kept as a possible later phase
(§5), not pursued now: S3DF caps arrays at 100 and the group runs one job at
a time.

---

## 2. Target design

### 2.1 The user-facing surface

One new mode key, `workers`, for `single` and `parameter_sweep`:

```yaml
workflow :
  - module : cubit
    journal : 'pillbox-rtop.jou'
  - module : omega3p
    input : 'pillbox-rtop.omega3p'
    tasks : 15
    cores : 4
    opts : '--cpu-bind=cores'
  - module : acdtool
    input : 'pillbox-rtop.rfpost'

mode :
  type : parameter_sweep
  workers : 4            # an integer, or 'auto'; default 1
  output_file : 'omega3p_sweep_output.txt'
```

- `workers: 1` (default): the serial path, **byte-identical command lines**
  to today. No baseline moves.
- `workers: N`: N points at once, each in its own slice (§2.3). Refused with
  a message listing the counts that would fit when N does not satisfy §2.4.
- `workers: auto`: the largest N that satisfies §2.4 for this allocation, so
  one YAML runs correctly under a one-node or a four-node batch file.
- `run-lume-ace3p --workers N <yaml>` overrides the key, for the same reason.

No new key in the batch file, and the YAML does not reference the batch
file. The coupling is one invariant, `workers × footprint ≤ allocation`,
checked at run time from the Slurm environment (§2.4). The batch file for
four half-node workers on two milano nodes is the shipped
`run_lume-ace3p_omega3p_sweep_s3df.batch` with `--nodes=2`; two two-node
workers is the same file with `--nodes=4` and `tasks: 240` in the YAML.

### 2.2 One step builder for every launch (Phase 1)

A new module `lume_ace3p/launch.py` with one function the four wrappers
and Cubit call:

```python
def step_command(mpi_caller, tasks, cores, opts, argv, slice=None):
    """The shell line that launches one external tool as one step."""
```

- `slice is None` (serial mode, or no MPI caller): returns exactly what each
  wrapper builds today — `'<mpi> -n T -c C <opts> <argv>'` for the solver,
  the acdtool form, Cubit's `--nodes=1 --ntasks=1` meshconvert form, and the
  **bare** Cubit mesher line. This is what keeps Phase 1 a no-behavior
  change; the existing per-wrapper command strings become test fixtures.
- `slice` given (a `Slice`, §2.3): returns
  `'<mpi> -N <nodes> -n T -c C --exact <mem flag> <opts> <argv>'`, and a
  **serial** step (`tasks` 1 or None: Cubit, meshconvert, serial acdtool,
  Geant4's single rank) is launched as `-n 1 -c <slice CPUs per node>` so it
  holds the whole slice — see decision 2. `--cpu-bind` in `opts` is kept
  (it is compatible with `--exact`).

The slice reaches the wrappers through `RunContext` (`ctx.slice`, default
`None`), the same way `paths` and `capture_output` do, so no module
signature changes beyond passing it into the wrapper constructors.

### 2.3 The slice model

A **slice** is the share of the allocation one worker owns for the whole of
its chain. Every step of that worker runs inside it; steps run one at a time
within a worker, so the slice's memory is available to each in turn.

Definitions, for a homogeneous allocation of `nodes` nodes with `cpn` CPUs
and `mem_per_node` MB each:

- **footprint** = max over the chain's modules of `tasks × cores` (Geant4:
  `1 × geant4_threads`; a module with no launch keys counts 1). This is the
  largest step, and it sizes the slice.
- **sub-node case** (`footprint ≤ cpn`): `per_node = cpn // footprint`
  workers fit on each node; a slice is `1` node, `footprint` CPUs,
  `footprint × mem_per_cpu` MB.
- **multi-node case** (`footprint > cpn`): `nodes_per_worker =
  ceil(footprint / cpn)`; a slice is that many whole nodes.
- `mem_per_cpu = mem_per_node // cpn` when the job carries a memory limit.

The Cubit question raised in review is settled by this model, not by a key:
if four ACE3P runs fit on a node, four Cubits fit on it too, each holding
its worker's 30 CPUs and quarter of the memory. A Cubit (or solver) that
needs more memory than its slice is one and the same situation — the user
lowers `workers`, and the run prints the per-worker slice
(`4 workers × (60 CPUs, 240 GB) on 2 nodes`) so that choice is informed.
The two-pass pattern the resume docs already describe (a Cubit-only sweep,
then the full chain with `resume: true`) gives a memory-hungry mesher a
whole node while the solvers still pack.

### 2.4 Runtime checks (Phase 2), in order, before the first point runs

1. **Slurm present?** No `SLURM_JOB_ID` and `workers > 1`: run the pool with
   `slice=None` (today's command lines) and print one line saying so. This
   is what the test suite and a laptop dry run see.
2. **Homogeneous nodes.** Parse `SLURM_JOB_CPUS_PER_NODE` (`120(x2)` form).
   More than one distinct CPU count → refuse: the slice model has no answer
   for a mixed allocation.
3. **Every step fits a slice.** Each module's `tasks × cores ≤ slice CPUs`
   (true by construction of `footprint`; the check is for the multi-node
   case, where `tasks` must be divisible across `nodes_per_worker`, and
   for a module whose `tasks` exceeds what `-N` can place).
4. **Containment and divisibility.** Sub-node case: `workers` must be a
   multiple of `nodes` and `workers / nodes ≤ per_node`. Multi-node case:
   `nodes` must be a multiple of `nodes_per_worker` and `workers ==
   nodes / nodes_per_worker`. Anything else — three workers on two nodes,
   two two-node workers on three nodes — is refused, and the message lists
   the counts that would pass (`2, 4, 6, 8` for a 60-CPU footprint on two
   nodes). `auto` picks the largest passing count.
5. **Memory known?** `SLURM_MEM_PER_NODE` or `SLURM_MEM_PER_CPU` present →
   `--mem-per-cpu=<mem_per_cpu>`. Neither (Perlmutter exclusive nodes) →
   `--mem=0`, with a printed note that memory is then not isolated between
   workers.
6. **Capacity.** `workers × footprint ≤ nodes × cpn`. Implied by 4, kept as
   the last line of defense because the probe showed the failure mode is a
   silent queue, not an error.

All of this is a pure function of `(modules, mode_cfg, environ)` →
`(workers, Slice)` or a `ValueError`, so it is unit-tested from a dict with
no Slurm present.

### 2.5 The pool (Phase 3)

```python
with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = [pool.submit(_run_point, workflow, i, ...) for i in range(N)]
    points = [f.result() for f in futures]
```

Threads, not processes: a worker spends its life in `subprocess.wait`, the
Python between steps is milliseconds, and threads share the `Workflow`
object the per-point `RunContext` already isolates. Point index is assigned
at **submission**, so workdir names (`indexed`, `auto`) are the serial
run's names and `resume` finds them.

Thread-safety audit of the per-point path (all read-only or per-workdir
except as noted): `_DIGESTS` in [`state.py`](../src/lume_ace3p/state.py#L401)
is a plain dict written under the GIL — a race only recomputes a digest;
`shutil.copy` of input files reads a shared source into a private workdir;
no `os.chdir` anywhere in `src/`; `os.getcwd()` (12 sites) is read-only.
`print` from worker threads is the one shared sink and is handled in §2.6.

`on_error: raise`: the first failure cancels the not-yet-started futures
(`shutdown(cancel_futures=True)`), lets running steps finish so their
manifests are complete, then raises. `KeyboardInterrupt` / Slurm's SIGTERM
at the wall clock: not caught, as today; children are in the job's steps and
Slurm kills them; partial manifests are what resume reads.

`single` with `workers > 1` is one point and runs as today; the key is
accepted so a YAML can move between the two modes unedited.

### 2.6 Output under concurrency (Phase 3)

With `workers > 1` the tee to the parent's stdout/stderr is switched off
(`run_logged(..., tee=False)` — the parameter already exists) and the sweep
prints **point events** instead, under one lock:

```
 - point 7 started (cav_radius=100.0, ellipticity=0.75) in lume-ace3p_omega3p_workdir_7
 - point 7 complete in 412 s
 - point 9 failed in 88 s: omega3p exited with status 7 — see lume-ace3p_omega3p_workdir_9/omega3p.log
```

Serial runs keep today's live solver output. The per-workdir logs are the
same in both cases. No YAML key: the behavior follows from `workers`.

### 2.7 Step timeout (Phase 4)

A per-module `timeout:` key (seconds; default none) passed to `run_logged`,
which kills the process group on expiry and returns a `CompletedProcess`
with a sentinel status so the wrapper raises `ToolExitError` naming the
timeout. The manifest records the module failed, `on_error: skip` gives
the point a NaN row, and `resume: true` re-runs it. Independent of
concurrency and valuable without it; placed here because a hung worker is
the one failure a pool would otherwise never report.

---

## 3. Design decisions

1. **Serial mode is byte-identical.** `workers: 1` produces today's command
   lines, today's tee, today's output. The step flags appear only with a
   slice. This is what lets Phase 1 land with no baseline change and keeps
   every shipped example's behavior.
2. **A serial step holds the whole slice.** Cubit, meshconvert, serial
   acdtool and Geant4 launch as `-n 1 -c <slice CPUs>`. Reserving CPUs that
   a single-threaded mesher will not use costs nothing (the slice is idle
   while it meshes) and buys the slice's full memory for the step; the
   alternative, `-c 1 --mem-per-cpu=4000`, caps Cubit at 4 GB and a dense
   mesh is killed by the cgroup. The tutorial already runs serial transwake
   as `srun -n 1 -c 256`.
3. **Workers never straddle nodes below the node size.** `-N
   <nodes_per_worker>` on every step. Without it `srun -n 15` spreads ranks
   over all allocated nodes and two half-node workers each run on both nodes.
4. **Refuse, don't warn, on a non-fitting worker count.** The probe shows
   the failure mode is silent queueing; a sweep that "works" at a third of
   the expected speed is worse than one that stops with the list of counts
   that fit.
5. **No per-module memory key.** The slice model gives every step the
   worker's memory share, which is the right answer for the ACE3P-bound
   case that motivates the feature. A `memory:` hint was considered and
   dropped: it would duplicate `workers` for one unusual case (a mesher
   needing more than a solver), which the two-pass resume pattern already
   covers.
6. **Threads, not processes, not a library.** §2.5 and §1.4.
7. **No change to the Xopt modes.** Their `Evaluator(max_workers=1)` and
   call-time iteration index stay; `plans/xopt_resume_workdir_plan.md`
   records what concurrency there would need (index at proposal time).
8. **The one-sbatch-per-point driver is a later phase, not this one.** The
   worker unit (`_run_point` into an indexed workdir, manifest as the
   record) is shaped so a `--point i` / `--assemble` CLI could reuse it; see
   §5.

---

## 4. Decisions to confirm before the phase that needs them

- **Phase 2:** the refusal in check 4 for the multi-node case requires
  `workers == nodes / nodes_per_worker` exactly. Allowing fewer (two
  two-node workers on six nodes) would idle nodes silently; proposed:
  refuse, same as the sub-node case. **Settled: refuse** (Phase 2).
- **Phase 3:** whether `collect_training_data` (an independent-sample loop,
  [`modes.py`](../src/lume_ace3p/modes.py#L856)) takes `workers` in the same
  phase. It is not sequential, so it is eligible; proposed: yes, since its
  loop body already calls `_evaluate` into a per-sample workdir, but the
  Geant4 campaign material stays out of the examples either way.
  **Settled 2026-10-29: yes**, in Phase 3 (the user's call).
- **Phase 3:** the point-event lines' exact wording and whether a progress
  line (`12 of 32 complete`) is printed per completion. **Settled
  2026-10-29:** wording is the implementer's choice; a progress line **is**
  printed, and it names what each worker is running at that moment (worker
  number and its input point), e.g.
  `12 of 32 complete; running: worker 1 point 13 (cav_radius=100.0), worker 2 point 14 (cav_radius=101.0)`.
- **Phase 4:** whether a timed-out step is distinguishable from a crashed
  one in `--status` (proposed: the manifest error string says `timed out
  after N s`, no new status value).

---

## Phase 1 — One step builder, no behavior change

**Done 2026-10-29.** The serial pins in `tests/test_launch.py` were run against
the pre-change wrappers first and pass there unchanged. The four historical
spellings are kept as `form`s of `step_command` (`SOLVER`, `ACDTOOL`,
`MESHCONVERT`, `BARE`; Geant4 uses `SOLVER`). A slice with no MPI caller falls
back to the serial spelling. Full suite on milano (job `42405576`): 1091
passed, 1 failed, and that failure, `test_the_examples_actually_cover_every_mode`,
also fails at `d9793e1`: no shipped example uses `collect_training_data`,
`train_surrogate`, `invert_optimize` or `invert_bayesian` since `13deade`
retired `geant4_beta_surrogate`.

### Approach

1. Add `lume_ace3p/launch.py`: `Slice` (a `NamedTuple`: `nodes`, `cpus`,
   `mem_flag`) and `step_command(...)` per §2.2.
2. Add `slice=None` to `RunContext`; thread it to `ACE3P`, `Acdtool`,
   `Cubit`, `Geant4` constructors (keyword, default `None`).
3. Replace the four command constructions and the bare Cubit line with
   `step_command` calls. With `slice=None` each returns the string the site
   builds today — assert this in a test that captures the current strings
   first (the fake-binary harness of `tests/test_resume.py` records the
   command line each fake receives).
4. `Cubit.run` with a slice launches the mesher under `srun`; without one it
   stays bare.

### Verification (Phase 1 done when)

- A test fixes the five serial command lines (solver, parallel acdtool,
  serial acdtool, meshconvert, Geant4) as they are today and passes.
- A test with a `Slice(1, 30, '--mem-per-cpu=4096')` shows every line
  gaining `-N 1 … --exact --mem-per-cpu=4096` and the serial ones `-n 1 -c 30`.
- Full `pytest` passes with no baseline change
  (`tests/baseline_utils.py` untouched).

## Phase 2 — Allocation model and sizing

**Done 2026-10-29.** `allocation_from_env`, `plan_workers`, `plan_summary` in
`launch.py`; `Module.launch_size()` gives each module's `(tasks, cores)`
(Cubit `(1, 1)`, a serial acdtool command one rank, Geant4 `(1, threads)`);
`modes.workers_of` / `modes.worker_plan`; `--workers N|auto` on the CLI.
`run_mode` sizes and prints the plan before the first point, but the points
still run serially until Phase 3. Settled while implementing:

- The §4 multi-node question went the proposed way: fewer workers than the
  nodes hold is refused (two two-node workers on six nodes lists `1, 3`).
- `workers: 1` returns `(1, None)` without reading the workflow or the
  environment, so no existing config or test double is touched.
- Under Slurm with `workers > 1` the MPI caller must be `srun` (the slice
  flags are srun step options); outside Slurm any caller is accepted.
- `auto` with nothing that fits runs serially rather than refusing.
- `workers` on any other mode is refused, not just warned about.

### Approach

1. `launch.allocation_from_env(environ)` → `Allocation(nodes, cpn,
   mem_per_node | None)` or `None` when not under Slurm; parses the
   `120(x2)` form; raises on heterogeneity.
2. `launch.plan_workers(modules, requested, allocation)` → `(workers,
   Slice)` implementing §2.3–2.4 with the refusal messages. `requested` is
   an int or `'auto'`.
3. `MODE_KEYS['single']` / `['parameter_sweep']` gain `'workers'`;
   `run_mode` validates the value (positive int or `'auto'`) before anything
   runs. `--workers` on the CLI.

### Verification (Phase 2 done when)

- Table-driven tests over `(footprint, nodes, cpn, requested)` cover: 4 on 2
  nodes (pass, 2 per node), 3 on 2 (refused, message lists 2/4/6/8), 2
  two-node workers on 4 nodes (pass), 2 on 3 nodes (refused), `auto` on
  each, no-Slurm (plan returns `(requested, None)`), heterogeneous
  (refused), no memory limit (`--mem=0` flag + note).
- The printed slice summary line is asserted once.

## Phase 3 — The pool, point events, tee off

**Done 2026-10-29.** `modes._run_pool(jobs, workers, run)` is the one pool,
used by `parameter_sweep` and both `collect_training_data` designs (DOE and
`per_bin`); `run_mode` hands it the `(workers, slice)` plan. Tests in
`tests/test_parallel_sweep.py`. Settled while implementing:

- `Workflow.evaluate` no longer reads `self.workdir` back after assigning it
  (it held whichever point started last under threads); the workdir is a local
  passed to `_resume_state` and the manifest writes.
- `workers > 1` refuses a sweep whose points resolve to one workdir (`manual`,
  or colliding `auto` names), naming `indexed`. Serially that is a warning.
- `tee` reaches the wrappers as `RunContext.tee`, beside `capture_output`.
- A worker failure under `raise` sets a stop flag before releasing its slot,
  so no queued point starts once the failure is known; running points finish.
- Event lines: ` - point 7 started (radius=100) in wd_7 (worker 2)`,
  ` - point 7 complete in 412 s` / ` - point 9 failed in 88 s: <first line>`,
  then ` - 12 of 32 done, 1 failed; running: worker 1 point 13 (radius=100), …`.
  The `Error:` block with the full message still goes to stderr.

### Approach

1. `parameter_sweep` takes `workers` and `slice`; `workers > 1` runs §2.5
   with `ctx.slice` set via a new `Workflow.evaluate(..., slice=...)`
   keyword (passed only when not `None`, like `workdir=` and `resume=`, so
   the test doubles keep working).
2. Point-event lines under a module-level lock; `tee=False` threaded from
   the mode layer to `run_logged` through `RunContext` (a `tee` attribute
   beside `capture_output`).
3. `on_error: raise` cancels pending futures as in §2.5.
4. `collect_training_data` takes `workers` too (confirmed): add it to
   `MODE_KEYS['collect_training_data']`, size it with `worker_plan`, and run
   its per-sample loop through the same pool and event lines. Its samples are
   indexed at submission, as sweep points are, so resume finds them.
5. Progress line (confirmed): after each completion or failure, one line
   with done/total and the in-flight workers with their input points (§4).
   Worker numbers are stable slots 1..N, not thread ids.

### Verification (Phase 3 done when)

- With the fake-binary harness and `workers: 3` over 7 points (no Slurm, so
  `slice=None`): the table is byte-identical to the serial run's; every
  `lume_ace3p_state.json` is complete; the fake solver's recorded start
  times overlap (at least two points in flight at once, using a `sleep` in
  the fake).
- A failing point under `skip` yields the same NaN row and `status` column
  as serial; under `raise` no point starts after the failure is observed.
- Killing the run mid-sweep and re-running with `resume: true` and
  `workers: 3` completes exactly the unfinished points (count the fake
  solver's invocations).
- Job stdout under `workers > 1` contains point-event lines, progress lines
  naming the in-flight workers and points, and no solver output; the
  per-workdir logs are complete.
- `collect_training_data` with `workers: 3` writes the same store as the
  serial run.
- The existing tests pass (1131 at Phase 2; the one known failure,
  `test_the_examples_actually_cover_every_mode`, predates this plan); no
  baseline change.

## Phase 4 — Step timeout

### Approach

`timeout` in `run_logged` (`Popen` with `start_new_session=True`; on expiry
`killpg`, join the pumps, return status `-9`); a `timeout:` launch key on
every external-tool module; `ToolExitError` message names the timeout; the
manifest `error` string says `timed out after N s`.

### Verification (Phase 4 done when)

A fake solver that sleeps past its `timeout` yields a failed module in the
manifest, a NaN row under `skip`, and a re-run that succeeds with a longer
timeout; the per-module `.log` ends with the timeout line.

## Phase 5 — Docs, examples, release

- `docs/parameter_sweep.md`: replace "Each workflow evaluation is run
  serially. Future versions may allow concurrent evaluations." with a
  **Running points in parallel** section: the `workers` key, the slice
  model in two sentences, the two batch-file variants (4 on 2, 2 on 4), the
  refusal message and how to read it, memory as the user's judgment.
- `docs/yaml_reference.md`: `workers`, `timeout`.
- `examples/omega3p_sweep`: a second batch file,
  `run_lume-ace3p_omega3p_sweep_s3df_4workers.batch` (`--nodes=2`), and a
  comment in the YAML showing `workers`. Validated on milano per
  `plans/s3df_example_validation_plan.md` (a two-node job: ask first, per
  the one-job rule).
- `CHANGELOG.md` under Unreleased; this plan's status to COMPLETE.
- Minor version bump (new keys, no behavior change at defaults): 0.9.0.

---

## 5. Out of scope

- Concurrency in the Xopt modes (decision 7).
- A driver outside the allocation: one sbatch or job-array task per point
  with `run-lume-ace3p --point i` and `--assemble` from manifests. The
  worker unit here is shaped for it; the trigger would be sweeps whose
  points differ widely in cost, or that outgrow one allocation's wall clock
  even with resume.
- Heterogeneous slices (a chain whose steps want different node counts
  concurrently): a worker runs its steps one at a time inside one slice.
- Solver-internal restart (T3P checkpointing), as before.
- Changing any shipped example's table.
