![logo](./logos/SLAC-lab-hires.png)
# LUME-ACE3P

This repository contains the LUME-ACE3P code interfaces for using ACE3P workflows for the use with LUME and Xopt. The dependencies are "lume-base=0.3.3" and "xopt=2.2.2" from conda-forge (later versions may work). The examples and scripts are configured to run on S3DF in an appropriate python environment with the aformentioned dependencies.

To activate the lume-ace3p conda environment on an S3DF iana terminal:
1. Run the command "/sdf/group/rfar/software/conda/bin/conda init" to set up conda for your terminal (only needs to be done once)
2. Reopen a terminal on S3DF iana and run the command: "conda activate lume-ace3p"
   - The text "(lume-ace3p)" should be shown on the command line indicating you are in the correct conda environment

To run the examples on an S3DF iana terminal:
1. Copy the examples folder to a desired location (e.g. in home or scratch) and set that as your cwd
2. Run the ace3p setup script with "source sdf-ace3p.sh" (required to run ACE3P on S3DF)
   - The sdf-ace3p.sh file is located in /sdf/group/rfar/ace3p/
3. Activate the lume-ace3p conda environment with "conda activate lume-ace3p" if not already active
4. Submit a batch job from one of the examples with "sbatch"
5. View the results in the folder that the batch job was run from

Note: the demo batch job scripts are configured to run using the RFAR group repo on S3DF, and may need to be adjusted.

# SLAC National Accelerator Laboratory
The SLAC National Accelerator Laboratory is operated by Stanford University for the US Departement of Energy.  
[DOE/Stanford Contract](https://legal.slac.stanford.edu/sites/default/files/Conformed%20Prime%20Contract%20DE-AC02-76SF00515%20as%20of%202022.10.01.pdf)

# License

We are beginning with the BSD-2 license but this is an open discussion between code authors, SLAC management, and DOE program managers along the funding line for the project.  
