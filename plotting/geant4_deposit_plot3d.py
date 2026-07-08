import os
import sys
import numpy as np
import tkinter as tk
from tkinter import filedialog
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.widgets import Slider, RadioButtons
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  (enables 3D projection)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geant4_deposit_common import (parse_deposit_file, is_yaml_file,
                                   load_sweep, load_sweep_deposit)

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
# Alternatively, a LUME-ACE3P sweep YAML (e.g. geant4_track3p_beta.yaml) may be
# passed. The sweep folders (one per swept value, e.g. beta) are discovered from
# 'workflow_parameters' / 'workdir', and the tool adds a slider per swept
# variable to choose the folder plus a dose/energy toggle to choose which
# deposit file in that folder to load.
#
# For a fast 2D slice view use plotting/geant4_deposit_plot.py; for a smooth
# volumetric (opacity) render use plotting/geant4_deposit_volume.py.
#
# Usage:  python plotting/geant4_deposit_plot3d.py [doseDeposit.txt | sweep.yaml]
# If no file is given on the command line, a file dialog is opened.

root = tk.Tk()
root.withdraw()

if len(sys.argv) == 2:
    file_path = sys.argv[1]
else:
    file_path = filedialog.askopenfilename(          # Prompt for file to load
        title='Choose a Geant4 deposit file or LUME-ACE3P sweep YAML')

if len(file_path) == 0:
    sys.exit()

# --- Load: either a single deposit file or a sweep YAML -------------------
D = {}
sweep = None
scalar_idx = []

if is_yaml_file(file_path):
    sweep = load_sweep(file_path)
    scalar_idx = [0] * len(sweep.axes)
    state_source = 'dose' if sweep.dose_name else 'edep'
else:
    state_source = 'dose'


def _empty_parsed():
    return {'mesh_name': '(missing)', 'scorer_name': 'value', 'units': '',
            'vlabel': 'value', 'grid': np.zeros((1, 1, 1)),
            'entry_grid': np.zeros((1, 1, 1)), 'nx': 1, 'ny': 1, 'nz': 1}


def apply_parsed(parsed):
    if parsed is None:
        parsed = _empty_parsed()
    D.update(parsed)
    grid = D['grid']
    nonzero = grid[grid > 0]
    D['vmin'] = nonzero.min() if nonzero.size else 1.0
    D['vmax'] = grid.max() if nonzero.size else 1.0
    if D['vmax'] <= D['vmin']:
        D['vmax'] = D['vmin'] * 10.0
    print('Loaded %s: scorer "%s", grid %d x %d x %d, %d nonzero voxels'
          % (D['mesh_name'], D['scorer_name'], D['nx'], D['ny'], D['nz'],
             int(np.count_nonzero(grid))))


if sweep is None:
    apply_parsed(parse_deposit_file(file_path))
else:
    _init_scalars = tuple(sweep.axes[i]['values'][scalar_idx[i]]
                          for i in range(len(sweep.axes)))
    apply_parsed(load_sweep_deposit(sweep, _init_scalars, state_source))

fntsz = 14
fdict = {'family': 'serif', 'weight': 'normal', 'size': fntsz}

fig = plt.figure(figsize=(14, 9))
ax = fig.add_axes([0.24, 0.12, 0.62, 0.82], projection='3d')
cax = fig.add_axes([0.89, 0.20, 0.02, 0.60])

# Radio buttons to pick which quantity to color by.
data_ax = fig.add_axes([0.03, 0.62, 0.16, 0.14])
data_radio = RadioButtons(data_ax, (D['vlabel'], 'entries'), active=0)
data_ax.set_title('Color by', fontdict={'size': 12})

# Slider to hide voxels below a percentile of the nonzero values.
thr_ax = fig.add_axes([0.30, 0.04, 0.50, 0.03])
thr_slider = Slider(thr_ax, 'hide below\npercentile', 0, 100, valinit=0,
                    valstep=1)

# Marker-size slider (visual only).
size_ax = fig.add_axes([0.30, 0.005, 0.50, 0.02])
size_slider = Slider(size_ax, 'marker size', 5, 200, valinit=60, valstep=5)

# Sweep-mode controls: a dose/energy toggle and one index slider per swept var.
source_radio = None
beta_sliders = []
if sweep is not None:
    src_opts = []
    if sweep.dose_name:
        src_opts.append('dose')
    if sweep.edep_name:
        src_opts.append('energy')
    source_ax = fig.add_axes([0.03, 0.40, 0.16, 0.12])
    source_radio = RadioButtons(source_ax, tuple(src_opts),
                                active=src_opts.index('dose'
                                    if state_source == 'dose' else 'energy'))
    source_ax.set_title('Deposit file', fontdict={'size': 12})

    for i, axis in enumerate(sweep.axes):
        sax = fig.add_axes([0.03, 0.30 - i * 0.05, 0.16, 0.03])
        n = len(axis['values'])
        s = Slider(sax, axis['name'], 0, n - 1, valinit=scalar_idx[i],
                   valstep=1)
        s.valtext.set_text(str(axis['values'][scalar_idx[i]]))
        beta_sliders.append(s)

state = {'source': 'value'}


def current_grid():
    return D['grid'] if state['source'] == 'value' else D['entry_grid']


def current_label():
    return D['vlabel'] if state['source'] == 'value' else 'entries'


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
            norm = LogNorm(vmin=D['vmin'], vmax=D['vmax'])
            cmap = 'YlOrRd'          # near-white low end -> smooth from empty
        else:
            emax = max(D['entry_grid'].max(), 1)
            norm = plt.Normalize(vmin=0, vmax=emax)
            cmap = 'viridis'
        # Accelerator convention: beam axis Z horizontal (plot x), X & Y transverse.
        # Faint edges keep pale (low-value) markers visible on the white panes.
        sc = ax.scatter(gz, gx, gy, c=vals, norm=norm, cmap=cmap,
                        s=size_slider.val, depthshade=False,
                        edgecolors='gray', linewidths=0.3, alpha=0.85)
        cbar = fig.colorbar(sc, cax=cax)
        cbar.set_label(current_label(), fontdict=fdict)

    ax.set_xlim(0, D['nz'] - 1)
    ax.set_ylim(0, D['nx'] - 1)
    ax.set_zlim(0, D['ny'] - 1)
    ax.set_xlabel('iZ (beam axis)', fontdict=fdict)
    ax.set_ylabel('iX', fontdict=fdict)
    ax.set_zlabel('iY', fontdict=fdict)
    title = '%s  |  %s' % (D['mesh_name'], current_label())
    if sweep is not None:
        pt = '  '.join('%s=%s' % (sweep.axes[i]['name'],
                                  sweep.axes[i]['values'][scalar_idx[i]])
                       for i in range(len(sweep.axes)))
        title = title + '   [' + pt + ']'
    ax.set_title(title, fontdict=fdict)
    fig.canvas.draw_idle()


def reload():
    scalars = tuple(sweep.axes[i]['values'][scalar_idx[i]]
                    for i in range(len(sweep.axes)))
    apply_parsed(load_sweep_deposit(sweep, scalars, state['source_file']))
    data_radio.labels[0].set_text(D['vlabel'])
    redraw()


def on_data(label):
    state['source'] = 'value' if label == D['vlabel'] else 'entries'
    redraw()


def on_source(label):
    state['source_file'] = 'dose' if label == 'dose' else 'edep'
    reload()


def make_on_beta(i):
    def handler(val):
        scalar_idx[i] = int(val)
        beta_sliders[i].valtext.set_text(
            str(sweep.axes[i]['values'][scalar_idx[i]]))
        reload()
    return handler


thr_slider.on_changed(lambda v: redraw())
size_slider.on_changed(lambda v: redraw())
data_radio.on_clicked(on_data)

if sweep is not None:
    state['source_file'] = state_source
    if source_radio is not None:
        source_radio.on_clicked(on_source)
    for i, s in enumerate(beta_sliders):
        s.on_changed(make_on_beta(i))

redraw()
plt.show()
