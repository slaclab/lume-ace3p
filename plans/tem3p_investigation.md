# Adding TEM3P to LUME-ACE3P: Investigation Summary

*November 2026. Status: investigation only. No code has been written yet.*

## Purpose

LUME-ACE3P already wraps Cubit, Omega3P, S3P, T3P, Track3P, acdtool and
Geant4. Users can chain them into workflows and then sweep or optimize those
workflows. This note looks at how to add **TEM3P**, ACE3P's coupled thermal,
structural and RF solver, and the integration users ask for most. The
remaining modules, Gun3P and Pic3P (an add-on to T3P), would follow later.

The investigation read four kinds of source:

- **Documentation:** the current ACE3P docs
  (<https://slaclab.github.io/ace3p-docs/latest/>), and the CW23 TEM3P command
  reference and tutorial in `/sdf/group/rfar/ace3p/cw23/`.
- **Archived runs:** the four archived CW23 TEM3P examples (SRFCell,
  TeslaCavity, SrfCoupler, RfGun-Coupler) and the `mmode` run.
- **Source code:** the TEM3P source, wherever the documentation was ambiguous.
- **The binary:** the TEM3P binary installed on S3DF, to see which input
  keywords it accepts.

The installed binary is a newer build (2026-11-02) than the one used for the
2023 CW23 runs, so all output formats still need to be confirmed against it
(Phase 0 below).

## Summary

- A single `tem3p` module covers TEM3P. Its problem types are just different
  input files for the same binary, so it does not need several module types.
- **TEM3P has almost no internal feedback.** Its only internal iteration is the
  nonlinear thermal solve. Every coupled workflow in practice is a short
  **linear chain** of separate solver runs. For example:
  `omega3p (vacuum) → tem3p (body) → omega3p (deformed vacuum)`.
- LUME-ACE3P's existing workflow machinery already supports this kind of chain:
  several instances of the same solver, several meshes, and explicit wiring
  between modules. Three additions are needed (described below).
- **The common feedback case is already covered.** That case is pre-tuning the
  geometry so the *deformed* cavity reaches the target frequency, and the
  existing optimization mode handles it once the chain is in place. A true
  "iterate until self-consistent" loop would need new workflow machinery, and
  should be designed only if there is a concrete need for it.

## What TEM3P is

- **What it solves:** finite-element thermal and structural problems on the
  *body* mesh (the cavity walls). Loads can come from an RF solution computed
  separately on the *vacuum* mesh by Omega3P or S3P.
- **What decides the run:** the blocks present in the input file. The
  `ThermoElasticProblem` marker block is not what turns coupling on.

| Input blocks | What runs |
|---|---|
| `ThermostaticProblem` | Steady heat conduction, linear or nonlinear. Adding a `Transient:` block makes it a thermal transient. |
| `ElasticProblem` | Static linear elasticity. With an `EigenSolver` block it computes mechanical modes; with a `HarmonicAnalysis` block it computes forced response. |
| Both blocks | Thermal first, then elastic. The temperature field becomes the thermal-expansion load. |

**Coupling to RF.** Two boundary types pull in an Omega3P or S3P result through
a `Directory:` key:

- `RFHeating` puts surface losses ½|B|²·Rs on the walls.
- `LFDetuning` puts radiation pressure on the walls.

`HeatSource` adds dielectric volume losses. The fields are scaled to a target
gradient, voltage, input power or wall loss. Loads are mapped by
**geometric coincidence** of the body and vacuum walls, not by matching surface
IDs.

**Deformed-mesh output.** Setting `MeshDump: { WriteDeformedEMMesh: on }`
writes `tem3p_results/DeformedVacuumMesh.ncdf`. Only the nodes of the vacuum
surface named by `Omega3PId` are displaced. A second Omega3P run on that mesh
gives the frequency shift.

**What TEM3P does not compute:** the frequency shift itself, or the
Lorentz-force detuning coefficient. Both are found by comparing the two Omega3P
results.

### Iteration inside TEM3P

- **There is one internal loop:** the nonlinear thermal solve (Picard steps,
  then Newton). It handles temperature-dependent conductivity and surface
  resistance, which are given as piecewise tables in a `functions/` directory.
- **Thermal-to-elastic is a single pass.** The `ThermoElasticProblem` keys for
  tolerances and `MaxIterations` are parsed but never used, and every log
  reports "tem3p iter 0".
- **TEM3P never re-solves the RF fields.** Any RF ↔ thermal ↔ mechanical
  self-consistency has to be done outside TEM3P, by chaining runs.

### The CW23 reference cases

| Example | Physics | Chain | Result |
|---|---|---|---|
| SRFCell | Lorentz-force detuning | omega3p → tem3p → omega3p (deformed) | Δf = −7.17 kHz at 35 MV/m, which is ≈ −5.9 Hz/(MV/m)² |
| RfGun-Coupler | RF heating + water cooling + Lorentz force, normal-conducting gun | omega3p → tem3p → omega3p (deformed) | Δf = +47.4 kHz |
| SrfCoupler | Nonlinear thermal load on a coupler, from S3P fields | s3p → tem3p | Max T = 324 K, Newton converged in 9 iterations |
| TeslaCavity | Mechanical eigenmodes | tem3p only | 5 modes; the first is at 62.8 Hz |

In both deformed-mesh chains, the second Omega3P input is identical to the
first except for its mesh file.

## How TEM3P maps onto LUME-ACE3P

### What already works

Since release 0.7.0, one workflow can contain two Omega3P instances, each with
its own name, and two meshes, for example from two Cubit journals. Each module
can be told explicitly which upstream module it takes inputs from. The
deformed-mesh chain has no loop, so it fits the existing workflow model, which
runs each module once per evaluation.

### What has to be added

1. **A module with two inputs.** TEM3P needs both the body mesh and an EM
   solution. No existing module takes two inputs. The EM input has to be
   optional, because pure thermal and eigenmode runs do not use it.
2. **A solver that produces a mesh.** When `WriteDeformedEMMesh` is on, TEM3P
   outputs `DeformedVacuumMesh.ncdf` as a mesh that a downstream module can
   consume. Today only Cubit and the `mesh` source module produce meshes.
3. **Passing that mesh path to the next solver.** Solvers currently read
   whichever mesh their own input file names. The downstream Omega3P input has
   to be pointed at the mesh TEM3P wrote. This should happen only when the
   upstream mesh is TEM3P's, so that existing workflows behave exactly as
   before.

### What the module would fill in automatically

This follows the existing Track3P pattern, where the field directory is
filled in from the upstream Omega3P run:

- `Directory:` in each coupled `Boundary` and `HeatSource`, set to the
  upstream EM results directory.
- `MeshDump.EMMeshInputDir:`, set to the same directory.
- `MeshFile:`, set to the body mesh.

**Constraint:** TEM3P finds the RF fields relative to its own working
directory, and finds the vacuum mesh through an absolute path recorded by the
EM run. LUME-ACE3P runs all modules of an evaluation in one shared directory,
which satisfies this. Moving an evaluation directory between runs would break
it, for example when resuming in a new location.

### Outputs as result columns

**From TEM3P's `ResultFile`,** whose column names can be read from its header:

- maximum and minimum temperature;
- power through each sideset;
- displacement minimum and maximum per component and per block;
- mechanical eigenfrequencies.

**From `tem3p.log`:**

- the field scaling factor;
- the number of Newton iterations.

**Calculated from the two Omega3P runs:** the frequency shift Δf and the
Lorentz-force detuning coefficient Δf/E². LUME-ACE3P's existing
`derived_parameters` can already compute both.

**Parsing traps:**

- `ResultFile` is opened in append mode, so a rerun into the same directory
  stacks results. The module must clean the file before each run.
- The mechanical eigenfrequencies printed in `tem3p.log` are wrong by a factor
  of c. The values in `ResultFile` and in the output file names are correct.
- The stress and strain min/max values in the log are uninitialized
  placeholders, not results.

### Checks before a run starts

TEM3P does not warn about unknown or misspelled keys, so LUME-ACE3P should
reject the cases below before submitting a job.

- **Misspellings that appear in the CW23 inputs.** In each case TEM3P silently
  uses its default value instead:
  - `NeumanValue`, written for `NeumannValue`;
  - `AbsoluteTolerante` and `AbsoluteTolerance`. Neither spelling is read:
    TEM3P's linear solvers read only `Tolerance`;
  - `Abs tolerance` and `Rel tolerance`, written for `AbsTolerance` and
    `RelTolerance`. In SrfCoupler this silently changed the Newton tolerance.
- **Bad `MixedType`.** Anything other than exactly three upper-case entries
  makes TEM3P exit.
- **More than one EM `Directory`.** TEM3P supports only one, and the last one
  loaded wins without warning.
- **Missing `Omega3PId`.** If a deformed mesh is used downstream but no
  `LFDetuning` or `Neumann` boundary sets `Omega3PId`, the deformed mesh
  would come out undeformed. This is inferred from the source code and still
  needs to be confirmed by a run.

### Resources

Every MPI rank loads the entire vacuum mesh and its fields. For SRFCell this
is about 390 MB per rank. That affects job sizing and how many sweep points
can share one node.

## Proposed phases

| Phase | Content |
|---|---|
| 0 | Re-run SRFCell, RfGun-Coupler, TeslaCavity and SrfCoupler on S3DF with the current build. Save their outputs as test fixtures, and confirm the output formats, the `Omega3PId` behaviour and the misspelling behaviour. |
| 1 | Input-file wrapper and output parsers, tested against the Phase 0 fixtures. |
| 2 | The `tem3p` module: the two inputs, the automatic fill-in, the pre-run checks and the result columns. |
| 3 | The `tem3p → omega3p` deformed-mesh hand-off, plus a shipped Lorentz-force detuning example: Cubit ×2 → omega3p → tem3p → omega3p, with Δf and the detuning coefficient as columns. This is a natural target for sweeps and optimization. |
| 4 | Thermal and nonlinear cases, and mechanical eigenmodes. Thermal and nonlinear are the RfGun and SrfCoupler cases; SrfCoupler has S3P upstream and needs the `functions/` tables staged. Eigenmodes are the TeslaCavity case and give a mode-indexed table. |
| 5 | Documentation: a TEM3P reference page and YAML reference entries. |

**Deferred:** thermal transients, harmonic analysis, and the per-mode deformed
meshes that the eigenmode solver can write.

## Feedback loops

| Need | How it would be handled |
|---|---|
| Pre-tune the geometry so the *deformed* cavity reaches the target frequency | The existing optimization mode, with the deformed frequency as the objective. Available after Phase 3. |
| A few rounds of RF ↔ thermal/mechanical coupling: re-solve the fields on the deformed geometry, then recompute the heating | Write the stages out explicitly in one workflow (omega3p → tem3p → omega3p → tem3p …). This works without new machinery. |
| Iterate until a convergence criterion is met | Needs a new "repeat until converged" construct. That would change the workflow model, which deliberately runs each module once. Design it only when a concrete use case needs it. |

**Caveat for any iterated chain:** after the first pass, the deformed vacuum
wall no longer coincides exactly with the undeformed body wall that TEM3P maps
loads onto. Whether micron-scale mismatches affect the load mapping needs
checking.

## Open questions

1. Which TEM3P use case matters most? Lorentz-force detuning in SRF cavities,
   thermal detuning of normal-conducting guns, coupler heat loads, or
   mechanical modes? The answer sets the order of Phases 3 and 4.
2. Do users need true iterate-to-convergence workflows, or does pre-tuning
   cover the demand?

## Gun3P and Pic3P (outlook)

- **Pic3P:** structurally close to T3P, which already has a module. It would
  be a small addition, and would give the existing acdtool `pic3pstats` and
  `pic3pconvert` commands a module to belong to.
- **Gun3P:** structurally different. It has electrostatic, magnetostatic and
  DC-gun problems and its own particle tracker, so it would be a larger
  addition. Neither is part of this investigation.

## Documentation discrepancies found

These came up while reading the TEM3P documentation. They are listed here for
the ACE3P documentation maintainers.

- **`SymmetryFactor`:** the documentation gives two conflicting conventions.
  The code defaults to 0, with a warning that every example triggers.
- **`EMMeshInputDir`:** the command reference calls it the output directory.
  It is actually the *input* EM results directory, and is required when
  `WriteDeformedEMMesh` is on.
- **`MeshDeformScale`:** documented, but the current source no longer reads
  it. Scaling deformations for visualization is done with
  `acdtool mesh deform` instead.
- **`DutyFactor`:** the keyword table lists a default of 0. The code uses
  1.0, and applies it only with `Method: Gradient`.
- **`Omega3PId`:** the tutorial describes it as the surface that supplies the
  pressure. The code uses it only to choose which vacuum surface the deformed
  mesh writer moves.
- **`MixedType`:** the tutorial slides use mixed case. The code accepts only
  upper case.
- **`tem3pOutputConverter`:** undocumented. It is a legacy converter; current
  TEM3P writes `.mod` files directly.
