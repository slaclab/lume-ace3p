# TEM3P reference

TEM3P is ACE3P's thermal and structural solver. It solves a thermal problem,
an elastic problem, or both, on the cavity *body* mesh. Its loads can come
from an Omega3P or S3P run: RF heating on the walls, or the radiation pressure
behind Lorentz-force detuning. Its main use here is **RF detuning**:

```
cubit(vacuum) ─▶ omega3p ─────────────┐ em_solution
cubit(body) ───────────────────────▶ tem3p ─▶ omega3p_deformed
                                 mesh       mesh (DeformedVacuumMesh.ncdf)
```

Omega3P solves the vacuum mode. TEM3P deforms the body under that mode's
loads and writes the vacuum mesh with its wall moved. A second Omega3P solves
the deformed mesh. The detuning is the difference of the two frequencies, a
`derived_parameters` entry. The chain covers Lorentz-force detuning (SRFCell),
thermal plus Lorentz-force detuning (RfGun), and sweeps or optimizations over
geometry or gradient.

TEM3P also runs without RF: mechanical eigenmodes, pure thermal problems with
prescribed loads, and S3P-driven heating (`s3p → tem3p`). These are supported
and tested, but no example ships for them.

For the YAML keys see [](yaml_reference.md#tem3p-module). The worked example is
`examples/tem3p_lfd`, a Lorentz-force detuning sweep over gradient.

:::{note}
**Sources.** The input keys are in `references/TEM3P-commands.pdf`. The output
formats come from real runs on the S3DF Rocky 9 build (2026-11-02), frozen as
fixtures under `tests/fixtures/tem3p/` with provenance in its `SOURCES.md`.
Several behaviours below (ignored keys, the `Omega3PId` rule) were confirmed in
the source, `/sdf/group/rfar/lge/sdf/ace3p/tem3p/src`. TEM3P warns about
nothing it ignores, which is why the module checks so much before a run.
:::

## Which problems run

The blocks in the input decide this. `ThermostaticProblem` runs the thermal
solve, `ElasticProblem` the elastic one, and with both TEM3P runs thermal
first and feeds the temperature to the elastic solve. An `EigenSolver` inside
`ElasticProblem` computes mechanical eigenmodes instead of a static
deformation.

There is no outer iteration. The `ThermoElasticProblem` block, its `RunId`,
and its tolerance and `MaxIterations` keys are not read. The only loop is the
Newton solve of a nonlinear thermal problem (temperature-dependent k(T) or
Rs(T)). A converged detuning therefore needs more passes in the workflow; see
[](#appended-pairs-for-convergence).

## What a run writes

Usage is `tem3p <input> [jobname]`, so `results_dir:` selects the job
directory (default `tem3p_results`). Every file is inside it.

| File | Read? |
|---|---|
| `ResultFile` | **yes**: the thermal block, the displacement row, the eigenmode table |
| `tem3p.log` | **yes**: scaling factors, DOFs, Newton iterations, run time, the EM directory it loaded, and the `Ending run at` terminator |
| `DeformedVacuumMesh.ncdf` | **handed on** as a `mesh` (below) |
| `tem3p.out` | no: an echo of the input, with no results |
| `tem3p.warn`, `tem3pStat.log` | no |
| `Displacement.mod`, `Temperature.mod`, `Stress.mod`, `Strain.mod` | no: viewer files |
| `DeformedMesh.ncdf`, `OUTPUT/`, `VECTOR/postprocess.in` | no |

- **`ResultFile` is opened in append mode.** A re-run into the same directory
  adds a second block. The module's `clean` removes the job directory before
  every fresh run, and the parser reads the last run only.
- **The exit code is no evidence.** `tem3p` with a wrong argument count prints
  its usage and exits 0. A run counts as finished when `ResultFile` exists,
  the log has its `Ending run at` line, and the deformed mesh exists if the
  input asks for it.
- **Some fatal errors go to srun's stdout only.** A missing table file, for
  example, prints `File functions/NbTi not found. TEM3P FATAL ERROR` there
  (rc 137) and nothing to `tem3p.log`. Look in the step's
  `<workdir>/<name>.log`.

## How TEM3P reads the EM run

A coupled `Boundary` or `HeatSource` names the EM run in `Directory`. TEM3P
reads `<Directory>/VECTOR/postprocess.in`, which holds the EM run's
**absolute** mesh path (`ModelInfo.File`) and its `JobName`. It then loads the
fields from `<JobName>/VECTOR/…` **relative to its own working directory**.

- TEM3P must run in the EM run's working directory. Every module of an
  evaluation shares one workdir, so this always holds.
- Only one EM run is read per TEM3P run.
- Every rank loads the whole vacuum mesh and its fields (see
  [](#memory-and-launch)).
- **A moved workdir breaks it.** The mesh path in `postprocess.in` is
  absolute, so after the evaluation directory moves, TEM3P aborts with
  `MPI_ABORT … errorcode -12313` and no message. Before launching, the module
  checks that every EM run it reads names a mesh that exists, and otherwise
  fails with an error naming the file and saying to re-run the EM step. A
  resume of a finished point re-parses and does not re-run TEM3P, so it is
  unaffected.

## What the module sets in the input

When an EM run is bound (`from: {em_solution: …}`, or the one `omega3p`/`s3p`
in the workflow):

| Leaf | Set to |
|---|---|
| `Directory` of every RF-coupled `Boundary` and `HeatSource` | the bound EM run's results directory, always |
| `ElasticProblem.MeshDump.EMMeshInputDir` (when the deformed mesh is written) | the **base** EM run: the bound one, unless its mesh came from a `tem3p`, in which case that `tem3p`'s base |

"RF-coupled" means a `ConditionType` of `RFHeating`, `RFheating`,
`RFHeatingMap`, `LFDetuning` or `LFdetuning`. A different value already in the
input is replaced, with a printed note. It is not kept just because it names a
directory that exists: an appended pair's workdir holds the *other* EM runs,
so an input still saying `omega3p_results` would silently read the first run's
fields. A dry run writes the injected leaves to its marker file.

`MeshFile` is not rewritten. The body mesh is staged into the workdir by
basename like any other mesh, and a build check confirms each problem block's
`MeshFile` is the file the bound mesh producer makes.

## The deformed vacuum mesh

```text
ElasticProblem: {
  ...
  MeshDump: { WriteDeformedEMMesh: on }
}
```

writes `<job>/DeformedVacuumMesh.ncdf`, the vacuum mesh with the cavity wall
moved by the computed displacement. The module then provides it as a `mesh`.
A solver bound to it (`from: tem3p`) gets `ModelInfo.File:
./<job>/DeformedVacuumMesh.ncdf`, so both Omega3P solves of the chain can
share **one** input file. Each writes its merged copy under its own name
(`SRFCell_omega3p.omega3p`, `SRFCell_omega3p_deformed.omega3p`).

- **Only nodes on the vacuum sideset named by `Omega3PId` move.** TEM3P reads
  `Omega3PId` from `LFDetuning` and `Neumann` boundaries of `ElasticProblem`
  only. Without one, TEM3P exits 0 and writes the mesh **undeformed** (a
  Δf of 2e-6 Hz in the probe). This is a build error when something reads
  the mesh, a warning otherwise.
- **`WriteDeformedEMMesh` is on only for the exact string `on`.** `On` or
  `yes` leaves it off, so the build refuses them. The same holds for
  `WriteDeformedMesh` and `WriteStressStrain`.
- `EMMeshInputDir` is mandatory with `WriteDeformedEMMesh`. The module sets it.
- `MeshDeformScale` is not read. To exaggerate a deformation for viewing, use
  `acdtool mesh deform` (see [](acdtool_reference.md)).
- Body and vacuum nodes are matched within a fixed 1e-3 tolerance, so the two
  meshes must share their wall geometry.
- An `EigenSolver` run writes one deformed mesh per mode
  (`DeformedVacuumMesh_Mode*`). These are not handed on, and binding a mesh to
  such a `tem3p` is a build error.

(appended-pairs-for-convergence)=
## Appended pairs, for convergence

One pass deforms the cavity under the *undeformed* mode's fields. To move
towards a self-consistent detuning, append `tem3p → omega3p` pairs, each
reading its fields from the previous deformed solve:

```yaml
  # ... cubit_vacuum, cubit_shell, omega3p, tem3p, omega3p_deformed as in
  # examples/tem3p_lfd, then:
  - module : tem3p
    name : tem3p_2
    input : 'SRFCell.tem3p'
    results_dir : 'tem3p_2'
    from : {mesh : cubit_shell, em_solution : omega3p_deformed}
  - module : omega3p
    name : omega3p_d2
    input : 'SRFCell.omega3p'
    results_dir : 'omega3p_d2'
    from : tem3p_2

output_parameters :
  'f0' : {module: omega3p,          quantity: Frequency, at: {mode: 0}}
  'f1' : {module: omega3p_deformed, quantity: Frequency, at: {mode: 0}}
  'f2' : {module: omega3p_d2,       quantity: Frequency, at: {mode: 0}}

derived_parameters :
  'df_1' : 'f1 - f0'
  'df_2' : 'f2 - f0'
```

The second `tem3p` reads the deformed run's fields, but the module points its
`EMMeshInputDir` at the original `omega3p` run. Every pass therefore deforms
the **original** vacuum mesh. Deforming the already-deformed mesh would add
the displacements together. On SRFCell at 35 MV/m the first pass gives
−7172.1 Hz and the second −7240.4 Hz (+0.95%). The same second pass on the
deformed mesh gives −14412.2 Hz.

Each pair needs its own `name:` and `results_dir:` (two solvers of one type
may not share a job directory), and the comparison of `df_1` and `df_2` is
yours to make. There is no "repeat until converged" construct.

:::{warning}
**A swept TEM3P leaf reaches one `tem3p` per scope.** The
`ace3p: tem3p: {ElasticProblem: …}` block of `examples/tem3p_lfd` reaches
`tem3p` only, not `tem3p_2`. A second scoped block for `tem3p_2` would be a
second sweep axis, giving a tensor product rather than one value applied to
both. An unscoped block would also be appended to both Omega3P inputs. So an
appended chain should sweep inputs that reach both passes (Cubit geometry),
or keep TEM3P's own leaves fixed in its input file.
:::

## Build-time checks

TEM3P ignores unknown and misspelled keys without a warning, and the CW23
inputs carry several of them. Every item below is a build error, raised
before the first mesh is built and listed together in one message:

| Problem | Fix the message names |
|---|---|
| `NeumanValue` | spell it `NeumannValue` (a load spelled this way is not applied) |
| `AbsoluteTolerante`, `AbsoluteTolerance` | remove it; the linear solvers stop on `Tolerance` only |
| `Abs tolerance`, `Rel tolerance` | spell them `AbsTolerance`, `RelTolerance` |
| A `MeshDump` switch written `On`, `yes`, `true`, `1` | write `on` |
| `MixedType` not exactly three upper-case `DIRICHLET`/`NEUMANN` entries | TEM3P exits on anything else |
| `LFDetuning` without `Method` | `Method: Gradient` with `TargetGradient`, `StartPoint`, `EndPoint` |
| `Method: Gradient` without `StartPoint` or `EndPoint` | add them (three coordinates each) |
| A table (`Function`, `SurfaceResistance`, `SigmaFunction`, `DielectricConstantFunction{E,H}`, `ExtVHeatingFile`) that `files:` does not stage | `functions/NbTi` needs `files: [functions]`; a bare `NbTi` needs a `files:` entry of that name |
| An RF-coupled boundary or `WriteDeformedEMMesh: on`, with no EM run bound | add the `omega3p` or `s3p` that solves the vacuum fields |
| A module reads this `tem3p`'s mesh, but its input does not write one | add `MeshDump: { WriteDeformedEMMesh: on }` |
| A module reads this `tem3p`'s mesh, but it runs an `EigenSolver` | remove the `EigenSolver` or bind the mesh elsewhere |
| A read mesh but no `Omega3PId` | set it on the `LFDetuning` boundary, to the vacuum mesh's wall sideset |
| `MeshFile` is not the bound producer's mesh (its export, or the `.ncdf` under `meshconvert`) | point `MeshFile` at it, or bind the right `mesh` module |
| The body mesh is bound to a `tem3p` | bind `from: {mesh: <body mesh module>}` |

An EM run that is bound but read by nothing in the input gets a warning.

The CW23 inputs use the ignored spellings verbatim, so copy them with care.
`examples/tem3p_lfd/SRFCell.tem3p` is CW23's with them corrected.

## Extractable quantities

From `ResultFile`:

| Quantity | Meaning | `at:` |
|---|---|---|
| `max_temperature`, `min_temperature` | K, from the thermal block | — |
| `sideset_power` | power into one sideset, W | `{sideset: n}`, required |
| `u_x_min`, `u_x_max`, `u_y_min`, `u_y_max`, `u_z_min`, `u_z_max`, `u_mag_max` | displacement extrema, m | — |
| `mechanical_frequency` | eigenmode frequencies, Hz, over the `Mode` axis | `{mode: n}`, optional |

From `tem3p.log`:

| Quantity | Meaning | `at:` |
|---|---|---|
| `scaling_factor` | the factor TEM3P scales the EM fields by (the first one logged; one is logged per coupled boundary or heat source) | `{problem: thermal\|elastic}` when both problems ran |
| `newton_iterations` | Newton iterations of a nonlinear solve | as above |
| `n_dofs` | DOFs of the body problem | as above |
| `run_time` | s | — |

- **The displacement row is one global summary, not one row per block.** Its
  `block_id` is the block where |u| peaks, so no `at:` is needed.
- **An eigen run is tabulated over `Mode`.** `mechanical_frequency` and the
  displacements are arrays over it, so a sweep table goes long (one row per
  point and mode) unless every output is narrowed with `at: {mode: n}`. The
  mode count is a result, so a dry run's table is wide.
- **Every name except `run_time` and `n_dofs` routes bare.** So does any spec
  with `at: {sideset: …}` or `at: {problem: …}`. Write `module: tem3p` for
  the other two, and always when the workflow has several `tem3p` modules.
- A quantity the run did not write (a temperature from an elastic-only run)
  raises, naming what is missing. The log's stress and strain extrema are not
  read: they are uninitialized values.
- Δf and the detuning coefficient are `derived_parameters`. An expression may
  read a swept input by name, so `k_l = df / (TargetGradient / 1e6)**2` holds
  under a gradient sweep.

(memory-and-launch)=
## Memory and launch

TEM3P is pure MPI, so `cores: 1`. Each rank holds the **whole** vacuum mesh
and its fields, on top of its share of the body problem, so an RF-coupled run
does not shrink its per-rank memory by adding ranks. Peak per rank in the
probe runs (`sacct` `MaxRSS`, 16 ranks unless noted):

| Case | Peak per rank |
|---|---|
| SRFCell (Lorentz-force detuning) | 0.30 GB |
| RfGun (thermal plus LFD) | 0.55 GB |
| TeslaCavity (mechanical eigenmodes) | 1.52 GB |
| SrfCoupler (S3P heating, 96 ranks) | 0.39 GB |

Under sweep workers, the `--mem-per-cpu` each worker gets must cover this
(see [](parameter_sweep.md#running-points-in-parallel)).

## Out of scope

- A feedback-loop construct ("repeat until converged"). Append pairs instead.
- Thermal transients (`Transient:`) and `HarmonicAnalysis`.
- The per-mode deformed meshes of an eigen run.
- `field()` access to the `.mod` files.
- `acdtool postprocess project` and `tem3pOutputConverter`.

## Related

- [](yaml_reference.md#tem3p-module): the module's keys and its place in the
  module table.
- [](yaml_reference.md#several-instances-of-one-module): `name:`, `from:` and
  `results_dir:` for two Omega3P solves in one workflow, and `files:`
  directories.
- [](yaml_reference.md#derived_parameters): expressions over outputs and
  swept inputs.
- `examples/tem3p_lfd`: the detuning sweep, with its validated numbers.
- `tests/fixtures/tem3p/SOURCES.md`: provenance for the outputs quoted here.
