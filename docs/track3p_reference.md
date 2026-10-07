# Track3P reference

Track3P is ACE3P's particle tracker. It reads the fields an Omega3P or S3P run
wrote and tracks emitted electrons, which makes it the solver behind two quite
different studies:

- **multipacting and dark current** — does the resonant electron population grow,
  and at which field level? The answer is in its postprocess tables.
- **field emission into Geant4** — where do Fowler–Nordheim electrons land, with
  what energy, so a downstream radiation simulation can transport them. The
  answer is in its 17-column impact dump.

The two want different output from the same binary, and the second needs an
**opt-in dump layout** that the first never sees. This page maps that surface:
what a run writes, which of it is read, the two dump layouts and the one-line
selector that chooses between them, and when to run Track3P in the pipeline
versus feed it a dump you already have.

For how to *use* it from a YAML config see [](yaml_reference.md#track3p-module);
for worked examples, `examples/track3p_multipacting` (a multipacting field scan)
and `examples/track3p_geant4_chain` (the in-pipeline field-emission chain).

:::{note}
**Sources.** `references/track3p-commands.pdf` is inputs-only: it documents none
of `OutputImpactsInfo`, `LostParticles_*`, `PtrackMonitor` or `Domain.Mode`, and
none of the `Emitter` keys `N M Q d SuppressionFactor`. Every output format below
therefore comes from real runs frozen as fixtures under
`tests/fixtures/track3p/`, with provenance in that directory's `SOURCES.md`;
where the PDF and the shipped build disagree, **the build wins** and this page
says so. The `InputParameters` file each run writes — an echo of every key the
build recognises, with its default — is the best documentation of the build that
exists.
:::

## What a run writes

The results directory is the second positional argument, else `track3p_results`.
Unlike T3P, Track3P accepts that argument, so `results_dir:` on the module
genuinely selects it.

| File | Written when | Read? |
|---|---|---|
| `track3p.log` | always | **yes** — the banner, the per-level `scale … Field …` lines, `Total Emitted Particles`, `Survived particles`, and the `Done!` terminator |
| `track3p.warn` | always | no |
| `InputParameters` | always | no (but see the note above — it is where undocumented keys are discoverable) |
| `ImpactsInfo_<level>` | `OutputImpacts: on` | **by path**, summarised on demand; layout depends on `OutputImpactsInfo` (below) |
| `LostParticles_<level>` | `OutputImpacts: on` | **by path** → `lost_count` |
| `OUTPUT/enhancementCounter` | `Postprocess.EnhancementCounter.Token: on` | **yes** |
| `OUTPUT/resonantparticles` | `Postprocess.ResonantParticles.Token: on` | **yes** |
| `OUTPUT/MPParticles<level>` | same | no — per-particle `Particle_ID: n Total_Impacts: m` blocks, not a table |
| `OUTPUT/faradaycup_<boundaryID>` | `Postprocess.FaradayCup: {Token: on  BoundaryID: …}` | **yes** → `captured_electrons` |
| `PARTICLES/partpath_ts*.ncdf`, `emissionevents_ts*.ascii` | `ScanToken: 0` | no — trajectories |

Two things the wrapper used to get wrong, both fixed:

- **There is no `track3p.out`.** The run log is `track3p.log`, *inside* the
  results directory.
- **There is no `en` file.** `en` is written by `acdtool postprocess track3p`,
  which [](acdtool_reference.md) covers separately.

**`Done!` is the only evidence a run finished**, and it is not on the last line —
a field-emission run prints `Total Emitted Particles = N` after it, and Lixin
Ge's build prints `Survived particles` before it. So the module's `verify` looks
for the terminator anywhere in the file, and a killed run (which leaves a
perfectly readable log) is correctly reported as incomplete.

## Field level is the index axis

Levels are declared **in the input**, in `FieldScales`: `Minimum`/`Maximum`/
`Interval` with `ScanToken: 1`, or a single `Scale` with `ScanToken: 0`. The same
numbers then appear in every output filename (`ImpactsInfo_2.3e+07`) and in the
`fieldlevel` / `Field_Level` table columns.

Because they come from the input, the axis is known **before** the run — S3P-shaped,
not Omega3P-shaped — which is what lets a dry run report the right number of
rows. A `single` or `parameter_sweep` table therefore goes long-format, one row
per field level, and `at: {field_level: x}` narrows a quantity to one scalar.

:::{note}
The same three sources spell a level with different rounding (`2.3e+07` in a
filename, `2.30000e+07` in a column, a `Minimum + k·Interval` floating sum in the
input). They are merged to one axis within 1e-9 relative, so a level is matched
by value rather than by its spelling.
:::

In a `omega3p → track3p` chain the Omega3P step also exposes an axis (`ModeID`).
Track3P's wins: the modes are its *input*, and a mode frequency in that chain is
one scalar per run, not something a multipacting result is tabulated over.

## The two `ImpactsInfo` layouts

Both are 17 columns wide. They are **different files**, and the width is a
coincidence — the distinguishing columns are the last two.

```text
# default ("general"), header NOT commented:
InitialID ImpactNum Initial_x Initial_y Initial_z Impact_x Impact_y Impact_z
InitialPhase ImpactPhase ImpactEnergy NumElectrons momentum_x momentum_y
momentum_z FaceID volID

# OutputImpactsInfo: { Type: Initials-Impacts }, header '#'-commented:
#InitialID ImpactOrder Initial_x Initial_y Initial_z Impact_x Impact_y Impact_z
InitialPhaseinRFcycle ImpactPhaseinRFcycle ImpactEnergy momentum_x momentum_y
momentum_z ImpactFaceID InitialNormalField InitialFaceArea
```

The field-emission chain needs the **second**: only it carries
`InitialNormalField` and `InitialFaceArea`, the surface field and face area the
Fowler–Nordheim weight is computed from. `InitialPhase` and
`InitialPhaseinRFcycle` are the same quantity (time × frequency).

:::{warning}
**The selector is a container, and the scalar spelling is silently ignored.**

```text
OutputImpacts: on
OutputImpactsInfo:
{
  Type: Initials-Impacts
}
```

Written as `OutputImpactsInfo: Initials-Impacts` it parses as a leaf, the build
ignores it (`findContainer`, `genptab.C:543`), and you get the **default** layout
with no error. Worse, the weighting step used to misread that file silently
rather than reject it — the uncommented header is consumed as a data row. Hence
`impacts_format: initials-impacts` on the module, which injects both lines, and
a build-time check that refuses a `field_emission` step downstream of a dump in
the default layout (including a `track3p_source` file, sniffed by its first
line).
:::

Three other `Type` values exist (`ImpactsManager.C:286–580`), none of which this
package reads: `Position` (12 columns), `PositionField` (16), and
`Geant4-Energy` (16). A fourth, `Type: Geant4`, writes a 10-column headerless
`particles.data` — the Geant4 source format — and needs `BoundarySurfaceID`;
`field_emission` writes that format itself, from the richer dump, so there is no
reason to use it here.

**The two field-emission columns are only meaningful for `Emitter Type: 7`.** In
a secondary-emission run they hold uninitialized memory — denormals and
negatives, not zeros. Even in a field-emission run they are real only on the
`ImpactOrder 1` rows; rows of order ≥ 2 are secondary electrons and their two FE
columns are garbage. The weighting filters to order 1 anyway.

### `InitialNormalField` already includes the phase factor

It is the normal field at the emission site **at the emission phase**, not the
peak field at that site. Verified on Lixin Ge's `c3_16MV` library: grouping the
emission rows by position and fitting against `InitialPhaseinRFcycle` gives
`E_peak · sin(2πφ + φ₀)` to 5e-7 relative. Do **not** apply a `sin φ` factor on
top of it — the weighting consumes `J(β · InitialNormalField)` directly, as the
LCLS-II reference converter does.

## Field emission that emits nothing

A `Type: 7` run reporting `Total Emitted Particles = 0` *after* a clean `Done!`
is the single most confusing failure here, and the reason is almost never what it
looks like.

**It is the emitter's bounding box, not the `N` threshold.** With `N <= 1.0` the
build sets the macroparticle count to 1 unconditionally
(`EMT_FieldEmission.C:200–202`) — the `this_N < 0.5 · m_N` cut that suppresses an
`N: 100` run is in the *other* branch, so `N: 1` cannot be what stops emission.
What stops it is the current itself being zero: `FowlerLawRF` is
`C₁·(βE)^2.5·exp(−C₂/E)`, and at β = 50, φ = 4.2 eV the exponent underflows
`exp()` to *exactly* zero below E ≈ 1.5e6 V/m, which `continue`s before a
particle exists.

So an `Emitter` block with an `x0..z1` box that happens to select low-field faces
emits nothing, silently. On the CW23 pillbox a box over the flat end-wall annulus
selects 14 of 9 100 surface faces, all below that floor: zero particles. The same
box at β = 1000 emits 522, which is how the cause was isolated. Dropping the box
entirely — emitting from all 6 558 faces of boundary 6 — gives 23 984 particles
with real field-emission columns.

If a Type-7 run emits nothing, **widen or remove the emitter box** before
touching `N`.

## Extractable quantities

Per field level, as arrays aligned to the axis (`at: {field_level: x}` narrows
one to a scalar):

| Quantity | Source | Meaning |
|---|---|---|
| `max_enhancement` | `enhancementCounter` | largest `maxEnhancement` among the level's rows |
| `mean_enhancement` | `enhancementCounter` | mean `averageEnhancement` |
| `total_impacts` | `enhancementCounter` | summed `totalImpactNum` |
| `resonant_count` | `enhancementCounter` | rows, i.e. particles above `MinimumEC` |
| `resonant_particles` | `resonantparticles` | **distinct** IDs (each resonant particle is listed at two positions) |
| `max_resonant_energy` | `resonantparticles` | largest `Energy` |
| `impact_count` | `ImpactsInfo_<level>` | rows with impact ordinal ≥ 1 (ordinal 0 is the emission point) |
| `max_impact_energy` | `ImpactsInfo_<level>` | largest `ImpactEnergy` among those |
| `lost_count` | `LostParticles_<level>` | rows |

Per Faraday cup — `at: {boundary: id}` is **required**:

| Quantity | Meaning |
|---|---|
| `captured_electrons` | `sum(NumElectrons)` of `OUTPUT/faradaycup_<id>` |

Run scalars, which repeat down the level rows:

| Quantity | Meaning |
|---|---|
| `total_emitted` | `Total Emitted Particles` from the log |
| `emitting_faces` | `number of all emitting faces` |
| `survived` | `Survived particles` — **Lixin Ge's build only**; NaN on the group build |

And the derived objective:

| Quantity | Meaning |
|---|---|
| `mp_onset_level` | the lowest field level whose `max_enhancement` is at least `at: {threshold: t}` (default 1.0, i.e. growth), NaN if none reaches it |

`mp_onset_level` is the multipacting objective: the quantity a geometry
optimization minimises or constrains. A level with no rows in a table is NaN; a
table the run did not write *raises*, naming the `Postprocess` token that enables
it, rather than returning NaN.

The two impact dumps are read **lazily and partially** — only the ordinal and
energy columns, on first use, cached after. Lixin Ge's cryomodule dumps are
137 MB each, so the parser records their paths and nothing more.

**No Track3P log carries a wall-time line**, so there is no `walltime_s`.

## Running it in-pipeline, or not

A workflow has **either** `track3p` **or** `track3p_source` — both provide
`track3p_particles`, and one producer per artifact is a structural rule.

| | `track3p` (in-pipeline) | `track3p_source` (pre-run dump) |
|---|---|---|
| Cost | a full solve per evaluation — **~50 node-minutes per field level** at cryomodule scale | seconds of pure Python |
| Right for | producing a dump: a geometry sweep, a new cavity, a new field level | exploring β over a dump you already have |

The asymmetry is the whole point: **one dump reweights analytically for any β**,
because the Fowler–Nordheim weight depends on β while the impact state does not.
So a β study — which is most of the dark-current modelling work — should run
Track3P once, externally, and sweep β over the dump. Reach for the in-pipeline
head only when the thing being varied changes the **fields**.

The in-pipeline head also injects what the chain needs rather than making you
hand-write it: `Domain.FieldDir` is pointed at the upstream solver's results
directory (an explicit, existing `FieldDir` is respected), and
`impacts_format: initials-impacts` adds the two layout lines.

## Fowler–Nordheim: two models, and why the default moved

`field_emission` implements two current-density models and they are **not
interchangeable** — at β·E ≈ 2e9 V/m they differ by about two orders of magnitude
in weight, so a β inferred with one is not comparable with a β inferred with the
other.

| | `fn_model: 'fn'` (default) | `fn_model: 'wang-loew'` (legacy) |
|---|---|---|
| J(βE) | plain FN: `1.541434e-6/φ · (βE)² · exp(−6.830890e9 φ^1.5/βE)` | Wang–Loew: `1.54e-6·10^(4.52/√φ)/φ · (βE)² · exp(−6.53e9 φ^1.5/βE)` |
| Emission time | `1/f` from `frequency:` — one RF period | `dt:`, a free parameter |
| Weight | real-valued | rounded to whole electrons |

**`fn` is the default** so the repo reproduces the LCLS-II reference converter's
22 shipped particle files; the acceptance test agrees with them to 1e-6 relative
on 8 of the 10 columns, which is the shipped files' own `%.6e` precision. (The
other two are cosmetic: the reference writes 0 for impact phase and face ID,
which the Geant4 reader ignores.) `wang-loew` is kept bit-exact for comparison
with older studies, including its original arithmetic and charge constant.

The repo's own addition is a **per-bin β vector**: `num_bins` z-bins across the
dump, each with its own β, which is what makes β *localization* expressible.

:::{warning}
**A β range belongs to its dump.** The LCLS-II study scans β = 100–150, but that
range is meaningful only on the cryomodule dumps it was derived for. On the
repo's shared 7-cell asset — whose `InitialNormalField` averages 3.7e7 V/m,
about 4× Lixin's cryomodule — the same β·E is reached near β = 30, so above
β ≈ 55 *every* macroparticle clears the one-electron cut and a sweep writes an
identical row at every point: no failure, no warning, and nothing demonstrated.
Size β from the dump's own `InitialNormalField`, never from another study's
range. See the `geant4_track3p_beta` README for the worked numbers.
:::

## Related

- [](yaml_reference.md#track3p-module): the module's keys, the `extract` spec
  forms, `impacts_format` and `field_level`.
- [](yaml_reference.md#field_emission-module-keys): the `field_emission` weighting
  keys, and how it resolves a dump out of a results directory.
- [](acdtool_reference.md): `postprocess track3p`, which writes the `en` file
  from a finished run.
- `tests/fixtures/track3p/SOURCES.md`: provenance for every byte of output
  quoted on this page, including the probe runs behind the field-emission
  findings.
