import sys
import numpy as np

# Optional volumetric (opacity) viewer for Geant4 dose / energy-deposit output
# produced by the geant4_track3p_beta example (doseDeposit.txt /
# energyDeposit.txt). It renders the scoring mesh as a smooth 3D volume whose
# opacity scales with the deposited value, which reads well for dense (high
# beta) runs. The value is log-compressed first, since deposits span many
# orders of magnitude.
#
# This tool needs PyVista (and VTK) and an interactive / GPU-capable session:
#     pip install pyvista
# For a dependency-free view use plotting/geant4_deposit_plot3d.py (3D scatter)
# or plotting/geant4_deposit_plot.py (2D slices).
#
# Voxels are placed in accelerator coordinates: the beam axis Z (the long mesh
# direction) is the first spatial dimension, with transverse X and Y after it.
#
# Usage:  python plotting/geant4_deposit_volume.py [doseDeposit.txt]

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
value = raw[:, 3]

nx = ix.max() + 1
ny = iy.max() + 1
nz = iz.max() + 1

grid = np.zeros((nx, ny, nz))
grid[ix, iy, iz] = value

vlabel = scorer_name + (' [' + units + ']' if units else '')
print('Loaded %s: scorer "%s", grid %d x %d x %d, %d nonzero voxels'
      % (mesh_name, scorer_name, nx, ny, nz, int(np.count_nonzero(grid))))

# --- Log-compress the value so opacity/color span the full dynamic range ---
# Map nonzero values to log10; leave zero voxels at the floor (fully clear).
nonzero = grid[grid > 0]
if nonzero.size == 0:
    print('All voxels are zero -- nothing to render.')
    sys.exit()

floor = np.log10(nonzero.min())
logval = np.full(grid.shape, floor)
logval[grid > 0] = np.log10(grid[grid > 0])

# Accelerator convention: beam axis Z first, then transverse X, Y.
logval = np.transpose(logval, (2, 0, 1))   # (nz, nx, ny)

# Build a uniform grid (ImageData). PyVista expects point/cell scalars in
# Fortran order for the (nz, nx, ny) dimensions.
igrid = pv.ImageData(dimensions=np.array(logval.shape) + 1)
igrid.cell_data[vlabel] = logval.flatten(order='F')

# Opacity ramps from transparent (low) to opaque (high) across the log range.
opacity = [0.0, 0.02, 0.08, 0.2, 0.45, 0.8]

p = pv.Plotter()
p.add_volume(igrid, scalars=vlabel, cmap='inferno', opacity=opacity,
             scalar_bar_args={'title': 'log10 ' + vlabel})
p.add_axes(xlabel='Z (beam)', ylabel='X', zlabel='Y')
p.show_grid(xtitle='iZ (beam axis)', ytitle='iX', ztitle='iY')
p.add_text('%s  |  log10 %s' % (mesh_name, vlabel), font_size=10)
p.show()
