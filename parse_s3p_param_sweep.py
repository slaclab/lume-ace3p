import os
import numpy as np

if len(sys.argv) == 2:
    file_path = sys.argv[1]
else:
    file_path = filedialog.askopenfilename()

freq = input('Enter the frequency you want to isolate: ')
S_param = input('Enter the S parameter you want to isolate: ')


with open(file_path, 'r') as file:
    lines = file.readlines()
    
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
        key = '('
        for k in range(freq_index):
            key += linesplit[k]
            if k!=freq_index-1:
                key += ','
        key += ')'
    data[key] = linesplit[s_index]

print(data)