# lume-ace3p

`lume-ace3p` is a set of Python interfaces, written by David Bizzozero and Lila Fowler,
for running [ACE3P](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23)
electromagnetic simulation workflows — including [Cubit](https://cubit.sandia.gov/)
mesh generation and acdtool postprocessing — for parameter sweeps and optimization
problems. It is built on top of [lume](https://github.com/slaclab/lume) by
Christopher Mayes and uses [Xopt](https://github.com/xopt-org/Xopt) by Ryan Roussel
for optimization.

```{image} _static/lume-ace3p-hierarchy.png
:alt: LUME-ACE3P file hierarchy
:align: center
:width: 60%
```

## What lume-ace3p does

A user submits a batch script to HPC nodes which calls `run_lume_ace3p.py` with a
user-defined YAML input file. That input file defines dictionaries for workflow
settings, input parameters, and output parameters. From there, the entry point
automatically calls Cubit, the requested ACE3P module (Omega3P, S3P, T3P, or
Track3P), and acdtool, parses the output, and writes results to a tab-delimited
file or hands them to Xopt for optimization. A separate Geant4 module is also
supported for downstream dose calculations driven by Track3P particle output.

To run a parameter sweep or optimization the user typically provides:

- a `lume-ace3p` input file (`.yaml`) with the workflow settings, input/output
  parameters, and ACE3P settings
- a Cubit journal (`.jou`) file (required for remeshing)
- an acdtool postprocess file (e.g. `.rfpost`) with desired postprocessing settings
  (used for Omega3P)
- a batch script (`.batch`) for submitting the job to HPC resources

ACE3P input parameters can be supplied either as a separate file (`.omega3p`,
`.s3p`, …) or directly inside the `lume-ace3p` YAML file.

## Where to start

- New here? Read [](installation.md), then walk through a
  [](parameter_sweep.md).
- Configuring inputs? See [](workflow_inputs.md) and the full
  [](yaml_reference.md).
- Running optimization? See [](optimization.md).
- Visualizing output? See [](plotting.md).
- Hit a snag? Check [](troubleshooting.md).
- Looking for class- or function-level docs? Browse the
  auto-generated [API reference](api/index).

```{toctree}
:hidden:
:maxdepth: 2
:caption: User guide

installation
workflow_inputs
parameter_sweep
optimization
yaml_reference
plotting
troubleshooting
```

```{toctree}
:hidden:
:maxdepth: 2
:caption: Reference

api/index
```
