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
- [x] `omega3p_ace3p_param_sweep` — run 2 (job 37602502, 8m40s): 32 wide rows + field_artifact, no NaNs, 2 modes at every point, Mode_freq 1.03–1.50 GHz, R/Q identical across Sigma, Q0 11.4k (σ=1.04e7) vs 29.7k (σ=5.8e7) in the artifacts. Run 1 (37571138, 7m54s after ~1.5 h in queue) exposed a repo bug: 3 of 32 points reported a single 2.2 GHz mode because Omega3P wrote `Mode` blocks first in `omega3p.out` and the un-stripped `/* */` header swallowed the first → fixed in b855423 (fixture added).
- [x] `omega3p_dispersion_sweep` — job 37615198, 41 s, 6 Theta points, 6 rows (long over ModeID, 1 mode each), no NaNs; f monotonic 11.36 GHz (θ=−180°) → 11.11 GHz (θ=−30°), Q ≈ 6960–7025; `dlwg-pbc.jou` sidesets OK (Euler check passes at all 6 points).
- [x] `t3p_transwake` — job 37615888, 1m00s, 1 workdir, 2335 rows long over `s` (0–1.4 m), no NaNs; `K` = 0.09622 V/pC equals acdtool's "Kick factor" header, `W_trans` matches `wakefield.out`, `W_at_1m` = 0.00347. (acdtool prints "Offset = 0.0125 m" on stderr — informational.)
- [x] `t3p_power_balance` — job 37616498, 1m08s, 3 thicknesses, 2001 rows long over `t` (667 steps to 6.67 ns), no NaNs; P_wall peak 0.023 → 0.16 → 0.35 W and P_out peak 2.06 → 0.92 → 0.094 W as the coating thickens; `P_wall_at_5ns` scalar populated. Companion `power_balance.py` ran on iana (MPLBACKEND=Agg), wrote the balanced table and plot. P_in is negative (inward-flux sign convention) — example design, not a bug.

### Optimizations (longer; check `xopt_parameters`/`max_evaluations` first and
### shrink for the test if it would exceed ~1 h, noting the change is test-only)

- [x] `omega3p_optimization` — job 37725620, 5m45s, 25 Nelder-Mead evaluations (25 workdirs), `sim_output.txt` 25 rows, no NaNs, no `xopt_error`; converges to the bound corner (cav_radius 105, ellipticity 0.5) with R/Q 135.96 Ω — consistent with the sweep's trend (R/Q ↑ with radius, ↓ with ellipticity). Converging to a bound is the example's design, not a bug.
- [x] `s3p_optimization` — run 2 (job 37727718, 7m04s): 25 NM evaluations, no NaNs/errors, |S11| 0.117 → 0.020, converging to the bound corner (cornercut 14, rcorner1 2.5). Run 1 (37726476, 8m04s) completed with exit 0 but **every objective was NaN**: the YAML asked for `at: {frequency: 12.0e9}` and the .s3p scan is 9.424 + k·0.25 GHz (no 12.0). Example moved to 11.924 GHz and an off-grid frequency now raises (64c45b3).
- [x] `s3p_mf_optimization` — run 2 (job 37730046, 7m27s): 14 evaluations over fidelities 0–1 (fidelity 0 now fine on 8 ranks), no NaNs/errors, terminated by `tolerance` (reflection 0.0000 at s=0.70; runtimes 10 s at s=0 to 36 s at s=1). Run 1 (37728605) FAILED: S3P SIGFPE at mesh_fidelity 0 (2634 elements) over 16 ranks; diag job 37729506 showed 16 ranks fail deterministically, 8/4 succeed → both MF examples moved to 8×4 (1e51dfc).
- [x] `s3p_bayesian_sweep` — job 37731398, 3m01s (8 ranks): 13 real S3P evaluations in `sim_output.txt` (|S11| 0.004–0.095), 100-point GP grid in `sweep_output.txt` (means 0.00–0.095), no NaNs/errors. A torch `requires_grad` → `float()` warning per grid point silenced with `.detach()`.

### Geant4 (needs `/sdf/group/rfar/geant4/example/dose-npass/sim` — exists)

- [x] `geant4_dose_single` — job 37732311, 1m54s: Geant4 ran with 120 threads on 144640 macro-particles; total_dose 60.85, peak_dose 0.505, total_edep 1.54e16, no NaNs; `field_0.npz` holds dose/edep grids (2641 non-zero dose voxels). Needed a fix first: the S3DF Geant4 batch scripts set no Geant4 environment (G4*DATA), so all three now source `/sdf/group/rfar/cho/geant4/geant4.sh` while preserving the conda PATH/PYTHONPATH.
- [x] `geant4_track3p_beta` — job 37732611, 7m52s, 5 beta points (40–60), 5 workdirs, table `geant4_beta_sweep_output` = `beta` + `field_artifact` (no `output_parameters` declared — design). Artifacts hold real dose grids: total dose 0.0 / 0.002 / 0.088 / 1.64 / 19.9 for beta 40…60, macro-particles loaded 16k → 145k. No NaNs.
- [x] `geant4_beta_surrogate` — collection: job 37733843, 26m14s, 16 Sobol samples, `training_table.txt` 16 rows, no NaNs, manifest OK. Then on iana (CPU-only): `train` 12 s → 9-mode PCA-GP (0.9913 energy; held-out rel-L2 mean 2.10 — poor, as expected for 16 samples in 8-D with linear dose; sklearn kernel-bound ConvergenceWarnings); `invert_optimize` 30 s → recovers sample_00000's beta to ~0.5 (e.g. 56.55 vs 57.01, 58.56 vs 58.63), 8/8 directions identifiable; `invert_bayesian` 20 s → 8000 draws, but r_hat 5.5 and the mode itself warns the chains did not mix (16-sample store; data, not code). All four stages exit 0.

### Python-only (run directly on iana, no sbatch)

- [x] `track3p_particle_weight` — ran on iana 2026-09-09 (19 s, exit 0): 3284 particles filtered, 8 bins with beta [50..65..50], 19-column weighted dump written, `Bin` 0–7 and `ParticleWeight` 0–8.7e6, no NaNs. No table (mode `single`, no `output_file`), as designed.

### Not runnable

- `examples/incomplete/*` — legacy YAMLs, skip.

## Other open items

- [~] Full `pytest` on iana: run started 2026-09-09 15:59 (source state = after
  9698d62, before 888f558): **661 passed, 2 skipped in 52m41s**. It is slow
  because `tests/test_bayesian.py` (6 tests, ~20 min total, up to 353 s each)
  and `tests/test_inversion.py` (~10 min) fit GPs / run MCMC; it also uses ~9
  cores on iana while doing so. Two stale stubs in `test_modules.py` broke on
  66c8e2a's exit-status recording and were fixed in 9a73b48. **Re-run once more
  after the last code change** (`python -m pytest -q tests/ -p no:cacheprovider`,
  ~55 min; `--deselect tests/test_bayesian.py` for a 30-min version).
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
