import sys
import yaml
from lume_ace3p.workflow import S3PWorkflow, Omega3PWorkflow

input_file = sys.argv[0]

with open(input_file) as file:
    try:
        lume_ace3p_data = yaml.safe_load(file)
    except yaml.YAMLError as exc:
        print(exc)

#Define workflow dictionary with input files, directory options, etc.
workflow_dict = lume_ace3p_data.get('workflow_parameters')
assert 'module' in workflow_dict.keys(), "Lume-ACE3P keyword 'module' not defined"
assert 'mode' in workflow_dict.keys(), "Lume-ACE3P keyword 'mode' not defined"

#Define input dictionary with keywords and values:
input_dict = lume_ace3p_data.get('input_parameters')

#Define output dictionary with data to extract from acdtool
output_dict = lume_ace3p_data.get('output_parameters')

if workflow_dict['mode'].lower() == 'parameter_sweep':
    if workflow_dict['module'].lower() == 's3p':
        workflow = S3PWorkflow(workflow_dict, input_dict)
        workflow.run_sweep()
    elif workflow_dict['module'].lower() == 'omega3p':
        workflow = Omega3PWorkflow(workflow_dict, input_dict, output_dict)
        workflow.run_sweep()

