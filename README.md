![logo](./logos/SLAC-lab-hires.png)
# LUME-ACE3P

LUME-ACE3P is a set of python code interfaces for running ACE3P workflows (including Cubit and postprocessing routines) with the intent of running parameter sweeps or optimization problems. The base structure of LUME-ACE3P is built on [lume](https://github.com/slaclab/lume), written by Cristopher Mayes, and the optimization routines use [Xopt](https://github.com/xopt-org/Xopt), written by Ryan Roussel.

# LUME-ACE3P Dependencies and Installation

LUME-ACE3P is not configured as a stand-alone Python module. Instead, it is a set of Python scripts dependent on "lume-base>=0.3.3" and "xopt>=2.2.2" from conda-forge. The examples and scripts are configured to run on NERSC Perlmutter or SLAC S3DF in an appropriate python environment with the aformentioned dependencies. See the following for details on how to access pre-made conda environments for LUME-ACE3P on the supported systems.

<details><summary>Perlmutter</summary>
   
To activate the lume-ace3p conda environment on a Perlmutter login node:
1. Run the command: ```/global/cfs/cdirs/ace3p/software/miniconda3/condabin/conda init``` to set up conda for your terminal (only needs to be done once)
2. Reopen a terminal on Perlmutter and run the command: ```conda activate lume-ace3p```
   - The text "(lume-ace3p)" should be shown on the command line indicating you are in the correct conda environment
   - The command ```conda deactivate``` can be used to exit the conda environment if desired

To run the examples on Perlmutter:
1. Copy the ```/global/cfs/cdirs/ace3p/lume-ace3p/examples``` folder to a desired location (e.g. in home or scratch)
2. Run the ace3p setup script with ```source perlmutter-ace3p.sh``` (required to run ACE3P on Perlmutter)
   - The `perlmutter-ace3p.sh` file is located in ```/global/cfs/cdirs/ace3p/```
   - This step is optional if your `.bashrc` file already has the necessary module imports for ACE3P
3. Set the environment variable `PYTHONPATH` to ```/global/cfs/cdirs/ace3p/lume-ace3p/```
   - Use the command ```export PYTHONPATH='/global/cfs/cdirs/ace3p/lume-ace3p/'``` which can be put in your `.bashrc` file.
   - Omitting this step may cause conda package conflicts with NERSC's built-in conda module
4. Activate the lume-ace3p conda environment with ```conda activate lume-ace3p``` if not already active
5. Submit a batch job of one of the *Perlmutter* examples with ```sbatch```
6. View the results in the folder that the batch job was run from
</details>

</details>

<details><summary>S3DF</summary>

To activate the lume-ace3p conda environment on an S3DF iana terminal:
1. Run the command: ```/sdf/group/rfar/software/conda/bin/conda init``` to set up conda for your terminal (only needs to be done once)
2. Reopen a terminal on S3DF iana and run the command: ```conda activate lume-ace3p```
   - The text "(lume-ace3p)" should be shown on the command line indicating you are in the correct conda environment
   - The command ```conda deactivate``` can be used to exit the conda environment if desired

To run the examples on an S3DF iana terminal:
1. Copy the ```/sdf/group/rfar/lume-ace3p/examples``` folder to a desired location (e.g. in home or scratch)
2. Run the ace3p setup script with ```source sdf-ace3p.sh``` (required to run ACE3P on S3DF)
   - The `sdf-ace3p.sh` file is located in ```/sdf/group/rfar/ace3p/```
3. Set the environment variable `PYTHONPATH` to ```/sdf/group/rfar/lume-ace3p/```
   - Use the command ```export PYTHONPATH='/sdf/grou/rfar/lume-ace3p/'``` which can be put in your `.bashrc` file.
4. Activate the lume-ace3p conda environment with ```conda activate lume-ace3p``` if not already active
5. Submit a batch job of one of the *S3DF* examples with ```sbatch```
6. View the results in the folder that the batch job was run from
</details>

# How to use LUME-ACE3P

<details><summary>Instructions</summary>
   
The LUME-ACE3P python scripts enable the use of parameter sweeping or optimization of ACE3P-workflows including Cubit mesh generation and acdtool postprocessing. To perform a parameter sweep or optimization run, a user will need to provide the following:

> - a Cubit journal (.jou) file for editing (required for remeshing)
> - an ACE3P input file (e.g. .omega3p) with desired input settings
> - an acdtool postprocess file (e.g. .rfpost) with desired postprocessing settings
> - a LUME-ACE3P python script (.py) containing the workflow settings and input/output parameters
> - a batch script (.batch) for submitting a job to the appropriate HPC resources

<img src="LUME-ACE3P File Hierarchy.png" width=800>

The basic idea is that a user submits the batch script to HPC nodes which contains the LUME-ACE3P python script. The LUME-ACE3P python script contains dictionary objects for the workflow settings, input parameters, and output parameters. A parameter sweep can be run by calling the ACE3P workflow function with the appropriate input/output parameters; this workflow will automatically call other codes (e.g. Cubit, Omega3P, etc.) and parse the output for writing to a text file or for use with optimization.

The Cubit journal file, ACE3P input file, and acdtool postprocess files are generally unaltered from normal ACE3P usage. The details on the LUME-ACE3P python script are discussed in detail in the [python scripts](#Setting-up-LUME-ACE3P-python-scripts) section.
</details>

# Setting up Cubit/ACE3P/Acdtool input files

<details><summary>Cubit Journal Files</summary>

Cubit journal files can be very complex, thus only the parts which directly interface with LUME-ACE3P will be discussed here. The important aspects to note in a Cubit file when using LUME-ACE3P are:
- Variable name references
- Mesh export commands

Variable names and values should generally be near the beginning of a Cubit journal file. LUME-ACE3P will read and adjust these values based on given parameter inputs. For example, a Cubit journal might contain APREPRO lines like:
```
#{my_variable_1 = 90}
#{my_variable_2 = 123}
#{my_variable_3 = 0.5}
```
This would be parsed with LUME-ACE3P which would overwrite the numeric quantities following the "=" signs in those lines. **Special care must be taken to ensure the variable names used in the Cubit journal file exactly match those used in the LUME-ACE3P python script workflow inputs!**

Since ACE3P can use acdtool to convert Genesis (.gen) formatted meshes into NetCDF (.ncdf), the "export" command in the Cubit journal should use the Genesis option. For example, a Cubit journal might contain the export command:
```
export Genesis "my_mesh_file.gen" block all overwrite
```
This will export the generated mesh into a .gen file and LUME-ACE3P will automatically call acdtool to convert it further into a .ncdf file with the same name ("my_mesh_file.ncdf" in this case).

For more information on Cubit journal files, see the official [Cubit documentation](https://cubit.sandia.gov/documentation/). 

</details>

<details><summary>ACE3P Input Files</summary>

ACE3P input files share the same structure format for all ACE3P modules (e.g. Omega3P, T3P, S3P, etc.). The general input structure is based on key-value containers with colon ":" separators and nested curly braces "{}". Many options are available in ACE3P however the most common container is the "ModelInfo" section. For example, an Omega3P input file may contain:
```
ModelInfo : {
  File: ./my_mesh_file.ncdf

  BoundaryCondition : {
    Magnetic: 1, 2
    Exterior: 6
  }

  SurfaceMaterial : {
    ReferenceNumber: 6
    Sigma: 5.8e7
  }
}
```
The boundary condition and surface material numbers correspond to the "sideset" flags defined in a Cubit journal. **The filename of the mesh must match the name used in the corresponding Cubit journal file "export" command (with the .ncdf extension since the .gen extension gets converted automatically)!**

For more information on configuring ACE3P input files, see the [ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23).

</details>

<details><summary>Acdtool Postprocess Files</summary>

An Acdtool postprocess script is used to parse ACE3P code outputs for quantities such as field monitors, impedance calculations, etc. The general input structure is based on sections whose contents are contained within curly braces "{}"; the contents are section-specific and key-value pairs separated by equals signs "=". Acdtool will read-in a ".rfpost" file and provide the results in a "rfpost.out" file. LUME-ACE3P is configured to parse this output file into a python dictionary object (see [acdtool python objects](#LUME-ACE3P-Python-object-structures-advanced-users) for more details).

For more information on configuring Acdtool input files, see the [ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23).

</details>

# Setting up LUME-ACE3P python scripts

<details><summary>Omega3P Parameter Sweep Example</summary>

A LUME-ACE3P python script for parameter sweeping primarily consists of definining 3 Python *dictionaries* ([dict objects](https://docs.python.org/3/tutorial/datastructures.html#dictionaries)): a workflow dict which contains various settings (e.g. paths to other code input files), an input dict which contains parameter name and values to be scanned through, and an output dict (optional) which sets which outputs to store after each parameter run. In this section, each of these dict objects of the example "lume-ace3p_simple_psweep.py" is explained in detail.

The script begins with the neccessary LUME-ACE3P imports and workflow dict definition:
```python
import os
import numpy as np
from lume_ace3p.workflow import Omega3PWorkflow

workflow_dict = {'cubit_input': 'pillbox-rtop.jou',
                 'omega3p_input': 'pillbox-rtop.omega3p',
                 'omega3p_tasks': 4,
                 'omega3p_cores': 4,
                 'omega3p_opts' : '--cpu-bind=cores',
                 'rfpost_input': 'pillbox-rtop.rfpost',
                 'workdir': os.path.join(os.getcwd(),'lume-ace3p_demo_workdir'),
                 'workdir_mode': 'auto',
                 'sweep_output_file': 'psweep_output.txt',
                 'sweep_output': True,
                 'autorun': False}
```
This workflow dict object contains various parameters such as input files (path is assumed to be in same directory), working directory settings, and HPC specific commands for ACE3P codes. Specifically for this example, the options are configured for running workflows in separate sub-diectories (automatically named using input values) with the `pillbox-rtop.jou`, `pillbox-rtop.omega3p`, and `pillbox-rtop.rfpost` files for Cubit, Omega3P, and Acdtool respectively. Additionally, Omega3P is configured to use 4 MPI tasks with 4 cores/task with the CPU thread-binding option to cores. The `sweep_output` keyword simply enables file output writing and the `autorun` keyword is set so `False` so the workflow can be used for a parameter sweep. See the [workflow dict](#LUME-ACE3P-Python-dict-structures-advanced-users) section for more details on each option.

Next, the input parameters are defined in a separate dict object:
```python
input_dict = {'cav_radius': np.linspace(90,120,4),
              'ellipticity': np.linspace(0.5,1.25,4)}
```
The input dict object contains keyword value pairs for the *exact* names of the variables (as defined in the Cubit journal file) and the corresponding values to sweep. Each parameter value can be a numpy vector array (e.g. numpy.linspace() or a list of numeric types. The parameter-sweep in LUME-ACE3P will evaluate the workflow for all possible tensor products of the input variable arrays. In this example, the `cav_radius` variable and the `ellipticity` variable are each vectors of length 4, thus the total number of workflow evaluations is 16 (4 x 4). Also, since the `workdir_mode` setting in the workflow dict was set to `auto`, each workflow evaluation will create a folder named "lume-ace3p_demo_workdir_X_Y" where "X" and "Y" will be replaced by numeric values of each `cav_radius` and `ellipticity` for a total of 16 different folders. See the [input dict](#LUME-ACE3P-Python-dict-structures-advanced-users) section for more details on using multiple parameters.

Next, the desired outputs are defined in a separate dict object:
```python
output_dict = {'R/Q': ['RoverQ', '0', 'RoQ'],
               'Mode_frequency': ['RoverQ', '0', 'Frequency'],
               'E_field_max': ['maxFieldsOnSurface', '6', 'Emax']}
```
The output dict object contains keyword value pairs for desired outputs to write to `sweep_output_file` in a tab-delimited text file. This file will contain one column for each input or output and rows corresponding to workflow evaluations. The format for the keyword values is a list object corresponding to the section id (e.g. 'RoverQ'), mode/surface id string (e.g. '0'), and entry name (e.g. 'RoQ') extracted from within the acdtool postprocess output file (named rfpost.out). In this example, the first row of the output file will contain 5 text entries: 'cav_radius', 'ellipticity', 'R/Q', 'Mode_frequency', and 'E_field_max'. Then in subsequent rows, the columns will be filled with the corresponding input value (for 'cav_radius' and 'ellipticity') or output quantity (extracted from the rfpost.out file for each workflow evaluation). See the [output dict](#LUME-ACE3P-Python-dict-structures-advanced-users) section for more details on different options to extract from rfpost.out files.

If no output dict is specified, the parameter sweep can still be run, but rfpost.out file data will not be parsed or tabulated (useful if only the different output folders are desired for each parameter combination).

Lastly, the workflow object is instantiated with the 3 defined dict objects and the parameter sweep can begin.
```python
workflow = Omega3PWorkflow(workflow_dict, input_dict, output_dict)
workflow.run_sweep()
```
LUME-ACE3P will internally sweep through the combinations of input parameters provided and write the desired outputs to the 'sweep_output_file' provided. As of now, LUME-ACE3P does not support checkpointing and each workflow evaluation is run serially (future vesion may allow multiple concurrent evaluations).

</details>

<details><summary>Optimization Example</summary>
To be implemented!
</details>

# LUME-ACE3P Python dict structures (advanced users)

<details><summary>workflow dict</summary>
To be implemented!
</details>

<details><summary>input dict</summary>
To be implemented!
</details>

<details><summary>output dict</summary>
To be implemented!
</details>

# SLAC National Accelerator Laboratory
The SLAC National Accelerator Laboratory is operated by Stanford University for the US Departement of Energy.  
[DOE/Stanford Contract](https://legal.slac.stanford.edu/sites/default/files/Conformed%20Prime%20Contract%20DE-AC02-76SF00515%20as%20of%202022.10.01.pdf)

# License

We are beginning with the BSD-2 license but this is an open discussion between code authors, SLAC management, and DOE program managers along the funding line for the project.  
