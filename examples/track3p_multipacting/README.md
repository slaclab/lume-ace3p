# track3p_multipacting

A Track3P multipacting scan on the declarative module/mode schema:

```
workflow:  mesh -> omega3p -> track3p
mode:      single
```

Omega3P solves the eigenmodes of the CW23 pillbox cavity, then Track3P tracks
secondary electrons emitted from the cavity wall at each field level of its
`FieldScales` scan (23, 24 and 25 MV/m). This is the CW23 `Pillbox.track3p`
tutorial case run in-pipeline: the `track3p` module points the input's
`Domain.FieldDir` at the directory the `omega3p` step wrote, and reads the
tables Track3P's own `Postprocess` block produces.

The five declared outputs are per field level, so the result table is
**long-format**: one row per `FieldLevel`, written to
`track3p_multipacting_output.txt`.

| Column | From | Meaning |
|---|---|---|
| `EC_max` | `OUTPUT/enhancementCounter` | largest `maxEnhancement` among the level's resonant particles |
| `EC_mean` | same | mean `averageEnhancement` |
| `impacts` | same | sum of `totalImpactNum` |
| `resonant` | `OUTPUT/resonantparticles` | distinct resonant particle IDs |
| `mp_onset` | derived | lowest level whose `EC_max` reaches the threshold (1.0); NaN when none does. One number per run, repeated down the rows |

An enhancement counter above 1 means the secondary yield sustains the
trajectory, i.e. multipacting. On this case every counter stays below 1, so
`mp_onset` is NaN. Other quantities the module offers: `impact_count` and
`max_impact_energy` (read on demand from the per-level `ImpactsInfo_*` dumps),
`lost_count`, `emitting_faces`, and for field-emission runs `total_emitted`,
`survived` and per-cup `captured_electrons`.

## Inputs

- `Pillbox.omega3p` — the eigensolve (2 modes, order-2 curved elements). Names
  the mesh by bare filename, which the `mesh` module stages from
  `../assets/Pillbox.ncdf` (63 091 elements, 2 MB).
- `Pillbox.track3p` — the CW23 tutorial input: a three-level `FieldScales`
  scan, a secondary-emission `Emitter` box on boundary 6, `OutputImpacts: on`,
  and `Postprocess` with `ResonantParticles` and `EnhancementCounter` enabled.
- `copper.dat` — the secondary-emission-yield table the `EnhancementCounter`
  block names. Track3P reads it from its working directory, so the module's
  `files:` key stages it there.

## Running

```bash
run-lume-ace3p track3p_multipacting.yaml
```

On SLAC S3DF:

```bash
sbatch run_lume-ace3p_track3p_multipacting_s3df.batch
```

Both solves take under two minutes on 16 ranks. To sweep a Track3P setting
instead of running once, switch to `mode: parameter_sweep` and add an
`input_parameters: ace3p:` block naming a leaf of `Pillbox.track3p`, e.g.
`Domain.InitialEnergy`.
