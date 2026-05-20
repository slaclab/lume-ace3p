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
