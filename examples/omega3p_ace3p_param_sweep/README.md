# omega3p_ace3p_param_sweep

A parameter sweep of the RF eigensolver pipeline that adds an ACE3P input
parameter as an extra sweep axis, on the declarative module/mode schema:

```
workflow:  cubit -> omega3p -> acdtool
mode:      parameter_sweep
```

Cubit meshes a pillbox cavity from the `pillbox-rtop.jou` journal, Omega3P
solves for its eigenmodes, and acdtool postprocesses the fields via
`pillbox-rtop.rfpost`. The sweep walks two Cubit journal variables —
`cav_radius` (90–120 mm, 4 points) and `ellipticity` (0.5–1.25, 4 points) — and
adds a third axis from the ACE3P config: the surface-material `Sigma` list
`[5.8e7, 1.04e7]` (2 values). That makes **4 x 4 x 2 = 32 runs**. The mode
iterates every swept axis generically, so the ACE3P axis rides alongside the
Cubit ones with no special-casing and appears as its own column in
`omega3p_sweep_output.txt`. The `output_parameters` pull `R/Q` and `Mode_freq`
from the acdtool `RoverQ` block plus `E_max` and its `loc_x/loc_y/loc_z` from
`maxFieldsOnSurface` on surface 6.

This differs from its siblings only in that added ACE3P axis:
[`../omega3p_sweep`](../omega3p_sweep) sweeps the same two Cubit axes alone (16
runs), and [`../omega3p_optimization`](../omega3p_optimization) replaces the grid
with an Xopt loop that maximizes `R/Q`. All three share the same
`pillbox-rtop.*` inputs.

## Inputs

The inputs are local to this folder (not in `../assets`):

- `pillbox-rtop.jou` — Cubit journal; builds and meshes the pillbox cavity and
  exposes `cav_radius` and `ellipticity` as the swept variables.
- `pillbox-rtop.omega3p` — Omega3P config; its `SurfaceMaterial` `Sigma` (default
  `5.8e7`) is overridden per-run by the ACE3P sweep axis.
- `pillbox-rtop.rfpost` — acdtool/rfpost config driving the RoverQ and
  maxFieldsOnSurface postprocessing.

## Running

```bash
run-lume-ace3p omega3p_ace3p_param_sweep.yaml
```

To submit as a batch job, use the script for your cluster:

```bash
sbatch run_lume-ace3p_omega3p_sweep_perlmutter.batch   # NERSC Perlmutter
sbatch run_lume-ace3p_omega3p_sweep_s3df.batch         # SLAC S3DF
```
