import os
import numpy as np

freq = input('Enter the frequency you want to isolate: ')
S_param = input('Enter the S parameter you want to isolate: ')
subdirectories = [entry.name for entry in os.scandir('.') if entry.is_dir()]

data_array = {}

#NOTE: only works on two param sweeps!
for subdir in subdirectories:
    if subdir.startswith('lume-ace3p_s3p_workdir'):
        first_half, second_param = subdir.rsplit('_')
        first_param = float(first_half.rsplit('_')[1])

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
                data_array[(float(first_param), float(second_param))] = l.split()[s_index]

        os.chdir('../..')
        
print(data_array)