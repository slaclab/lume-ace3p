# Omega3P dispersion sweep

A `cubit → omega3p` workflow that sweeps the periodic-boundary **phase advance**
of a single accelerator cell and reports the eigenmode at each phase, tracing
the structure's dispersion curve.

```
workflow:  cubit → omega3p
mode:      parameter_sweep
```

Model and input files are adapted from the ACE3P tutorial `omega3p/dlwg-pbc`
example: one period of a detuned-structure (DS) disk-loaded waveguide, meshed as
a quarter model with a periodic master/slave surface pair.

## What makes this example different

**No acdtool step.** Every other Omega3P example here runs
`cubit → omega3p → acdtool`. Omega3P's own eigenmode results in
`omega3p_results/omega3p.out` are read directly, so a workflow that wants only
frequency and Q needs no postprocessing.

**The swept axis is not a Cubit variable.** `Theta`, the phase advance across
the periodic boundary in degrees, lives in the `.omega3p` input file, so it is
declared under `input_parameters: ace3p:` addressed by its path in that file:

```yaml
input_parameters :
  ace3p :
    'ModelInfo' :
      'BoundaryCondition' :
        'Theta' : [-180, -150, -120, -90, -60, -30]
```

The mesh does not change across the sweep, but the module chain is re-run whole
for each evaluation, so Cubit still runs per grid point. Meshing takes seconds
against an eigensolve.

The ACE3P sub-block takes an explicit **list**. The `{min, max, num}` shorthand is
a `cubit:` / `geant4:` / `particles:` convenience and is not applied to ACE3P
leaves.

## Files

| File | Role |
| --- | --- |
| `dlwg-pbc.jou` | Cubit journal — builds the DS cell, cuts the quarter model, meshes it, exports `pbc-4.gen` |
| `dlwg-pbc.omega3p` | Omega3P config: periodic BCs with `Theta`, one eigenvalue about a 1.1 GHz shift |
| `omega3p_dispersion_sweep.yaml` | The sweep configuration |
| `run_lume-ace3p_omega3p_dispersion_sweep_perlmutter.batch` | NERSC Perlmutter job script |
| `run_lume-ace3p_omega3p_dispersion_sweep_s3df.batch` | SLAC S3DF job script |

The mesh is not checked in: Cubit writes `pbc-4.gen` and `acdtool meshconvert`
converts it to the `pbc-4.ncdf` the `.omega3p` file references.

The journal **joins the tutorial's two** (`step1-Make-DS-cell.jou` builds the
solid and exports `.sat` files; `step2a-pbc-mesh_1fourth.jou` imports one and
meshes it), because a `cubit` module plays exactly one journal. The ACIS
round-trip is dropped and only the quarter model is built. The `compress ids`
before the meshing half renumbers the surviving entities contiguously, as an
export/import pair would, so the surface / curve / vertex IDs the meshing half
names still resolve.

**The periodic pair needs identical meshes** on its master and slave surfaces, so
the journal copies surface 2's mesh onto surface 4 rather than meshing both. If
you edit the geometry, keep that `copy Mesh Surface` line and its source/target
curve and vertex arguments consistent, or Omega3P's `Periodic_M`/`Periodic_S`
boundary pair will not match up.

## Running

```bash
run-lume-ace3p omega3p_dispersion_sweep.yaml    # or sbatch one of the .batch scripts
```

Without an ACE3P environment the workflow auto-enables dry-run: each grid point's
workdir gets a `DRY_RUN.txt` describing the step that would have run, and the
result table is produced with the solver columns as `NaN`.

## Output

`dispersion_sweep_output.txt`, tab-delimited:

| Column | Meaning |
| --- | --- |
| `ace3p:ModelInfo.BoundaryCondition.Theta` | the swept phase advance, degrees (the column is labeled by the leaf's path in the ACE3P input) |
| `ModeID` | the eigenmode index (the field index — see below) |
| `f` | mode frequency, Hz |
| `Q` | intrinsic quality factor |

Plot `f` against `Theta` for the dispersion curve; the tutorial ships the two
hand-run points (`-m0` accelerating, `-m1` dipole) that this sweep generalizes.
The tutorial's own `Theta = -150` point lands at **11.360 GHz** with `Q = 6941`.
This is an X-band cell: `FrequencyShift: 1.10e9` in the input file is the
Arnoldi shift the tutorial starts from, not the expected answer.

Other mode quantities available from the same output as `output_parameters`:
`ExternalQ`, `Frequency_imag` (this run is lossy, so its eigenvalues are complex
and `Frequency` is the real part), `TotalEnergy` and `PowerLoss`.

**The table shape depends on whether the solve ran.** Neither `f` nor `Q`
carries an `at:`, which asks for the whole mode axis, so a real run emits one
row per `(Theta, ModeID)` with `ModeID` as an index column. A **dry run has no
modes yet** (the count is a result of the eigensolve, not an input), so its
table stays wide: one row per `Theta`, no `ModeID` column.

Omega3P's own output lands under each workdir in `omega3p_results/`:
`omega3p.out` (the parsed file: KVC syntax, one `Mode` section per eigenmode,
preceded by an echo of the input the solver actually resolved), `omega3p.warn`,
and one `.mod` file per mode for ParaView.

## Adapting this to your own model

* **More passbands.** Raise `EigenSolver.NumEigenvalues` in `dlwg-pbc.omega3p`.
  Because the output specs ask for the whole mode axis, the table grows rows,
  not columns: one per `(Theta, ModeID)`.
* **One mode only.** Add `at: {mode: 0}` to an output spec to reduce it to that
  mode's scalar; the table then stays wide, one row per `Theta`. This is also the
  form an Xopt objective needs (`scalar_optimize` on a phase-matched frequency).
* **Dipole rather than accelerating mode.** The tutorial's `-m1` variant differs
  only in its boundary conditions (`Electric: 2` in place of `Magnetic: 2`);
  either edit `dlwg-pbc.omega3p` or override
  `ModelInfo: {BoundaryCondition: {Electric: 2}}` from the `ace3p:` block.
* **The eighth model.** The tutorial also ships an eighth-model mesh journal for
  the accelerating mode. It is a different mesh, so it belongs in a separate
  journal and example.
