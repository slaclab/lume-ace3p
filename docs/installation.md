# Installation and setup

`lume-ace3p` is a Python package. It requires Python 3.9 or newer and depends
on `lume-base>=0.3.3`, `xopt>=3.0.0`, `ruamel.yaml`, `numpy`, `pandas`,
`scipy`, `scikit-learn`, and `numpyro`. On NERSC Perlmutter and SLAC S3DF,
pre-made conda environments already contain `lume-ace3p` and its dependencies;
just activate the environment. On any other system, install with pip from a
clone of the repository.

## Generic install (with pip)

Clone the repository and install it into the active Python environment:

```bash
git clone https://github.com/slaclab/lume-ace3p.git
cd lume-ace3p
pip install .
```

For a development install, where source edits are picked up without
reinstalling:

```bash
pip install -e .
```

After installation the `run-lume-ace3p` console script is available and the
package can be imported as `lume_ace3p`.

:::{note}
pip does *not* install ACE3P itself. To run real workflows you need an ACE3P
installation reachable through one of the mechanisms in [](#executable-paths).
See [](#dry-run-mode) for testing without ACE3P.
:::

## Perlmutter (NERSC)

A pre-made conda environment is provided; no `pip install` is required.

To activate it on a Perlmutter **login node**:

1. Load the NERSC Conda module (or your own conda manager):

   ```bash
   module load conda
   ```

2. Activate the supplied `lume-ace3p` environment:

   ```bash
   conda activate /global/cfs/cdirs/ace3p/software/lume-ace3p
   ```

   `(lume-ace3p)` should appear at the start of the prompt. Use
   `conda deactivate` to exit.

To run the examples on Perlmutter:

1. Copy `/global/cfs/cdirs/ace3p/lume-ace3p/examples` to a desired location
   (e.g. `$HOME` or scratch).
2. Run the ACE3P setup script `source perlmutter-ace3p.sh` from
   `/global/cfs/cdirs/ace3p/`. This is required unless your `.bashrc` already
   loads the ACE3P modules.
3. Activate the `lume-ace3p` conda environment (if not already active).
4. Submit a batch job from one of the *Perlmutter* examples with `sbatch`.
5. View results in the folder the job was run from.

## S3DF (SLAC)

A pre-made conda environment is provided; no `pip install` is required.

To activate it on an S3DF **iana node**:

1. **Skip this step if you have your own conda.**
   Run this once to initialize conda for your profile:

   ```bash
   /sdf/group/rfar/software/conda/bin/conda init
   ```

2. Reopen a terminal on S3DF iana and run:

   ```bash
   conda activate lume-ace3p
   ```

   `(lume-ace3p)` should appear at the start of the prompt. With your own
   conda, activate `/sdf/group/rfar/software/conda/envs/lume-ace3p`.

To run the examples from an S3DF iana terminal:

1. Copy `/sdf/group/rfar/lume-ace3p/examples` to a desired location.
2. Run the ACE3P setup script: `source /sdf/group/rfar/ace3p/ace3p.sh`
   (required to run ACE3P on S3DF).
3. Activate the `lume-ace3p` conda environment (if not already active).
   Do this **after** sourcing the ACE3P (or Geant4) setup script. Those scripts
   reset `PATH`, so activating conda first leaves the shell without
   `run-lume-ace3p`.
4. Submit a batch job from one of the *S3DF* examples with `sbatch`.
5. View results in the folder the job was run from.

**Sizing a solver step for a milano node.** The S3DF batch scripts request one
node with `--ntasks-per-node=120`; a milano node exposes 120 usable cores with
no hyperthreading. A solver module's `tasks × cores` must stay **≤ 120**, e.g.
`tasks: 16, cores: 4` or `tasks: 12, cores: 8`. Otherwise `srun` refuses the
step with `More processors requested than permitted` and the run fails on its
first point. The Perlmutter scripts are sized for 256 logical CPUs and use
larger products; do not copy their `tasks`/`cores` onto S3DF unchanged.

(executable-paths)=
## Executable paths

`lume-ace3p` must locate ACE3P, Cubit, the MPI launcher, and (for Geant4
workflows) the Geant4 application. Each path is resolved independently, in
this order of precedence (highest first):

1. **YAML override**: a `paths` mapping in `workflow_parameters`, e.g.

   ```yaml
   workflow_parameters :
     'paths' :
       'ace3p' : '/path/to/ace3p/bin/'
       'cubit' : '/path/to/cubit/'
       'mpi'   : 'srun'
       'geant4_app_path' : '/path/to/geant4-app/'
       'geant4_app_exe'  : 'my_geant4_app'
   ```

2. **Environment variable**, one per tool:

   | Path key          | Environment variable |
   |-------------------|----------------------|
   | `ace3p`           | `ACE3P_PATH`         |
   | `cubit`           | `CUBIT_PATH`         |
   | `mpi`             | `MPI_CALLER`         |
   | `geant4_app_path` | `GEANT4_APP_PATH`    |
   | `geant4_app_exe`  | `GEANT4_APP_EXE`     |

3. **Site default**: built-in defaults on a recognized site (Perlmutter or
   S3DF). Site detection compares the hostname (from `NERSC_HOST`,
   `HOSTNAME`, or `os.uname()`) against the prefixes `'perlmutter'` and
   `'sdf'`.

4. **Autodetect**: for `ace3p` and `cubit`, `lume-ace3p` looks for the
   binary on `PATH` (`omega3p`, `cubit`), then falls back to a recursive
   glob under `$HOME` (`**/ace3p/bin`, `**/Cubit*`). For `mpi`, it falls
   back to `mpirun` on `PATH`. The Geant4 keys are not autodetected and must
   be set explicitly when needed.

If the `ace3p` path (or a Geant4 path) cannot be resolved, the workflow
auto-enables dry-run mode (see below).

(dry-run-mode)=
## Dry-run mode

**Dry-run** mode exercises the full Python pipeline (YAML parsing,
parameter-tensor construction, working-directory layout, output bookkeeping)
**without** invoking Cubit, the ACE3P solver, or acdtool. Use it for:

- developing or debugging a workflow YAML on a laptop with no ACE3P install,
- sanity-checking parameter sweeps before submitting a real HPC job,
- continuous-integration testing.

Two ways to enable it:

1. **Automatic.** If the `ace3p` path cannot be resolved (no YAML
   override, no `ACE3P_PATH`, no site default, and no autodetected
   binary), dry-run mode is enabled for ACE3P workflows and `lume-ace3p`
   prints:

   ```
   ACE3P environment not configured, enabling dry run mode.
   ```

   For Geant4 workflows the trigger is that `geant4_app_path` or
   `geant4_app_exe` is unresolved; `lume-ace3p` then prints:

   ```
   Geant4 environment not configured, enabling dry run mode.
   ```

2. **Explicit.** Add `dry_run: True` to `workflow_parameters`:

   ```yaml
   workflow_parameters :
     'workdir' : 'lume-ace3p_workdir'
     'dry_run' : True
     # ...
   ```

Either way, each workflow evaluation creates its working directory and
writes a `DRY_RUN.txt` marker file noting which steps were skipped. No mesh
generation, solver call, or postprocessing is performed.
