import sys
import numpy as np
import tkinter as tk
from tkinter import filedialog, simpledialog
import matplotlib.pyplot as plt
from matplotlib import colormaps
from matplotlib.widgets import Slider

root = tk.Tk()
root.withdraw()

file_path = filedialog.askopenfilename()     #Prompt for file to load

if len(file_path) == 0:
    sys.exit()

with open(file_path,'r') as file:
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
num_f = int(sum([(swp_data == swp_data[0])[i].all() for i in range(len(swp_data))]))   #Number of frequencies
num_p = int((swp_data.shape[0])/num_f)         #Number of sweep parameter tuples
assert num_f * num_p == swp_data.shape[0], "Error: sweep output file data size inconsistent."
fs = f_data[0:num_f]      #Vector of all frequencies
swps = swp_data[::num_f]  #Array of all sweep parameter tuples
sp = 1                    #Default S-parameter to plot (usually S(0,0))

#Open prompt to select which sweep parameters to provide sliders for
plot_swp = simpledialog.askstring('Select sweep parameters', 'Input file parsed: '
                                 + str(f_col-1) + ' sweep parameter(s) found.\n'
                                 + ' Select up to 2 sweep parameters for sliders:',
                                 initialvalue=', '.join([str(i+1) for i in range(min(f_col-1,2))]))

if plot_swp is None:
    sys.exit()

#Set font parameters and colors for line plots
fntsz = 20
fdict = {'family': 'serif', 'weight': 'normal', 'size': fntsz}
pcolors = colormaps['jet'](np.linspace(0,1,num_p))

assert isinstance(eval(plot_swp),(int,tuple)), "Error: unknown argument specified."

if isinstance(eval(plot_swp),int):
    num_slider = 1
    sl_param1 = eval(plot_swp)
    p1_vals = np.unique(np.transpose(swps)[sl_param1-1])
    num_p1 = len(p1_vals)
else:
    num_slider = 2
    sl_param1 = eval(plot_swp)[0]
    sl_param2 = eval(plot_swp)[1]
    p1_vals = np.unique(np.transpose(swps)[sl_param1-1])
    num_p1 = len(p1_vals)
    p2_vals = np.unique(np.transpose(swps)[sl_param2-1])
    num_p2 = len(p2_vals)

if num_slider == 1:
    fig = plt.figure(figsize=(18,10))
    sparam_slider_ax  = fig.add_axes([0.3, 0.12, 0.45, 0.03])
    paramt_slider_ax  = fig.add_axes([0.3, 0.08, 0.45, 0.03])
    param1_slider_ax  = fig.add_axes([0.3, 0.04, 0.45, 0.03])
elif num_slider == 2:
    fig = plt.figure(figsize=(18,11))
    sparam_slider_ax  = fig.add_axes([0.3, 0.16, 0.45, 0.03])
    paramt_slider_ax  = fig.add_axes([0.3, 0.12, 0.45, 0.03])
    param1_slider_ax  = fig.add_axes([0.3, 0.08, 0.45, 0.03])
    param2_slider_ax  = fig.add_axes([0.3, 0.04, 0.45, 0.03])

#Slider object to select specific S-parameter
sparam_slider = Slider(sparam_slider_ax, 'S-parameter: ', 1, num_sp, valinit=1,
                       valstep=[i+1 for i in range(num_sp)])
sparam_slider.label.set_size(fntsz)
sparam_slider.valtext.set_text(col_names[1+f_col-1])
sparam_slider.valtext.set_fontsize(fntsz)

#Slider object to select sweep parameter tuple
swparamt_slider = Slider(paramt_slider_ax, 'Sweep param. tuple: ', 1, num_p, valinit=1,
                       valstep=[i+1 for i in range(num_p+1)])
swparamt_slider.label.set_size(fntsz)
swparamt_slider.valtext.set_text(str(swps[0]))
swparamt_slider.valtext.set_fontsize(fntsz)

#Slider object to adjust selected sweep parameter value (keep others const.)
swparam1_slider = Slider(param1_slider_ax, col_names[sl_param1-1]+" value: ", 1, num_p1, valinit=1,
                       valstep=[i+1 for i in range(num_p1+1)])
swparam1_slider.label.set_size(fntsz)
swparam1_slider.valtext.set_text(str(swps[0][sl_param1-1]))
swparam1_slider.valtext.set_fontsize(fntsz)

if num_slider == 2:
    #Slider object to adjust selected sweep parameter value (keep others const.)
    swparam2_slider = Slider(param2_slider_ax, col_names[sl_param2-1]+" value: ", 1, num_p2, valinit=1,
                           valstep=[i+1 for i in range(num_p2+1)])
    swparam2_slider.label.set_size(fntsz)
    swparam2_slider.valtext.set_text(str(swps[0][sl_param2-1]))
    swparam2_slider.valtext.set_fontsize(fntsz)

ax = fig.add_subplot(111)
fig.subplots_adjust(bottom=0.25)

#Plotting function for s-parameter "sp" and sweep parameter index "swp"
def plot_sparam(sp,swp):
    
    ax.clear()
    line_list = []
    for indp in range(len(swps)):
        if indp == swp-1:  #Color selected curve in bold
            line_list.append(ax.plot(fs, sp_data[num_f*indp:num_f*(indp+1),sp-1],
                                     color='k', linewidth=3))
        else:
            line_list.append(ax.plot(fs, sp_data[num_f*indp:num_f*(indp+1),sp-1],
                                     color=pcolors[indp], linewidth=2, alpha=0.3))
    plt.xlim(min(fs), max(fs))
    plt.ylim(bottom=0)
    plt.title(col_names[sp+f_col-1], fontdict=fdict)
    plt.xlabel('Frequency (Hz)', fontdict=fdict)
    plt.xticks(fontsize=fntsz)
    plt.yticks(fontsize=fntsz)
    
    return line_list

plot_sparam(1,1)

#Functions for what to when the sliders are changed
def sparam_changed(val):
    val = int(round(val))   #val is the column index for the S-param. data
    plot_sparam(val,swparamt_slider.val)
    sparam_slider.valtext.set_text(col_names[val+f_col-1])
    sparam_slider.valtext.set_fontsize(fntsz)

def swparamt_changed(val):
    val = int(round(val))   #val is the index for the sweep param. tuple
    plot_sparam(sparam_slider.val,val)
    swparamt_slider.valtext.set_text(str(swps[val-1]))
    swparamt_slider.valtext.set_fontsize(fntsz)

def swparam1_changed(val):
    val = int(round(val))   #val is the index for sweep param. 1
    curr_swp1 = np.copy(swps[swparamt_slider.val-1])
    curr_swp1[sl_param1-1] = np.copy(p1_vals[val-1])
    swp = int(np.where((curr_swp1 == swps).all(axis=1))[0][0])+1
    swparam1_slider.valtext.set_text(str(p1_vals[val-1]))
    swparam1_slider.valtext.set_fontsize(fntsz)
    swparamt_slider.set_val(swp)
    
def swparam2_changed(val):
    val = int(round(val))   #val is the index for sweep param. 2
    curr_swp2 = np.copy(swps[swparamt_slider.val-1])
    curr_swp2[sl_param2-1] = np.copy(p2_vals[val-1])
    swp = int(np.where((curr_swp2 == swps).all(axis=1))[0][0])+1
    swparam2_slider.valtext.set_text(str(p2_vals[val-1]))
    swparam2_slider.valtext.set_fontsize(fntsz)
    swparamt_slider.set_val(swp)
    
sparam_slider.on_changed(sparam_changed)
swparamt_slider.on_changed(swparamt_changed)
swparam1_slider.on_changed(swparam1_changed)
if num_slider == 2:
    swparam2_slider.on_changed(swparam2_changed)

plt.show()