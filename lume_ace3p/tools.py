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
        text += name + '\t' #Column for each input and output name
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

def WriteS3PDataTable(filename, sweep_data, input_names):
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
    for name in input_names:
        text += name + '\t' #Column for each input and output name
    text += 'Frequency\t'
    key1 = sweep_data.keys()[0] #Extract data from one S3P run
    #Get all S-parameter names (skeys) that were saved in sweep_data
    skeys = [key for key in sweep_data[key1].keys() if key not in ['IndexMap', 'Frequency']]
    for skey in skeys:
        text += skey + '\t'
    text += '\n'
    for key, value in sweep_data.keys():
        for i in range(len(input_names)): #Loop over input parameters
            for idf in range(len(value['Frequency'])): #Loop over frequencies scanned
                #Write value of each input in tuple for evaluation
                text += str(key[i]) + '\t'
                #Write frequency for the S3P evaluation
                text += str(value['Frequency'][idf]) + '\t'
                for skey in skeys:
                    #Write particular S-parameters in corresponding columns
                    text += str(value[skey][idf]) + '\t'
                text += '\n'
    with open(filename,'w') as file:
        file.write(text)

def WriteXoptData(filename, Xopt_obj):
    #Helper script to write Xopt object data to a text file with proper formatting:
    #
    #  filename = filename to write data output
    #  Xopt_obj = Xopt-class object containing data
    #
    #Note: pandas is used to set display options for printing without truncation

    pandas.set_option("display.max_rows", 1000000)
    pandas.set_option("display.max_colwidth", 1000000)
    pandas.set_option("expand_frame_repr", False)

    with open(filename,'w') as file:
        print(Xopt_obj.data, file=file)