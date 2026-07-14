"""Load the Geant4 solid geometry (an STL file) as a PyVista surface for
overlaying on the Geant4 deposit volume viewer.

Geant4 defines its geometry with STL files, referenced in the Geant4 input file
as `solid_stl` / `cavity_stl`. Only the *solid* is overlaid: the cavity is just
the vacuum region the particles occupied during the upstream Track3P step, not
useful dose context. STL coordinates are already in mm (the same frame as the
scoring mesh), so aligning to the deposit volume needs only the viewer's
axis permutation into VTK (Z, X, Y) order (and the input file's scale_factor if
it is not 1.0). PyVista reads STL natively, so there is no extra dependency.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geant4_deposit_common import read_geant4_geometry


def load_solid_overlay(source, scale=None):
    """Load the Geant4 solid geometry as a PyVista PolyData in the viewer frame.

    `source` is either a Geant4 input file (from which `solid_stl` and
    `scale_factor` are read) or a direct path to an `.stl` file. Returns a
    pv.PolyData in the viewer's VTK (Z, X, Y) mm coordinates, or None if no
    solid STL can be resolved.

    Coordinates are scaled by `scale` if given, else by the input file's
    `scale_factor` (default 1.0 for a direct STL), then the columns are permuted
    (X, Y, Z) -> (Z, X, Y) to match the deposit volume's axis convention (see
    `_log_field`'s (2, 0, 1) transpose in geant4_deposit_common).
    """
    import pyvista as pv

    if source and source.lower().endswith('.stl'):
        stl_path = source if os.path.isfile(source) else None
        if scale is None:
            scale = 1.0
    else:
        geom = read_geant4_geometry(source)
        if geom is None:
            return None
        stl_path = geom['solid_stl']
        if scale is None:
            scale = geom['scale_factor']
    if not stl_path:
        return None

    mesh = pv.read(stl_path)
    if scale != 1.0:
        mesh.points = mesh.points * scale
    # Physical (X, Y, Z) -> VTK (Z, X, Y), matching _log_field's (2, 0, 1)
    # transpose so the solid shares the deposit volume's axis convention.
    mesh.points = mesh.points[:, [2, 0, 1]]
    return mesh
