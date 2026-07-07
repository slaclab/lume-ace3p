# Plotting tools

Several Python plotting tools are bundled with `lume-ace3p` for visualizing
parameter-sweep and optimization output. They live in the `plotting/`
directory and are run as standalone scripts (they prompt for input files via
a file dialog).

## S3P parameter-sweep viewer

`plotting/s3p_sweep_plot.py` reads a `sweep_output_file` produced by an S3P
workflow and plots the results in an interactive plot. To use it:

```bash
python plotting/s3p_sweep_plot.py
```

When prompted, load the appropriate S3P `sweep_output_file`. Try
`plotting/s3p_demo_sweep_output.txt` for an interactive demo.

The input file must come from a complete S3P parameter sweep — every
parameter combination must have the same frequencies scanned. Output from
incomplete S3P parameter sweeps is not supported by this tool.

The script then prompts for up to two parameters to add sliders for. Enter
the column numbers in the `sweep_output_file` ranging from 1 to the number
of different parameters listed in the input dict. In the 90-degree bend
example there are only two swept parameters (`cornercut` and `rcorner2`),
so the default `1, 2` is appropriate. With more than two swept parameters,
only two have individual sliders, but all parameter combinations are shown
and can be examined via the sweep-parameter tuple slider.

## Geant4 dose / energy-deposit viewer

`plotting/geant4_deposit_plot.py` reads a Geant4 dose or energy-deposit file
produced by the `geant4_track3p_beta` example (`doseDeposit.txt` or
`energyDeposit.txt`) and shows the 3D scoring mesh as interactive 2D slices.
To use it:

```bash
python plotting/geant4_deposit_plot.py [doseDeposit.txt]
```

If no file is given on the command line, a file dialog is opened. Either the
dose file (values in Gy) or the energy-deposit file (values in eV) can be
loaded — the mesh name, scorer name, and units are read automatically from the
comment header, so no configuration is needed.

The files are comma-separated voxel grids with columns
`iX, iY, iZ, total(value), total(val^2), entry` over the scoring mesh defined
in the Geant4 input file (`mesh_nx` x `mesh_ny` x `mesh_nz`). The viewer offers:

- a **slice-normal** selector (X, Y, or Z) that chooses which axis the slider
  steps through, showing the perpendicular plane,
- a **slice-index** slider to step through slices along that axis, and
- a **color-by** selector to switch between the deposited value and the raw
  scoring-entry count.

The deposited value uses a logarithmic color scale with zero voxels masked,
since deposits typically span many orders of magnitude and grow steeply with
`beta` (at small `beta` few particles are emitted, so most voxels are empty).
The entry count uses a linear scale.

Slices are drawn in accelerator coordinates: the beam axis Z (the long mesh
direction) is horizontal whenever it lies in the slice plane, and the
transverse (Z-normal) slice puts X horizontal and Y vertical.

### 3D voxel views

Two companion tools show the whole mesh in 3D instead of one slice at a time.
Both read the same dose / energy-deposit files and use the same accelerator
convention (beam axis Z drawn as the long horizontal dimension).

`plotting/geant4_deposit_plot3d.py` is a dependency-free 3D scatter of the
nonzero voxels, colored on a log scale by the deposited value (or linearly by
entry count):

```bash
python plotting/geant4_deposit_plot3d.py [doseDeposit.txt]
```

It provides a **color-by** selector (value or entries), a **threshold** slider
that hides voxels below a chosen percentile of the nonzero values (so the beam
channel stands out in dense, high-`beta` runs), and a **marker-size** slider.

`plotting/geant4_deposit_volume.py` is an optional smooth volumetric render
whose opacity scales with the (log-compressed) deposited value. It gives the
best result for dense runs but requires PyVista and an interactive /
GPU-capable session:

```bash
pip install pyvista
python plotting/geant4_deposit_volume.py [doseDeposit.txt]
```

If PyVista is not installed, the script prints an install hint and exits; use
the 3D scatter or 2D slice viewers instead.

## S3P optimization viewers

Three plotting tools are included for visualizing optimization output:

### `plotting/xopt_param_sweep_plot.py`

Visualizes the optimization algorithm's choice of points.

- Requires that a parameter sweep has been run.
- Prompts the user first for a file containing all of the parameter-sweep
  data, then for a file containing the optimization data.
- Can produce:
  - a 3D plot showing the optimized parameter as a function of the input
    parameters,
  - a 2D color map of the optimized parameter as a function of input
    parameters with the optimizer's choice of points overlaid, or
  - an animated version of the previous plot showing the algorithm's
    progress over time.

### `plotting/s3p_xopt_plot.py`

Visualizes S-parameters as a function of frequency, with sliders for
iteration number and S-parameter.

- Prompts the user for both optimization-run output files.
- The plot dynamically changes based on the S-parameter and iteration
  slider values.
- Frequencies that were optimized over are highlighted.

### `plotting/xopt_plot_still.py`

A static version of `s3p_xopt_plot.py` (no slider). The user can configure
how many and which iterations to show.
