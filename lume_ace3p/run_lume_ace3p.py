import sys
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor, Constructor
from collections import defaultdict
import numpy as np
import copy
from lume_ace3p.workflow import S3PWorkflow, Omega3PWorkflow
from lume_ace3p.run_xopt import run_xopt

input_file = sys.argv[1]

#overwriting class from ruamel.yaml that will allow a file to be read in with repeat keys
#now, if a repeat key is detected, .LILA.#.LILA. is appended to the repeat key, where # is an integer
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
        yaml = UniqueKeyYAML(typ='safe')
        lume_ace3p_data = yaml.load(file)
    except yaml.YAMLError as exc:
        print(exc)
        
#Define workflow dictionary with input files, directory options, etc.
workflow_dict = lume_ace3p_data.get('workflow_parameters')
assert 'module' in workflow_dict.keys(), "Lume-ACE3P keyword 'module' not defined"
assert 'mode' in workflow_dict.keys(), "Lume-ACE3P keyword 'mode' not defined"

#this function takes the raw .yaml input read in and converts it to desired form
#.LILA.#.LILA. are replaced with appropriate ReferenceNumber or Attribute, and those keys are deleted
#data is stored in single key value pairs, so the dictionary is no longer nested
def input_to_dict(input_dict, output_dict, temp_key='', ace3p=False):
    if input_dict is not None:
        for key in input_dict:
            new_key = key
            #if a particular key is associated with an attribute, add -(attribute number)
            if isinstance(input_dict[key], dict) and 'Attribute' in input_dict[key].keys():
                period_index = key.find('.LILA.')
                period_index_2 = key.find('.LILA.', period_index+5)
                #if there already is a .number associated with this key, replace the number
                #the .number comes from reading the .yaml file, and will be a random number so we replace to make it meaningful
                if period_index != -1:
                    new_key = key[:period_index] + '|LILA|' + str(input_dict[key]['Attribute']) + '|LILA&' + key[period_index_2+6:]
                else:
                    new_key = key + '|LILA|' + str(input_dict[key]['Attribute']) + '|LILA&'
            #if a particular key is associated with a reference number, add .(reference number)
            elif isinstance(input_dict[key], dict) and 'ReferenceNumber' in input_dict[key].keys():
                period_index = key.find('.LILA.')
                period_index_2 = key.find('.LILA.', period_index+5)
                if period_index != -1:
                    new_key = key[:period_index] + '?LILA?' + str(input_dict[key]['ReferenceNumber']) + '?LILA&' + key[period_index_2+6:]
                else:
                    new_key = key + '?LILA?' + str(input_dict[key]['ReferenceNumber']) + '?LILA&'
            if key != 'Attribute' and key != 'ReferenceNumber':
                value = input_dict.get(key)
                #adds an ACE3P flag to ACE3P parameters in the code. This will distinguish it from Cubit parameters to be changed
                if ace3p:
                    new_key = 'ACE3P' + new_key
                #options and min/max/num indicate a parameter to be swept over, value indicates a parameter to be set but not swept
                if isinstance(value,dict):
                    if 'min' in value:
                        output_dict[temp_key+new_key] = np.linspace(value.get('min'),value.get('max'),value.get('num'))
                    else:
                        input_to_dict(value, output_dict, temp_key+new_key+'_')
                else:
                    if isinstance(value,list):
                        if len(value)>1:
                            output_dict[temp_key+new_key] = value
                        else:
                            output_dict['DONTINCLUDE'+temp_key+new_key] = value
                    else:
                        output_dict['DONTINCLUDE'+temp_key+new_key] = str(value)

                                   
#Define input dictionary with keywords and values:
input_dict = {}
input_to_dict(lume_ace3p_data.get('input_parameters'), input_dict)
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

if workflow_dict['mode'].lower() == 'scalar_optimize':
    if workflow_dict['module'].lower() == 's3p':
        vocs_dict = lume_ace3p_data.get('vocs_parameters')
        xopt_dict = lume_ace3p_data.get('xopt_parameters')
        if 'constraints' not in vocs_dict:
            vocs_dict['constraints'] = {}
        if 'observables' not in vocs_dict:
            vocs_dict['observables'] = []
        if 'constants' not in vocs_dict:
            vocs_dict['constants'] = {}
        
        run_xopt(workflow_dict, vocs_dict, xopt_dict)
