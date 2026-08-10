"""Differentiable (JAX) re-expression of the PCA-GP surrogate's prediction.

Bayesian inversion (``invert_bayesian``, Phase 4b) samples a posterior over β with
NUTS, which needs **gradients** of the GP predictive mean/variance w.r.t. β. The
surrogate's GPs are fitted scikit-learn objects, which are not differentiable, so
this module re-expresses only the *prediction* in JAX from the already-fitted
attributes. Fitting stays entirely in scikit-learn — nothing here re-trains
anything.

That is possible because a fitted GP's predictive mean and variance are closed
form. For the kernel the surrogate uses (``ConstantKernel * RBF(ARD) +
WhiteKernel``, see :func:`lume_ace3p.surrogate._build_gp`), with training inputs
``X``, dual coefficients ``α`` and the Cholesky factor ``L`` of the training
kernel matrix:

    k*(u)  = const · exp(−½ Σ_d ((u_d − X_d)/ℓ_d)²)
    mean   = (k*·α)·y_std + y_mean
    var    = (const + noise − ‖L⁻¹k*‖²)·y_std²

which is elementwise arithmetic plus one triangular solve — trivially
differentiable. This is verified against ``sklearn``'s own ``predict`` to ~1e-9 in
``tests/test_bayesian.py``; that test is the load-bearing guard on this
re-expression and will catch a future change in scikit-learn's internals.

All JAX lives in this module so :mod:`lume_ace3p.surrogate` stays numpy +
scikit-learn only, and the JAX import cost is paid solely by the Bayesian path.
"""

import os

import numpy as np


_X64_ENABLED = False


def _require_jax():
    """Import JAX, or raise a clear install hint."""
    try:
        import jax  # noqa: F401
    except ImportError as exc:      # pragma: no cover - environment dependent
        raise ImportError(
            "Bayesian inversion needs JAX (installed with numpyro). Install the "
            "package dependencies (`pip install -e .`) or `pip install numpyro`. "
            f"Original error: {exc}") from exc


def enable_x64(num_host_devices=1):
    """Enable JAX 64-bit mode, default to CPU, and size the chain device pool.

    Both JAX settings matter here. In JAX's default float32 the GP re-expression
    agrees with scikit-learn only to ~1e-3, which would silently degrade the
    posterior; float64 restores the ~1e-9 agreement. And this is a small
    CPU-sized problem (a few hundred training points, k ≲ 20 GPs), so we do not
    quietly commandeer a GPU the user may want for something else — an explicit
    ``JAX_PLATFORMS`` in the environment still wins.

    ``num_host_devices`` requests that many CPU devices so numpyro can run that
    many chains **in parallel** instead of sequentially (multiple chains are
    essential here — see :meth:`DoseSurrogate.sample_posterior`). This must be set
    before JAX initializes its backend, so it only takes effect on the first call;
    a later call asking for more devices cannot grow the pool, and chains then fall
    back to running sequentially (correct, just slower)."""
    global _X64_ENABLED
    if _X64_ENABLED:
        return
    os.environ.setdefault('JAX_PLATFORMS', 'cpu')
    requested = max(1, int(num_host_devices))
    if requested > 1:
        import numpyro
        numpyro.set_host_device_count(requested)
    import jax
    jax.config.update('jax_enable_x64', True)
    _X64_ENABLED = True


def gp_params_from_sklearn(gp):
    """Extract the closed-form predictive parameters of one fitted sklearn GP.

    Returns a dict of plain numpy arrays/floats: ``const``, ``ls`` (per-dimension
    RBF length scales), ``noise``, ``X``, ``alpha``, ``L``, ``y_mean``, ``y_std``.

    The kernel structure is **validated** rather than assumed: this reads a
    ``ConstantKernel * RBF + WhiteKernel`` fitted kernel by position, so a model
    trained with a different kernel would otherwise be silently mis-read (wrong
    hyperparameters → a wrong posterior with no error). If the structure does not
    match, raise with what was found."""
    from sklearn.gaussian_process.kernels import (
        RBF, ConstantKernel, WhiteKernel)

    kernel = getattr(gp, 'kernel_', None)
    if kernel is None:
        raise ValueError(
            "the surrogate's GPs are not fitted (no 'kernel_'); train the "
            "surrogate before Bayesian inversion.")

    product = getattr(kernel, 'k1', None)
    white = getattr(kernel, 'k2', None)
    constant = getattr(product, 'k1', None)
    rbf = getattr(product, 'k2', None)
    if not (isinstance(white, WhiteKernel)
            and isinstance(constant, ConstantKernel)
            and isinstance(rbf, RBF)):
        raise ValueError(
            "the differentiable GP re-expression expects the surrogate's "
            "'ConstantKernel * RBF + WhiteKernel' kernel (see "
            "lume_ace3p.surrogate._build_gp), but this model was fitted with "
            f"'{kernel}'. Bayesian inversion cannot read its hyperparameters "
            "safely — refit the surrogate with the standard kernel, or extend "
            "gp_params_from_sklearn to handle this one.")

    return {
        'const': float(constant.constant_value),
        'ls': np.atleast_1d(np.asarray(rbf.length_scale, dtype=float)),
        'noise': float(white.noise_level),
        'X': np.asarray(gp.X_train_, dtype=float),
        'alpha': np.asarray(gp.alpha_, dtype=float).ravel(),
        'L': np.asarray(gp.L_, dtype=float),
        # normalize_y=True stores these; a 0-d array for a single target.
        'y_mean': float(np.ravel(gp._y_train_mean)[0]),
        'y_std': float(np.ravel(gp._y_train_std)[0]),
    }


def coeff_mean_var_fn(surrogate):
    """Build a differentiable ``β (D,) -> (mean (k,), var (k,))`` function.

    Mirrors :meth:`lume_ace3p.surrogate.DoseSurrogate.predicted_coeffs` for a
    single β — same unit-box input rescaling, same per-coefficient GPs, same
    output space (the model's fit space) — but in JAX, so ``jax.grad`` works
    through it. The returned callable is jitted."""
    _require_jax()
    enable_x64()
    import jax
    import jax.numpy as jnp
    from jax.scipy.linalg import solve_triangular

    params = [gp_params_from_sklearn(gp) for gp in surrogate.gps]
    # Move the fitted arrays onto device once, outside the traced function.
    packed = [{
        'const': p['const'], 'noise': p['noise'],
        'ls': jnp.asarray(p['ls']), 'X': jnp.asarray(p['X']),
        'alpha': jnp.asarray(p['alpha']), 'L': jnp.asarray(p['L']),
        'y_mean': p['y_mean'], 'y_std': p['y_std'],
    } for p in params]

    lo = jnp.asarray(surrogate.beta_lo, dtype=float)
    hi = jnp.asarray(surrogate.beta_hi, dtype=float)
    span = jnp.where(hi - lo == 0.0, 1.0, hi - lo)

    def coeff_mean_var(beta):
        unit = (beta - lo) / span
        means = []
        variances = []
        for p in packed:
            scaled = (unit[None, :] - p['X']) / p['ls']
            k_star = p['const'] * jnp.exp(-0.5 * jnp.sum(scaled ** 2, axis=-1))
            means.append(jnp.dot(k_star, p['alpha']) * p['y_std'] + p['y_mean'])
            # var = (k(x,x) - ||L^-1 k*||^2) * y_std^2, with k(x,x) = const+noise.
            solved = solve_triangular(p['L'], k_star, lower=True)
            variances.append((p['const'] + p['noise']
                              - jnp.sum(solved ** 2)) * p['y_std'] ** 2)
        return jnp.stack(means), jnp.stack(variances)

    return jax.jit(coeff_mean_var)


def sample_posterior_nuts(surrogate, target_coeffs, *, bounds, dose_sigma,
                          num_warmup, num_samples, num_chains, seed):
    """Run NUTS over β and return ``(samples, diagnostics)``.

    The model is deliberately simple and stated explicitly because both parts do
    real work:

    * **Prior** ``β ~ Uniform(beta_lo, beta_hi)`` — the surrogate's *training box*.
      This is not a neutral default: the inverse problem is rank-deficient (the
      dose constrains at most ``k`` combinations of β), so along the flat
      directions the posterior *is* the prior. The bounds are therefore part of
      the answer and are recorded with the result.
    * **Likelihood** ``c_target ~ Normal(μ_GP(β), sqrt(Var_GP(β) + dose_sigma²))``
      in the model's retained-coefficient (fit) space — the GP's own predictive
      variance plus an assumed target-noise term.

    Returns the ``(num_samples·num_chains, D)`` draws and a diagnostics dict
    (per-β ``r_hat`` / ``n_eff``)."""
    _require_jax()
    # Size the device pool for the requested chains before the backend starts, so
    # they run in parallel rather than one after another.
    enable_x64(num_host_devices=num_chains)
    import jax
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist
    from numpyro.diagnostics import summary
    from numpyro.infer import MCMC, NUTS

    coeff_mean_var = coeff_mean_var_fn(surrogate)
    lo = jnp.asarray([b[0] for b in bounds], dtype=float)
    hi = jnp.asarray([b[1] for b in bounds], dtype=float)
    target = jnp.asarray(np.asarray(target_coeffs, dtype=float).ravel())
    sigma = float(dose_sigma)

    def model():
        beta = numpyro.sample('beta', dist.Uniform(lo, hi))
        mean, var = coeff_mean_var(beta)
        numpyro.sample('obs', dist.Normal(mean, jnp.sqrt(var + sigma ** 2)),
                       obs=target)

    # dense_mass=True is essential, not a tuning nicety. The degenerate set is a
    # curved manifold whose flat directions are *correlated* combinations of β; a
    # diagonal mass matrix cannot represent that geometry, and NUTS then fails to
    # move along the manifold. Measured on the synthetic fixture at
    # 1000 warmup / 2000 draws x 4 chains: diagonal gave r_hat 1.12 and n_eff 19
    # (and more warmup did not help — r_hat stayed 1.1-1.4), while dense gave
    # r_hat 1.002 and n_eff ~2060, and ran faster.
    kernel = NUTS(model, dense_mass=True)
    mcmc = MCMC(kernel, num_warmup=int(num_warmup),
                num_samples=int(num_samples), num_chains=int(num_chains),
                progress_bar=False)
    mcmc.run(jax.random.PRNGKey(int(seed)))

    # Grouped draws (chains kept separate) give meaningful r_hat.
    grouped = mcmc.get_samples(group_by_chain=True)['beta']
    stats = summary({'beta': grouped}, prob=0.9)['beta']
    samples = np.asarray(mcmc.get_samples()['beta'], dtype=float)
    diagnostics = {
        'r_hat': np.asarray(stats['r_hat'], dtype=float),
        'n_eff': np.asarray(stats['n_eff'], dtype=float),
    }
    return samples, diagnostics
