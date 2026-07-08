import os
import sys
import numpy as np

# Optional volumetric (opacity) viewer for Geant4 dose / energy-deposit output
# produced by the geant4_track3p_beta example (doseDeposit.txt /
# energyDeposit.txt). It renders the scoring mesh as a smooth 3D volume whose
# opacity scales with the deposited value, which reads well for dense (high
# beta) runs. The value is log-compressed first, since deposits span many
# orders of magnitude.
#
# Alternatively, a LUME-ACE3P sweep YAML (e.g. geant4_track3p_beta.yaml) may be
# passed. The sweep folders (one per swept value, e.g. beta) are discovered from
# 'workflow_parameters' / 'workdir', and the tool adds a slider per swept
# variable to choose the folder plus a dose/energy toggle to choose which
# deposit file in that folder to load.
#
# This tool needs PyVista (and VTK) and an interactive / GPU-capable session:
#     pip install pyvista
# For a dependency-free view use plotting/geant4_deposit_plot3d.py (3D scatter)
# or plotting/geant4_deposit_plot.py (2D slices).
#
# Voxels are placed in accelerator coordinates: the beam axis Z (the long mesh
# direction) is the first spatial dimension, with transverse X and Y after it.
#
# Usage:  python plotting/geant4_deposit_volume.py [doseDeposit.txt | sweep.yaml]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geant4_deposit_common import (parse_deposit_file, is_yaml_file,
                                   load_sweep, load_sweep_deposit)

try:
    import pyvista as pv
except ImportError:
    print('This tool requires PyVista.  Install it with:  pip install pyvista')
    print('For a dependency-free 3D view use geant4_deposit_plot3d.py instead.')
    sys.exit(1)

if len(sys.argv) == 2:
    file_path = sys.argv[1]
else:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title='Choose a Geant4 deposit file or LUME-ACE3P sweep YAML')

if len(file_path) == 0:
    sys.exit()

# --- Load: either a single deposit file or a sweep YAML -------------------
sweep = None
scalar_idx = []
state = {'source_file': 'dose'}

if is_yaml_file(file_path):
    sweep = load_sweep(file_path)
    scalar_idx = [0] * len(sweep.axes)
    state['source_file'] = 'dose' if sweep.dose_name else 'edep'


def make_igrid(parsed):
    """Build a PyVista ImageData (log-compressed opacity field) from a parsed
    deposit dict, or None if there are no nonzero voxels. Returns (igrid,
    vlabel, mesh_name)."""
    if parsed is None:
        return None, 'value', '(missing)'
    grid = parsed['grid']
    vlabel = parsed['vlabel']
    nonzero = grid[grid > 0]
    if nonzero.size == 0:
        return None, vlabel, parsed['mesh_name']

    # Map nonzero values to log10; leave zero voxels at the floor (fully clear).
    floor = np.log10(nonzero.min())
    logval = np.full(grid.shape, floor)
    logval[grid > 0] = np.log10(grid[grid > 0])

    # Accelerator convention: beam axis Z first, then transverse X, Y.
    logval = np.transpose(logval, (2, 0, 1))   # (nz, nx, ny)

    # PyVista expects point/cell scalars in Fortran order for these dimensions.
    igrid = pv.ImageData(dimensions=np.array(logval.shape) + 1)
    igrid.cell_data[vlabel] = logval.flatten(order='F')
    print('Loaded %s: scorer "%s", grid %d x %d x %d, %d nonzero voxels'
          % (parsed['mesh_name'], parsed['scorer_name'], parsed['nx'],
             parsed['ny'], parsed['nz'], int(nonzero.size)))
    return igrid, vlabel, parsed['mesh_name']


def load_current():
    """Parse the deposit file for the current file_path / sweep point."""
    if sweep is None:
        return parse_deposit_file(file_path)
    scalars = tuple(sweep.axes[i]['values'][scalar_idx[i]]
                    for i in range(len(sweep.axes)))
    return load_sweep_deposit(sweep, scalars, state['source_file'])


# Opacity ramps from transparent (low) to opaque (high) across the log range.
OPACITY = [0.0, 0.02, 0.08, 0.2, 0.45, 0.8]

p = pv.Plotter()
_actor = {'volume': None, 'text': None}


def render():
    """(Re)build and show the volume for the current selection."""
    parsed = load_current()
    igrid, vlabel, mesh_name = make_igrid(parsed)

    if _actor['volume'] is not None:
        p.remove_actor(_actor['volume'])
        _actor['volume'] = None
    if _actor['text'] is not None:
        p.remove_actor(_actor['text'])
        _actor['text'] = None

    pt = ''
    if sweep is not None:
        pt = '   [' + '  '.join(
            '%s=%s' % (sweep.axes[i]['name'],
                       sweep.axes[i]['values'][scalar_idx[i]])
            for i in range(len(sweep.axes))) + ']'

    if igrid is None:
        _actor['text'] = p.add_text('%s  |  no nonzero voxels%s'
                                    % (mesh_name, pt), font_size=10)
    else:
        _actor['volume'] = p.add_volume(
            igrid, scalars=vlabel, cmap='inferno', opacity=OPACITY,
            scalar_bar_args={'title': 'log10 ' + vlabel})
        _actor['text'] = p.add_text('%s  |  log10 %s%s'
                                    % (mesh_name, vlabel, pt), font_size=10)
    p.render()


p.add_axes(xlabel='Z (beam)', ylabel='X', zlabel='Y')
p.show_grid(xtitle='iZ (beam axis)', ytitle='iX', ztitle='iY')

# Sweep-mode interactivity: a dose/energy toggle plus one slider per swept var.
if sweep is not None:
    has_dose = bool(sweep.dose_name)
    has_edep = bool(sweep.edep_name)

    def on_source(flag):
        # Checkbox on -> energy, off -> dose (only wired when both exist).
        state['source_file'] = 'edep' if flag else 'dose'
        render()

    if has_dose and has_edep:
        p.add_checkbox_button_widget(
            on_source, value=(state['source_file'] == 'edep'),
            position=(10, 10), size=30)
        p.add_text('energy (on) / dose (off)', position=(50, 15),
                   font_size=8)

    def make_on_beta(i):
        values = sweep.axes[i]['values']

        def handler(val):
            # Snap the continuous slider to the nearest discrete axis value.
            j = int(round(val))
            j = max(0, min(j, len(values) - 1))
            if j != scalar_idx[i]:
                scalar_idx[i] = j
                render()
        return handler

    for i, axis in enumerate(sweep.axes):
        n = len(axis['values'])
        p.add_slider_widget(
            make_on_beta(i), rng=[0, n - 1], value=scalar_idx[i],
            title=axis['name'], fmt='%.0f',
            pointa=(0.72, 0.90 - i * 0.12), pointb=(0.98, 0.90 - i * 0.12))

render()
p.show()
