# omega3p_frequency_target

Tune a pillbox cavity's radius until its fundamental mode sits at 1.3 GHz:

```
workflow:  cubit -> omega3p
mode:      scalar_optimize   (Nelder-Mead over cav_radius)
```

The objective is a distance from a target, which no solver writes. It is
computed from Omega3P's mode frequency by `derived_parameters`:

```yaml
output_parameters :
  'mode_freq' : {module: omega3p, quantity: Frequency, at: {mode: 0}}

derived_parameters :
  'f_target' : 1.3e9                          # a constant, Hz
  'f_error'  : 'abs(mode_freq - f_target)'    # Hz from the target
  'f_ppm'    : '1e6 * f_error / f_target'

vocs_parameters :
  'objectives' :
    'f_error' : 'MINIMIZE'
```

A derived name works anywhere an output name does: here as the VOCS objective
(`f_error`) and an observable (`f_ppm`), and as a column of `sim_output.txt`.
See [`derived_parameters`](../../docs/yaml_reference.md#derived_parameters).
Changing `f_target` changes the campaign's config hash, so a resumed run with a
new target starts over rather than continuing the old one.

This is the optimization counterpart of
[`../omega3p_dispersion_sweep`](../omega3p_dispersion_sweep): `cubit -> omega3p`
with no acdtool step. [`../omega3p_optimization`](../omega3p_optimization)
optimizes the same cavity for R/Q, which needs acdtool.

## Files

| File | Role |
| --- | --- |
| `pillbox-rtop.jou` | Cubit journal (as in `omega3p_sweep`); `cav_radius` is the knob |
| `pillbox-rtop.omega3p` | Omega3P input: order 2, 2 eigenvalues about 1 GHz |
| `omega3p_frequency_target.yaml` | The optimization configuration |
| `run_lume-ace3p_omega3p_frequency_target_s3df.batch` | SLAC S3DF job script |
| `run_lume-ace3p_omega3p_frequency_target_perlmutter.batch` | NERSC Perlmutter job script |

The bounds `[90, 105]` mm bracket the target: at `ellipticity` 0.5 the
fundamental is about 1.41 GHz at 90 mm and 1.25 GHz at 100 mm.

## Running

```bash
run-lume-ace3p omega3p_frequency_target.yaml   # or sbatch one of the .batch scripts
```

Each evaluation runs in its own workdir (`_0`, `_1`, …). `sim_output.txt` logs
`cav_radius` and the VOCS outputs `f_error`, `mode_freq` and `f_ppm` per
evaluation.
