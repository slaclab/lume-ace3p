import numpy as np
import pandas as pd
import torch

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
            fidelity_variable = xopt_dict.get('fidelity_variable','s')  #Add the fidelity_variable name (default is "s")
            input_dict[fidelity_variable] = input_dict.pop('s') #Rename fidelity_variable name in input_dict if default "s"
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
    elif xopt_dict['generator'] == 'UpperConfidenceBoundGenerator':
        from xopt.generators.bayesian import UpperConfidenceBoundGenerator
        options = xopt_dict.get('generator_options', {}) 
        generator = UpperConfidenceBoundGenerator(vocs=vocs, **options)    
    elif xopt_dict['generator'] == 'ExpectedHypervolumeImprovementGenerator':
         from xopt.generators.bayesian.mobo import MOBOGenerator as ExpectedHypervolumeImprovementGenerator
         options = xopt_dict.get('generator_options', {})
         if 'reference_point' not in options:
             print("Error: 'reference_point' is required for Multi-Objective optimization.")
             return 0
         generator = ExpectedHypervolumeImprovementGenerator(vocs=vocs, **options)
    else:
        print("That generator is not supported. Ensure that the generator name specified in the yaml file matches exactly with the Xopt generator name of choice. Exiting the program.")
        return 0
    X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)
    
    if 'num_random' in xopt_dict.keys() and 'cost_budget' not in xopt_dict.keys() and 'alotted_time' not in xopt_dict.keys():
        #Run X.random_evaluate() to generate + evaluate a few initial points
        for i in range(xopt_dict['num_random']):
            X.random_evaluate()
            #writes an output file with information only about S parameter and frequency of interest
            WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)
            iteration_index += 1

    if 'num_step' in xopt_dict.keys() and 'cost_budget' not in xopt_dict.keys() and 'alotted_time' not in xopt_dict.keys():
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

    elif 'cost_budget' in xopt_dict.keys() or 'alotted_time' in xopt_dict.keys(): #Loop for multi-fidelity optimization
        num_random = xopt_dict.get('num_random',2)
        random_inputs = vocs.random_inputs(num_random)
        for iter in range(len(random_inputs)):
            random_inputs[iter]['s'] = 0.0
            if iter == 1:  #Do one non-zero fidelity random run
                random_inputs[iter]['s'] = 1.0
        X.evaluate_data(pd.DataFrame(random_inputs))
        WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)
        
        p1 = X.data['xopt_runtime'][1] / X.data['xopt_runtime'][0]
        
        cost_function = xopt_dict.get('cost_function', 'exponential')
        if cost_function.lower() == 'exponential':
            if 'cost_function_p1' in xopt_dict.keys():
                p1 = xopt_dict.get('cost_function_p1')    #Manual override for cost function p1
            def cost_func(x):
                val = torch.exp(torch.tensor(np.log(p1)) * x)
                return val
            X.generator.cost_function = cost_func
        else:
            print("Cost function type: '" + cost_function + "' not supported.")
            return 0
        
        iteration_index += xopt_dict.get('num_random',2)
        if 'cost_budget' in xopt_dict.keys():
            cost_budget = xopt_dict.get('cost_budget')
        elif 'alotted_time' in xopt_dict.keys():
            hours, minutes, seconds = xopt_dict.get('alotted_time').split(':')
            cost_budget = (float(hours)*3600 + float(minutes)*60 + float(seconds))*0.8 / X.data['xopt_runtime'][0]
        while X.generator.calculate_total_cost() < cost_budget:
            X.step()
            WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)
            iteration_index += 1
    
    else:
        print("No termination criteria specified for Xopt. Provide a criterion such as 'num_step', 'tolerance', or 'cost_budget' (for multi-fidelity).")
        return 0
    if xopt_dict.get('save_model', False):
        try:
            if hasattr(X.generator, 'model') and X.generator.model is not None:
                torch.save(X.generator.model.state_dict(), "Binary_gp_model.pt")
                with open("gp_parameters.txt", "w") as f:
                    f.write("Gaussian Process Hyperparameters:\n")
                    f.write("=================================\n")
                    for name, param in X.generator.model.named_parameters():
                        val = param.detach().cpu().numpy()
                        f.write(f"{name}: {val}\n")
            else:
                print(" - Generator has no model to save.")
        except Exception as e:
            print(f" - Error saving model: {e}")
            
    return 0