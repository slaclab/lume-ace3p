# s3p_sweep_no_s3p_file

The same S3P parameter sweep as [`../s3p_sweep`](../s3p_sweep), but with the
ACE3P settings supplied **inline** in the YAML instead of a separate `.s3p`
file:

```
workflow:  cubit -> s3p
mode:      parameter_sweep
```

The sweep itself is identical — a tensor product over `cornercut` (5 values,
12-16) and `rcorner2` (3 values, 4-16), long-format output (one row per
`(cornercut, rcorner2, Frequency)`) to `s3p_sweep_output.txt`. The difference
is purely how S3P is configured: the `s3p` module lists **no `input:` file**,
and its ACE3P settings come entirely from the `input_parameters.ace3p` block.
That block is duplicate-key aware, so the two `Port` entries coexist just as
they would in a native `.s3p` file.

This shows the inline alternative to the standalone-`.s3p` approach used by its
siblings; compare with [`../s3p_sweep`](../s3p_sweep) for the file-based form.

## Inputs

Only one input file is present, and that is deliberate:

- `bend-90degree.jou` — the Cubit journal that builds and meshes the bent
  waveguide; `cornercut` and `rcorner2` are the journal variables the sweep
  drives.
- **No `.s3p` file** — by design. The `FrequencyScan`, finite-element order,
  boundary conditions, and both ports live in the `input_parameters.ace3p`
  block of `s3p_sweep_no_s3p_file.yaml`.

## Running

```bash
run-lume-ace3p s3p_sweep_no_s3p_file.yaml
```

No batch scripts ship with this example; run it directly or adapt one of the
scripts in [`../s3p_sweep`](../s3p_sweep).
