import os
import sys
import numpy as np
import tkinter as tk
from tkinter import filedialog
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm, Normalize
from matplotlib.widgets import Slider, RadioButtons

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
# deposit viewers.
#
# Usage:  python plotting/surrogate_fit_plot.py [store_dir]
# If no directory is given, a directory dialog is opened.

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Jet colormap for the dose, matching the Geant4 deposit viewers; zero / masked
# voxels render as the (white) background so nonzero voxels stand out.
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


# --- Load ------------------------------------------------------------------
root = tk.Tk()
root.withdraw()

if len(sys.argv) == 2:
    store_dir = sys.argv[1]
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

mesh = (ts.manifest or {}).get('mesh') or {}
bins = mesh.get('bins')
if bins and len(bins) == 3:
    NX, NY, NZ = (int(bins[0]), int(bins[1]), int(bins[2]))
else:
    # Fall back to the voxel index extents if the manifest lacks a mesh block.
    NX = int(indices[:, 0].max()) + 1
    NY = int(indices[:, 1].max()) + 1
    NZ = int(indices[:, 2].max()) + 1

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
    sax = fig.add_axes([0.80, y, 0.16, 0.02])
    lo, hi = float(bounds[j][0]), float(bounds[j][1])
    s = Slider(sax, beta_names[j], lo, hi, valinit=float(beta[0, j]))
    beta_sliders.append(s)

sample_ax = fig.add_axes([0.80, 0.90 - D * 0.052 - 0.02, 0.16, 0.02])
sample_slider = Slider(sample_ax, 'sample', 0, N - 1, valinit=0, valstep=1)

slice_ax = fig.add_axes([0.30, 0.02, 0.37, 0.02])
slice_slider = Slider(slice_ax, 'slice index', 0, NX - 1, valinit=NX // 2,
                      valstep=1)


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


def plane_labels(axis):
    if axis == 'X':
        return 'iZ', 'iY'
    if axis == 'Y':
        return 'iZ', 'iX'
    return 'iX', 'iY'


def axis_size(axis):
    return {'X': NX, 'Y': NY, 'Z': NZ}[axis]


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

    if project:
        img = project_2d(grid2d, axis)
    else:
        idx = min(int(slice_slider.val), axis_size(axis) - 1)
        img = slice_2d(grid2d, axis, idx)
    xlab, ylab = plane_labels(axis)

    ax2d.clear()
    cax.clear()
    pos = img[img > 0]
    if logscale and pos.size:
        vmin = pos.min()
        vmax = img.max()
        if vmax <= vmin:
            vmax = vmin * 10.0
        norm = LogNorm(vmin=vmin, vmax=vmax)
        masked = np.ma.masked_less_equal(img, 0.0)
    else:
        norm = Normalize(vmin=0.0, vmax=(img.max() if img.max() > 0 else 1.0))
        masked = img
    im = ax2d.imshow(masked, origin='lower', aspect='auto', norm=norm,
                     cmap=VALUE_CMAP)
    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label(clabel, fontdict={'size': 11})
    ax2d.set_xlabel(xlab, fontdict=fdict)
    ax2d.set_ylabel(ylab, fontdict=fdict)
    mode = 'sum over %s' % axis if project else '%s = %d' % (axis, int(slice_slider.val))
    ax2d.set_title('dose grid  |  %s  |  %s' % (content, mode),
                   fontdict={'size': 12})

    # ---- 1D z-profile panel ----
    ax1d.clear()
    zs = np.arange(NZ)
    prof_pred = z_profile(mean_grid)
    prof_lo = z_profile(lower_grid)
    prof_hi = z_profile(upper_grid)
    ax1d.plot(zs, prof_pred, '-', color='C0', lw=2, label='surrogate')
    ax1d.fill_between(zs, np.clip(prof_lo, 0.0 if not logscale else DISP_FLOOR,
                                  None),
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
        ax1d.set_yscale('log')
    ax1d.set_xlabel('iZ (beam axis)', fontdict=fdict)
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
    if state['view'] == 'slice':
        redraw()


def on_content(label):
    state['content'] = label
    redraw()


sample_slider.on_changed(on_sample_change)
for s in beta_sliders:
    s.on_changed(on_beta)
scale_radio.on_clicked(on_scale)
view_radio.on_clicked(on_view)
axis_radio.on_clicked(on_axis)
slice_radio_visible = slice_slider.on_changed(on_slice)
content_radio.on_clicked(on_content)

# Initial state: snapped to sample 0, projection view (slice slider hidden).
snap_to_sample(0)
slice_slider.ax.set_visible(state['view'] == 'slice')
print('Loaded store "%s": %d samples, %d-D beta, %dx%dx%d mesh, '
      'dose_transform=%s' % (store_name, N, D, NX, NY, NZ, model.dose_transform))
redraw()
plt.show()
