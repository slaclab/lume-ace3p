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
with a user-defined YAML configuration. The entry point automatically calls
Cubit, the requested ACE3P module (Omega3P, S3P, …), and acdtool, parses the
output, and writes results to a tab-delimited file or hands them to Xopt for
optimization.

## Documentation

Full documentation is hosted on Read the Docs:
**<https://lume-ace3p.readthedocs.io>**

The documentation covers:

- [Installation and setup](https://lume-ace3p.readthedocs.io/en/latest/installation.html) — Perlmutter and S3DF.
- [Workflow input files](https://lume-ace3p.readthedocs.io/en/latest/workflow_inputs.html) — Cubit, ACE3P, and acdtool conventions.
- [Parameter sweeping](https://lume-ace3p.readthedocs.io/en/latest/parameter_sweep.html) — Omega3P and S3P examples.
- [Optimization](https://lume-ace3p.readthedocs.io/en/latest/optimization.html) — Xopt scalar, multifidelity, and Omega3P-via-script.
- [YAML configuration reference](https://lume-ace3p.readthedocs.io/en/latest/yaml_reference.html) — every `*_parameters` block.
- [Plotting tools](https://lume-ace3p.readthedocs.io/en/latest/plotting.html).
- [Troubleshooting / FAQs](https://lume-ace3p.readthedocs.io/en/latest/troubleshooting.html).
- [API reference](https://lume-ace3p.readthedocs.io/en/latest/api/index.html) — auto-generated from source on every build.

## Repository layout

- `src/lume_ace3p/` — the Python package (entry point: `run_lume_ace3p.py`).
- `examples/` — runnable Cubit / ACE3P / YAML / batch-script examples.
- `plotting/` — interactive plotting scripts for sweep and optimization output.
- `docs/` — Sphinx documentation source.
- `references/` — external reference material.

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
