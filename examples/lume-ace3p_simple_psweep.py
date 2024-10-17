import os

from lume_ace3p.workflow import Omega3PWorkflow

#Define parameters to sweep in lists
cav_radii = [90 + 10*i for i in range(4)]      #Cavity radii in mm (units in cubit journal)
ellipticities = [0.5 + 0.25*i for i in range(4)]   #Cavity ellipticity parameter

workflow_dict = {'cubit_input': 'pillbox-rtop.jou',
                 'omega3p_input': 'pillbox-rtop.omega3p',
                 'omega3p_tasks': 4,
                 'omega3p_cores': 4,
                 'rfpost_input': 'pillbox-rtop.rfpost',
                 'workflow_mode': 'auto',
                 'workdir': os.path.join(os.getcwd(),'lume-ace3p_demo_workdir'),
                 'sweep_output': 'psweep_output.txt',
                 'autorun': False}

input_dict = {'cav_radius': cav_radii,
              'ellipticity': ellipticities}

output_dict = {'Output_1': {'RoverQ': {'0': {'RoQ'}}},
               'Output_2': {'RoverQ': {'0': {'Frequency'}}}}

Omega3PWorkflow(workflow_dict, input_dict, output_dict)
Omega3PWorkflow.run_sweep(input_dict)
Omega3PWorkflow.print_sweep_output()

# #Define base working directory for all simulations (each will get its own folder)
# my_base_dir = os.path.join(os.getcwd(),'lume-ace3p_demo_workdir')

# #Define the function workflow to evaluate
# def workflow_function(input_dict):

#     #Load working directory from base name + parameters
#     sim_dir = input_dict['workflow_dir']

#     #Create cubit object, parse input file, update values, and then run cubit
#     cubit_obj = Cubit('pillbox-rtop.jou',workdir=sim_dir)
#     cubit_obj.set_value(input_dict) #Update any values in journal file from input
#     cubit_obj.run()
    
#     #Create omega3p object, parse input file, and run omega3p
#     omega3p_obj = Omega3P('pillbox-rtop.omega3p',workdir=sim_dir)
#     omega3p_obj.run()
    
#     #Create acdtool object, parse input file, and run acdtool
#     acdtool_obj = Acdtool('pillbox-rtop.rfpost',workdir=sim_dir)
#     acdtool_obj.run()   #Defaults to 'postprocess rf' command if .rfpost file given
    
#     #Create output dict containing desired quantities
#     #The acdtool_obj.output_data is a nested dictionary generally of the form:
#     #  output_data['RoverQ']['modeID']['ColumnName']
#     #  output_data['maxFieldsOnSurface']['SurfaceID']['ModeID']['ColumnName']
#     output_dict = {"RoQ": acdtool_obj.output_data['RoverQ']['0']['RoQ'],
#                    "Frequency": acdtool_obj.output_data['RoverQ']['0']['Frequency']}
    
#     return output_dict

# #Sweep through all parameter combinations (single or multiple for-loops)
# sim_output = {} #Output dict to store results#
# for i in range(len(input1)):
#     for j in range(len(input2)):
#         #Create input dict for sim function
#         #Note: desired cubit input names must match those in Cubit journal!
#         inputs = {'cav_radius': input1[i],
#                   'cav_ellipticity': input2[j],
#                   'workflow_dir': my_base_dir + '_' + str(input1[i]) + '_' + str(input2[j])}
        
#         #Call sim function for set of inputs
#         sim_output[(input1[i],input2[j])] = workflow_function(inputs)

#         #Write data to text file (this will overwrite the file as the sim_output dict grows)
#         #(or put WriteDataTable outside of for loop to only write data at end simulations)
#         #See src/tools.py for more information on the WriteDataTable function
#         WriteDataTable('psweep_output.txt', sim_output, ['Radius','Ellipticity'], ['RoQ','Frequency'])