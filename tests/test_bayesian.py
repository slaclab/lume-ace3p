"""Phase-4b tests: `invert_bayesian` — a posterior over β for a target dose
(see docs/geant4_surrogate_inversion_plan.md, Phase 4b).

This is the mode that *answers* the non-uniqueness `invert_optimize` reports.
Because the surrogate reaches β only through its ``k`` retained POD coefficients,
the dose constrains at most ``k`` combinations of β; with ``k < D`` the honest
answer is a distribution that is **tight along the constrained directions and
prior-wide along the flat ones**. That split is the headline bar below.

Verification:

* The JAX re-expression of the GP prediction matches scikit-learn's own
  ``predict`` to ~1e-8, and gradients flow through it. This is the load-bearing
  test for the whole differentiable layer — it will catch a future change in
  scikit-learn's internals.
* An unexpected kernel raises a clear error instead of silently mis-reading
  hyperparameters.
* Posterior width recovers the identifiability split.
* The known truth's *identifiable combinations* land inside the 90% credible
  interval (not per-component β — the flat directions are unconstrained by
  construction).
* Convergence diagnostics are reported, and a stuck run is flagged rather than
  passing silently.
* Mode + dispatch + artifacts + fixed-seed reproducibility.

The MCMC tests take tens of seconds each (a converged 4-chain NUTS run), so this
file is the slowest in the suite at ~3 minutes total — but it runs in the default
gate, because it is the only coverage of the Bayesian inversion path and it does
converge reliably. There is deliberately no opt-in "slow" tier: the old one held
botorch tests so expensive they were never run, so it gated nothing.
"""

import os

import numpy as np
import pandas as pd
import pytest

from lume_ace3p import surrogate_data
from lume_ace3p.modes import invert_bayesian, run_mode, train_surrogate
from lume_ace3p.surrogate import DoseSurrogate, PosteriorResult
from tests.test_surrogate import BETA_NAMES, _collect_store, dose_of_beta

pytest.importorskip('numpyro')

TRUTH_BETA = np.array([52., 47., 55., 44., 58., 49., 46., 53.])


def _trained(tmp_path, num_samples=48, noise=0.02):
    store = _collect_store(tmp_path, num_samples=num_samples, noise=noise)
    ts = surrogate_data.load_training_store(store)
    model = DoseSurrogate.fit(ts.beta, ts.dose, variance=0.999, seed=0,
                              beta_names=ts.beta_names,
                              voxel_indices=ts.indices)
    return model, ts, store


def _identifiable(model, target_coeffs):
    """Identifiability at a converged point estimate for the same target."""
    return model.identifiability(
        model.invert(target_coeffs, num_starts=8, seed=0).beta)


# --------------------------------------------------------------------------- #
# The differentiable GP layer — numerical equivalence with scikit-learn.
# --------------------------------------------------------------------------- #


def test_jax_prediction_matches_sklearn(tmp_path):
    """The whole Bayesian path rests on this: the JAX re-expression must reproduce
    sklearn's fitted GP prediction, or the posterior is of a different model."""
    import jax.numpy as jnp
    from lume_ace3p.surrogate_jax import coeff_mean_var_fn

    model, _ts, _store = _trained(tmp_path, num_samples=32)
    predict = coeff_mean_var_fn(model)

    rng = np.random.default_rng(0)
    betas = rng.uniform(model.beta_lo, model.beta_hi, size=(6, len(BETA_NAMES)))
    for beta in betas:
        jax_mean, jax_var = predict(jnp.asarray(beta))
        sk_mean, sk_var = model.predicted_coeffs(beta)
        assert np.allclose(np.asarray(jax_mean), sk_mean[0], atol=1e-8)
        assert np.allclose(np.asarray(jax_var), sk_var[0], atol=1e-8)


def test_gradients_flow_through_the_jax_gp(tmp_path):
    import jax
    import jax.numpy as jnp
    from lume_ace3p.surrogate_jax import coeff_mean_var_fn

    model, _ts, _store = _trained(tmp_path, num_samples=24)
    predict = coeff_mean_var_fn(model)
    grad = jax.grad(lambda b: jnp.sum(predict(b)[0]))(
        jnp.asarray(0.5 * (model.beta_lo + model.beta_hi)))
    grad = np.asarray(grad)
    assert grad.shape == (len(BETA_NAMES),)
    assert np.all(np.isfinite(grad))
    assert np.any(grad != 0.0)          # the map genuinely depends on β


def test_unexpected_kernel_is_rejected(tmp_path):
    """A model fitted with a different kernel must fail loudly — reading its
    hyperparameters positionally would otherwise produce a wrong posterior with
    no error at all."""
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF
    from lume_ace3p.surrogate_jax import gp_params_from_sklearn

    model, ts, _store = _trained(tmp_path, num_samples=16)
    plain = GaussianProcessRegressor(kernel=RBF(length_scale=1.0))
    plain.fit(model._to_unit(ts.beta, model.beta_lo, model.beta_hi),
              ts.dose[:, 0])
    with pytest.raises(ValueError, match='ConstantKernel'):
        gp_params_from_sklearn(plain)


def test_unfitted_gp_is_rejected():
    from sklearn.gaussian_process import GaussianProcessRegressor
    from lume_ace3p.surrogate_jax import gp_params_from_sklearn

    with pytest.raises(ValueError, match='not fitted'):
        gp_params_from_sklearn(GaussianProcessRegressor())


# --------------------------------------------------------------------------- #
# The headline bar — posterior width recovers the identifiability split.
# --------------------------------------------------------------------------- #


def test_posterior_recovers_identifiability_split(tmp_path):
    """Tight along the directions the dose constrains, prior-wide along the flat
    ones. This is the quantitative answer to "how do I rank the non-unique
    solutions?" — you don't; you report which combinations are determined."""
    model, _ts, _store = _trained(tmp_path, num_samples=48)
    target_coeffs = model.project(dose_of_beta(TRUTH_BETA))
    ident = _identifiable(model, target_coeffs)
    assert ident.rank == 3 and ident.num_flat == 5

    posterior = model.sample_posterior(target_coeffs, num_warmup=400,
                                       num_samples=800, seed=0)
    widths = posterior.direction_widths(ident)
    constrained = [r for label, _p, _pr, r in widths
                   if label.startswith('constrained')]
    flat = [r for label, _p, _pr, r in widths if label.startswith('flat')]

    assert len(constrained) == 3 and len(flat) == 5
    # Measured ~0.013-0.078 vs ~1.11-1.25, so these bars have real margin.
    assert max(constrained) < 0.2, constrained
    assert min(flat) > 0.8, flat


def test_truth_identifiable_combinations_are_covered(tmp_path):
    """The truth's identifiable combinations fall in the 90% credible interval.
    Deliberately NOT per-component β: the flat directions are unconstrained by
    construction, so a per-component bar would be testing the prior."""
    model, _ts, _store = _trained(tmp_path, num_samples=48)
    target_coeffs = model.project(dose_of_beta(TRUTH_BETA))
    ident = _identifiable(model, target_coeffs)
    posterior = model.sample_posterior(target_coeffs, num_warmup=600,
                                       num_samples=1200, seed=0)

    for direction in ident.identifiable:
        projected = posterior.samples @ direction
        lo, hi = np.quantile(projected, 0.05), np.quantile(projected, 0.95)
        truth = float(TRUTH_BETA @ direction)
        assert lo <= truth <= hi, (truth, lo, hi)


def test_posterior_converges_and_reports_diagnostics(tmp_path):
    model, _ts, _store = _trained(tmp_path, num_samples=32)
    posterior = model.sample_posterior(model.project(dose_of_beta(TRUTH_BETA)),
                                       num_warmup=400, num_samples=800, seed=0)
    assert 'r_hat' in posterior.diagnostics
    assert 'n_eff' in posterior.diagnostics
    # dense_mass makes this geometry mix; a diagonal mass matrix did not.
    assert posterior.max_r_hat() < 1.05
    assert np.min(posterior.diagnostics['n_eff']) > 50


def test_posterior_is_reproducible_and_within_bounds(tmp_path):
    model, _ts, _store = _trained(tmp_path, num_samples=24)
    coeffs = model.project(dose_of_beta(TRUTH_BETA))
    kwargs = dict(num_warmup=200, num_samples=400, seed=0)
    first = model.sample_posterior(coeffs, **kwargs)
    second = model.sample_posterior(coeffs, **kwargs)
    assert np.allclose(first.samples, second.samples)
    # The uniform prior is a hard box.
    assert np.all(first.samples >= model.beta_lo - 1e-9)
    assert np.all(first.samples <= model.beta_hi + 1e-9)


def test_custom_bounds_narrow_the_posterior(tmp_path):
    """The prior is doing real work: narrowing the box narrows the flat directions
    (which is exactly how a user breaks the degeneracy on physical grounds)."""
    model, _ts, _store = _trained(tmp_path, num_samples=24)
    coeffs = model.project(dose_of_beta(np.full(8, 50.0)))
    tight = [(48.0, 52.0)] * 8
    posterior = model.sample_posterior(coeffs, bounds=tight, num_warmup=200,
                                       num_samples=400, seed=0)
    assert np.all(posterior.samples >= 48.0 - 1e-9)
    assert np.all(posterior.samples <= 52.0 + 1e-9)


def test_sample_posterior_rejects_wrong_coefficient_count(tmp_path):
    model, _ts, _store = _trained(tmp_path, num_samples=16)
    with pytest.raises(ValueError, match='coefficients'):
        model.sample_posterior(np.zeros(model.num_components + 2))


# --------------------------------------------------------------------------- #
# PosteriorResult accessors (cheap — no sampling).
# --------------------------------------------------------------------------- #


def test_posterior_result_accessors():
    rng = np.random.default_rng(0)
    samples = rng.normal(50.0, 1.0, size=(500, 3))
    result = PosteriorResult(
        samples=samples, beta_names=['b0', 'b1', 'b2'],
        bounds=[(40.0, 60.0)] * 3, dose_sigma=0.1,
        diagnostics={'r_hat': np.array([1.0, 1.01, 1.0]),
                     'n_eff': np.array([400.0, 380.0, 420.0])},
        target_coeffs=np.zeros(2))
    assert len(result) == 500
    assert result.mean().shape == (3,)
    lo, hi = result.credible_interval(0.9)
    assert np.all(lo < hi)
    # A 20-wide uniform box has std 20/sqrt(12).
    assert np.allclose(result.prior_std, 20.0 / np.sqrt(12.0))
    assert result.max_r_hat() == pytest.approx(1.01)


def test_max_r_hat_is_nan_without_diagnostics():
    result = PosteriorResult(samples=np.zeros((10, 2)), beta_names=['a', 'b'],
                             bounds=[(0.0, 1.0)] * 2, dose_sigma=1.0,
                             diagnostics={}, target_coeffs=np.zeros(1))
    assert np.isnan(result.max_r_hat())


# --------------------------------------------------------------------------- #
# invert_bayesian mode
# --------------------------------------------------------------------------- #


def test_mode_writes_samples_and_summary(tmp_path):
    store = _collect_store(tmp_path, num_samples=32)
    train_surrogate({'type': 'train_surrogate', 'store': store,
                     'variance': 0.999, 'seed': 0})
    posterior = invert_bayesian({
        'type': 'invert_bayesian', 'store': store,
        'target': os.path.join(store, 'sample_00000', 'field.npz'),
        'num_warmup': 300, 'num_samples': 600, 'seed': 0})

    assert isinstance(posterior, PosteriorResult)
    samples_file = os.path.join(store, 'posterior_samples.txt')
    summary_file = os.path.join(store, 'posterior_summary.txt')
    assert os.path.isfile(samples_file) and os.path.isfile(summary_file)

    draws = pd.read_csv(samples_file, sep='\t')
    assert list(draws.columns) == BETA_NAMES
    assert len(draws) == len(posterior)

    text = open(summary_file).read()
    # The summary must say plainly that prior-wide flat directions are correct.
    assert 'CORRECT result' in text
    assert 'constrained_0' in text and 'flat_0' in text
    table = pd.read_csv(summary_file, sep='\t', comment='#')
    assert list(table['beta']) == BETA_NAMES
    for column in ('mean', 'median', 'std', 'ci5', 'ci95', 'prior_std',
                   'r_hat', 'n_eff'):
        assert column in table.columns


def test_run_mode_dispatches_invert_bayesian(tmp_path):
    store = _collect_store(tmp_path, num_samples=20)
    train_surrogate({'type': 'train_surrogate', 'store': store, 'seed': 0})
    posterior = run_mode({
        'type': 'invert_bayesian', 'store': store,
        'target': os.path.join(store, 'sample_00000', 'field.npz'),
        'num_warmup': 150, 'num_samples': 300, 'seed': 0}, workflow=None)
    assert isinstance(posterior, PosteriorResult)


def test_mode_runs_from_minimal_yaml_without_workflow(tmp_path):
    """invert_bayesian is store-consuming: its YAML needs no 'workflow:' block."""
    import textwrap
    from lume_ace3p.inputs import load_yaml
    from lume_ace3p.modes import is_store_consuming
    from lume_ace3p.run_lume_ace3p import _run_declarative

    store = _collect_store(tmp_path, num_samples=20)
    train_surrogate({'type': 'train_surrogate', 'store': store, 'seed': 0})
    path = tmp_path / 'bayes.yaml'
    path.write_text(textwrap.dedent(f"""\
        mode :
          type : invert_bayesian
          store : '{store}'
          target : '{os.path.join(store, 'sample_00000', 'field.npz')}'
          num_warmup : 150
          num_samples : 300
          seed : 0
    """))
    data = load_yaml(str(path))
    assert data.get('workflow') is None
    assert is_store_consuming(data.get('mode'))
    assert isinstance(_run_declarative(data), PosteriorResult)


def test_mode_requires_model_and_target(tmp_path):
    with pytest.raises(ValueError, match='model_dir'):
        invert_bayesian({'type': 'invert_bayesian', 'target': 'x.npz'})
    store = _collect_store(tmp_path, num_samples=16)
    with pytest.raises(ValueError, match='train_surrogate first'):
        invert_bayesian({'type': 'invert_bayesian', 'store': store,
                         'target': 'x.npz'})


def test_shipped_bayesian_example_is_store_consuming():
    from lume_ace3p.inputs import load_yaml
    from lume_ace3p.modes import is_store_consuming

    path = os.path.join(os.path.dirname(__file__), '..', 'examples',
                        'geant4_beta_surrogate',
                        'geant4_beta_surrogate_invert_bayesian.yaml')
    data = load_yaml(path)
    assert data.get('workflow') is None
    assert is_store_consuming(data.get('mode'))


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
