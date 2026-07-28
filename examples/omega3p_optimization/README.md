# omega3p_optimization

A scalar optimization of the RF eigensolver pipeline on the declarative
module/mode schema:

```
workflow:  cubit -> omega3p -> acdtool
mode:      scalar_optimize
```

Cubit meshes a pillbox cavity from the `pillbox-rtop.jou` journal, Omega3P
solves for its eigenmodes, and acdtool postprocesses the fields via
`pillbox-rtop.rfpost`. Instead of walking a grid, this example drives an Xopt
loop (`NelderMeadGenerator`, `num_random: 0`, `num_step: 25`) that **maximizes
`R/Q`** over two Cubit journal variables, `cav_radius` (bounds `[95, 105]`) and
`ellipticity` (bounds `[0.5, 1.2]`). The VOCS objective name `R/Q` is an
`output_parameters` name (`['RoverQ', '0', 'RoQ']` from the acdtool results), so
the Xopt driver never parses acdtool output itself — extraction stays a workflow
concern. `mode_freq` is declared as a tracked observable, not optimized.

Declaring `input_parameters` gives each VOCS variable an explicit home: a
variable routes to the module bucket where it is declared (both live under
`cubit:` here), which checks the routing and makes typos fail loudly rather than
silently becoming junk Cubit variables.

This is the optimization counterpart of the sweep siblings:
[`../omega3p_sweep`](../omega3p_sweep) walks a 16-point Cubit grid and
[`../omega3p_ace3p_param_sweep`](../omega3p_ace3p_param_sweep) adds an ACE3P
`Sigma` axis (32 runs). All three share the same `pillbox-rtop.*` inputs.

## Inputs

The inputs are local to this folder (not in `../assets`):

- `pillbox-rtop.jou` — Cubit journal; builds and meshes the pillbox cavity and
  exposes `cav_radius` and `ellipticity` as the optimization knobs.
- `pillbox-rtop.omega3p` — Omega3P config; 2nd-order elements, 2 eigenvalues
  about a 1 GHz frequency shift.
- `pillbox-rtop.rfpost` — acdtool/rfpost config driving the RoverQ
  postprocessing that supplies the `R/Q` objective.

## Running

```bash
run-lume-ace3p omega3p_optimization.yaml
```

To submit as a batch job, use the script for your cluster:

```bash
sbatch run_lume-ace3p_omega3p_optimization_perlmutter.batch   # NERSC Perlmutter
sbatch run_lume-ace3p_omega3p_optimization_s3df.batch         # SLAC S3DF
```
