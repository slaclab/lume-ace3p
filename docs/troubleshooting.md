# Troubleshooting

## FAQs

### Why did `lume-ace3p` enable dry-run mode by itself?

When you see one of these messages on startup —

```
ACE3P environment not configured, enabling dry run mode.
```
```
Geant4 environment not configured, enabling dry run mode.
```

— `lume-ace3p` could not resolve the path to ACE3P (or to the Geant4
application, when the workflow includes a `geant4` module) through any of the
four resolution mechanisms: a `paths` mapping in `workflow_parameters`, the
relevant
environment variable (`ACE3P_PATH`, `GEANT4_APP_PATH` /
`GEANT4_APP_EXE`), a built-in site default (Perlmutter / S3DF), or
autodetection on `PATH`/`$HOME`. The workflow still runs end-to-end in
Python but skips the external solver call and writes a `DRY_RUN.txt`
marker in each working directory. To run a real workflow, set one of
those paths — see [](installation.md#executable-paths) for the full
precedence chain.

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

This is the recommended way to pin a specific build for a given
workflow file without changing your shell environment.

### Why does `lume-ace3p` fail to find the mesh file generated from Cubit?

Check that the `.gen` filename provided in the Cubit journal `export`
command matches the `.ncdf` filename in the Omega3P input file. For
example, if the Cubit journal includes
`export genesis "my_mesh.gen"`, the Omega3P input file should contain
`File: ./my_mesh.ncdf` within the `ModelInfo` block.

### Why does `lume-ace3p` fail during Omega3P?

Check that the mesh file is correct and that appropriate resources are
allocated for the problem size (i.e. no out-of-memory errors). If the
mesh is unexpectedly large, check the Cubit journal for errors,
particularly in the meshing routine.

Also check the Omega3P input file for typos in the key-value containers
or for sideset/ID inconsistencies between the Omega3P input and the
Cubit journal `export`.

### Why does `lume-ace3p` fail for specific parameter values?

Cubit journal files require care when using parametric variables. Some
variables cannot exceed certain quantities or the geometry may become
undefined or topologically change. When topological changes occur, the
Cubit vertex/curve/surface/volume IDs may change and affect sideset ID
definitions. Sideset IDs are used by Omega3P and acdtool to define
surfaces; if these are incorrectly assigned, the workflow may crash or
produce junk results.

Verify that the journal file works as intended at the extremal values
of all given parameters. For example, if sweeping `input_1` from 20 to
80, make sure the journal file works properly when `input_1 = 20` *and*
`input_1 = 80` (assuming the deformation is smooth and continuous
between those values).

### Can I restart a parameter sweep if the job failed mid-sweep?

Yes. Add `resume: True` to the `mode:` block, set
`workflow_parameters: {workdir_mode: indexed}`, and re-run the same command: each
point that already finished contributes its row without launching a solver, the
point that was interrupted restarts at the step that did not finish, and the rest
run normally. The result table comes out identical to an uninterrupted run — see
[](#resuming-a-sweep) for the full per-point rules, and
`run-lume-ace3p --status <config.yaml>` to see what is already done before
re-running.

:::{important}
The result table (`mode.output_file`) is still written once, when the sweep
completes, so a job that failed mid-sweep leaves no table for the rows it
finished. That is what makes resuming the thing to do rather than combining
partial tables: the resumed run rebuilds the *whole* table, earlier rows included,
from the results already in the per-point workdirs.

`resume` must be opted into and cannot be used with `workdir_mode: manual` (every
point shares one directory there, so no point has state of its own). Without it,
adjusting the swept range in `input_parameters` remains the workaround: sweeping
`input_1` from 20 to 80 in steps of 10, if the job fails at `input_1 = 50`, edit
that leaf's range to start at 50 and give the restart a different `output_file` to
combine afterward.
:::
