# Field-emission β localization from cryomodule dose data

**Status:** DRAFT — feasibility scoped, not started.
**Target:** infer field-enhancement factors β at the irises of the eight 9-cell
cavities in CM01, from archived Gradient-ACT and DecaRad dose time series.
**Revision:** 2026-09-03, after review of Frosio et al. NIM-A 1086 (2026) 171340
(§12). Changed: §8/§9 re-tabulated on `E_pk = 2·E_acc` with β = 60–300 (the
earlier exponent numbers were ~2× pessimistic, and the binding constraint is κ
collinearity, not Fowler–Nordheim); Step 0's additivity gate rewritten (the
sub-additivity is expected physics with a published mechanism, not a model
failure); monitor count corrected from 8 to **10**; new **Step 0b / Phase 0b**
extracting β ratios from onset gradients with no simulation; new constraint #9;
§2 record-completeness check; §10 regrouped.

---

## 1. Objective, honestly scoped

Given measured radiation dose at ten monitor locations while cavities are
powered one at a time, infer the field-enhancement factor β at each of the ten
irises of each cavity.

**What is achievable per cavity, per stationary epoch:**

- the β of the *dominant* emitter, to the precision with which the cavity
  gradient is known (see §9 — this is an exact 1:1 relationship, not an
  approximation);
- a probabilistic statement about which iris hosts it;
- 95% upper limits on the remaining nine β;
- **β *ratios* between cavities, and a per-cavity emission direction, with no
  simulation at all** (Step 0b) — a quantitative upgrade of the published
  qualitative result (§12) and the project's fallback deliverable.

**What is not achievable:** ten resolved β per cavity. The Fowler–Nordheim
exponential puts a hard floor here — an iris below roughly two thirds of the
dominant β is invisible at any gradient and any noise level (§8). The deliverable
must be written up as "dominant emitter + upper limits", never as a
ten-component β vector with error bars, or it will overstate what the data
support.

**What actually decides the interesting question:** how many *resolvable*
emitters (2–4, per §8) can be separated depends on κ collinearity, not on the FN
exponent. The earlier draft named the exponent as the binding constraint; §8 now
shows that was a factor-two arithmetic artifact, and the measured monitor
point-spread function is the real limit. Phase 5 is the feasibility verdict.

---

## 2. The data

One cryomodule (CM01), eight 9-cell TESLA-type cavities, 72 cells total.

**Monitor complement: ten DecaRad dosimeters, not eight.** Eight sit in front of
the eight cavities; **two more sit at the upstream and downstream ends** of the
cryomodule (Frosio et al. §4.b.ii and Fig. 15's ninth panel — see §12). The end
pair is what carries the forward/backward asymmetry, and forward/backward is the
one origin discriminant that is *not* near-degenerate across the irises of a
single cavity. All ten must be modelled and scored. Earlier drafts of this plan
said eight throughout; that has been corrected in Step 4, §6 and constraint #4.

Single 15-minute record, `06-Oct-2023 08:20:00` onward:

- **t ≈ 0–0.013 h** — all eight cavities on simultaneously, several at
  ~16 MV/m ("the all-on burst").
- **t ≈ 0.02–0.25 h** — cavities powered one at a time, ~2–3 min per window.
- Monitors are **10-second means**, in mR/hr. Gradient traces appear to be
  sampled much faster.

Per-cavity gradient profile, which determines how much sweep information each
window carries:

| Cavity | profile in its window | distinct gradient levels |
|---|---|---|
| C1 | ramp 4 → 16 MV/m over ~130 s | ~13 |
| C2 | **never individually powered** | 0 |
| C3 | ~flat at 10 MV/m, slow drift | few (drift only) |
| C4 | ~flat at 9 MV/m, slow drift | few (drift only) |
| C5 | ramp to ~13 MV/m | ~10 |
| C6 | ramp to ~17 MV/m over ~72 s | ~7 |
| C7 | ramp to ~17 MV/m over ~90 s | ~9 |
| C8 | ramp to ~16 MV/m | ~8 |

Two consequences that shape the whole plan:

- **C2's ten β are unidentifiable from this dataset.** It appears only inside
  the all-on burst, confounded with seven other cavities. The tractable problem
  is 70 β, not 80.
- **Time slices are not free samples.** A slice adds information only if it
  sits at a *distinct* gradient. On a flat top, the ~12–18 slices are repeat
  measurements of one point: they average down counting noise and add nothing
  about β. Because ∂lnI/∂lng ≈ 100, however, even a 1% within-window gradient
  drift produces an e-fold in dose, so the C3/C4 windows are not
  information-free — their slow drift *is* the sweep. Mining it requires
  knowing the gradient to much better than the drift, and forward-modelling the
  10-second boxcar (§7, constraint #7).

### Is this record complete? — resolve before Phase 1

Three signs that the 15-minute pull may be a **truncated slice of a longer run**,
and that the missing part is the most valuable part:

- **The window budget does not close.** t ≈ 0.02–0.25 h is 13.8 min, which is
  1.7 min per cavity across eight windows, not the 2–3 min the windows appear to
  occupy. Something is cut off.
- **No repeat segments.** The equivalent CM08 run (Frosio et al. Fig. 15) is
  ~31 min and its gradient axis reads `1 2 3 4 5 6 7 8 | 2 | 3 | 6` — after the
  first pass, the *identified emitters* are re-powered for a second, usually
  higher or finer, ramp. Cavities 2, 3, 6 are exactly the three CM08 rows of
  their Table 1. A CM01 run of the same design should therefore re-power
  cavities **2, 3, 5, 8** (the CM01 rows of Table 1).
- **C2 is listed here as never individually powered**, yet Table 1 reports CM01
  cavity 2 as an emitter (>15 MV/m, backward). Either their CM01 result comes
  from a different run, or C2 *is* powered in the part of the record not pulled,
  or the trace-to-cavity mapping in the table above is off by one.

The all-on burst is also much shorter here (47 s) than CM08's (~5 min).
**Action:** re-query the archive for the full run before committing to the
identifiability estimates in §2's table — recovering repeat ramps on C2/C3/C5/C8
would change Phase 5's answer more than any modelling choice available in Phases
1–4. Verifying the trace-to-cavity mapping against Table 1 is a free consistency
check on the ingest.

Beyond re-querying the archive, no *new* measurements can be requested; the
experimental setup is not accessible. The staircase-gradient and
closing-all-on-burst improvements that would sharply improve identifiability are
recorded in §11 for any future run, but are out of scope here.

---

## 3. Physics basis: the κ factorization

β enters the pipeline **only** as a per-particle Fowler–Nordheim weight
(`particles.py:64-72`), and Geant4 dose is linear in event weights (dark current
is nA–µA, so no space charge and no field loading). The forward map therefore
factorizes exactly:

```
                    ┌ closed form, carries all β ┐   ┌ Geant4, β-free ┐
D_m(β, g)  =  Σ_p   │  (Δt/q_e) · J(β_i(p) · E_p) · A_p │ · │   κ_p,m(g)     │
```

where `p` indexes Track3P trajectory rows, `E_p` = `InitialNormalField`,
`A_p` = `InitialFaceArea`, `i(p)` = origin bin from `Initial_z` via pinned
`bin_edges`, and

```
J(x) = [1.54e-6 · 10^(4.52/√φ) / φ] · x² · exp(-6.53e9 · φ^1.5 / x)
```

**κ_p,m is the expected monitor-m response per single electron launched with
trajectory p's impact state** (position, direction, kinetic energy). It is a
property of geometry and transport only, and contains no β.

This is the pivotal structural fact for the whole project: the expensive
simulation is computed **once**, independent of β, and every likelihood
evaluation afterwards is a dot product. It means:

- No design-of-experiments over β, and no learned surrogate, is required. The
  κ table *is* the surrogate — a linear-response one that is exact rather than
  fitted.
- The 10-second boxcar can be forward-modelled by quadrature (§7) — free here,
  awkward-to-impossible with a black-box surrogate over discrete β.
- κ's Monte-Carlo error is a measurable, stationary systematic that can be
  propagated as a fixed covariance, unlike a GP's entangled epistemic error.

The existing PCA-GP path (`DoseSurrogate`) is retained only as an independent
cross-check (§6, Phase 4), not as the primary route.

---

## 4. Process outline

*(This section is written to be lifted into the technical note.)*

- **Step 0 — Characterize the data, and do *not* use the all-on burst as an
  additivity test.** No simulation. Fit a single-emitter FN curve per cavity from
  its individual window, extrapolate each to its all-on-burst gradient, sum over
  the eight cavities, and compare to the measured burst at all ten monitors.
  - Reading the figure suggests **sub-additivity**: C3 alone and C4 alone each
    drive Mon5 to ~45–50 mR/hr, yet all eight cavities together also give ~50.
  - **This is expected physics, not a model failure, and the mechanism is
    published.** Frosio et al. observed the same thing inside CM08 and diagnosed
    it: switching cavities 1–5 off took RDM LI02 from 50 → 12 mSv/h, but
    switching 6–8 off *as well* pushed it back **up** to 28 mSv/h. Their
    explanation is that cavities 1–5, *when powered*, capture and re-accelerate
    backward emission arriving from downstream; de-powering them removes that
    transport channel. Dose is linear in emitted current at a **fixed field
    configuration** — which is all §3 claims — but κ itself is a function of
    which cavities are powered and at what relative phase. The burst and the
    one-at-a-time windows are therefore *different κ problems*, and summing the
    latter to predict the former is not a valid superposition test.
  - Consequently Step 2 must not assume the burst is field-free outside the
    powered cavity: predicting the burst requires **its own κ run with all eight
    cavities energized**, plus a relative-phase assumption. Phase matters a lot —
    Frosio et al. Fig. 10 gives mean captured energy ~50 MeV (max 140) in phase
    versus ~20 MeV (max 80) in SELA, a factor ~2.5 in mean energy and hence a
    large change in both dose per electron and shielding penetration.
  - Remaining candidate causes for residual sub-additivity, **reordered**:
    configuration-dependent transport (above, most likely); gradients differing
    between burst and individual windows; the 10-s mean clipping a short
    transient; DecaRad saturation or dead time (**demoted** — Frosio et al.
    Fig. 15 shows per-cavity DecaRad axes topping out between 0.15 and
    2.0 mSv/h, with the Cav1 panel reaching 2.0, so 50 mR/hr = 0.5 mSv/h is ~4×
    below a value the same instrument family reads elsewhere).
  - **Gate, restated.** Pass = the per-cavity windows are internally consistent
    (a single-emitter FN curve fits each window's dose-vs-gradient at all ten
    monitors within calibration error) and the record is stationary across
    repeated gradients. The burst is deferred to Phase 4 as a *geometry*
    validation target under its own κ, not used as a go/no-go here. The old gate
    — "if additivity fails, stop" — would have killed the project for a reason
    the literature already explains.

- **Step 0b — Extract β *ratios* from onset gradients. No simulation at all.**
  This is a standalone deliverable and should be taken before any of Steps 1–6.
  - Current depends on β and field only through the product `β·E_pk`. So the
    gradient at which a cavity's dose crosses a fixed detection threshold
    satisfies `β_i · E_pk,i(onset) ≈ const`, and therefore
    **`β_i / β_j ≈ E_pk,j(onset) / E_pk,i(onset)`** — a β *ratio* measurement
    requiring no Track3P, no Geant4, and no κ, only the gradient traces and a
    consistently defined threshold.
  - This reproduces Frosio et al. Table 1 while upgrading it from qualitative
    (`CM, cavity, onset, direction`) to quantitative. Their CM01 rows give
    cavity 2 > 15, cavity 3 > 10, cavity 5 > 10, cavity 8 > 15 MV/m, so e.g.
    `β_3/β_2 ≈ 1.5` falls straight out and is directly checkable against our
    record.
  - **Why it is robust:** emitting area and κ enter the prefactor while β sits in
    the exponent, so a factor-*X* error in `A·κ` displaces the inferred `β·E_pk`
    by only `ln X / (2 + u)` — ~7% for `X = 10` at u = 32, ~20% at u = 10 (§8).
    For *ratios between cavities* of nominally identical geometry, the common
    part of that error cancels and only the differential survives, which is far
    smaller than 10×.
  - Also record the per-cavity **direction** (forward / backward /
    both) from the two end dosimeters, in Table 1's format, so the comparison is
    like-for-like.
  - **Gate.** Onset gradients extractable for ≥6 of 8 cavities, and the
    direction assignment reproduces Table 1 for CM01 where Table 1 has an entry.
    Disagreement here means the ingest or the trace-to-cavity mapping is wrong
    (§2) and must be fixed before Phase 1.

- **Step 1 — Omega3P: dominant accelerating mode of one 9-cell cavity.**
  - Solve the π-mode (1.3 GHz, TESLA-type) on a single 9-cell cavity. All eight
    cavities are nominally identical, so **one solve is reused for all eight**,
    scaled per cavity by its own measured gradient.
  - Extract the peak surface field at each of the ten irises, `E_local,i`. β and
    field enter only as the product `β_i · E_local,i`, so the fractional spread
    in `E_local,i` (field flatness, typically 1–3%) is the entire budget for
    separating equal-β irises by field alone. Record it; it drives Step 5.
  - **Sanity target: `E_pk/E_acc ≈ 2.0`** for a TESLA-type 9-cell, with the peak
    *electric* surface field located at the **irises**. This is the physical
    justification for binning β at the irises and should be stated as such in the
    note rather than left implicit. It is also the correction that re-scales all
    of §8 and §9 — every sensitivity must be evaluated at `E_pk`, roughly twice
    the accelerating gradient, never at `E_acc`.
  - A nominal eigenmode discards *per-cavity* flatness differences. If cavity
    qualification data include measured flatness, perturb the cell amplitudes
    accordingly; otherwise state the assumption.

- **Step 2 — Track3P: emit and collect trajectories in the full 72-cell
  geometry.**
  - Geometry is the whole eight-cavity string; for the one-at-a-time windows,
    fields are non-zero only in the powered cavity and zero elsewhere. Electrons
    that leave the powered cavity are then tracked through the field-free
    downstream/upstream cells in the same run, which is what makes the
    far-monitor signal available (see constraint #6).
  - **The all-on burst is a separate configuration, not a sum of these runs.** It
    needs its own Track3P library with all eight cavities energized and a stated
    relative-phase convention (constraint #9). Do not attempt to synthesize it
    from the single-cavity libraries.
  - Emit from every surface triangle **above a local field threshold**, at
    **every RF phase on a fine grid** (§8). The threshold and the phase span are
    both functions of the largest β admitted and must be sized from it, not
    fixed — see §8, which supersedes earlier fixed values of 0.7 `E_pk` and ±40°.
    Record for each launch: origin position, `InitialNormalField`,
    `InitialFaceArea`, launch phase, and impact position / energy / direction.
  - **One run per distinct gradient.** Trajectories are not scale-invariant
    (relativistic dynamics), so the map must be rebuilt at each gradient. But
    identical geometry means the map at gradient *g* is **reusable across all
    eight cavities** — only the longitudinal placement in the Geant4 geometry
    differs. Track3P cost is therefore (number of distinct gradients), not
    (8 × that).
  - Track3P is run externally and its dump supplied through the
    `track3p_source` module; there is no in-pipeline tracker.

- **Step 3 — Fowler–Nordheim weighting.**
  - Assign each launch to an origin bin from `Initial_z` using `bin_edges`
    pinned at the iris z-positions (constraint #1).
  - Weight per launch: `w_p = J(β_i · E_p) · A_p · Δt / q_e`, in **float** —
    never rounded to integer electrons (constraint #5).
  - Because the weight depends on β and the impact state does not, this step is
    the *only* β-dependent stage, and it is closed-form.

- **Step 4 — Geant4: build the κ table.**
  - Stratify launches into cells `c = (origin bin i, E_p decile)`, ~60 cells per
    gradient. Run Geant4 with unit-weight primaries per cell and score the
    **ten monitor volumes** — eight in front of the cavities plus the two
    cryomodule-end dosimeters (§2) — to get `κ̄_c,m(g)`, with per-cell MC standard
    errors stored alongside.
  - Stratification by `E_p` is mandatory even though κ does not depend on
    `E_p`: FN reweighting within an iris shifts the effective impact-state
    distribution toward the highest-field faces, so an iris-averaged κ is a
    β-dependent quantity masquerading as a constant.
  - Use ten small dedicated scorers at the surveyed monitor positions, not a
    coarse global dose mesh, and score the **monitor's energy-dependent
    response** rather than absorbed dose (constraint #4).
  - **The two end scorers are cheap and carry disproportionate information.**
    They are the only monitors that distinguish forward from backward escape,
    which is the origin discriminant least degenerate across the irises of one
    cavity, and they are the basis of the published directional result this study
    is compared against (§12).
  - Cost: (number of gradients) × ~60 independent, embarrassingly parallel runs
    per source cavity — or one run per gradient if deposits can be tagged by
    primary group.
  - Cross-check: κ should be approximately translation-invariant in the lattice
    interior (`κ_k,m ≈ κ_1,m-k+1`). Verify rather than assume; failures localize
    end effects and geometry errors.

- **Step 5 — Assemble the forward model and quantify identifiability.**
  - `D_m(β, g) = Σ_i Σ_{c⊆i} W_c(β_i, g) · κ̄_c,m(g)`, with `W_c` summed exactly
    per particle (no binning in `E_p` needed for the weight).
  - Wrap the 10-second boxcar: `D̂_m,t = (1/T)∫ D_m(β, g(τ)) dτ` by quadrature
    over the fast gradient trace (constraint #7).
  - SVD the Jacobian `∂lnD_m,t / ∂lnβ_i` over the *actual* (monitor × time-slice)
    grid to get the effective rank and the constrained directions. Report how
    rank grows with added gradient points — expect saturation by ~10.
  - Validate against the full existing pipeline at 5–10 β vectors; agreement
    within MC error is the go/no-go for replacing the learned surrogate.

- **Step 6 — Inversion against the measurement data.**
  - Likelihood per monitor *m* and slice *t*:
    `D_obs ~ Normal(α_m · D̂_m,t(β) + b_m, σ²)`, `σ² = σ0_m² + (f_m · D̂)²`,
    with per-monitor gain `α_m` (~10% prior) and background `b_m` as sampled
    nuisances. Sample `log β_i`, not `β_i`.
  - Segment the record into stationary epochs first, using the repeated-gradient
    reproducibility available in the trace; fit within epochs only.
  - Run point-estimate and Bayesian inversions; report dominant iris, its β,
    and 95% upper limits on the rest. Posteriors for non-dominant β are
    *shelves* — flat at the prior below a threshold, cliff above — so a mean ± sd
    would read as a measurement where there is none.
  - Held-out validation: predict the all-on burst from the per-cavity fits and
    compare — but **through the burst's own κ table** (Step 2), not by summing
    single-cavity predictions. This is a joint test of geometry fidelity and of
    the re-acceleration physics Frosio et al. identified in CM08; a clean pass is
    strong evidence the κ columns are trustworthy, and a failure localizes to
    either shielding fidelity (§9) or the relative-phase assumption.
  - Report results in Frosio et al. Table 1's format alongside the β posteriors,
    so the cavity-level conclusions are directly comparable to the published CM01
    rows even where the iris-level claim is only an upper limit.

---

## 5. Phases and gates

| Phase | Content | Gate |
|---|---|---|
| 0 | Step 0 data characterization, DecaRad linearity, record completeness (§2) | per-cavity windows internally FN-consistent; record stationary across repeated gradients |
| 0b | **Step 0b onset-gradient β ratios + direction, data only** | onsets for ≥6 of 8 cavities; direction reproduces Table 1 for CM01 |
| 1 | Omega3P π-mode, `E_local,i` extraction | field flatness spread recorded; `E_pk/E_acc ≈ 2.0` confirmed |
| 2 | Track3P 72-cell library, one per gradient (+ one all-on) | phase convention, span/threshold sizing, and escape handling verified (§7 #2, #6, #9) |
| 3 | Geant4 κ table + MC errors, **10 monitors** | ≤5% SE per (cell, monitor) on cells carrying ≥1% of dose |
| 4 | Forward model + pipeline validation + all-on burst under its own κ | agreement within MC error away from the rounding floor |
| 5 | Jacobian SVD / identifiability | *this phase is the feasibility answer* — no pass/fail |
| 6 | Synthetic recovery with realistic noise | identifiable combinations recovered, upper limits correctly covered |
| 7 | Real-data inversion, C1 then C3–C8 | posterior predictive check on held-out slices; cavity-level result consistent with Phase 0b and Table 1 |

Phases 0–6 are simulation-only, and **Phase 0b is a publishable deliverable on
its own** — a quantitative β-ratio and direction table for CM01, with no
simulation, comparable directly to the published qualitative table. It is the
project's insurance policy: it lands a result even if Phase 5 returns rank 1.

Phase 5 determines what Phase 7 can claim: rank ≥ 3 makes iris localization
viable; rank 1 means the dominant β and nothing else.

---

## 6. Reuse map to existing code

| Need | Existing asset | Change |
|---|---|---|
| FN weighting | `particles.py` `Particles` | float weights; 10 (or 11) bins; pinned `bin_edges` |
| Track3P dump ingest | `track3p_source` module | none |
| Geant4 driver | `geant4` module | **10** monitor scorers (8 per-cavity + 2 cryomodule-end); response-weighted scoring |
| Training store | `surrogate_data`, `collect_training_data` | reusable for the κ campaign |
| Identifiability | `DoseSurrogate.identifiability` (`surrogate.py:399`) | same construction, applied to the analytic Jacobian |
| Point inversion | `modes.invert_optimize` (`modes.py:1024`) | retarget to κ model |
| Bayesian inversion | `modes.invert_bayesian` (`modes.py:1199`), `surrogate.sample_posterior` (`surrogate.py:527`) | retarget; keep `num_chains=4` and `dense_mass=True` |

**Recommended architecture:** implement a `KernelSurrogate` exposing the same
public interface as `DoseSurrogate` (`predict_dose`, `project`, `coeff_misfit`,
`identifiability`, `invert`, `sample_posterior`, `save`/`load`) backed by the κ
table instead of PCA-GPs. The downstream inversion modes then work unchanged.

Note that `DoseSurrogate`'s identifiability result carries a construction
artifact that does **not** apply here: `rank(∂c_GP/∂β) ≤ k` because β reaches the
model only through *k* retained POD coefficients. The analytic model has no such
ceiling, so Phase 5 yields the first structurally honest rank for this problem.

---

## 7. Correctness constraints

Numbered to continue the project's existing series (#1 pinned `bin_edges`,
#2 genuine GP noise term, #3 pinned scoring mesh).

- **#1 (restated, now physical).** `bin_edges` must be set explicitly at the
  iris z-positions. The default in `particles.py:54` is data-driven
  (`z_vals.min()..max()`) and drifts per run. Here the bins *are* the physical
  unknowns, so a drifting edge silently redefines β.

- **#4 — Score the monitor response, not absorbed dose, at all ten monitors.**
  DecaRad readings are mR/hr from an energy-dependent detector. Folding the
  response function into the Geant4 scorer makes `α_m` a pure scalar gain;
  omitting it makes the gain spectrum-dependent, and the spectrum varies with
  emission origin — corrupting exactly the origin discrimination the project is
  trying to extract. The monitor count is **ten** (§2), and the two end
  dosimeters are the ones the direction result rests on.

  Note that the DecaRad response curve is **not** obtainable from Frosio et al.
  They publish calibrations for the Cherenkov fiber (7 mV/W), the diamond
  (125 mW/mV at CYC13) and the RDM model (Thermo-Fisher FHT 190, with a datasheet
  footnote), but nothing for the DecaRad. Ask the authors — see §10.

- **#5 — Float weights in the analytic path.** `particles.py:72` rounds weights
  to integer electrons and `particles.py:106` drops the zeros, making dose
  exactly 0 below a β threshold: a non-differentiable floor that corrupts both
  the Phase-4 comparison and any gradient-based or MCMC inversion. Where the
  analytic model and the pipeline disagree at low weight, the *pipeline* is
  wrong.

- **#6 — Escaping particles must reach Geant4.** `particles.py:44-47` selects
  rows by `ImpactOrder` and `ImpactFaceID`, i.e. wall impacts. A several-MeV
  electron that exits the powered cavity is precisely what lights up distant
  monitors, and the data show large far-monitor signal. Running Track3P in the
  full 72-cell geometry addresses this by keeping the escape inside the tracked
  domain — but verify that such rows survive the impact filter and are not
  silently dropped.

- **#7 — Pin the phase sampling, and derive `Δt` from it.** `Δt` is the time
  slice one launch represents, so it must equal the phase-grid spacing:
  `Δt = Δφ_deg / (360 · f_RF)`. At 1.3 GHz with Δφ = 2° that is **4.3 ps**, not
  the `1.0e-10` in `examples/geant4_track3p_beta/geant4_track3p_beta.yaml` (which
  corresponds to Δφ ≈ 47°). A mismatch scales absolute dose by the ratio, and an
  inconsistent phase grid between gradient runs corrupts the axis being inferred.

- **#8 — Forward-model the 10-second boxcar; never pair 10-s mean dose with
  10-s mean gradient.** Dose is exponentially convex in *g*, so during a ramp the
  bin mean is not the dose at the bin's mean gradient. For a C1-like ramp
  (4 → 16 MV/m accelerating in 130 s), evaluated correctly at
  `E_pk = 2 · E_acc` (§8):

  | bin (accelerating *g*) | `E_pk` across bin | ⟨I⟩/I(⟨g⟩), β = 60 | β = 200 |
  |---|---|---|---|
  | early (~6 MV/m) | 11.7 → 13.5 | **35** | **1.6** |
  | mid (~10 MV/m) | 19.1 → 20.9 | **2.3** | **1.09** |
  | late (~15.5 MV/m) | 30.2 → 32.0 | **1.18** | **1.06** |

  These supersede the earlier draft's 4044 / 11.8 / 1.8, which were computed with
  `E_acc` in place of `E_pk` and so roughly doubled `u`. **The constraint stands
  but is far less severe than previously stated** — at the likely β it is a
  tens-of-percent effect at low gradient rather than a factor of thousands. It
  still matters, because the bias is gradient-dependent and therefore corrupts
  the FN slope and hence β directly. Resolve by quadrature over the fast gradient
  trace; the cost is negligible either way.

- **#9 — κ is a function of the field configuration, so tag every κ table with
  it.** Which cavities are powered, and at what relative phase, changes electron
  transport and therefore κ — the mechanism behind the CM08 non-monotonicity in
  Step 0. A κ table computed for "only cavity *k* powered" is invalid for the
  all-on burst and vice versa. Store the configuration as part of the table's
  identity and refuse to mix them at prediction time. Two corollaries:
  - Step 4's translation-invariance cross-check (`κ_k,m ≈ κ_1,m-k+1`) is only
    expected to hold *within* a single configuration.
  - Whether the neighbouring cryomodules were powered during our CM01 windows is
    now a first-order question, not a detail — see §10.

---

## 8. Identifiability: what the FN exponent does and does not forbid

**This section was revised after reviewing Frosio et al. (§12); it supersedes the
earlier "why ten β per cavity is unreachable".** The conclusion is unchanged in
kind — ten resolved β remains out of reach — but the earlier numbers were
pessimistic by roughly a factor two in the exponent, and the *binding constraint*
turns out not to be the one previously named.

β and field enter only as the product `β_i · E_local,i`, so every sensitivity in
this plan is a function of a single dimensionless quantity:

```
u  =  C / (β · E_pk),      C = 6.53e9 · φ^1.5 = 6.23e10 V/m   (φ = 4.5 eV)
```

Two corrections, both of which move `u` **down** and therefore make
identifiability **better** than previously stated:

- **Evaluate at the peak surface field, not the accelerating gradient.** For a
  TESLA-type 9-cell `E_pk/E_acc ≈ 2.0`, and the peak electric surface field sits
  at the **irises** — which is the physical justification for the iris binning.
  At the 16 MV/m accelerating operating point `E_pk ≈ 32 MV/m`, not 16. The
  earlier draft's `u ≈ 65` at "β = 60, 16 MV/m" plugged `E_acc` into an `E_pk`
  slot; the correct value is `u ≈ 32`.
- **β is probably in the low hundreds, not 40–100.** Fitting the exponent of
  Frosio et al. Fig. 11 (captured current vs gradient, entire cryomodule
  synchronized) gives `∂lnI/∂lng ≈ 12.6` over 15 → 21 MV/m, falling to ≈ 6.5 by
  36 MV/m. Via `∂lnI/∂lng = 2 + u` that implies `u ≈ 5–11`, i.e. β of order
  150–450 if their axis is surface field, or 100–250 if it is accelerating
  gradient. **Treat this as an order-of-magnitude anchor, not a measurement** — it
  depends on their axis convention and on the uniformly-distributed-emitter model
  of their ref. [21], which should be read before relying on it. But it does rule
  out β ≈ 60 as this plan's design point.

Working range at `E_pk = 32 MV/m`:

| β | u | ∂lnI/∂lnβ ≡ ∂lnI/∂lng |
|---|---|---|
| 60 | 32.5 | 34.5 |
| 100 | 19.5 | 21.5 |
| 200 | 9.7 | 11.7 |
| 300 | 6.5 | 8.5 |

### Contribution of a sub-dominant emitter

Relative current from a second iris at `β₂/β₁ = r`, as `r² exp(-u(1/r - 1))`:

| β (u) | r = 0.95 | 0.90 | 0.80 | 0.50 |
|---|---|---|---|---|
| 60 (32.5) | 0.16 | 2e-2 | 2e-4 | 2e-15 |
| 100 (19.5) | 0.32 | 9e-2 | 5e-3 | 8e-10 |
| 200 (9.7) | 0.54 | 0.27 | 6e-2 | 2e-5 |
| 300 (6.5) | 0.64 | 0.39 | 0.13 | 4e-4 |

The visibility floor is therefore **~65–70% of the dominant β** at the likely
operating point, not the ~80% previously claimed, and an iris at 90% contributes
a quarter to a third of the signal rather than 2%. **Expect 2–4 resolvable
emitters per cavity, not 1–3.** Ten remains unreachable: below ~half the dominant
β an iris is invisible at any gradient and any noise level.

### The binding constraint is κ collinearity, not the FN exponent

This is the substantive change. The measured dose patterns confirm the
*cavity*-level problem is information-rich: C3-on peaks at Mon5, C4-on peaks at
Mon3, C5-on peaks at Mon6/7 — genuinely different, structured signatures,
including substantial signal upstream of the powered cavity. But that only
discriminates *which cavity* is emitting, which the one-at-a-time schedule
already tells us.

For the iris-level question the empirical evidence is now **worse** than the FN
evidence. Frosio et al. Fig. 15 shows that powering a single cavity produces dose
at roughly four adjacent dosimeter positions — cavity 2 above 13 MV/m lights
positions 1, 2 and the upstream end; cavity 3 above 12 MV/m lights positions 1–4;
cavity 6 above 13 MV/m lights positions 4–7. The paper calls this "a
non-localized effect of field emissions". That is a measured point-spread
function several times wider than the ~1 m cavity inside which the ten irises
live.

Two things keep this from being fatal:

- A known PSF can be deconvolved below its own width given sufficient SNR, and
  §9 shows the dominant β is pinned to ~0.1% by 1% dose accuracy.
- The spread appears *upstream* of the powered cavity, which means it is electron
  **transport** — precisely what Track3P + Geant4 model — and not photon scatter
  in a geometry we would have to guess at.

So it is modelable in principle, and the limiting factor becomes the fidelity of
κ (shielding, §9) rather than the width of the response. But it sets the honest
prior: origin information is concentrated in the nearest one or two monitors, and
**effective rank 2–3 remains the working estimate.** Phase 5 decides, and it is
now decidedly the interesting phase.

### Emission-region assumptions

Relative contribution of a face at `E/E_pk = f` is `f² exp(-u(1/f - 1))` — the
same expression as above, since a field deficit and a β deficit are
interchangeable. At `u = 32.5`: 0.16 at f = 0.95, 2e-2 at 0.90, 2e-4 at 0.80,
4e-7 at 0.70. At `u = 9.7`: 0.54, 0.27, 6e-2, 8e-3, and still 5e-4 at f = 0.60.

**Consequence: the emission-area threshold must not be fixed at 0.7 `E_pk`.** At
β = 60 that cut is safe and buys ~two orders of magnitude in trajectory count; at
β = 200 the retained-but-marginal faces contribute ~1% each and are not
negligible once summed over many faces. Set it from `u` for the largest β
admitted — roughly the `f_min` where `f² exp(-u(1/f-1)) < 1e-4`, which is 0.72 at
u = 32.5 but 0.53 at u = 9.7 — and record that the cut is a **prior on β
contrast**: a face at 0.5 `E_pk` with β = 400 outranks a face at `E_pk` with
β = 200.

The iris-bin parameterization also **assumes emitters sit at the irises**. This is
well motivated for a π-mode 9-cell (the electric field maximum *is* the iris,
per Step 1), but a particulate on an equator weld, a beampipe flange, or an HOM
coupler is not representable and its signal will be misattributed to the nearest
iris. Given the size of the C3/C4 doses this stays a live hypothesis: keep at
least one non-iris bin, and read poor residuals as evidence of an unmodelled
origin rather than tuning β harder.

### Emission phase window

Emission concentrates near the field crest, so the phase scan can be narrow but
must be fine. Half-width about the crest, from `-u(1/cos δ - 1) + 2 ln cos δ`:

| β (u) | J > J_max/e | > J_max/1e2 | > J_max/1e4 |
|---|---|---|---|
| 60 (32.5) | 13.6° | 28.1° | 38.0° |
| 100 (19.5) | 17.2° | 34.7° | 45.8° |
| 200 (9.7) | 22.9° | 44.5° | 56.7° |
| 300 (6.5) | 26.8° | 50.4° | 62.7° |

The earlier "crest ±40° at 2°" is adequate only for β ≲ 60. **Scan crest ±75° at
~2°** to cover β up to 300 with margin — 76 phase points instead of 41, so
roughly 2× the Track3P row count, which the relaxed area threshold above partly
pays for. The window widens with β, so size it for the largest β admitted, and
note again that **the phase weight depends on β, so the phase integral cannot be
pre-collapsed** — keep every (triangle, phase) pair as its own row, which is what
`Particles` already does. `Δt` follows from Δφ, not from the window width
(constraint #7), so widening the span does not change it.

---

## 9. Error budget

Because `J` depends on β and *g* only through the product `βE`, the
sensitivities are *identically* equal: `∂lnI/∂lng ≡ ∂lnI/∂lnβ = 2 + u`. At
`E_pk = 32 MV/m` (§8) that is ≈ 34 at β = 60 and ≈ 12 at β = 200 — the earlier
draft's 67/106 used `E_acc` in place of `E_pk`. Therefore:

- **A 1% error in the gradient signal is exactly a 1% error in β.** Not
  approximately — exactly, and independently of `u`. No quantity of monitor data
  and no modelling improvement removes this floor. If Gradient ACT is calibrated
  to a few percent, absolute β lands at a few percent, dominating monitor noise by
  a wide margin.
- **Watch for a MV vs MV/m ambiguity in the gradient PV.** Frosio et al. use
  "MV" and "MV/m" interchangeably (Table 1 reads ">15 MV"; the text says cavities
  were "raised to 17 MV"), which is survivable for them because a TESLA 9-cell
  active length is ~1.04 m so the two differ by only ~4%. Here a 4% gradient
  ambiguity is a **4% error in β directly**, comparable to the entire rest of the
  budget. Confirm the unit and the active length before quoting an absolute β.
- Correspondingly, 1% dose accuracy pins the dominant β to ~0.03% (β = 60) to
  ~0.09% (β = 200), so the measurement is enormously sensitive to the dominant
  emitter and nearly blind to the rest.
- **Cross-check against the published drift.** Frosio et al. report field emission
  exceeding design goals by a factor 5–10 after ~1.5 years, attributed to surface
  contamination. At these sensitivities a factor 5 in current is only a **5–14%
  change in β** — or, equally consistent with their data, no change in β at all
  and a ~5× increase in the number of emission sites. Either reading makes the
  within-15-minute stationarity assumed in Step 6 very safe. It also means the
  "factor 5–10 above design" language and any β number this project produces live
  on wildly different scales, and the note must not let them be conflated.
- **Onset-gradient ratios (Step 0b) are the most error-tolerant result available.**
  A factor-*X* error in the `A·κ` prefactor displaces the inferred `β·E_pk` by only
  `ln X / (2 + u)` — ~7% for `X = 10` at u = 32, ~20% at u = 10 — and for ratios
  between nominally identical cavities the common part cancels.
- A *common* gradient scale error cancels in relative comparisons between irises
  within one cavity, so **iris localization is immune to it** and depends
  instead on field-flatness accuracy. Report the absolute-β and
  relative-localization error budgets separately.
- Shielding fidelity is **the** dominant κ risk, and with §8's revision it is now
  the dominant risk to the project as a whole. Far-monitor signal is set by
  attenuation through cryostat, vacuum vessel, and tunnel concrete. Floating
  `α_m` absorbs the origin-*independent* part of that error; it cannot absorb the
  origin-dependent part, which is exactly the part carrying the iris information.
  Hence the all-on burst as a geometry validation target — **under its own κ**
  (constraint #9), not as a superposition test.
  - Encouraging precedent: Frosio et al. Fig. 13 reproduced RDM LI02 within ~50%
    (10 µSv/h simulated vs 15 measured) in SELA mode with a detailed FLUKA
    geometry, and their in-phase discrepancy (10 mSv/h vs 50) they attribute to
    captured current rather than to geometry. So ~factor-2 absolute fidelity
    through this shielding is demonstrated. Whether the *origin-dependent* part is
    good to the few percent the iris question needs is untested by anyone.

---

## 10. Open questions

Grouped by how they get answered. Reviewing Frosio et al. (§12) closed some and
promoted others.

### Answerable from data already in hand — do these first

- **Is the DecaRad "10 sec mean" a boxcar over a fast detector, or an instrument
  with its own RC time constant?** Frosio et al. Fig. 15 is itself the diagnostic
  and so is our own trace: if the dose curve persists visibly *after* the gradient
  drops to zero at the end of a cavity window, there is a time constant. This was
  previously listed as blocking; it is a five-minute check.
- **Is the record complete, and is the trace-to-cavity mapping right?** See §2.
  Check for repeat ramps on C2/C3/C5/C8, and verify direction assignments against
  Table 1's CM01 rows.
- **Does the C1 ramp's boxcar bias behave as constraint #8 predicts?** The
  measured dose-vs-gradient curvature within single 10-s bins is a direct check.

### Must be asked of the authors or the archive

- **Surveyed positions of all ten DecaRad units, and the DecaRad
  energy-response curve** (constraint #4). *Not* in Frosio et al. — they publish
  fiber (7 mV/W), diamond (125 mW/mV at CYC13) and RDM (Thermo-Fisher FHT 190)
  calibrations but nothing for the DecaRad. Frosio and Santana Leitner are SLAC
  RP; ask directly rather than reverse-engineering it. **This is now the top
  blocking item** — without it `α_m` is spectrum-dependent and the origin
  discrimination is corrupted at source.
- **Were the neighbouring cryomodules powered during our CM01 one-at-a-time
  windows, and in phase or in SELA?** Promoted to first-order by constraint #9:
  it changes κ, and Frosio et al. Fig. 10 shows in-phase vs SELA is a factor ~2.5
  in mean captured energy. Previously not asked at all.
- **Native sample rate of the Gradient ACT trace, its calibration uncertainty,
  and its units (MV or MV/m).** This sets the β error floor exactly (§9).
- Per-cavity field flatness from qualification data, if it exists.

### Closed by Frosio et al.

- **RF frequency: 1.3 GHz confirmed**, TESLA-type, seventh harmonic of the gun's
  185.7 MHz. Feeds `Δt` via constraint #7.
- **Monitor complement: ten dosimeters**, 8 per-cavity + 2 at the cryomodule
  ends (§2).
- **Far-monitor signal is real and transport-dominated**, not a shielding-leak
  artifact — dose appears upstream of the powered cavity (§8).

### Still open, simulation-side (unchanged, resolve before Phase 2)

- Does `InitialNormalField` include the `sin φ` factor, i.e. is it the field at
  the emission site *at the emission phase*, or the peak field at that site?
  Diagnostic: for a fixed face, plot `InitialNormalField` against
  `InitialPhaseinRFcycle`; flat means the phase factor must be applied manually.
  This silently breaks everything if wrong.
- Can Track3P carry a field defined only on the powered-cavity subdomain of a
  72-cell mesh, or must the Omega3P mode be interpolated onto the full mesh?
  Memory/storage feasibility of the latter needs checking, and the all-on
  configuration (constraint #9) needs fields on all eight. See
  `references/track3p-commands.pdf` and `references/omega3p-commands.pdf`.
- How does Track3P report particles that leave the tracked domain (constraint
  #6)?

---

## 11. Recorded for any future measurement campaign

Out of scope now (no access to the setup), but these would change the answer
more than any modelling improvement:

- **Staircase, not ramp.** Dwell 45–60 s at each of ~8 gradient levels instead
  of ramping continuously. Same beam time, but every slice becomes a clean
  flat-top at a known gradient, the boxcar bias vanishes, and the sweep yields
  ~8 genuinely distinct points instead of a smeared ramp. Highest-value change.
- **Repeat the all-on burst at the end of the run.** One extra minute; gives a
  direct β-stationarity test over the record.
- **Power C2**, and give C3/C4 a real sweep — they are the strongest emitters and
  currently the least-constrained.
- **Follow the Fig. 15 design for the repeat pass.** Frosio et al. re-power the
  identified emitters after the first 1→8 sweep, which is the right instinct;
  combine it with the staircase above and push the repeats to the highest safe
  gradient, since the visibility window widens with `β·E_pk` (§8).
- **Add a monitor between L2 and L3.** Frosio et al. note high residual dose
  there with no RDM to see it (next one is after L3), so forward emission from
  the mid-linac cryomodules is currently unobserved. Out of scope for CM01, but
  it is the field's open gap.

---

## 12. External prior art

- **Frosio, Allan, Blaha, Brogognia, Rokny, Santana Leitner, Aderhold, Bai,
  Littleton, "Radiation Physics commissioning of LCLS-II superconducting Linac.
  Gun and cryomodules commissioning", NIM-A 1086 (2026) 171340.**
  doi:10.1016/j.nima.2026.171340. The direct predecessor to this work and the
  source of most of the revisions in this draft. What to take from it:
  - **Table 1** is the validation target: `(CM, cavity, onset gradient,
    direction)` for CM01, CM05, CM08, CM33, CM35. CM01 rows: cavity 2 > 15
    backward, 3 > 10 forward, 5 > 10 forward, 8 > 15 MV/m forward. Phase 0b
    reproduces this quantitatively.
  - **Fig. 15** is the CM08 equivalent of our record and defines the measurement
    design. Nine panels: blue in panel *N* is the gradient *of cavity N*, red is
    the dose at the dosimeter *in front of cavity N*, and the ninth panel holds
    the two end dosimeters plus RDM LI02. The figure is a matrix readout — to
    localize, fix the powered cavity and scan the red curves down the rows.
  - **Fig. 11** — captured current vs gradient from their FLUKA field-emission
    routine. Fitting its exponent is the basis of §8's β ≈ 150–450 estimate.
  - **Fig. 13** — FLUKA dose at RDM LI02 vs measurement; the closest published
    precedent for our κ approach, and the source of the ~factor-2 shielding
    fidelity claim in §9.
  - **Fig. 10** — captured-current energy spectra, in phase (mean ~50 MeV, max
    140) vs SELA (mean ~20, max 80). Sizes the phase-configuration sensitivity in
    constraint #9.
  - **§4.b.ii, CM08 cavity switch-off** — the published mechanism for the
    superposition failure that Step 0 must not treat as fatal.
  - Their methodology ladder, for context on where this project sits: sequential
    current balance (Fig. 7) → differential ablation (Figs. 9, 14) → one-at-a-time
    + monitor array (Fig. 15) → absolute normalization by forward FLUKA (Fig. 13)
    → FN scaling of the gradient axis (Fig. 11). Their inference at every rung is
    qualitative or single-parameter; this plan is attempting an iris-resolved
    inversion, which is two rungs further. Expect no methodological help, but take
    every constraint.

- **M. Santana Leitner et al., "Field emission radiation characterization of
  LCLS-II cavities", IPAC2016** (their ref. [21]). **Read before Phase 2.** This
  is the FLUKA field-emission user routine behind Figs. 10 and 11 — uniformly
  distributed emitters within a cavity, Fowler–Nordheim current scaling. It is the
  closest existing forward model to ours and should state the β value and emitter
  model that §8's Fig. 11 exponent fit is currently inferring indirectly.

- **A. S. Fisher et al., PRAB 23 (2020)** (their ref. [16]) — beam-loss detection
  for LCLS-II; diamond and Cherenkov fiber response, wavelength-dependent
  attenuation, PMT quantum efficiency. Relevant if the monitor set is ever
  extended beyond the DecaRad.

- **S. Posen et al., arXiv:2110.14580** (their ref. [22]) — LCLS-II-HE
  verification cryomodule gradient and quench behaviour. Sets the ~70 W
  quench threshold and the multipacting band (~18 MV) that bound the gradients any
  future campaign (§11) could request.
