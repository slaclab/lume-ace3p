import os, shutil
import subprocess
import numpy as np
import copy

from lume.base import CommandWrapper

class ACE3P(CommandWrapper):

    MPI_CALLER = os.environ['MPI_CALLER']
    ACE3P_PATH = os.environ['ACE3P_PATH']
    module_name = ''

    def __init__(self, *args, ace3p_tasks=1, ace3p_cores=1, ace3p_opts='', **kwargs):
        super().__init__(*args, **kwargs)
        self.ace3p_tasks = ace3p_tasks
        self.ace3p_cores = ace3p_cores
        self.ace3p_opts = ace3p_opts
        self.output_file = None
        self.output_data = {}
        if self.workdir is None:
            self.workdir = os.getcwd()
        if self.ace3p_opts is None:
            self.ace3p_opts = ''
        if self.ace3p_opts.startswith('--cpu-bind') and self.MPI_CALLER != 'srun':
            self.ace3p_opts = ''
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        #will generate a blank file in current directory that gets written to later
        if self.input_file is None:
            print('WARNING: no .ace3p input file specified, writing one based on contents of .yaml file. Errors may occur if essential parameters like ModelInfo are not specified in .yaml file.')
            self.make_default_input()
        self.original_input_file = self.input_file
        if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
            shutil.copy(self.input_file, self.workdir)
        with open(self.input_file) as file:
            text = file.read()
            self.input_data = text

    def run(self):
        self.write_input()
        subprocess.run(self.MPI_CALLER + ' -n ' + str(self.ace3p_tasks) + ' -c ' + str(self.ace3p_cores) + ' ' + self.ace3p_opts + ' ' + self.ACE3P_PATH + self.module_name + ' ' + self.input_file, shell=True, cwd=self.workdir)
        self.output_parser()

    def load_input_file(self, *args):
        if args:
            self.input_file = args[0]
        with open(self.input_file) as file:
            text = file.read()
        #self.input_data = self.input_parser(text) #DUMMIED OUT FOR COMPATIBILITY
        self.input_data = text
        
    def input_parser(self,text):
        #this function reads in .ace3p data, processes it with raw_input_parser, and then parses to get it in the correct format (Attribute and ReferenceNumber stored as part of the keys)
        data = self.raw_input_parser(text)
        fixed_data = {}
        
        #turns inputted data string into a nested dictionary and adds signifiers for ReferenceNumber and Attribute
        #needs to be a subfunction because it is recursive but we don't want self.raw_input_parser to be called recursively
        def input_to_dict(input_dict, output_dict):
            for key in input_dict:
                new_key = key
                #if a particular key is associated with an attribute, add |LILA|(attribute number)|LILA&
                if isinstance(input_dict[key], dict) and 'Attribute' in input_dict.keys():
                    period_index = key.find('.LILA.')
                    period_index_2 = key.find('.LILA.', period_index+5)
                    #if there already is a .number associated with this key, replace the number
                    #the .number comes from reading the .yaml file, and will be a random number so we replace to make it meaningful
                    if period_index != -1:
                        new_key = key[:period_index] + '|LILA|' + str(input_dict['Attribute']) + '|LILA&' + key[period_index_2+6:]
                    else:
                        new_key = key + '|LILA|' + str(input_dict['Attribute']) + '|LILA&'
                #if a particular key is associated with a reference number, add ?LILA?(reference number)?LILA&
                elif isinstance(input_dict[key], dict) and 'ReferenceNumber' in input_dict.keys():
                    period_index = key.find('.LILA.')
                    period_index_2 = key.find('.LILA.', period_index+5)
                    if period_index != -1:
                        new_key = key[:period_index] + '?LILA?' + str(input_dict['ReferenceNumber']) + '?LILA&' + key[period_index_2+6:]
                    else:
                        new_key = key + '?LILA?' + str(input_dict['ReferenceNumber']) + '?LILA&'
                        
                output_dict[new_key] = input_dict[key]
                #if input_dict[key] is a dictionary, recursively check for ReferenceNumber and Attribute within
                if isinstance(input_dict[key], dict):
                    #this prevents ReferenceNumber and Attribute from remaining in the dictionary as values
                    if 'ReferenceNumber' in input_dict[key].keys():
                        output_dict[new_key] = {k: v for k,v in input_dict[key].items() if k!='ReferenceNumber'}
                    elif 'Attribute' in input_dict[key].keys():
                        output_dict[new_key] = {k: v for k,v in input_dict[key].items() if k!='Attribute'}
                    input_to_dict(input_dict[key], output_dict[new_key])
                #if input_dict[key] is not a dictionary, it is the end of the nested dictionary and is a value. For use in later parsing, replace commas in value with COMMA
                else:
                    comma_index = str(input_dict[key]).find(',')
                    while comma_index != -1:
                        output_dict[new_key] = str(output_dict[new_key])[:comma_index] + 'COMMA' + str(output_dict[new_key])[comma_index+1:]
                        comma_index = str(output_dict[new_key]).find(',')
                        
        input_to_dict(data, fixed_data)   
        return fixed_data
    
    def raw_input_parser(self, text):
        data = {}
        key, value  = None, None
        i, str_start, str_end = 0, 0, 0
        state = 'key'   #State flag to switch between 'key', 'value', and 'comment' text
        index = 0
        while i < len(text):
            if text[i] not in ['\n','/','{','}',':']:
                i += 1
                continue
            if state == 'key':
                if text[i] == ':':  #Switch state from 'key' to 'value' and save key
                    str_end = i
                    key = text[str_start:str_end].strip()
                    str_start = i+1
                    state = 'value'
                i += 1
            elif state == 'value':  #Switch state from 'value' to 'key' and write to dict
                if text[i] == '\n':
                    str_end = i
                    value = text[str_start:str_end].strip()
                    data[key] = value
                    str_start = i+1
                    state = 'key'
                    i += 1
                    continue
                elif text[i] == '/':    #Switch state to 'comment' and write value to dict
                    if text[i+1] == '/':
                        str_end = i
                        value = text[str_start:str_end].strip()
                        data[key] = value
                        state = 'comment'
                    i += 2
                    continue
                elif text[i] == '{':    #Setup recursion for nested dict
                    layer = 1
                    for j in range(i+1,len(text)):  #Find the corresponding closed '}'
                        if text[j] == '{':
                            layer += 1
                        elif text[j] == '}':
                            layer -= 1
                            if layer == 0:
                                break
                    subtext = text[i+1:j]   #Extract nested dict contents
                    value = self.raw_input_parser(subtext)  #Recursively parse nested dict
                    if(key in data):
                        data[key+'.LILA.'+str(index)+'.LILA.'] = value
                        index += 1
                    else:
                        data[key] = value
                    str_start = j+1
                    i = j+1
                    state = 'key'
                    continue
                i += 1
            elif state == 'comment':    #Switch state from 'comment' to 'key' with newline
                if text[i] == '\n':
                    str_start = i+1
                    state = 'key'
                i += 1
        return data
    
    def set_value(self, kwargs):
        #function that will take keys in target_dict and assign their value to matching keys in search_dict
        #will be used to update parameters being swept over
        def recursive_update(target_dict, search_dict):
            for k in target_dict:
                if k in search_dict:
                    recursive_update(target_dict.get(k),search_dict.get(k))
                else:
                    search_dict.update({k: target_dict.get(k)})
        
        param_updates = {}
        index = 0
        #takes input_dict and unpacks keys into a nested dictionary
        for key in kwargs:
            num_underscore = key.count('_')
            if(num_underscore==0):
                param_updates[key] = kwargs[key]
            else:
                underscore_index = key.rfind('_')
                temp_key = key[:underscore_index]
                temp_dict = {key[underscore_index+1:]: kwargs[key]}
                for i in range(num_underscore):
                    new_dictionary = {}
                    underscore_index = temp_key.rfind('_')
                    new_dictionary[temp_key[underscore_index+1:]] = temp_dict
                    temp_dict = new_dictionary
                    temp_key = temp_key[:underscore_index]
                #puts temp_dict in correct spot within param_updates--this is needed to avoid repeat keys when multiple parameters fall under the same category (eg both Coating and Frequency fall under SurfaceMaterial
                recursive_update(temp_dict, param_updates) 

        #generates a dictionary based on contents of .s3p file
        ace3p_data = self.input_parser(self.input_data) 
        
        #eliminates param update values that relate to the cubit file
        #removes ACE3P and DONTINCLUDE flags
        ace3p_params = copy.deepcopy(param_updates)
        for key in param_updates:
            if key.startswith('DONTINCLUDE'):
                ace3p_params[key[11:]] = ace3p_params[key]
            if key.startswith('ACE3P'):
                ace3p_params[key[5:]] = ace3p_params[key]
            del ace3p_params[key]

        def update_dict(new_inputs, dict_to_be_updated):
            for key in new_inputs:
                if isinstance(new_inputs.get(key), dict):
                    if key in dict_to_be_updated:
                        update_dict(new_inputs.get(key), dict_to_be_updated[key])
                    else:
                        dict_to_be_updated[key] = new_inputs[key]
                else:
                    dict_to_be_updated[key] = new_inputs[key]
        #replace values in ace3p_data dictionary where indicated by param_updates dictionary values
        update_dict(ace3p_params, ace3p_data)
        
        #turn updated ace3p_data dictionary into a string that follows .ace3p format
        ace3p_string = str(ace3p_data)
        #get rid of beginning and ending { characters
        ace3p_string = str(ace3p_string)[1:-1]
        #add newline after nested element begins
        ace3p_string = ace3p_string.replace("{", "{\n")
        #replace commas with newlines
        ace3p_string = ace3p_string.replace(", ", "\n")
        #remove extraneous string signifiers on keys
        ace3p_string = ace3p_string.replace("'", "")
        #add a newline after end of each nest element
        ace3p_string = ace3p_string.replace("}", "\n}")
        #replace COMMA, a signifier for where a comma is supposed to be in the values, with a comma
        ace3p_string = ace3p_string.replace("COMMA", ",")

        #find and replace ReferenceNumber signifiers with ReferenceNumber
        question_index = ace3p_string.find('?LILA?')
        amp_index = ace3p_string.find('?LILA&')
        #prevents there being an extra bracket after ReferenceNumber
        bracket_index = ace3p_string.find('{', amp_index)
        while question_index != -1:
            ace3p_string = ace3p_string[:question_index] + ':\n{\nReferenceNumber: ' + ace3p_string[question_index+6] + ace3p_string[bracket_index+1:]
            question_index = ace3p_string.find('?LILA?')
            amp_index = ace3p_string.find('?LILA&')
            bracket_index = ace3p_string.find('{', amp_index)
        #find and replace Attribute signifiers with Attribue
        bar_index = ace3p_string.find('|LILA|')
        amp_index = ace3p_string.find('|LILA&')
        #prevents there being an extra bracket after Attribute
        bracket_index = ace3p_string.find('{', amp_index)
        while bar_index != -1:
            ace3p_string = ace3p_string[:bar_index] + ':\n{\nAttribute: ' + ace3p_string[bar_index+6] + ace3p_string[bracket_index:]
            bar_index = ace3p_string.find('|LILA|')
            amp_index = ace3p_string.find('|LILA&')
            bracket_index = ace3p_string.find('{', amp_index)
            
        #sets input data to updated values. self.input_data is written into the input file in self.write_input, called at the beginning of self.run
        self.input_data = ace3p_string
    
    def write_input(self, *args):
        if args:
            file = args[0]
        else:
            file = self.input_file
        if os.path.isfile(os.path.join(self.workdir, self.input_file)):
            if os.path.samefile(os.path.join(self.workdir, self.input_file), os.path.join(os.getcwd(), self.original_input_file)):
                file = file + '_copy'   #Used to not overwrite original input if in same directory
        self.input_file = file
        text = self.input_data
        #write the contents of input_data (which should by now, after set_value is called, be a string in correct format to .ace3p file
        with open(os.path.join(self.workdir, file), 'w') as file:
            file.write(text)

    def unpack_dict(self, data, text, indent):
        for key, value in data.items():
            if isinstance(value, dict): #Recursively unpack nested dict
                text += '  '*indent + key + ' : {\n'
                text = self.unpack_dict(value, text, indent+1)
                text += '  '*indent + '}\n'
            else:
                text += '  '*indent + key + ' : ' + value + '\n'
        return text
    
    def make_default_input(self):
        pass

    def output_parser(self):
        pass

    def load_output(self):
        return 'Not implemented.'

    def format_data(self):
        return 'Not implemented.'
    
    def configure(self):
        return 'Not implemented.'
    
    def archive(self):
        return 'Not implemented.'

    def load_archive(self):
        return 'Not implemented.'
    
    def plot(self):
        return 'Not implemented.'

class Omega3P(ACE3P):

    module_name = 'omega3p'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 'omega3p.out'
    def make_default_input(self):
        self.input_file = 'omega3p_input_file.omega3p'
        with open(self.input_file, 'w') as f:
            pass

class S3P(ACE3P):

    module_name = 's3p'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 's3p.out'
        
    def output_parser(self):
        self.output_data = {}
        with open(os.path.join(self.workdir, 's3p_results/Reflection.out')) as file:
            lines = file.readlines()
        for ind in range(len(lines)):
            if lines[ind].startswith('#Index'):
                indrow = ind
            if lines[ind].startswith('#Frequency'):
                freqrow = ind
                break
        self.output_data['IndexMap'] = {}
        for row in lines[indrow+1:freqrow]:
            id = row.strip('#').split()[0]
            self.output_data['IndexMap'][id] = {}
            self.output_data['IndexMap'][id]['Port'] = row.split('Port')[1].split()[0].strip(',')
            self.output_data['IndexMap'][id]['Mode'] = row.split('Mode')[1].split()[0].strip(',')
            self.output_data['IndexMap'][id]['Type'] = row.split('Type:')[1].split()[0].strip('(')
            self.output_data['IndexMap'][id]['Cutoff'] = eval(row.split('cutoff:')[1].split('Hz')[0].strip())
        frequency= []
        sparameters = []
        for row in lines[freqrow+1::]:
            rowlist = row.split()
            frequency.append(eval(rowlist[0]))
            sparameter = []
            for entry in rowlist[1::]:
                sparameter.append(eval(entry))
            sparameters.append(sparameter)
        sparameters = np.array(sparameters).transpose()
        self.output_data['Frequency'] = np.array(frequency)
        num_ids = len(self.output_data['IndexMap'].keys())
        for id1 in range(num_ids):
            for id2 in range(num_ids):
                sname = 'S(' + str(id1) + ',' + str(id2) + ')'
                self.output_data[sname] = sparameters[id1*num_ids+id2]
                
    def make_default_input(self):
        self.input_file = 's3p_input_file.s3p'
        with open(self.input_file, 'w') as f:
            pass

class T3P(ACE3P):

    module_name = 't3p'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 't3p.out'

# class Omega3P(CommandWrapper):
    
#     MPI_CALLER = os.environ['MPI_CALLER']
#     ACE3P_PATH = os.environ['ACE3P_PATH']
#     module_name = 'omega3p'
    
#     def __init__(self, *args, tasks=1, cores=1, opts='', **kwargs):
#         super().__init__(*args, **kwargs)
#         self.tasks = tasks
#         self.cores = cores
#         self.opts = opts
#         if self.workdir is None:
#             self.workdir = os.getcwd()
#         if not os.path.exists(self.workdir):
#             os.mkdir(self.workdir)
#         self.original_input_file = self.input_file
#         if self.input_file is None:
#             self.make_default_input()
#         if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
#             shutil.copy(self.input_file, self.workdir)
#         self.output_file = 'omega3p.out'    #Default output file for omega3p
#         self.load_input_file()
        
#     def run(self):
#         self.write_input()
#         result = subprocess.run(self.MPI_CALLER + ' -n ' + str(self.tasks) + ' -c ' + str(self.cores) + ' ' + self.opts + ' '
#                                 + self.ACE3P_PATH + self.module_name + ' ' + self.input_file,
#                                 shell=True, cwd=self.workdir, capture_output=True, text=True)
#         with open(os.path.join(self.workdir, self.output_file), 'w') as file:
#             file.writelines(result.stdout)
        
#     def get_frequencies(self):  #Script to extract frequency modes from stdout
#         with open(os.path.join(self.workdir, self.output_file)) as file:
#             lines = file.readlines()
#         mode_list = []
#         freq_list = []
#         for line in lines:
#             if line.startswith('COMMIT MODE:'):
#                 subline = line.strip('COMMIT MODE: ').split()
#                 mode_list.append(eval(subline[0]))
#                 freq_list.append(eval(subline[3]))
#         self.frequencies = dict(zip(mode_list,freq_list))
        
#     def load_input_file(self, *args):
#         if args:
#             self.input_file = args[0]
#         with open(self.input_file) as file:
#             text = file.read()
#         self.input_data = self.input_parser(text)
        
#     def input_parser(self, text):
#         data = {}
#         key, value  = None, None
#         i, str_start, str_end = 0, 0, 0
#         state = 'key'   #State flag to switch between 'key', 'value', and 'comment' text
#         while i < len(text):
#             if text[i] not in ['\n','/','{','}',':']:
#                 i += 1
#                 continue
#             if state == 'key':
#                 if text[i] == ':':  #Switch state from 'key' to 'value' and save key
#                     str_end = i
#                     key = text[str_start:str_end].strip()
#                     str_start = i+1
#                     state = 'value'
#                 i += 1
#             elif state == 'value':  #Switch state from 'value' to 'key' and write to dict
#                 if text[i] == '\n':
#                     str_end = i
#                     value = text[str_start:str_end].strip()
#                     data[key] = value
#                     str_start = i+1
#                     state = 'key'
#                     i += 1
#                     continue
#                 elif text[i] == '/':    #Switch state to 'comment' and write value to dict
#                     if text[i+1] == '/':
#                         str_end = i
#                         value = text[str_start:str_end].strip()
#                         data[key] = value
#                         state = 'comment'
#                     i += 2
#                     continue
#                 elif text[i] == '{':    #Setup recursion for nested dict
#                     layer = 1
#                     for j in range(i+1,len(text)):  #Find the corresponding closed '}'
#                         if text[j] == '{':
#                             layer += 1
#                         elif text[j] == '}':
#                             layer -= 1
#                             if layer == 0:
#                                 break
#                     subtext = text[i+1:j]   #Extract nested dict contents
#                     value = self.input_parser(subtext)  #Recursively parse nested dict
#                     data[key] = value
#                     str_start = j+1
#                     i = j+1
#                     state = 'key'
#                     continue
#                 i += 1
#             elif state == 'comment':    #Switch state from 'comment' to 'key' with newline
#                 if text[i] == '\n':
#                     str_start = i+1
#                     state = 'key'
#                 i += 1
#         return data
        
#     def write_input(self, *args):
#         if args:
#             file = args[0]
#         else:
#             file = self.input_file
#         if os.path.isfile(os.path.join(self.workdir, self.input_file)):
#             if os.path.samefile(os.path.join(self.workdir, self.input_file), os.path.join(os.getcwd(), self.original_input_file)):
#                 file = file + '_copy'   #Used to not overwrite original input if in same directory
#         self.input_file = file
#         text = self.unpack_dict(self.input_data,'',0)
#         with open(os.path.join(self.workdir, file), 'w') as file:
#             file.write(text)
        
#     def unpack_dict(self, data, text, indent):
#         for key, value in data.items():
#             if isinstance(value, dict): #Recursively unpack nested dict
#                 text += '  '*indent + key + ' : {\n'
#                 text = self.unpack_dict(value, text, indent+1)
#                 text += '  '*indent + '}\n'
#             else:
#                 text += '  '*indent + key + ' : ' + value + '\n'
#         return text

#     def load_output(self):
#         return 'Not implemented.'

#     def format_data(self):
#         return 'Not implemented.'
    
#     def configure(self):
#         return 'Not implemented.'
    
#     def archive(self):
#         return 'Not implemented.'

#     def load_archive(self):
#         return 'Not implemented.'
    
#     def plot(self):
#         return 'Not implemented.'

#     def make_default_input(self):
#         self.input_file = 'default_input.omega3p'   #Not written to a file until write_input is called
#         self.input_data = {'ModelInfo': {
#                                 'File': './mesh_file.ncdf'}, 
#                            'FiniteElement': {
#                                 'Order': '2',
#                                 'CurvedSurfaces': 'on'},
#                            'EigenSolver': {
#                                 'NumEigenvalues': '1',
#                                 'FreqShift': '1.0E9'},
#                            'PostProcess': {
#                                 'Toggle': 'on',
#                                 'ModeFile': 'mode'}
#                           }
