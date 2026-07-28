# s3p_mf_optimization

A multi-fidelity scalar optimization of the S3P S-parameter solver on the
declarative module/mode schema:

```
workflow:  cubit -> s3p
mode:      scalar_optimize
```

Like [`../s3p_optimization`](../s3p_optimization), an Xopt loop drives the
`cubit -> s3p` chain to minimize reflection — here the objective `reflection` is
S3P quantity `S(1,1)` evaluated `at` frequency `12.0e+09`. The difference is the
generator: `MultiFidelityGenerator` trades solver cost against fidelity.
`mesh_fidelity` is the Cubit knob that sets mesh resolution (the journal sizes
elements as `4.0/(mesh_fidelity + 1.0)`), exposed to Xopt as the fidelity axis
via `xopt_parameters.fidelity_variable`.

The optimization variables `cornercut` (bounds `[12.5, 13.5]`) and `wgwidth`
(bounds `[21, 22]`) are Cubit journal variables declared under
`input_parameters.cubit`; `mesh_fidelity` is driven separately as the fidelity
axis. The run uses an `exponential` cost function, `num_random: 3`, a
`00:30:00` time budget, and a `1.0e-03` tolerance.

For the single-fidelity optimizer see
[`../s3p_optimization`](../s3p_optimization); for the GP exploration sweep that
reuses the same `_mf` inputs see
[`../s3p_bayesian_sweep`](../s3p_bayesian_sweep).

## Inputs

Both inputs are local to this directory:

- `bend-90degree_mf.jou` — the Cubit journal that builds and meshes the bent
  waveguide; adds `mesh_fidelity` and a fidelity-dependent element size on top
  of `cornercut` and `wgwidth`.
- `bend-90degree_mf.s3p` — the S3P/ACE3P config (order-2 curved elements, two
  waveguide ports, and a `FrequencyScan` over 11-13 GHz).

## Running

```bash
run-lume-ace3p s3p_mf_optimization.yaml
```

On a cluster, submit the batch script:

```bash
sbatch run_lume-ace3p_s3p_mf_optimization_perlmutter.batch   # NERSC Perlmutter
```

Only a Perlmutter script ships with this example; adapt it for other clusters.
