import sys
import numpy as np
import tkinter as tk
from tkinter import filedialog, simpledialog
import matplotlib.pyplot as plt
from matplotlib import colormaps
from matplotlib.widgets import Slider

root = tk.Tk()
root.withdraw()

if len(sys.argv) == 3:
    full_file_path = sys.argv[1]
    short_file_path = sys.argv[2]
else:
    full_file_path = filedialog.askopenfilename(title='Enter file with all frequencies and S parameters.')     #Prompt for file to load
    short_file_path = filedialog.askopenfilename(title='Enter file with only optimized parameters.')

if len(full_file_path) == 0:
    sys.exit()
    
with open(short_file_path,'r') as short_file:
    sdlines = short_file.readlines()
scol_names = sdlines[0].split()
s_opts = []
f_opts = []
for name in scol_names:
    if name.startswith('S('):
        s,f = name.split('_')
        s_opts.append(s)
        f_opts.append(float(f))
        
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
num_iter = int(swp_data[len(swp_data)-1][0]) #number of iterations
        
#swp_data is an array of first few columns--all param values
#so here, swp_data contains [[iteration, cornercut, rcorner1], etc...]
#sp_data is an array of S parameter data

num_f = int(sum([(swp_data == swp_data[0])[i].all() for i in range(len(swp_data))]))   #Number of frequencies
num_p = int((swp_data.shape[0])/num_f)         #Number of sweep parameter tuples
assert num_f * num_p == swp_data.shape[0], "Error: sweep output file data size inconsistent."
fs = f_data[0:num_f]      #Vector of all frequencies
swps = swp_data[::num_f]  #Array of all sweep parameter tuples
sp = 1                   #Default S-parameter to plot (usually S(0,0))


#Set font parameters and colors for line plots
fntsz = 20
fdict = {'family': 'serif', 'weight': 'normal', 'size': fntsz}
pcolors = colormaps['jet'](np.linspace(0,1,num_p))

fig = plt.figure(figsize=(18,11))
sparam_slider_ax  = fig.add_axes([0.3, 0.16, 0.45, 0.03])
iter_slider_ax  = fig.add_axes([0.3, 0.12, 0.45, 0.03])

#Slider object to select specific S-parameter
sparam_slider = Slider(sparam_slider_ax, 'S-parameter: ', 1, num_sp, valinit=1, valstep=[i+1 for i in range(num_sp)])
sparam_slider.label.set_size(fntsz)
sparam_slider.valtext.set_text(col_names[1+f_col-1])
sparam_slider.valtext.set_fontsize(fntsz)

#Slider object to adjust selected sweep parameter value (keep others const.)
iter_slider = Slider(iter_slider_ax, "Iteration: ", 1, num_iter, valinit=0, valstep=[i for i in range(num_iter+1)])
iter_slider.label.set_size(fntsz)
iter_slider.valtext.set_text(str(swps[0][0]))
iter_slider.valtext.set_fontsize(fntsz)

ax = fig.add_subplot(111)
fig.subplots_adjust(bottom=0.3)

#Plotting function for s-parameter "sp" and sweep parameter index "swp"
def plot_sparam(sp,iteration):
    
    xlims = ax.get_xlim()
    ylims = ax.get_ylim()
    ax.clear()
    ax.set_xlim(xlims)
    ax.set_ylim(ylims)
    line_list = []
    
    param_vals = ''
    for i in range(1,len(swps[0])):
        param_vals += col_names[i]
        param_vals += ': '
        param_vals += str(round(swps[iteration][i],5))
        if i!=len(swps[0])-1:
            param_vals += ', '
    ax.text(0.5,0.9,param_vals, ha='center', fontdict=fdict, transform=ax.transAxes)
    
    line_list.append(ax.plot(fs, sp_data[num_f*iteration:num_f*(iteration+1), sp-1], color='k', linewidth=3))
    
    for freq in f_opts:
        plt.axvline(x=freq, color='lightgray', linestyle='--')
    title = 'Optimized '
    for f in range(len(f_opts)):
        title = title + str(s_opts[f]) + ' at frequency ' + str(f_opts[f])
        if f != len(f_opts)-1:
            title += ', '
    plt.title(title, fontdict=fdict)
    plt.xlim(min(fs), max(fs))
    plt.ylim(bottom=0)
    plt.xlabel('Frequency (Hz)', fontdict=fdict)
    plt.xticks(fontsize=fntsz)
    plt.yticks(fontsize=fntsz)

plot_sparam(1,1)
plt.xlim(min(fs), max(fs))
plt.ylim(bottom=0)

#Functions for what to when the sliders are changed
def sparam_changed(val):
    val = int(round(val))   #val is the column index for the S-param. data
    plot_sparam(val,iter_slider.val)
    sparam_slider.valtext.set_text(col_names[val+f_col-1])
    sparam_slider.valtext.set_fontsize(fntsz)
    
def iter_changed(val):
    val = int(round(val))
    plot_sparam(sparam_slider.val, val)
    iter_slider.valtext.set_text(str(int(swps[val][0])))
    iter_slider.valtext.set_fontsize(fntsz)


sparam_slider.on_changed(sparam_changed)
iter_slider.on_changed(iter_changed)

plt.show()