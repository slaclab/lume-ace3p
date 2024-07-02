
import os, shutil
import subprocess

from lume.base import CommandWrapper

class Cubit(CommandWrapper):

    MPI_CALLER = 'mpirun'
    ACE3P_PATH = '/sdf/group/rfar/ace3p/bin/'
    CUBIT_PATH = '/sdf/group/rfar/software/Cubit-16.12/'
    COMMAND = 'cubit'
    WORKDIR = os.getcwd()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.workdir is None:
            self.workdir = self.WORKDIR
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        assert self.input_file is not None, 'Cubit object requires input journal file'
        self.original_input_file = self.input_file
        if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
            shutil.copy(self.input_file, self.workdir)
        self.input_parser()
        
    def input_parser(self):    #Read in Cubit input file
        with open(self.input_file) as file:
            self.lines = file.readlines()
        numrows = len(self.lines)    #Number of rows in input file
        self.ncflag = [0] * numrows    #Flag to store non-commented lines
        for i in range(numrows):
            if self.lines[i].strip() != '':    #Ignore empty rows
                if not self.lines[i].startswith('##'):  #Ignore comment rows
                    self.ncflag[i] = i+1
        self.ncflag = [i-1 for i in self.ncflag if i != 0]
        
    def get_value(self, key):  #Read value of first instance of variable
        value = None
        for i in range(len(self.ncflag)):
            line = self.lines[self.ncflag[i]]
            flag = line.replace(' ','').find(key + '=')
            if flag != -1:    #If variable name declaration found in file
                indx = line.find('=')    #Index of '=' sign following name
                if line.strip().startswith('#{'):    #If APREPRO code line
                    value = line[indx+1::].replace('}','')
                else:
                    value = line[indx+1::].strip()
                break
        if flag == -1:
            print('Error: \'' + key + '=\' not found in Cubit file, ' \
                  + 'value \'None\' returned.')
            return None
        else:
            return eval(value)
    
    def set_value(self, kwargs):    #Set value of first instance of variable
        for key, value in kwargs.items():
            for i in range(len(self.ncflag)):
                line = self.lines[self.ncflag[i]]
                flag = line.replace(' ','').find(key + '=')
                if flag != -1:    #If variable name declaration found in file
                    indx = line.find('=')    #Index of '=' sign following name
                    if line.strip().startswith('#{'):    #If APREPRO code line
                        new_line = line[0:indx] + '=' + str(value) + '}\n'
                    else:
                        new_line = line[0:indx] + '=' + str(value) + '\n'
                    self.lines[self.ncflag[i]] = new_line
                    break
            if flag == -1:
                print('Error: \'' + key + '\' not defined in Cubit file, no action taken.')
    
    def set_export(self, name, format_='genesis', opts='overwrite'):
        for i in range(len(self.ncflag)):
            words = self.lines[self.ncflag[i]].split()
            if words[0] == 'export':
                new_line = ['export', format_, '"' + name + '"']
                if opts is not None:
                    if not isinstance(opts, list):
                        opts = [opts]
                    new_line = new_line + [opt for opt in opts]
                self.lines[self.ncflag[i]] = ' '.join(new_line + ['\n'])
                return
        print('Error: no export command found in Cubit journal, no action taken.')
                
    def write_input(self, *args):
        if args:
            file = args[0]
        else:
            file = self.input_file
        if os.path.isfile(os.path.join(self.workdir, self.input_file)):
            if os.path.samefile(os.path.join(self.workdir, self.input_file), os.path.join(os.getcwd(), self.original_input_file)):
                file = file + '_copy'   #Used to not overwrite original input if in same directory
        self.input_file = file
        with open(os.path.join(self.workdir, self.input_file), 'w') as file:
            file.writelines(self.lines)
                
    def run(self):
        self.write_input()
        subprocess.run(self.CUBIT_PATH + self.COMMAND + ' -nographics -nojournal -noecho ' + self.input_file,
                        shell=True, cwd=self.workdir)
                        
    def meshconvert(self, file):
        subprocess.run(self.MPI_CALLER + ' -n 1 -c 1 ' + self.ACE3P_PATH + 'acdtool meshconvert ' + file,
                        shell=True, cwd=self.workdir)
        
    def configure(self):
        return 'Not implemented.'
    
    def archive(self):
        return 'Not implemented.'

    def load_archive(self):
        return 'Not implemented.'
    
    def plot(self):
        return 'Not implemented.'
    
    def load_output(self):
        return 'Not implemented.'