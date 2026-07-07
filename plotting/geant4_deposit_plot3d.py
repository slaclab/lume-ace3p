import sys
import numpy as np
import tkinter as tk
from tkinter import filedialog
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.widgets import Slider, RadioButtons
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  (enables 3D projection)

# Interactive 3D viewer for Geant4 dose / energy-deposit output produced by the
# geant4_track3p_beta example (doseDeposit.txt / energyDeposit.txt). It shows a
# 3D scatter of the nonzero voxels of the scoring mesh, colored on a log scale
# by the deposited value (or linearly by entry count). A threshold slider hides
# the weakest voxels so the beam channel stands out even when the mesh is dense.
#
# Voxels are placed in accelerator coordinates: the beam axis Z (the long mesh
# direction) is drawn horizontally, with the transverse X and Y as the other
# two axes.
#
# For a fast 2D slice view use plotting/geant4_deposit_plot.py; for a smooth
# volumetric (opacity) render use plotting/geant4_deposit_volume.py.
#
# Usage:  python plotting/geant4_deposit_plot3d.py [doseDeposit.txt]
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

grid = np.zeros((nx, ny, nz))
grid[ix, iy, iz] = value
entry_grid = np.zeros((nx, ny, nz))
entry_grid[ix, iy, iz] = entry

vlabel = scorer_name + (' [' + units + ']' if units else '')
print('Loaded %s: scorer "%s", grid %d x %d x %d, %d nonzero voxels'
      % (mesh_name, scorer_name, nx, ny, nz, int(np.count_nonzero(grid))))

fntsz = 14
fdict = {'family': 'serif', 'weight': 'normal', 'size': fntsz}

# Positive floor for the log color scale.
nonzero = grid[grid > 0]
vmin = nonzero.min() if nonzero.size else 1.0
vmax = grid.max() if nonzero.size else 1.0
if vmax <= vmin:
    vmax = vmin * 10.0

fig = plt.figure(figsize=(14, 9))
ax = fig.add_axes([0.24, 0.12, 0.62, 0.82], projection='3d')
cax = fig.add_axes([0.89, 0.20, 0.02, 0.60])

# Radio buttons to pick which quantity to color by.
data_ax = fig.add_axes([0.03, 0.62, 0.16, 0.14])
data_radio = RadioButtons(data_ax, (vlabel, 'entries'), active=0)
data_ax.set_title('Color by', fontdict={'size': 12})

# Slider to hide voxels below a percentile of the nonzero values.
thr_ax = fig.add_axes([0.30, 0.04, 0.50, 0.03])
thr_slider = Slider(thr_ax, 'hide below\npercentile', 0, 100, valinit=0,
                    valstep=1)

# Marker-size slider (visual only).
size_ax = fig.add_axes([0.30, 0.005, 0.50, 0.02])
size_slider = Slider(size_ax, 'marker size', 5, 200, valinit=60, valstep=5)

state = {'source': 'value'}


def current_grid():
    return grid if state['source'] == 'value' else entry_grid


def current_label():
    return vlabel if state['source'] == 'value' else 'entries'


def redraw():
    g = current_grid()
    gx, gy, gz = np.nonzero(g)
    vals = g[gx, gy, gz]

    ax.clear()
    cax.clear()

    if vals.size:
        pct = thr_slider.val
        thresh = np.percentile(vals, pct) if pct > 0 else 0.0
        keep = vals >= thresh
        gx, gy, gz, vals = gx[keep], gy[keep], gz[keep], vals[keep]

    if vals.size:
        if state['source'] == 'value':
            norm = LogNorm(vmin=vmin, vmax=vmax)
            cmap = 'YlOrRd'          # near-white low end -> smooth from empty
        else:
            norm = plt.Normalize(vmin=0, vmax=max(entry_grid.max(), 1))
            cmap = 'viridis'
        # Accelerator convention: beam axis Z horizontal (plot x), X & Y transverse.
        # Faint edges keep pale (low-value) markers visible on the white panes.
        sc = ax.scatter(gz, gx, gy, c=vals, norm=norm, cmap=cmap,
                        s=size_slider.val, depthshade=False,
                        edgecolors='gray', linewidths=0.3, alpha=0.85)
        cbar = fig.colorbar(sc, cax=cax)
        cbar.set_label(current_label(), fontdict=fdict)

    ax.set_xlim(0, nz - 1)
    ax.set_ylim(0, nx - 1)
    ax.set_zlim(0, ny - 1)
    ax.set_xlabel('iZ (beam axis)', fontdict=fdict)
    ax.set_ylabel('iX', fontdict=fdict)
    ax.set_zlabel('iY', fontdict=fdict)
    ax.set_title('%s  |  %s' % (mesh_name, current_label()), fontdict=fdict)
    fig.canvas.draw_idle()


def on_data(label):
    state['source'] = 'value' if label == vlabel else 'entries'
    redraw()


thr_slider.on_changed(lambda v: redraw())
size_slider.on_changed(lambda v: redraw())
data_radio.on_clicked(on_data)

redraw()
plt.show()
