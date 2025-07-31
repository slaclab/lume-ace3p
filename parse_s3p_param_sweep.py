import os
import sys
import numpy as np

def parse_file(lines):
    print('hell0')
    freq = input('Enter the frequency you want to isolate: ')
    S_param = input('Enter the S parameter you want to isolate: ')
    print('from the other side')
    colnames = lines[0].split()
    freq_index = 0
    s_index = 0
    for i in range(len(colnames)):
        if colnames[i]==S_param:
            s_index = i
        elif colnames[i]=='Frequency':
            freq_index = i
    data = {}
    for l in lines[1:]:
        linesplit = l.split()
        if linesplit[freq_index] == freq:
            key = ()
            for k in range(freq_index):
                key = key + (float(linesplit[k]),)

            data[key] = float(linesplit[s_index])
    return data