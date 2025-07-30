import os
import numpy as np

freq = input('Enter the frequency you want to isolate: ')
S_param = input('Enter the S parameter you want to isolate: ')
subdirectories = [entry.name for entry in os.scandir('.') if entry.is_dir()]

data_array = []
current_list = []
first_one = True
first_ever_run = True
prev_value = ''
for subdir in subdirectories:
    if subdir.startswith('lume-ace3p_s3p_workdir'):
        if (not first_ever_run) and (subdir.startswith(prev_value)):
            first_one = False
        
        if first_one = True:
            data_array.append(current_list)
            current_list = []
        else:
            current_list = []
            
        print(subdir)
        os.chdir(subdir)
        os.chdir('s3p_results')
        with open('Reflection.out') as file:
            lines = file.readlines()
        colnames = []
        s_index = 0
        for l in lines:
            if l.startswith('#Frequency'):
                colnames = l.split()
                s_index = colnames.index(S_param)
            if l.startswith(freq):
                current_list.append(l.split()[s_index])
        
        first_one = False
        prev_value = subdir.rsplit('_')[0]
        first_ever_run = False
        
print(data_array)