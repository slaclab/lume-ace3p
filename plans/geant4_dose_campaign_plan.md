# Geant4 dose campaign: per-bin forward model and inversion on real data

**Status:** COMPLETE through Phase 3 (2026-10-21). Phases 0 to 2 done 2026-10-19; Phase 3 items 1 to 3 done 2026-10-20 (`AdditiveSurrogate`, per-bin training mode, measured fidelity); item 4 done 2026-10-21 (example retired, docs, changelog). Release of 0.9.0 and Phase 4 are what remain; the research write-up is `docs/dose_inversion_summary.md` in the campaign directory. Supersedes Phase 5 of
`plans/geant4_surrogate_inversion_plan.md` and picks up the real-data work that
plan left open.
**Owner:** dbizzoze
**Where the work lives:** a campaign directory outside the repo (§3). The repo
changes only in Phase 3, after the approach has been shown to work on real data.
**How to start in a fresh session:** read §3, §4 and §7, then begin at Phase 0
(§5). §1 and §2 are background, not steps. The first action is creating the
campaign directory and writing the Phase 0 configs and batch script into it.

---

## 1. Why a new plan

The surrogate + inversion plan delivered its machinery (DOE collection, PCA-GP
surrogate, point and Bayesian inversion, identifiability) against a synthetic
fixture, and the 2026-09-10 S3DF validation then ran all four stages on real
Geant4 data (job 37733843, 16 Sobol samples). Everything exited 0 and the result
was poor:

| stage | result on the 16 real samples |
|---|---|
| `train_surrogate` (linear) | 9 POD modes, held-out relative L2 mean 2.10 |
| `invert_optimize` | sample 0's β recovered to about 0.5, "8 of 8 directions identifiable" |
| `invert_bayesian` | `r_hat` 5.5, chains did not mix |

The 2026-10-19 review of that store found why, and the reason changes the
approach rather than the sample count:

- **The forward map is additive over bins.** Geant4 transports each primary
  independently and the app scores dose with weight `W` per history, so dose is
  linear in the particle weights. Each particle's weight depends only on the β of
  its own axial bin (`particles.py`, `calculate_particle_weight`). Therefore
  `D(β) = Σ_b D_b(β_b)`: eight one-dimensional curves of dose grids, not a general
  8-D function. The 8-D DOE plus 8-D GP cannot resolve that from 16 points, and
  no feasible number of full-chain samples fixes it efficiently.
- **The data confirms it.** Total dose across the 16 samples correlates 0.999
  with the summed particle weight and spans a factor 67. The log-space POD
  spectrum is flat (15%, 11%, 8%, 7%, ...): the shape signal is buried in Monte
  Carlo noise at one history per macroparticle.
- **The recorded fidelity is a label, not a measurement.** The example writes
  `fidelity: 10000` into every row while the real runs used 81,950 to 133,522
  primaries, varying with β because the one-electron cut drops more particles at
  low β. The actual fidelity knob of the dose-npass app is `histories_per_macro`
  (aliases `passes`, `nhistories`, default 1), which the example never sets.
- **"8 of 8 identifiable" was an artifact.** Nine GPs fit on 16 points
  interpolate noise, so the Jacobian has full rank for the wrong reason. In the
  additive model identifiability is physics: with exponential weights the bins
  with the highest β dominate the dose and low-β bins are invisible. That is the
  "dominant emitter plus upper limits" statement of
  `plans/beta_localization_plan.md` §1, reached independently for the ten-monitor
  problem. The two plans converge on the same architecture (§6 there:
  a kernel-table surrogate behind `DoseSurrogate`'s interface).

Two things confirmed on 2026-10-19 by dry run, with no code changes:

- `beta: 1.0` in a bin zeroes every weight in it (`exp(-6.53e9 φ^1.5 / (βE))`
  underflows at β = 1), and the one-electron cut then drops those rows, so a
  `parameter_sweep` over one β with the other seven at 1 is a bin-isolated run
  (22,206 rows for bin 3, equal to its per-bin count).
- `input_parameters: geant4: {histories_per_macro: N}` reaches the Geant4 input
  file (appended when the key is absent).

Also found: the dose file's `total(val^2)` column is a per-voxel Monte Carlo
variance that `read_dose_file` currently discards (it keeps column 4 only). The
dose-npass binary accepts no seed key, so replicate runs are not a usable noise
estimate; the `val^2` column is.

## 2. Goal and deliverables

Build a forward model `β ∈ ℝ⁸ → dose grid` for the 7-cell example chain that is
accurate on real Geant4 data, state honestly which β combinations the dose
constrains, and invert real dose files with the existing `invert_optimize` /
`invert_bayesian` modes.

Deliverables, in order:

1. An **additivity and noise measurement** (Phase 0): does the sum of eight
   bin-isolated runs reproduce a full run within Monte Carlo error, and how does
   the error scale with `histories_per_macro`.
2. A **per-bin training campaign** (Phase 1): eight 1-D sweeps, each a table of
   `(β_b, dose grid)` at measured fidelity.
3. An **additive forward model and its analysis** (Phase 2), validated against the
   16 existing full-chain samples as a free held-out set.
4. **Repo changes** (Phase 3), only once Phase 2 works: an additive surrogate
   behind the `DoseSurrogate` interface so both inversion modes run unchanged,
   fidelity recorded from the run, and the stale documentation fixed.

## 3. Where the work lives

Campaign configs, batch scripts, analysis scripts and stores are **not** repo
content. Recommended layout, one directory, its own small git repo for the
scripts and configs only:

```
/sdf/group/rfar/dbizzoze/dose_campaign/          # persistent, group-readable
  README.md                 # what each sub-directory is, which plan phase
  configs/                  # YAMLs + sbatch scripts (committed)
  analysis/                 # python scripts and notebooks (committed)
  runs/                     # Geant4 workdirs and stores (NOT committed; .gitignore)
  stores/                   # finished training stores copied out of runs/ (NOT committed)
```

Reasons: home has 11 GB free, scratch is purgeable and the September store is
already 212 MB for 16 samples, and group space is where the Geant4 app and the
previous campaign artifacts already live. If the directory is placed inside the
checkout instead, add one line to the repo `.gitignore` (`campaigns/`) and
nothing else; do not commit stores or workdirs to `lume-ace3p`.

Configs reference the repo's shared assets by absolute path
(`/sdf/home/d/dbizzoze/lume-ace3p/examples/assets/...`) and the example's
`input_7cell.geant4` by absolute path too, so the campaign does not copy
multi-MB files and stays pinned to the repo revision noted in its README.
Record the `lume-ace3p` version and git hash in each run's README entry; the
store manifest records the mesh and `bin_edges`.

Analysis scripts import `lume_ace3p` from the `lume-ace3p-dev` environment and
read stores through `surrogate_data.load_training_store` / `results.load_field`;
they do not re-implement parsers.

## 4. Correctness constraints

Numbered to continue the series (#1 pinned `bin_edges`, #2 genuine noise term,
#3 pinned scoring mesh; all three stay in force).

- **#4 Fidelity is measured, never typed.** Record per run: the particle-file row
  count (primaries), `histories_per_macro`, and their product (events). Until
  Phase 3 lands this in the manifest, the analysis scripts read them from
  `particles.data` and the written Geant4 input in each workdir. Drop the
  `fidelity: 10000` label from any campaign YAML.
- **#5 Bin isolation is verified, not assumed.** A bin-isolated run's
  `particles.data` must be exactly the rows the full run at the same β emits
  from that bin, in order, and every one of them must come from that bin. The
  row count equals the full-dump bin count
  (`[3423, 23049, 22996, 22206, 24183, 23526, 21926, 3423]` under the example's
  filters) only when the bin is saturated; at sample 0's β it is
  `[3423, 23049, 17696, 16732, 9115, 23526, 18821, 2619]`, summing to the full
  run's 114,981. Bins are assigned by `Initial_z` (the emission site), not
  `Impact_z`: 49% of survivors land outside their emission bin, so an `Impact_z`
  range says nothing about isolation. Check per run in the analysis.
- **#6 Noise comes from the `val^2` column.** No seed key exists in dose-npass,
  so two identical runs are not independent replicates. Per-voxel variance is
  `total(val^2)/entry - (total/entry)^2` scaled by the entry count; derive it
  from the dose file and carry it beside the dose grid.
- **#7 A β range is sized against the dump.** Under the example's `wang-loew`
  settings every macroparticle survives the one-electron cut above β ≈ 52, and
  below β ≈ 42 fewer than half do. Print surviving rows and summed weight per bin
  across any proposed grid before running it (the check in
  `memory: beta-range-is-per-dump`).
- **#8 Additive model only inside the single-pass regime.** Additivity holds
  because the app's transport is independent per primary. It would not hold for
  a model with secondary-emission feedback or self-shielding by the emitted
  charge. Say so in the write-up.

## 5. Phases and gates

### Phase 0: additivity and noise measurement

One sbatch job on milano (one node, `--constraint=OS_VER:8.10`, about one hour).

Runs, all at the β of September's `sample_00000`
(`57.01, 58.63, 47.25, 47.29, 42.80, 51.21, 48.45, 47.78`):

| run | β | `histories_per_macro` | purpose |
|---|---|---|---|
| F1 | full 8-vector | 1 | reproduces the stored sample 0 (same binary, no seed, so expect bit-identical or near) |
| F10 | full 8-vector | 10 | noise scaling of the full run |
| B0..B7 (8 runs) | bin b at its sample-0 value, others 1 | 10 | the eight partial doses |

Implementation: one `parameter_sweep` YAML per group, `workdir_mode: indexed`,
`on_error: skip`. The bin-isolated group is eight YAMLs (or one YAML per bin with
a one-point sweep); a tensor sweep cannot express "one bin at a time", so do not
try to make it one YAML. Each YAML sets `histories_per_macro` under
`input_parameters: geant4:`.

Analysis (`analysis/phase0_additivity.py`):

- Load the ten dose grids and their `val^2` variances (constraint #6).
- Check constraint #5 on B0..B7.
- Compare `Σ_b B_b` with F10 voxel by voxel: report the relative L2 of the
  difference, and the fraction of voxels where `|Σ_b B_b - F10|` exceeds 2σ of the
  combined MC error. Additivity holds if that fraction is at the few-percent
  level expected from noise alone and the relative L2 is at the MC noise level.
- Compare F1 with F10 and with the stored sample 0: measures the noise at one
  history and whether `histories_per_macro` reduces it as `1/√N`.
- From the per-bin partial doses, report each bin's share of the total dose and
  its peak-voxel location: a first look at which bins the dose can see.

**Gate:** additivity within MC error, and a `histories_per_macro` chosen such
that the per-voxel relative error in the dose-carrying region is below the
per-bin shape differences Phase 2 needs to resolve (target: below 10% in voxels
holding 90% of the dose). If additivity fails, stop and record why; the 8-D
approach is then the only route and the sample count must grow, which is a
different plan.

- [x] Job submitted and complete; runs and logs under `runs/phase0/`.
  Job 41319551 (lume-ace3p 0.8.0 @ 546a30e), 2026-10-19, all ten runs exit 0
  in 30 min of node time (F10 alone 12 min). Numbers in
  `runs/phase0/phase0_results.json`.
- [x] Constraint #5 holds for all eight bin runs: each B_b's source rows are
  exactly the full run's bin-b rows, and the eight sum to F1's 114,981.
- [x] Additivity verdict: **holds** (independently recomputed 2026-10-19 from the
  raw dose files: relative L2 0.176 vs 0.171 MC-expected, 4.2% of 90%-dose
  voxels beyond 2σ). Phase 1 may proceed.
- [x] `histories_per_macro` for Phase 1 (confirmed 2026-10-19): **per run,
  `ceil(4e5 / primaries)` with a floor of 13**, not one fixed value. Reason: the
  shape `S_b(β)` can only change below saturation (β ≲ 52, where the surviving
  particle set changes), and that is exactly where a bin has the fewest
  primaries (593 for bin 0 at β 40 vs 23,049 for bin 1 at β 52). A fixed hpm
  gives the noisiest curves where the shape information is. Cost at 4e5 events
  per run: about 7 h of node time for 88 runs, two sequential 4 h jobs with
  `resume`. Fixed hpm 13 would be 3.4 h but leaves low-β runs at ~8k events.

**Phase 0 results (2026-10-19).**

- *Additivity holds within MC error.* Σ B_b vs F10: relative L2 0.176 against
  0.171 expected from the `val^2` variances alone (ratio 1.03); 4.2% of the 453
  voxels holding 90% of the dose are off by more than 2σ (1.6% over all 3,704
  live voxels), the few-percent level noise alone gives.
- *Total dose is heavy-tailed, energy is not.* Σ B_b totals 2.352 Gy against
  F10's 2.478 (−5%), and F1 is −4% against F10, while total deposited energy
  agrees to 0.1% (5.763e14 vs 5.765e14 eV for F10 vs F1). All the dose sits in
  partly filled boundary voxels (under a quarter of a full voxel's copper mass),
  where one event's deposit is a large dose; summing per-voxel variances
  understates the total's error because one event hits many voxels. Not an
  additivity failure, but Phase 2 should not use total dose as a precise
  amplitude.
- *Noise.* F1 reproduces September's sample 0 to relative L2 0.053 (same source
  rows, not bit-identical: Geant4 thread scheduling). Relative error in the
  90%-dose region: F1 median 0.29 / p90 0.45, F10 median 0.11 / p90 0.21. The
  F1/F10 error ratio is 2.6, below the √10 = 3.16 of 1/√N (the `val^2`
  estimate is low at one history in heavy-tailed voxels). Extrapolating F10
  at 1/√N, the 10% target needs `histories_per_macro` ≈ 13 for the median and
  ≈ 43 for the p90.
- *Visibility at sample 0's β.* Dose share: B1 (β 58.6) 88%, B0 (57.0) 10%,
  B5 (51.2) 1.4%, every other bin below 0.12% (B4, β 42.8, 3e-5). Each bin's
  dose centroid follows its own z (−98 mm for B0 to +86 mm for B7), so the
  shapes differ; but at this β only two or three bins are within reach of the
  noise. That is the "dominant emitter plus upper limits" picture of §1.
- *Run time.* Geant4 time is about 0.5 ms per event at 120 threads
  (F10: 1.15M events in 714 s).

### Phase 1: per-bin training campaign

**Approved 2026-10-19 by the user (the rfar czar): four concurrent milano
jobs, one node each**, two bins per job. This is a one-off approval for this
campaign, not a change to the one-job-at-a-time rule.

For each bin `b`, a 1-D `parameter_sweep` over `β_b` with the other seven at 1.

**Grid:** 11 points, 40 to 60 in steps of 2 (checked under constraint #7 on
2026-10-19: survivors per bin rise from 593 / 3,332 at β 40 to the full bin
count at β 52 and stay there, so the grid covers the onset and the saturated
region). Primaries per `(β, bin)` are known ahead of time from the dump; the
table below is the design.

| β | bin 0 | bin 1 | bin 2 | bin 3 | bin 4 | bin 5 | bin 6 | bin 7 |
|---|---|---|---|---|---|---|---|---|
| 40 | 593 | 3,332 | 3,001 | 1,229 | 3,202 | 3,024 | 1,181 | 565 |
| 42 | 1,107 | 7,185 | 7,166 | 5,409 | 7,328 | 7,234 | 5,464 | 1,129 |
| 44 | 1,526 | 11,636 | 11,175 | 9,513 | 12,129 | 11,705 | 9,658 | 1,507 |
| 46 | 2,065 | 15,712 | 15,535 | 14,408 | 16,573 | 15,991 | 14,151 | 2,019 |
| 48 | 2,715 | 19,497 | 19,341 | 18,138 | 20,362 | 19,898 | 18,047 | 2,703 |
| 50 | 3,373 | 22,691 | 22,707 | 21,875 | 23,723 | 23,146 | 21,611 | 3,381 |
| 52 to 60 | 3,423 | 23,049 | 22,996 | 22,206 | 24,183 | 23,526 | 21,926 | 3,423 |

**Fidelity rule (confirmed 2026-10-19):** per run,
`histories_per_macro = max(13, ceil(4e5 / primaries))`, so every run has about
4e5 events. The shape `S_b(β)` can only change below saturation, where a bin has
the fewest primaries, so a fixed value would put the noisiest curves exactly
where the information is. Because `histories_per_macro` is swept alongside `β_b`
under `input_parameters: geant4:`, and the two must move together (not as a
tensor), each bin's sweep is **11 one-point YAMLs or one YAML per β**, not one
11-point tensor sweep. Simplest: a generator script writes
`configs/phase1/bin<b>/beta<β>.yaml` with both values filled in and the
`workdir` set to `runs/phase1/bin<b>/beta<β>`, and each batch script loops over
its two bins' 22 YAMLs. Record `histories_per_macro` in each YAML's header
comment and read it back from the written Geant4 input in the analysis.

**Jobs:** four batch scripts `configs/phase1/run_phase1_<k>.batch`, bins
`{0,1}`, `{2,3}`, `{4,5}`, `{6,7}`, identical headers to Phase 0 (milano,
rfar:regular, `OS_VER:8.10`, 1 node, 120 CPUs, 480 GB), `--time=0-03:00:00`.
Expected per job: 22 runs × (4e5 events × 0.5 ms + ~30 s overhead) ≈ 1.5 h.
Submit all four at once; the queue had 47 idle EL8 nodes and no rfar jobs on
2026-10-19. If one job times out, the remaining YAMLs are re-run by resubmitting
the same script: a run whose `field_0.npz` exists is skipped by the script
(check before `run-lume-ace3p`), and a run that started but did not finish is
re-run from scratch (a single-point run has nothing worth resuming).

**Outputs:** 88 workdirs `runs/phase1/bin<b>/beta<β>/`, each with `field_0.npz`,
`doseDeposit.txt`, `particles.data` and the written Geant4 input. The analysis
builds one `(β, primaries, histories, events, field)` table per bin from the
workdirs rather than from eight sweep tables, since each run is its own sweep
of one point. Copy the finished tables and fields to `stores/perbin_<date>/`.

- [x] Generator script written; 88 YAMLs and 4 batch scripts committed in the
  campaign repo; row counts in the design table reproduced by a dry run of one
  low-β and one saturated YAML per end bin.
  `configs/phase1/make_configs.py` (campaign commit eea9d20). Dry run: bin 0
  β 40/60 → 593 / 3,423 rows, bin 7 β 40/60 → 565 / 3,423, `histories_per_macro`
  reaches the written Geant4 input. Indexed mode puts each run in
  `runs/phase1/bin<b>/beta<β>_0/`.
- [x] Four jobs submitted together; job IDs logged in the campaign README.
  2026-10-19: 41324455 (bins 0,1), 41324456 (2,3), 41324457 (4,5), 41324458
  (6,7). 41324457 hit a Slurm launch failure on sdfmilan134 and was held;
  released with that node excluded, started 2 min after the others. Measured
  about 5 min per run (275 s Geant4 at 4e5 events), so about 1.8 h per job.
- [x] 88 runs complete (all `field_0.npz` present, exit 0); failures listed and
  re-run. All four jobs COMPLETED in 1:35 to 1:55; 88/88 exit 0, empty error
  files, nothing to re-run. Copied to `stores/perbin_2026-10-19/bin<b>/beta<β>/`
  (211 MB, with `inventory.json`).
- [x] Per-run fidelity (constraint #4) and bin isolation (constraint #5, by
  `Initial_z`) tabulated by `analysis/phase1_inventory.py`. 88/88 ok:
  primaries and `histories_per_macro` read from each workdir equal the design
  table, and every `particles.data` is exactly the uniform-β full run's bin-b
  rows. The sweep table has no `status` column; the script checks that
  `field_artifact` names the run's field instead. Events 400,020 to 423,468.
  Relative error in each run's 90%-dose voxels: median 5 to 14%, p90 7 to 28%,
  worst in the end bins 0 and 7 (fewest primaries, largest hpm, so the most
  correlated histories). Total dose rises about 6.5e5× from β 40 to 60 in bin 0,
  and bins 1 to 6 reach 2 to 4 Gy at β 60.

**Phase 1 review (2026-10-19, from the 88 grids before Phase 2 started).**

- *Amplitude.* Total dose per bin rises smoothly and log-linearly in β with no
  saturation (successive log10 ratios fall from 0.85 to 0.41 across 40 to 60,
  the same for every bin): the particle *set* saturates at β 52 but the weights
  keep growing exponentially. A log-space interpolant on the 11 points is enough.
- *Shape is constant in β within noise.* Normalized shape `S_b(β)` vs `S_b(60)`
  differs by relative L2 0.12 to 0.18 across β 42 to 50, against 0.10 to 0.15
  expected from the variance column; only β 40 in bins 3, 4, 6 and 7 stands
  out (0.28 to 0.53), where 565 to 1,229 primaries repeated 300 to 700 times
  make the `val^2` estimate optimistic (correlated histories). So Phase 2
  step 1's question is answered: `S_b` is a constant grid per bin, and the model
  is eight amplitude curves times eight fixed shapes. The eight shapes are well
  separated from each other (cross-bin relative L2 0.5 to 1.7).
- *Saturated-regime runs share one random sequence.* For β 52 to 60 in a bin,
  the particle file has identical rows and identical `histories_per_macro`,
  and the dose-npass binary has no seed, so the `entry` column is bit-identical
  across the five runs: they are one Monte Carlo realization with five
  weightings, not five independent samples. Averaging them does not reduce
  shape noise. Runs with different particle sets (β ≤ 50) are independent of
  each other (49% of entries equal between 50 and 60). Amend constraint #6:
  two runs are independent only if their particle file or event count differs.
- *Held-out test, Phase 2 step 3, done early.* The simplest additive model
  (constant shape from the saturated runs, log-linear amplitude) predicts the 16
  September samples with mean relative L2 **0.358**, and per sample the error is
  0.93 to 1.11 times that sample's own Monte Carlo floor from its `val^2`
  column (the September targets are `histories_per_macro = 1`). Against the
  lower-noise Phase 0 F10 run the model gives 0.159, where the direct sum of the
  eight Phase 0 bin runs gave 0.176. **The model is at the noise floor of the
  targets; the PCA-GP was at 2.10.** The Phase 2 gate is met in substance; the
  phase is now about writing it down properly, the identifiability picture, and
  the inversion rehearsal.
- *Visibility.* In every September sample one or two bins carry over 60% of the
  dose (shares from the amplitude curves), as Phase 0 showed for sample 0.

### Phase 2: additive forward model and analysis (off-repo scripts)

`analysis/perbin_model.py`, importing `lume_ace3p` but adding no code to it yet.

1. **Per-bin curves.** For each bin, the dose grids along its grid are a
   `(n_points, M)` matrix. Fit a 1-D interpolant per bin in log amplitude:
   factor each curve as `D_b(β) = a_b(β) · S_b(β)` with `a_b` the total dose
   (smooth, near-exponential in β, fit in log) and `S_b` the normalized shape
   (slowly varying; interpolate linearly or with a 1-D GP on its leading POD
   coefficients). Report how much the shape actually changes with β per bin; if
   it does not, `S_b` is a constant and the model is eight scalars times eight
   fixed grids.
   *(Answered by the Phase 1 review: constant within noise. Build `S_b` as the
   variance-weighted mean of the independent runs β 44 to 60, treating the five
   saturated runs as one realization, and report the residual shape noise.)*
2. **Forward model.** `D(β) = Σ_b D_b(β_b)` plus a variance `Σ_b Var_b(β_b)` from
   the `val^2` columns and the interpolation error.
3. **Validation against the 16 September samples.** They are full-chain real
   runs at scattered 8-D β and were not used to build the model. Report relative
   L2 in linear and log space per sample, and the MC noise floor from F1/F10 so
   the number has a reference. This is the bar the PCA-GP failed (2.10).
4. **Identifiability.** The Jacobian `∂D/∂β` has columns `∂D_b/∂β_b`, so its
   singular values say directly which bins the dose can see at a given β and at
   what noise level. Produce the rank-vs-β picture: at a uniform β, at sample 0's
   β, and at a β with one dominant bin. Compare with the "8 of 8" PCA-GP claim.
5. **Inversion rehearsal.** Invert the 16 samples' dose grids with the additive
   model (a direct bounded least squares in this phase; the repo modes come in
   Phase 3) and report recovered β against truth, per bin, with the Jacobian
   singular values beside each so a poorly recovered bin is seen to be an
   invisible one.

**Gate:** held-out relative L2 on the 16 samples at or near the MC floor, and an
identifiability picture that matches the physics (dominant bins constrained,
low-β bins flat). If the held-out error is far above the floor, the per-bin
shapes are β-dependent in a way step 1 missed; refine the interpolant before
touching the repo.

- [x] Per-bin shape variation quantified. `analysis/perbin_model.py` (campaign
  repo), 17 s on iana; numbers in `runs/phase2/phase2_results.json`, model in
  `stores/perbin_2026-10-19/additive_model.npz`.
- [x] Held-out relative L2 on the 16 samples reported with the MC floor.
- [x] Identifiability figure produced (`runs/phase2/identifiability.png`).
- [x] Inversion rehearsal table produced (printed by the script, in the JSON,
  and `runs/phase2/inversion.png`).

**Phase 2 results (2026-10-19).** Gate met; Phase 3 may start.

- *Model as built.* `a_b`: monotone PCHIP in log10(total dose) over the 11
  points. Leave-one-out error is 0.1 to 2.5% in amplitude, except at β 42 in
  bins 3 and 6 (13 to 15%, the steep onset). `S_b`: mean of five independent
  realizations (β 44, 46, 48, 50, and 52 to 60 averaged as one), weighted by a
  scalar inverse total variance. Per-voxel inverse-variance weights were not
  used: with `val^2` estimates they bias low-dose voxels down. Residual shape
  noise 3.3 to 4.9% relative L2 per bin.
- *Step 1, shape vs β.* Each run's shape against `S_b` sits at its MC-expected
  value (0.06 to 0.13 against 0.06 to 0.15) from β 42 up. Exceptions are β 40
  in bins 3 and 6 (0.48, 0.42 vs 0.06 to 0.07, the correlated-history case from
  the Phase 1 review) and, more mildly, β 40 in bins 0, 2, 4, 5 and 7 (0.13 to
  0.22). β 40 is outside the shape fit and costs nothing: at β 40 a bin carries
  under 1e-4 of any sample's dose. Within the saturated runs (a single random
  sequence, so noise-free in comparison), 52 vs 60 differs by 3 to 5.5%: that
  is the true β-dependence of the shape from re-weighting, and it sits below the
  noise of every target.
- *Step 3, held-out.* Mean relative L2 **0.353** against a mean MC floor of 0.359
  (floor = the target's `val^2` plus model variance). Ratio to floor is 0.92 to
  1.07 per sample, so no sample is off the floor. Total dose is within 0.91 to
  1.07. Log-space relative L2 is 0.29 (log10 with a floor at the smallest
  positive voxel). That is the same as F1 vs F10 in log space (0.24), so it is
  also noise. Against F10, 0.158 vs a 0.153 floor. PCA-GP: 2.10.
- *Noise model for the target.* Per-bin relative variance per event, scaled by
  primaries × hpm, predicts the September `val^2` totals within 0.35 to 3.2×
  (median 1.1). This needs a per-voxel sigma floor (1e-3 of the largest voxel
  sigma). Without it, voxels expected to see under one event get a near-zero
  `val^2` sigma, one stray hit lands thousands of σ off, and χ² per voxel
  becomes 2e6. With the floor it is 0.5 to 2.6.
- *Step 4, identifiability* (Cramér-Rao σ(β_b) from the whitened Jacobian, hpm 1
  target). At **uniform β** every bin is constrained (σ 0.05 to 0.33, end bins
  0 and 7 the weakest because they have the fewest primaries), because equal β
  means comparable dose per bin. At **sample 0's β** 4 of 8 are: bins 1 and 5
  (σ 0.05, 0.10), 0 (0.30) and 6 (0.76). Bins 2, 3 and 4 have σ 5 to 39 and bin
  7 has 1.9. At hpm 10 that becomes 5 of 8, bin 7 crossing to 0.65. With **bin 3
  dominant** (58 vs 48 elsewhere, 97% of the dose), bin 3 has σ 0.03, bins 0 to 2
  are 1.8 to 4.4, and bins 4 to 7 sit at 0.5 to 0.9: the far-side neighbours stay
  barely visible, the same-side ones vanish. This is the "dominant emitter plus
  upper limits" physics. The PCA-GP's "8 of 8" at sample 0 was wrong, as §1
  argued.
- *Step 5, inversion rehearsal* (weighted NNLS on amplitudes, IRLS, bounded
  least-squares polish in β). Across 16 × 8 = 128 bin estimates, **64 are
  constrained** (σ < 1). Their median |error| is 0.20 and the max 2.6. Only 61%
  are within 2σ, so the Cramér-Rao σ is about 2× optimistic for the visible bins:
  correlated-voxel noise is not in a diagonal `val^2` model. Unconstrained bins:
  median |error| 4.2, max 14.9, many at the lower bound 40. That is correct
  behaviour, since a dark bin is only bounded from above. On F10 (hpm 10):
  bins 0, 1, 5 and 6 come back within 0.2 to 1.3, bin 7 within 2.4 (σ 0.65), and
  bins 2 to 4 are off by 1.2 to 4.5 (σ 1.8 to 14). Not comparable: the PCA-GP's
  near-exact sample-0 recovery, because sample 0 was in its own training set.
- *For Phase 3.* Three things carry over to `AdditiveSurrogate`: the σ floor,
  scalar realization weights, and the "independent unless particle file or event
  count differ" rule. The Cramér-Rao σ needs an inflation of about 2× (or a
  voxel-correlation model) before it is reported as an error bar. Constraint #8
  (single-pass regime) still applies to the write-up.

### Phase 3: promote to the repo

Only after Phase 2 passes. One session per item, each with tests, following the
repo's existing patterns.

1. **`AdditiveSurrogate`** in `surrogate.py` (or a sibling module) with the same
   public interface as `DoseSurrogate`: `predict_dose`, `project`,
   `coeff_misfit`, `identifiability`, `invert`, `sample_posterior`, `save`/`load`,
   `voxel_indices`. The "coefficient space" is the per-bin amplitude vector, so
   `project` becomes a nonnegative least-squares fit of the target onto the eight
   shapes. `invert_optimize` and `invert_bayesian` then run unchanged against
   it; the Bayesian path needs a JAX expression of the per-bin interpolants in
   `surrogate_jax.py` with the same equivalence test the GP path has. This is also
   the `KernelSurrogate` the β-localization plan asks for, so build it once.
2. **`train_additive_surrogate` mode** (or `train_surrogate: {model: additive}`)
   reading eight per-bin sweep tables, checking constraint #5 per table and that
   the eight share one mesh fingerprint.
3. **Constraint #4 in `collect_training_data` and the sweep modes:** record
   primaries and `histories_per_macro` per row from the run, replace the typed
   `fidelity` key (keep reading it from old manifests). `read_dose_file` gains an
   optional variance return from the `val^2` column (constraint #6), persisted in
   `field.npz` as a second array so `save_field` / `load_field` need no format
   change.
4. **Example and docs:** retire or re-point `geant4_beta_surrogate` to the
   per-bin design with bounds sized under constraint #7 (decide then whether to
   move it to `fn_model: 'fn'`, which was deferred to this project by the Track3P
   plan). Fix `docs/configuration_by_mode.md`, which still says "two further
   modes" and documents only two of the four surrogate modes. Update the
   `geant4_surrogate_inversion_plan.md` status to point here.

- [x] `AdditiveSurrogate` + both inversion modes green on a synthetic additive
  fixture and on the Phase 1 store. 2026-10-20: `src/lume_ace3p/surrogate_additive.py`,
  `tests/test_surrogate_additive.py` (22 tests), checked on the real store by
  the campaign's `analysis/phase3_repo_check.py` (milano job 41405657).
  Training mode added in item 2 below.

**Phase 3 item 1 results (2026-10-20).**

- *Design.* The coefficient space is the eight amplitudes, and `project()`
  returns them packed with the upper Cholesky factor `R` of their precision
  (`B + B²` numbers). `‖R(a − â)‖²` is the voxel χ² up to a constant, so
  `coeff_misfit`, `invert` and the NUTS likelihood score in eight dimensions
  what the voxels say. `invert` is exact: the misfit is convex in amplitudes and
  a β box maps to an amplitude box, so it is one bounded linear least squares
  (BVLS), with no multi-start. `load_surrogate(model_dir)` picks the model kind
  from the files present, and the two inversion modes call it. JAX side: the
  PCHIP curves are evaluated from scipy's stored cubic coefficients
  (`amplitudes_fn`), and the existing NUTS runner is shared.
- *Noise model choice, measured on the 16 September samples.* Weighting by
  the target's own `val^2` gave visible-bin median |error| 0.90, against 0.20
  for Phase 2's model-predicted noise. A full Gaussian likelihood with σ(β)
  inside the optimizer (log-det term) was also worse (0.87). So the weights are
  the model-predicted noise at the IRLS estimate, frozen; that is a
  Gauss-Newton likelihood, and the posterior uses the same one.
- *Reported errors are inflated 2×* (`noise_scale`, from the Phase 2 finding).
  With it, the visible-bin pull scale is 1.25 and 83% of visible bins are within
  2σ (59 bins with σ < 1). Without it the Phase 2 numbers were 2.5 and 61%.
- *Matches Phase 2 exactly.* Predictions and shapes are identical to the
  analysis class, held-out mean relative L2 0.353, and recovered β agree with
  Phase 2's to 3e-6 on the visible bins. 16 inversions take 17 s.
- *NUTS on samples 0 to 3*: 3 s each, `r_hat` 1.002 to 1.016 except sample 0
  (1.11, a dark bin pinned against the lower bound). Visible bins: std 0.06 to
  0.8; dark bins: std 1.7 to 4.3, bounded from above. Truth lies in the 90% CI
  for 10 of 14 visible bins and 16 of 18 dark ones.
- [x] Training mode (item 2), 2026-10-20, as `collect_training_data`
  `design: per_bin` plus `train_surrogate` `model: additive` (the user's choice
  over a trainer reading eight sweep tables, which record neither primaries nor
  per-run hpm). `tests/test_per_bin_collection.py` (13 tests, real
  `field_emission` on a synthetic dump).

**Phase 3 item 2 results (2026-10-20).**

- *Collection.* Before any run, `plan_per_bin_primaries` reweights the dump for
  every `(bin, β)`. That gives each run's primaries, so hpm is
  `max(min_histories_per_macro, ceil(events_per_run / primaries))`, and it
  fails if a switched-off bin still emits (constraint #5) or a grid point emits
  nothing (#7). After each run the written particle source is counted against
  the plan. Per run, β and hpm go in as qualified overrides
  (`particles:beta<b>`, `geant4:histories_per_macro`); `apply_overrides` now
  routes an undeclared qualified name to its bucket rather than to cubit. The
  table records `bin`, the eight β, `primaries`, `histories_per_macro` and
  `events`.
- *Item 3 partly landed here.* `read_dose_file` returns `val2`, and the Geant4
  field artifact carries it (a new key, so no format change). Primaries and hpm
  are recorded per run in per-bin stores. Still open: the DOE collector's typed
  `fidelity`.
- *Checked on the real example.* A dry run of `grid: [40, 50, 60]`,
  `events_per_run: 4e5`, `min_histories_per_macro: 13` reproduces Phase 1's
  design (bin 0: 593 × 675, 3,373 × 119, 3,423 × 117), and hpm reaches the
  written Geant4 input. The plan takes 46 s for 24 runs on iana. The Phase 1
  runs were rewritten as a repo store by the campaign's
  `analysis/import_phase1_store.py` (`stores/perbin_repo_2026-10-20`, no Geant4).
  `train_surrogate model: additive` on it reproduces the item 1 model to 3e-15.
- [x] Fidelity recorded from the run in the DOE collector; old manifests still
  load. 2026-10-20 (item 3 complete): every sample row gets `primaries` (rows
  of the particle source the run wrote), `histories_per_macro` (from the merged
  Geant4 input, 1 when unset) and `events`. A sample skipped because its
  `field.npz` exists is read back from its workdir, so a re-run records the
  same values. `TrainingStore` gains the three arrays. The typed `fidelity:`
  key still works, with a deprecation warning, and stays in its column. The
  September store (no measured columns) loads with NaN there. Example YAML no
  longer sets `fidelity`. `docs/configuration_by_mode.md` now lists all four
  surrogate modes (an item 4 bullet, done here because the old text described
  the key being deprecated).
- [x] Docs and example updated; changelog entry. 2026-10-21: the
  `geant4_beta_surrogate` example was **retired, not re-pointed** (the user's
  call: one user, and the four YAMLs only run usefully on a milano node for
  hours). The last shipped copy, the September store (copied off scratch) and
  the per-bin configs live in the campaign directory
  (`retired_example/`, `stores/september_2026-09-10/`, `configs/phase1/`).
  `docs/configuration_by_mode.md` carries a complete per-bin pipeline in the
  example's place; the baseline registry, two gate tests and the cross-references
  in docs, READMEs and the plot script were updated. The `fn` vs `wang-loew`
  decision is moot with no shipped example; the campaign stays on `wang-loew`.
  The inversion plan is marked CLOSED and the β-localization plan notes that its
  `KernelSurrogate` is `AdditiveSurrogate`.

**Phase 3 close-out.** Still to do, outside this plan's items: bump to 0.9.0
(two new mode keys and a new model), PR dev to main, deploy. Phase 4 runs in the
campaign directory and needs no repo work unless the coarse-mesh question asks
for the lossy resample path.

### Phase 4 (optional): the research question

With a working forward model, the questions the inversion was built for:

- Given a measured dose file from a run with unknown β, which bins are the
  emitters and with what upper limits on the rest (posterior from
  `invert_bayesian`, read through the identifiability table).
- How the answer degrades with dose noise (lower `histories_per_macro` in the
  target) and with coarser scoring meshes (a downsampled target, which needs the
  lossy resample path that constraint #3 deliberately left unbuilt).

## 6. Decisions taken and open

Taken:

- Separate plan file; the surrogate + inversion plan is closed as delivered, and
  this one carries the real-data work.
- Campaign work lives outside the repo (§3); the repo changes only in Phase 3.
- Fidelity is `histories_per_macro` × primaries, recorded from the run.
- The 16 September samples are the held-out set; they are not re-run.

Open, to decide at the phase that needs them:

- ~~Whether `S_b` needs a β-dependent model at all.~~ Resolved 2026-10-19: no,
  constant per bin within the data's noise (Phase 1 review).
- ~~`wang-loew` vs `fn` for the shipped example (Phase 3 step 4).~~ Moot
  2026-10-21: no shipped example. The campaign stays on `wang-loew` so the 16
  held-out samples remain comparable.
- Whether a thread count below 120 is worth it for the 3,423-primary end-bin
  runs (not unless queue time becomes the bottleneck; one job at a time anyway).

## 7. Operational notes

- Ground rules from `plans/s3df_example_validation_plan.md`: sbatch to
  `--partition=milano --account=rfar:regular --constraint=OS_VER:8.10`, one job
  at a time, source `~/geant4.sh` before `conda activate lume-ace3p-dev`, never
  run the binary on iana.
- Concurrency: the user is the rfar czar and approved **four concurrent
  one-node jobs for Phase 1** (2026-10-19). Any other phase stays at one job at
  a time unless approved again. Check `squeue -p milano -o "%a %u %t" | grep
  rfar` and `sinfo -p milano -t idle -o "%D %f" | grep 8.10` before submitting;
  the four jobs only help if EL8 nodes are idle.
- Each Geant4 step is `srun -n 1 -c 120`; `--cpus-per-task=120` must match
  `geant4_threads`.
- A `parameter_sweep` left at `workdir_mode: manual` overwrites itself; every
  campaign YAML sets `workdir_mode: indexed`.
- `on_error: skip` means a failed point is a NaN row with no field; check the
  `status` column before analysis and re-run failures with `resume: true`.
- The dose-npass README and the app's own `input.txt` are at
  `/sdf/group/rfar/geant4/example/dose-npass/`; the Phase 4 polycone app is a
  different binary and not used here.
