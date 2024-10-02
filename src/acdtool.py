
import os, shutil
import subprocess

from lume.base import CommandWrapper

class Acdtool(CommandWrapper):
    
    MPI_CALLER = os.environ['MPI_CALLER']
    ACE3P_PATH = os.environ['ACE3P_PATH']
    WORKDIR = os.getcwd()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.workdir is None:
            self.workdir = self.WORKDIR
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        self.original_input_file = self.input_file
        if self.input_file is None:
            self.make_default_input()
        if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
            shutil.copy(self.input_file, self.workdir)
        self.load_input_file()
        
    def run(self, *args):
        if args[0] == 'postprocess rf':
            acdcommand = 'postprocess rf'
            self.output_file = 'rfpost.out' #Default filename for output of postprocess rf
        else:
            print('Error: Unknown acdtool command.')
            return
        self.write_input()
        result = subprocess.run(self.MPI_CALLER + ' -n 1 -c 1 ' 
                                + self.ACE3P_PATH + 'acdtool '
                                + acdcommand + ' ' + self.input_file,
                                shell=True, cwd=self.workdir)
        self.load_output()
        
    def load_input_file(self, *args):
        if args:
            self.input_file = args[0]
        self.input_parser()
        
    def input_parser(self):
        with open(os.path.join(self.workdir, self.input_file)) as file:
            lines = file.readlines()
        self.input_data = {}  #Create base-level dict for base-level keys
        key1 = None
        for i in range(len(lines)):
            line = lines[i].strip()
            if len(line) == 0:   #Skip empty lines
                continue
            elif line.startswith('{'):   #Create dict for the base entry
                key1 = lines[i-1].strip()
                assert len(key1) > 0   #Check that base entry name in line before '{'
                self.input_data[key1] = {}
                continue
            elif line.startswith('}'):   #Set key1 to None when not in a base entry
                key1 = None
                continue
            if key1 is not None:  #Within base entry, add key-value pairs to dict
                if '=' in line:
                    split_line = line.split('=')
                    key2 = split_line[0].strip()
                    value = split_line[1].split('//')[0].strip()
                    self.input_data[key1][key2] = value

    def write_input(self, *args):
        if args:
            file = args[0]
        else:
            file = self.input_file
        if os.path.isfile(os.path.join(self.workdir, self.input_file)):
            if os.path.samefile(os.path.join(self.workdir, self.input_file), os.path.join(os.getcwd(), self.original_input_file)):
                file = file + '_copy'   #Used to not overwrite original input if in same directory
        self.input_file = file
        lines = []
        for key1 in self.input_data.keys():
            lines.append(key1 + '\n{\n')
            for key2, value2 in self.input_data[key1].items():
                lines.append('   ' + key2 + ' = ' + value2 + '\n')
            lines.append('}\n\n')
        with open(os.path.join(self.workdir, self.input_file), 'w') as file:
            file.writelines(lines)

    def load_output(self, *args):
        if args:
            self.output_file = args[0]
        if self.output_file is None:
            print('No output file found to load.')
            return
        self.output_data = {}
        if self.input_data is not None:
            for key in self.input_data.keys():  #Scan for keys in input file
                if 'ionoff' in self.input_data[key].keys():
                    if eval(self.input_data[key]['ionoff']):
                        self.output_parser(key) #Only parse flagged key containers

    def output_parser(self, key):
        with open(os.path.join(self.workdir, self.output_file)) as file:
            lines = file.readlines()
        keyword = '[' + key + ']'
        start_ind = 0
        end_ind = 0
        for i in range(len(lines)): #Locate line with key in square [] brackets
            if lines[i].startswith(keyword):
                start_ind = i+1
            if lines[i].startswith('}') and start_ind > 0:
                end_ind = i
                break
        if end_ind == 0:
            print('Data key \"' + key + '\" not found in output file.')
            return
        self.output_data[key] = {}
        datalines = lines[start_ind:end_ind]    #Lines inside curly braces {} after [key]
        if key == 'RoverQ':
            modestart = 0
            for i in range(len(datalines)):
                if datalines[i].strip().startswith('ModeID'):
                    modestart = i+1
                    break
            modedata = datalines[modestart:end_ind]
            modelist = []
            for j in range(len(modedata)):
                modeline = modedata[j].split()
                mkey = modeline[0]  #Mode ID number
                modelist.append(mkey)
                self.output_data[key][mkey] = {}
                self.output_data[key][mkey]['Frequency'] = eval(modeline[1])
                self.output_data[key][mkey]['Qext'] = eval(modeline[2])
                self.output_data[key][mkey]['V_r'] = eval(modeline[3].strip(','))
                self.output_data[key][mkey]['V_i'] = eval(modeline[4])
                self.output_data[key][mkey]['absV'] = eval(modeline[5])
                self.output_data[key][mkey]['RoQ'] = eval(modeline[6])
            self.output_data[key]['ModeIDs'] = modelist
        elif key == 'maxFieldsOnSurface':
            surfacelist = []
            modelist = []
            for i in range(len(datalines)):
                if datalines[i].strip().startswith('surfaceID'):
                    skey = datalines[i].split(':')[1].strip()   #Surface ID number
                    if skey not in surfacelist:
                        surfacelist.append(skey)
                        self.output_data[key][skey] = {}
                    mkey = datalines[i+1].split(':')[1].strip() #Mode ID number
                    if mkey not in modelist:
                        modelist.append(mkey)
                    Emax = eval(datalines[i+2].split()[2])  #Emax units are V*m
                    Emax_loc = eval(datalines[i+2].split('at')[1])  #3-tuple of (x,y,z) coordinates
                    Hmax = eval(datalines[i+3].split()[2])  #Hmax units are A/m
                    Hmax_loc = eval(datalines[i+3].split('at')[1])  #3-tuple of (x,y,z) coordinates
                    self.output_data[key][skey][mkey] = {}
                    self.output_data[key][skey][mkey]['Emax'] = Emax
                    self.output_data[key][skey][mkey]['Emax_location'] = Emax_loc
                    self.output_data[key][skey][mkey]['Hmax'] = Hmax
                    self.output_data[key][skey][mkey]['Hmax_location'] = Hmax_loc
            self.output_data[key]['SurfaceIDs'] = surfacelist
            self.output_data[key]['ModeIDs'] = modelist
        else:
            print('Data key \"' + key + '\" parsing not implemented.')

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
        self.input_file = 'default_input.rfpost'    #Not written to a file until write_input is called
        self.input_data = {'RFField' : {
                                'ResultDir' : 'omega3p_results',
                                'FreqScanID' : '0',
                                'ModeID' : '0',
                                'xsymmetry' : 'none',
                                'ysymmetry' : 'none',
                                'gradient' : '2.00000e+07',
                                'cavityBeta' : '1.00000',
                                'reversePowerFlow' : '0',
                                'x0' : '0.00000',
                                'y0' : '0.00000',
                                'gz1' : '-0.05700',
                                'gz2' : '0.05700',
                                'npoint' : '300',
                                'fmnx' : '10',
                                'fmny' : '10',
                                'fmnz' : '50'},
                           'RoverQ' : {
                                'ionoff' : '1',
                                'modeID1' : '-1',
                                'modeID2' : '-1',
                                'x1' : '0.00000',
                                'x2' : '0.00100',
                                'y1' : '0.00100',
                                'y2' : '0.00100',
                                'z1' : '1.00000e+10',
                                'z2' : '1.00000e+10'}
                          }