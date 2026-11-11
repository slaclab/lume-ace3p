# omega3p_sweep_multi_journal

The [`../omega3p_sweep`](../omega3p_sweep) grid, with the Cubit work split
across three journals and one imported geometry file:

```
workflow:  cubit -> omega3p -> acdtool
mode:      parameter_sweep
```

The sweep is the same as in `omega3p_sweep`: `cav_radius` 90–120 mm and
`ellipticity` 0.5–1.25, 4 × 4 = 16 runs, with the same outputs. Only the
`cubit` entry is different:

```yaml
- module : cubit
  journal : ['cavity_vars.jou', 'cavity_geometry.jou', 'cavity_mesh.jou']
  files : 'beampipe.sat'
```

The three journals run in order in one Cubit session, so the variables that
`cavity_vars.jou` declares are visible in the other two. lume-ace3p writes each
swept value into the first journal that declares it. `beampipe.sat` is copied
into the workdir before Cubit starts, and `cavity_geometry.jou` imports it from
there. The meshes are identical to the ones `omega3p_sweep` builds, so the two
tables match. See
[Several files in one session](../../docs/workflow_inputs.md#several-files-in-one-session).

`cavity_vars.jou` ends in `quit`, so it also works when run by itself.
lume-ace3p comments that `quit` out of the copy it runs, because a `quit`
before the last file would end the session without a mesh.

## Why a `.sat` file and not STEP

`cavity_geometry.jou` blends curves by ID (`modify curve 14 16 …`), and those
IDs depend on the topology of the imported pipe. An ACIS `.sat` import keeps
the topology Cubit created. A STEP import of the same cylinder does not: the
blend IDs then name the wrong curves, or no curves at all, and Cubit still
exits normally with a mesh. To import STEP geometry, select curves and surfaces
by position, not by ID.

## Files

| File | Role |
| --- | --- |
| `cavity_vars.jou` | APREPRO declarations: `cav_length`, `cav_radius`, `ellipticity`, `iris_rounding` |
| `cavity_geometry.jou` | Builds the cell, imports the pipe, cuts the quarter, blends the edges |
| `cavity_mesh.jou` | Meshes, sets the sidesets, exports `pillbox-rtop4.gen` |
| `beampipe.sat` | The beam pipe (300 mm long, 35 mm radius), staged by `files:` |
| `make_beampipe.jou` | Writes `beampipe.sat`; not part of the workflow |
| `pillbox-rtop.omega3p`, `pillbox-rtop.rfpost` | As in `omega3p_sweep` |

## Running

```bash
run-lume-ace3p omega3p_sweep_multi_journal.yaml
```

On SLAC S3DF:

```bash
sbatch run_lume-ace3p_omega3p_sweep_multi_journal_s3df.batch
```
