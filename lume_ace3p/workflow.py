import os
import numpy as np

from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P
from lume_ace3p.acdtool import Acdtool

class Omega3PWorkflow(__dict__):
    
    def __init__(self, workflow_dict, *args):
        if args:
            input_dict = args[0]
            if len(args)>1:
                output_dict = args[1]
        self.cubit_input = workflow_dict.get('cubit_input')
        self.omega3p_input = workflow_dict.get('omega3p_input')
        self.omega3p_tasks = workflow_dict.get('omega3p_tasks',1)
        self.omega3p_cores = workflow_dict.get('omega3p_cores',1)
        self.omega3p_opts = workflow_dict.get('omega3p_opts','')
        self.acdtool_input = workflow_dict.get('acdtool_input')
        self.workdirmode = workflow_dict.get('workdirmode','manual')
        self.baseworkdir = workflow_dict.get('workdir',os.getcwd())
        self.autorun =  workflow_dict.get('autorun',True)
        self.output_data = {}
        if self.autorun:
            self.run(input_dict)
            self.evaluate(output_dict)
            return self.output_data

    def getworkdir(self, input_dict=None):
        if self.workdirmode == 'manual':
            self.workdir = self.baseworkdir
        elif self.workdirmode == 'auto':
            name_str = ''
            if input_dict is not None:
                for key in input_dict.keys():
                    if isinstance(input_dict[key], list):
                        value = input_dict[key][0]
                    else:
                        value = input_dict[key]
                    name_str = name_str + '_' + str(value)
            if self.workdir is None:
                self.workdir = 'workflow_output' + name_str
            else:
                self.workdir = self.baseworkdir + name_str

    def run(self, input_dict=None):
        self.getworkdir(input_dict)

        if self.cubit_input is not None:
            self.cubit_obj = Cubit(self.cubit_input,
                              workdir=self.workdir)
            if input_dict is not None:
                self.cubit_obj.set_value(input_dict)
            self.cubit_obj.run()

        if self.omega3p_input is not None:
            self.omega3p_obj = Omega3P(self.omega3p_input,
                                  tasks=self.omega3p_tasks,
                                  cores=self.omega3p_cores,
                                  workdir=self.workdir)
            self.omega3p_obj.run()

        if self.acdtool_input is not None:
            self.acdtool_obj = Acdtool(self.acdtool_input,
                                  workdir=self.workdir)
            self.acdtool_obj.run()

    def evaluate(self, output_dict):
        if self.acdtool_obj is not None:
            for output_name in output_dict.keys():
                for section in output_dict[output_name].keys():
                    if section == 'RoverQ':
                        for mode in output_dict[output_name][section].keys():
                            for entry in output_dict[output_name][section][mode].keys():
                                self.output_data[output_name] = self.acdtool_obj.output_data[output_name][section][mode][entry]
                    if section == 'maxFieldsOnSurface':
                        for surface in output_dict[output_name][section].keys():
                            for mode in output_dict[output_name][section][surface].keys():
                                for entry in output_dict[output_name][section][surface][mode].keys():
                                    self.output_data[output_name] = self.acdtool_obj.output_data[output_name][section][surface][mode][entry]

    def run_sweep(self, input_dict):
        input_varname = []  #List of input parameter names
        input_vardim = []   #List of vector lengths for each parameter
        input_vardata = []  #List of numpy array vectors of parameters
        
        #Unpack dict of inputs into lists
        var_id = 0
        for var, value in input_dict.items():
            input_varname[var_id] = var
            input_vardim[var_id] = len(value)
            input_vardata[var_id] = np.array(value)
            var_id += 1

        #Build a full tensor product of all combinations of parameters
        #   If input_dict has 3 parameters with vectors of length 10, 20, and 30
        #   Then input_tensor is a 6000 x 3 array of all combinations from the 3 parameters
        input_tensor = input_vardata[0]         #First parameter vector
        if len(input_varname) > 1:
            t1 = np.tile(input_tensor,input_vardim[1])
            t2 = np.repeat(input_vardata[0],input_vardim[0])
            input_tensor = np.vstack([t1,t2]).T #Cartesian tensor product of first 2 parameter vectors
            if len(input_varname) > 2:
                for i in range(2,len(input_varname)):
                    t1 = np.tile(input_tensor,(input_vardim[i],1))
                    t2 = np.repeat(input_vardata[i],len(t1))
                    input_tensor = np.vstack([t1.T,t2]).T   #Recursive tensor product of 1st-nth parameter tensor array with (n+1)st parameter vector

        for i in len(range(np.size(input_tensor,0))):
            sweep_dict = {}
            for j in range(len(input_varname)):
                sweep_dict[input_varname[j]] = input_tensor[i][j]

            self.run(sweep_dict)