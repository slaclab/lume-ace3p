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
    """

    def __init__(self, axes, base_dir, dose_name, edep_name, scalar_str):
        self.axes = axes
        self.base_dir = base_dir
        self.dose_name = dose_name
        self.edep_name = edep_name
        self._scalar_str = scalar_str

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
    if (dose_name is None or edep_name is None) and geant4_input is not None:
        input_path = geant4_input
        if not os.path.isabs(input_path):
            input_path = os.path.join(yaml_dir, input_path)
        if os.path.isfile(input_path):
            kv = _read_key_value_file(input_path)
            if dose_name is None:
                dose_name = kv.get('output_dose')
            if edep_name is None:
                edep_name = kv.get('output_edep')

    return SweepInfo(axes, base_workdir, dose_name, edep_name, _scalar_str)


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
