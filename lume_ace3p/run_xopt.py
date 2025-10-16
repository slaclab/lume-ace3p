import numpy as np
import pandas as pd

from xopt.vocs import VOCS
from xopt.evaluator import Evaluator
from xopt import Xopt
from lume_ace3p.workflow import S3PWorkflow
from lume_ace3p.tools import WriteXoptData, WriteS3PDataTable
    
def run_xopt(workflow_dict, vocs_dict, xopt_dict):
    if isinstance(vocs_dict['objectives']['s_parameter'],list):
        S_params = vocs_dict['objectives']['s_parameter']
        freqs = vocs_dict['objectives']['frequency']
        opts = vocs_dict['objectives']['optimization']

        if 'tolerance' in vocs_dict['objectives'].keys():
            tols = vocs_dict['objectives']['tolerance']
            checking_tols = True
        else:
            checking_tols = False

    #if there is a single objective, make sure to save parameters as lists for compatibility
    else:
        S_params = [vocs_dict['objectives']['s_parameter']]
        freqs = [vocs_dict['objectives']['frequency']]
        opts = [vocs_dict['objectives']['optimization']]

        if 'tolerance' in vocs_dict['objectives'].keys():
            tols = [vocs_dict['objectives']['tolerance']]
            checking_tols = True
        else:
            checking_tols = False


    #param_and_freq is a dictionary that contains a single keyword for each quantity to be optimized, paired with its optimization keyword
    #example: {'S(0,0)_9.494e9': 'MINIMIZE'}
    param_and_freq = {}
    for i in range(len(S_params)):
        param_and_freq[S_params[i]+'_'+str(freqs[i])] = opts[i]

    #Define variables and function objectives/constraints/observables
    vocs = VOCS(variables=vocs_dict['variables'], objectives=param_and_freq, constraints=vocs_dict['constraints'], observables=vocs_dict['observables'])

    iteration_index = 0
    if checking_tols:
        tol_achieved = False

    #Define simulation function for xopt (based on workflow w/ postprocessing)
    def sim_function(input_dict):
        #Create workflow object and run with provided inputs
        if 'fidelity_variable' in xopt_dict.keys():
            fidelity_variable = xopt_dict.get('fidelity_variable','s')
            input_dict[fidelity_variable] = input_dict.pop('s')
        workflow = S3PWorkflow(workflow_dict,input_dict)
        output_data = workflow.run()
        param_values = ()
        param_list = []
        for key in input_dict:
            param_list.append(key)
            param_values = param_values + (input_dict[key],)
        #this puts the output data in the sweep format needed to run WriteS3PDataTable
        modified_output_data = {param_values: output_data}
        #appends data to a file containing information about all frequencies and S parameters for every parameter combination
        WriteS3PDataTable('sim_output_all_values.txt', modified_output_data, param_list, True, iteration_index)

        output_dict = {}
        freq_index = 0

        for f in range(len(freqs)):
            try:
                freq_index = list(output_data['Frequency']).index(freqs[f])
            except ValueError:
                print("Inputted frequency to be optimized is not in frequency sweep.")

            #example: output_dict['S(0,0)_9.494e9'] = output_data['S(0,0)'][0]
            output_dict[S_params[f]+'_'+str(freqs[f])] = output_data[S_params[f]][freq_index]
            if checking_tols:
                #sets tolerance achieved boolean to true if one parameter satisfies condition
                if output_data[S_params[f]][freq_index] <= tols[f]:
                    tol_achieved = True
                else:
                    tol_achieved = False
                    #this prevents tolerance from counting as True unless all parameters satisfy their tolerances
                    break

        return output_dict

    #Create Xopt evaluator, generator, and Xopt objects
    evaluator = Evaluator(function=sim_function)
    generator = None
    if xopt_dict['generator'] == 'NelderMeadGenerator':
        from xopt.generators.scipy.neldermead import NelderMeadGenerator
        generator = NelderMeadGenerator(vocs=vocs)
    elif xopt_dict['generator'] == 'ExpectedImprovementGenerator':
        from xopt.generators.bayesian import ExpectedImprovementGenerator
        generator = ExpectedImprovementGenerator(vocs=vocs)
    elif xopt_dict['generator'] == 'MultiFidelityGenerator':
        from xopt.generators.bayesian import MultiFidelityGenerator
        generator = MultiFidelityGenerator(vocs=vocs)
        generator.gp_constructor.use_low_noise_prior = True
        cost_function = xopt_dict.get('cost_function', 'exponential')
        if cost_function.lower() == 'exponential':
            p1 = xopt_dict.get('cost_function_p1', 2.0)
            generator.cost_function = lambda s: np.exp(s * np.log(p1))
        else:
            print("Cost function type: '" + cost_function + "' not supported.")
            return 0
    else:
        print("That generator is not supported. Ensure that the generator name specified in the yaml file matches exactly with the Xopt generator name of choice. Exiting the program.")
        return 0
    X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)
    
    if 'num_random' in xopt_dict.keys() and 'cost_budget' not in xopt_dict.keys():
        #Run X.random_evaluate() to generate + evaluate a few initial points
        for i in range(xopt_dict['num_random']):
            X.random_evaluate()
            #writes an output file with information only about S parameter and frequency of interest
            WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)
            iteration_index += 1

    if 'num_step' in xopt_dict.keys():
        #Run optimization for subsequent steps
        for i in range(xopt_dict['num_step']):
            X.step()
            #writes an output file with information only about S parameter and frequency of interest
            WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)
            iteration_index += 1

        if checking_tols:
            while iteration_index < xopt_dict['max_iterations'] and (not tol_achieved):
                X.step()
                WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)
                iteration_index += 1

    elif 'cost_budget' in xopt_dict.keys():
        for i in range(xopt_dict.get('num_random')):
            random_inputs = vocs.random_inputs(1)
            random_inputs[0]['s'] = 0.0
            X.evaluate(pd.DataFrame(random_inputs))
        cost_budget = xopt_dict.get('cost_budget')
        while X.generator.calculate_total_cost() < cost_budget:
            X.step()
            WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)
            iteration_index += 1

    else:
        print("No termination criteria specified for Xopt. Provide a criterion such as 'num_step', 'tolerance', or 'cost_budget' (for multi-fidelity).")
        return 0