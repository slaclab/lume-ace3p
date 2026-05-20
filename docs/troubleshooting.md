# Troubleshooting

## FAQs

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
