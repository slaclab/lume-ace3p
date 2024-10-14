![logo](./logos/SLAC-lab-hires.png)
# LUME-ACE3P

LUME-ACE3P is a set of python code interfaces for running ACE3P workflows (including Cubit and postprocessing routines) with the intent of running parameter sweeps or optimization problems. The base structure of LUME-ACE3P is built on [lume](https://github.com/slaclab/lume), written by Cristopher Mayes, and the optimization routines use [Xopt](https://github.com/xopt/xopt), written by Ryan Roussel.

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
The LUME-ACE3P python scripts enable the use of parameter sweeping and parameter optimization of ACE3P-workflows including Cubit mesh generation and acdtool postprocessing. To perform a simple parameter sweep a user will need to provide the following:

- a Cubit journal (.jou) file for editing (required for remeshing)
- an ACE3P input file (e.g. .omega3p) with desired input settings
- an acdtool postprocess file (e.g. .rfpost) with desired postprocessing settings
- a LUME-ACE3P python script (.py) containing the ACE3P workflow and parameter sweeping/optimization settings
- a batch script (.batch) for submitting a job to the appropriate HPC resources

The basic idea is that a user submits the batch script to HPC nodes which contains the LUME-ACE3P python script. The LUME-ACE3P python script contains 2 main parts: an ACE3P workflow function definition, and the parameter sweep/optimization loop. The parameter sweep/optimization loop calls the ACE3P workflow function and uses the appropriate input files with the corresponding codes (e.g. Cubit, Omega3P, etc.) and parses the output for writing to a text file or for use with optimization.

The Cubit journal file, ACE3P input file, and acdtool postprocess files are generally unaltered from normal ACE3P usage. The details on the LUME-ACE3P python script are discussed in detail in the [python scripts](#Setting-up-LUME-ACE3P-python-files) section.
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
This would be parsed with LUME-ACE3P with a cubit object (see cubit_obj parameters for more details) which would overwrite the numeric quantities following the "=" signs in those lines. **Special care must be taken to ensure the variable names used in the Cubit journal file exactly match those used in the LUME-ACE3P python script workflow inputs!**

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
The boundary condition and surface material numbers correspond to the "sideset" flags defined in a Cubit journal. **The filename of the mesh must match the name used in the corresponding Cubit journal file "export" command (with the .ncdf extension since the .gen extension gets converted automatically)!** Additionally, ACE3P input file parameters can be adjusted directly with LUME-ACE3P with an ACE3P object (see omega3p_obj parameters for more details).

For more information on configuring ACE3P input files, see the [ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23).

</details>

<details><summary>Acdtool Postprocess Files</summary>
</details>

# Setting up LUME-ACE3P python scripts

<details><summary>Parameter Sweep Example</summary>

A LUME-ACE3P python script primarily consists of two sections: a workflow "function" section which contains the start-to-end steps for evaluating a chain of tasks (e.g. Cubit -> Omega3P -> acdtool), and a parameter sweep section which contains how the inputs and outputs of the workflow function are managed/written to files. In this section, each part of the example "lume-ace3p_psweep_demo.py" is explained in detail.

The script begins with the neccessary LUME-ACE3P imports.
```python
import os
from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P
from lume_ace3p.acdtool import Acdtool
from lume_ace3p.tools import WriteDataTable

#Define parameters to sweep in lists
input1 = [90 + 10*i for i in range(4)]      #Cavity radii in mm (units in cubit journal)
input2 = [0.5 + 0.25*i for i in range(4)]   #Cavity ellipticity parameter
```
This part makes lists for the user-defined parameters to sweep. Any number of parameter inputs (with arbitrary names) can be defined here and are simply python lists of numeric values. If more nuanced parameterization is needed, see the parameter sweep section with for loops.

```python
#Define base working directory for all simulations (each will get its own folder)
my_base_dir = os.path.join(os.getcwd(),'lume-ace3p_demo_workdir')
```
This sets a user-defined folder prefix for all the workflow runs in the parameter sweep. In this example, each parameter run will create a folder named "lume-ace3p_demo_workdir_X_Y" where "X" and "Y" will be replaced by parameter values of input1 and input2. The base prefix is defined here.

```python
#Define the function workflow to evaluate
def workflow_function(input_dict):

    #Load working directory from base name + parameters
    sim_dir = input_dict['workflow_dir']

    #Create cubit object, parse input file, update values, and then run cubit
    cubit_obj = Cubit('pillbox-rtop.jou',workdir=sim_dir)
    cubit_obj.set_value(input_dict) #Update any values in journal file from input
    cubit_obj.run()
    
    #Create omega3p object, parse input file, and run omega3p
    omega3p_obj = Omega3P('pillbox-rtop.omega3p',workdir=sim_dir)
    omega3p_obj.run()
    
    #Create acdtool object, parse input file, and run acdtool
    acdtool_obj = Acdtool('pillbox-rtop.rfpost',workdir=sim_dir)
    acdtool_obj.run()   #Defaults to 'postprocess rf' command if .rfpost file given
    
    #Create output dict containing desired quantities
    output_dict = {"RoQ": acdtool_obj.output_data['RoverQ']['0']['RoQ'],
                   "Frequency": acdtool_obj.output_data['RoverQ']['0']['Frequency']}
    
    return output_dict
```
This is the workflow function definition for LUME-ACEP and is the main part of how the steps are joined together. The function is set up with python dictionary inputs and outputs.

The python input dictionary will have the form ```{'var_name1': var_value1, 'var_name2': var_value2, ...}``` which will be passed into the necessary modules (e.g. Cubit) to update values.

- The ```sim_dir``` value will be updated for each parameter run. If each parameter run doesn't need to be saved, the ```sim_dir``` variable can be any folder path name (which will be created/overwritten).
- The ```cubit_obj``` object is created from a user-provided Cubit journal file. The values in the journal file are updated by any changes to the variables defined in the input dictionary followed by running Cubit in ```--nographics``` mode to generate the mesh (it will automatically be converted to a .netcdf format for ACE3P).
- The ```omega3p_obj``` object is created from a user-provided Omega3P input file and then run with Omega3P. Since no values are changed here, the .omega3p script is run as-is.
- The ```acdtool_obj``` object is created from a user-provided acdtool rfpost input file and then run with acdtool. Since no values are changed here, the .rfpost script is run as-is.

Lastly, the ```output_dict``` dictionary is created which returns user-specified quantities from the postprocessing outputs of acdtool. The structure of `the acdtool_obj.output_data` is a nested set of dictionaries corresponding to a parsed output of the `rfpost.out` file generated from acdtool. In this example, the first layer is `['RoverQ']` which corresponds to the "RoverQ" section defined in `pillbox-rtop.rfpost`. The second layer `['0']` corresponds to the mode ID number "0" within the "RoverQ" printout in the .rfpost file. The third layer `['Frequency']` corresponds to the data column "Frequency" of the corresponding mode ID. See the object options section for more details.

<details><summary>Example rfpost.out text</summary>
Within the rfpost.out text file, the "RoverQ" output has the form:

```
[RoverQ]
{  // RoverQ=V^2/(omega*U)
   Integral:  x1  = 0.0000e+00,  y1  = 1.0000e-03,  z1  =-1.5000e-01
              x2  = 0.0000e+00,  y2  = 1.0000e-03,  z2  = 1.5000e-01
 ModeID   Frequency       Qext              V_r, V_i              |V|          RoQ(ohm/cavity)
    0   1.4088933e+09  0.00000e+00  -1.1598e+00, -3.9855e+00    4.15088e+00      1.09912e+02
    1   2.3462886e+09  0.00000e+00   2.8356e+00, -1.4684e+00    3.19326e+00      3.90596e+01
}
```

This output would be parsed by LUME-ACE3P in the example as ```output_dict = {"RoQ": 1.09912e+02, "Frequency": 1.4088933e+09}```
</details> 

```python
#Sweep through all parameter combinations (single or multiple for-loops)
sim_output = {} #Output dict to store results
for i in range(len(input1)):
    for j in range(len(input2)):
        #Create input dict for sim function
        #Note: desired cubit input names must match those in Cubit journal!
        inputs = {'cav_radius': input1[i],
                  'cav_ellipticity': input2[j],
                  'workflow_dir': my_base_dir + '_' + str(input1[i]) + '_' + str(input2[j])}
        
        #Call sim function for set of inputs
        sim_output[(input1[i],input2[j])] = workflow_function(inputs)

        #Write data to text file (this will overwrite the file as the sim_output dict grows)
        #(or put WriteDataTable outside of for loop to only write data at end simulations)
        #See src/tools.py for more information on the WriteDataTable function
        WriteDataTable('psweep_output.txt', sim_output, ['Radius','Ellipticity'], ['RoQ','Frequency'])
```
The last part of the LUME-ACE3P python script contains the parameter sweeping for-loop. In the given example, all pairs of values of (input1,input2) are swept over for a total of 16 evaluations (since input1 and input2 were lists of 4 values each).

To set-up the inputs for the workflow function, a dictionary ```inputs``` is created with the keywords corresponding to the variable names in the Cubit journal file. **The names of the keywords in this inputs dictionary must exactly match the variable names defined in the Cubit journal!** The "workflow_dir" keyword is used to concatenate the input1 and input2 pair of values to the workflow folder name with the base foldername "my_base_dir" defined before.

The workflow function is then called with the generated input dictionary. Thus the "sim_output" dictionary uses the (input1,input2) tuples as *keys* with the corresponding workflow outputs "output_dict" as *values* of those keys!

The ```WriteDataTable``` routine will unpack the "sim_output" nested-dictionary into a tab-delimited text file named "psweep_output.txt". In this example, input1 corresponds to the variable name "Radius" and input2 corresponds to the variable name "Ellipticity" (these are aribtrary names and only used in writing the column names in the text file). However, the outputs "RoQ" and "Frequency" correspond to the **exact** output name used in the "output_dict" of the workflow function.

</details>

<details><summary>Optimization Example</summary>
To be implemented!
</details>

# SLAC National Accelerator Laboratory
The SLAC National Accelerator Laboratory is operated by Stanford University for the US Departement of Energy.  
[DOE/Stanford Contract](https://legal.slac.stanford.edu/sites/default/files/Conformed%20Prime%20Contract%20DE-AC02-76SF00515%20as%20of%202022.10.01.pdf)

# License

We are beginning with the BSD-2 license but this is an open discussion between code authors, SLAC management, and DOE program managers along the funding line for the project.  
