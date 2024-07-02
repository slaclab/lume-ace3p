![logo](./logos/SLAC-lab-hires.png)
# LUME-ACE3P

This repository contains the LUME-ACE3P code interfaces for using ACE3P workflows for the use with LUME and Xopt. The dependencies are "lume-base=0.3.3" and "xopt=2.2.2" from conda-forge (later versions may work). The examples and scripts are configured to run on S3DF in an appropriate python environment with the aformentioned dependencies.

To run the examples on an S3DF iana terminal:
1. Copy the examples folder to a desired location (e.g. in home or scratch) and set that as your cwd
2. Source the "sdf-ace3p.sh" script (required to run ACE3P on S3DF)
3. Use the lume-ace3p python environment (or any python environment with "lume-base=0.3.3" and "xopt=2.2.2")
4. Submit a batch job from one of the examples with "sbatch"
5. View the results in the folder that the batch job was run from

Note: the demo batch job scripts are configured to run using the RFAR group repo on S3DF, and may need to be adjusted.

# SLAC National Accelerator Laboratory
The SLAC National Accelerator Laboratory is operated by Stanford University for the US Departement of Energy.  
[DOE/Stanford Contract](https://legal.slac.stanford.edu/sites/default/files/Conformed%20Prime%20Contract%20DE-AC02-76SF00515%20as%20of%202022.10.01.pdf)

# License

We are beginning with the BSD-2 license but this is an open discussion between code authors, SLAC management, and DOE program managers along the funding line for the project.  
