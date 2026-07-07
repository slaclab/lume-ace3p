import sys
import numpy as np
import tkinter as tk
from tkinter import filedialog
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.widgets import Slider, RadioButtons

# Sequential colormap whose low end is near-white, so nonzero voxels emerge
# smoothly from the (white) empty background instead of jumping to black.
VALUE_CMAP = mpl.colormaps['YlOrRd'].copy()
VALUE_CMAP.set_bad('white')          # zero / masked voxels render as background

# Interactive viewer for Geant4 dose / energy-deposit output produced by the
# geant4_track3p_beta example (doseDeposit.txt / energyDeposit.txt). The files
# are comma-separated voxel grids with the columns
#     iX, iY, iZ, total(value), total(val^2), entry
# over the scoring mesh defined in the Geant4 input file (mesh_nx x mesh_ny x
# mesh_nz). The mesh name, scorer name, and value units are read from the three
# comment lines at the top of the file.
#
# Usage:  python plotting/geant4_deposit_plot.py [doseDeposit.txt]
# If no file is given on the command line, a file dialog is opened.

root = tk.Tk()
root.withdraw()

if len(sys.argv) == 2:
    file_path = sys.argv[1]
else:
    file_path = filedialog.askopenfilename(          # Prompt for file to load
        title='Choose a Geant4 dose / energy-deposit file')

if len(file_path) == 0:
    sys.exit()

# --- Parse the file -------------------------------------------------------
with open(file_path, 'r') as file:
    dlines = file.readlines()

mesh_name = 'mesh'
scorer_name = 'value'
units = ''
data_rows = []
for line in dlines:
    line = line.strip()
    if not line:
        continue
    if line.startswith('#'):
        text = line.lstrip('#').strip()
        if text.startswith('mesh name:'):
            mesh_name = text.split(':', 1)[1].strip()
        elif text.startswith('primitive scorer name:'):
            scorer_name = text.split(':', 1)[1].strip()
        elif 'total(value)' in text:
            # e.g. "iX, iY, iZ, total(value) [Gy], total(val^2), entry"
            if '[' in text and ']' in text:
                units = text[text.index('[') + 1:text.index(']')].strip()
        continue
    data_rows.append(line.split(','))

if len(data_rows) == 0:
    print('No data rows found in file.')
    sys.exit()

raw = np.array(data_rows, dtype=float)
ix = raw[:, 0].astype(int)
iy = raw[:, 1].astype(int)
iz = raw[:, 2].astype(int)
value = raw[:, 3]        # total deposited value (Gy or eV)
entry = raw[:, 5]        # number of scoring entries in the voxel

nx = ix.max() + 1
ny = iy.max() + 1
nz = iz.max() + 1

# Fill dense grids (missing voxels stay zero).
grid = np.zeros((nx, ny, nz))
grid[ix, iy, iz] = value
entry_grid = np.zeros((nx, ny, nz))
entry_grid[ix, iy, iz] = entry

vlabel = scorer_name + (' [' + units + ']' if units else '')
print('Loaded %s: scorer "%s", grid %d x %d x %d, %d nonzero voxels'
      % (mesh_name, scorer_name, nx, ny, nz, int(np.count_nonzero(grid))))

# --- Slice geometry -------------------------------------------------------
# axis index -> (name, in-plane axis labels, in-plane sizes)
AXES = ['X', 'Y', 'Z']
sizes = {'X': nx, 'Y': ny, 'Z': nz}

fntsz = 16
fdict = {'family': 'serif', 'weight': 'normal', 'size': fntsz}

# Positive floor for the log color scale (smallest nonzero value in the file).
nonzero = grid[grid > 0]
vmin = nonzero.min() if nonzero.size else 1.0
vmax = grid.max() if nonzero.size else 1.0
if vmax <= vmin:
    vmax = vmin * 10.0

fig = plt.figure(figsize=(14, 9))
ax = fig.add_axes([0.30, 0.15, 0.55, 0.78])
cax = fig.add_axes([0.88, 0.15, 0.025, 0.78])   # dedicated colorbar axis

# Radio buttons to pick the slice-normal axis.
axis_ax = fig.add_axes([0.03, 0.55, 0.16, 0.20])
axis_radio = RadioButtons(axis_ax, ('slice along X', 'slice along Y',
                                     'slice along Z'), active=2)
axis_ax.set_title('Slice normal', fontdict={'size': 12})

# Radio buttons to pick which quantity to color by.
data_ax = fig.add_axes([0.03, 0.32, 0.16, 0.15])
data_radio = RadioButtons(data_ax, (vlabel, 'entries'), active=0)
data_ax.set_title('Color by', fontdict={'size': 12})

# Slider to step through slices along the chosen normal axis.
slice_ax = fig.add_axes([0.30, 0.06, 0.55, 0.03])
slice_slider = Slider(slice_ax, 'slice index', 0, nz - 1, valinit=nz // 2,
                      valstep=1)

state = {'axis': 'Z', 'source': 'value'}


def current_grid():
    return grid if state['source'] == 'value' else entry_grid


def current_label():
    return vlabel if state['source'] == 'value' else 'entries'


def get_slice(axis, idx):
    # Accelerator convention: Z (beam axis) is horizontal whenever it is in the
    # slice plane; the transverse (Z-normal) slice puts X horizontal, Y vertical.
    g = current_grid()
    if axis == 'X':
        return g[idx, :, :]            # rows = Y, cols = Z
    if axis == 'Y':
        return g[:, idx, :]            # rows = X, cols = Z
    return g[:, :, idx].T              # rows = Y, cols = X (slice along Z)


def plane_labels(axis):
    # returns (horizontal label, vertical label)
    if axis == 'X':
        return 'iZ', 'iY'
    if axis == 'Y':
        return 'iZ', 'iX'
    return 'iX', 'iY'


def redraw():
    axis = state['axis']
    idx = int(slice_slider.val)
    sl = get_slice(axis, idx)
    xlab, ylab = plane_labels(axis)

    ax.clear()
    cax.clear()

    # Log color scale (zeros masked) for the deposited value; entries are small
    # integer counts, so a linear scale reads better there.
    if state['source'] == 'value':
        masked = np.ma.masked_less_equal(sl, 0.0)
        im = ax.imshow(masked, origin='lower', aspect='auto',
                       norm=LogNorm(vmin=vmin, vmax=vmax), cmap=VALUE_CMAP)
    else:
        emax = entry_grid.max() if entry_grid.max() > 0 else 1
        im = ax.imshow(sl, origin='lower', aspect='auto', vmin=0, vmax=emax,
                       cmap='viridis')

    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label(current_label(), fontdict=fdict)
    ax.set_xlabel(xlab, fontdict=fdict)
    ax.set_ylabel(ylab, fontdict=fdict)
    ax.set_title('%s  |  %s = %d' % (mesh_name, axis, idx), fontdict=fdict)
    ax.tick_params(labelsize=fntsz - 4)
    fig.canvas.draw_idle()


def on_slice(val):
    redraw()


def on_axis(label):
    axis = label.split()[-1]           # "slice along Z" -> "Z"
    state['axis'] = axis
    n = sizes[axis]
    # Reconfigure the slider range for the new axis.
    slice_slider.valmax = n - 1
    slice_ax.set_xlim(0, n - 1)
    if slice_slider.val > n - 1:
        slice_slider.set_val(n // 2)
    else:
        redraw()


def on_data(label):
    state['source'] = 'value' if label == vlabel else 'entries'
    redraw()


slice_slider.on_changed(on_slice)
axis_radio.on_clicked(on_axis)
data_radio.on_clicked(on_data)

redraw()
plt.show()
