import pandas

def WriteDataTable(filename, data_dict, input_names, output_names):
    #Helper script to write dict data in tabulated format to file:
    #
    #  filename     = filename to write data output
    #  data_dict    = dict containing data in tuple-dict pairs
    #  input_names  = list of input names of data_dict to write
    #  output_names = list of output names within inputs to write
    #
    #Note, the data_dict structure uses a tuple (of input values) as a key and a
    #dict as the corresponding value (with outputs inside). The input names are
    #NOT present in the data_dict and are sorted by index in the tuple ordering.
    #The output_names list must MATCH the names within the data_dict value dict.
    #
    #Example:
    #  input_names = ['my_input1','my_input2']
    #  output_names = ['my_output1','my_output2']
    #  data_dict = {(1.23,4.56): {'my_output1': 3.14, 'my_output2': 2.72, 'my_output3': 1.0},
    #          (1.23,7.89): {'my_output1': 1.44, 'my_output2': 6.28, 'my_output3': 2.0}}
    #
    #  Would produce a tab-delimited text file with 3 rows of text:
    #""
    #  my_input1  my_input2  my_output1  my_output2
    #  1.23       4.56       3.14        2.72
    #  1.23       7.89       1.44        6.28
    #""
    #Note: my_output3, while present in the data_dict, will not be written since
    #it was not present in the output_name list. Items in the output_names list
    #not present in the data_dict will cause an error.
    
    text = ''
    for name in (input_names + output_names):
        text += name + '\t' #Column for each input and output name
    text += '\n'
    for key, value in data_dict.items():
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

def WriteS3PDataTable(filename, data_dict, input_names):

    text = ''
    for name in input_names:
        text += name + '\t' #Column for each input and output name
    text += 'Frequency\t'
    key1 = data_dict.keys()[0] #Extract data from one S3P run
    #Get all S-parameter names (skeys) that were saved in data_dict
    skeys = [key for key in data_dict[key1].keys() if key not in ['IndexMap', 'Frequency']]
    for skey in skeys:
        text += skey + '\t'
    text += '\n'
    for key, value in data_dict.keys():
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