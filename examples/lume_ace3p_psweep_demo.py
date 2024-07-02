
import os, sys
import pandas as pd
sys.path.append('/sdf/group/rfar')

from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P
from lume_ace3p.acdtool import Acdtool

#Define parameters to sweep in lists
cav_rs = [90 + 10*i for i in range(4)]
cav_es = [0.5 + 0.25*i for i in range(4)]

#Define base working directory for all simulations (each will get its own folder)
base_dir = os.path.join(os.getcwd(),'lume_ace3p_demo_workdir')

#Define the function workflow to evaluate
def sim_function(input_dict):

    workdir = input_dict['workdir']
    cubit_obj = Cubit('pillbox-rtop.jou',workdir=workdir)
    cubit_obj.set_value({'cav_radius':input_dict['cav_radius']})
    cubit_obj.set_value({'cav_ellipticity':input_dict['cav_ellipticity']})
    cubit_obj.run()
    cubit_obj.meshconvert('pillbox-rtop4.gen')
    
    omega3p_obj = Omega3P('pillbox-rtop.omega3p',workdir=workdir)
    omega3p_obj.run()
    
    acdtool_obj = Acdtool('pillbox-rtop.rfpost',workdir=workdir)
    acdtool_obj.run('postprocess rf')
    
    output_dict = {"RoQ": acdtool_obj.output_data['RoverQ']['0']['RoQ'],
                   "Frequency": acdtool_obj.output_data['RoverQ']['0']['Frequency']}
    
    return output_dict

#This is just a quick helper function to pretty-print the output dictionary
def unpack_dict(data, text, indent):
    for key, value in data.items():
        if isinstance(value, dict): #Recursively unpack nested dict
            text += '  '*indent + str(key) + ' : {\n'
            text = unpack_dict(value, text, indent+1)
            text += '  '*indent + '}\n'
        else:
            text += '  '*indent + str(key) + ' : ' + str(value) + '\n'
    return text

#Sweep through all parameter combinations (single or multiple for-loops)
sim_output = {} #Output dict to store results
for i in range(len(cav_rs)):
    sim_output[cav_rs[i]] = {}
    for j in range(len(cav_es)):
        inputs = {'cav_radius': cav_rs[i],
                  'cav_ellipticity': cav_es[j],
                  'workdir': base_dir + '_' + str(cav_rs[i]) + '_' + str(cav_es[j])}
        sim_output[cav_rs[i]][cav_es[j]] = sim_function(inputs)
        text = unpack_dict(sim_output,'',0)
        with open('psweep_output.txt', 'w') as file:
            file.write(text)