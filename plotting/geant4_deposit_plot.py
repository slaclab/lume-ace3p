import os
import sys
import numpy as np
import tkinter as tk
from tkinter import filedialog
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.widgets import Slider, RadioButtons

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geant4_deposit_common import (parse_deposit_file, is_yaml_file,
                                   load_sweep, load_sweep_deposit)

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
# Alternatively, a LUME-ACE3P sweep YAML (e.g. geant4_track3p_beta.yaml) may be
# passed. The sweep folders (one per swept value, e.g. beta) are discovered from
# 'workflow_parameters' / 'workdir', and the tool adds a slider per swept
# variable to choose the folder plus a dose/energy toggle to choose which
# deposit file in that folder to load.
#
# Usage:  python plotting/geant4_deposit_plot.py [doseDeposit.txt | sweep.yaml]
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
# D holds the currently displayed grids and derived quantities. In sweep mode
# it is refreshed by reload() whenever a slider / toggle changes.
D = {}
sweep = None
scalar_idx = []          # per-axis current index into axis 'values' (sweep mode)

if is_yaml_file(file_path):
    sweep = load_sweep(file_path)
    scalar_idx = [0] * len(sweep.axes)
    state_source = 'dose' if sweep.dose_name else 'edep'
else:
    state_source = 'dose'


def _empty_parsed():
    """A 1x1x1 zero grid so redraw() has something to show when a sweep file
    is missing."""
    return {'mesh_name': '(missing)', 'scorer_name': 'value', 'units': '',
            'vlabel': 'value', 'grid': np.zeros((1, 1, 1)),
            'entry_grid': np.zeros((1, 1, 1)), 'nx': 1, 'ny': 1, 'nz': 1}


def apply_parsed(parsed):
    """Copy a parse_deposit_file dict into D and recompute derived state."""
    if parsed is None:
        parsed = _empty_parsed()
    D.update(parsed)
    grid = D['grid']
    # Positive floor for the log color scale (smallest nonzero value).
    nonzero = grid[grid > 0]
    D['vmin'] = nonzero.min() if nonzero.size else 1.0
    D['vmax'] = grid.max() if nonzero.size else 1.0
    if D['vmax'] <= D['vmin']:
        D['vmax'] = D['vmin'] * 10.0
    D['sizes'] = {'X': D['nx'], 'Y': D['ny'], 'Z': D['nz']}
    print('Loaded %s: scorer "%s", grid %d x %d x %d, %d nonzero voxels'
          % (D['mesh_name'], D['scorer_name'], D['nx'], D['ny'], D['nz'],
             int(np.count_nonzero(grid))))


if sweep is None:
    apply_parsed(parse_deposit_file(file_path))
else:
    _init_scalars = tuple(sweep.axes[i]['values'][scalar_idx[i]]
                          for i in range(len(sweep.axes)))
    apply_parsed(load_sweep_deposit(sweep, _init_scalars, state_source))

# --- Slice geometry -------------------------------------------------------
AXES = ['X', 'Y', 'Z']

fntsz = 16
fdict = {'family': 'serif', 'weight': 'normal', 'size': fntsz}

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
data_radio = RadioButtons(data_ax, (D['vlabel'], 'entries'), active=0)
data_ax.set_title('Color by', fontdict={'size': 12})

# In sweep mode the slice slider and the beta slider(s) share the bottom, so
# lift the slice slider to make room; otherwise keep the original position.
if sweep is None:
    slice_ax = fig.add_axes([0.30, 0.06, 0.55, 0.03])
else:
    slice_ax = fig.add_axes([0.30, 0.16, 0.55, 0.03])
slice_slider = Slider(slice_ax, 'slice index', 0, D['nz'] - 1,
                      valinit=D['nz'] // 2, valstep=1)

# Sweep-mode controls: a dose/energy toggle and one index slider per swept var.
source_radio = None
beta_sliders = []
if sweep is not None:
    src_opts = []
    if sweep.dose_name:
        src_opts.append('dose')
    if sweep.edep_name:
        src_opts.append('energy')
    source_ax = fig.add_axes([0.03, 0.14, 0.16, 0.12])
    source_radio = RadioButtons(source_ax, tuple(src_opts),
                                active=src_opts.index('dose'
                                    if state_source == 'dose' else 'energy'))
    source_ax.set_title('Deposit file', fontdict={'size': 12})

    for i, axis in enumerate(sweep.axes):
        sax = fig.add_axes([0.30, 0.02 + i * 0.045, 0.55, 0.025])
        n = len(axis['values'])
        s = Slider(sax, axis['name'], 0, n - 1, valinit=scalar_idx[i],
                   valstep=1)
        s.valtext.set_text(str(axis['values'][scalar_idx[i]]))
        beta_sliders.append(s)

state = {'axis': 'Z', 'source': 'value'}


def current_grid():
    return D['grid'] if state['source'] == 'value' else D['entry_grid']


def current_label():
    return D['vlabel'] if state['source'] == 'value' else 'entries'


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
    idx = min(int(slice_slider.val), D['sizes'][axis] - 1)
    sl = get_slice(axis, idx)
    xlab, ylab = plane_labels(axis)

    ax.clear()
    cax.clear()

    # Log color scale (zeros masked) for the deposited value; entries are small
    # integer counts, so a linear scale reads better there.
    if state['source'] == 'value':
        masked = np.ma.masked_less_equal(sl, 0.0)
        im = ax.imshow(masked, origin='lower', aspect='auto',
                       norm=LogNorm(vmin=D['vmin'], vmax=D['vmax']),
                       cmap=VALUE_CMAP)
    else:
        emax = D['entry_grid'].max() if D['entry_grid'].max() > 0 else 1
        im = ax.imshow(sl, origin='lower', aspect='auto', vmin=0, vmax=emax,
                       cmap='viridis')

    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label(current_label(), fontdict=fdict)
    ax.set_xlabel(xlab, fontdict=fdict)
    ax.set_ylabel(ylab, fontdict=fdict)
    title = '%s  |  %s = %d' % (D['mesh_name'], axis, idx)
    if sweep is not None:
        pt = '  '.join('%s=%s' % (sweep.axes[i]['name'],
                                  sweep.axes[i]['values'][scalar_idx[i]])
                       for i in range(len(sweep.axes)))
        title = title + '   [' + pt + ']'
    ax.set_title(title, fontdict=fdict)
    ax.tick_params(labelsize=fntsz - 4)
    fig.canvas.draw_idle()


def reload():
    """Reload the deposit grid for the current sweep point / source, then
    refresh the color-by label and slice-slider range before redrawing."""
    scalars = tuple(sweep.axes[i]['values'][scalar_idx[i]]
                    for i in range(len(sweep.axes)))
    apply_parsed(load_sweep_deposit(sweep, scalars, state['source_file']))
    # The value label may change between dose and energy files.
    data_radio.labels[0].set_text(D['vlabel'])
    slice_slider.valmax = D['nz'] - 1
    slice_ax.set_xlim(0, D['nz'] - 1)
    if slice_slider.val > D['nz'] - 1:
        slice_slider.set_val(D['nz'] // 2)
    redraw()


def on_slice(val):
    redraw()


def on_axis(label):
    axis = label.split()[-1]           # "slice along Z" -> "Z"
    state['axis'] = axis
    n = D['sizes'][axis]
    # Reconfigure the slider range for the new axis.
    slice_slider.valmax = n - 1
    slice_ax.set_xlim(0, n - 1)
    if slice_slider.val > n - 1:
        slice_slider.set_val(n // 2)
    else:
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


slice_slider.on_changed(on_slice)
axis_radio.on_clicked(on_axis)
data_radio.on_clicked(on_data)

if sweep is not None:
    state['source_file'] = state_source
    if source_radio is not None:
        source_radio.on_clicked(on_source)
    for i, s in enumerate(beta_sliders):
        s.on_changed(make_on_beta(i))

redraw()
plt.show()
