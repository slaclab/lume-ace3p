# acdtool reference

`acdtool` is ACE3P's shared pre- and post-processing utility: **nineteen**
commands, and a `postprocess rf` input format with **24 blocks**. Every ACE3P
solver reference ends with *"Note: Refer to acdtool command syntax for
postprocessing capabilities."* and documents no output formats of its own, so
acdtool is the postprocessing layer for all of ACE3P.

This page lists that surface with each item's implementation status in
LUME-ACE3P: what exists, what is reachable, and what is deliberately not wired.
For how to *use* the wired subset from a YAML config, see
[](yaml_reference.md#acdtool-module).

:::{note}
**Sources.** The eight SLAC ACD command references under `references/`
(`acdtool-commands.pdf` plus one per solver; provenance in
`references/README.md`) are authoritative. They specify inputs thoroughly and
outputs barely, so every output format below comes from real runs frozen as
fixtures under `tests/fixtures/acdtool/`, with per-block coverage in
`tests/fixtures/acdtool/COVERAGE.md`. Where a block has no real output behind
it, this page says so.

The tables digest `lume_ace3p.acdtool.COMMANDS` and `lume_ace3p.acdtool.SECTIONS`,
which drive dispatch and parsing. If the two disagree, the code is right.
:::

## The 19 commands

Three top-level, five `mesh` subtasks, eleven `postprocess` subtasks. The
"CW23" column counts invocations across every batch script in the ACE3P tutorial
archive.

| Command | Argument form | Consumes | CW23 | Status here |
|---|---|---|---|---|
| `meshconvert <f>.gen [out.ncdf]` | positional | genesis mesh | 50 | invocable; **mesh producer, not wired**. Already run inside the `cubit` module |
| `meshconvertdirect <f>.gen [out.ncdf]` | positional | genesis mesh | — | invocable; mesh producer, not wired |
| `resource <f>.omega3p` | positional | Omega3P input | — | invocable; writes a suggested batch script to stdout / `acdtool.log` only |
| `mesh stats <f>.ncdf` | positional | mesh | (implicit) | invocable; stdout only. Run internally by `meshconvert` |
| `mesh check <f>.ncdf` | positional | mesh | (implicit) | invocable; stdout only. Run internally by `meshconvert` |
| `mesh fix <in>.ncdf <out>.ncdf` | positional | mesh | — | invocable; mesh producer, not wired |
| `mesh deform <in>.ncdf <out>.ncdf <scale>` | positional | TEM3P deformed mesh | 2 | invocable; mesh producer, not wired. See below |
| `mesh warpsurface <warp.in>` | positional (`warp.in` is a **third dialect**) | mesh | — | invocable; the filename passes through opaquely and the file is never parsed |
| `postprocess rf <f>.rfpost` | input file (`=` dialect) | Omega3P **or** S3P results | 16 | **wired**; requires `em_solution` |
| `postprocess eigentomode <jobname>` | positional | Omega3P/S3P results | — | invocable; writes ParaView `.mod` files, which nothing here reads. The solvers convert by default anyway |
| `postprocess volmontomode <jobname>` | positional | T3P/PIC3P results | 2 | **wired**; requires `td_solution` |
| `postprocess wake_new <jobname> <x y>` | positional | T3P results | — | invocable; not wired for lack of a fixture. The longitudinal counterpart of `transwake` and the most likely next addition |
| `postprocess wake_direct <jobname> <x y>` | positional | T3P results | — | invocable; as `wake_new`, by direct integration rather than a Laplace solve |
| `postprocess transwake <jobname> <x1 y1> <x2 y2>` | positional | T3P results | 2 | **wired**; requires `td_solution`; **mutates** its producer's output |
| `postprocess coaxsignal <jobname>` | positional | T3P results | 2 | **wired**; requires `td_solution` |
| `postprocess pic3pstats <f>.ncdf <symmetry factor>` | positional | PIC3P particles | 1 | invocable; no PIC3P module exists to hang it on |
| `postprocess pic3pconvert <f>` | positional | PIC3P particles | 2 | invocable; no PIC3P module exists to hang it on |
| `postprocess track3p <f>.acdtool <jobname>` | input file (`:` dialect) + jobname | Track3P results | 2 | **not invocable**; needs the KVC dialect this wrapper does not parse |
| `postprocess project <eigenmodes> [displacements]` | positional | TEM3P results | — | invocable; TEM3P is out of scope and the L2 projections go to stdout |

**"Wired"** means `AcdtoolModule` accepts it as a `workflow:` step.
**"Invocable"** means `lume_ace3p.acdtool.Acdtool.run` can dispatch it directly
from Python, but no module-layer home exists. An unknown command raises listing
the known ones. A known-but-unwired command raises naming why it is held back.

### Things about dispatch that are easy to get wrong

**`<jobname>` is a name, not a path.** Every positional `postprocess` command
takes the producing solver's job name, defaulting per solver (`omega3p_results`,
`s3p_results`, `t3p_results`, `pic3p_results`, `track3p_results`). LUME-ACE3P
injects the jobname from the producing module's resolved results directory: the
`results_dir:` module key if set, else a top-level `JobName` leaf in the solver
input file, else the per-solver default. `results_dir:` is the supported
override. `JobName` is undocumented in every solver reference and set by no
tutorial input file, though T3P's output confirms it honours one.

**`postprocess track3p`'s second argument is a jobname, not a field level.** The
tutorial calls `acdtool postprocess track3p Pillbox.acdtool 2.3MV`, where `2.3MV`
is a **directory** named after the field level the example ran at.

**Three input dialects, deliberately not unified.**

| Dialect | Files | Read by |
|---|---|---|
| `key = value` with braces | `.rfpost` (`postprocess rf`) | `lume_ace3p.acdtool` |
| KVC `key : value` with braces | `.acdtool` (`postprocess track3p`), and every solver input file | `lume_ace3p.ace3p.parse_ace3p` |
| flat `Key: value`, no braces | `warp.in` (`mesh warpsurface`) | nobody; the filename passes through opaquely |

A `.acdtool` input handed to this wrapper **raises**, naming the unsupported
command. Routing that dialect can land when selected-particle `Trajectory`
extraction is wanted. `postprocess track3p` otherwise duplicates Track3P's own
`Postprocess: {EnhancementCounter}` container, so `Trajectory`'s explicit
`ParticleID` list is the only capability the acdtool route adds.

**Serial vs parallel.** *"acdtool submodules run serially with the exception of
acdtool postprocess volmontomode and acdtool postprocess rf."* The tutorial runs
even those two at one rank, so one rank is the correct default for all 19.
`tasks:` is forced to 1 for the 17 serial commands, with a warning rather than a
silent override. `cores:` is *not* pinned: the tutorial runs the serial
`transwake` as `srun -n 1 -c 256`, one rank over many threads.

**Commands that mutate their producer's output.** `transwake`, `wake_new` and
`wake_direct` overwrite `<jobname>/OUTPUT/wakefield.out`, the file T3P itself
wrote and the workflow has already parsed. The T3P reference says the acdtool
wake commands write to the path *"where the file name 'wakefield' has been
specified in Monitor"*. The module layer declares the mutation and has the
producer re-read its output afterwards, so `t3p` stays the single owner of every
wakefield quantity and the workflow does not report the *longitudinal* loss
factor computed before acdtool ran. `coaxsignal` writes a new file
(`<jobname>/OUTPUT/signal.out`) and is unaffected.

T3P writes **six** kinds of monitor output and all of them are read, so
`{module: t3p, monitor: <Name>, …}` reaches a `Power`, `Point`, `ModeVoltage` or
`SurfacePowerLoss` monitor with no acdtool step; see [](t3p_reference.md). The
wake commands add only what no monitor can do: a transverse wake computed from
an on-contour longitudinal one.

**Why the mesh commands are not wired.** Producing a mesh would make acdtool a
second producer of the `mesh` artifact, which the one-producer-per-artifact rule
forbids, and `meshconvert` already lives in `lume_ace3p.cubit`. `mesh deform`
also duplicates TEM3P's own `MeshDump: {MeshDeformScale, EMMeshInputDir}`, which
writes the deformed vacuum mesh straight into the EM mesh input directory and is
the better route if the TEM3P chain is ever attempted. The acdtool reference
calls `mesh deform` a visualization convenience for small deformations.

### Out of scope, and why

**PIC3P and Gun3P.** Both are fully specified in their references, but neither
has a module, which leaves `pic3pstats` / `pic3pconvert` unwired. PIC3P is
structurally close to T3P (`ModelInfo`, `FiniteElement`, `PRegion`, `Loading`,
`TimeStepping`, `Monitor`, `LinearSolver`, `CheckPoint`), so a PIC3P module would
be a small lift and the natural next one. Gun3P is structurally different
(`DCGunProblem`, `ElectrostaticProblem`, `MagnetostaticProblem`, `Tracker`,
`Gun3pOutputConverter`) and a larger lift.

**TEM3P.** The tutorial's multiphysics chain
(`cubit ×2 → omega3p(vacuum) → tem3p(body) → acdtool mesh deform →
omega3p(deformed) → Δf`) needs two meshes, two `omega3p` modules and acdtool
*producing* a mesh. All three need per-instance rather than per-kind artifact
identity, a separate foundational effort.

## The `postprocess rf` input: 24 blocks in six output shapes

A `.rfpost` file is one required `RFField` configuration block plus any number of
postprocess blocks, each switched on by `ionoff = 1`.

**Two block sets, and they disagree.** The tutorial's template carries 19 blocks.
The reference documents 20 functionalities plus a 21st (`RoverQRoverQT`) in its
body without listing it. Neither is a superset of the other:

- in the tutorial but **absent from the reference**: `Track`, `TrackScan`,
  `coaxPort`;
- in the reference but **absent from the tutorial's template**: `pointRoverQ`,
  `dFSlater`, `RoverQRoverQT`, `IMPACTMap`, `OpenPMD_IMPACT`.

The union is **24 blocks**; the tutorial's template is from an older build. The
input parser therefore **tolerates unknown blocks and round-trips them
untouched** rather than enumerating a fixed list: a newer template carries blocks
never seen here, and an older one carries blocks the reference forgot.

Running `acdtool postprocess rf` with **no arguments** writes a `sample.rfpost`
template for the installed build, the authoritative default input.
`Acdtool.make_default_input` uses it when no `input:` is given, falling back to a
hardcoded 2-block subset when no binary is reachable.

The 24 blocks collapse into six output shapes, one reader each:

| Shape | Index axis | Blocks | Written to |
|---|---|---|---|
| Mode-indexed table | `ModeID` | `RoverQ`, `RoverQT`, `RoverQRoverQT`, `kickFactor`, `pointRoverQ`, `dFSlater`, `VFFT`, `ALLFieldAtPoint`, `coaxPort` | `rfpost.out` |
| Surface-indexed scalars | `surfaceID` | `maxFieldsOnSurface`, `powerThroughSurface` | `rfpost.out` |
| Single-mode scalars | — (uses `RFField`'s `ModeID`) | `FieldAtPoint` | `rfpost.out` |
| Column curves | position / phase | `FieldOnLine`, `ALLFieldOnLine`, `Multipole`, `GBZFFT`, `Track`, `TrackScan` | separate files |
| Field maps / grids | — | `FieldMap`, `IMPACTMap`, `OpenPMD_IMPACT`, `fieldOnSurface`, `fieldOn2DBoundary` | separate files |
| Run-level scalars | — | `[scaling]`: **always emitted, never declared** | `rfpost.out` |
| *(configuration)* | — | `RFField` | emits no output |

The **mode-indexed** blocks are exactly those carrying `modeID1`/`modeID2`.
`FieldAtPoint` has neither and evaluates only the single mode named in
`RFField`, so unlike `ALLFieldAtPoint` it has no index axis.

Column names come from the file (the header row of a column table, the
`name = value` / `name : value` lines of a scalar block), not from a per-block
list of column positions, so a build that adds or reorders a column is still read
correctly. A block whose output cannot be read warns naming itself
(`lume_ace3p.acdtool.AcdtoolOutputWarning`) rather than silently vanishing.

**Grid output is recorded, not parsed.** The filenames a grid block produced are
recorded; their contents are not read. Two of the five are binary or HDF5.

### Real-output coverage

Four of the 24 have a real acdtool output frozen behind them: **`RoverQ`**,
**`ALLFieldOnLine`**, **`maxFieldsOnSurface`** and **`[scaling]`**. The
`maxFieldsOnSurface` output, from an S3DF run of `examples/omega3p_sweep` (which
depends on that block for `E_max`), separates name and value with `:` rather
than `=`, prints a unit after the value, and adds a `ModeID :` line; the reader
accepts both separators. `kickFactor` has **none**: no tutorial run ever enabled
it, and the reference documents inputs only. Its reader follows the file's own
header row rather than an assumed layout, but the layout itself remains
unverified until a real run enables the block at `ionoff = 1`. See
`tests/fixtures/acdtool/COVERAGE.md`.

### Curve and grid filenames differ per block

The naming schemes are **not** uniform and cannot be inferred from one another:

| Block | Writes |
|---|---|
| `FieldOnLine` | `<filename>.e` and `<filename>.b` (real fields at `rfphase`), `<filename>.ec` / `<filename>.bc` (complex). E and B split across two files, **no mode suffix** |
| `ALLFieldOnLine` | `<filename>_<modeID>` (E **and** B together, plus `Sz`), plus `<filename>_<modeID>.ec` / `.bc` |
| `Multipole`, `GBZFFT`, `fieldOnSurface`, `fieldOn2DBoundary` | `<filename>` |
| `TrackScan` | `<filename>` and `<scanfilename>` |
| `FieldMap` | **fixed** names `Efield-map.dat` / `Bfield-map.dat`; the block has no `filename` key at all |
| `IMPACTMap` | `EBfield-map-<filename>.dat`, IMPACT format |
| `OpenPMD_IMPACT` | `E_Real.h5`, `E_Imag.h5`, `B_Real.h5`, `B_Imag.h5`: **HDF5**, not text |

The per-mode suffix is not knowable before the run (`modeID2 = -1` means every
mode the solver produced), so these names are **globbed rather than predicted**.
The glob also picks up the `.ec`/`.bc` siblings in one pass.

They also differ in **scaling**: `FieldOnLine` fields are scaled to `RFField`'s
`gradient`, while `ALLFieldOnLine` fields come straight from the eigenmode,
normalized to total stored energy. `[scaling]`'s `m_factor` records the same
distinction.

### Input semantics you cannot guess from the files

These are the reference's statements, not inferences:

| | |
|---|---|
| **`z1`/`z2`/`gz1`/`gz2` above `1e6` are sentinels** | They mean "use the minimum/maximum z of the computational domain", *not* coordinates. The tutorial's `z1 = 100000000.00000` is not a 100 m integration path. Anything that validates, sweeps or rescales these must not treat them as lengths. |
| **`gradient = -1` means "no scaling"** | It selects the point-scaled variant of the `[scaling]` output (`Ez from O3P`, `Ez scaled to`) instead of the gradient-normalized one (`V`, `ga`). |
| **`modeID1 = -1` means mode 0; `modeID2 = -1` means *all* modes the solver produced** | The tutorial's `-1 / -1` default already means "every mode", which is why the mode index is an *axis* rather than a selector. |
| **`ModeID` in `RFField` means different things per solver** | An eigenmode index for Omega3P; a **port-mode (excitation) index** for S3P, ordered by port then mode. |
| **`FreqScanID` is for S3P only** | A zero-based index into the `FrequencyScan`, selecting which scan point to postprocess. It silently means something different if the scan's `Start` / `Interval` changes. |
| **`powerThroughSurface`'s output is complex** | Unit W, the real part being the average power flow from the complex Poynting vector. It gets the same real/imaginary split as Omega3P's complex eigenfrequency, not a plain scalar. |
| **`VFFT` has a `printGroup` key** (`nterm` \| `ModeID`) | It groups the output by multipole component or by mode. Only `printGroup = ModeID` is a mode-indexed table; `nterm` warns naming the key rather than misreading it. |
| **`[scaling]` may ship unclosed** | In the S3P case the block has no closing `}`, so section ends are found by the next `[section]` header rather than by a closing brace. |
| **`ResultDir` is the batch job's "Jobname"** | Not a path chosen in the input file; see the jobname note above. |

### Headerless and oddly-commented outputs

- `postprocess coaxsignal`'s `signal.out` has **no header row at all**; its three
  columns (`t`, `V`, `I`) are named from the reference.
- `postprocess track3p`'s `en` names its seven columns on a bare **uncommented**
  first line.
- S3P's `PortRef<n>_<m>.out` is `%`-commented rather than `#`-commented, the only
  ACE3P output in the fixture set that is.
- T3P's own series monitors (`Point`, `Power`, `ModeVoltage`) are headerless too
  and read the same way; see [](t3p_reference.md).
- The T3P reference gives the wakefield monitor's columns as `W(s)` in V/C and
  `I(s)` in A/m, while the real file header says **V/pC** and **C/m**. The file
  header is authoritative and is what is read; do not "correct" the units to match
  the document.
