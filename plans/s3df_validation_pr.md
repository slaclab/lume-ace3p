# Release 0.5.1: merge `dev` into `main`

`main` (374e83d, PR #55 merged 2026-09-01) is a strict ancestor of `dev`, so
there is one unmerged chunk: everything on `dev` since 0.5.0. The merge is a
fast-forward in content; GitHub will still record a merge commit as it did for
PR #55. No git tags exist for earlier releases, so none is required here.

## Merge plan

Steps 1 to 3 are done on `dev`; 4 to 8 are for the user (or a session the
user is watching), in order.

1. [x] Full `pytest tests/` on iana after the last `src/` change (65a76a2,
       the `gp_parameter_sweep` cap). Record the result below.
2. [x] `pyproject.toml` version 0.5.1; CHANGELOG `Unreleased` closed as
       `0.5.1 — 2026-09-10` with a fresh empty `Unreleased` above it. Commit
       "Release 0.5.1" (this is the tip of `dev`).
3. [x] `git push origin dev`.
4. [ ] Open the PR with the body below (`gh` is logged in as dbizzoze):

   ```bash
   cd ~/lume-ace3p
   ~/.local/bin/gh pr create --base main --head dev \
     --title "Release 0.5.1: validate every example on S3DF; fix what real runs found" \
     --body-file <(sed -n '/^## Summary/,$p' plans/s3df_validation_pr.md)
   ```

   or https://github.com/slaclab/lume-ace3p/compare/main...dev

5. [ ] Review on GitHub. The two behaviour changes (table shape, off-grid
       frequency raise) are the things to read closely; both are flagged ⚠️ in
       the CHANGELOG. Merge with a merge commit, as PR #55 was.
6. [ ] Refresh the users' install in `/sdf/group/rfar/lume-ace3p` (ask the
       user first; it is the shared release copy, non-editable):

   ```bash
   cd /sdf/group/rfar/lume-ace3p && git checkout main && git pull
   source /sdf/group/rfar/software/conda/etc/profile.d/conda.sh
   conda activate lume-ace3p && pip install /sdf/group/rfar/lume-ace3p --no-deps
   python -c "import lume_ace3p; print(lume_ace3p.__version__)"   # expect 0.5.1
   ```

7. [ ] Refresh the dev env's metadata so its runtime version matches:
       `conda activate lume-ace3p-dev && pip install -e ~/lume-ace3p --no-deps`.
8. [ ] Check Read the Docs built `latest` from `main` without warnings (the
       local build is warning-free).

Deferred to after the release (see the validation plan's "Follow-ups the user
has deferred"): moving the optimization VOCS boxes off their bound corners,
the Geant4 surrogate sample count, the docs reorganization, and the
ACE3P-internal-sweep discussion.

## Test result for step 1

Filled in by the session that ran it; see the bottom of this file.

---

## Summary

Every example under `examples/` (17, excluding `incomplete/`) was run for real on
S3DF milano nodes via the shipped `*_s3df.batch` scripts, from the `dev`
checkout, with acceptance criteria per example (job COMPLETED, expected
workdir count, output table present with no NaNs in solver-derived columns,
clean stderr, physically plausible values). Job IDs, timings and notes are in
`plans/s3df_example_validation_plan.md`.

Real runs found six defects that dry runs cannot see, all fixed here with
tests and, where an assumed format was wrong, a real-output fixture:

1. **acdtool `maxFieldsOnSurface` was unreadable.** The reader assumed
   `Emax = value at (x,y,z)`; acdtool writes `Emax :  3.94e+07 (V.m)  at (…)`.
   `examples/omega3p_sweep` failed on its first real run. (9698d62)
2. **A leading Omega3P `Mode` block was lost.** The ACE3P tokenizer did not
   strip the `/* … */` header, which was glued onto the first key. 3 of 32
   sweep points silently reported the wrong mode's frequency. (b855423)
3. **Tables exploded over a field index nobody used.** An Omega3P sweep with
   every output narrowed to mode 0 produced 2 rows per point, the second
   carrying mode 0's values under `ModeID = 1`. The axis is now used only when
   an output spans it (or none is declared). ⚠️ behaviour change; no frozen
   baseline moves. (888f558)
4. **`s3p_optimization` optimized NaN.** Its objective sat at 12.0 GHz, which
   was not a point of its 9.424 + k·0.25 GHz scan. Off-grid frequencies now
   raise naming the scan; the three bend examples scan 9.5 to 12.5 GHz so
   12.0 GHz is on grid. ⚠️ behaviour change. (64c45b3, 6faaf98)
5. **`s3p_window_rfpost`'s journal put an interior face in a symmetry sideset**
   (hard-coded tutorial surface IDs after a journal join); bad Euler
   characteristic, S3P aborted. Surfaces are now selected by position. (cedee39)
6. **S3P dies over 16 ranks on the multi-fidelity examples' coarsest mesh**
   (~2.6k elements, SIGFPE); verified 8 and 4 ranks complete. Both examples use
   8×4. (1e51dfc)

Found while cleaning up afterwards:

7. **`gp_parameter_sweep` ran one step past `max_steps` and ignored
   `num_step`.** `examples/s3p_bayesian_sweep` declared `num_step: 3`, a
   `scalar_optimize` key the block check accepts anywhere, so it ran until the
   patience test stopped it. The cap is exact, the mode warns about `num_step`,
   and a zero-step campaign still emits its posterior sweep. (65a76a2)

Also: `examples/s3p_sweep` and `s3p_sweep_no_s3p_file` declare the four
`S(m,n)` spectra plus a scalar `reflection_12GHz`, so `plotting/s3p_sweep_plot.py`
has columns to draw again (6faaf98, baselines re-frozen); Geant4 S3DF batch
scripts source the group's Geant4 environment (594edbb); a GP-sweep torch
warning is silenced; two stale test stubs fixed; installation docs gain the
S3DF `tasks × cores ≤ 120` rule and setup-script ordering; every `docs/` page
and example README was tightened for concision with about fifteen
code-vs-doc mismatches corrected (2a6afce); version 0.5.1.

## Test plan

- [x] Full `pytest tests/` on iana after the last `src/` change (see the
      release commit message for the count).
- [x] Baseline self-check passes (two S3P sweep baselines intentionally
      re-frozen for new NaN columns; nothing else moved).
- [x] All 17 examples validated on milano; the four S3P bend examples
      re-validated after their scan/output changes (jobs 37747638, 37748242,
      37748676, 37751697).
- [x] Sphinx build of `docs/` is warning-free.

## After merge

In `/sdf/group/rfar/lume-ace3p`: `git pull` on `main`, then
`conda activate lume-ace3p && pip install /sdf/group/rfar/lume-ace3p --no-deps`.
