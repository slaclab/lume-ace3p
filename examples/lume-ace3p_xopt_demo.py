from xopt.vocs import VOCS
from xopt.evaluator import Evaluator
from xopt.generators.bayesian import ExpectedImprovementGenerator
from xopt import Xopt
from lume_ace3p.workflow import Omega3PWorkflow
from lume_ace3p.tools import WriteXoptData

#Define workflow dictionary with input files, directory options, etc.
workflow_dict = {'cubit_input': 'pillbox-rtop.jou',
                 'omega3p_input': 'pillbox-rtop.omega3p',
                 'omega3p_tasks': 16,
                 'omega3p_cores': 16,
                 'omega3p_opts' : '--cpu-bind=cores',
                 'rfpost_input': 'pillbox-rtop.rfpost',
                 'workdir': 'lume-ace3p_xopt_workdir'}

#Define output dictionary with data to extract from acdtool
#  Keywords can be any user-provided string (will be used for column names in output)
#  Values are lists of strings of the form [section_name, mode/surface_id, column_name]
output_dict = {'R/Q': ['RoverQ', '0', 'RoQ'],
               'mode_freq': ['RoverQ', '0', 'Frequency']}

#Define variables and function objectives/constraints/observables
vocs = VOCS(
    variables={"cav_radius": [95, 105], "ellipticity": [0.5, 1.2]},
    objectives={"R/Q": "MAXIMIZE"},
    constraints={"freq_error" : ["LESS_THAN", 0.0001]},
    observables=["mode_freq"]
)

#Define a target frequency (to optimize towards)
target_freq = 1.3e9

#Define simulation function for xopt (based on workflow w/ postprocessing)
def sim_function(input_dict):

    #Create workflow object and run with provided inputs
    workflow = Omega3PWorkflow(workflow_dict,input_dict,output_dict)
    output_data = workflow.run()

    #Add postprocessing for constraint calculation to output_data dict
    output_data['freq_error'] = (output_data['mode_freq']-target_freq)**2/target_freq**2

    return output_data

#Create Xopt evaluator, generator, and Xopt objects
evaluator = Evaluator(function=sim_function)
generator = ExpectedImprovementGenerator(vocs=vocs)
X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)

#Run X.random_evaluate() to generate + evaluate a few initial points
for i in range(5):
    X.random_evaluate()
    WriteXoptData('sim_output.txt',X)

#Run optimization for subsequent steps
for i in range(15):
    X.step()
    WriteXoptData('sim_output.txt',X)