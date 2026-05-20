# Installation and setup

`lume-ace3p` is not a stand-alone PyPI package. It is a set of Python scripts
that depend on `lume-base>=0.3.3` and `xopt>=2.2.2` from conda-forge. The
examples and scripts are configured to run on NERSC Perlmutter or SLAC S3DF in
an appropriate Python environment with those dependencies. Pre-made conda
environments are provided on each supported system.

## Perlmutter (NERSC)

To activate the `lume-ace3p` conda environment on a Perlmutter **login node**:

1. Load the NERSC Conda module (or your own conda manager):

   ```bash
   module load conda
   ```

2. Load the supplied `lume-ace3p` conda environment:

   ```bash
   conda activate /global/cfs/cdirs/ace3p/software/lume-ace3p
   ```

   The text `(lume-ace3p)` should appear at the start of the prompt, indicating
   the correct environment is active. Use `conda deactivate` to exit.

To run the examples on Perlmutter:

1. Copy the `/global/cfs/cdirs/ace3p/lume-ace3p/examples` folder to a desired
   location (e.g. in `$HOME` or scratch).
2. Run the ACE3P setup script with `source perlmutter-ace3p.sh` (required to
   run ACE3P on Perlmutter). The file is located in `/global/cfs/cdirs/ace3p/`.
   This step is optional if your `.bashrc` already has the necessary module
   imports for ACE3P.
3. Set the `PYTHONPATH` environment variable:

   ```bash
   export PYTHONPATH='/global/cfs/cdirs/ace3p/lume-ace3p/'
   ```

   This can also be placed directly in the batch job script. Omitting this
   step may cause Python package conflicts with some NERSC modules.
4. Activate the `lume-ace3p` conda environment (if not already active).
5. Submit a batch job from one of the *Perlmutter* examples with `sbatch`.
6. View results in the folder the job was run from.

## S3DF (SLAC)

To activate the `lume-ace3p` conda environment on an S3DF **iana node**:

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
3. Set `PYTHONPATH`:

   ```bash
   export PYTHONPATH='/sdf/group/rfar/lume-ace3p/'
   ```

   This can also be placed in the batch job script.
4. Activate the `lume-ace3p` conda environment (if not already active).
5. Submit a batch job from one of the *S3DF* examples with `sbatch`.
6. View results in the folder the job was run from.
