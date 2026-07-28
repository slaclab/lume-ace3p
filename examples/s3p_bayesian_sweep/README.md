# s3p_bayesian_sweep

A Gaussian-process exploration sweep of the S3P S-parameter solver on the
declarative module/mode schema:

```
workflow:  cubit -> s3p
mode:      gp_parameter_sweep
```

This mode couples an Xopt `BayesianExploration` loop with a GP posterior-mean
sweep. The driver actively explores the input space by running the
`cubit -> s3p` chain, fits a GP to the results, then evaluates the GP's
posterior mean over the dense `sweep_parameters` grid — here a 10x10 grid over
`cornercut` (12.5-13.5) and `wgwidth` (21-22). The explored objective
`S(1,1)_12.0e+09` is an `output_parameters` name (S3P quantity `S(1,1)` `at`
frequency `12.0e+09`) with goal `explore`, so the mode pulls it from the
workflow generically without any S-parameter parsing of its own.

Two output tables are written: `sim_output.txt` (the sampled trajectory, via
`output_file`) and `sweep_output.txt` (the GP posterior-mean sweep, via
`sweep_output_file`). The exploration itself runs `num_step: 3`.

Unlike the deterministic grid of [`../s3p_sweep`](../s3p_sweep) or the
optimizers in [`../s3p_optimization`](../s3p_optimization) and
[`../s3p_mf_optimization`](../s3p_mf_optimization), this builds a surrogate and
sweeps its predictions.

## Inputs

Both inputs are local to this directory (shared with
[`../s3p_mf_optimization`](../s3p_mf_optimization)):

- `bend-90degree_mf.jou` — the Cubit journal that builds and meshes the bent
  waveguide; `cornercut` and `wgwidth` are the journal variables explored.
- `bend-90degree_mf.s3p` — the S3P/ACE3P config (order-2 curved elements, two
  waveguide ports, and a `FrequencyScan` over 11-13 GHz).

## Running

```bash
run-lume-ace3p s3p_bayesian_sweep.yaml
```

No batch scripts ship with this example; run it directly or adapt one from a
sibling such as [`../s3p_mf_optimization`](../s3p_mf_optimization).
