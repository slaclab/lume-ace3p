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
input_dict = {'cornercut': np.linspace(13,16,16)}

#Create workflow object and run sweep over input dictionary provided
#Output file will contain keys in input_dict and output_dict as columns and each
#parameter evaluation as a row (tab delimited)
workflow = S3PWorkflow(workflow_dict, input_dict)
workflow.run_sweep()