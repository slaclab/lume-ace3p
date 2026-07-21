"""Phase-3 tests: the PCA-GP forward dose surrogate + the ``train_surrogate``
mode (see docs/geant4_surrogate_inversion_plan.md, Phase 3).

Verification (Phase 3 done when):

* Held-out β: reconstructed dose matches the (noiseless) truth within a reported
  relative-L2 metric.
* Predicted variance is calibrated — strictly > 0 (constraint #2: genuine GP
  noise term), and held-out error is within a few predicted std (coverage).
* Round-trip: ``project(predict_dose(β)) ≈ predicted_coeffs(β)``.
* Model saves and reloads to identical predictions.
* The ``train_surrogate`` mode + CLI dispatch produce a saved model dir from a
  real (synthetic) store, and a dry-run (grid-less) store raises a clear error.

Per the synthetic-first strategy, everything runs locally against a synthetic
analytic β→dose fixture with injected MC-style noise — no Geant4 env. The fixture
is a genuinely low-rank + noisy map so PCA-GP has real structure to recover, and
it drives the actual Phase-2 ``collect_training_data`` mode (via a fake workflow
emitting the synthetic grid) so the full store → load → fit path is exercised.
"""

import os

import numpy as np
import pytest

from lume_ace3p import surrogate_data
from lume_ace3p.modes import collect_training_data, train_surrogate, run_mode
from lume_ace3p.surrogate import DoseSurrogate


BETA_NAMES = [f'beta{i}' for i in range(8)]
BIN_EDGES = list(np.linspace(-0.1251788, 0.1252434, 9))


# --------------------------------------------------------------------------- #
# Synthetic analytic β → dose fixture.
#
# A low-rank, smooth, noisy map: dose(β) = mean + Σ_j g_j(β)·ψ_j(voxel) + ε.
# Three smooth spatial fields ψ_j (Gaussians along z) modulated by smooth
# nonlinear functions g_j of β — so ~3 PCA modes capture the coherent signal and
# the discarded tail absorbs the injected Gaussian noise.
# --------------------------------------------------------------------------- #

_NZ = 40                      # voxels along z (M = _NZ for this fixture)
_Z = np.linspace(0.0, 1.0, _NZ)


def _gaussian(center, width):
    return np.exp(-0.5 * ((_Z - center) / width) ** 2)


# Three fixed spatial modes.
_PSI = np.vstack([
    _gaussian(0.25, 0.10),
    _gaussian(0.55, 0.15),
    _gaussian(0.80, 0.08),
])                             # (3, _NZ)
_MEAN_FIELD = 1.0 + 0.2 * _Z   # a smooth baseline


def _coeffs_of_beta(beta):
    """Smooth nonlinear amplitudes g_j(β) for the three spatial modes."""
    b = np.atleast_2d(np.asarray(beta, dtype=float))
    # Use disjoint β groups so each mode responds to a different part of β.
    g0 = b[:, 0:3].mean(axis=1)
    g1 = 0.05 * (b[:, 3:6] ** 2).mean(axis=1)
    g2 = 10.0 * np.sin(0.05 * b[:, 6:8].mean(axis=1))
    return np.vstack([g0, g1, g2]).T          # (B, 3)


def dose_of_beta(beta, noise=0.0, seed=None):
    """Synthetic dose grid(s) for β. Noiseless when ``noise == 0``.

    Returns ``(M,)`` for a single β, ``(B, M)`` for a batch."""
    beta_arr = np.atleast_2d(np.asarray(beta, dtype=float))
    single = np.asarray(beta).ndim == 1
    g = _coeffs_of_beta(beta_arr)             # (B, 3)
    grids = _MEAN_FIELD + g @ _PSI            # (B, _NZ)
    if noise:
        rng = np.random.default_rng(seed)
        grids = grids + rng.normal(0.0, noise, size=grids.shape)
    return grids[0] if single else grids


# --------------------------------------------------------------------------- #
# Fake workflow that drives the real collect_training_data mode with synthetic
# dose grids (mirrors tests/test_surrogate_data.py::_FakeWorkflow).
# --------------------------------------------------------------------------- #


class _FakeModule:
    type = 'particles'

    def __init__(self):
        self.params = {'num_bins': 8, 'beta_inputs': list(BETA_NAMES),
                       'bin_edges': list(BIN_EDGES)}


class _SyntheticDoseWorkflow:
    """Emits the synthetic β→dose grid (with fixed per-sample MC noise) so a real
    training store can be collected and loaded locally without Geant4."""

    def __init__(self, noise=0.02):
        self.modules = [_FakeModule()]
        self.workdir_mode = 'manual'
        self.baseworkdir = None
        self.dry_run = False
        self.noise = noise
        self._last_beta = None
        self._call = 0

    def evaluate(self, overrides):
        self._last_beta = np.array([overrides[n] for n in BETA_NAMES])
        self._call += 1
        return {}

    def field(self):
        # Deterministic per-sample noise seed so a resumed run is reproducible.
        seed = int(abs(self._last_beta.sum()) * 1000) % (2 ** 31)
        values = dose_of_beta(self._last_beta, noise=self.noise, seed=seed)
        indices = np.stack(
            [np.zeros(_NZ, int), np.zeros(_NZ, int), np.arange(_NZ)], axis=1)
        return {'dose': {'indices': indices, 'values': values}}


def _mode_cfg(store, **overrides):
    cfg = {'type': 'collect_training_data', 'store': store,
           'num_samples': 40, 'sampler': 'sobol', 'seed': 0, 'fidelity': 10000,
           'variables': {n: [40.0, 60.0] for n in BETA_NAMES}}
    cfg.update(overrides)
    return cfg


def _collect_store(tmp_path, num_samples=40, noise=0.02):
    store = str(tmp_path / 'store')
    collect_training_data(_mode_cfg(store, num_samples=num_samples),
                          _SyntheticDoseWorkflow(noise=noise))
    return store


def _fit_from_store(store, **kwargs):
    ts = surrogate_data.load_training_store(store)
    return DoseSurrogate.fit(ts.beta, ts.dose, beta_names=ts.beta_names,
                             **kwargs), ts


# --------------------------------------------------------------------------- #
# DoseSurrogate — direct unit tests on the fixture.
# --------------------------------------------------------------------------- #


def test_fit_shapes_and_low_rank(tmp_path):
    store = _collect_store(tmp_path, num_samples=40)
    surrogate, ts = _fit_from_store(store, variance=0.99, seed=0)
    assert ts.dose.shape == (40, _NZ)
    assert surrogate.mean.shape == (_NZ,)
    # The signal has 3 spatial modes; PCA at 99% energy should stay small.
    assert 1 <= surrogate.num_components <= 6
    assert surrogate.basis.shape == (surrogate.num_components, _NZ)
    assert surrogate.kept_energy >= 0.99


def test_explicit_k_overrides_variance(tmp_path):
    store = _collect_store(tmp_path)
    surrogate, _ = _fit_from_store(store, k=3, seed=0)
    assert surrogate.num_components == 3


def test_holdout_reconstruction_accuracy(tmp_path):
    """Held-out β: reconstructed dose matches the noiseless truth within a
    reported relative-L2 (Phase-3 bar #1)."""
    store = _collect_store(tmp_path, num_samples=48, noise=0.02)
    ts = surrogate_data.load_training_store(store)
    n_hold = 12
    rng = np.random.default_rng(0)
    perm = rng.permutation(len(ts))
    hold, train = perm[:n_hold], perm[n_hold:]

    surrogate = DoseSurrogate.fit(ts.beta[train], ts.dose[train],
                                  variance=0.999, seed=0,
                                  beta_names=ts.beta_names)
    pred_mean, _ = surrogate.predict_dose(ts.beta[hold])
    truth = dose_of_beta(ts.beta[hold], noise=0.0)   # noiseless truth
    rel_l2 = (np.linalg.norm(pred_mean - truth, axis=1)
              / np.linalg.norm(truth, axis=1))
    assert rel_l2.mean() < 0.10


def test_predicted_variance_is_positive_and_calibrated(tmp_path):
    """Constraint #2: predicted variance is strictly > 0, and held-out error is
    within a few predicted std (coverage sanity)."""
    store = _collect_store(tmp_path, num_samples=48, noise=0.05)
    ts = surrogate_data.load_training_store(store)
    n_hold = 12
    rng = np.random.default_rng(1)
    perm = rng.permutation(len(ts))
    hold, train = perm[:n_hold], perm[n_hold:]

    surrogate = DoseSurrogate.fit(ts.beta[train], ts.dose[train],
                                  variance=0.999, seed=0)
    pred_mean, pred_var = surrogate.predict_dose(ts.beta[hold])
    assert np.all(pred_var > 0.0)               # genuine, non-zero noise
    truth = dose_of_beta(ts.beta[hold], noise=0.0)
    std = np.sqrt(pred_var)
    # Predicted std is in a sane range — not collapsed to ~0 (the interpolating
    # prior constraint #2 forbids) and not absurdly wide. It should be on the
    # order of the injected noise (0.05), within an order of magnitude either way.
    mean_std = float(std.mean())
    assert 5e-3 < mean_std < 5e-1
    # Held-out error mostly sits within a few predicted std of the truth. PCA-GP
    # variance omits the POD truncation residual, so this is a coverage sanity
    # check, not a strict statistical guarantee — keep the bar off the knife-edge.
    z = np.abs(pred_mean - truth) / std
    assert np.mean(z < 3.0) >= 0.85


def test_roundtrip_project_of_predict(tmp_path):
    """project(predict_dose(β)) ≈ predicted_coeffs(β) (Phase-3 bar #3)."""
    store = _collect_store(tmp_path)
    surrogate, ts = _fit_from_store(store, seed=0)
    beta = ts.beta[:5]
    mean_grid, _ = surrogate.predict_dose(beta)
    projected = surrogate.project(mean_grid)
    coeff_mean, _ = surrogate.predicted_coeffs(beta)
    assert np.allclose(projected, coeff_mean, atol=1e-8)


def test_project_single_and_batch(tmp_path):
    store = _collect_store(tmp_path)
    surrogate, ts = _fit_from_store(store, seed=0)
    k = surrogate.num_components
    assert surrogate.project(ts.dose[0]).shape == (k,)
    assert surrogate.project(ts.dose[:4]).shape == (4, k)


def test_predict_single_and_batch_shapes(tmp_path):
    store = _collect_store(tmp_path)
    surrogate, ts = _fit_from_store(store, seed=0)
    m, v = surrogate.predict_dose(ts.beta[0])
    assert m.shape == (_NZ,) and v.shape == (_NZ,)
    mb, vb = surrogate.predict_dose(ts.beta[:3])
    assert mb.shape == (3, _NZ) and vb.shape == (3, _NZ)


def test_save_reload_identical_predictions(tmp_path):
    """Model saves and reloads to identical predictions (Phase-3 bar #4)."""
    store = _collect_store(tmp_path)
    surrogate, ts = _fit_from_store(store, seed=0)
    model_dir = str(tmp_path / 'surrogate')
    surrogate.save(model_dir)
    for name in ('basis.npz', 'gps.joblib', 'surrogate.json'):
        assert os.path.isfile(os.path.join(model_dir, name))

    reloaded = DoseSurrogate.load(model_dir)
    beta = ts.beta[:6]
    m0, v0 = surrogate.predict_dose(beta)
    m1, v1 = reloaded.predict_dose(beta)
    assert np.allclose(m0, m1, atol=1e-10)
    assert np.allclose(v0, v1, atol=1e-10)
    assert np.allclose(surrogate.project(ts.dose[:6]),
                       reloaded.project(ts.dose[:6]), atol=1e-10)
    assert reloaded.beta_names == BETA_NAMES


def test_fit_rejects_misaligned_or_tiny(tmp_path):
    with pytest.raises(ValueError):
        DoseSurrogate.fit(np.zeros((3, 8)), np.zeros((4, 40)))   # misaligned
    with pytest.raises(ValueError):
        DoseSurrogate.fit(np.zeros((1, 8)), np.zeros((1, 40)))   # too few


# --------------------------------------------------------------------------- #
# train_surrogate mode + dispatch.
# --------------------------------------------------------------------------- #


def test_train_surrogate_mode_saves_model(tmp_path):
    store = _collect_store(tmp_path, num_samples=32)
    model_dir = str(tmp_path / 'model')
    cfg = {'type': 'train_surrogate', 'store': store, 'variance': 0.99,
           'seed': 0, 'model_dir': model_dir}
    surrogate = train_surrogate(cfg)
    assert isinstance(surrogate, DoseSurrogate)
    assert os.path.isfile(os.path.join(model_dir, 'basis.npz'))
    assert os.path.isfile(os.path.join(model_dir, 'surrogate.json'))


def test_train_surrogate_default_model_dir(tmp_path):
    store = _collect_store(tmp_path, num_samples=24)
    train_surrogate({'type': 'train_surrogate', 'store': store})
    assert os.path.isfile(os.path.join(store, 'surrogate', 'basis.npz'))


def test_train_surrogate_holdout_report(tmp_path):
    store = _collect_store(tmp_path, num_samples=40)
    cfg = {'type': 'train_surrogate', 'store': store, 'variance': 0.999,
           'holdout': 0.25, 'seed': 0}
    train_surrogate(cfg)
    report = os.path.join(store, 'train_report.txt')
    assert os.path.isfile(report)
    import pandas as pd
    df = pd.read_csv(report, sep='\t')
    assert 'relative_l2' in df.columns
    assert len(df) == 10          # 25% of 40


def test_run_mode_dispatches_train_surrogate(tmp_path):
    store = _collect_store(tmp_path, num_samples=16)
    model_dir = str(tmp_path / 'm')
    out = run_mode({'type': 'train_surrogate', 'store': store,
                    'model_dir': model_dir}, workflow=None)
    assert isinstance(out, DoseSurrogate)
    assert os.path.isfile(os.path.join(model_dir, 'gps.joblib'))


def test_train_surrogate_requires_store():
    with pytest.raises(ValueError, match='store'):
        train_surrogate({'type': 'train_surrogate'})


def test_train_surrogate_rejects_gridless_store(tmp_path):
    """A dry-run store (β rows, no dose grids) hard-fails with a clear message."""
    store = str(tmp_path / 'dry_store')
    os.makedirs(store)
    import pandas as pd
    from lume_ace3p.results import write_table
    df = pd.DataFrame({n: [50.0, 51.0] for n in BETA_NAMES})
    df[surrogate_data.FIDELITY_COLUMN] = [np.nan, np.nan]
    write_table(df, os.path.join(store, surrogate_data.TABLE_FILENAME))
    surrogate_data.write_manifest(store, {'beta_names': BETA_NAMES,
                                          'num_bins': 8})
    with pytest.raises(ValueError, match='no dose grids'):
        train_surrogate({'type': 'train_surrogate', 'store': store})


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
