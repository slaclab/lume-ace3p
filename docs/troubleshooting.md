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

### Why does `lume-ace3p` fail for specific parameter values?

Parametric Cubit journals need care. Past certain values a variable can make
the geometry undefined or change its topology, which can renumber the Cubit
vertex/curve/surface/volume IDs and so alter the sideset definitions. Omega3P
and acdtool use sideset IDs to define surfaces; wrongly assigned, the workflow
may crash or produce junk results.

Verify that the journal works at the extremal values of every parameter, e.g.
at both `input_1 = 20` *and* `input_1 = 80` when sweeping `input_1` from 20 to
80 (assuming the deformation is smooth and continuous between them).

### Can I restart a parameter sweep if the job failed mid-sweep?

Yes. Add `resume: True` to the `mode:` block, set
`workflow_parameters: {workdir_mode: indexed}`, and re-run the same command.
Finished points contribute their rows without launching a solver, the
interrupted point restarts at the step that did not finish, and the rest run
normally. The result table is identical to an uninterrupted run. See
[](#resuming-a-sweep) for the per-point rules; `run-lume-ace3p --status
<config.yaml>` shows what is already done before re-running.

:::{important}
The result table (`mode.output_file`) is written once, when the sweep completes,
so a job that failed mid-sweep leaves no table for the rows it finished. Resume
rather than combining partial tables: the resumed run rebuilds the *whole* table,
earlier rows included, from the results already in the per-point workdirs.

`resume` must be opted into and cannot be used with `workdir_mode: manual`, where
every point shares one directory and so has no state of its own. Without it, the
workaround is to adjust the swept range in `input_parameters`: sweeping `input_1`
from 20 to 80 in steps of 10, if the job fails at `input_1 = 50`, edit that
leaf's range to start at 50 and give the restart a different `output_file` to
combine afterward.
:::
