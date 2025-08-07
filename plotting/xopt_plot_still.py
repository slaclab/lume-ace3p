import sys
import numpy as np
import tkinter as tk
from tkinter import filedialog, simpledialog
import matplotlib.pyplot as plt
from matplotlib import colormaps
from matplotlib.widgets import Slider
from matplotlib.colors import LinearSegmentedColormap

root = tk.Tk()
root.withdraw()

if len(sys.argv) == 3:
    full_file_path = sys.argv[1]
    short_file_path = sys.argv[2]
else:
    full_file_path = filedialog.askopenfilename(title='Enter file with all frequencies and S parameters.')
    short_file_path = filedialog.askopenfilename(title='Enter file with only optimized parameters.')

if len(full_file_path) == 0:
    sys.exit()
    
with open(short_file_path,'r') as short_file:
    sdlines = short_file.readlines()
    
short_data = np.zeros((len(sdlines)-1,3))
scol_names = sdlines[0].split()
s_opts = []
f_opts = []
num_inputs=0
is_input = True
for name in scol_names:
    if name.startswith('S('):
        is_input = False
        s,f = name.split('_')
        s_opts.append(s)
        f_opts.append(float(f))
    if is_input:
        num_inputs += 1
for i in range(1,len(sdlines)-1):
    l = sdlines[i].split()
    short_data[i,0] = l[0]
    short_data[i,1] = l[1]
    short_data[i,2] = l[2]
        
with open(full_file_path,'r') as file:
    dlines = file.readlines()                #Total number of data rows
col_names = dlines[0].split()
f_col = col_names.index('Frequency')+1       #Column index for frequencies
num_sp = len(col_names)-f_col                #Number of S-parameters
swp_data = np.zeros((len(dlines)-1,f_col-1)) #Sweep parameter data
f_data = np.zeros((len(dlines)-1,1))         #Frequency data
sp_data = np.zeros((len(dlines)-1,num_sp))   #S-parameter data
for i in range(len(dlines)-1):
    dline = dlines[i+1].split()
    for j in range(f_col-1):
        swp_data[i][j] = dline[j]
    f_data[i] = dline[f_col-1]
    for j in range(num_sp):
        sp_data[i][j] = dline[f_col+j]
num_iter = int(swp_data[len(swp_data)-1][0])

num_f = int(sum([(swp_data == swp_data[0])[i].all() for i in range(len(swp_data))]))
fs = f_data[0:num_f]


s_opt_index = 0
if len(s_opts) > 1:
    s_param = input('Please enter the S parameter you want to view: ')
    s_opt_index = s_opts.index(s_param)

#Set font parameters and colors for line plots
fntsz = 20
fdict = {'family': 'serif', 'weight': 'normal', 'size': fntsz}

choice = input("Please choose how you would like to plot iterations.\n1. All iterations \n2. The last X iterations \n3. All iterations, spaced by X \n4. Custom choice of iterations.\n")
indices = []
if choice=='1':
    indices = [i for i in range(num_iter)]
elif choice=='2':
    last_few = input('Please enter how many of the last iterations you want to see: ')
    indices = [i for i in range(num_iter-int(last_few), num_iter)]
elif choice=='3':
    spacing = input('Please enter the spacing between iterations to be plotted: ')
    indices = [i for i in range(0,num_iter,int(spacing))]
else:
    indices = [int(i) for i in input('Please enter your choice of iterations, separated by commas: ').split(',')]
    
n = len(indices)
colors = colormaps['jet'](np.linspace(0,1,n))

fig = plt.figure(figsize=(18,11))
ax = fig.add_subplot(111)
fig.subplots_adjust(bottom=0.3)

k=0
for i in indices:
    label='Iteration ' + str(i) + ', '
    for j in range(num_inputs):
        label += str(short_data[i][j])
        if j != num_inputs-1:
            label += ', '
    ax.plot(fs, sp_data[num_f*i:num_f*(i+1), s_opt_index], linewidth=3, color=colors[k], label=label)
    k+=1
for freq in f_opts:
    ax.axvline(x=freq, color='lightgray', linestyle='--')
ax.set_xlabel('Frequency (Hz)', fontdict=fdict)
ax.set_ylabel(s_opts[s_opt_index], fontdict=fdict)
title = 'Optimized '
for f in range(len(f_opts)):
    title = title + str(s_opts[f]) + ' at frequency ' + str(f_opts[f])
    if f != len(f_opts)-1:
        title += ', '
plt.title(title, fontdict=fdict)
plt.legend(loc='upper left')

plt.xlim(min(fs), max(fs))
plt.ylim(bottom=0)

plt.show()