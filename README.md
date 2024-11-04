<a id="readme-top"></a>

![logo](./logos/SLAC-lab-hires.png)

<details><summary>Contents</summary>
<ol>
 <li>
   <a href="#lume-ace3p-introduction">Introduction</a>
   <ul>
     <li><a href="#general-usage">General Usage</a></li>
   </ul>
 </li>
 <li>
   <a href="#installation-and-setup">Installation and Setup</a>
   <ul>
     <li><a href="#perlmutter">Perlmutter</a></li>
     <li><a href="#s3df">S3DF</a></li>
   </ul>
 </li>
 <li>
   <a href="#setting-up-workflow-input-files">Setting up workflow input files</a>
   <ul>
     <li><a href="#cubit-journal-files">Cubit Journal Files</a></li>
     <li><a href="#ace3p-input-files">ACE3P Input Files</a></li>
     <li><a href="#acdtool-postprocess-files">Acdtool Postprocess Files</a></li>
   </ul>
 </li>
 <li><a href="#license">License</a></li>
 <li><a href="#contact">Contact</a></li>
 <li><a href="#acknowledgments">Acknowledgments</a></li>
</ol>
</details>

# LUME-ACE3P Introduction

LUME-ACE3P is a set of python code interfaces, written by David Bizzozero, for running ACE3P workflows (including Cubit and postprocessing routines) with the intent of running parameter sweeps or optimization problems. The base structure of LUME-ACE3P is built on [lume](https://github.com/slaclab/lume), written by Christopher Mayes, and the optimization routines use [Xopt](https://github.com/xopt-org/Xopt), written by Ryan Roussel.

## General Usage
   
The LUME-ACE3P python scripts enable the use of parameter sweeping or optimization of ACE3P-workflows including Cubit mesh generation and acdtool postprocessing. To perform a parameter sweep or optimization run, a user will need to provide the following:

* a Cubit journal (.jou) file for editing (required for remeshing)
* an ACE3P input file (e.g. .omega3p, .s3p, etc.) with desired input settings
* an acdtool postprocess file (e.g. .rfpost) with desired postprocessing settings
* a LUME-ACE3P python script (.py) containing the workflow settings and input/output parameters
* a batch script (.batch) for submitting a job to the appropriate HPC resources

The LUME-ACE3P guides and examples assume a user is familiar with running ACE3P modules and using Cubit for meshing. Visit the [Cubit](https://cubit.sandia.gov/) and [ACE3P](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23) websites for additional information on these codes.

<p align="center"><img src="LUME-ACE3P File Hierarchy.png" width=60% display=block margin=auto></p>

The basic idea is that a user submits the batch script to HPC nodes which contains the LUME-ACE3P python script. The LUME-ACE3P python script contains dictionary objects for the workflow settings, input parameters, and output parameters. A parameter sweep can be run by calling the ACE3P workflow function with the appropriate input/output parameters; this workflow will automatically call other codes (e.g. Cubit, Omega3P, etc.) and parse the output for writing to a text file or for use with optimization.

The Cubit journal file, ACE3P input file, and acdtool postprocess files are generally unaltered from normal ACE3P usage. The details on the LUME-ACE3P python script are discussed in detail in the [python scripts](#Setting-up-LUME-ACE3P-python-scripts) section.

# Installation and Setup

LUME-ACE3P is not configured as a stand-alone Python module. Instead, it is a set of Python scripts dependent on "lume-base>=0.3.3" and "xopt>=2.2.2" from conda-forge. The examples and scripts are configured to run on NERSC Perlmutter or SLAC S3DF in an appropriate python environment with the aformentioned dependencies. See the following for details on how to access pre-made conda environments for LUME-ACE3P on the supported systems.

## Perlmutter
<details><summary>Setup</summary>
   
To activate the lume-ace3p conda environment on a Perlmutter login node:
1. Run the command: `/global/cfs/cdirs/ace3p/software/miniconda3/condabin/conda init` to set up conda for your terminal (only needs to be done once)
2. Reopen a terminal on Perlmutter and run the command: `conda activate lume-ace3p`
   - The text "(lume-ace3p)" should be shown on the command line indicating you are in the correct conda environment
   - The command `conda deactivate` can be used to exit the conda environment if desired

To run the examples on Perlmutter:
1. Copy the `/global/cfs/cdirs/ace3p/lume-ace3p/examples` folder to a desired location (e.g. in home or scratch)
2. Run the ace3p setup script with `source perlmutter-ace3p.sh` (required to run ACE3P on Perlmutter)
   - The `perlmutter-ace3p.sh` file is located in `/global/cfs/cdirs/ace3p/`
   - This step is optional if your `.bashrc` file already has the necessary module imports for ACE3P
3. Set the environment variable `PYTHONPATH` to `/global/cfs/cdirs/ace3p/lume-ace3p/`
   - Use the command `export PYTHONPATH='/global/cfs/cdirs/ace3p/lume-ace3p/'` which can be put in your ".bashrc" file.
   - This command can also instead be placed directly in the batch job script
   - Omitting this step may cause conda package conflicts with NERSC's built-in conda module
4. Activate the lume-ace3p conda environment with the command: `conda activate lume-ace3p` if not already active
5. Submit a batch job of one of the *Perlmutter* examples with `sbatch`
6. View the results in the folder that the batch job was run from
</details>

</details>

## S3DF
<details><summary>Setup</summary>

To activate the lume-ace3p conda environment on an S3DF iana terminal:
1. Run the command: `/sdf/group/rfar/software/conda/bin/conda init` to set up conda for your terminal (only needs to be done once)
2. Reopen a terminal on S3DF iana and run the command: `conda activate lume-ace3p`
   - The text "(lume-ace3p)" should be shown on the command line indicating you are in the correct conda environment
   - The command `conda deactivate` can be used to exit the conda environment if desired

To run the examples on an S3DF iana terminal:
1. Copy the `/sdf/group/rfar/lume-ace3p/examples` folder to a desired location (e.g. in home or scratch)
2. Run the ace3p setup script with `source sdf-ace3p.sh` (required to run ACE3P on S3DF)
   - The `sdf-ace3p.sh` file is located in `/sdf/group/rfar/ace3p/`
3. Set the environment variable `PYTHONPATH` to `/sdf/group/rfar/lume-ace3p/`
   - Use the command `export PYTHONPATH='/sdf/grou/rfar/lume-ace3p/'` which can be put in your ".bashrc" file.
   - This command can also instead be placed directly in the batch job script
4. Activate the lume-ace3p conda environment with the command: `conda activate lume-ace3p` if not already active
5. Submit a batch job of one of the *S3DF* examples with `sbatch`
6. View the results in the folder that the batch job was run from
</details>

# Setting up workflow input files

## Cubit Journal Files
<details><summary>File setup</summary>

Cubit journal files can be very complex, thus only the parts which directly interface with LUME-ACE3P will be discussed here. The important aspects to note in a Cubit file when using LUME-ACE3P are:
- Variable name references
- Mesh export commands

Variable names and values should generally be near the beginning of a Cubit journal file. LUME-ACE3P will read and adjust these values based on given parameter inputs. For example, a Cubit journal might contain APREPRO lines like:
```
#{my_variable_1 = 90}
#{my_variable_2 = 123}
#{my_variable_3 = 0.5}
```
This would be parsed with LUME-ACE3P which would overwrite the numeric quantities following the "=" signs in those lines. **Special care must be taken to ensure the variable names used in the Cubit journal file exactly match those used in the LUME-ACE3P python script input dictionary!**

Since ACE3P can use acdtool to convert Genesis (.gen) formatted meshes into NetCDF (.ncdf), the "export" command in the Cubit journal should use the Genesis option. For example, a Cubit journal might contain the export command:
```
export Genesis "my_mesh_file.gen" block all overwrite
```
This will export the generated mesh into a .gen file and LUME-ACE3P will automatically call acdtool to convert it further into a .ncdf file with the same name ("my_mesh_file.ncdf" in this case).

For more information on Cubit journal files, see the official [Cubit documentation](https://cubit.sandia.gov/documentation/). 

</details>

## ACE3P Input Files
<details><summary>File setup</summary>

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

## Acdtool Postprocess Files
<details><summary>File setup</summary>

An Acdtool postprocess script is used to parse ACE3P code outputs for quantities such as field monitors, impedance calculations, etc. The general input structure is based on sections whose contents are contained within curly braces "{}"; the contents are section-specific and key-value pairs separated by equals signs "=". Acdtool will read-in a ".rfpost" file and provide the results in a "rfpost.out" file. LUME-ACE3P is configured to parse this output file into a python dictionary which can be used to print output parameters or for optimization.

For more information on configuring Acdtool input files, see the [ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23).

</details>

# Setting up LUME-ACE3P python scripts

<details><summary>Omega3P Parameter Sweep Example</summary>
   
This example (based on the rounded-top pillbox from the [ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23)) will set up LUME-ACE3P to run a parameter sweep over the cavity radius and cavity wall ellipticity parameters. The idea is to automate the entire geometry meshing process, Omega3P calculation, and mode postprocessing steps into a simple python script that is submitted directly to HPC resources.

A LUME-ACE3P python script for parameter sweeping primarily consists of definining 3 Python *dictionaries* ([dict objects](https://docs.python.org/3/tutorial/datastructures.html#dictionaries)): a workflow dict which contains various settings (e.g. paths to other code input files), an input dict which contains parameter name and values to be scanned through, and an output dict (optional) which sets which outputs to store after each parameter run. In this section, each of these dict objects of the example "lume-ace3p_simple_psweep.py" is explained in detail.

The script begins with the neccessary LUME-ACE3P imports and workflow dict definition:
```python
import os
import numpy as np
from lume_ace3p.workflow import Omega3PWorkflow

workflow_dict = {'cubit_input': 'pillbox-rtop.jou',
                 'omega3p_input': 'pillbox-rtop.omega3p',
                 'omega3p_tasks': 16,
                 'omega3p_cores': 16,
                 'omega3p_opts' : '--cpu-bind=cores',
                 'rfpost_input': 'pillbox-rtop.rfpost',
                 'workdir': os.path.join(os.getcwd(),'lume-ace3p_demo_workdir'),
                 'workdir_mode': 'auto',
                 'sweep_output': True,
                 'sweep_output_file': 'psweep_output.txt'}
```
This workflow dict object contains various parameters such as input files (path is assumed to be in same directory), working directory settings, and HPC specific commands for ACE3P codes. Specifically for this example, the options are configured for running workflows in separate sub-diectories (automatically named using input values) with the "pillbox-rtop.jou", "pillbox-rtop.omega3p", and "pillbox-rtop.rfpost" files for Cubit, Omega3P, and Acdtool respectively. Additionally, Omega3P is configured to use 16 MPI tasks with 16 cores/task with the CPU thread-binding option to cores. The "sweep_output" keyword simply enables file output writing to a "sweep_output_file" name provided. See the [Workflow dict](#LUME-ACE3P-Python-structures-advanced-users) section for more details on each option.

Next, the input parameters are defined in a separate dict object:
```python
input_dict = {'cav_radius': np.linspace(90,120,4),
              'ellipticity': np.linspace(0.5,1.25,4)}
```
The input dict object contains keyword value pairs for the *exact* names of the variables (as defined in the Cubit journal file) and the corresponding values to sweep. Each parameter value can be a numpy vector array (e.g. numpy.linspace() or a list of numeric types. The parameter-sweep in LUME-ACE3P will evaluate the workflow for all possible tensor products of the input variable arrays.

In this example, the "cav_radius" variable and the "ellipticity" variable are each vectors of length 4, thus the total number of workflow evaluations is 16 (4 x 4). Also, since the "workdir_mode" setting in the workflow dict was set to "auto", each workflow evaluation will create a folder named "lume-ace3p_demo_workdir_X_Y" where "X" and "Y" will be replaced by numeric values of each "cav_radius" and "ellipticity" for a total of 16 distinct folders. See the [Input dict](#LUME-ACE3P-Python-structures-advanced-users) section for more details on using multiple parameters.

Next, the desired outputs are defined in a separate dict object:
```python
output_dict = {'R/Q': ['RoverQ', '0', 'RoQ'],
               'mode_freq': ['RoverQ', '0', 'Frequency'],
               'E_max': ['maxFieldsOnSurface', '6', 'Emax'],
               'loc_x' : ['maxFieldsOnSurface', '6', 'Emax_location', 'x'],
               'loc_y' : ['maxFieldsOnSurface', '6', 'Emax_location', 'y'],
               'loc_z' : ['maxFieldsOnSurface', '6', 'Emax_location', 'z']}
```
The output dict object contains keyword value pairs for desired outputs to write to the specified "sweep_output_file", a tab-delimited text file. This file will contain one column for each input or output and rows corresponding to workflow evaluations. The format for the keyword values is a list object corresponding to the section id (e.g. 'RoverQ'), mode/surface id string (e.g. '0'), and entry name (e.g. 'RoQ') extracted from within the acdtool postprocess output file (named "rfpost.out").

In this example, the first row of the output file will contain 8 text entries: 'cav_radius', 'ellipticity', 'R/Q', 'mode_freq', 'E_max', 'loc_x', 'loc_y', and 'loc_z'. Then in subsequent rows, the columns will be filled with the corresponding 2 input values ('cav_radius' and 'ellipticity') and the 6 output values (extracted from the rfpost.out file for each workflow evaluation). See the [Output dict](#LUME-ACE3P-Python-structures-advanced-users) section for more details on different options to extract from rfpost.out files.

If no output dict is specified, the parameter sweep can still be run, but rfpost.out file data will not be parsed or tabulated (useful if only the different output folders are desired for each parameter combination).

Lastly, the workflow object is instantiated with the 3 defined dict objects and the parameter sweep can begin.
```python
workflow = Omega3PWorkflow(workflow_dict, input_dict, output_dict)
workflow.run_sweep()
```
LUME-ACE3P will internally sweep through the combinations of input parameters provided and write the desired outputs to the "sweep_output_file" provided. See the [Omega3PWorkflow object](#LUME-ACE3P-Python-structures-advanced-users) section for more details on the class usage.

As of now, LUME-ACE3P does not support checkpointing and each workflow evaluation is run serially (future vesion may allow multiple concurrent evaluations).

</details>

<details><summary>Omega3P Optimization Example</summary>
   
This example (based on the rounded-top pillbox from the [ACE3P tutorials](https://confluence.slac.stanford.edu/display/AdvComp/Materials+for+CW23)) will set up LUME-ACE3P to run an optimization loop over the cavity radius and cavity wall ellipticity parameters to maximize the R/Q quantity with a target frequency constraint. The idea is to automate the entire geometry meshing process, Omega3P calculation, and mode postprocessing steps into a simple python script that is interfaced by Xopt routines for optimization.

A LUME-ACE3P python script for optimization primarily consists of definining a few Python *dictionaries* ([dict objects](https://docs.python.org/3/tutorial/datastructures.html#dictionaries)) and configuring Xopt options. As in the parameter-sweeping example, a workflow dict and an output dict are needed to configure the workflow parameters; however, no input_dict is used as this will be handled by Xopt and the VOCS structure (explained below). Additionally, the python script will wrap the workflow in a function that Xopt will call (along with any post-processing steps). Lastly, the Xopt optimizer is run in steps corresponding to ACE3P workflow evaluations.

The script begins with the neccessary LUME-ACE3P imports and workflow dict definition:
```python
import numpy as np
from xopt.vocs import VOCS
from xopt.evaluator import Evaluator
from xopt.generators.bayesian import ExpectedImprovementGenerator
from xopt import Xopt
from lume_ace3p.workflow import Omega3PWorkflow
from lume_ace3p.tools import WriteXoptData

workflow_dict = {'cubit_input': 'pillbox-rtop.jou',
                 'omega3p_input': 'pillbox-rtop.omega3p',
                 'omega3p_tasks': 16,
                 'omega3p_cores': 16,
                 'omega3p_opts' : '--cpu-bind=cores',
                 'rfpost_input': 'pillbox-rtop.rfpost',
                 'workdir': 'lume-ace3p_xopt_workdir'}
```
This workflow dict object contains various parameters such as input files (path is assumed to be in same directory), working directory settings, and HPC specific commands for ACE3P codes. Specifically for this example, the options are configured for running workflows in a single working directory with the "pillbox-rtop.jou", "pillbox-rtop.omega3p", and "pillbox-rtop.rfpost" files for Cubit, Omega3P, and Acdtool respectively.

In this example, the contents of the workflow folder will be overwritten with each evaluation; however, the "workdir_mode" option can be set to write to separate folders automatically. See the [Workflow dict](#LUME-ACE3P-Python-structures-advanced-users) section for more details on each option.

Next, the desired outputs are defined in a separate dict object:
```python
output_dict = {'R/Q': ['RoverQ', '0', 'RoQ'],
               'mode_freq': ['RoverQ', '0', 'Frequency']}
```
The format for the keyword values is a list object corresponding to the section id (e.g. 'RoverQ'), mode/surface id string (e.g. '0'), and entry name (e.g. 'RoQ' or 'Frequency') extracted from within the acdtool postprocess output file (named rfpost.out). This dict is used for parsing the ACE3P workflow output for use with Xopt. See the [Output dict](#LUME-ACE3P-Python-structures-advanced-users) section for more details on different options to extract from rfpost.out files.

The next step is to define the Xopt VOCS (Variables, Objectives, Constraints) configuration:
```python
vocs = VOCS(
    variables={"cav_radius": [95, 105], "ellipticity": [0.5, 1.2]},
    objectives={"R/Q": "MAXIMIZE"},
    constraints={"freq_error" : ["LESS_THAN", 0.0001]},
    observables=["mode_freq"]
)
```
The format for VOCS is a stucture with dict and list objects. In this example, the "variables" dict contains the workflow input parameters to optimize and their bounds. Next, the "objectives" dict contains the quantity in the previously defined output dict to maximize (or minimize). The "constraints" dict is optional and specifies some inequality that is desired for the optimization. And lastly, the "observables" list is optional is simply tracked by Xopt but not used in optimization. See the [VOCS data structure](https://xopt.xopt.org/examples/basic/xopt_vocs) formatting from the Xopt user guide for more information.

**Note, while "R/Q" and "mode_freq" are defined in the output dict, the quantity "freq_error" is not! This is intentional and will be addressed in the simulation function definition next.**

Since the goal of this example is to optimize the "R/Q" quantity with a constraint of the "mode_freq" being within 1% of a specified "target_freq", the simulation function which runs the ACE3P workflow must include an extra step to calculate the "freq_error" quantity for Xopt to read-in.
```python
target_freq = 1.3e9

def sim_function(input_dict):
    workflow = Omega3PWorkflow(workflow_dict,input_dict,output_dict)
    output_data = workflow.run()
    output_data['freq_error'] = (output_data['mode_freq']-target_freq)**2/target_freq**2
    return output_data
```
In this example, a target frequency is set and the sim function is defined as function with dict-type inputs and outputs. Within this sim function, an ACE3P workflow is created and run. Afterwards, the "output_data" dict is modified to include a "freq_error" key with the value calculated by the squared relative error between the "mode_freq" and the "target_freq". Then the "output_data" dict is returned for use by Xopt.

In short, this sim function will provide the ACE3P workflow an input dict containing values of "cav_radius" and "ellipticity", run the workflow, and return an output data dict containing the values of "R/Q", "mode_freq", and "freq_error". Xopt will use this function as a "black-box" to optimize the 2 inputs ("cav_radius" and "ellipticity") with the given output objective ("R/Q"), output constraint ("freq_error"), and tracked observable ("mode_freq").

The last part is to create the Xopt object with a chosen optimizer and provided VOCS and sim function:
```python
evaluator = Evaluator(function=sim_function)
generator = ExpectedImprovementGenerator(vocs=vocs)
X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)
```

To run the optimizer, the Xopt object is called with ".random_evaluate()" and ".step()" methods. The ".random_evaluate()" method will simply call Xopt to run the ACE3P workflow with randomly selected inputs (to initially train Xopt's internal model). The ".step()" method will use the previously computed workflows to select a new set of inputs in the optimization.
```python
for i in range(5):
    X.random_evaluate()
    WriteXoptData('sim_output.txt',X)

for i in range(15):
    X.step()
    WriteXoptData('sim_output.txt',X)
```
In this example, Xopt will call the ACE3P workflow 5 times with randomly selected inputs within the bounding box and then optimize the objective quantity over 15 more ACE3P workflow evaluations (steps). The output file "sim_output.txt" is a user-provided filename and simply prints the Xopt data structure output.

</details>

# LUME-ACE3P Python structures (advanced users)

<details><summary>Workflow dict</summary>

The LUME-ACE3P workflow dict control the workflow task chain (by specifying related input files), directory management, and other settings. The workflow dict *keywords* are:
   * `cubit_input` : `String` [Default `None`] with path to Cubit journal file (.jou) used for the workflow.
   * `omega3p_input` : `String` [Default `None`] with path to Omega3P input file (.omega3p) used for the workflow.
   * `omega3p_cores` : `Int` [Default `1`] to specify the number of cores per task to use with Omega3P.
   * `omega3p_opts` : `String` [Default `''`] to specify additional mpirun or srun arguments when calling Omega3P.
   * `omega3p_tasks` : `Int` [Default `1`] to specify the number of MPI tasks to use with Omega3P.
   * `rfpost_input` : `String` [Default `None`] with path to Acdtool rfpost file (.rfpost) used for the workflow.
   * `sweep_output` : `Boolean` [Detault `False`] to toggle writing parameter sweep output to text file.
   * `sweep_output_file` : `String` [Detault `None`] path for writing parameter sweep output.
   * `workdir` : `String` or `Path` [Default `os.getcwd()`] with path to working directory name for running LUME-ACE3P.
   * `workdir_mode` : `String` [Default `'manual'`] set to either `'manual'` (single workflow folder) or `'auto'` (automatic folder generation)

</details>

<details><summary>Input dict</summary>

The LUME-ACE3P input dict *keywords* and *values* are user-defined. The keyword-value structure is:
   * `input_parameter` : `Int`, `Float`, `List`, or `numpy.ndarray` value

If any input keyword's *value* is a vector-like object (list or ndarray), then the workflow can only be run as a parameter sweep (not a single evaluation). **Input dict *keywords* must *exactly* match the variable names in Cubit journal files.**

During parameter sweeping, all possible combinations of the parameters are evaluated (full tensor product of all input parameter vectors). For example, if three input parameters are provided with vectors of lengths 10, 12, and 15 respectively, then the workflow will be evaluated 1800 times (all 10 x 12 x 15 combinations)!

</details>

<details><summary>Output dict</summary>

The LUME-ACE3P output dict *keywords* are user-defined but the *values* are lists containing specific strings corresponding to specific outputs in the acdtool "rfpost.out" file. The keyword-value structure is:
   * `output_name` : `List` of `String` entries of the form `['section', string1, string2, ...]` with section-specific strings following the section name at the start of the list (see examples below).
     * The `output_name` *keywords* are arbitrary strings, only used for printing column headers in parameter sweep output files or for optimization routines.

The currently supported *values* with section names and string list values are:
   * `['RoverQ', string1, string2]` corresponding to the "[RoverQ]" data block in the "rfpost.out" file.
     * `string1` contains the mode ID number to be processed (usually starting from `"0"`)
     * `string2` contains the data column name of the corresponding mode
       * `string2` must be one of `'Frequency'`, `'Qext'`, `'V_r'`, `'V_i'`, `'AbsV'`, or `'RoQ'`
   * `['maxFieldsOnSurface', string1, string2, string3]` corresponding to the "[maxFieldsOnSurface]" data block in the "rfpost.out" file.
     * `string1` contains the surface ID number to be processed (defined by the sideset in the Cubit journal file)
     * `string2` contains the data column name of the corrseponding surface
       * `string2` must be one of `'Emax'`, `'Emax_location'`, `'Hmax'`, or `'Hmax_location'`
     * `string3` is either `'x'`, `'y'`, or `'z'`, and specifies the component of the `'Emax_location'` or `'Hmax_location'` vector

More sections and entries will be added in future updates.

</details>

<details><summary>Omega3PWorkflow class</summary>

The "Omega3PWorkflow" class can be instantiated using only a workflow dict. An "Omega3PWorkflow" object can run as-is, but no workflow input files will be adjusted. A selected list of usage examples is provided:

Object constructor usage:
* `workflow_object = Omega3PWorkflow(workflow_dict, *input_dict, *output_dict)` --- creates a workflow object from the workflow_dict and sets input/output dicts (optional arguments)

Object method usage:
* `workflow_object.run(*input_dict, *output_dict)` --- runs workflow using input_dict parameter values (overwrites initial input_dict) and returns an output data dict (overwrites initial output_dict)
   * Note: the `.run()` method will **not** work with input dicts containing lists or vectors! Use `.run_sweep()` for multi-valued inputs)!
* `workflow_object.run_sweep(*input_dict, *output_dict)` --- runs workflow as a parameter sweep using the optionally provided input/output dicts (defaults to initially provided dicts)
* `workflow_object.evaluate(*output_dict)` --- evaluates quantities referenced in output_dict (defaults to initially provided output dict) and returns an output data dict
* `workflow_object.print_sweep_output(*filename)` --- writes out quantities from parameter sweep to provided filename (defaults to `sweep_output_file` value provided in workflow dict)

Object data output:
* `output_data = workflow_object.evaluate()` --- returns a dict object with the same keywords as the output dict but with the values replaced by the numeric quantities from evaluation
* `output_data = workflow_object.run()` --- returns a dict object with the same keywords as the output dict but with the values replaced by the numeric quantities from evaluation
* `output_sweep_data = workflow_object.run_sweep()` --- returns a dict object with `Tuple` *keywords* formed by the combination of input parameters and output data dict *values* for each evaluation
  * Note: this is a nested dict object with the outer keywords consisting of tuples of inputs and the inner keywords corresponding to the evaluated output dict for each input combination.

</details>

# Troubleshooting

## LUME-ACE3P FAQs

<details><summary>Why does LUME-ACE3P fail to find the mesh file generated from Cubit?</summary>

Check that .gen filename provided with the "export" command in the Cubit journal matches the .ncdf filename in the Omega3P input file. For example, if the Cubit journal includes the command `export genesis "my_mesh.gen"`, then the Omega3P input file should contain `File: ./my_mesh.ncdf` within the "ModelInfo" block.

</details>

<details><summary>Why does LUME-ACE3P fail during Omega3P?</summary>

Check that the mesh file is correct and appropriate resources are allocated for the problem size (i.e. no out-of-memory errors). If the mesh is unexpectedly too large, check the Cubit journal for errors, particularly in the meshing routine.
Also check the Omega3P input file for errors (typos in the key-value containers) or inconsistencies (sideset numbers are matched with Cubit journal export).

</details>

<details><summary>Why does LUME-ACE3P fail for specific parameter values?</summary>

Cubit journal files require care in constructing when using parametric variables. Some variables cannot exceed certain quantities or the geometry may undefined or topologically change. When topological changes occur, the Cubit vertex/curve/surface/volume IDs may change and affect sideset ID definitions. These sideset IDs are used by Omega3P and Acdtool in defining surfaces and if incorrectly assigned, may cause the LUME-ACE3P workflow to crash or produce junk results.

Check that the provided journal file works as intended with extremal values for all given parameters. For example, if using LUME-ACE3P to sweep the parameter "input_1" from 20 to 80, make sure the journal file works properly when the Cubit variable "input_1" is 20 and 80 (assuming the deformation is smooth and continuous between those values).

</details>

<details><summary>Can I restart a parameter sweep if the job failed mid-sweep?</summary>

As for now, checkpointing is not implemented in LUME-ACE3P. However, as a workaround, adjusting the input dictionary parameters can achieve similar results. For example, if sweeping the parameter "input_1" from 20 to 80 in steps of 10 with `input_1 : np.linspace(20,80,7)` (i.e. 7 evaluations total: 20, 30, 40, 50, 60, 70, and 80) and the job fails when "input_1" is 50 (e.g. due to job timeout). Then editing the input dict with `input_1 : np.linspace(50,80,4)` will restart the sweep at 50 and continue to 80 (i.e. 4 evaluations total: 50, 60, 70, and 80).

**Note: the parameter sweep output file is *overwritten* each workflow evaluation, so save the incomplete (failed) run output file to a new filename to later combine the results with the restarted run!**

</details>

# SLAC National Accelerator Laboratory
The SLAC National Accelerator Laboratory is operated by Stanford University for the US Departement of Energy.  
[DOE/Stanford Contract](https://legal.slac.stanford.edu/sites/default/files/Conformed%20Prime%20Contract%20DE-AC02-76SF00515%20as%20of%202022.10.01.pdf)

# License

We are beginning with the BSD-2 license but this is an open discussion between code authors, SLAC management, and DOE program managers along the funding line for the project.  
