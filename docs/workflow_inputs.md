# Setting up workflow input files

Internally, `lume-ace3p` evaluates ACE3P workflows (e.g. Cubit → Omega3P →
acdtool task chains), each of which requires its own input files. Typical
input files used in ACE3P workflows need only minimal adjustment for use with
`lume-ace3p`. The main consideration is making sure variable and file names
are consistent throughout each input file.

## Cubit journal files

Cubit journal files can be very complex; only the parts that directly interface
with `lume-ace3p` are described here. The important aspects are:

- variable name references
- mesh export commands

Variable names and values should generally be near the beginning of a Cubit
journal file. `lume-ace3p` will read and adjust these values based on input
parameters. For example, a Cubit journal might contain APREPRO lines like:

```
#{my_variable_1 = 90}
#{my_variable_2 = 123}
#{my_variable_3 = 0.5}
```

`lume-ace3p` will overwrite the numeric quantities following the `=` signs in
those lines.

:::{important}
The variable names in the Cubit journal file must **exactly** match those used
in the `lume-ace3p` Python script input dictionary.
:::

Since ACE3P can use acdtool to convert Genesis (`.gen`) meshes into NetCDF
(`.ncdf`), the `export` command in the Cubit journal should use the Genesis
option, e.g.:

```
export Genesis "my_mesh_file.gen" block all overwrite
```

This exports the generated mesh into a `.gen` file, and `lume-ace3p`
automatically calls acdtool to convert it to a `.ncdf` file with the same name
(`my_mesh_file.ncdf` here).

For more information on Cubit journal files, see the official
[Cubit documentation](https://cubit.sandia.gov/documentation/).

## ACE3P input files

Providing an ACE3P input file is optional — users may instead include all
ACE3P parameters within the `lume-ace3p` YAML file (see
[](parameter_sweep.md)). ACE3P input files share a common structure across all
ACE3P modules (Omega3P, T3P, S3P, …). The general format is based on key-value
containers with colon (`:`) separators and nested curly braces. The most common
container is the `ModelInfo` section. For example, an Omega3P input file may
contain:

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
`export` command (with the `.ncdf` extension, since the `.gen` extension is
converted automatically).
:::

ACE3P sections allow same-named siblings (e.g. two `Port:` blocks
distinguished by `ReferenceNumber`, multiple `SurfaceMaterial:` blocks).
`lume-ace3p` parses ACE3P inputs into an ordered tree of name/child
pairs, so duplicates are preserved end-to-end; matching overrides from
`ace3p_input_parameters` are merged positionally back into the file.

When you provide an ACE3P input file via `workflow_parameters.ace3p_input`
and `ace3p_input_parameters` does not override or sweep any value inside
it, the file is copied to each working directory verbatim — no parse /
rewrite round-trip occurs. Parsing only happens when overrides are
present, or when no `ace3p_input` is provided and the entire input must
be assembled from the YAML.

For more information on configuring ACE3P input files, see the
[ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23).

## acdtool postprocess files

An acdtool postprocess script is used to parse ACE3P code outputs for
quantities such as field monitors, impedance calculations, etc. The general
input structure is based on sections whose contents are contained within curly
braces. Section contents are key-value pairs separated by `=` signs. acdtool
reads in a `.rfpost` file and writes results into a `rfpost.out` file.
`lume-ace3p` parses that output into a Python dictionary which can be used for
printing output parameters or for optimization.

:::{important}
Make sure the appropriate sections (e.g. `[RoverQ]`) are included with the
appropriate `ionoff` flag set to `1` for postprocessing.
:::

For more information on configuring acdtool input files, see the
[ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23).
