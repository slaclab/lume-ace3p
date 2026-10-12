# omega3p_p_refinement

Two Omega3P eigensolves of one mesh, at element orders 1 and 2, tabulated side
by side:

```
workflow:  cubit -> omega3p (coarse) + omega3p (fine)
mode:      parameter_sweep
```

Cubit meshes the pillbox cavity of [`../omega3p_sweep`](../omega3p_sweep) at
three radii (`cav_radius` 95, 100, 105 mm). At each radius both solvers run on
that mesh with the same `pillbox-rtop.omega3p`, and the table puts each one's
fundamental frequency and Q next to the other's.

## The scoped block is the example

Both solvers read one input file. What makes them different solves is this:

```yaml
input_parameters :
  ace3p :
    coarse :
      'FiniteElement' : { 'Order' : 1 }
    fine :
      'FiniteElement' : { 'Order' : 2 }
```

A key under `ace3p:` that names a workflow module reaches that module only.
Without the scope, `FiniteElement.Order` would reach both solvers and they would
run the same solve twice. See
[Module-scoped inputs](../../docs/yaml_reference.md#module-scoped-inputs).

Three more things two instances of one type need:

- **`name:`** on each entry. The name is the scope key, the step's log file, its
  manifest entry, and what an output's `module:` refers to.
- **`results_dir:`** on at least one, so the two solves do not write into one
  directory. Each solver also writes its merged input as
  `pillbox-rtop_<name>.omega3p`, so neither overwrites the other's.
- **`module: <name>`** in `output_parameters`. `module: omega3p` would match both
  instances and is an error.

See [Several instances of one module](../../docs/yaml_reference.md#several-instances-of-one-module).

## Files

| File | Role |
| --- | --- |
| `pillbox-rtop.jou` | Cubit journal (as in `omega3p_sweep`), exports `pillbox-rtop4.gen` |
| `pillbox-rtop.omega3p` | Omega3P input shared by both solvers: 2 eigenvalues about 1 GHz |
| `omega3p_p_refinement.yaml` | The sweep configuration |
| `run_lume-ace3p_omega3p_p_refinement_s3df.batch` | SLAC S3DF job script |
| `run_lume-ace3p_omega3p_p_refinement_perlmutter.batch` | NERSC Perlmutter job script |

The input file's own `Order: 2` is overridden for both solvers, so the file's
value does not matter here.

## Running

```bash
run-lume-ace3p omega3p_p_refinement.yaml   # or sbatch one of the .batch scripts
```

## Output

`p_refinement_output.txt`, one row per radius:

| Column | Meaning |
| --- | --- |
| `cav_radius` | the swept radius, mm |
| `f_coarse`, `f_fine` | mode-0 frequency at order 1 / order 2, Hz |
| `Q_coarse`, `Q_fine` | mode-0 intrinsic Q at order 1 / order 2 |
| `f_shift_ppm` | `1e6 * (f_coarse - f_fine) / f_fine`, from `derived_parameters` |

Each workdir holds `coarse_results/` and `fine_results/`, the two logs
`coarse.log` and `fine.log`, and the two merged inputs. The per-row
`field_artifact` `.npz` holds both solves' full per-mode arrays, keyed by
module name (`load_field(h)['fine']['Frequency']`).
