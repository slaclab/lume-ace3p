# track3p_geant4_chain

The dark-current chain with Track3P run **in the pipeline**:

```
workflow:  mesh -> omega3p -> track3p -> particles
mode:      single
```

Omega3P solves the CW23 pillbox eigenmodes, Track3P tracks field-emitted
electrons off the cavity wall at 23 MV/m, and the `particles` step reweights
the resulting 17-column dump into a Geant4 source file by the Fowler–Nordheim
current at `β·E`.

This is the in-pipeline counterpart of
[`geant4_track3p_beta`](../geant4_track3p_beta), which supplies a pre-run dump
through `track3p_source`.

## Which head to use

| | this example (`track3p`) | `geant4_track3p_beta` (`track3p_source`) |
|---|---|---|
| Track3P | runs per evaluation | run once, externally |
| Cost | a full solve per point — **~50 node-minutes per field level** for a cryomodule mesh | seconds of pure Python per point |
| Good for | producing a dump: a geometry sweep, a new cavity, a new field level | exploring β over a dump you already have |

The same dump reweights analytically for **any** β, so a β sweep does not need
this head. Reach for it when the thing you are varying changes the *fields* —
and reach for `track3p_source` otherwise.

## The Geant4 step is missing, deliberately

The plan for this example called for a five-module chain ending in `geant4`.
It stops at `particles` instead: **there is no Pillbox Geant4 geometry.** The
dose app needs a copper body (`solid_stl`) and a vacuum cavity (`cavity_stl`)
as STL meshes, and nothing in this repo or in the app's own package ships them
for the pillbox. The 7cell STLs in `../assets/` are a different cavity — this
dump's primaries sit at r = 0.079–0.10 m, |z| ≤ 0.05 m, nowhere near the 7cell
wall — so pointing at them would produce a dose number with no physical
meaning.

The cavity surface *could* be extracted from `Pillbox.ncdf` exactly (6 836
closed triangles). The copper body could not: the mesh describes only the
vacuum volume, so a wall thickness would have to be invented, and the dose
scales with it. That is a modelling decision, not a packaging one, so it is
deferred rather than guessed. The YAML carries the `geant4` block commented out
with the two filenames it would need.

Everything the chain exercises up to that point is real: the `FieldDir`
injection, the `Initials-Impacts` selector injection, resolving the dump out of
Track3P's results directory, and the weighting itself.

## Two things that silently break this case

Both were found the hard way (`plans/track3p_module_plan.md`, Phase 3 step 2).

**The emitter must not have a bounding box.** `Emitter` with `x0..z1` emits
only from faces inside the box, and the obvious box over the end-wall annulus
selects 14 faces whose normal field is low enough that the Fowler–Nordheim
current underflows to *exactly* zero. The run then emits nothing, writes a
header-only dump, and still exits `Done!`. Boundary 6 is the whole cavity wall,
which is what gives the 6 558 emitting faces this case relies on.

**β belongs to this dump, not to the LCLS-II study.** `InitialNormalField`
here averages 3.7e7 V/m, so the one-electron cut bites between β = 35 and
β = 50:

| β | macroparticles kept (of 23 984) |
|---|---|
| 30 | 0 |
| 35 | 51 |
| 40 | 9 191 |
| 45 | ~17 000 |
| 50 and up | 23 984 (saturated) |

The example uses **β = 45**. At the study's 100–150 every row survives at every
point and the weighting demonstrates nothing — the same trap that silently
flattened `geant4_track3p_beta` in Phase 2.

## Inputs

- `Pillbox.omega3p` — the eigensolve (2 modes, order-2 curved elements). Names
  the mesh by bare filename; the `mesh` module stages
  `../assets/Pillbox.ncdf` (63 091 elements, 2 MB), shared with
  `track3p_multipacting`.
- `Pillbox-fe.track3p` — field emission (`Emitter Type: 7`, `N: 1`,
  `WorkFunction: 4.2`, `Beta: 50`) on boundary 6 at one level
  (`ScanToken: 0  Scale: 23e6`). Verified on milano; a fixture copy is
  `tests/fixtures/track3p/inputs/Pillbox-fieldemission-n1.track3p`.
- `copper.dat` — the SEY table the input's `EnhancementCounter` block names.
  Track3P reads it from its working directory, so `files:` stages it there.

The `Beta: 50` in the Track3P input and the `beta: 45.0` on the `particles`
module are different quantities that happen to share a name: Track3P's scales
the field that decides *which* electrons are emitted and tracked, the module's
scales the field that decides *how many real electrons* each macroparticle
stands for. Changing the former needs a new solve; changing the latter does
not.

## `impacts_format`

Track3P's default dump has no field-emission columns, and
`particles` **misreads it silently** rather than rejecting it — it eats the
uncommented header as a data row and weights whatever the columns contain.
`impacts_format: initials-impacts` on the `track3p` module injects the two
lines that select the right layout:

```
OutputImpacts: on
OutputImpactsInfo: { Type: Initials-Impacts }
```

The selector is a *container*; the scalar spelling
`OutputImpactsInfo: Initials-Impacts` is silently ignored by the build. The
workflow refuses to build if a field-emission step is downstream of a `track3p`
step that would write the wrong layout, so this cannot be forgotten — and the
same check rejects a `track3p_source` file whose header is the default layout.

`Pillbox-fe.track3p` also carries both lines itself, so it stays runnable by
hand. The injection is idempotent.

## Outputs

One field level, so the table has one row, written to
`track3p_geant4_chain_output.txt`.

| Column | From | Meaning |
|---|---|---|
| `EC_max` | `OUTPUT/enhancementCounter` | largest `maxEnhancement` |
| `impacts` | same | sum of `totalImpactNum` |
| `emitted` | `track3p.log` | `Total Emitted Particles` (23 984 here) |
| `primaries` | `particles` | macroparticles surviving the filters |
| `electrons` | `particles` | real electrons they stand for (summed weight) |

## Running

```bash
run-lume-ace3p track3p_geant4_chain.yaml
```

On SLAC S3DF:

```bash
sbatch run_lume-ace3p_track3p_geant4_chain_s3df.batch
```

Both solves take about two minutes. The batch script exports
`OMP_NUM_THREADS=8` to match the YAML's `cores: 8`: `srun` carries `-n`/`-c`
but nothing carries the OpenMP environment, and there is no per-module `env:`
key to keep in sync. Keep the two in step if you change either.

## Not frozen as a baseline

This example is in `tests/baseline/not_frozen.json`. The `particles` step has
no dry-run branch — the weighting is pure Python, so it always runs — and under
a dry run the `track3p` artifact is a workdir with no dump in it, so the step
would raise. Building a placeholder-artifact mechanism so two steps with
nothing to compute can pass a dry run is not worth it.

CI covers the chain by asserting that it **validates and orders**, plus the
build-time layout rejections above (`tests/test_workflow_graph.py`). Its value
beyond that is one real S3DF run, recorded in
`plans/s3df_example_validation_plan.md`.
