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
user-defined YAML input file. That input file declares a **`workflow:`** — an
ordered list of pipeline **modules** — plus a **`mode:`** describing how to drive
it. The modules are validated into a runnable DAG by their artifact
dependencies, run in order (Cubit meshing, the requested ACE3P solver, acdtool
postprocessing, and/or a Geant4 dose run driven by Track3P particle output), and
the scalars named in `output_parameters` are written to a tab-delimited results
table or handed to Xopt for optimization.

The three layers — **modules** (one adapter per step), the declarative
**workflow** DAG, and the workflow-agnostic **modes** (`single`,
`parameter_sweep`, `scalar_optimize`, `gp_parameter_sweep`) — are described in
`docs/workflow_module_refactor_plan.md`.

To run a parameter sweep or optimization the user typically provides:

- a `lume-ace3p` input file (`.yaml`) with the `workflow:` module list, `mode:`,
  input/output parameters, and any ACE3P settings
- a Cubit journal (`.jou`) file (required for remeshing)
- an acdtool postprocess file (e.g. `.rfpost`) with desired postprocessing settings
  (used for Omega3P)
- a batch script (`.batch`) for submitting the job to HPC resources

ACE3P input parameters can be supplied either as a separate file (`.omega3p`,
`.s3p`, …) named on the solver module, or directly inside the `lume-ace3p` YAML
file via the `ace3p:` sub-block of `input_parameters`.

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
testing
troubleshooting
```

```{toctree}
:hidden:
:maxdepth: 2
:caption: Reference

api/index
```
