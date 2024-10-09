![logo](./logos/SLAC-lab-hires.png)
# LUME-ACE3P

This repository contains the LUME-ACE3P code interfaces for using ACE3P workflows for the use with LUME and Xopt. The dependencies are "lume-base=0.3.3" and "xopt=2.2.2" from conda-forge (later versions may work). The examples and scripts are configured to run on S3DF in an appropriate python environment with the aformentioned dependencies.

To activate the lume-ace3p conda environment on an S3DF iana terminal:
1. Run the command: ```/sdf/group/rfar/software/conda/bin/conda init``` to set up conda for your terminal (only needs to be done once)
2. Reopen a terminal on S3DF iana and run the command: ```conda activate lume-ace3p```
   - The text "(lume-ace3p)" should be shown on the command line indicating you are in the correct conda environment
   - The command ```conda deactivate``` can be used to exit the conda environment if desired

To run the examples on an S3DF iana terminal:
1. Copy the ```/sdf/group/rfar/lume-ace3p/examples``` folder to a desired location (e.g. in home or scratch)
2. Run the ace3p setup script with ```source sdf-ace3p.sh``` (required to run ACE3P on S3DF)
   - The sdf-ace3p.sh file is located in ```/sdf/group/rfar/ace3p/```
3. Activate the lume-ace3p conda environment with ```conda activate lume-ace3p``` if not already active
4. Submit a batch job from one of the examples with ```sbatch```
5. View the results in the folder that the batch job was run from

Note: the demo batch job scripts are configured to run using the RFAR group repo on S3DF, and may need to be adjusted.

# How to use LUME-ACE3P

The LUME-ACE3P python scripts enable the use of parameter sweeping and parameter optimization of ACE3P-workflows including Cubit mesh generation and acdtool postprocessing. To perform a simple parameter sweep a user will need to provide the following:

- a Cubit journal (.jou) file for editing (required for remeshing)
- an ACE3P input file (e.g. .omega3p) with desired input settings
- an acdtool postprocess file (e.g. .rfpost) with desired postprocessing settings
- a LUME-ACE3P python script (.py) containing the ACE3P workflow and parameter sweeping/optimization settings
- a batch script (.batch) for submitting a job to HPC resources

The basic idea is that a user submits the batch script to HPC nodes which contains the LUME-ACE3P python script. The LUME-ACE3P python script contains 2 main parts: an ACE3P workflow function definition, and the parameter sweep loop. The parameter sweep loop calls the ACE3P workflow function and uses the appropriate input files with the corresponding codes (e.g. Cubit, Omega3P, etc.) and parses the output for writing to a text file or for use with optimization.

The Cubit journal file, ACE3P input file, and acdtool postprocess files are unchanged from if running ACE3P normally. The details on the LUME-ACE3P python script are discussed in the following section.

# Setting up a LUME-ACE3P python script

A LUME-ACE3P python script primarily consists of two sections: a workflow "function" section which contains the start-to-end steps for evaluating a chain of steps (e.g. Cubit -> Omega3P -> acdtool), and a parameter sweep/optimization section which contains how the inputs and outputs of the workflow function are managed/written to files. In this section, each part of the example "lume-ace3p_psweep_demo.py" is explained in detail.

```python
import os
from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P
from lume_ace3p.acdtool import Acdtool
from lume_ace3p.tools import WriteDataTable

#Define parameters to sweep in lists
input1 = [90 + 10*i for i in range(4)]      #Cavity radii in mm (units in cubit journal)
input2 = [0.5 + 0.25*i for i in range(4)]   #Cavity ellipticity parameter

#Define base working directory for all simulations (each will get its own folder)
my_base_dir = os.path.join(os.getcwd(),'lume-ace3p_demo_workdir')

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

# SLAC National Accelerator Laboratory
The SLAC National Accelerator Laboratory is operated by Stanford University for the US Departement of Energy.  
[DOE/Stanford Contract](https://legal.slac.stanford.edu/sites/default/files/Conformed%20Prime%20Contract%20DE-AC02-76SF00515%20as%20of%202022.10.01.pdf)

# License

We are beginning with the BSD-2 license but this is an open discussion between code authors, SLAC management, and DOE program managers along the funding line for the project.  
