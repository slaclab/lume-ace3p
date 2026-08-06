import os
import sys
import numpy as np
import tkinter as tk
from tkinter import filedialog
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm, Normalize
from matplotlib.widgets import Slider, RadioButtons, RangeSlider

# Interactive accuracy viewer for the PCA-GP dose surrogate (the model saved by
# the `train_surrogate` mode). It is launched on a *training-store directory* --
# the folder holding `training_table.txt`, `manifest.json`, and a `surrogate/`
# subfolder produced by the geant4_beta_surrogate example. It loads the store's
# (beta, dose) training pairs and the fitted surrogate, then lets you:
#
#   * slide the 8 (D) beta field-enhancement knobs and watch the surrogate's
#     predicted dose update live,
#   * step a sample selector through the stored training samples -- this snaps
#     the beta sliders onto a real DOE point and overlays that sample's true
#     Geant4 dose against the prediction, so you can see the fit quality directly.
#     Nudging any beta slider off the snapped value drops the truth overlay
#     (there is no Geant4 truth away from the training points), leaving the
#     prediction + its uncertainty band,
#   * toggle a log10 / linear vertical scale. Dose is exponential in beta
#     (Fowler-Nordheim) and spans ~9 orders of magnitude across voxels, so log is
#     the default and the honest scale; the relative-L2 readout is computed in the
#     displayed scale, which is why a good fit reads ~0.2 in log but a misleading
#     ~2 in linear.
#
# Two panels are shown: a 2D view of the dose grid (a slice normal to a chosen
# axis, or a sum-projection over it) and the 1D dose profile projected onto the
# beam axis Z (summing every voxel over x, y per z) -- the profile the accuracy
# is easiest to read on. Beam axis Z is drawn horizontal, matching the Geant4
# deposit viewers. All spatial axes are labelled in PHYSICAL mm, derived from the
# manifest's scoring-mesh center / half-sizes / bin counts.
#
# Both dose scales are LOCKED log ranges rather than autoscaled per frame, so
# stepping samples or sliding beta shows a genuine change in dose instead of a
# rescaled axis. Each panel has its own vertical range slider (two handles, in
# log10 decades): 'voxel dose' for the 2D color scale and 'profile dose' for the
# 1D y-axis. They are separate because the 1D panel sums each z-slice over x and
# y, landing ~7 decades above per-voxel dose -- one shared range would squash the
# profile against the top of its axis. Empty (zero) voxels are clamped to the
# bottom of the 2D range so they read as the lowest color: on a log scale zero is
# -inf, and pinning it to a very small number is the right call for visualization.
#
# Usage:  python plotting/surrogate_fit_plot.py [store_dir] [--clamp LO HI]
# If no directory is given, a directory dialog is opened. --clamp takes the log10
# decade bounds for the color scale, e.g. `--clamp -9 -1`.

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Jet colormap for the dose, matching the Geant4 deposit viewers. Zero voxels are
# clamped to the bottom of the locked log range rather than masked, so they take
# the lowest color; set_bad only catches genuine NaN/inf now.
VALUE_CMAP = mpl.colormaps['jet'].copy()
VALUE_CMAP.set_bad('white')


def _load_store_and_model(store_dir):
    """Load the training store + saved surrogate for `store_dir`.

    The store's field-artifact handles are recorded relative to the store's
    PARENT directory (they include the store folder name), so we resolve them by
    working from that parent -- the same convention `load_training_store` +
    `results.load_field` use. Returns (training_store, surrogate, store_name).
    Raises ImportError with an install hint if lume_ace3p is not importable, and
    surfaces a clear error if the store has no dose grids or no saved model.
    """
    try:
        from lume_ace3p.surrogate_data import load_training_store
        from lume_ace3p.surrogate import DoseSurrogate
    except ImportError as exc:
        raise ImportError(
            'The surrogate fit viewer requires the lume_ace3p package to be '
            'importable (pip install -e .). Original error: ' + str(exc))

    store_dir = os.path.abspath(store_dir)
    parent = os.path.dirname(store_dir)
    store_name = os.path.basename(store_dir)
    # Resolve relative field handles from the store's parent.
    os.chdir(parent)

    ts = load_training_store(store_name)
    if ts.dose is None:
        raise ValueError(
            'training store "%s" has no dose grids to visualize (a dry-run '
            'store has beta rows but no field artifacts).' % store_dir)

    model_dir = os.path.join(store_name, 'surrogate')
    if not os.path.isdir(model_dir):
        raise ValueError(
            'no saved surrogate found at %s; run the train_surrogate mode first.'
            % os.path.join(store_dir, 'surrogate'))
    model = DoseSurrogate.load(model_dir)
    return ts, model, store_name


# --- Arguments -------------------------------------------------------------
# Parse `--clamp LO HI` out first, so the remaining positional argument (if any)
# is the store directory.
_clamp_arg = None
_argv = list(sys.argv[1:])
if '--clamp' in _argv:
    _i = _argv.index('--clamp')
    try:
        _clamp_arg = (float(_argv[_i + 1]), float(_argv[_i + 2]))
        del _argv[_i:_i + 3]
    except (IndexError, ValueError):
        print('Warning: --clamp needs two numbers (log10 decades), e.g. '
              '--clamp -9 -1; using the data-derived range.')
        del _argv[_i:_i + 1]

# --- Load ------------------------------------------------------------------
root = tk.Tk()
root.withdraw()

if _argv:
    store_dir = _argv[0]
else:
    store_dir = filedialog.askdirectory(
        title='Choose a surrogate training-store directory')

if not store_dir:
    sys.exit()

ts, model, store_name = _load_store_and_model(store_dir)

# Import the transform inverse so the uncertainty band (fit-space mean +/- std)
# can be mapped back to linear dose before projecting/summing.
from lume_ace3p.surrogate import _invert_transform

beta = ts.beta                       # (N, D)
dose = ts.dose                       # (N, M) linear dose
indices = ts.indices                 # (M, 3) voxel (ix, iy, iz)
beta_names = list(ts.beta_names)
N, D = beta.shape

AXES = ['X', 'Y', 'Z']

mesh = (ts.manifest or {}).get('mesh') or {}
bins = mesh.get('bins')
if bins and len(bins) == 3:
    NX, NY, NZ = (int(bins[0]), int(bins[1]), int(bins[2]))
else:
    # Fall back to the voxel index extents if the manifest lacks a mesh block.
    NX = int(indices[:, 0].max()) + 1
    NY = int(indices[:, 1].max()) + 1
    NZ = int(indices[:, 2].max()) + 1

# --- Physical scoring-mesh geometry (mm) ----------------------------------
# The manifest records the mesh half-sizes and center in mm, so voxel index i
# along an axis spans [origin + i*spacing, origin + (i+1)*spacing]. Axes are
# labelled and ticked in mm rather than voxel index; VOXEL_MM is the per-axis
# voxel size and ORIGIN_MM the low corner. Falls back to unit spacing centred on
# zero if the manifest lacks the geometry (labels then read as index-like mm).
_half = mesh.get('half')
_center = mesh.get('center')
if _half and _center and len(_half) == 3 and len(_center) == 3:
    VOXEL_MM = [2.0 * float(_half[i]) / (NX, NY, NZ)[i] for i in range(3)]
    ORIGIN_MM = [float(_center[i]) - float(_half[i]) for i in range(3)]
else:
    VOXEL_MM = [1.0, 1.0, 1.0]
    ORIGIN_MM = [0.0, 0.0, 0.0]


def axis_extent_mm(axis):
    """(low, high) physical edge of `axis` ('X'|'Y'|'Z') in mm."""
    i = AXES.index(axis)
    n = (NX, NY, NZ)[i]
    return ORIGIN_MM[i], ORIGIN_MM[i] + n * VOXEL_MM[i]


def axis_centers_mm(axis):
    """Voxel-center coordinates along `axis` in mm (length = that axis' bins)."""
    i = AXES.index(axis)
    n = (NX, NY, NZ)[i]
    return ORIGIN_MM[i] + (np.arange(n) + 0.5) * VOXEL_MM[i]

# Per-beta slider bounds from the manifest DOE bounds, falling back to the
# observed training range.
bounds = (ts.manifest or {}).get('bounds')
if not bounds or len(bounds) != D:
    bounds = [[float(beta[:, j].min()), float(beta[:, j].max())]
              for j in range(D)]

# Fixed positive floor for the log display scale + log-space error metric: the
# smallest positive dose over the whole store, so every frame is comparable.
_positive = dose[dose > 0.0]
DISP_FLOOR = float(_positive.min()) if _positive.size else 1.0

# --- Fixed log color range (clamp) ----------------------------------------
# The 2D dose color scale is LOCKED across frames rather than autoscaled per
# frame, so stepping samples / sliding beta shows a real change in dose instead
# of a rescaled colorbar. Autoscaling also let a single hot voxel wash out the
# rest, and let the ~1e-16 Monte-Carlo noise tail stretch the low end.
#
# The range is taken from a robust percentile of the store's positive doses
# (not the raw min/max) so the noise tail is excluded, then snapped out to whole
# decades. Override on the command line with --clamp LO HI (decades, e.g.
# `--clamp -9 -1`), or set CLAMP_DECADES below to pin it permanently.
CLAMP_DECADES = None        # e.g. (-9.0, -1.0) to hard-pin the range

def _default_clamp():
    if not _positive.size:
        return -9.0, -1.0
    lo = float(np.floor(np.log10(np.percentile(_positive, 5.0))))
    hi = float(np.ceil(np.log10(_positive.max())))
    return lo, hi


# Precedence: --clamp on the command line, then a pinned CLAMP_DECADES, then the
# robust data-derived range.
if _clamp_arg is not None:
    LOG_LO, LOG_HI = _clamp_arg
elif CLAMP_DECADES is not None:
    LOG_LO, LOG_HI = CLAMP_DECADES
else:
    LOG_LO, LOG_HI = _default_clamp()

if LOG_HI <= LOG_LO:
    LOG_HI = LOG_LO + 1.0
# Linear-dose clamp bounds. CLAMP_FLOOR doubles as the value empty (zero) voxels
# are shown at: on a log scale zero is -inf, so clamping it to the bottom of the
# range renders no-data cells as the lowest color instead of masking them out.
CLAMP_FLOOR = 10.0 ** LOG_LO
CLAMP_CEIL = 10.0 ** LOG_HI

IX, IY, IZ = indices[:, 0].astype(int), indices[:, 1].astype(int), \
    indices[:, 2].astype(int)


def to_grid(flat):
    """Scatter a flat (M,) voxel vector into a dense (NX, NY, NZ) grid (missing
    voxels stay zero), matching the deposit viewers' grid[ix, iy, iz] = value."""
    g = np.zeros((NX, NY, NZ))
    g[IX, IY, IZ] = flat
    return g


def z_profile(grid):
    """Project a dense grid onto the beam axis Z: sum every voxel over x, y per z
    -> (NZ,). This is the 1-D dose profile the accuracy is easiest to read on."""
    return grid.sum(axis=(0, 1))


def predict_grids(beta_vec):
    """Predict for one beta: return (mean_lin_grid, lower_lin_grid,
    upper_lin_grid), all dense (NX, NY, NZ) linear-dose grids. The lower/upper
    grids are the per-voxel +/- 2 sigma band mapped back to linear dose (for a
    log model the band is symmetric in log, asymmetric in linear -- inverted
    per-voxel here so the projected profile band is always well defined)."""
    mfit, vfit = model.predict_dose(beta_vec, space='fit')
    sfit = np.sqrt(np.maximum(vfit, 0.0))
    mean_lin = _invert_transform(mfit, model.dose_transform, model.floor)
    lower_lin = _invert_transform(mfit - 2.0 * sfit, model.dose_transform,
                                  model.floor)
    upper_lin = _invert_transform(mfit + 2.0 * sfit, model.dose_transform,
                                  model.floor)
    return to_grid(mean_lin), to_grid(lower_lin), to_grid(upper_lin)


def rel_l2(pred_flat, truth_flat, logspace):
    """Per-sample relative-L2 error in the displayed scale. In log space the
    grids are mapped with log10(x + floor) first -- the meaningful metric for the
    exponential-in-beta dose (a good fit reads ~0.2 in log vs a misleading ~2 in
    linear)."""
    if logspace:
        p = np.log10(np.clip(pred_flat, 0.0, None) + DISP_FLOOR)
        t = np.log10(np.clip(truth_flat, 0.0, None) + DISP_FLOOR)
    else:
        p, t = pred_flat, truth_flat
    den = np.linalg.norm(t)
    return float(np.linalg.norm(p - t) / (den if den > 0.0 else 1.0))


# --- State -----------------------------------------------------------------
AXES = ['X', 'Y', 'Z']
state = {
    'sample': 0,          # current training-sample index
    'on_sample': True,    # sliders sit exactly on the sample's beta -> show truth
    'logscale': True,     # log10 vertical / color scale
    'view': 'project',    # 'slice' or 'project' for the 2D panel
    'axis': 'Y',          # slice-normal axis (project sums over it)
    'content': 'predicted',   # 'predicted' | 'truth' | 'difference'
}
_snapping = False         # guard so programmatic slider snaps don't trip on_sample


def current_beta():
    return np.array([beta_sliders[j].val for j in range(D)], dtype=float)


# --- Figure / axes ---------------------------------------------------------
fntsz = 14
fdict = {'family': 'serif', 'weight': 'normal', 'size': fntsz}

fig = plt.figure(figsize=(15, 9))
ax2d = fig.add_axes([0.30, 0.54, 0.34, 0.40])          # 2D dose grid
cax = fig.add_axes([0.655, 0.54, 0.012, 0.40])         # its colorbar
ax1d = fig.add_axes([0.30, 0.09, 0.37, 0.33])          # 1D z-profile

# Left-column radio controls.
scale_ax = fig.add_axes([0.03, 0.80, 0.15, 0.10])
scale_radio = RadioButtons(scale_ax, ('log10', 'linear'), active=0)
scale_ax.set_title('Vertical scale', fontdict={'size': 11})

view_ax = fig.add_axes([0.03, 0.64, 0.15, 0.10])
view_radio = RadioButtons(view_ax, ('slice', 'sum (project)'), active=1)
view_ax.set_title('2D view mode', fontdict={'size': 11})

axis_ax = fig.add_axes([0.03, 0.44, 0.15, 0.14])
axis_radio = RadioButtons(axis_ax, ('normal X', 'normal Y', 'normal Z'),
                          active=1)
axis_ax.set_title('2D slice normal', fontdict={'size': 11})

content_ax = fig.add_axes([0.03, 0.24, 0.15, 0.14])
content_radio = RadioButtons(content_ax,
                             ('predicted', 'truth', 'difference'), active=0)
content_ax.set_title('2D content', fontdict={'size': 11})

# Right-column sliders: one per beta, then the sample selector and slice index.
beta_sliders = []
for j in range(D):
    y = 0.90 - j * 0.052
    sax = fig.add_axes([0.83, y, 0.13, 0.02])
    lo, hi = float(bounds[j][0]), float(bounds[j][1])
    # initcolor='none' suppresses matplotlib's red "initial value" tick. It marks
    # valinit (sample 0's beta), which is meaningless once another sample is
    # selected or beta is moved freely.
    s = Slider(sax, beta_names[j], lo, hi, valinit=float(beta[0, j]),
               initcolor='none')
    beta_sliders.append(s)

sample_ax = fig.add_axes([0.83, 0.90 - D * 0.052 - 0.02, 0.13, 0.02])
sample_slider = Slider(sample_ax, 'sample', 0, N - 1, valinit=0, valstep=1,
                       initcolor='none')

slice_ax = fig.add_axes([0.30, 0.02, 0.37, 0.02])
slice_slider = Slider(slice_ax, 'slice [mm]', 0, NX - 1, valinit=NX // 2,
                      valstep=1, initcolor='none')

# Two independent log10-decade range sliders (one vertical bar with two handles
# each), because the panels live on genuinely different scales: the 2D panel shows
# per-voxel dose, while the 1D panel SUMS each z-slice over x and y, which lands
# ~7 decades higher. One shared range would leave the profile squashed against
# the top with most of the axis empty, so each panel gets its own.
#
# Both are LOCKED (not autoscaled per frame) so stepping samples or sliding beta
# shows a real change in dose rather than a rescaled axis.
_slider_lo = float(np.floor(np.log10(DISP_FLOOR))) - 1.0
_slider_hi = float(np.ceil(np.log10(dose.max()))) + 1.0 if dose.max() > 0 else 1.0

crange_ax = fig.add_axes([0.715, 0.58, 0.013, 0.32])
crange_slider = RangeSlider(crange_ax, 'voxel\ndose\n$10^{x}$',
                            _slider_lo, _slider_hi,
                            valinit=(LOG_LO, LOG_HI), orientation='vertical')


# The 1D panel's own range, defaulted from the actual z-projected profile spread
# (robust p5 -> max over the store, snapped to whole decades) rather than the
# per-voxel range.
def _profile_decades():
    prof = np.stack([dose[:, IZ == z].sum(axis=1) for z in range(NZ)], axis=1)
    pos = prof[prof > 0]
    if not pos.size:
        return -4.0, 0.0
    return (float(np.floor(np.log10(np.percentile(pos, 5.0)))),
            float(np.ceil(np.log10(pos.max()))))


PROF_LO, PROF_HI = _profile_decades()
_prof_slider_lo = min(_slider_lo, PROF_LO - 1.0)
_prof_slider_hi = max(_slider_hi, PROF_HI + 1.0)
prange_ax = fig.add_axes([0.715, 0.11, 0.013, 0.28])
prange_slider = RangeSlider(prange_ax, 'profile\ndose\n$10^{x}$',
                            _prof_slider_lo, _prof_slider_hi,
                            valinit=(PROF_LO, PROF_HI), orientation='vertical')


def _span(slider):
    """(lo, hi) log10 decades from a RangeSlider, guaranteeing at least one decade
    of span so a fully-collapsed pair still renders instead of producing a
    degenerate norm / y-axis."""
    lo, hi = (float(v) for v in slider.val)
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


def log_limits():
    """(lo, hi) per-voxel dose range in log10 decades (2D panel)."""
    return _span(crange_slider)


def color_limits():
    """(vmin, vmax) linear per-voxel dose bounds for the 2D color scale. The floor
    doubles as the value empty (zero) voxels are displayed at."""
    lo, hi = log_limits()
    return 10.0 ** lo, 10.0 ** hi


def profile_limits():
    """(ymin, ymax) linear bounds for the 1D z-projected profile's y-axis."""
    lo, hi = _span(prange_slider)
    return 10.0 ** lo, 10.0 ** hi


# --- 2D panel helpers (mirror geant4_deposit_plot orientation) -------------
def _orient(axis, plane):
    # Beam axis Z horizontal whenever it is in the plane; Z-normal puts X
    # horizontal, Y vertical.
    return plane.T if axis == 'Z' else plane


def slice_2d(grid, axis, idx):
    if axis == 'X':
        return _orient(axis, grid[idx, :, :])   # rows Y, cols Z
    if axis == 'Y':
        return _orient(axis, grid[:, idx, :])   # rows X, cols Z
    return _orient(axis, grid[:, :, idx])       # rows Y, cols X (Z-normal)


def project_2d(grid, axis):
    return _orient(axis, grid.sum(axis=AXES.index(axis)))


def plane_axes(axis):
    """(horizontal, vertical) axis names for the plane normal to `axis`. Beam
    axis Z is horizontal whenever it lies in the plane (accelerator convention);
    the Z-normal plane puts X horizontal, Y vertical."""
    if axis == 'X':
        return 'Z', 'Y'
    if axis == 'Y':
        return 'Z', 'X'
    return 'X', 'Y'


def plane_labels(axis):
    """Axis labels for the 2D panel, in physical mm."""
    haxis, vaxis = plane_axes(axis)
    return '%s [mm]' % haxis, '%s [mm]' % vaxis


def axis_size(axis):
    return {'X': NX, 'Y': NY, 'Z': NZ}[axis]


# --- Persistent 2D image + colorbar ---------------------------------------
# Created ONCE here and updated in place by redraw(). matplotlib's fig.colorbar()
# carves space out of its target axes each time it is called, so calling it per
# redraw made the bar shrink progressively; a single long-lived Colorbar avoids
# that entirely (and is faster).
_vmin0, _vmax0 = color_limits()
im = ax2d.imshow(np.full((NX, NZ), _vmin0), origin='lower', aspect='auto',
                 norm=LogNorm(vmin=_vmin0, vmax=_vmax0), cmap=VALUE_CMAP,
                 extent=(*axis_extent_mm('Z'), *axis_extent_mm('X')))
cbar = fig.colorbar(im, cax=cax, extend='both')


# --- Redraw ----------------------------------------------------------------
def redraw():
    beta_vec = current_beta()
    mean_grid, lower_grid, upper_grid = predict_grids(beta_vec)
    logscale = state['logscale']
    on_sample = state['on_sample']

    truth_grid = None
    if on_sample:
        truth_grid = to_grid(dose[state['sample']])

    # ---- 2D panel ----
    axis = state['axis']
    project = state['view'] == 'project'
    content = state['content']
    if content == 'truth' and not on_sample:
        content = 'predicted'
    if content == 'difference' and not on_sample:
        content = 'predicted'

    if content == 'predicted':
        grid2d = mean_grid
        clabel = 'predicted dose'
    elif content == 'truth':
        grid2d = truth_grid
        clabel = 'true dose'
    else:
        grid2d = np.abs(mean_grid - truth_grid)
        clabel = '|true - predicted|'

    slice_idx = min(int(slice_slider.val), axis_size(axis) - 1)
    if project:
        img = project_2d(grid2d, axis)
    else:
        img = slice_2d(grid2d, axis, slice_idx)
    xlab, ylab = plane_labels(axis)

    # Physical extent (mm) of the displayed plane, so the image is drawn in mm
    # rather than voxel index. imshow extent is (left, right, bottom, top).
    haxis, vaxis = plane_axes(axis)
    hlo, hhi = axis_extent_mm(haxis)
    vlo, vhi = axis_extent_mm(vaxis)

    if logscale:
        # Locked log range from the range slider. Empty (zero) voxels are clamped
        # up to the floor so they render as the lowest color instead of being
        # masked to white -- on a log scale zero is -inf, and showing it at the
        # bottom of the range reads correctly as "no dose here".
        vmin, vmax = color_limits()
        shown = np.clip(img, vmin, vmax)
        norm = LogNorm(vmin=vmin, vmax=vmax)
    else:
        shown = img
        norm = Normalize(vmin=0.0, vmax=(img.max() if img.max() > 0 else 1.0))

    # Update the persistent image + colorbar in place. Creating either one per
    # redraw would re-steal space from `cax` on every frame, which is what made
    # the colorbar shrink a little more with each slider move.
    im.set_data(shown)
    im.set_norm(norm)
    im.set_extent((hlo, hhi, vlo, vhi))
    cbar.update_normal(im)
    cbar.set_label(clabel, fontdict={'size': 11})
    ax2d.set_xlim(hlo, hhi)
    ax2d.set_ylim(vlo, vhi)
    ax2d.set_xlabel(xlab, fontdict=fdict)
    ax2d.set_ylabel(ylab, fontdict=fdict)
    if project:
        mode = 'sum over %s' % axis
    else:
        mode = '%s = %.1f mm' % (axis, axis_centers_mm(axis)[slice_idx])
    ax2d.set_title('dose grid  |  %s  |  %s' % (content, mode),
                   fontdict={'size': 12})

    # ---- 1D z-profile panel ----
    ax1d.clear()
    zs = axis_centers_mm('Z')          # voxel centers along the beam axis, mm
    prof_pred = z_profile(mean_grid)
    prof_lo = z_profile(lower_grid)
    prof_hi = z_profile(upper_grid)
    ax1d.plot(zs, prof_pred, '-', color='C0', lw=2, label='surrogate')
    # Clip the lower band edge to the panel's own floor so a band reaching zero
    # stays drawable on the log axis.
    _band_floor = profile_limits()[0] if logscale else 0.0
    ax1d.fill_between(zs, np.clip(prof_lo, _band_floor, None),
                      prof_hi, color='C0', alpha=0.2,
                      label=r'$\pm 2\sigma$')
    title = 'z-projected dose profile'
    if on_sample:
        prof_true = z_profile(truth_grid)
        ax1d.plot(zs, prof_true, '--', color='k', lw=2, label='Geant4 truth')
        err = rel_l2(mean_grid.ravel(), truth_grid.ravel(), logscale)
        space = 'log10' if logscale else 'linear'
        title += '   |   sample %d   |   rel-L2 (%s) = %.3f' % (
            state['sample'], space, err)
    else:
        title += '   |   off-sample (prediction only)'
    if logscale:
        # The profile has its OWN locked range (see prange_slider): summing each
        # z-slice over x, y puts it ~7 decades above per-voxel dose, so sharing the
        # 2D panel's range would squash it against the top of the axis.
        ax1d.set_yscale('log')
        ax1d.set_ylim(*profile_limits())
    ax1d.set_xlim(*axis_extent_mm('Z'))
    ax1d.set_xlabel('Z [mm] (beam axis)', fontdict=fdict)
    ax1d.set_ylabel('dose (sum over x, y)', fontdict=fdict)
    ax1d.set_title(title, fontdict={'size': 12})
    ax1d.legend(fontsize=10, loc='best')
    ax1d.grid(True, which='both', alpha=0.3)

    fig.canvas.draw_idle()


# --- Callbacks -------------------------------------------------------------
def snap_to_sample(i):
    """Snap all beta sliders to training sample `i` and turn the truth overlay
    on. The `_snapping` guard keeps the programmatic set_val calls from marking
    the view off-sample."""
    global _snapping
    _snapping = True
    for j in range(D):
        beta_sliders[j].set_val(float(beta[i, j]))
    _snapping = False
    state['sample'] = i
    state['on_sample'] = True


def on_sample_change(val):
    snap_to_sample(int(val))
    redraw()


def on_beta(val):
    if _snapping:
        return
    # A manual beta change moves the point off the stored sample -> no truth.
    state['on_sample'] = False
    redraw()


def on_scale(label):
    state['logscale'] = (label == 'log10')
    redraw()


def on_view(label):
    state['view'] = 'project' if label.startswith('sum') else 'slice'
    slice_slider.ax.set_visible(state['view'] == 'slice')
    redraw()


def on_axis(label):
    axis = label.split()[-1]
    state['axis'] = axis
    n = axis_size(axis)
    slice_slider.valmax = n - 1
    slice_ax.set_xlim(0, n - 1)
    if slice_slider.val > n - 1:
        slice_slider.set_val(n // 2)
    redraw()


def on_slice(val):
    # The slider steps voxel indices; show the corresponding physical mm
    # coordinate so the readout matches the axis units.
    idx = min(int(val), axis_size(state['axis']) - 1)
    slice_slider.valtext.set_text(
        '%.1f' % axis_centers_mm(state['axis'])[idx])
    if state['view'] == 'slice':
        redraw()


def on_content(label):
    state['content'] = label
    redraw()


def on_range(val):
    # Either dose-range slider (2D color scale / 1D y-axis). Both only apply on
    # the log scale (linear mode autoscales).
    if state['logscale']:
        redraw()


sample_slider.on_changed(on_sample_change)
for s in beta_sliders:
    s.on_changed(on_beta)
scale_radio.on_clicked(on_scale)
view_radio.on_clicked(on_view)
axis_radio.on_clicked(on_axis)
slice_slider.on_changed(on_slice)
content_radio.on_clicked(on_content)
crange_slider.on_changed(on_range)
prange_slider.on_changed(on_range)

# Initial state: snapped to sample 0, projection view (slice slider hidden).
snap_to_sample(0)
slice_slider.ax.set_visible(state['view'] == 'slice')
print('Loaded store "%s": %d samples, %d-D beta, %dx%dx%d mesh, '
      'dose_transform=%s' % (store_name, N, D, NX, NY, NZ, model.dose_transform))
print('  voxel size %.3g x %.3g x %.3g mm, extent X%s Y%s Z%s mm'
      % (VOXEL_MM[0], VOXEL_MM[1], VOXEL_MM[2],
         axis_extent_mm('X'), axis_extent_mm('Y'), axis_extent_mm('Z')))
print('  voxel-dose color scale locked to 1e%g .. 1e%g (empty voxels shown at '
      'the floor); --clamp LO HI sets this' % (LOG_LO, LOG_HI))
print('  profile y-axis locked to 1e%g .. 1e%g (summed over x, y -- its own '
      'scale)' % (PROF_LO, PROF_HI))
print('  drag either range slider\'s handles to retune')
redraw()
plt.show()
