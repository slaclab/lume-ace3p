# Installation and setup

`lume-ace3p` is a Python package that depends on `lume-base>=0.3.3` and
`xopt>=2.2.2`. On NERSC Perlmutter and SLAC S3DF, pre-made conda environments
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
ACE3P installation reachable through the `ACE3P_PATH` environment variable
(see [](#dry-run-mode) below for testing without ACE3P).
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

1. **Automatic.** If the `ACE3P_PATH` environment variable is unset when the
   workflow runs, dry-run mode is enabled automatically and `lume-ace3p`
   prints:

   ```
   ACE3P environment not configured, enabling dry run mode.
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
