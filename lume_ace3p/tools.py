import pandas

def WriteOmega3PDataTable(filename, sweep_data, input_names, output_names):
    #Helper script to write Omega3P sweep_data dict in tabulated format to file:
    #
    #  filename     = filename to write data output
    #  sweep_data   = dict containing data in tuple-dict pairs
    #  input_names  = list of input names of sweep_data to write
    #  output_names = list of output names within inputs to write
    #
    #Note, the data_dict structure uses a tuple (of input values) as a key and a
    #dict as the corresponding value (with outputs inside). The input names are
    #NOT present in the sweep_data and are sorted by index in the tuple ordering.
    #The output_names list must MATCH the names within the sweep_data value dict.
    #
    #Example:
    #  input_names = ['my_input1','my_input2']
    #  output_names = ['my_output1','my_output2']
    #  sweep_data = {(1.23,4.56): {'my_output1': 3.14, 'my_output2': 2.72, 'my_output3': 1.0},
    #          (1.23,7.89): {'my_output1': 1.44, 'my_output2': 6.28, 'my_output3': 2.0}}
    #
    #  Would produce a tab-delimited text file with 3 rows of text:
    #""
    #  my_input1  my_input2  my_output1  my_output2
    #  1.23       4.56       3.14        2.72
    #  1.23       7.89       1.44        6.28
    #""
    #Note: my_output3, while present in the sweep_data, will not be written since
    #it was not present in the output_name list. Items in the output_names list
    #not present in the sweep_data will cause an error.
    
    text = ''
    for name in (input_names + output_names):
        new_name = name
        #parses input parameter names modified in run_lume_ace3p.py to be readable
        if new_name.startswith('ACE3P'):
            new_name = new_name.rsplit('_',1)[1]
        text += new_name + '\t' #Column for each input and output name
    text += '\n'
    for key, value in sweep_data.items():
        #Write each evaluation as a tab-delimited row
        for i in range(len(input_names)):
            #Write value of each input in tuple for evaluation
            text += str(key[i]) + '\t'  
        for i in range(len(output_names)):
            #Write value of each output in dict from given inputs
            text += str(value[output_names[i]]) + '\t'
        text += '\n'
    with open(filename,'w') as file:
        file.write(text)

def WriteS3PDataTable(filename, sweep_data, input_names, is_xopt=False, iteration_index=0):
    #Helper script to write S3P sweep_data dict in tabulated format to file:
    #
    #  filename     = filename to write data output
    #  sweep_data    = dict containing data in tuple-dict pairs
    #  input_names  = list of input names of sweep_data to write
    #
    #Note, the sweep_data structure uses a tuple (of input values) as a key and a
    #dict as the corresponding value (with outputs inside). The input names are
    #NOT present in the sweep_data and are sorted by index in the tuple ordering.
    #
    #Example:
    #  input_names = ['my_input1','my_input2']
    #  sweep_data = {(1.23,4.56): {'IndexMap': {...}, 'Frequency': [1.11,1.22,1.33], 'S(0,0)': [...], 'S(0,1)': [...], ...},
    #          (1.23,7.89): {'IndexMap': {...}, 'Frequency': [1.11,1.22,1.33], 'S(0,0)': [...], 'S(0,1)': [...], ...}}
    #
    #  Would produce a tab-delimited text file with 6 rows of text corrseponding to
    #  each input tuple times the number of frequencies provided (2 x 3):
    #""
    #  my_input1  my_input2  Frequency  S(0,0) S(0,1) ...
    #  1.23       4.56       1.11       ...    ...
    #  1.23       4.56       1.22       ...    ...
    #  1.23       4.56       1.33       ...    ...
    #  1.23       7.89       1.11       ...    ...
    #  1.23       7.89       1.22       ...    ...
    #  1.23       7.89       1.33       ...    ...
    #""
    #Note: the default S-parameter column names are of the from "S(m,n)" but any key names can be used

    text = ''
    #for the first run of xopt and for any non-S3P run, write column titles
    if (is_xopt and iteration_index==0) or not is_xopt:
        if is_xopt:
            text += 'Iteration\t'
        for name in input_names:
            text += name + '\t' #Column for each input and output name
        text += 'Frequency\t'
        key1 = list(sweep_data.keys())[0] #Extract data from one S3P run
        #Get all S-parameter names (skeys) that were saved in sweep_data
        skeys = [key for key in sweep_data[key1].keys() if key not in ['IndexMap', 'Frequency']]
        for skey in skeys:
            text += skey + '\t'
    text += '\n'
    #if this is an xopt run, adds iteration index to first column
    if is_xopt:
        text += str(iteration_index)+'\t'
    for key, value in sweep_data.items():
        for idf in range(len(value['Frequency'])): #Loop over frequencies scanned
            for i in range(len(input_names)): #Loop over input parameters
                new_input = str(key[i])
                #parses input parameter names modified in run_lume_ace3p.py to be readable
                if new_input.startswith('ACE3P'):
                    new_input = new_input.rsplit('_',1)[1]
                #Write value of each input in tuple for evaluation
                text += new_input + '\t'
            #Write frequency for the S3P evaluation
            text += str(value['Frequency'][idf]) + '\t'
            for skey in skeys:
                #Write particular S-parameters in corresponding columns
                text += str(value[skey][idf]) + '\t'
            text += '\n'
            #if this is an xopt run, adds iteration index to first column
            if is_xopt:
                text += str(iteration_index)+'t'
    if is_xopt:
        with open(filename,'a') as file:
            file.write(text)
    else:
        with open(filename,'w') as file:
            file.write(text)

def WriteXoptData(filename, param_dict, Xopt_data, iteration_index):
    #param_dict contains all objectives as keys and their split into objective and frequency
    #Xopt_data is a dataframe containing results from Xopt run

    pandas.set_option("display.max_rows", 1000000)
    pandas.set_option("display.max_colwidth", 1000000)
    pandas.set_option("expand_frame_repr", False)
    
    for key in param_dict:
        #eg, replace S(0,0)_9.424e+09 with S(0,0)
        Xopt_data.columns = (Xopt_data.columns).str.replace(key, param_dict[key][0])
    #add a column detailing which iteration this is
    Xopt_data.insert(loc=0, column='Iteration', value=np.full(len(Xopt_data), iteration_index))
    
    with open(filename,'w') as file:
        print(Xopt_data.to_string(index=False),file=file)