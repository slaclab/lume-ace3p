# Shared example assets

This folder holds large input files that are **shared across multiple examples**.
They are referenced by relative path (`../assets/...`) from each example's YAML so
the repository carries no multi-megabyte duplicates.

This folder is **not a runnable example** — it has no YAML and nothing to run. It
exists only to store inputs consumed elsewhere.

## Contents

- `7cell_solid_whole.stl`, `7cell_cavity_whole.stl` — Geant4 STL geometry for the
  7-cell cavity. Consumed by the `geant4_*` examples
  ([`../geant4_dose_single`](../geant4_dose_single),
  [`../geant4_track3p_beta`](../geant4_track3p_beta),
  [`../geant4_beta_surrogate`](../geant4_beta_surrogate)), listed under
  `geant4_geometry_files` so the module stages them into the workdir.
- `sample_track3p_particles.txt` — the external Track3P particle dump fed to the
  `track3p_source` module. Consumed by every `geant4_*` example above and by
  [`../track3p_particle_weight`](../track3p_particle_weight).
- `test_particles.txt` — a small (~2k-line) Track3P dump in the same column
  format, kept as a lightweight fixture. It is **not referenced by any example
  YAML**.
