import os
import numpy as np
from lume_ace3p.workflow import S3PWorkflow

#Define workflow dictionary with input files, directory options, etc.
workflow_dict = {'cubit_input': 'bend-90degree.jou',
                 'ace3p_input': 'bend-90degree.s3p',
                 'ace3p_tasks': 16,
                 'ace3p_cores': 16,
                 'ace3p_opts' : '--cpu-bind=cores',
                 'workdir': os.path.join(os.getcwd(),'lume-ace3p_s3p_workdir'),
                 'workdir_mode': 'auto',
                 'sweep_output': True,
                 'sweep_output_file': 's3p_sweep_output.txt',
                 'autorun': False}

#Define input dictionary with keywords and values:
#  Keywords must match exact Cubit file variable names
#  Values can be scalar numbers or vectors (for parameter sweeping)
input_dict = {'cornercut': np.linspace(12,18,4),
              'rcorner2': np.linspace(3,6,4)}

#Define output dictionary with data to extract from acdtool
#  Keywords can be any user-provided string (will be used for column names in output)
#  Values are S-parameter names indexed via IndexMap from Reflection.out
output_dict = {'M0S11': 'S(0,0)',
               'M0S12': 'S(0,2)',
               'M0S21': 'S(2,0)',
               'M0S22': 'S(2,2)'}

#Create workflow object and run sweep over input dictionary provided
#Output file will contain keys in input_dict and output_dict as columns and each
#parameter evaluation as a row (tab delimited)
workflow = S3PWorkflow(workflow_dict, input_dict, output_dict)
workflow.run_sweep()