import os
from xopt.vocs import VOCS
from xopt.evaluator import Evaluator
from xopt.generators.bayesian import ExpectedImprovementGenerator
from xopt import Xopt
from src.cubit import Cubit
from src.ace3p import Omega3P
from src.acdtool import Acdtool
from src.tools import WriteXoptData

#Define variables and function objectives/constraints/observables
vocs = VOCS(
    variables={"cav_radius": [95, 105], "cav_ellipticity": [0.5, 1.2]},
    objectives={"RoQ": "MAXIMIZE", "delta_f": "MINIMIZE"},
    constraints={},
    observables=["Frequency"]
)

#Define working directory for all simulations
sim_dir = os.path.join(os.getcwd(),'xopt_ace3p_demo_workdir')

#Define the function workflow to optimize
def sim_function(input_dict):

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
    #Note: target frequency is 1.30e9 Hz so that delta_f = abs(Frequency - 1.30e9)
    output_dict = {"delta_f": abs(acdtool_obj.output_data['RoverQ']['0']['Frequency']-1.30e9),
                   "RoQ": acdtool_obj.output_data['RoverQ']['0']['RoQ'],
                   "Frequency": acdtool_obj.output_data['RoverQ']['0']['Frequency']}
    
    return output_dict

#Create Xopt evaluator, generator, and Xopt objects
evaluator = Evaluator(function=sim_function)
generator = ExpectedImprovementGenerator(vocs=vocs)
X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)

#Run X.random_evaluate() to generate + evaluate a few initial points
for i in range(5):
    X.random_evaluate()
    WriteXoptData('sim_output.txt',X)

#Run optimization for many steps
for i in range(5):
    X.step()
    WriteXoptData('sim_output.txt',X)