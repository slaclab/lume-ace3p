from xopt.vocs import VOCS
from xopt.evaluator import Evaluator
from xopt.generators.bayesian import ExpectedImprovementGenerator
from xopt import Xopt
import os, sys
import pandas as pd
sys.path.append('/sdf/group/rfar')

from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P
from lume_ace3p.acdtool import Acdtool

#Define variables and function objectives/constraints/observables
vocs = VOCS(
    variables={"cav_radius": [95, 105], "cav_ellipticity": [0.5, 1.2]},
    objectives={"RoQ": "MAXIMIZE"},
    constraints={"delta_f": ["LESS_THAN", 2.0e6]},
    observables=["Frequency"]
)

#Define working directory for all simulations
workdir = os.path.join(os.getcwd(),'xopt_ace3p_demo_workdir')

#Define the function workflow to optimize
def sim_function(input_dict):

    cubit_obj = Cubit('pillbox-rtop.jou',workdir=workdir)
    cubit_obj.set_value({'cav_radius':input_dict['cav_radius']})
    cubit_obj.set_value({'cav_ellipticity':input_dict['cav_ellipticity']})
    cubit_obj.run()
    cubit_obj.meshconvert('pillbox-rtop4.gen')
    
    omega3p_obj = Omega3P('pillbox-rtop.omega3p',workdir=workdir)
    omega3p_obj.run()
    
    acdtool_obj = Acdtool('pillbox-rtop.rfpost',workdir=workdir)
    acdtool_obj.run('postprocess rf')
    
    output_dict = {"delta_f": abs(acdtool_obj.output_data['RoverQ']['0']['Frequency']-1.30e9),
                   "RoQ": acdtool_obj.output_data['RoverQ']['0']['RoQ'],
                   "Frequency": acdtool_obj.output_data['RoverQ']['0']['Frequency']}
    
    return output_dict

#Create Xopt evaluator, generator, and Xopt objects
evaluator = Evaluator(function=sim_function)
generator = ExpectedImprovementGenerator(vocs=vocs)
X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)

#Set write options to capture entire X.data print output
pd.set_option("display.max_rows", 1000000)
pd.set_option("display.max_colwidth", 1000000)
pd.set_option("expand_frame_repr", False)

#Run X.random_evaluate() to generate + evaluate a few initial points
for i in range(5):
    X.random_evaluate()
    with open('sim_output.txt','w') as file:
        print(X.data, file=file)

#Run optimization for many steps
for i in range(5):
    X.step()
    with open('sim_output.txt','w') as file:
        print(X.data, file=file)