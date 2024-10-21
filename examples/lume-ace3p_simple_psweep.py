import os
import numpy as np
from lume_ace3p.workflow import Omega3PWorkflow

workflow_dict = {'cubit_input': 'pillbox-rtop.jou',
                 'omega3p_input': 'pillbox-rtop.omega3p',
                 'omega3p_tasks': 4,
                 'omega3p_cores': 4,
                 'rfpost_input': 'pillbox-rtop.rfpost',
                 'workdir': os.path.join(os.getcwd(),'lume-ace3p_demo_workdir'),
                 'workdir_mode': 'auto',
                 'sweep_output_file': 'psweep_output.txt',
                 'sweep_output': True,
                 'autorun': False}

input_dict = {'cav_radius': np.linspace(90,120,4),
              'ellipticity': np.linspace(0.5,1.25,4)}

output_dict = {'Output_1': ['RoverQ', '0', 'RoQ'],
               'Output_2': ['RoverQ', '0', 'Frequency'],
               'Output_3': ['maxFieldsOnSurface', '6', 'Emax']}

workflow = Omega3PWorkflow(workflow_dict, input_dict, output_dict)
workflow.run_sweep()