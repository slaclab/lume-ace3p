![SLAC](./logos/SLAC-lab-hires.png)

# lume-ace3p

[![Documentation Status](https://readthedocs.org/projects/lume-ace3p/badge/?version=latest)](https://lume-ace3p.readthedocs.io/en/latest/?badge=latest)

`lume-ace3p` is a set of Python interfaces, written by David Bizzozero and
Lila Fowler, for running [ACE3P](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23)
electromagnetic simulation workflows — including [Cubit](https://cubit.sandia.gov/)
mesh generation and acdtool postprocessing — for parameter sweeps and
optimization problems. It is built on top of [lume](https://github.com/slaclab/lume)
by Christopher Mayes and uses [Xopt](https://github.com/xopt-org/Xopt)
by Ryan Roussel for optimization.

The user submits a batch script to HPC nodes which calls `run_lume_ace3p.py`
with a user-defined YAML configuration. The YAML declares a **`workflow:`** — an
ordered list of pipeline **modules** (`cubit`, `omega3p`/`s3p`/`t3p`, `acdtool`,
`track3p_source`, `particles`, `geant4`, and mesh/particle source modules) — plus
a **`mode:`** that says how to drive it (`single`, `parameter_sweep`,
`scalar_optimize`, `gp_parameter_sweep`). The modules are validated into a
runnable DAG by their artifact dependencies, run in order, and the scalars named
in `output_parameters` are pulled out into a tab-delimited results table or
handed to Xopt for optimization. Because the modes are workflow-agnostic, any
chain — an S3P sweep, a Geant4 dose optimization, or a full
`track3p_source → particles → geant4` pipeline — is driven by the same code.

### Architecture

Three cleanly separated layers (see
[`plans/workflow_module_refactor_plan.md`](plans/workflow_module_refactor_plan.md)):

1. **Modules** (`src/lume_ace3p/modules.py`) — one adapter per pipeline step,
   each declaring the artifact kinds it `requires` and `provides`.
2. **Workflow** (`src/lume_ace3p/workflow_graph.py`) — a declarative,
   YAML-defined list of modules validated into an ordered DAG, exposing a single
   black-box `evaluate(input_dict) -> output_dict`.
3. **Modes** (`src/lume_ace3p/modes.py`) — how the workflow is driven; they call
   only `evaluate`/`sweep_axes` and own the outer loop (tensor product, Xopt
   generators, termination). Results flow through one shared writer
   (`src/lume_ace3p/results.py`).

See the [`examples/`](examples/) directory for a YAML per mode and solver family,
and [`docs/testing.md`](docs/testing.md) for how to run the test suite.

## Documentation

Full documentation is hosted on Read the Docs:
**<https://lume-ace3p.readthedocs.io>**

The documentation covers:

- [Installation and setup](https://lume-ace3p.readthedocs.io/en/latest/installation.html) — Perlmutter and S3DF.
- [Workflow input files](https://lume-ace3p.readthedocs.io/en/latest/workflow_inputs.html) — Cubit, ACE3P, and acdtool conventions.
- [Parameter sweeping](https://lume-ace3p.readthedocs.io/en/latest/parameter_sweep.html) — Omega3P and S3P examples.
- [Optimization](https://lume-ace3p.readthedocs.io/en/latest/optimization.html) — Xopt scalar, multifidelity, and Omega3P-via-script.
- [YAML configuration reference](https://lume-ace3p.readthedocs.io/en/latest/yaml_reference.html) — every `*_parameters` block.
- [acdtool reference](https://lume-ace3p.readthedocs.io/en/latest/acdtool_reference.html) — its 19 commands and 24 `.rfpost` blocks, with what is implemented here.
- [Plotting tools](https://lume-ace3p.readthedocs.io/en/latest/plotting.html).
- [Troubleshooting / FAQs](https://lume-ace3p.readthedocs.io/en/latest/troubleshooting.html).
- [API reference](https://lume-ace3p.readthedocs.io/en/latest/api/index.html) — auto-generated from source on every build.

## Repository layout

- `src/lume_ace3p/` — the Python package (entry point: `run_lume_ace3p.py`).
- `examples/` — runnable Cubit / ACE3P / YAML / batch-script examples.
- `plotting/` — interactive plotting scripts for sweep and optimization output.
- `CHANGELOG.md` — what changed in each release.
- `docs/` — Sphinx documentation source.
- `plans/` — implementation plans for the larger pieces of work, each recording
  what was built, how it deviated from the design, and what it left owed. Kept
  out of `docs/` because they are development history rather than user
  documentation.
- `references/` — external reference material, including the SLAC ACE3P
  command-syntax references for every module and `acdtool`.

## Building the docs locally

```bash
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

Then open `docs/_build/html/index.html`.

## License

Distributed under the BSD-2-Clause License. See [LICENSE](./LICENSE) for
details. The licensing model is an open discussion between the code authors,
SLAC management, and DOE program managers along the funding line for the
project.

## SLAC National Accelerator Laboratory

The SLAC National Accelerator Laboratory is operated by Stanford University
for the US Department of Energy. See the [DOE/Stanford contract](https://legal.slac.stanford.edu/sites/default/files/Conformed%20Prime%20Contract%20DE-AC02-76SF00515%20as%20of%202022.10.01.pdf).
