import sys
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor, Constructor
import numpy as np
#from lume_ace3p.workflow import S3PWorkflow, Omega3PWorkflow

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
                temp_key = key+'.'+str(temp_index)
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


def input_to_dict(input_dict, output_dict, temp_key=''):
    for key in input_dict:
        new_key = key
        #if a particular key is associated with an attribute, add .(attribute number)
        if 'Attribute' in str(input_dict.get(key)):
            period_index = key.find('.')
            #if there already is a .number associated with this key, replace the number
            #the .number comes from reading the .yaml file, and will be a random number so we replace to make it meaningful
            if period_index != -1:
                new_key = key[:period_index+1] + str(input_dict[key]['Attribute']) + key[period_index+2:]
            else:
                new_key = key + '.' + str(input_dict[key]['Attribute'])
        #if a particular key is associated with a reference number, add .(reference number)
        elif 'ReferenceNumber' in str(input_dict.get(key)):
            period_index = key.find('.')
            if period_index != -1:
                new_key = key[:period_index+1] + str(input_dict[key]['ReferenceNumber']) + key[period_index+2:]
            else:
                new_key = key + '.' + str(input_dict[key]['ReferenceNumber'])
        
        value = input_dict.get(key)
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
input_to_dict(lume_ace3p_data.get('ace3p_input_parameters'), input_dict)
print(input_dict)
                      



param_updates = {}
index = 0
for key in input_dict:
    num_underscore = key.count('_')
    if(num_underscore==0):
        #prevents repeat keys being overwritten
        if(key in param_updates):
            param_updates[key+'.'+str(index)] = input_dict[key]
            index+=1
        else:
            param_updates[key] = input_dict[key]
    else:
        underscore_index = key.rfind('_')
        temp_key = key[:underscore_index]
        temp_dict = {key[underscore_index+1:]: input_dict[key]}
        for i in range(num_underscore):
            new_dictionary = {}
            underscore_index = temp_key.rfind('_')
            new_dictionary[temp_key[underscore_index+1:]] = temp_dict
            temp_dict = new_dictionary
            temp_key = temp_key[:underscore_index]
        #puts temp_dict in correct spot within param_updates--this is needed to avoid repeat keys when multiple parameters fall under the same category (eg both Coating and Frequency fall under SurfaceMaterial
        def recursive_update(target_dict, search_dict):
            for k in target_dict:
                if k in search_dict:
                    recursive_update(target_dict.get(k),search_dict.get(k))
                else:
                    search_dict.update({k: target_dict.get(k)})
        
        recursive_update(temp_dict, param_updates) 
        
        
print("----------")      
print(param_updates)
                             
                             
                        
def update_dict(new_inputs, dict_to_be_updated):
    for key in new_inputs:
        if isinstance(new_inputs.get(key), dict):
            update_dict(new_inputs.get(key), dict_to_be_updated[key])
        else:
            dict_to_be_updated[key] = new_inputs[key]
            
#update_dict(param_updates, s3p_data)


    #what i want to do is read the file as a dictionary, change things, then rewrite as dictionary
    #data=s3pfile.input_parser()
    #then turn input_dict into nested dict instead of with _ and then replace values in data with values in input dict
    #then convert data back into file 
    
            
#Define output dictionary with data to extract from acdtool (optional)
#output_dict = lume_ace3p_data.get('output_parameters') #None type if not present

#if workflow_dict['mode'].lower() == 'parameter_sweep':
 #   if workflow_dict['module'].lower() == 's3p':
  #      workflow = S3PWorkflow(workflow_dict, input_dict)
   #     workflow.run_sweep()
    #elif workflow_dict['module'].lower() == 'omega3p':
     #   workflow = Omega3PWorkflow(workflow_dict, input_dict, output_dict)
      #  workflow.run_sweep()

