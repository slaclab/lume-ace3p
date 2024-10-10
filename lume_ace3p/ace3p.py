import os, shutil
import subprocess

from lume.base import CommandWrapper

class Omega3P(CommandWrapper):
    
    MPI_CALLER = os.environ['MPI_CALLER']
    ACE3P_PATH = os.environ['ACE3P_PATH']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.workdir is None:
            self.workdir = os.getcwd()
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        self.original_input_file = self.input_file
        if self.input_file is None:
            self.make_default_input()
        if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
            shutil.copy(self.input_file, self.workdir)
        self.output_file = 'omega3p.out'    #Default output file for omega3p
        self.load_input_file()
        
    def run(self,tasks=1,cores=1,opts=''):
        self.write_input()
        result = subprocess.run(self.MPI_CALLER + ' -n ' + tasks + ' -c ' + cores + ' ' + opts + ' ' +
                                + self.ACE3P_PATH + 'omega3p' + ' ' + self.input_file,
                                shell=True, cwd=self.workdir, capture_output=True, text=True)
        with open(os.path.join(self.workdir, self.output_file), 'w') as file:
            file.writelines(result.stdout)
        
    def get_frequencies(self):  #Script to extract frequency modes from stdout
        with open(os.path.join(self.workdir, self.output_file)) as file:
            lines = file.readlines()
        mode_list = []
        freq_list = []
        for line in lines:
            if line.startswith('COMMIT MODE:'):
                subline = line.strip('COMMIT MODE: ').split()
                mode_list.append(eval(subline[0]))
                freq_list.append(eval(subline[3]))
        self.frequencies = dict(zip(mode_list,freq_list))
        
    def load_input_file(self, *args):
        if args:
            self.input_file = args[0]
        with open(self.input_file) as file:
            text = file.read()
        self.input_data = self.input_parser(text)
        
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
        text = self.unpack_dict(self.input_data,'',0)
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

    def make_default_input(self):
        self.input_file = 'default_input.omega3p'   #Not written to a file until write_input is called
        self.input_data = {'ModelInfo': {
                                'File': './mesh_file.ncdf'}, 
                           'FiniteElement': {
                                'Order': '2',
                                'CurvedSurfaces': 'on'},
                           'EigenSolver': {
                                'NumEigenvalues': '1',
                                'FreqShift': '1.0E9'},
                           'PostProcess': {
                                'Toggle': 'on',
                                'ModeFile': 'mode'}
                          }
        
        
        
        