# Installation and setup

`lume-ace3p` is a Python package that requires Python 3.9 or newer and
depends on `lume-base>=0.3.3`, `xopt>=2.2.2`, `ruamel.yaml`, `numpy`, and
`pandas`. On NERSC Perlmutter and SLAC S3DF, pre-made conda environments
already contain `lume-ace3p` and its dependencies, and no installation step
is needed — just activate the environment. On any other system, install with
pip from a clone of the repository.

## Generic install (with pip)

Clone the repository and install it into the active Python environment:

```bash
git clone https://github.com/slaclab/lume-ace3p.git
cd lume-ace3p
pip install .
```

For a development install (edits to the source are picked up without
reinstalling):

```bash
pip install -e .
```

After installation, the `run-lume-ace3p` console script is available and the
package can be imported as `lume_ace3p`.

:::{note}
ACE3P itself is *not* installed by pip. To run real workflows you need an
ACE3P installation reachable through one of the resolution mechanisms
described in [](#executable-paths) below (or see [](#dry-run-mode) for
testing without ACE3P).
:::

## Perlmutter (NERSC)

A pre-made conda environment is provided — no `pip install` is required.

To activate the environment on a Perlmutter **login node**:

1. Load the NERSC Conda module (or your own conda manager):

   ```bash
   module load conda
   ```

2. Activate the supplied `lume-ace3p` environment:

   ```bash
   conda activate /global/cfs/cdirs/ace3p/software/lume-ace3p
   ```

   The text `(lume-ace3p)` should appear at the start of the prompt. Use
   `conda deactivate` to exit.

To run the examples on Perlmutter:

1. Copy the `/global/cfs/cdirs/ace3p/lume-ace3p/examples` folder to a desired
   location (e.g. in `$HOME` or scratch).
2. Run the ACE3P setup script with `source perlmutter-ace3p.sh` (required to
   run ACE3P on Perlmutter). The file is located in `/global/cfs/cdirs/ace3p/`.
   This step is optional if your `.bashrc` already has the necessary module
   imports for ACE3P.
3. Activate the `lume-ace3p` conda environment (if not already active).
4. Submit a batch job from one of the *Perlmutter* examples with `sbatch`.
5. View results in the folder the job was run from.

## S3DF (SLAC)

A pre-made conda environment is provided — no `pip install` is required.

To activate the environment on an S3DF **iana node**:

1. **Skip this step if you have your own conda.**
   Run this command once (initializes a conda environment for your profile):

   ```bash
   /sdf/group/rfar/software/conda/bin/conda init
   ```

2. Reopen a terminal on S3DF iana and run:

   ```bash
   conda activate lume-ace3p
   ```

   The text `(lume-ace3p)` should appear at the start of the prompt. If using
   your own conda, the environment lives at
   `/sdf/group/rfar/software/conda/envs/lume-ace3p`.

To run the examples from an S3DF iana terminal:

1. Copy the `/sdf/group/rfar/lume-ace3p/examples` folder to a desired location.
2. Run the ACE3P setup script: `source sdf-ace3p.sh` (required to run ACE3P on
   S3DF). The file is located in `/sdf/group/rfar/ace3p/`.
3. Activate the `lume-ace3p` conda environment (if not already active).
4. Submit a batch job from one of the *S3DF* examples with `sbatch`.
5. View results in the folder the job was run from.

(executable-paths)=
## Executable paths

`lume-ace3p` needs to know where to find ACE3P, Cubit, the MPI launcher,
and (for Geant4 workflows) the Geant4 application. Each path is resolved
independently, in the following order of precedence (highest first):

1. **YAML override** — a `paths` mapping in `workflow_parameters`, e.g.

   ```yaml
   workflow_parameters :
     'paths' :
       'ace3p' : '/path/to/ace3p/bin/'
       'cubit' : '/path/to/cubit/'
       'mpi'   : 'srun'
       'geant4_app_path' : '/path/to/geant4-app/'
       'geant4_app_exe'  : 'my_geant4_app'
   ```

2. **Environment variable** — one per tool:

   | Path key          | Environment variable |
   |-------------------|----------------------|
   | `ace3p`           | `ACE3P_PATH`         |
   | `cubit`           | `CUBIT_PATH`         |
   | `mpi`             | `MPI_CALLER`         |
   | `geant4_app_path` | `GEANT4_APP_PATH`    |
   | `geant4_app_exe`  | `GEANT4_APP_EXE`     |

3. **Site default** — when running on a recognized site (Perlmutter or
   S3DF), built-in defaults are used. Site detection compares the
   hostname (via `NERSC_HOST`, `HOSTNAME`, or `os.uname()`) against the
   prefixes `'perlmutter'` and `'sdf'`.

4. **Autodetect** — for `ace3p` and `cubit`, `lume-ace3p` looks for the
   relevant binary on `PATH` (e.g. `omega3p`, `cubit`) and falls back to
   a recursive glob under `$HOME` (`**/ace3p/bin`, `**/Cubit*`). For
   `mpi`, it falls back to `mpirun` on `PATH`. The Geant4 keys are not
   autodetected — they must be set explicitly when needed.

If a path cannot be resolved, the corresponding tool is unavailable and
the workflow auto-enables dry-run mode (see below).

(dry-run-mode)=
## Dry-run mode

`lume-ace3p` ships with a **dry-run** mode that exercises the full Python
pipeline — YAML parsing, parameter-tensor construction, working-directory
layout, output bookkeeping — **without** invoking Cubit, the ACE3P solver,
or acdtool. This is useful for:

- developing or debugging a workflow YAML on a laptop with no ACE3P install,
- sanity-checking parameter sweeps before submitting a real HPC job,
- continuous-integration testing.

There are two ways to enable it:

1. **Automatic.** If the `ace3p` path cannot be resolved (no YAML
   override, no `ACE3P_PATH`, no site default, and no autodetected
   binary), dry-run mode is enabled automatically for ACE3P workflows
   and `lume-ace3p` prints:

   ```
   ACE3P environment not configured, enabling dry run mode.
   ```

   For Geant4 workflows, the analogous trigger is that *both*
   `geant4_app_path` and `geant4_app_exe` are unresolved, in which case
   `lume-ace3p` prints:

   ```
   Geant4 environment not configured, enabling dry run mode.
   ```

2. **Explicit.** Add `dry_run: True` to `workflow_parameters` in the YAML
   file:

   ```yaml
   workflow_parameters :
     'mode' : 'parameter_sweep'
     'module' : 'omega3p'
     'dry_run' : True
     # ...
   ```

In either case, each workflow evaluation creates its working directory and
writes a `DRY_RUN.txt` marker file noting which steps were skipped. No mesh
generation, solver call, or postprocessing is performed.
