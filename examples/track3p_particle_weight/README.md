# track3p_particle_weight

A single-evaluation run that does **field-emission particle weighting only**, on
the declarative module/mode schema:

```
workflow:  track3p_source -> field_emission
mode:      single
```

There is **no ACE3P or Geant4 solver in this chain**. The weighting is the
`particles` post-processing module, fed by an external Track3P dump supplied to
the `track3p_source` module; the tracking itself is done externally. The
`particles` step is pure Python, so `mode: single` runs it once.

Unlike the Geant4 chain, this example sets `output_format: track3p`, which writes
the 19-column *weighted* Track3P dump (`track3p_particles_weighted.txt`) rather
than the 10-column `geant4` source file that
[`../geant4_dose_single`](../geant4_dose_single) and
[`../geant4_track3p_beta`](../geant4_track3p_beta) produce. Here `beta` is an
explicit per-bin vector (length `num_bins`), not a broadcast input variable that
a mode sweeps.

This example pins the **legacy Wang–Loew Fowler–Nordheim form**
(`fn_model: wang-loew`, weights rounded to whole electrons; emission time
`1/frequency` = 1e-10 s) so its frozen baseline stands. New studies should use
the default `fn` model, which reproduces the LCLS-II reference converter; the two
forms differ by about two orders of magnitude in weight and a β fitted with one
is not comparable with the other.

## Assets

The external Track3P dump lives in the shared [`../assets/`](../assets) folder and
is referenced by relative path from this example's YAML:

- `sample_track3p_particles.txt` — the external Track3P dump

Run from this directory so the `../assets/` path resolves.

## Running

```bash
run-lume-ace3p track3p_particle_weight.yaml
```

This is a local-only example with no batch script. The weighting step runs for
real and writes the weighted dump.
