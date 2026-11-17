# tem3p_lfd

Lorentz-force detuning (LFD) of a 1.3 GHz SRF cell, swept over the
accelerating gradient:

```
workflow:  cubit(vacuum) -> omega3p ----------------+ em_solution
           cubit(shell) ------------------------> tem3p -> omega3p_deformed
                                             mesh        mesh
mode:      parameter_sweep
```

The steps:

1. Omega3P solves the fundamental mode of the vacuum region.
2. TEM3P loads that mode's radiation pressure onto the inner wall of the 3 mm
   niobium shell, scaled to `TargetGradient`, and solves for the elastic
   deformation. It also writes the vacuum mesh with the wall moved
   (`tem3p_results/DeformedVacuumMesh.ncdf`).
3. Omega3P solves the same input again on the deformed mesh.

The difference of the two frequencies is the detuning `df`. LFD goes as
the square of the gradient, so `k_l = df / Eacc²` should be the same in every
row. The geometry and inputs are the CW23 `SRFCell` case
(`/sdf/group/rfar/ace3p/cw23/examples/tem3p/SRFCell`).

On S3DF (Rocky 9, 2026-11-12), this sweep gives:

- `f0` = 1300956950 Hz;
- `df` = −1312.0 / −3644.4 / −7143.0 Hz at 15 / 25 / 35 MV/m;
- `k_l` = −5.831 Hz/(MV/m)² in all three rows.

CW23's archived run gives −7172.1 Hz at 35 MV/m. That run used the meshes
shipped with the case. Cubit regenerates them here, slightly differently
(34120 shell elements against 34122), which accounts for the 0.4%.

## What lume-ace3p does for you

- **Field and mesh directories.** In the TEM3P input, the LFDetuning
  `Directory` and `MeshDump.EMMeshInputDir` are set to the bound `omega3p`
  module's results directory.
- **One Omega3P input for both solves.** `omega3p_deformed` reads its mesh from
  the `tem3p` module (`from: tem3p`). Its `ModelInfo.File` is therefore set to
  `./tem3p_results/DeformedVacuumMesh.ncdf`, replacing the
  `SRFCellVacuum.ncdf` in `SRFCell.omega3p`, with a printed note. Each solve
  writes its merged input under its own name
  (`SRFCell_omega3p.omega3p`, `SRFCell_omega3p_deformed.omega3p`).
- **Checks before the first mesh is built.** The checks are listed below,
  and every problem fails the build:
  - `MeshFile` must name the mesh the shell journal makes;
  - an `Omega3PId` must be set (without it the vacuum mesh comes out
    undeformed, and nothing reports it);
  - the input must not use spellings TEM3P silently ignores.

## Sweeping a TEM3P leaf

```yaml
input_parameters :
  ace3p :
    tem3p :
      'ElasticProblem' :
        'Boundary' :
          'TargetGradient' : [15.0e+6, 25.0e+6, 35.0e+6]
```

- **The `tem3p:` key scopes the block** to the `tem3p` module. Unscoped, the
  `ElasticProblem` block would be merged into both Omega3P inputs too.
- **Same-named blocks match by position.** `Boundary` reaches the *first*
  `Boundary` of `ElasticProblem`, so `SRFCell.tem3p` lists the LFDetuning
  boundary first.

The derived `k_l` reads the swept gradient by its bare name, `TargetGradient`.
A derived expression reads inputs by name the same way it reads outputs.

## Files

| File | Role |
| --- | --- |
| `SRFCell3mmThick.sat` | CW23 solid model: the vacuum and the shell, two bodies |
| `meshSRFVacuum.jou` | Cubit journal: meshes the vacuum, exports `SRFCellVacuum.gen` |
| `meshSRFShell.jou` | Cubit journal: meshes the shell, exports `SRFCellShell.gen` |
| `SRFCell.omega3p` | Omega3P input shared by both solves (CW23, verbatim) |
| `SRFCell.tem3p` | TEM3P input: CW23's, with the ignored spellings corrected and the LFDetuning boundary first |
| `tem3p_lfd.yaml` | The sweep configuration |
| `run_lume-ace3p_tem3p_lfd_s3df.batch` | SLAC S3DF job script |
| `run_lume-ace3p_tem3p_lfd_perlmutter.batch` | NERSC Perlmutter job script |

The journals are CW23's, with Windows line endings converted. Both stage
`SRFCell3mmThick.sat` through `files:`. The journals delete bodies by ID, so
they need the `.sat` file: a STEP import numbers the bodies differently.

## Running

```bash
run-lume-ace3p tem3p_lfd.yaml   # or sbatch one of the .batch scripts
```

Each point runs both Cubit journals and all three solves, about 85 s per
point on one milano node. Peak memory is about 0.3 GB per TEM3P rank.

## Output

`tem3p_lfd_output.txt`, one row per gradient:

| Column | Meaning |
| --- | --- |
| `ace3p:tem3p/ElasticProblem.Boundary.TargetGradient` | the swept gradient, V/m |
| `f0` | mode-0 frequency of the undeformed cavity, Hz |
| `f_deformed` | mode-0 frequency on the deformed mesh, Hz |
| `u_mag_max` | largest displacement of the shell, m |
| `df` | `f_deformed - f0`, Hz |
| `k_l` | `df / (TargetGradient / 1e6)**2`, Hz/(MV/m)² |
| `field_artifact` | the row's `.npz`: both Omega3P solves' per-mode arrays, keyed by module name |

Each workdir holds `omega3p_results/`, `tem3p_results/` (with
`Displacement.mod` for the viewer) and `omega3p_results_deformed/`.

To iterate towards a self-consistent detuning, append further `tem3p` →
`omega3p` pairs, each reading its fields from the previous deformed solve.
lume-ace3p keeps every pair deforming the original vacuum mesh.
