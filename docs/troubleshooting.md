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
application, for `mode: 'geant4'`) through any of the four resolution
mechanisms: a `paths` mapping in `workflow_parameters`, the relevant
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

Checkpointing is not currently implemented. As a workaround, adjusting
`input_parameters` can achieve similar results. For example, when
sweeping `input_1` from 20 to 80 in steps of 10 (7 evaluations: 20, 30,
40, 50, 60, 70, 80), if the job fails at `input_1 = 50`, edit
`input_parameters` to start at 50; the sweep will restart at 50 and
continue to 80 (4 evaluations: 50, 60, 70, 80).

:::{important}
The parameter-sweep output file is *overwritten* on each workflow
evaluation, so save the incomplete (failed) run output file to a new
filename before the restart so you can later combine it with the
restarted run.
:::
