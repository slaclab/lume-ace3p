# geant4_dose_single

A single-evaluation run of the full downstream-dose pipeline on the declarative
module/mode schema:

```
workflow:  track3p_source -> field_emission -> geant4
mode:      single
```

An externally-produced Track3P particle dump is field-emission-weighted into a
Geant4-format source file (`particles.data`), which a Geant4 run turns into
dose/edep voxel grids; `output_parameters` then pulls scalar dose/edep totals out
of that output into a one-row result table.

Particle tracking is done externally and the dump is supplied to the
`track3p_source` module. The weighting uses the plain Fowler–Nordheim form of the
LCLS-II reference converter (work function 4.2 eV, one RF period at 1.2999 GHz,
impacts below 1 keV dropped), the same settings as
[`../geant4_track3p_beta`](../geant4_track3p_beta); where that example *sweeps* a
broadcast `beta` scalar, this one uses a fixed per-bin `beta` vector and runs
once.

The vector is `[44, 46, 48, 50, 50, 48, 46, 44]` — low at the ends, peaked in the
middle, so the axial bins carry visibly different weights. Those values are sized
against *this dump*, whose `InitialNormalField` averages 3.7e7 V/m (about 4× the
LCLS-II cryomodule the reference converter's own β ≈ 120 belongs to): above about
β = 55 every macroparticle clears the one-electron cut and the per-bin structure
washes out, so 44–50 is where the bins still differ. See the sibling example's
README for the same caveat on its swept range.

## Assets

The large *shared* inputs live in [`../assets/`](../assets) and are referenced
by relative path from this example's YAML:

- `sample_track3p_particles.txt` — the external Track3P dump
- `7cell_solid_whole.stl`, `7cell_cavity_whole.stl` — geometry

The Geant4 input file `input_7cell.geant4` is *not* shared; each Geant4 example
carries its own. It names its STL geometry by bare filename, so the YAML lists
the `../assets/` STLs under `geant4_geometry_files` and the module stages them
into the workdir. Run from this directory so the `../assets/` paths resolve.

## Running

```bash
run-lume-ace3p geant4_dose_single.yaml
```

With the Geant4 binary absent the run is a **dry run**: the particle-weighting
step still executes for real and writes `particles.data`, but the dose scalars
are `NaN` until a real Geant4 run produces the scoring files.
