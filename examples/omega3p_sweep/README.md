# omega3p_sweep

A grid parameter sweep of the RF eigensolver pipeline on the declarative
module/mode schema:

```
workflow:  cubit -> omega3p -> acdtool
mode:      parameter_sweep
```

Cubit meshes a pillbox cavity from the `pillbox-rtop.jou` journal, Omega3P
solves for its eigenmodes, and acdtool postprocesses the fields via
`pillbox-rtop.rfpost`. The mode walks a plain Cartesian grid over two Cubit
journal variables — `cav_radius` (90–120 mm, 4 points) and `ellipticity`
(0.5–1.25, 4 points) — for **4 x 4 = 16 runs**, emitting one scalar row per grid
point into `omega3p_sweep_output.txt`. Each `output_parameters` entry is a
`[section, ...]` address into the acdtool results: `R/Q` and `Mode_freq` from the
`RoverQ` block, plus `E_max` and its `loc_x/loc_y/loc_z` location from
`maxFieldsOnSurface` on surface 6.

This is the baseline of the trio. [`../omega3p_ace3p_param_sweep`](../omega3p_ace3p_param_sweep)
adds an ACE3P `Sigma` axis on top of the same two Cubit axes (32 runs), and
[`../omega3p_optimization`](../omega3p_optimization) replaces the grid with an
Xopt loop that maximizes `R/Q`. All three share the same `pillbox-rtop.*` inputs.

## Inputs

The inputs are local to this folder (not in `../assets`):

- `pillbox-rtop.jou` — Cubit journal; builds and meshes the pillbox cavity and
  exports `pillbox-rtop4.gen`. Exposes `cav_radius` and `ellipticity` as the
  swept variables.
- `pillbox-rtop.omega3p` — Omega3P config; 2nd-order elements, 2 eigenvalues
  about a 1 GHz frequency shift.
- `pillbox-rtop.rfpost` — acdtool/rfpost config driving the RoverQ and
  maxFieldsOnSurface postprocessing.

## Running

```bash
run-lume-ace3p omega3p_sweep.yaml
```

This example ships no batch scripts — run it locally. For cluster submission see
the sibling examples, which carry Perlmutter and S3DF `sbatch` scripts.
