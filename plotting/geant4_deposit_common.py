"""Shared helpers for the Geant4 dose / energy-deposit viewers.

The three viewers (geant4_deposit_plot.py, geant4_deposit_plot3d.py,
geant4_deposit_volume.py) all accept either:

  * a single Geant4 deposit file (doseDeposit.txt / energyDeposit.txt), or
  * a LUME-ACE3P YAML describing a Geant4 parameter sweep
    (e.g. examples/geant4_track3p_beta/geant4_track3p_beta.yaml).

For the deposit-file case, `parse_deposit_file` returns the parsed voxel grids
(this is the block that used to be duplicated in each viewer). For the YAML
case, `load_sweep` discovers the per-value sweep folders and the dose/energy
filenames so the viewer can offer a slider per swept variable plus a
dose/energy toggle; `load_sweep_deposit` loads the deposit file for a chosen
point in the sweep.
"""

import os
import itertools
import numpy as np


def is_yaml_file(path):
    """True if `path` looks like a YAML file (by extension)."""
    return os.path.splitext(path)[1].lower() in ('.yaml', '.yml')


def parse_deposit_file(path):
    """Parse a Geant4 dose / energy-deposit file.

    The files are comma-separated voxel grids with the columns
        iX, iY, iZ, total(value), total(val^2), entry
    over the scoring mesh. The mesh name, scorer name, and value units are read
    from the comment lines at the top of the file.

    Returns a dict with keys:
        mesh_name, scorer_name, units, vlabel,
        grid, entry_grid, nx, ny, nz
    Raises ValueError if the file contains no data rows.
    """
    with open(path, 'r') as file:
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
        raise ValueError('No data rows found in file: ' + path)

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
    return {
        'mesh_name': mesh_name,
        'scorer_name': scorer_name,
        'units': units,
        'vlabel': vlabel,
        'grid': grid,
        'entry_grid': entry_grid,
        'nx': int(nx),
        'ny': int(ny),
        'nz': int(nz),
    }


def _read_key_value_file(path):
    """Minimal reader for a Geant4 'key = value' input file. Returns a dict of
    the first value seen for each key. Kept local (rather than importing the
    Geant4 class) so the viewers don't need the ACE3P/Geant4 app environment."""
    values = {}
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                continue
            key, val = stripped.split('=', 1)
            key = key.strip()
            if key and key not in values:
                values[key] = val.strip()
    return values


class SweepInfo:
    """Describes a Geant4 parameter sweep loaded from a LUME-ACE3P YAML.

    Attributes:
        axes:      list of {'name': str, 'values': np.ndarray} (one per slider)
        base_dir:  the sweep base workdir (absolute, resolved from the YAML dir)
        dose_name: dose deposit filename inside each folder (or None)
        edep_name: energy deposit filename inside each folder (or None)
        geant4_input: absolute path to the Geant4 input file (or None) — source
                   of the scoring-mesh geometry and STL geometry for an overlay.
    """

    def __init__(self, axes, base_dir, dose_name, edep_name, scalar_str,
                 geant4_input=None):
        self.axes = axes
        self.base_dir = base_dir
        self.dose_name = dose_name
        self.edep_name = edep_name
        self._scalar_str = scalar_str
        self.geant4_input = geant4_input

    def folder_for(self, scalar_tuple):
        """Return the workdir path for a tuple of swept-axis scalar values,
        matching the naming built by workflow._getworkdir."""
        suffix = ''.join('_' + self._scalar_str(v) for v in scalar_tuple)
        return self.base_dir + suffix

    def filename_for(self, source):
        """Return the deposit filename for source 'dose' or 'edep'."""
        return self.dose_name if source == 'dose' else self.edep_name


def load_sweep(yaml_path):
    """Load a LUME-ACE3P YAML describing a Geant4 sweep and return a SweepInfo.

    Reuses lume_ace3p's own YAML/inputs/workflow helpers so the folder-naming
    convention stays in exactly one place. Raises ImportError with a clear
    message if the package is not importable, and ValueError if the YAML is not
    a Geant4 sweep.
    """
    try:
        from lume_ace3p.inputs import load_yaml, build_inputs
        from lume_ace3p.workflow_graph import _scalar_str
    except ImportError as exc:
        raise ImportError(
            'Loading a LUME-ACE3P sweep YAML requires the lume_ace3p package '
            'to be importable (pip install -e .). Original error: ' + str(exc))

    data = load_yaml(yaml_path)
    workflow_dict = data.get('workflow_parameters') or {}
    # New declarative schema: the geant4 module config lives in the ordered
    # 'workflow:' list rather than in workflow_parameters. Pull the geant4
    # entry's keys out so the folder-naming + output-file resolution below works
    # for a migrated YAML (falls back to workflow_parameters for older configs).
    geant4_entry = {}
    for entry in (data.get('workflow') or []):
        if isinstance(entry, dict) and str(entry.get('module')).lower() == 'geant4':
            geant4_entry = entry
            break

    inputs = build_inputs(data)
    sweep_axes = inputs.sweep_axes()
    if not sweep_axes:
        raise ValueError(
            'No swept variables found in ' + yaml_path +
            ' (nothing to slide over).')
    axes = [{'name': label, 'values': np.asarray(values)}
            for (label, values, _setter) in sweep_axes]

    # Base workdir, resolved relative to the YAML file's directory.
    yaml_dir = os.path.dirname(os.path.abspath(yaml_path))
    base_workdir = workflow_dict.get('workdir', 'lume-ace3p_workflow_output')
    if not os.path.isabs(base_workdir):
        base_workdir = os.path.join(yaml_dir, base_workdir)

    # Dose / energy filenames: prefer explicit overrides (on the geant4 module,
    # or legacy workflow_parameters), otherwise read output_dose / output_edep
    # from the Geant4 input file.
    dose_name = (geant4_entry.get('geant4_dose_output')
                 or geant4_entry.get('geant4_scoring_output')
                 or workflow_dict.get('geant4_dose_output')
                 or workflow_dict.get('geant4_scoring_output'))
    edep_name = (geant4_entry.get('geant4_edep_output')
                 or workflow_dict.get('geant4_edep_output'))
    geant4_input = (geant4_entry.get('geant4_input')
                    or workflow_dict.get('geant4_input'))
    input_path = None
    if geant4_input is not None:
        input_path = geant4_input
        if not os.path.isabs(input_path):
            input_path = os.path.join(yaml_dir, input_path)
        if (dose_name is None or edep_name is None) and os.path.isfile(input_path):
            kv = _read_key_value_file(input_path)
            if dose_name is None:
                dose_name = kv.get('output_dose')
            if edep_name is None:
                edep_name = kv.get('output_edep')

    return SweepInfo(axes, base_workdir, dose_name, edep_name, _scalar_str,
                     geant4_input=input_path)


def load_sweep_deposit(sweep, scalar_tuple, source):
    """Parse the dose/edep deposit file for one point in the sweep.

    `source` is 'dose' or 'edep'. Returns the parse_deposit_file dict, or None
    (after printing a warning) if the folder or file is missing / empty so the
    caller can render an empty frame instead of crashing.
    """
    filename = sweep.filename_for(source)
    if not filename:
        print('Warning: no %s output filename is defined for this sweep.'
              % source)
        return None
    folder = sweep.folder_for(scalar_tuple)
    path = os.path.join(folder, filename)
    if not os.path.isfile(path):
        print('Warning: deposit file not found: ' + path)
        return None
    try:
        return parse_deposit_file(path)
    except ValueError as exc:
        print('Warning: ' + str(exc))
        return None


def _log_field(parsed, floor=None):
    """Log10-compress a parsed deposit grid into the viewer's voxel-axis order.

    Shared by `log_igrid` and `physical_log_igrid` so the log transform and the
    accelerator axis convention live in exactly one place. Returns a 5-tuple:

        (logval_t, vlabel, mesh_name, logmin, logmax)

    where `logval_t` is the log10 field transposed to (nz, nx, ny) — beam axis Z
    first, then transverse X, Y — with zero voxels held at the log floor, and
    (logmin, logmax) are the log10 range over the nonzero voxels. If `parsed` is
    None or has no nonzero voxels, `logval_t` is None.

    `floor` fixes the value assigned to empty (zero) voxels. By default it is the
    per-frame log minimum, which is correct for a single autoscaled view. When a
    caller locks the color range across frames (global scaling), it must pass the
    GLOBAL log minimum here: otherwise a frame whose own minimum sits above the
    global floor would map its empty voxels to a nonzero position in the fixed
    range, so the opacity ramp no longer clears them and the whole box fills with
    a per-frame haze — the choppy, "the scale changed" artifact global scaling is
    meant to avoid.
    """
    if parsed is None:
        return None, 'value', '(missing)', None, None
    grid = parsed['grid']
    vlabel = parsed['vlabel']
    nonzero = grid[grid > 0]
    if nonzero.size == 0:
        return None, vlabel, parsed['mesh_name'], None, None

    # Map nonzero values to log10; leave zero voxels at the floor (fully clear).
    logmin = float(np.log10(nonzero.min()))
    logmax = float(np.log10(nonzero.max()))
    fill = logmin if floor is None else float(floor)
    logval = np.full(grid.shape, fill)
    logval[grid > 0] = np.log10(grid[grid > 0])

    # Accelerator convention: beam axis Z first, then transverse X, Y.
    logval = np.transpose(logval, (2, 0, 1))   # (nz, nx, ny)
    return logval, vlabel, parsed['mesh_name'], logmin, logmax


def log_igrid(parsed, floor=None):
    """Build a PyVista ImageData holding the log10-compressed deposit field.

    Shared by the volumetric viewer and the animation tool so the log transform
    and voxel-axis convention live in one place. `parsed` is a parse_deposit_file
    dict (or None). Returns a 5-tuple:

        (igrid, vlabel, mesh_name, logmin, logmax)

    where igrid is a pv.ImageData with cell scalars `vlabel` (log10 of the
    deposit, zero voxels held at the floor), and (logmin, logmax) are the log10
    range over the nonzero voxels. If there are no nonzero voxels, igrid is None
    and logmin/logmax are None. The grid sits in voxel-index space (unit spacing,
    origin at 0); see `physical_log_igrid` for a version placed in physical mm.

    `floor` sets the value used for empty voxels; pass the global log minimum
    when locking the color range across frames (see `_log_field`).

    PyVista is imported lazily so the dependency-free 2D/3D-scatter viewers can
    keep importing this module without PyVista installed.
    """
    import pyvista as pv

    logval, vlabel, mesh_name, logmin, logmax = _log_field(parsed, floor=floor)
    if logval is None:
        return None, vlabel, mesh_name, logmin, logmax

    # PyVista expects point/cell scalars in Fortran order for these dimensions.
    igrid = pv.ImageData(dimensions=np.array(logval.shape) + 1)
    igrid.cell_data[vlabel] = logval.flatten(order='F')
    return igrid, vlabel, mesh_name, logmin, logmax


def read_mesh_geometry(geant4_input_path):
    """Read the Geant4 scoring-mesh geometry from a Geant4 input file.

    Pulls the scoring-mesh keys (`mesh_cx/cy/cz` center, `mesh_x/y/z` half-sizes,
    `mesh_nx/ny/nz` bin counts — all in mm) via `_read_key_value_file` and returns
    a dict describing the box in the viewer's VTK-axis order (Z, X, Y):

        {'origin': (oz, ox, oy),          # low corner, mm, VTK order
         'spacing': (sz, sx, sy),         # per-voxel size, mm, VTK order
         'center': (cx, cy, cz),          # raw physical center, mm
         'half': (hx, hy, hz),            # raw physical half-sizes, mm
         'bins': (nx, ny, nz)}            # raw bin counts

    Origin/spacing follow the (nz, nx, ny) transpose used by `_log_field`, so a
    physical ImageData built with them lines up with the log field. Spacing is
    computed per-axis (never assumes cubic voxels). Returns None if the path is
    missing/unreadable or any required key is absent or non-numeric.
    """
    if not geant4_input_path or not os.path.isfile(geant4_input_path):
        return None
    kv = _read_key_value_file(geant4_input_path)
    keys = ('mesh_cx', 'mesh_cy', 'mesh_cz', 'mesh_x', 'mesh_y', 'mesh_z',
            'mesh_nx', 'mesh_ny', 'mesh_nz')
    if any(k not in kv for k in keys):
        return None
    try:
        cx, cy, cz = (float(kv['mesh_cx']), float(kv['mesh_cy']),
                      float(kv['mesh_cz']))
        hx, hy, hz = (float(kv['mesh_x']), float(kv['mesh_y']),
                      float(kv['mesh_z']))
        nx, ny, nz = (int(float(kv['mesh_nx'])), int(float(kv['mesh_ny'])),
                      int(float(kv['mesh_nz'])))
    except (ValueError, TypeError):
        return None
    if nx <= 0 or ny <= 0 or nz <= 0:
        return None

    # VTK order (Z, X, Y): spacing = full extent / bins per axis; origin = the
    # low corner (center - half), because ImageData cell i spans
    # [origin + i*spacing, origin + (i+1)*spacing].
    spacing = (2.0 * hz / nz, 2.0 * hx / nx, 2.0 * hy / ny)
    origin = (cz - hz, cx - hx, cy - hy)
    return {'origin': origin, 'spacing': spacing,
            'center': (cx, cy, cz), 'half': (hx, hy, hz),
            'bins': (nx, ny, nz)}


def physical_log_igrid(parsed, geom, floor=None):
    """Like `log_igrid`, but place the volume in physical mm space.

    Builds the ImageData with the `origin` and `spacing` (mm, VTK Z/X/Y order)
    from `read_mesh_geometry`, so the deposit volume co-locates with a physical
    STL overlay. Same 5-tuple contract as `log_igrid`. Falls back to unit spacing
    / zero origin if `geom` is None (equivalent to `log_igrid`).

    `floor` sets the value used for empty voxels; pass the global log minimum
    when locking the color range across frames (see `_log_field`).
    """
    import pyvista as pv

    logval, vlabel, mesh_name, logmin, logmax = _log_field(parsed, floor=floor)
    if logval is None:
        return None, vlabel, mesh_name, logmin, logmax

    igrid = pv.ImageData(dimensions=np.array(logval.shape) + 1)
    if geom is not None:
        igrid.origin = geom['origin']
        igrid.spacing = geom['spacing']
    igrid.cell_data[vlabel] = logval.flatten(order='F')
    return igrid, vlabel, mesh_name, logmin, logmax


def read_geant4_geometry(geant4_input_path):
    """Resolve the Geant4 geometry STL paths from a Geant4 input file.

    Reads `solid_stl` / `cavity_stl` (filenames, resolved against the input
    file's directory) and `scale_factor` (float, default 1.0) via
    `_read_key_value_file`. Returns a dict:

        {'solid_stl': abs path or None,
         'cavity_stl': abs path or None,
         'scale_factor': float}

    or None if the input file is missing/unreadable. The `solid_stl` entry is
    None when the key is absent or the referenced file does not exist (the viewer
    overlays only the solid; cavity is returned for completeness).
    """
    if not geant4_input_path or not os.path.isfile(geant4_input_path):
        return None
    kv = _read_key_value_file(geant4_input_path)
    base = os.path.dirname(os.path.abspath(geant4_input_path))

    def _resolve(name):
        if not name:
            return None
        path = name if os.path.isabs(name) else os.path.join(base, name)
        return path if os.path.isfile(path) else None

    try:
        scale = float(kv.get('scale_factor', 1.0))
    except (ValueError, TypeError):
        scale = 1.0
    return {'solid_stl': _resolve(kv.get('solid_stl')),
            'cavity_stl': _resolve(kv.get('cavity_stl')),
            'scale_factor': scale}


def scan_sweep_logrange(sweep, source_list):
    """Scan every point in a sweep and return the global log10 deposit range.

    Iterates all axis-index combinations, loads each deposit file for the given
    source(s), and tracks the smallest / largest nonzero deposit across all of
    them. Returns (logmin, logmax) in log10 units, or None if no frame has any
    nonzero voxels. `source_list` is a list of sources to include, e.g.
    ['dose'], ['edep'], or ['dose', 'edep'].
    """
    lo = None
    hi = None
    ranges = [range(len(axis['values'])) for axis in sweep.axes]
    for combo in itertools.product(*ranges):
        scalars = tuple(sweep.axes[i]['values'][combo[i]]
                        for i in range(len(sweep.axes)))
        for source in source_list:
            parsed = load_sweep_deposit(sweep, scalars, source)
            if parsed is None:
                continue
            grid = parsed['grid']
            nonzero = grid[grid > 0]
            if nonzero.size == 0:
                continue
            vmin = float(np.log10(nonzero.min()))
            vmax = float(np.log10(nonzero.max()))
            lo = vmin if lo is None else min(lo, vmin)
            hi = vmax if hi is None else max(hi, vmax)
    if lo is None:
        return None
    return lo, hi


# --- Volume colormaps -----------------------------------------------------
# The volumetric viewers ramp voxel opacity from transparent (low deposit) to
# opaque (high). For faint / no-data cells to blend into the render background
# instead of leaving a visible seam, the colormap's LOWEST color must equal the
# background. build_volume_cmap() anchors the low stop to the chosen background,
# so any (scheme, background) pair is seam-free by construction.
#
# Each scheme lists only the UPPER color stops (low -> high); the background is
# prepended as the lowest stop. Stops are picked saturated/mid so they stay
# visible on both light and dark backgrounds.
VOLUME_SCHEMES = {
    'hot':     ['#ffe08a', '#ffcc33', '#ffa100', '#ff6a00', '#ee3300',
                '#c00000', '#aa0000'],
    'cool':    ['#bfefff', '#7fdbff', '#4fb8ec', '#2f9fe0', '#1e77d0',
                '#1155cc', '#0a3a9e', '#04206a'],
    'viridis': ['#addc30', '#7ad151', '#4ac16d', '#22a884', '#2a788e',
                '#33638d', '#414487', '#472d7b'],
    'gray':    ['#d9d9d9', '#bdbdbd', '#9e9e9e', '#888888', '#666666',
                '#4d4d4d', '#2b2b2b', '#111111'],
    # Classic "jet" ramp (blue -> cyan -> green -> yellow -> red) spanning the
    # full hue range. Low deposits read blue, high reads red. Extra intermediate
    # stops keep the hue sweep (and the white->blue rise off the background)
    # smooth rather than showing kinks at a handful of primary colors.
    'jet':     ['#0000ff', '#0055ff', '#0088ff', '#00c4ff', '#00ffff',
                '#00ffaa', '#00ff55', '#00ff00', '#55ff00', '#aaff00',
                '#ffff00', '#ffcc00', '#ff8800', '#ff4400', '#ff0000'],
}

# Selectable render backgrounds (index 0 is the default).
BACKGROUNDS = ['white', 'black']

# Opacity transfer function for the volume renderers, mapped across the log
# color range (evenly spaced control points, low deposit -> high). Empty voxels
# sit at the floor (position 0, fully transparent) so background stays clear,
# but opacity ramps up to max in the 25%-62.5% range. The nine points
# land at 0, 0.125, 0.25, ... 1.0; the fifth (0.5) is the first at 1.0.
VOLUME_OPACITY = [0.0, 0.0, 0.1, 0.3, 0.7, 0.9, 1.0, 1.0, 1.0]

# Number of quantization levels in the volume colormap / scalar bar. A high
# count makes the gradient (and the color bar) read as a continuous ramp rather
# than a few discrete bands. Passed both to the colormap LUT (so the samples
# exist) and to add_volume's n_colors (so the mapper uses them).
VOLUME_N_COLORS = 1024


def build_volume_cmap(scheme, background, n_colors=VOLUME_N_COLORS):
    """Return a matplotlib LinearSegmentedColormap whose lowest color equals
    `background`, ramping up through the named scheme's stops.

    `scheme` is a key of VOLUME_SCHEMES; `background` is any matplotlib color.
    `n_colors` is the size of the color lookup table — larger means a smoother
    gradient (defaults to VOLUME_N_COLORS). Matplotlib is imported lazily so the
    dependency-free scatter viewers don't pay for it. Raises KeyError for an
    unknown scheme name.
    """
    from matplotlib.colors import LinearSegmentedColormap, to_rgb

    stops = [to_rgb(background)] + [to_rgb(c) for c in VOLUME_SCHEMES[scheme]]
    return LinearSegmentedColormap.from_list('volume_' + scheme, stops,
                                             N=n_colors)


def contrast_color(background):
    """Return 'black' or 'white' — whichever reads against `background`.

    Used to keep grid lines, axis labels, and scalar-bar text legible when the
    background is switched. Uses a simple perceptual-luminance test.
    """
    from matplotlib.colors import to_rgb

    r, g, b = to_rgb(background)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return 'black' if luminance > 0.5 else 'white'
