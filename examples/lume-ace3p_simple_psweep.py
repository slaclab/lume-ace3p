import os
import numpy as np
from lume_ace3p.workflow import Omega3PWorkflow

#Define workflow dictionary with input files, directory options, etc.
workflow_dict = {'cubit_input': 'pillbox-rtop.jou',
                 'omega3p_input': 'pillbox-rtop.omega3p',
                 'omega3p_tasks': 4,
                 'omega3p_cores': 4,
                 'omega3p_opts' : '--cpu-bind=cores',
                 'rfpost_input': 'pillbox-rtop.rfpost',
                 'workdir': os.path.join(os.getcwd(),'lume-ace3p_demo_workdir'),
                 'workdir_mode': 'auto',
                 'sweep_output': True,
                 'sweep_output_file': 'psweep_output.txt',
                 'autorun': False}

#Define input dictionary with keywords and values:
#  Keywords must match exact Cubit file variable names
#  Values can be scalar numbers or vectors (for parameter sweeping)
input_dict = {'cav_radius': np.linspace(90,120,4),
              'ellipticity': np.linspace(0.5,1.25,4)}

#Define output dictionary with data to extract from acdtool
#  Keywords can be any user-provided string (will be used for column names in output)
#  Values are lists of strings of the form [section_name, mode/surface_id, column_name]
output_dict = {'R/Q': ['RoverQ', '0', 'RoQ'],
               'Mode_frequency': ['RoverQ', '0', 'Frequency'],
               'E_max': ['maxFieldsOnSurface', '6', 'Emax'],
               'loc_x': ['maxFieldsOnSurface', '6', 'Emax_location', 'x'],
               'loc_y': ['maxFieldsOnSurface', '6', 'Emax_location', 'y'],
               'loc_z': ['maxFieldsOnSurface', '6', 'Emax_location', 'z']}

#Create workflow object and run sweep over input dictionary provided
#Output file will contain keys in input_dict and output_dict as columns and each
#parameter evaluation as a row (tab delimited)
workflow = Omega3PWorkflow(workflow_dict, input_dict, output_dict)
workflow.run_sweep()