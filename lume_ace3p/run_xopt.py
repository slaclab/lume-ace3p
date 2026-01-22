import numpy as np
import pandas as pd
import torch
import random

from xopt.vocs import VOCS
from xopt.evaluator import Evaluator
from xopt import Xopt
from lume_ace3p.workflow import S3PWorkflow
from lume_ace3p.tools import WriteXoptData, WriteS3PDataTable
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C

def run_xopt(workflow_dict, vocs_dict, xopt_dict):
    multi_objective = False
    if isinstance(vocs_dict['objectives']['s_parameter'],list):
        multi_objective = True
        S_params = vocs_dict['objectives']['s_parameter']
        freqs = vocs_dict['objectives']['frequency']
        opts = vocs_dict['objectives']['optimization']

        tol_achieved = False
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

        tol_achieved = False
        if 'tolerance' in vocs_dict['objectives'].keys():
            tols = [vocs_dict['objectives']['tolerance']]
            checking_tols = True
        else:
            checking_tols = False
    save_model = False
    if 'save_model' in xopt_dict:
        save_model = True

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
            if checking_tols and multi_objective:
                #sets tolerance achieved boolean to true if one parameter satisfies condition
                if output_data[S_params[f]][freq_index] <= tols[f]:
                    tol_achieved = True
                else:
                    tol_achieved = False
                    #this prevents tolerance from counting as True unless all parameters satisfy their tolerances
                    break
            elif checking_tols and not multi_objective:
                if output_data[S_params[f]][freq_index] <= tols:
                    tol_achieved = True
                else: 
                    tol_achieved = False
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
    else:
        print("That generator is not supported. Ensure that the generator name specified in the yaml file matches exactly with the Xopt generator name of choice. Exiting the program.")
        return 0
    X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)

    #if num_random keyword is specified, run random model evaluation that many times
    if 'num_random' in xopt_dict.keys():
        #Run X.random_evaluate() to generate + evaluate a few initial points
        for i in range(xopt_dict['num_random']):
            X.random_evaluate()
            #writes an output file with information only about S parameter and frequency of interest
            WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)
            iteration_index += 1
            
    #if num_step keyword is specified, run optimization that many times
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
                
    #alternatively, if some kind of cost limit is established, run until a criteria or time limit is reached
    elif 'cost_budget' in xopt_dict.keys() or 'alotted_time' in xopt_dict.keys():
         
        if 'cost_budget' in xopt_dict.keys():
            cost_budget = xopt_dict.get('cost_budget')
            
        elif 'alotted_time' in xopt_dict.keys():
            hours, minutes, seconds = xopt_dict.get('alotted_time').split(':')
            cost_budget = float(hours)*3600 + float(minutes)*60 + float(seconds)
        
        #do random steps (default 2) to train the GP
        num_random = xopt_dict.get('num_random',2)
        random_inputs = vocs.random_inputs(num_random)
        #choose initial fidelities evenly spaced from 0 to 1--helps train the GP model
        init_fidelity = np.linspace(0,1,num_random)
        for iter in range(len(random_inputs)):
            random_inputs[iter]['s'] = init_fidelity[iter]
        #evaluate random points and save to data file
        X.evaluate_data(pd.DataFrame(random_inputs))
        WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)

        #default cost function is exponential
        cost_function = xopt_dict.get('cost_function', 'exponential')
        if cost_function.lower() == 'exponential':
            #ratio of cost of highest fidelity to lowest fidelity
            p1 = X.data['xopt_runtime'][num_random-1] / X.data['xopt_runtime'][0]
            def cost_func(x):
                #weighted cost function based on remaining time
                val = X.data['xopt_runtime'][0] * torch.exp(torch.tensor(np.log(p1)) * x)
                time_left = cost_budget - X.calculate_total_cost()
                return val / time_left
            X.generator.cost_function = cost_func

        #alternative cost function based on a trained GP; useful for problems where dependence on cost is unknown
        elif cost_function.lower() == 'gaussian_process':
            kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=2.0, length_scale_bounds=(1e-2, 1e2))
            gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=3, alpha=1e-4, normalize_y=True)
            def cost_func(x):
                #this is important for converting back and forth from numpy arrays to tensors
                x_np = x.detach().cpu().numpy().reshape(-1, 1)
                x_train = np.array(X.data['s']).reshape(-1,1)
                y_train = np.array(X.data['xopt_runtime']).reshape(-1,1)
                gp.fit(x_train, y_train)
                return torch.as_tensor(gp.predict(x_np), dtype=torch.float32).view(-1,1,1)
            X.generator.cost_function = cost_func

        else:
            print("Cost function type: '" + cost_function + "' not supported.")
            return 0
       
        iteration_index += num_random

        #run optimization until cost budget or tolerance is achieved
        while X.data['xopt_runtime'].sum() < cost_budget and (not tol_achieved):
            X.step()
            WriteXoptData('sim_output.txt', param_and_freq, X.data, iteration_index)
            iteration_index += 1
        
        if save_model:
            X.dump("saved_xopt_model.yaml")

    else:
        print("No termination criteria specified for Xopt. Provide a criterion such as 'num_step', 'tolerance', or 'cost_budget' (for multi-fidelity).")
        return 0
