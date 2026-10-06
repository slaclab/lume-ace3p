# Plan: pin threads and settle the milano pytest slowdown

**Status: DONE 2026-10-06 — verdict: oversubscription confirmed.** Five pinned
EL8 runs (§4 step 2 plus two node-targeted extras) all landed 6:17–7:25 wall and
7:10–8:02 CPU, against 12:14–12:25 CPU for the previously *fast* unpinned runs.
Decisive run: sdfmilan265, the 25:13 / 1:14:59 node, did 7:25 / 8:02 pinned on
the same node, which rules out the §4 "node is the cause" branch — no S3DF
ticket needed. The `threadpool_limits(1)` autouse fixture landed in
`tests/conftest.py`; full suite with the fixture alone (no `PIN=1`) was 5:34
wall / 6:56 CPU, 783 passed / 2 skipped. Deviations from the plan as written are
noted inline below.

Written 2026-10-05 for a separate session to execute. Everything needed is in
this file; the memory note `pytest-suite-slowdown-2026-10-01` holds the longer
history and should be updated at the end (§6).

## 1. The problem in one paragraph

The full suite (`python -m pytest tests/`) on one milano node, 8 CPUs, 32 GB,
takes either ~5:40 or ~21–25 minutes on the **same tree**. Nine logged runs
since 2026-09-14; two were slow. Slurm accounting shows the slow runs burned
6–7× the CPU time for 4× the wall (fast: ~12 CPU-min, slow: 75–86 CPU-min), the
penalty is spread across many tests rather than concentrated in the long
Gaussian-process tests, and both slow runs were on RHEL 8.10 nodes while all
three RHEL 9.6 runs were fast (two EL8 runs were also fast). Extra CPU time with
no extra work is the signature of threads spinning. The dev env's OpenBLAS
(0.3.32, pthreads build) defaults to `nproc` = 128 threads on a milano node
inside an 8-core cpuset, and scikit-learn bundles its own `libgomp`, so two
thread pools fight over 8 cores. The batch script pins nothing. The diff was
ruled out on 2026-10-05 (the fastest run had the most tests). `~/ace3p.sh` was
ruled out too: the pytest job never sources it, and conda's extensions use
RPATH so its `LD_LIBRARY_PATH` cannot shadow them.

| Job | Node | OS | Wall | TotalCPU | Warnings |
|---|---|---|---|---|---|
| 39639568 | sdfmilan238 | 8.10 | 5:49 | 12:14 | 575 |
| 39643250 | sdfmilan262 | 8.10 | 5:49 | 12:25 | 575 |
| 39647859 | sdfmilan265 | 8.10 | **25:13** | **1:14:59** | 575 |
| 39926642 | sdfmilan010 | 9.6 | 6:54 | 14:48 | 567 |
| 39931202 | sdfmilan132 | 9.6 | 5:47 | – | 567 |
| 39932673 | sdfmilan261 | 8.10 | **21:12** | **1:26:16** | 575 |
| 39935147 | sdfmilan132 | 9.6 | 5:42 | 12:17 | 567 |

The warning count is an OS fingerprint: EL8 runs emit 8 extra lbfgs
`ABNORMAL` ConvergenceWarnings from seeded GP fits in `test_surrogate.py`.

> **Corrected 2026-10-06.** It is not an OS fingerprint — every *pinned* EL8 run
> emitted 567. Those 8 warnings are a threading artifact of the seeded GP fits,
> so thread count perturbs that floating-point path. No assertion depends on it
> (783 passed pinned), but the pin is therefore not purely a perf change.

## 2. Rules (from memory; do not relax them)

- One sbatch job at a time on `--partition=milano --account=rfar:regular`.
  `pytest_milano.sh` already refuses if a job of mine is queued.
- Use the `lume-ace3p-dev` conda env. Never touch the user env `lume-ace3p`.
- **Do not edit `~/ace3p.sh`** — it is a symlink to the group file
  `/sdf/group/rfar/ace3p/ace3p.sh`, owned by Cho and sourced by every rfar user.
  Thread counts are per job and do not belong there.
- Nothing here changes the repo until §5 decides it should. Steps 1–3 edit only
  the helper script in scratch.
- Never run the full suite on the login node (iana).

## 3. Where things are

| What | Path |
|---|---|
| Helper that writes and submits the batch job | `/sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/_tools/pytest_milano.sh` |
| Logs (one per run, timestamped) | `/sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/_tools/pytest_<stamp>.log` |
| Last submitted job id | `/sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/_tools/pytest.jobid` |
| Repo | `/sdf/home/d/dbizzoze/lume-ace3p` (branch `dev`) |
| Memory note to update at the end | `/sdf/home/d/dbizzoze/.claude/projects/-sdf-home-d-dbizzoze-lume-ace3p/memory/pytest-suite-slowdown-2026-10-01.md` |

## 4. The three steps

### Step 1 — make the helper pin threads and fingerprint the node

Edit `pytest_milano.sh` so that two environment variables steer the generated
`#SBATCH` file. Keep the default behaviour identical to today when neither is
set, so the old runs stay comparable.

- `PIN=1` → add, after `conda activate lume-ace3p-dev` and before `pytest`:
  ```bash
  export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
  ```
- `OSVER=8.10` (or `9.6`) → add `#SBATCH --constraint=OS_VER:$OSVER`.
- Always (both modes) print a fingerprint block at the top of the log, before
  `pytest`:
  ```bash
  echo "== node $(hostname)  $(cat /etc/redhat-release)  PIN=${PIN:-0}"
  python - <<'PY'
  import os; from threadpoolctl import threadpool_info
  print('affinity cpus', len(os.sched_getaffinity(0)))
  for d in threadpool_info():
      print(d['internal_api'], d['num_threads'], d['filepath'])
  PY
  ```
  `threadpoolctl` is installed in the dev env (verified 2026-10-05). Expected
  output with `PIN=1`: `openblas 1` and `openmp 1`. Without it: `26`+ on a
  login node, up to 128 on a milano node.

  > **Corrected 2026-10-06, two things.** (a) The snippet as written prints only
  > the affinity line — `threadpool_info()` is empty until the libraries are
  > loaded, so it needs `import numpy, sklearn.gaussian_process` first (numpy
  > first, per §5). (b) Unpinned on a milano node it reports **8**, not 128:
  > OpenBLAS does respect the cpuset. Two pools of 8 over 8 cores was enough to
  > cause the whole effect.
- Also background one process sample two minutes in, so a slow run explains
  itself:
  ```bash
  ( sleep 120; echo "== 120s sample"; ps -o pid,nlwp,pcpu,rss,comm -u $USER --sort=-pcpu | head -5 ) &
  ```
- Make the `echo` at the end of the `.sl` file also print `sacct`'s
  `Elapsed,TotalCPU,NodeList` for `$SLURM_JOB_ID` so the table in §1 can be
  extended from the log alone.

Sanity-check the generated `.sl` once by reading it before submitting.

### Step 2 — three pinned runs on RHEL 8.10

Both slow runs were EL8, so EL8 is the only OS on which pinning can be shown to
help. Submit one at a time:

```bash
PIN=1 OSVER=8.10 /sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/_tools/pytest_milano.sh
```

Wait for completion (`squeue -u dbizzoze`, or `sacct -j $(cat pytest.jobid)
-X -o Elapsed,TotalCPU,NodeList,State`), then submit the next. Each should
take 5–7 minutes plus queue time. Record for each: job id, node, wall,
TotalCPU, passed/skipped counts, warning count, the fingerprint block. Append
the rows to the table in the memory note (§6).

If the EL8 partition is busy and a job sits queued more than ~20 minutes, do
not add a second job; wait.

### Step 3 — decide, and only then act

Read the three results against the table in §1.

- **All three pinned EL8 runs ≤ ~7 min and TotalCPU ≤ ~15 CPU-min** →
  oversubscription is confirmed as the cause (the unpinned EL8 failure rate was
  2 of 4). Make the pin permanent in the repo: a session-scoped autouse fixture
  in `tests/conftest.py` (create it; none exists) that enters
  `threadpoolctl.threadpool_limits(limits=1)` for the whole session, with a
  short comment pointing at this plan. Do not export `OMP_NUM_THREADS` from
  `conftest.py` — Track3P examples set their own and the fixture must not leak
  into solver subprocesses launched by tests. One full milano run afterwards
  without `PIN=1` to show the fixture alone is enough. This is the thread item
  in `plans/track3p_module_plan.md` Phase 4 step 0; tick it there.
- **Any pinned EL8 run > ~15 min** → the node is the cause, not the threads.
  Record its node. Draft a message to S3DF support for David to send (confirm the
  current support address or ticket portal first; do not guess one) with: job ids 39647859 and 39932673 plus the new slow one,
  nodes sdfmilan265 / sdfmilan261 / the new one, the CPU-time inflation table,
  and the question whether those nodes showed CPU throttling or correctable
  memory errors in those windows. Still land the `threadpoolctl` fixture — it
  costs nothing and removes a confound — but say in the commit message that it
  did not fix the swing.
- **Mixed (one slow of three)** → run three more before deciding. The effect
  was 2 in 4 unpinned, so three clean pinned runs are suggestive, not proof;
  six are.

Two conversations are worth starting regardless of outcome, and are **not**
this session's job to resolve, only to raise if David asks: the RHEL 8.10
retirement timeline on milano (ACE3P needs EL8 for `libgsl.so.23`, half the
partition is already 9.6), and whether Cho plans an EL9 ACE3P build.

## 5. What not to do

- Do not change `n_jobs`, `n_restarts_optimizer` or any seed in
  `src/lume_ace3p/surrogate.py`; the baselines depend on them.
- Do not add the exports to any example `*_s3df.batch` — those run solvers,
  not pytest, and `track3p_geant4_chain` deliberately exports `OMP_NUM_THREADS=8`.
- Do not read a high node load average as the explanation; every milano node
  runs ~100 cgroup-pinned co-tenants and the fast sdfmilan262 run was on a
  fuller node than the slow sdfmilan265 run.
- Do not run `import torch` before `import numpy` in ad-hoc checks in the dev
  env; it fails with a `GLIBCXX_3.4.29` error on the login node. The suite
  imports numpy first and is unaffected.

## 6. Closing the loop

1. Extend the table in the memory note with the new rows and write a one-line
   verdict at the top ("resolved: oversubscription" / "node-bound; S3DF ticket
   N opened" / "still open after N pinned runs").
2. Update the `MEMORY.md` index line for that note to match.
3. If the fixture landed: one commit, message ending with the attribution lines
   the session reminder gives; full suite green on milano before committing.
4. Mark this plan's status at the top of the file.
