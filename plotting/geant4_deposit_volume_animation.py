import os
import sys
import argparse
import numpy as np

# Render a Geant4 dose / energy-deposit parameter sweep as an animated GIF.
#
# This is the off-screen, scriptable companion to geant4_deposit_volume.py: it
# reuses the same log-compressed volumetric rendering, but instead of an
# interactive window it walks a swept variable (e.g. beta) and writes one frame
# per value to a GIF. The camera is fixed for the whole movie (choose the plane
# with --view) so the frames are directly comparable, and --global-scale locks
# the color bar across all frames so brightness tracks the deposit and not the
# per-frame autoscale. The colormap (--cmap) is anchored to the render
# background (--background) so faint / no-data cells fade seamlessly into it
# instead of leaving a visible seam.
#
# Only a LUME-ACE3P sweep YAML makes sense here (a single deposit file has
# nothing to animate).
#
# Like the interactive viewer, the Geant4 solid geometry can be OVERLAID as a
# translucent shell: pass the Geant4 input file (which names solid_stl and holds
# the scoring-mesh geometry) or a solid .stl as a second argument. When shown,
# the deposit volume is placed in physical mm (origin/spacing from the scoring
# mesh) so the two align. If the YAML argument is omitted, a file dialog prompts
# for the YAML and then an optional geometry file.
#
# Requires PyVista (+ VTK) and imageio (PyVista's GIF backend):
#     pip install pyvista imageio
#
# Usage:
#   python plotting/geant4_deposit_volume_animation.py \
#       [sweep.yaml] [input.geant4 | solid.stl] \
#       [-o out.gif] [--source dose|edep] [--view xy|xz|yz|iso] \
#       [--fps N] [--global-scale] [--axis I] \
#       [--cmap hot|cool|viridis|gray] [--background white|black]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geant4_deposit_common import (is_yaml_file, load_sweep, load_sweep_deposit,
                                   log_igrid, physical_log_igrid,
                                   read_mesh_geometry, scan_sweep_logrange,
                                   VOLUME_SCHEMES, BACKGROUNDS,
                                   build_volume_cmap, contrast_color)

# Same opacity ramp as the interactive volume viewer.
OPACITY = [0.0, 0.02, 0.08, 0.2, 0.45, 0.8]

# Camera plane -> PyVista view method name. The axes map (see add_axes in the
# viewer) is Z (beam) -> VTK x, X -> VTK y, Y -> VTK z, so 'xy' shows the Z-X
# plane, 'xz' the Z-Y plane, and 'yz' the transverse X-Y plane.
VIEWS = {'xy': 'view_xy', 'xz': 'view_xz', 'yz': 'view_yz',
         'iso': 'view_isometric'}


def parse_args():
    ap = argparse.ArgumentParser(
        description='Render a Geant4 deposit sweep (e.g. beta) as a GIF using '
                    'the same volumetric view as geant4_deposit_volume.py.')
    ap.add_argument('yaml', nargs='?', default=None,
                    help='LUME-ACE3P sweep YAML (e.g. geant4_track3p_beta.yaml). '
                         'If omitted, a file dialog prompts for it.')
    ap.add_argument('geometry', nargs='?', default=None,
                    help='optional Geant4 input file or solid .stl to overlay '
                         'the solid geometry as a translucent shell')
    ap.add_argument('-o', '--output', default=None,
                    help='output GIF path (default: <yaml-stem>_<source>.gif)')
    ap.add_argument('--source', choices=['dose', 'edep'], default=None,
                    help='which deposit file to animate (default: dose if '
                         'available, else edep)')
    ap.add_argument('--view', choices=list(VIEWS), default='iso',
                    help='fixed camera plane for every frame (default: iso)')
    ap.add_argument('--fps', type=float, default=2.0,
                    help='frames per second (default: 2)')
    ap.add_argument('--global-scale', action='store_true',
                    help='lock the color bar to the global min/max across all '
                         'frames (recommended for a coherent movie); otherwise '
                         'each frame autoscales')
    ap.add_argument('--axis', type=int, default=0,
                    help='which swept axis to animate when the sweep has more '
                         'than one (0-based; default: 0). Other axes are held '
                         'at their first value.')
    ap.add_argument('--cmap', choices=list(VOLUME_SCHEMES), default='jet',
                    help='colormap scheme (default: jet). Its lowest color is '
                         'anchored to --background so faint cells fade in with '
                         'no seam.')
    ap.add_argument('--background', choices=BACKGROUNDS, default=BACKGROUNDS[0],
                    help='render background color (default: %s)'
                         % BACKGROUNDS[0])
    return ap.parse_args()


def main():
    args = parse_args()

    yaml_path = args.yaml
    geometry_path = args.geometry
    # No YAML on the command line -> prompt for it (and an optional geometry).
    if yaml_path is None:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        yaml_path = filedialog.askopenfilename(
            title='Choose a LUME-ACE3P sweep YAML')
        if not yaml_path:
            sys.exit()
        if geometry_path is None:
            # Second, optional dialog; Cancel (empty) simply skips the overlay.
            geometry_path = filedialog.askopenfilename(
                title='Optional: choose a Geant4 input or solid STL '
                      '(Cancel to skip)') or None

    if not is_yaml_file(yaml_path):
        sys.exit('Error: expected a sweep YAML; a single deposit file has '
                 'nothing to animate. Use geant4_deposit_volume.py to view one '
                 'file.')

    try:
        import pyvista as pv
    except ImportError:
        sys.exit('This tool requires PyVista.  Install it with:  '
                 'pip install pyvista')
    try:
        import imageio  # noqa: F401  (PyVista's open_gif backend)
    except ImportError:
        sys.exit("This tool needs imageio for GIF output.  Install it with:  "
                 "pip install imageio")

    sweep = load_sweep(yaml_path)

    # Resolve the solid-geometry overlay (optional). Mirrors the interactive
    # viewer: the deposit volume is placed in physical mm (origin/spacing from
    # the scoring-mesh geometry) so it aligns with the solid STL.
    geom = None
    solid = None
    if geometry_path:
        geant4_input = geometry_path
        if geometry_path.lower().endswith('.stl'):
            # A direct STL was given; take the scoring geometry from the sweep's
            # Geant4 input file instead.
            geant4_input = sweep.geant4_input
        geom = read_mesh_geometry(geant4_input)
        if geom is None:
            print('Warning: cannot align an overlay -- the scoring-mesh geometry '
                  'was not found. Rendering without the geometry.')
        else:
            # load_solid_overlay accepts either the input file (reads its
            # solid_stl) or a direct .stl path.
            try:
                from geant4_stl_overlay import load_solid_overlay
                solid = load_solid_overlay(geometry_path)
                if solid is None:
                    print('Warning: no solid_stl found for the overlay; '
                          'rendering without the geometry.')
                    geom = None
            except (ImportError, ValueError, OSError) as exc:
                print('Warning: could not load geometry overlay: ' + str(exc))
                geom = None

    # Resolve source: honor --source, else prefer dose, else edep.
    source = args.source
    if source is None:
        source = 'dose' if sweep.dose_name else 'edep'
    if not sweep.filename_for(source):
        sys.exit('Error: the sweep defines no %s output file.' % source)

    if not (0 <= args.axis < len(sweep.axes)):
        sys.exit('Error: --axis %d out of range (sweep has %d swept axes).'
                 % (args.axis, len(sweep.axes)))

    anim_axis = sweep.axes[args.axis]
    values = anim_axis['values']

    # Hold the non-animated axes at their first value.
    scalar_idx = [0] * len(sweep.axes)

    out = args.output
    if out is None:
        stem = os.path.splitext(os.path.basename(yaml_path))[0]
        out = '%s_%s.gif' % (stem, source)

    clim = None
    if args.global_scale:
        print('Scanning sweep for global color range (%s)...' % source)
        clim = scan_sweep_logrange(sweep, [source])
        if clim is None:
            print('  no nonzero voxels found; frames will autoscale.')

    # Colormap anchored to the background so faint cells fade in seamlessly;
    # decor + scalar-bar text use the contrasting color for legibility.
    cmap = build_volume_cmap(args.cmap, args.background)
    fg = contrast_color(args.background)

    p = pv.Plotter(off_screen=True)
    p.background_color = args.background
    p.add_axes(xlabel='Z (beam)', ylabel='X', zlabel='Y', color=fg)
    p.show_grid(xtitle='iZ (beam axis)', ytitle='iX', ztitle='iY', color=fg)

    # The solid overlay is static across frames; add it once. A translucent
    # steel-blue shell reads clearly over the jet-colored volume (matches the
    # interactive viewer; no depth peeling -- it hides the surface under the
    # volume renderer).
    if solid is not None:
        p.add_mesh(solid, color='steelblue', opacity=0.3, show_edges=False,
                   smooth_shading=True)
        print('Overlaid solid geometry, bbox mm: %s'
              % np.round(solid.bounds, 1).tolist())

    p.open_gif(out, fps=args.fps)
    n_written = 0
    for j, value in enumerate(values):
        scalar_idx[args.axis] = j
        scalars = tuple(sweep.axes[i]['values'][scalar_idx[i]]
                        for i in range(len(sweep.axes)))
        parsed = load_sweep_deposit(sweep, scalars, source)
        # Physical mm placement when overlaying geometry, else voxel-index.
        if geom is not None:
            igrid, vlabel, mesh_name, _lo, _hi = physical_log_igrid(parsed, geom)
        else:
            igrid, vlabel, mesh_name, _lo, _hi = log_igrid(parsed)

        vol_actor = None
        label = '%s = %g' % (anim_axis['name'], value)
        if igrid is None:
            txt_actor = p.add_text('%s  |  no nonzero voxels   [%s]'
                                   % (mesh_name, label), font_size=10,
                                   color=fg)
        else:
            vol_kw = {'clim': clim} if clim is not None else {}
            vol_actor = p.add_volume(
                igrid, scalars=vlabel, cmap=cmap, opacity=OPACITY,
                scalar_bar_args={'title': 'log10 ' + vlabel, 'color': fg},
                **vol_kw)
            txt_actor = p.add_text('%s  |  log10 %s   [%s]'
                                   % (mesh_name, vlabel, label), font_size=10,
                                   color=fg)

        # Snap the (fixed) camera the same way for every frame.
        getattr(p, VIEWS[args.view])()
        p.write_frame()
        n_written += 1

        # Clear this frame's actors so the next frame starts clean.
        if vol_actor is not None:
            p.remove_actor(vol_actor)
        p.remove_actor(txt_actor)

    p.close()
    print('Wrote %s (%d frames, %s, view=%s, cmap=%s, bg=%s%s)'
          % (out, n_written, source, args.view, args.cmap, args.background,
             ', global scale' if clim is not None else ''))


if __name__ == '__main__':
    main()
