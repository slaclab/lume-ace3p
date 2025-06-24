import os, shutil
import subprocess
import numpy as np

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
        if self.ace3p_opts.startswith('--cpu-bind') and self.MPI_CALLER is not 'srun':
            self.ace3p_opts = ''
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        self.original_input_file = self.input_file
        if self.input_file is None:
            self.make_default_input()
        if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
            shutil.copy(self.input_file, self.workdir)
        self.load_input_file()

    def run(self):
        self.write_input()
        subprocess.run(self.MPI_CALLER + ' -n ' + str(self.ace3p_tasks) + ' -c ' + str(self.ace3p_cores) + ' ' + self.ace3p_opts + ' '
                                + self.ACE3P_PATH + self.module_name + ' ' + self.input_file,
                                shell=True, cwd=self.workdir)
        self.output_parser()

    def load_input_file(self, *args):
        if args:
            self.input_file = args[0]
        with open(self.input_file) as file:
            text = file.read()
        #self.input_data = self.input_parser(text) #DUMMIED OUT FOR COMPATIBILITY
        self.input_data = text
    
    def input_parser(self, text):
        data = {}
        key, value  = None, None
        i, str_start, str_end = 0, 0, 0
        state = 'key'   #State flag to switch between 'key', 'value', and 'comment' text
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
                    value = self.input_parser(subtext)  #Recursively parse nested dict
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

    def write_input(self, *args):
        if args:
            file = args[0]
        else:
            file = self.input_file
        if os.path.isfile(os.path.join(self.workdir, self.input_file)):
            if os.path.samefile(os.path.join(self.workdir, self.input_file), os.path.join(os.getcwd(), self.original_input_file)):
                file = file + '_copy'   #Used to not overwrite original input if in same directory
        self.input_file = file
        #text = self.unpack_dict(self.input_data,'',0) #DUMMIED OUT FOR COMPATIBILITY
        text = self.input_data
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
