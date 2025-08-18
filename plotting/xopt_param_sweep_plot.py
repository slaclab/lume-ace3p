import sys
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib import colormaps
from tkinter import filedialog, simpledialog
from mpl_toolkits.mplot3d import Axes3D

print('Warning: This is an optimization parameter choice visualization tool only intended for use if you have also performed a parameter sweep.')


if len(sys.argv) == 3:
    big_file_path = sys.argv[1]
    small_file_path = sys.argv[2]
else:
    big_file_path = filedialog.askopenfilename(title='Choose the file with parameter sweep data')
    small_file_path = filedialog.askopenfilename(title='Choose the file with optimization data')

with open(small_file_path,'r') as file:
    dlines = file.readlines()                #Total number of data rows
col_names = dlines[0].split()

input_params = []
ip = True
opt_params = []
for n in col_names:
    if n.startswith('S('):
        ip = False
        opt_params.append(n)
    if ip==True:
        input_params.append(n)
if len(input_params) >2:
    print('This plotting tool is two dimensional, and therefore can only show results in the space made by 2 out of your ' + str(len(input_params)) + ' input parameters. Please enter the names of the two input parameters you would like to have plotted.')
    while True:
        try: 
            ip_1_str = input('Input parameter 1: ')
            ip_1 = input_params.index(ip_1_str)
            break
        except IndexError:
            inputs_string = ''
            for i in range(len(input_parameters)):
                inputs_string += input_parameters[i]
                if i != input_parameters-1:
                    inputs_string += ', '
            print('That input parameter is not recognized. Please enter one of the following: ' + inputs_string + '.')
    while True:
        try: 
            ip_2_str = input('Input parameter 2: ')
            ip_2 = input_params.index(ip_2_str)
            break
        except IndexError:
            inputs_string = ''
            for i in range(len(input_parameters)):
                inputs_string += input_parameters[i]
                if i != input_parameters-1:
                    inputs_string += ', '
            print('That input parameter is not recognized. Please enter one of the following: ' + inputs_string + '.')
else:
    ip_1 = 0
    ip_2 = 1

#contains input parameters of interest
swp_data = np.zeros((len(dlines)-1,2))
for i in range(len(dlines)-1):
    dline = dlines[i+1].split()
    swp_data[i][0] = dline[ip_1]
    swp_data[i][1] = dline[ip_2]
    
with open(big_file_path, 'r') as file:
    lines = file.readlines()
    
if len(opt_params) > 1:
    print('Please choose one of your optimized parameters to view.')
    freq = input('Enter the frequency you want to isolate: ')
    s_param = input('Enter the S parameter you want to isolate: ')
else:
    s_param, freq = opt_params[0].split('_')
    
    
def parse_file(S_param, freq, lines):
    
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
        if float(linesplit[freq_index]) == freq:
            key = ()
            for k in range(freq_index):
                key = key + (float(linesplit[k]),)

            data[key] = float(linesplit[s_index])
    return data
    
data = parse_file(s_param, float(freq), lines)

x_vals = sorted(set(k[0] for k in data.keys()))
y_vals = sorted(set(k[1] for k in data.keys()))

# Create a 2D array from the dictionary
heatmap = np.array([[data.get((x, y), np.nan) for y in y_vals] for x in x_vals])
mode = input('2D/3D? ')

fntsz = 14
fdict = {'family': 'serif', 'weight': 'normal', 'size': fntsz}
high_res_cmap = colormaps.get_cmap('jet').resampled(2000)

if mode == '2D':
    mode_1 = input('animate/still? ')
    
    if mode_1 == 'still':
        plt.plot()
        img = plt.imshow(heatmap, origin='lower', cmap=high_res_cmap, vmin=0, vmax=0.08, extent=[min(y_vals), max(y_vals), min(x_vals), max(x_vals)])
        cbar = plt.colorbar(img)
        cbar.set_label('S(1,1) at 12 GHz', fontdict=fdict)
        #cbar.set_label(str(s_param)+'_'+str(freq), fontdict=fdict)
        #plt.ylabel(input_params[ip_1], fontdict=fdict)
        #plt.xlabel(input_params[ip_2], fontdict=fdict)
        plt.xlim(min(y_vals), max(y_vals))
        plt.ylim(min(x_vals), max(x_vals))
        
        plt.xlabel('waveguide width [mm]', fontdict=fdict)
        plt.ylabel('chamfer length [mm]', fontdict=fdict)
        plt.title('Parameter Space Exploration', fontdict=fdict)

        num_arrows = len(swp_data[:,0])
        colors = [str(0.7/(num_arrows) * i) for i in range(num_arrows)]

        for i in range(num_arrows):
            plt.scatter(swp_data[i,1], swp_data[i,0], color=colors[i], zorder=3)
        plt.show()

    elif mode_1 == 'animate':
        fig, ax = plt.subplots()
        img = ax.imshow(heatmap, origin='lower', cmap=high_res_cmap, vmin=0, vmax=0.08, extent=[min(y_vals), max(y_vals), min(x_vals), max(x_vals)])
        cbar = plt.colorbar(img)
        cbar.set_label(str(s_param)+'_'+str(freq), fontdict=fdict)
        ax.set_ylabel(input_params[ip_1], fontdict=fdict)
        ax.set_xlabel(input_params[ip_2], fontdict=fdict)
        line, = ax.plot([],[], 'o', color='black')
        ax.set_xlim(min(y_vals), max(y_vals))
        ax.set_ylim(min(x_vals), max(x_vals))

        def init():
            line.set_data([],[])
            return line,

        def animate(i):
            x=  swp_data[:i,1]
            y = swp_data[:i,0]
            line.set_data(x,y)
            return line,

        ani = animation.FuncAnimation(fig,animate,init_func=init,frames=len(swp_data[:,0]), interval=20,blit=False)
        ani.save('param_space_exploration.gif', writer='pillow', fps=5)
    
elif mode == '3D':
    X, Y = np.meshgrid(x_vals,y_vals)
    Z = heatmap
    
    fig = plt.figure()
    ax = fig.add_subplot(111,projection='3d')
    ax.plot_surface(X,Y,Z, cmap=high_res_cmap)
    ax.set_ylabel(input_params[ip_2], fontdict=fdict)
    ax.set_xlabel(input_params[ip_1], fontdict=fdict)
    ax.set_zlabel(str(s_param)+' at '+str(freq), fontdict=fdict)
    plt.show()