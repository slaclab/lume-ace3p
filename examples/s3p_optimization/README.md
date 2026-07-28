# s3p_optimization

A scalar optimization of the S3P S-parameter solver on the declarative
module/mode schema:

```
workflow:  cubit -> s3p
mode:      scalar_optimize
```

An Xopt `NelderMeadGenerator` loop drives the two-step `cubit -> s3p` chain,
adjusting the waveguide-bend geometry to minimize reflection. The objective
`reflection` is an `output_parameters` name — defined as S3P quantity `S(0,0)`
evaluated `at` frequency `12.0e+09` — so the Xopt driver never parses
S-parameters itself; extraction is a workflow concern.

The two optimization knobs, `cornercut` (bounds `[14, 17]`) and `rcorner1`
(bounds `[0.5, 2.5]`), are Cubit journal variables, so they are declared under
`input_parameters.cubit`; each VOCS variable routes to the bucket where it is
declared. The run is capped at `num_step: 25` with no random seeding.

Unlike the grid-based [`../s3p_sweep`](../s3p_sweep), this searches for a single
optimum. For the multi-fidelity variant that trades solver cost against mesh
resolution see [`../s3p_mf_optimization`](../s3p_mf_optimization); for a
GP-driven exploration sweep see [`../s3p_bayesian_sweep`](../s3p_bayesian_sweep).

## Inputs

Both inputs are local to this directory:

- `bend-90degree.jou` — the Cubit journal that builds and meshes the bent
  waveguide; `cornercut` and `rcorner1` are the journal variables Xopt drives.
- `bend-90degree.s3p` — the S3P/ACE3P config (order-2 curved elements, two
  waveguide ports, and the `FrequencyScan` covering 12 GHz).

## Running

```bash
run-lume-ace3p s3p_optimization.yaml
```

On a cluster, submit one of the batch scripts:

```bash
sbatch run_lume-ace3p_s3p_optimization_perlmutter.batch   # NERSC Perlmutter
sbatch run_lume-ace3p_s3p_optimization_s3df.batch         # SLAC S3DF
```
