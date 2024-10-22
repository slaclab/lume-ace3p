import os
import numpy as np

from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P
from lume_ace3p.acdtool import Acdtool
from lume_ace3p.tools import WriteDataTable

class Omega3PWorkflow:
    
    def __init__(self, workflow_dict, input_dict=None, output_dict=None):
        self.input_dict = input_dict
        self.output_dict = output_dict
        self.cubit_input = workflow_dict.get('cubit_input')
        self.omega3p_input = workflow_dict.get('omega3p_input')
        self.omega3p_tasks = workflow_dict.get('omega3p_tasks',1)
        self.omega3p_cores = workflow_dict.get('omega3p_cores',1)
        self.omega3p_opts = workflow_dict.get('omega3p_opts','')
        self.rfpost_input = workflow_dict.get('rfpost_input')
        self.workdir_mode = workflow_dict.get('workdir_mode','manual')
        self.baseworkdir = workflow_dict.get('workdir',os.getcwd())
        self.sweep_output_file = workflow_dict.get('sweep_output_file')
        self.sweep_output = workflow_dict.get('sweep_output',False)
        self.autorun = workflow_dict.get('autorun',True)
        if self.autorun:
            assert self.input_dict is not None, 'Input dictionary required for autorun mode'
            assert self.output_dict is not None, 'Output dictionary required for autorun mode'
            self.run(self.input_dict)
            return self.evaluate(self.output_dict)

    def getworkdir(self, input_dict):
        if self.workdir_mode == 'manual':
            self.workdir = self.baseworkdir
        elif self.workdir_mode == 'auto':
            name_str = ''
            if input_dict is not None:
                for key, value in input_dict.items():
                    if len(value)>1:
                        raise ValueError('Workflow cannot use \'.run()\' with non-scalar input dictonaries, use \'.run_sweep()\' instead.')
                    value = input_dict[key]
                    name_str = name_str + '_' + str(value)
                if self.baseworkdir is None:
                    self.workdir = 'lume-ace3p_workflow_output' + name_str
                else:
                    self.workdir = self.baseworkdir + name_str
            else:
                self.workdir = self.baseworkdir
        else:
            raise ValueError("Key: \'workdir_mode\' must be either \'manual\' or \'auto\'.")
            

    def run(self, input_dict=None):
        if input_dict is None:
            input_dict = self.input_dict
        self.getworkdir(input_dict)

        #Load Cubit journal, update values, and run
        if self.cubit_input is not None:
            self.cubit_obj = Cubit(self.cubit_input,
                              workdir=self.workdir)
            if input_dict is not None:
                self.cubit_obj.set_value(input_dict)
            self.cubit_obj.run()

        #Load Omega3P input and run
        if self.omega3p_input is not None:
            self.omega3p_obj = Omega3P(self.omega3p_input,
                                  tasks=self.omega3p_tasks,
                                  cores=self.omega3p_cores,
                                  workdir=self.workdir)
            self.omega3p_obj.run()

        #Load acdtool rfpost input and run
        if self.rfpost_input is not None:
            self.acdtool_obj = Acdtool(self.rfpost_input,
                                  workdir=self.workdir)
            self.acdtool_obj.run()

    def evaluate(self, output_dict):
        #Read acdtool postprocess RF output and return values referenced in output_dict
        self.output_data = {}
        if output_dict is not None:
            if self.acdtool_obj is not None:
                for output_name, output_params in output_dict.items():
                    section = output_params[0]
                    if section == 'RoverQ':
                        mode = output_params[1]
                        entry = output_params[2]
                        self.output_data[output_name] = self.acdtool_obj.output_data[section][mode][entry]
                    if section == 'maxFieldsOnSurface':
                        surface = output_params[1]
                        entry = output_params[2]
                        self.output_data[output_name] = self.acdtool_obj.output_data[section][surface][entry]
        return self.output_data

    def run_sweep(self, input_dict=None, output_dict=None):
        if input_dict is None:
            input_dict = self.input_dict
        if output_dict is None:
            output_dict = self.output_dict
        self.input_varname = []     #List of input parameter names
        self.input_vardim = []      #List of vector lengths for each parameter
        self.input_vardata = []     #List of numpy array vectors of parameters
        self.output_varname = []    #List of output parameter names
        self.sweep_data = {}        #Dict to store parameter sweep data
        
        #Unpack dict of inputs into lists
        for var, value in input_dict.items():
            self.input_varname.append(var)
            self.input_vardim.append(len(value))
            self.input_vardata.append(np.array(value))
        
        for var in output_dict.keys():
            self.output_varname.append(var)

        #Build a full tensor product of all combinations of parameters
        #   If input_dict has 3 parameters with vectors of length 10, 20, and 30
        #   Then input_tensor is a 6000 x 3 array of all combinations from the 3 parameters
        self.input_tensor = self.input_vardata[0]         #First parameter vector
        if len(self.input_varname) > 1:
            t1 = np.tile(self.input_tensor,self.input_vardim[1])
            t2 = np.repeat(self.input_vardata[1],self.input_vardim[0])
            self.input_tensor = np.vstack([t1,t2]).T #Cartesian tensor product of first 2 parameter vectors
            if len(self.input_varname) > 2:
                for i in range(2,len(self.input_varname)):
                    t1 = np.tile(self.input_tensor,(self.input_vardim[i],1))
                    t2 = np.repeat(self.input_vardata[i],np.size(self.input_tensor,0))
                    self.input_tensor = np.vstack([t1.T,t2]).T   #Recursive tensor product of 1st-nth parameter tensor array with (n+1)st parameter vector
        
        for i in range(np.size(self.input_tensor,0)):
            sweep_input_dict = {}
            sweep_input_tuple = tuple(self.input_tensor[i])
            for j in range(len(self.input_varname)):
                sweep_input_dict[self.input_varname[j]] = self.input_tensor[i][j]
            self.run(sweep_input_dict)
            self.sweep_data[sweep_input_tuple] = self.evaluate(output_dict)
            if self.sweep_output:
                self.print_sweep_output()

    def print_sweep_output(self, filename=None):
        if filename is None:
            filename = self.sweep_output_file
            if self.sweep_output_file is None:
                return
        if len(self.input_varname) == 0:
            return 
        WriteDataTable(filename, self.sweep_data, self.input_varname, self.output_varname)