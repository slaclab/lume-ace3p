# Setting up workflow input files

A `lume-ace3p` run is a user-composed **`workflow:`**, an ordered list of
modules (`cubit`, `omega3p`/`s3p`/`t3p`, `acdtool`, `track3p_source`,
`particles`, `geant4`, …) validated into a runnable DAG by their artifact
dependencies. The chain is *not* a fixed pipeline. You list only the modules you
want (an Omega3P sweep is `cubit → omega3p → acdtool`; an S3P sweep is
`cubit → s3p`), and each module carries its own input-file references. This page
covers the external input files those modules consume; see [](yaml_reference.md)
for the module list itself. Typical ACE3P input files need only minimal
adjustment for `lume-ace3p`. The main consideration is keeping variable and file
names consistent throughout each input file.

## Cubit journal files

Only the parts of a Cubit journal that interface with `lume-ace3p` are
described here:

- variable name references
- mesh export commands

Variable names and values should generally be near the beginning of the
journal, as APREPRO lines like:

```
#{my_variable_1 = 90}
#{my_variable_2 = 123}
#{my_variable_3 = 0.5}
```

`lume-ace3p` overwrites the numeric quantities after the `=` signs with the
input parameters.

:::{important}
The variable names in the Cubit journal must **exactly** match those used in the
`cubit:` sub-block of `input_parameters` in the `lume-ace3p` YAML.
:::

Since ACE3P can use acdtool to convert Genesis (`.gen`) meshes into NetCDF
(`.ncdf`), the `export` command in the Cubit journal should use the Genesis
option, e.g.:

```
export Genesis "my_mesh_file.gen" block all overwrite
```

This exports the mesh to a `.gen` file. The conversion is a sub-step *inside*
the `cubit` module: after meshing, the module calls acdtool to convert the
`.gen` file to a `.ncdf` file of the same name (`my_mesh_file.ncdf` here). It is
not a separate workflow entry. Set `meshconvert: false` on the `cubit` module
entry to skip conversion, e.g. when the journal already exports a `.ncdf` mesh.
To skip Cubit meshing entirely, drop the `cubit` module and provide the mesh
with a `mesh` source module instead; see [](yaml_reference.md).

For more on Cubit journal files, see the official
[Cubit documentation](https://cubit.sandia.gov/documentation/).

## ACE3P input files

An ACE3P input file is optional; all ACE3P parameters may instead go in the
`lume-ace3p` YAML file (see [](parameter_sweep.md)). ACE3P input files share one
structure across all modules (Omega3P, T3P, S3P, …): key-value containers with
colon (`:`) separators and nested curly braces. The most common container is
`ModelInfo`. For example, an Omega3P input file may contain:

```
ModelInfo : {
  File: ./my_mesh_file.ncdf

  BoundaryCondition : {
    Magnetic: 1, 2
    Exterior: 6
  }

  SurfaceMaterial : {
    ReferenceNumber: 6
    Sigma: 5.8e7
  }
}
```

The boundary condition and surface material numbers correspond to the
`sideset` flags defined in the Cubit journal.

:::{important}
The mesh filename in `File:` must match the name used in the Cubit journal
`export` command, with the `.ncdf` extension (the `.gen` file is converted
automatically).
:::

ACE3P sections allow same-named siblings, e.g. two `Port:` blocks distinguished
by `ReferenceNumber`, or multiple `SurfaceMaterial:` blocks. `lume-ace3p` parses
ACE3P inputs into an ordered tree of name/child pairs, so duplicates are
preserved end-to-end. Matching overrides from the `ace3p:` sub-block of
`input_parameters` are merged positionally back into the file.

Both brace placements are accepted: on the key's line (`ModelInfo : {`, the
usual Omega3P/S3P style) or on its own line below it, as in the T3P tutorial
examples:

```
ModelInfo:
{
  File: ./my_mesh_file.ncdf
}
```

:::{important}
Some ACE3P keys contain spaces, for instance T3P's `Number of sigmas`,
`Curved Surfaces` and `Start contour`. The parser preserves them verbatim; the
solver strips whitespace from keys internally, so `t3p.out` echoes
`Numberofsigmas`. An override key in the `ace3p:` block must be spelled
**exactly as it appears in your input file**, spaces included:

```yaml
input_parameters :
  ace3p :
    'LoadingInfo' :
      'Bunch' :
        'Number of sigmas' : 6
```
:::

When the solver module's `input:` key names an ACE3P input file and the
`ace3p:` overrides do not change or sweep any value inside it, the file is
copied to each working directory verbatim, with no parse / rewrite round-trip.
Parsing happens only when overrides are present, or when no `input:` file is
provided and the entire input is assembled from the YAML.

For more on configuring ACE3P input files, see the
[ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23).

## acdtool postprocess files

An acdtool postprocess script parses ACE3P outputs for quantities such as field
monitors and impedances. Its input is sections of `=`-separated key-value pairs
in curly braces. acdtool reads a `.rfpost` file and writes `rfpost.out`, which
`lume-ace3p` parses into a Python dictionary for output parameters or
optimization.

:::{important}
Make sure the appropriate sections (e.g. `[RoverQ]`) are included with the
`ionoff` flag set to `1`.
:::

A `.rfpost` file is one of acdtool's three input dialects, and `postprocess rf`
one of its nineteen commands. [](acdtool_reference.md) covers the full command
surface, all 24 `.rfpost` blocks with the shape and destination of each one's
output, and the input semantics not guessable from the tutorial files (the
`>1e6` domain-bound sentinels, `gradient = -1`, `modeID2 = -1`).

For more on configuring acdtool input files, see the
[ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23).
