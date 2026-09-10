# PR text: dev → main (S3DF example validation)

Open with (after `gh auth login`, once):

```bash
gh pr create --base main --head dev --title "Validate every example on S3DF; fix what real runs found" --body-file plans/s3df_validation_pr.md
```

or via https://github.com/slaclab/lume-ace3p/compare/main...dev

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

1. **acdtool `maxFieldsOnSurface` was unreadable** — the reader assumed
   `Emax = value at (x,y,z)`; acdtool writes `Emax :  3.94e+07 (V.m)  at (…)`.
   `examples/omega3p_sweep` failed on its first real run. (9698d62)
2. **A leading Omega3P `Mode` block was lost** — the ACE3P tokenizer did not
   strip the `/* … */` header, which was glued onto the first key. 3 of 32
   sweep points silently reported the wrong mode's frequency. (b855423)
3. **Tables exploded over a field index nobody used** — an Omega3P sweep with
   every output narrowed to mode 0 produced 2 rows per point, the second
   carrying mode 0's values under `ModeID = 1`. The axis is now used only when
   an output spans it (or none is declared). ⚠️ behaviour change; no frozen
   baseline moves. (888f558)
4. **`s3p_optimization` optimized NaN** — its objective sat at 12.0 GHz, not a
   point of its 9.424 + k·0.25 GHz scan. Off-grid frequencies now raise naming
   the scan; the example uses 11.924 GHz. ⚠️ behaviour change. (64c45b3)
5. **`s3p_window_rfpost`'s journal put an interior face in a symmetry sideset**
   (hard-coded tutorial surface IDs after a journal join); bad Euler
   characteristic, S3P aborted. Surfaces are now selected by position. (cedee39)
6. **S3P dies over 16 ranks on the multi-fidelity examples' coarsest mesh**
   (~2.6k elements, SIGFPE); verified 8 and 4 ranks complete. Both examples use
   8×4. (1e51dfc)

Plus: Geant4 S3DF batch scripts now source the group's Geant4 environment
(preserving conda `PATH`/`PYTHONPATH`) (594edbb); the nonzero-exit note no
longer claims the solver "never ran"; a GP-sweep torch warning is silenced; two
stale test stubs fixed; installation docs gain the S3DF `tasks × cores ≤ 120`
rule and setup-script ordering; CHANGELOG has an Unreleased section.

## Test plan

- [x] Full `pytest tests/` on iana after the last `src/` change: 662 passed,
      2 skipped (+1 test-only fix, file re-run 24/24). ~41 min.
- [x] Baseline self-check passes (no frozen dry-run table moved).
- [x] All 17 examples validated on milano — see the plan's checklist.

## Open question for the reviewer

`s3p_sweep` / `s3p_sweep_no_s3p_file` declare no `output_parameters`, so their
tables are `inputs + Frequency` only (the docs used to promise S-parameter
columns; corrected). Adding outputs would move their dry-run baselines.

## After merge

In `/sdf/group/rfar/lume-ace3p`: `git pull` on `main`, then
`conda activate lume-ace3p && pip install /sdf/group/rfar/lume-ace3p --no-deps`.
