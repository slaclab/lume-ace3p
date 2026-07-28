# s3p_sweep

A parameter sweep of the S3P S-parameter solver on the declarative
module/mode schema:

```
workflow:  cubit -> s3p
mode:      parameter_sweep
```

Each sweep point re-runs the two-step chain: Cubit meshes the 90-degree
waveguide bend from the journal, then S3P computes its scattering parameters.
`parameter_sweep` takes the tensor product of the swept Cubit inputs —
`cornercut` (5 values, 12-16) and `rcorner2` (3 values, 4-16), so 15 geometries.

Because S3P exposes a `Frequency` field index, the result table is emitted
**long-format**: one row per `(cornercut, rcorner2, Frequency)` across the S3P
`FrequencyScan` (9.424-12.424 GHz), written to `s3p_sweep_output.txt`.

Unlike [`../s3p_sweep_no_s3p_file`](../s3p_sweep_no_s3p_file) — which runs the
same sweep with the ACE3P settings inlined and no `.s3p` file — this example
keeps the S3P configuration in a standalone `.s3p` input. For optimization
rather than a grid, see [`../s3p_optimization`](../s3p_optimization) and
[`../s3p_mf_optimization`](../s3p_mf_optimization); for a GP-driven sweep see
[`../s3p_bayesian_sweep`](../s3p_bayesian_sweep).

## Inputs

Both inputs are local to this directory:

- `bend-90degree.jou` — the Cubit journal that builds and meshes the bent
  waveguide; `cornercut` and `rcorner2` are the journal variables the sweep
  drives.
- `bend-90degree.s3p` — the S3P/ACE3P config (order-2 curved elements, two
  waveguide ports, and the `FrequencyScan`).

## Running

```bash
run-lume-ace3p s3p_sweep.yaml
```

On a cluster, submit one of the batch scripts:

```bash
sbatch run_lume-ace3p_s3p_sweep_perlmutter.batch   # NERSC Perlmutter
sbatch run_lume-ace3p_s3p_sweep_s3df.batch         # SLAC S3DF
```
