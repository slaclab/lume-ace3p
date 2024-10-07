import os
from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P
from lume_ace3p.acdtool import Acdtool
from lume_ace3p.tools import WriteDataTable

#Define parameters to sweep in lists
cav_rs = [90 + 10*i for i in range(4)]      #Cavity radii in mm (units in cubit journal)
cav_es = [0.5 + 0.25*i for i in range(4)]   #Cavity ellipticity parameter

#Define base working directory for all simulations (each will get its own folder)
base_dir = os.path.join(os.getcwd(),'lume-ace3p_demo_workdir')

#Define the function workflow to evaluate
def sim_function(input_dict):

    #Load working directory from base name + parameters
    sim_dir = input_dict['sim_dir']

    #Create cubit object, parse input file, update values, and then run cubit
    cubit_obj = Cubit('pillbox-rtop.jou',workdir=sim_dir)
    cubit_obj.set_value(input_dict) #Update any values in journal file from input
    cubit_obj.run()
    
    #Create omega3p object, parse input file, and run omega3p
    omega3p_obj = Omega3P('pillbox-rtop.omega3p',workdir=sim_dir)
    omega3p_obj.run()
    
    #Create acdtool object, parse input file, and run acdtool
    acdtool_obj = Acdtool('pillbox-rtop.rfpost',workdir=sim_dir)
    acdtool_obj.run()   #Defaults to 'postprocess rf' command if .rfpost file given
    
    #Create output dict containing desired quantities
    output_dict = {"RoQ": acdtool_obj.output_data['RoverQ']['0']['RoQ'],
                   "Frequency": acdtool_obj.output_data['RoverQ']['0']['Frequency']}
    
    return output_dict

#Sweep through all parameter combinations (single or multiple for-loops)
sim_output = {} #Output dict to store results
for i in range(len(cav_rs)):
    for j in range(len(cav_es)):
        #Create input dict for sim function
        #Note: desired cubit input names must match those in Cubit journal!
        inputs = {'cav_radius': cav_rs[i],
                  'cav_ellipticity': cav_es[j],
                  'sim_dir': base_dir + '_' + str(cav_rs[i]) + '_' + str(cav_es[j])}
        
        #Call sim function for set of inputs
        sim_output[(cav_rs[i],cav_es[j])] = sim_function(inputs)

        #Write data to text file (this will overwrite the file as the sim_output dict grows)
        #(or put WriteDataTable outside of for loop to only write data at end simulations)
        #See src/tools.py for more information on the WriteDataTable function
        WriteDataTable('psweep_output.txt', sim_output, ['Radius','Ellipticity'], ['RoQ','Frequency'])