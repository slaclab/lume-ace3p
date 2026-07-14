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
# The Geant4 solid geometry may be OVERLAID as a translucent shell so the dose
# can be seen in the context of the real cavity structure. Pass the Geant4 input
# file (which names the solid_stl and holds the scoring-mesh geometry) or a solid
# .stl directly as a second argument; in dialog mode a second, optional file
# picker offers it. When an overlay is shown the deposit volume is placed in
# physical mm (origin/spacing from the scoring-mesh geometry) so the two align;
# the axis convention (Z beam, then X, Y) is unchanged. Only the solid is drawn:
# the cavity STL is just the upstream Track3P vacuum region.
#
# Usage:  python plotting/geant4_deposit_volume.py \
#             [doseDeposit.txt | sweep.yaml] [input.geant4 | solid.stl]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geant4_deposit_common import (parse_deposit_file, is_yaml_file,
                                   load_sweep, load_sweep_deposit,
                                   log_igrid, physical_log_igrid,
                                   read_mesh_geometry, scan_sweep_logrange,
                                   BACKGROUNDS,
                                   build_volume_cmap, contrast_color)

try:
    import pyvista as pv
except ImportError:
    print('This tool requires PyVista.  Install it with:  pip install pyvista')
    print('For a dependency-free 3D view use geant4_deposit_plot3d.py instead.')
    sys.exit(1)

# overlay_path: optional Geant4 input file (names the solid_stl + geometry) or a
# solid .stl passed directly. Empty -> no geometry overlay.
overlay_path = ''
if len(sys.argv) >= 2:
    file_path = sys.argv[1]
    if len(sys.argv) >= 3:
        overlay_path = sys.argv[2]
else:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title='Choose a Geant4 deposit file or LUME-ACE3P sweep YAML')
    if file_path:
        # Second, optional dialog for the geometry; Cancel (empty) just skips it.
        overlay_path = filedialog.askopenfilename(
            title='Optional: choose a Geant4 input or solid STL (Cancel to skip)')

if len(file_path) == 0:
    sys.exit()

# --- Load: either a single deposit file or a sweep YAML -------------------
sweep = None
scalar_idx = []
# color_mode: 'auto' (per-frame rescale, default) or 'global' (locked clim).
# clim: cached (logmin, logmax) for global mode, or None.
# scheme: fixed to 'jet'. background: selectable render background; the cmap's
# lowest color is anchored to it so faint cells blend in seamlessly.
# geom: physical scoring-mesh geometry (mm) for placing the volume in the STL's
# frame, or None (legacy voxel-index rendering). Set only when an overlay loads.
state = {'source_file': 'dose', 'color_mode': 'auto', 'clim': None,
         'scheme': 'jet', 'background': BACKGROUNDS[0], 'geom': None}

if is_yaml_file(file_path):
    sweep = load_sweep(file_path)
    scalar_idx = [0] * len(sweep.axes)
    state['source_file'] = 'dose' if sweep.dose_name else 'edep'


def _resolve_geant4_input():
    """Locate the Geant4 input file for the scoring-mesh geometry.

    Prefers an explicitly-supplied overlay arg that is itself an input file (not
    an .stl). Otherwise: sweep mode -> the path resolved by load_sweep;
    single-file mode -> auto-discover an input_*.geant4 next to the deposit file.
    Returns a path or None."""
    if overlay_path and not overlay_path.lower().endswith('.stl'):
        if os.path.isfile(overlay_path):
            return overlay_path
    if sweep is not None:
        return sweep.geant4_input
    import glob
    folder = os.path.dirname(os.path.abspath(file_path))
    for pattern in ('input_*.geant4', '*.geant4'):
        matches = sorted(glob.glob(os.path.join(folder, pattern)))
        if matches:
            return matches[0]
    return None


def make_igrid(parsed):
    """Build a PyVista ImageData (log-compressed opacity field) from a parsed
    deposit dict, or None if there are no nonzero voxels. Thin wrapper around the
    shared log_igrid / physical_log_igrid helpers; returns (igrid, vlabel,
    mesh_name) and logs a load summary, keeping render() unchanged. Uses physical
    mm placement when an overlay set state['geom']; otherwise voxel-index."""
    if state['geom'] is not None:
        igrid, vlabel, mesh_name, _lo, _hi = physical_log_igrid(parsed,
                                                                state['geom'])
    else:
        igrid, vlabel, mesh_name, _lo, _hi = log_igrid(parsed)
    if igrid is not None:
        print('Loaded %s: scorer "%s", grid %d x %d x %d, %d nonzero voxels'
              % (mesh_name, parsed['scorer_name'], parsed['nx'],
                 parsed['ny'], parsed['nz'],
                 int(np.count_nonzero(parsed['grid']))))
    return igrid, vlabel, mesh_name


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
_actor = {'volume': None, 'text': None, 'stl': None}

# --- Optional Geant4 solid-geometry overlay -------------------------------
# Load the solid STL once (it is static across sweep points), place the deposit
# volume in the same physical mm frame (origin/spacing from the scoring-mesh
# geometry), and add the solid as a translucent shell. Skipped cleanly if no
# overlay was requested, or warned if the geometry/STL cannot be resolved.
if overlay_path:
    geant4_input = _resolve_geant4_input()
    state['geom'] = read_mesh_geometry(geant4_input)
    if state['geom'] is None:
        print('Warning: cannot align an overlay -- the scoring-mesh geometry '
              '(a Geant4 input file with mesh_x/y/z, mesh_nx/ny/nz, ...) was '
              'not found. Rendering the dose volume without the geometry.')
    else:
        # An input file was passed as the overlay arg; use it for the STL too.
        stl_source = overlay_path
        if not overlay_path.lower().endswith('.stl') and geant4_input:
            stl_source = geant4_input
        try:
            from geant4_stl_overlay import load_solid_overlay
            solid = load_solid_overlay(stl_source)
            if solid is None:
                print('Warning: no solid_stl found for the overlay; rendering '
                      'the dose volume without the geometry.')
            else:
                # A translucent steel-blue shell reads clearly over the
                # jet-colored dose volume on both white and black backgrounds.
                # NOTE: do NOT enable depth peeling here -- with the volume
                # renderer it makes the translucent surface composite away to
                # near-invisibility (the opposite of what it does for
                # surface-only scenes).
                _actor['stl'] = p.add_mesh(solid, color='steelblue',
                                           opacity=0.3, show_edges=False,
                                           smooth_shading=True)
                print('Overlaid solid geometry, bbox mm: %s'
                      % np.round(solid.bounds, 1).tolist())
        except (ImportError, ValueError, OSError) as exc:
            print('Warning: could not load geometry overlay: ' + str(exc))


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

    fg = contrast_color(state['background'])
    if igrid is None:
        _actor['text'] = p.add_text('%s  |  no nonzero voxels%s'
                                    % (mesh_name, pt), font_size=10, color=fg)
    else:
        # In global mode, lock the color range to the cached scan; otherwise let
        # add_volume auto-scale to this frame (original per-frame behavior).
        vol_kw = {}
        if state['color_mode'] == 'global' and state['clim'] is not None:
            vol_kw['clim'] = state['clim']
        # Colormap anchored to the current background so faint cells fade into
        # it with no seam; scalar-bar text uses the contrasting color.
        cmap = build_volume_cmap(state['scheme'], state['background'])
        _actor['volume'] = p.add_volume(
            igrid, scalars=vlabel, cmap=cmap, opacity=OPACITY,
            scalar_bar_args={'title': 'log10 ' + vlabel, 'color': fg},
            **vol_kw)
        _actor['text'] = p.add_text('%s  |  log10 %s%s'
                                    % (mesh_name, vlabel, pt), font_size=10,
                                    color=fg)
    p.render()


def draw_frame_decor():
    """(Re)draw the orientation axes and bounds grid in a color that contrasts
    with the current background. Safe to call repeatedly (used on startup and
    after a background switch)."""
    fg = contrast_color(state['background'])
    p.remove_bounds_axes()
    p.add_axes(xlabel='Z (beam)', ylabel='X', zlabel='Y', color=fg)
    p.show_grid(xtitle='iZ (beam axis)', ytitle='iX', ztitle='iY', color=fg)


p.background_color = state['background']
draw_frame_decor()


# Static widget captions are added once, so track them and recolor on a
# background switch (they don't go through render()/draw_frame_decor()).
_widget_labels = []


def add_label(text, position):
    """add_text a static widget caption in the current contrast color, and
    remember it so a later background switch can recolor it."""
    actor = p.add_text(text, position=position, font_size=8,
                       color=contrast_color(state['background']))
    _widget_labels.append(actor)
    return actor


def recolor_widget_labels():
    fg = contrast_color(state['background'])
    for actor in _widget_labels:
        actor.GetTextProperty().SetColor(pv.Color(fg).float_rgb)


# --- Camera snap buttons (all modes) --------------------------------------
# Snap the camera to the standard accelerator planes or reset to isometric.
# The axes map is Z (beam) -> VTK x, X -> VTK y, Y -> VTK z (see add_axes), so
# view_xy shows the Z-X plane, view_xz the Z-Y plane, and view_yz the X-Y plane.
# PyVista has no momentary "push button", so these are checkbox buttons whose
# state is snapped back to off inside the callback: each click just fires the
# camera action and leaves the button visually unpressed (a plain button).
_camera_widgets = {}


def _snap(name, view_fn):
    def handler(_flag):
        view_fn()
        w = _camera_widgets.get(name)
        if w is not None:
            w.GetRepresentation().SetState(0)   # momentary: never stays "on"
        p.render()
    return handler


_CAMERA_BUTTONS = [
    ('Z-X plane', p.view_xy),
    ('Z-Y plane', p.view_xz),
    ('X-Y plane', p.view_yz),
    ('reset (iso)', lambda: (p.view_isometric(), p.reset_camera())),
]
for _bi, (_label, _fn) in enumerate(_CAMERA_BUTTONS):
    _y = 220 + _bi * 40
    _camera_widgets[_label] = p.add_checkbox_button_widget(
        _snap(_label, _fn), value=False, position=(10, _y), size=25)
    add_label(_label, position=(45, _y + 3))


# --- Geometry overlay visibility toggle (only when a solid loaded) --------
# Flip the static STL actor's visibility rather than removing/re-adding it, so
# toggling is instant and never triggers a rebuild.
if _actor['stl'] is not None:
    def on_geometry_toggle(flag):
        _actor['stl'].visibility = flag
        p.render()

    _y = 220 + len(_CAMERA_BUTTONS) * 40
    p.add_checkbox_button_widget(on_geometry_toggle, value=True,
                                 position=(10, _y), size=25)
    add_label('show geometry', position=(45, _y + 3))


# --- Background selector (all modes) --------------------------------------
# The colormap is fixed to 'jet'; its lowest color is anchored to the background
# (see build_volume_cmap) so faint cells fade seamlessly into it for either bg.
def make_on_background(name):
    def handler():                     # radio callback: no args
        state['background'] = name
        p.background_color = name
        draw_frame_decor()             # recolor axes/grid for the new bg
        recolor_widget_labels()        # recolor static captions for the new bg
        render()
    return handler


for _gi, _name in enumerate(BACKGROUNDS):
    _y = 400 + _gi * 40
    p.add_radio_button_widget(
        make_on_background(_name), 'background',
        value=(state['background'] == _name), position=(10, _y), size=25)
    add_label('bg: ' + _name, position=(45, _y + 3))


# Sweep-mode interactivity: dose/energy toggle, color-scale mode, beta sliders.
if sweep is not None:
    has_dose = bool(sweep.dose_name)
    has_edep = bool(sweep.edep_name)

    def _rescan_clim():
        """Recompute and cache the global log range for the current source."""
        print('Scanning sweep for global color range (%s)...'
              % state['source_file'])
        state['clim'] = scan_sweep_logrange(sweep, [state['source_file']])
        if state['clim'] is None:
            print('  no nonzero voxels found; staying on per-frame scale.')

    def on_source(flag):
        # Checkbox on -> energy, off -> dose (only wired when both exist).
        state['source_file'] = 'edep' if flag else 'dose'
        # The locked range is source-specific, so rescan when it changes.
        if state['color_mode'] == 'global':
            _rescan_clim()
        render()

    if has_dose and has_edep:
        p.add_checkbox_button_widget(
            on_source, value=(state['source_file'] == 'edep'),
            position=(10, 10), size=30)
        add_label('energy (on) / dose (off)', position=(50, 15))

    # Color-scale mode as a radio group: exactly one of per-frame / global is
    # highlighted at a time (add_radio_button_widget enforces the "only one on"
    # behavior within a named group).
    def make_on_color_mode(mode):
        # add_radio_button_widget invokes its callback with NO arguments (unlike
        # the checkbox widget, which passes the bool state).
        def handler():
            state['color_mode'] = mode
            if mode == 'global':
                _rescan_clim()
            render()
        return handler

    p.add_radio_button_widget(
        make_on_color_mode('auto'), 'color_scale',
        value=(state['color_mode'] == 'auto'), position=(10, 60), size=25)
    add_label('per-frame color scale', position=(45, 63))
    p.add_radio_button_widget(
        make_on_color_mode('global'), 'color_scale',
        value=(state['color_mode'] == 'global'), position=(10, 100), size=25)
    add_label('global (fixed) color scale', position=(45, 103))

    def make_on_beta(i, labels):
        def handler(label):
            # Text slider hands back the chosen label; map it to its index.
            j = labels.index(label)
            if j != scalar_idx[i]:
                scalar_idx[i] = j
                render()
        return handler

    for i, axis in enumerate(sweep.axes):
        labels = [('%g' % v if isinstance(v, float) else str(v))
                  for v in axis['values']]
        y = 0.88 - i * 0.14
        # 'modern' is a thin slider track (vs. the bulky default); the selected
        # beta value shows as the slider's own title. The axis-name caption sits
        # well above the track so the slider handle never covers it.
        p.add_text_slider_widget(
            make_on_beta(i, labels), data=labels, value=scalar_idx[i],
            pointa=(0.75, y), pointb=(0.97, y), style='modern')
        _beta_lbl = p.add_text(axis['name'] + ' =', position=(0.60, y - 0.01),
                               viewport=True, font_size=10,
                               color=contrast_color(state['background']))
        _widget_labels.append(_beta_lbl)

render()
p.show()
