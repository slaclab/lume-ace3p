# S3DF example validation plan

Goal: run every example in `examples/` for real on S3DF milano nodes from the
**home dev checkout** (`/sdf/home/d/dbizzoze/lume-ace3p`, branch `dev`), fix
whatever breaks, commit on `dev`, then push and release. This plan is written so
a fresh Claude Code CLI session (or a person) can execute it without any other
context. Status is tracked in the checklist below; **update it as you go**.

## How to run this plan from a terminal (NoMachine / ssh to sdfiana)

```bash
tmux new -s lume            # survives NoMachine/ssh disconnects; `tmux attach -t lume` to return
cd ~/lume-ace3p
claude                      # ~/.local/bin/claude
```

Then tell Claude: *"Execute plans/s3df_example_validation_plan.md. Pick up at the
first unchecked item."* Everything below is what it needs.

## Ground rules (from the RFAR czar, 2026-09-09)

- Real ACE3P/Geant4 runs go ONLY through `sbatch` to `--partition=milano
  --account=rfar:regular`. Never run a solver on iana.
- **One job at a time, one node.** Courtesy to other rfar users, not a hard limit.
  Multi-node or concurrent jobs: ask first. Queueing behind other users is fine.
- Test jobs may be submitted without asking. Wall time 5 min to a few hours.
- Dry-run (`dry_run: True` under `workflow_parameters`) and `pytest` are fine on iana.
  Dry-run does NOT auto-enable on iana (site detection finds ACE3P/Cubit there).
- Conda: `conda activate lume-ace3p-dev` (editable install of the home checkout).
  `lume-ace3p` (no `-dev`) is the users' release env — do not modify it.
  Shell state does not persist between Claude Bash calls: prefix every Python
  command with `conda activate lume-ace3p-dev &&`.
- Test artifacts go in scratch: `/sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/<example>/`.
- The shipped `*_s3df.batch` scripts work as-is when submitted from a shell with
  the conda env active (verified: `t3p_sweep` ran identically with and without
  `source ~/ace3p.sh`). If a login shell sources `~/ace3p.sh`/`~/geant4.sh`, do
  that BEFORE `conda activate` (they hard-reset PATH).

## Sizing constraint that caused the first failures

Milano nodes: 128 cores, **120 usable**, no hyperthreading. Batch headers allocate
120 (`--ntasks-per-node=120`). The solver runs as `srun -n <tasks> -c <cores>`, so
**`tasks × cores` must be ≤ 120** or srun refuses the step with
`More processors requested than permitted`. Fixed in commit `66c8e2a` (all
examples now ≤ 64). Geant4 examples use `--ntasks=1 --cpus-per-task=120` with
`geant4_threads: 120`, which fits.

## Helpers (already in scratch)

`/sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/_tools/`:

- `stage_and_submit.sh <example>` — deletes any old copy, copies
  `examples/<example>` (and `examples/assets`) fresh from the home checkout,
  activates the dev env, submits the `*_s3df.batch`, writes `.jobid`. Refuses if
  one of my rfar jobs is already queued.
- `check.sh <example>` — job state from `sacct`/`squeue`, workdir count, output
  files, filtered stderr, stdout tail.

Loop per example:

```bash
T=/sdf/scratch/users/d/dbizzoze/lume-ace3p-tests/_tools
$T/stage_and_submit.sh <example>
# poll every ~30-60 s (sleep in Bash; don't block on the whole run)
$T/check.sh <example>
```

## Acceptance criteria per example

1. Job `COMPLETED` with exit code `0:0`.
2. Expected number of workdirs (sweep grid size, or 1 for `single`, or the
   optimizer's evaluation count).
3. The `output_file` named in the YAML exists, has the expected columns, and
   **no NaNs** in solver-derived columns (dry-run produces NaNs; real runs must not).
4. Filtered stderr contains no `Error`/`Traceback`/`srun: error`.
5. Sanity: values physically plausible (e.g. loss factor magnitude decreasing with
   iris radius; S-parameter magnitudes ≤ 1; Omega3P frequencies near the design).

Check outputs with pandas, e.g.

```bash
conda activate lume-ace3p-dev && python -c "
import pandas as pd; df=pd.read_csv('<output>.txt', sep='\t')
print(len(df), list(df.columns)); print(df.isna().sum().to_string())"
```

## When something fails

- Read `error-<jid>.txt`, then the per-module logs in the workdir
  (`cubit.log`, `<solver>.log`, `acdtool.log`). Since `66c8e2a` a nonzero solver
  exit prints `"<solver> exited with status N (command: ...)"` to stderr.
- Fix in the **home checkout** (`src/`, `examples/`, `docs/`), never in
  `/sdf/group/rfar/lume-ace3p`. Re-stage and re-submit (the helper copies fresh).
- After any `src/` change, run the relevant `pytest` file on iana.
- Commit per logical fix on `dev` with an explanatory message, ending in
  `Generated with AI` / `Co-Authored-By: SLAC AI`.
- Distinguish repo bugs from example-design choices (e.g. an optimizer that
  legitimately takes an hour). Note the latter in the checklist; don't
  "fix" them silently.

## Checklist

Status legend: `[x]` validated on milano, `[~]` ran but with notes, `[ ]` todo.

### Already validated

- [x] `t3p_sweep` — job 37554432, 4m19s, 9 points, no NaNs, loss factor trend sane. (First attempt 37554125 failed on 16×16 sizing; fixed.)
- [x] `s3p_sweep` — job 37559395, 6m18s, 15 points, 195 rows, no NaNs.

### Sweeps (short, do these first)

- [x] `s3p_sweep_no_s3p_file` — job 37561378, 6m13s, 15 points, 195 rows, no NaNs; SParameter.out sane (|S|≤1, |S11|²+|S21|²≈1). Note: like `s3p_sweep`, the YAML declares no `output_parameters`, so the table is only `cornercut, rcorner2, Frequency` (design choice, not a bug).
- [x] `s3p_window_rfpost` — job 37563164, 1m40s, 3 points, 48 rows, no NaNs; |S11|²+|S21|²=1, best match at the 3 mm design (|S11|=0.019 @ 2.556 GHz), acdtool `field1_*` curves present, m_factor=1. First attempt 37562352 failed: the joined `window.jou` named surfaces by tutorial ID, which put a merged ceramic/vacuum face into a symmetry sideset (bad Euler characteristic, S3P aborted in ParMETIS). Fixed by selecting ports/symmetry planes by coordinate.
- [x] `omega3p_sweep` — run 3 (job 37568528, 3m59s): 16 wide rows + `field_artifact` (both modes in the .npz), no NaNs, f falls with radius (1.41→1.03 GHz), R/Q 96–148 Ω, E_max ~3.7–3.95e7 V/m at 20 MV/m gradient. Two repo bugs found and fixed: (1) run 1 (37563717) — acdtool parser expected `Emax = …`, real output is `Emax : value (unit) at (…)` → 9698d62, real fixture added; (2) run 2 (37565335) — 32 rows: ModeID axis exploded although every output was narrowed to mode 0, mode-1 rows carried mode-0 values → 888f558 (table stays wide when no output spans the index).
- [ ] `omega3p_ace3p_param_sweep`
- [ ] `omega3p_dispersion_sweep`
- [ ] `t3p_transwake` (mode `single`; includes `acdtool postprocess transwake`)
- [ ] `t3p_power_balance`

### Optimizations (longer; check `xopt_parameters`/`max_evaluations` first and
### shrink for the test if it would exceed ~1 h, noting the change is test-only)

- [ ] `omega3p_optimization`
- [ ] `s3p_optimization`
- [ ] `s3p_mf_optimization` (new S3DF batch script)
- [ ] `s3p_bayesian_sweep` (new S3DF batch script; GP sweep)

### Geant4 (needs `/sdf/group/rfar/geant4/example/dose-npass/sim` — exists)

- [ ] `geant4_dose_single`
- [ ] `geant4_track3p_beta`
- [ ] `geant4_beta_surrogate` (mode `invert_bayesian`; reads a store — check its
  README for what must exist first; may depend on `geant4_track3p_beta` output)

### Python-only (run directly on iana, no sbatch)

- [x] `track3p_particle_weight` — ran on iana 2026-09-09 (19 s, exit 0): 3284 particles filtered, 8 bins with beta [50..65..50], 19-column weighted dump written, `Bin` 0–7 and `ParticleWeight` 0–8.7e6, no NaNs. No table (mode `single`, no `output_file`), as designed.

### Not runnable

- `examples/incomplete/*` — legacy YAMLs, skip.

## Other open items

- [ ] Full `pytest` on iana: was started 2026-09-09 ~14:55 with the new
  regression test and had not finished after 20 min. Re-run
  `conda activate lume-ace3p-dev && python -m pytest -q tests/` in tmux (or
  `-x --durations=10` to find the slow ones) and record the result here. The
  new test alone passes (`-k refused_solver_launch`).
- [ ] Decide whether `s3p_sweep` / `s3p_sweep_no_s3p_file` should declare
  `output_parameters` (S(0,0), S(0,1), …). With none declared the table is only
  `inputs + Frequency`; the docs used to promise S-parameter columns (corrected
  in 888f558 to describe actual behavior). Adding outputs to the YAMLs would
  move the dry-run baselines (new NaN columns) → re-freeze. User's call.
- [x] Note added to `docs/installation.md` (S3DF section) about
  `tasks × cores ≤ 120` and the `source ace3p.sh` → `conda activate` order.

## Finish

1. All boxes checked (or `[~]` with notes). `pytest` green.
2. Update `CHANGELOG.md` (unreleased section) summarizing the fixes.
3. `git push origin dev`.
4. Open a PR `dev → main` on GitHub (`gh pr create`), summarizing validation.
5. **Only after the user merges:** in `/sdf/group/rfar/lume-ace3p` run
   `git pull` on `main`, then in the **users'** env
   `conda activate lume-ace3p && pip install /sdf/group/rfar/lume-ace3p --no-deps`
   (non-editable; that env must keep a release copy). Ask before this step.
