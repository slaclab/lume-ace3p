import numpy as np
import pandas as pd
import torch
from botorch.utils.multi_objective.hypervolume import Hypervolume
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
    is_mobo = False
    param_and_freq_writing = param_and_freq.copy()
    if xopt_dict['generator'] == 'ExpectedHypervolumeImprovementGenerator':
        is_mobo = True
        param_and_freq_writing['hypervolume'] = 'MAXIMIZE'
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
        param_values = tuple(input_dict[k] for k in input_dict)
        param_list = list(input_dict.keys())
        #this puts the output data in the sweep format needed to run WriteS3PDataTable
        modified_output_data = {param_values: output_data}
        #appends data to a file containing information about all frequencies and S parameters for every parameter combination
        WriteS3PDataTable('sim_output_all_values.txt', modified_output_data, param_list, True, iteration_index)

        output_dict = {}
        freq_index = 0
        sim_freqs = np.array(output_data['Frequency'])
        print(sim_freqs)
        print(freqs)
        for f in range(len(freqs)):
            try:
                # Mo: correct for closest frequency  in case of sweep with 1 Hz tolerance
                target_freq_str = str(freqs[f])
                target_freq_float = float(freqs[f])
                freq_index = (np.abs(sim_freqs - target_freq_float)).argmin()
                #freq_index = list(output_data['Frequency']).index(freqs[f])
                if abs(sim_freqs[freq_index] - target_freq_float) > 100.0:
                    raise ValueError    
            except ValueError:
                print("Inputted frequency to be optimized is not in frequency sweep.")
                
            val = output_data[S_params[f]][freq_index]
            key_name = S_params[f] + '_' + target_freq_str
            output_dict[key_name] = val
            #example: output_dict['S(0,0)_9.494e9'] = output_data['S(0,0)'][0]
            #output_dict[S_params[f]+'_'+str(freqs[f])] = output_data[S_params[f]][freq_index]
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
    # add check for Bayesian
    is_bayesian = xopt_dict['generator'] in [
        'ExpectedImprovementGenerator', 
        'MultiFidelityGenerator', 
        'UpperConfidenceBoundGenerator', 
        'ExpectedHypervolumeImprovementGenerator'
    ]
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
         is_mobo = True
    else:
        print("That generator is not supported. Ensure that the generator name specified in the yaml file matches exactly with the Xopt generator name of choice. Exiting the program.")
        return 0
    X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)
    
    ####
        # MO    --- ADD PREDICTION COLUMNS TO WRITER ---
    if is_bayesian and not is_mobo:
        for obj in vocs.objective_names:
            param_and_freq_writing[f"pred_opt_{obj}"] = 'OBSERVE'
        for var in vocs.variable_names:
            if var != 's':  # Hide fidelity param
                param_and_freq_writing[f"pred_opt_{var}"] = 'OBSERVE'

    # MO   ---   EXTRACT GP MODEL MINIMUM ---
    def update_model_predictions():
        if not is_bayesian: return
        
        for obj in vocs.objective_names:
            col = f"pred_opt_{obj}"
            if col not in X.data.columns:
                X.data[col] = np.nan
                
        if hasattr(X.generator, 'model') and X.generator.model is not None:
            try:
                best_x = X.generator.get_optimum()
                pt = torch.tensor(best_x[vocs.variable_names].to_numpy(), dtype=torch.double)
                if pt.ndim == 1: pt = pt.unsqueeze(0)
                
                with torch.no_grad():
                    preds = X.generator.model.posterior(pt).mean.detach().numpy()
                    
                if preds.ndim > 1 and preds.shape[0] > 1:
                    best_pred = preds[np.argmin(np.sum(preds, axis=1))]
                else:
                    best_pred = preds.flatten()
                    
                for idx, obj in enumerate(vocs.objective_names):
                    if not is_mobo:
                        X.data.at[X.data.index[-1], f"pred_opt_{obj}"] = best_pred[idx]
            except Exception:
                pass

        # MO ### export MOBO Pareto Front
        # --- HELPER: EXPORT MOBO PARETO FRONT ---
# MO ### export MOBO Pareto Front
        # --- HELPER: EXPORT MOBO PARETO FRONT ---
        if is_mobo:
            if hasattr(X.generator, 'model') and X.generator.model is not None:
                try:
                    # 1. Generate dense random samples across your input space
                    num_samples = 10000 
                    var_names = vocs.variable_names
                    obj_names = vocs.objective_names
                    
                    # Extract bounds from VOCS to scale our random samples
                    # Transposing ensures bounds[0] is minimums and bounds[1] is maximums
                    bounds = torch.tensor([vocs.variables[k] for k in var_names], dtype=torch.double).T
                    
                    # Generate random points in [0, 1] and scale to bounds
                    rand_x = torch.rand(num_samples, len(var_names), dtype=torch.double)
                    candidates = bounds[0] + rand_x * (bounds[1] - bounds[0])
                    
                    # Force fidelity parameter 's' to maximum fidelity (usually 1.0) for the prediction
                    if 's' in var_names:
                        s_idx = var_names.index('s')
                        candidates[:, s_idx] = 1.0

                    # 2. Get the model's posterior mean predictions
                    with torch.no_grad():
                        posterior = X.generator.model.posterior(candidates)
                        mean_preds = posterior.mean
                    
                    # 3. Format objectives for BoTorch's Pareto filter 
                    # Note: is_non_dominated assumes MAXIMIZATION, so we flip the sign of MINIMIZE objectives
                    obj_weights = torch.ones(len(obj_names), dtype=torch.double)
                    for i, obj_name in enumerate(obj_names):
                        if vocs.objectives[obj_name] == "MINIMIZE":
                            obj_weights[i] = -1.0
                            
                    weighted_preds = mean_preds * obj_weights
                    
                    # 4. Find the non-dominated points
                    pareto_mask = is_non_dominated(weighted_preds)
                    
                    # Extract the Pareto optimal inputs and outputs based on the mask
                    pareto_x = candidates[pareto_mask]
                    pareto_y = mean_preds[pareto_mask]
                    
                    # 5. Reconstruct into a pandas DataFrame
                    df_data = {}
                    for i, var_name in enumerate(var_names):
                        if var_name != 's':  # Hide fidelity param from final CSV
                            df_data[var_name] = pareto_x[:, i].numpy()
                            
                    for i, obj_name in enumerate(obj_names):
                        df_data[f"pred_{obj_name}"] = pareto_y[:, i].numpy()
                        
                    predicted_front = pd.DataFrame(df_data)
                    
                    # 6. Save the cleanly extracted model Pareto front
                    if not predicted_front.empty:
                        predicted_front.to_csv('predicted_model_pareto_front.csv', index=False)
                        
                except Exception as e:
                    print(f"[-] Could not generate predicted MOBO Pareto front from model. Error: {e}")  

                    
    if 'num_random' in xopt_dict.keys() and 'cost_budget' not in xopt_dict.keys() and 'alotted_time' not in xopt_dict.keys():
        #Run X.random_evaluate() to generate + evaluate a few initial points
        for i in range(xopt_dict['num_random']):
            X.random_evaluate()
            update_model_predictions()
            #writes an output file with information only about S parameter and frequency of interest
            WriteXoptData('sim_output.txt', param_and_freq_writing, X.data, iteration_index)
            iteration_index += 1
    # MO: add logic to output hypervolume
    hv_class = None
    if is_mobo:
        try:
            ref_dict = xopt_dict['generator_options']['reference_point']
            ref_point = torch.tensor(list(ref_dict.values()), dtype=torch.double) * -1.0
            hv_class = Hypervolume(ref_point=ref_point)
        except KeyError:
            pass
    if 'num_step' in xopt_dict.keys() and 'cost_budget' not in xopt_dict.keys() and 'alotted_time' not in xopt_dict.keys():
        #Run optimization for subsequent steps
        for i in range(xopt_dict['num_step']):
            X.step()
             # MO: add hypervolume to X.data
            if is_mobo and hv_class is not None:
                try:
                    obj_cols = list(ref_dict.keys())
                    valid_data = X.data[obj_cols].dropna()
                    if len(valid_data) > 0:
                        obs_points = torch.tensor(valid_data.values, dtype=torch.double) * -1.0
                        current_hv = float(hv_class.compute(obs_points))
                        X.data.at[X.data.index[-1], 'hypervolume'] = current_hv
                    else:
                        X.data.at[X.data.index[-1], 'hypervolume'] = 0.0
                except Exception as e:
                    print(f"!!! HV CRASH !!! Error details: {e}")
                    X.data.at[X.data.index[-1], 'hypervolume'] = 0.0
            #writes an output file with information only about S parameter and frequency of interest, and hypervolume if applicabe
            update_model_predictions()
            WriteXoptData('sim_output.txt', param_and_freq_writing, X.data, iteration_index)
            iteration_index += 1

        if checking_tols:
            while iteration_index < xopt_dict['max_iterations'] and (not tol_achieved):
                X.step()
                update_model_predictions()
                WriteXoptData('sim_output.txt', param_and_freq_writing, X.data, iteration_index)
                iteration_index += 1

    elif 'cost_budget' in xopt_dict.keys() or 'alotted_time' in xopt_dict.keys(): #Loop for multi-fidelity optimization
        num_random = xopt_dict.get('num_random',2)
        random_inputs = vocs.random_inputs(num_random)
        for iter in range(len(random_inputs)):
            random_inputs[iter]['s'] = 0.0
            if iter == 1:  #Do one non-zero fidelity random run
                random_inputs[iter]['s'] = 1.0
        X.evaluate_data(pd.DataFrame(random_inputs))
        update_model_predictions()
        WriteXoptData('sim_output.txt', param_and_freq_writing, X.data, iteration_index)
        
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
            update_model_predictions()
            WriteXoptData('sim_output.txt', param_and_freq_writing, X.data, iteration_index)
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