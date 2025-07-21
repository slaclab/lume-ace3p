from xopt.vocs import VOCS
from xopt.evaluator import Evaluator
from xopt.generators.bayesian import ExpectedImprovementGenerator
from xopt import Xopt
from lume_ace3p.workflow import S3PWorkflow
from lume_ace3p.tools import WriteXoptData

#Define workflow dictionary with input files, directory options, etc.
workflow_dict = {'cubit_input': 'bend-90degree.jou',
                 'ace3p_input': 'bend-90degree.s3p',
                 'ace3p_tasks': 16,
                 'ace3p_cores': 16,
                 'ace3p_opts' : '--cpu-bind=cores',
                 'workdir': 'lume-ace3p_xopt_workdir'}

#Define variables and function objectives/constraints/observables
vocs = VOCS(
    variables={"rcorner1": [1,1.1], "rcorner2": [4.9,5]},
    objectives={"S(0,0)": "MAXIMIZE"}
)

#Define simulation function for xopt (based on workflow w/ postprocessing)
def sim_function(input_dict):

    #Create workflow object and run with provided inputs
    workflow = S3PWorkflow(workflow_dict,input_dict)
    output_data = workflow.run()

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