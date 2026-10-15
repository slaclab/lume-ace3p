# Troubleshooting

## FAQs

### Why did `lume-ace3p` enable dry-run mode by itself?

One of these messages on startup

```
ACE3P environment not configured, enabling dry run mode.
```
```
Geant4 environment not configured, enabling dry run mode.
```

means `lume-ace3p` could not resolve the path to ACE3P (or to the Geant4
application, when the workflow has a `geant4` module) by any of the four
mechanisms: a `paths` mapping in `workflow_parameters`, the environment variable
(`ACE3P_PATH`, `GEANT4_APP_PATH` / `GEANT4_APP_EXE`), a built-in site default
(Perlmutter / S3DF), or autodetection on `PATH`/`$HOME`. The workflow still runs
in Python but skips the external solver call and writes a `DRY_RUN.txt` marker
in each working directory. To run for real, set one of those paths; see
[](installation.md#executable-paths) for the precedence chain.

### `lume-ace3p` is using the wrong ACE3P/Cubit/MPI binary — how do I override it?

Add a `paths` mapping under `workflow_parameters`. YAML overrides take
precedence over environment variables, site defaults, and autodetection:

```yaml
workflow_parameters :
  'paths' :
    'ace3p' : '/my/custom/ace3p/bin/'
    'cubit' : '/my/custom/cubit/'
    'mpi'   : 'srun'
```

This pins a specific build for one workflow file without changing your shell
environment.

### Why does `lume-ace3p` fail to find the mesh file generated from Cubit?

Check that the `.gen` filename in the Cubit journal `export` command matches
the `.ncdf` filename in the Omega3P input file. For example, if the journal
has `export genesis "my_mesh.gen"`, the Omega3P input file should contain
`File: ./my_mesh.ncdf` in the `ModelInfo` block.

### Why does `lume-ace3p` fail during Omega3P?

Check that the mesh file is correct and that enough resources are allocated
for the problem size (no out-of-memory errors). If the mesh is unexpectedly
large, check the Cubit journal for errors, particularly in the meshing routine.

Also check the Omega3P input file for typos in the key-value containers and
for sideset/ID inconsistencies with the Cubit journal `export`.

### My `ace3p:` override went into the wrong solver

A shared `input_parameters: ace3p:` container reaches every ACE3P solver in the
workflow. With `omega3p` and `t3p` in one chain, an `EigenSolver` block meant for
Omega3P is also appended to the `.t3p` file. The workflow warns when this happens:

```text
Warning: the shared 'input_parameters: ace3p:' container 'EigenSolver' is not in
the input file of 't3p', so it is appended there as a new block. ...
```

Nest the block under the solver's module name so it reaches that solver only:

```yaml
input_parameters :
  ace3p :
    omega3p :
      'EigenSolver' : { 'NumEigenvalues' : 5 }
```

`cubit:` and `geant4:` blocks scope the same way. See
[](#module-scoped-inputs).

### Two instances of one module fail to build

Two entries of one type both default to the type as their name, so the build
fails with `two modules are named 'omega3p'`. Give each a `name:`. A module that
reads an artifact both instances provide, such as an `acdtool` after two
`omega3p` steps, then fails with `requires artifact 'em_solution', which 2
modules provide`. Name the one it reads with `from:`:

```yaml
workflow :
  - module : omega3p
    name : coarse
    input : 'cav.omega3p'
    results_dir : 'coarse_results'
  - module : omega3p
    name : fine
    input : 'cav.omega3p'
    results_dir : 'fine_results'
  - module : acdtool
    input : 'cav.rfpost'
    from : fine
```

The two solvers also need different `results_dir:` values, and an
`output_parameters` spec that says `module: omega3p` must use the instance name
instead. See [](#several-instances-of-one-module).

### Why does `lume-ace3p` fail for specific parameter values?

Parametric Cubit journals need care. Past certain values a variable can make
the geometry undefined or change its topology, which can renumber the Cubit
vertex/curve/surface/volume IDs and so alter the sideset definitions. Omega3P
and acdtool use sideset IDs to define surfaces; wrongly assigned, the workflow
may crash or produce junk results.

Verify that the journal works at the extremal values of every parameter, e.g.
at both `input_1 = 20` *and* `input_1 = 80` when sweeping `input_1` from 20 to
80 (assuming the deformation is smooth and continuous between them).

A point that fails does not stop the sweep: its row is NaN, the table's
`status` column says `failed`, and stderr names the point, its workdir and the
error. The step's `<module>.log` in that workdir has the tool's own output. See
[](#on-error).

### Can I restart a parameter sweep if the job failed mid-sweep?

Yes. Add `resume: True` to the `mode:` block, set
`workflow_parameters: {workdir_mode: indexed}`, and re-run the same command.
Finished points contribute their rows without launching a solver, the
interrupted point restarts at the step that did not finish, and the rest run
normally. The result table is identical to an uninterrupted run. See
[](#resuming-a-sweep) for the per-point rules; `run-lume-ace3p --status
<config.yaml>` shows what is already done before re-running.

A sweep in which some points failed still writes its table, with NaN rows
for the failed points; resuming re-runs only those. A job killed by the wall
clock writes no table, so resume it rather than combining partial tables: the
resumed run rebuilds the whole table from the per-point workdirs. `resume`
cannot be used with `workdir_mode: manual`.

### A step exited nonzero

The error names the tool, its exit status, the command line and the log, e.g.

```text
ToolExitError: omega3p exited with status 255 (command: srun -n 16 -c 4 omega3p
cavity.omega3p); either the launcher refused the step or the solver crashed —
check lume-ace3p_workdir_3/omega3p.log (...)
```

The log in that workdir holds the tool's own output, and the cause is there. A
step that `srun` refused (for example "More processors requested than
permitted") shows only `srun`'s message. An
exit status of 127 means the binary or a library it links was not found (on
S3DF, an ACE3P binary on an EL9 node; request `--constraint=OS_VER:8.10`).
Any nonzero status is a failure, even if the tool wrote output first, and the
step's previous output is removed before it runs, so a crashed step never
leaves an earlier run's results to be read as its own.
