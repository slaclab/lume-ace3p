"""Tests for the additive per-bin dose surrogate (plans/geant4_dose_campaign_plan.md,
Phase 3 item 1).

The fixture is additive by construction: eight Gaussian shapes along z, one per
bin, each with an exponential-in-β amplitude, and a Poisson-like noise model
(variance per event proportional to the shape). One noisy "run" per (bin, β grid
point) plays the part of a Phase 1 bin-isolated Geant4 run. Bins 0 and 7 launch
fewer primaries, as the end bins of the real chain do.
"""

import os

import numpy as np
import pytest

from lume_ace3p.modes import invert_bayesian, invert_optimize
from lume_ace3p.results import save_field
from lume_ace3p.surrogate import DoseSurrogate, load_surrogate
from lume_ace3p.surrogate_additive import AdditiveSurrogate


NB = 8
NZ = 64
GRID = np.arange(40.0, 61.0, 2.0)
BETA_NAMES = [f'beta{b}' for b in range(NB)]
INDICES = np.stack([np.zeros(NZ, int), np.zeros(NZ, int), np.arange(NZ)], axis=1)
_Z = np.linspace(0.0, 1.0, NZ)
_SHAPES = np.array([np.exp(-0.5 * ((_Z - (b + 0.5) / NB) / 0.07) ** 2)
                    for b in range(NB)])
_SHAPES /= _SHAPES.sum(axis=1, keepdims=True)
_PRIMARIES = np.array([3e3, 2e4, 2e4, 2e4, 2e4, 2e4, 2e4, 3e3])
HPM = 13


def true_amplitudes(beta):
    return np.exp(0.6 * (np.asarray(beta, float) - 50.0))


def true_dose(beta):
    return true_amplitudes(beta) @ _SHAPES


def true_variance(beta, hpm=1):
    """Per-event relative variance per voxel equal to the shape, so a run with
    E events at amplitude a has voxel variance a² S / E."""
    amp = true_amplitudes(beta)
    return (amp ** 2 / (_PRIMARIES * hpm)) @ _SHAPES


def noisy_target(beta, hpm=1, seed=0):
    rng = np.random.default_rng(seed)
    return true_dose(beta) + rng.normal(size=NZ) * np.sqrt(true_variance(beta, hpm))


def training_runs(seed=0, noise=True):
    """``(dose, variance, primaries, hpm)`` as (B, n, M) / (B, n) arrays."""
    rng = np.random.default_rng(seed)
    dose = np.empty((NB, GRID.size, NZ))
    variance = np.empty_like(dose)
    for b in range(NB):
        for i, beta in enumerate(GRID):
            amp = true_amplitudes(beta)
            events = _PRIMARIES[b] * HPM
            var = amp ** 2 * _SHAPES[b] / events
            # Dose is nonnegative, as a scored grid is.
            dose[b, i] = np.abs(amp * _SHAPES[b] + (
                rng.normal(size=NZ) * np.sqrt(var) if noise else 0.0))
            variance[b, i] = var
    primaries = np.repeat(_PRIMARIES[:, None], GRID.size, axis=1)
    return dose, variance, primaries, np.full(primaries.shape, HPM)


def fitted(noise=True, **kwargs):
    dose, variance, primaries, hpm = training_runs(noise=noise)
    return AdditiveSurrogate.fit(GRID, dose, variance, primaries, hpm,
                                 beta_names=BETA_NAMES, voxel_indices=INDICES,
                                 **kwargs)


# --------------------------------------------------------------------------- #
# Fit and forward model
# --------------------------------------------------------------------------- #


def test_noiseless_fit_reproduces_the_additive_truth():
    model = fitted(noise=False)
    assert model.num_bins == NB and model.shapes.shape == (NB, NZ)
    assert np.allclose(model.shapes, _SHAPES)
    for beta in (np.full(NB, 51.3), np.linspace(41.0, 59.0, NB)):
        assert np.allclose(model.predict_dose(beta, space='linear'),
                           true_dose(beta), rtol=1e-4)


def test_noisy_fit_predicts_within_mc_error():
    model = fitted()
    beta = np.array([52., 47., 55., 44., 58., 49., 46., 53.])
    mean, var = model.predict_dose(beta)
    assert np.all(var > 0.0)
    rel = np.linalg.norm(mean - true_dose(beta)) / np.linalg.norm(true_dose(beta))
    assert rel < 0.05


def test_predict_batch_and_single_agree():
    model = fitted()
    betas = np.array([np.full(NB, 45.0), np.full(NB, 55.0)])
    mean, var = model.predict_dose(betas)
    assert mean.shape == var.shape == (2, NZ)
    assert np.allclose(mean[1], model.predict_dose(betas[1])[0])


def test_amplitude_curve_inverts():
    model = fitted()
    beta = np.linspace(40.5, 59.5, NB)
    assert np.allclose(model.beta_from_amplitudes(model.amplitudes(beta)), beta,
                       atol=1e-8)
    # Outside the trained range: clipped to the grid ends, not extrapolated.
    ends = model.beta_from_amplitudes(np.r_[0.0, 1e30, np.ones(NB - 2)])
    assert ends[0] == GRID[0] and ends[1] == GRID[-1]


def test_target_variance_matches_the_fixture_noise():
    model = fitted(noise=False)
    beta = np.full(NB, 50.0)
    assert np.allclose(model.target_variance(beta, 1), true_variance(beta, 1))
    assert np.allclose(model.target_variance(beta, 10), true_variance(beta, 10))


def test_runs_with_one_particle_file_are_one_realization():
    """Same primaries and hpm: one Monte Carlo realization reweighted. Its shape
    must count once in the weighted mean, not once per run."""
    dose, variance, primaries, hpm = training_runs()
    # Make runs 5..10 of bin 3 one realization, as the saturated runs are.
    primaries = primaries.copy()
    primaries[3, :5] = np.arange(1, 6) * 1e3
    model = AdditiveSurrogate.fit(GRID, dose, variance, primaries, hpm)
    # 5 singleton groups + 1 group of 6 (same primaries as the default).
    assert model.shape_var[3].sum() == pytest.approx(
        _expected_group_variance(dose[3], variance[3], [[0], [1], [2], [3], [4],
                                                        list(range(5, 11))]))


def _expected_group_variance(dose, variance, groups):
    totals = dose.sum(axis=1)
    vs = [np.mean(variance[g] / totals[g, None] ** 2, axis=0) for g in groups]
    w = np.array([1.0 / v.sum() for v in vs])
    w /= w.sum()
    return float(sum(wi ** 2 * v.sum() for wi, v in zip(w, vs)))


def test_shape_beta_min_drops_onset_runs_from_the_shape_only():
    dose, variance, primaries, hpm = training_runs()
    primaries = primaries * np.arange(1, GRID.size + 1)    # all independent
    full = AdditiveSurrogate.fit(GRID, dose, variance, primaries, hpm)
    cut = AdditiveSurrogate.fit(GRID, dose, variance, primaries, hpm,
                                shape_beta_min=44.0)
    assert np.array_equal(full.log_amp, cut.log_amp)
    assert not np.allclose(full.shapes, cut.shapes)


def test_fit_rejects_a_dark_or_non_monotone_bin():
    dose, variance, primaries, hpm = training_runs(noise=False)
    dark = dose.copy()
    dark[2, 0] = 0.0
    with pytest.raises(ValueError, match='no dose'):
        AdditiveSurrogate.fit(GRID, dark, variance, primaries, hpm)
    flat = dose.copy()
    flat[5, 4] = flat[5, 5]
    with pytest.raises(ValueError, match='rise strictly'):
        AdditiveSurrogate.fit(GRID, flat, variance, primaries, hpm)
    with pytest.raises(ValueError, match='variance has shape'):
        AdditiveSurrogate.fit(GRID, dose, variance[:, :3], primaries, hpm)


# --------------------------------------------------------------------------- #
# Coefficient space and point inversion
# --------------------------------------------------------------------------- #


def test_project_recovers_amplitudes_and_misfit_vanishes_at_truth():
    model = fitted(noise=False)
    beta = np.full(NB, 52.0)
    coeffs = model.project(true_dose(beta))
    assert coeffs.shape == (NB + NB * NB,)
    amp_hat, upper = model.unpack(coeffs)
    assert np.allclose(amp_hat, true_amplitudes(beta), rtol=1e-6)
    assert np.allclose(upper, np.triu(upper))
    assert model.coeff_misfit(beta, coeffs) < 1e-12
    assert model.coeff_misfit(beta + 0.5, coeffs) > 1.0


def test_misfit_equals_voxel_chi_square_up_to_a_constant():
    """The packed precision makes the 8-D misfit the voxel chi-square."""
    model = fitted()
    target = noisy_target(np.full(NB, 50.0), seed=3)
    coeffs = model.project(target)
    sigma = model.noise_sigma(model.beta_from_amplitudes(model.unpack(coeffs)[0]))
    chi2 = []
    misfit = []
    for beta in (np.full(NB, 49.0), np.full(NB, 51.0), np.linspace(45, 55, NB)):
        resid = (model.predict_dose(beta, space='linear') - target) / sigma
        chi2.append(np.sum(resid ** 2))
        misfit.append(model.coeff_misfit(beta, coeffs) * model.noise_scale ** 2)
    assert np.allclose(np.diff(chi2), np.diff(misfit), rtol=1e-6)


def test_project_batch_and_wrong_length():
    model = fitted()
    batch = np.stack([true_dose(np.full(NB, 48.0)), true_dose(np.full(NB, 52.0))])
    assert model.project(batch).shape == (2, NB + NB * NB)
    with pytest.raises(ValueError, match='voxels'):
        model.project(np.ones(NZ - 1))
    with pytest.raises(ValueError, match='coefficients'):
        model.coeff_misfit(np.full(NB, 50.0), np.ones(NB))


def test_invert_recovers_beta_when_every_bin_is_visible():
    model = fitted()
    truth = np.array([52., 47., 55., 44., 58., 49., 46., 53.])
    # Uniform-ish β with a high-fidelity target: every bin carries dose.
    target = noisy_target(np.full(NB, 50.0), hpm=100, seed=1)
    result = model.invert(model.project(target))
    assert result.num_distinct == 1
    assert np.allclose(result.beta, 50.0, atol=0.3)
    # A noiseless model and target with spread β come back exactly, even the
    # bins the noise would hide.
    exact = fitted(noise=False).invert(
        fitted(noise=False).project(true_dose(truth)))
    assert np.allclose(exact.beta, truth, atol=0.05)


def test_invert_respects_bounds_and_rejects_extrapolation():
    model = fitted()
    coeffs = model.project(true_dose(np.full(NB, 50.0)))
    bounds = [(51.0, 60.0)] * NB
    result = model.invert(coeffs, bounds=bounds)
    assert np.all(result.beta >= 51.0 - 1e-9)
    with pytest.raises(ValueError, match='does not extrapolate'):
        model.invert(coeffs, bounds=[(30.0, 60.0)] * NB)


# --------------------------------------------------------------------------- #
# Identifiability
# --------------------------------------------------------------------------- #


def test_identifiability_uniform_beta_constrains_every_bin():
    ident = fitted().identifiability(np.full(NB, 54.0))
    assert ident.rank == NB and not ident.is_degenerate
    assert ident.beta_sigma.shape == (NB,)
    assert np.all(ident.beta_sigma < 1.0)
    assert 'resolution' in ident.summary()


def test_identifiability_one_dominant_bin_hides_its_neighbours():
    """The dominant bin's own noise buries the bins next to it. Bins far from
    it, where its shape has no dose, stay visible against the sigma floor: the
    pattern Phase 2 found on the real chain."""
    beta = np.full(NB, 42.0)
    beta[3] = 58.0
    ident = fitted().identifiability(beta)
    assert ident.beta_sigma[3] < 0.2
    assert np.all(ident.beta_sigma[[1, 2, 4, 5]] > 1.0)
    assert ident.beta_sigma[0] < 1.0 and ident.beta_sigma[7] < 1.0
    assert ident.rank < NB and ident.num_flat >= 4


def test_noise_scale_inflates_sigma_and_hpm_shrinks_it():
    model = fitted()
    beta = np.full(NB, 50.0)
    base = model.identifiability(beta).beta_sigma
    model.noise_scale = 4.0
    assert np.allclose(model.identifiability(beta).beta_sigma, 2.0 * base,
                       rtol=1e-6)
    model.noise_scale = 2.0
    model.target_histories_per_macro = 100
    assert np.all(model.identifiability(beta).beta_sigma < base)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def test_save_load_round_trip_and_dispatch(tmp_path):
    model = fitted(noise_scale=3.0, beta_resolution=0.5)
    model.save(str(tmp_path / 'm'))
    again = load_surrogate(str(tmp_path / 'm'))
    assert isinstance(again, AdditiveSurrogate)
    beta = np.linspace(41.0, 59.0, NB)
    for a, b in zip(model.predict_dose(beta), again.predict_dose(beta)):
        assert np.array_equal(a, b)
    assert again.noise_scale == 3.0 and again.beta_resolution == 0.5
    assert np.array_equal(again.voxel_indices, INDICES)
    assert again.beta_names == BETA_NAMES


def test_load_surrogate_still_loads_a_pca_gp(tmp_path):
    rng = np.random.default_rng(0)
    beta = rng.uniform(40, 60, (12, NB))
    DoseSurrogate.fit(beta, np.array([true_dose(b) for b in beta]),
                      k=2).save(str(tmp_path / 'gp'))
    assert isinstance(load_surrogate(str(tmp_path / 'gp')), DoseSurrogate)


# --------------------------------------------------------------------------- #
# The inversion modes run unchanged against it
# --------------------------------------------------------------------------- #


def _model_and_target(tmp_path, beta, hpm=1, seed=0):
    model_dir = str(tmp_path / 'model')
    fitted().save(model_dir)
    target = str(tmp_path / 'target.npz')
    save_field({'dose': {'indices': INDICES,
                         'values': noisy_target(beta, hpm, seed)}}, target)
    return model_dir, target


def test_invert_optimize_mode_with_additive_model(tmp_path):
    truth = np.full(NB, 42.0)
    truth[[2, 5]] = [57.0, 55.0]
    model_dir, target = _model_and_target(tmp_path, truth, hpm=10)
    out = str(tmp_path / 'inv.txt')
    result = invert_optimize({'type': 'invert_optimize', 'model_dir': model_dir,
                              'target': target, 'output_file': out,
                              'target_histories_per_macro': 10})
    assert abs(result.beta[2] - 57.0) < 0.5 and abs(result.beta[5] - 55.0) < 0.5
    ident = result.identifiability
    assert ident.beta_sigma[2] < 0.5 and ident.beta_sigma[0] > 1.0
    text = open(os.path.join(tmp_path, 'identifiability.txt')).read()
    assert 'Cramer-Rao' in text and '\tsigma\t' in text
    assert os.path.isfile(out)


def test_target_hpm_is_rejected_for_a_pca_gp(tmp_path):
    rng = np.random.default_rng(0)
    beta = rng.uniform(40, 60, (12, NB))
    DoseSurrogate.fit(beta, np.array([true_dose(b) for b in beta]), k=2,
                      voxel_indices=INDICES).save(str(tmp_path / 'gp'))
    target = str(tmp_path / 't.npz')
    save_field({'dose': {'indices': INDICES,
                         'values': true_dose(np.full(NB, 50.0))}}, target)
    with pytest.raises(ValueError, match='additive surrogate only'):
        invert_optimize({'type': 'invert_optimize', 'model_dir':
                         str(tmp_path / 'gp'), 'target': target,
                         'target_histories_per_macro': 4})


# --------------------------------------------------------------------------- #
# Bayesian path
# --------------------------------------------------------------------------- #


def test_jax_amplitudes_match_numpy():
    pytest.importorskip('numpyro')
    import jax.numpy as jnp
    from lume_ace3p.surrogate_jax import amplitudes_fn

    model = fitted()
    amplitudes = amplitudes_fn(model)
    rng = np.random.default_rng(0)
    for beta in np.vstack([rng.uniform(39.0, 61.0, (6, NB)), GRID[:NB],
                           GRID[-NB:]]):
        assert np.allclose(np.asarray(amplitudes(jnp.asarray(beta))),
                           model.amplitudes(beta), rtol=1e-10)


def test_posterior_is_tight_on_visible_bins_and_prior_wide_on_dark_ones(tmp_path):
    pytest.importorskip('numpyro')
    truth = np.full(NB, 42.0)
    truth[[2, 5]] = [57.0, 55.0]
    model_dir, target = _model_and_target(tmp_path, truth, hpm=10)
    posterior = invert_bayesian({
        'type': 'invert_bayesian', 'model_dir': model_dir, 'target': target,
        'output_file': str(tmp_path / 'post.txt'), 'num_warmup': 400,
        'num_samples': 600, 'num_chains': 2, 'seed': 0,
        'target_histories_per_macro': 10})
    assert posterior.max_r_hat() < 1.05
    std = posterior.std()
    assert std[2] < 0.5 and std[5] < 0.5
    lo, hi = posterior.credible_interval(0.95)
    assert lo[2] < 57.0 < hi[2] and lo[5] < 55.0 < hi[5]
    # A dark bin is bounded from above only: far wider than a visible bin, and
    # reaching down to the prior's lower bound.
    assert std[0] > 4.0 * max(std[2], std[5]) and lo[0] < 41.0
    assert os.path.isfile(str(tmp_path / 'posterior_summary.txt'))
