import sys
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor, Constructor
from collections import defaultdict
import numpy as np
import copy
from lume_ace3p.workflow import S3PWorkflow, Omega3PWorkflow

input_file = sys.argv[1]

#overwriting class from ruamel.yaml that will allow a file to be read in with repeat keys
class UniqueKeyConstructor(SafeConstructor):
    def construct_mapping(self, node, deep=False):
        mapping = {}
        temp_index = 0
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            temp_key = key
            if key in mapping:
                temp_key = key+'.LILA.'+str(temp_index)+'.LILA.'
                temp_index += 1
            value = self.construct_object(value_node, deep=deep)
            mapping[temp_key] = value
        return mapping
    
class UniqueKeyYAML(YAML):
    def __init__(self, **kw):
        YAML.__init__(self, **kw)
        self.Constructor = UniqueKeyConstructor

with open(input_file) as file:
    try:
        #lume_ace3p_data = yaml.safe_load(file)
        yaml = UniqueKeyYAML(typ='safe')
        lume_ace3p_data = yaml.load(file)
    except yaml.YAMLError as exc:
        print(exc)
        
#Define workflow dictionary with input files, directory options, etc.
workflow_dict = lume_ace3p_data.get('workflow_parameters')
assert 'module' in workflow_dict.keys(), "Lume-ACE3P keyword 'module' not defined"
assert 'mode' in workflow_dict.keys(), "Lume-ACE3P keyword 'mode' not defined"


def input_to_dict(input_dict, output_dict, temp_key='', ace3p=False):
    for key in input_dict:
        #if a particular key is associated with an attribute, add -(attribute number)
        if isinstance(input_dict[key], dict) and 'Attribute' in input_dict.keys():
            period_index = key.find('.LILA.')
            period_index_2 = key.find('.LILA.', period_index+5)
            #if there already is a .number associated with this key, replace the number
            #the .number comes from reading the .yaml file, and will be a random number so we replace to make it meaningful
            if period_index != -1:
                new_key = key[:period_index] + '|LILA|' + str(input_dict['Attribute']) + '|LILA&' + key[period_index_2+6:]
            else:
                new_key = key + '|LILA|' + str(input_dict['Attribute']) + '|LILA&'
        #if a particular key is associated with a reference number, add .(reference number)
        elif isinstance(input_dict[key], dict) and 'ReferenceNumber' in input_dict.keys():
            period_index = key.find('.LILA.')
            period_index_2 = key.find('.LILA.', period_index+5)
            if period_index != -1:
                new_key = key[:period_index] + '?LILA?' + str(input_dict['ReferenceNumber']) + '?LILA&' + key[period_index_2+6:]
            else:
                new_key = key + '?LILA?' + str(input_dict['ReferenceNumber']) + '?LILA&'
       
        value = input_dict.get(key)
        if ace3p:
            key = 'ACE3P' + key
        new_key = key
        if isinstance(value,dict):
            if 'options' in value:
                output_dict[temp_key+new_key] = value.get('options')
            elif 'min' in value:
                output_dict[temp_key+new_key] = np.linspace(value.get('min'),value.get('max'),value.get('num'))
            else:
                input_to_dict(value, output_dict, temp_key+new_key+'_')
                      
#Define input dictionary with keywords and values:
input_dict = {}
input_to_dict(lume_ace3p_data.get('cubit_input_parameters'), input_dict)
input_to_dict(lume_ace3p_data.get('ace3p_input_parameters'), input_dict, ace3p=True)

        
#Define output dictionary with data to extract from acdtool (optional)
output_dict = lume_ace3p_data.get('output_parameters') #None type if not present

if workflow_dict['mode'].lower() == 'parameter_sweep':
    if workflow_dict['module'].lower() == 's3p':
        workflow = S3PWorkflow(workflow_dict, input_dict)
        workflow.run_sweep()
    elif workflow_dict['module'].lower() == 'omega3p':
        workflow = Omega3PWorkflow(workflow_dict, input_dict, output_dict)
        workflow.run_sweep()
