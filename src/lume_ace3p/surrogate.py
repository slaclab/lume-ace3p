"""PCA-GP forward dose surrogate (Phase 3 of the surrogate + inversion project).

This module builds the *cheap forward surrogate* the project is centred on: a
reduced-basis map ``β ∈ ℝ^D → dose ∈ ℝ^M`` learned from the ``(β, dose_grid)``
pairs collected by the Phase-2 ``collect_training_data`` mode (see
:func:`lume_ace3p.modes.collect_training_data` and
:func:`lume_ace3p.surrogate_data.load_training_store`).

The construction is a classic **PCA-GP** (a.k.a. reduced-basis / POD-GP)
surrogate:

1. Stack the training dose grids into ``Y (N, M)``, subtract the sample mean and
   take an economy SVD. Dose fields are smooth and low-rank, so the leading ``k``
   left/right singular vectors capture ~all of the coherent β-driven signal while
   the discarded tail absorbs the Monte-Carlo shot noise — PCA is a *denoiser*
   here, not just a compressor (constraint #3 rationale).
2. Project the centred grids onto the retained basis ``Φ (k, M)`` to get ``k``
   scalar coefficients per sample, ``C (N, k)``.
3. Fit **one independent Gaussian Process per coefficient**, ``β → c_i``. Each GP
   carries a genuine fitted ``WhiteKernel`` noise term — Geant4 dose is
   Monte-Carlo noisy, so we must NOT use a low-noise / interpolating prior
   (**correctness constraint #2**). The predicted coefficient variance flows
   through to a non-zero, calibrated dose-grid variance.

The single object :class:`DoseSurrogate` exposes:

* ``predict_dose(β) -> (mean_grid, var_grid)`` — reconstruct
  ``mean + Σ_i c_i(β)·φ_i`` with propagated variance,
* ``project(dose_grid) -> coeff vector`` — the *single* coefficient-space seam
  the inversion phase (Phase 4) talks to, so an alternate "profile summaries"
  input mode can later supply a different ``project`` without touching the GPs,
* ``predicted_coeffs(β) -> (mean, var)`` — raw per-coefficient GP outputs, so the
  round-trip ``project(predict_dose(β)) ≈ predicted_coeffs(β)`` holds by
  construction and inversion can score directly in coefficient space,
* ``save(dir)`` / ``load(dir)`` — round-trip to identical predictions.

The class is pure numpy + scikit-learn with **no workflow coupling** — it takes
plain arrays, so it is trained/tested locally against a synthetic analytic
β→dose fixture (synthetic-first strategy) and later against a real Geant4 store
without any code change.
"""

import json
import os

import numpy as np


# Canonical filenames inside a saved surrogate directory.
BASIS_FILENAME = 'basis.npz'          # PCA mean/basis/normalization (pickle-free)
GPS_FILENAME = 'gps.joblib'           # fitted sklearn GPs (trusted local artifact)
PROVENANCE_FILENAME = 'surrogate.json'  # k / variance target / kept energy / kernels


# Supported dose transforms. The whole PCA-GP is fit in the transformed space;
# `'log10'` addresses the Fowler-Nordheim exponential-in-β dynamic range (dose
# spans ~9 orders of magnitude across voxels, and the per-sample total varies far
# less in log than in linear), where the linear fit barely captures the dose
# *shape*. See :meth:`DoseSurrogate.fit`.
DOSE_TRANSFORMS = ('linear', 'log10')


def _apply_transform(dose, dose_transform, floor):
    """Map a raw dose array into the fit space (``'linear'`` is the identity;
    ``'log10'`` returns ``log10(dose + floor)``). ``floor`` is a strictly positive
    offset that keeps zero voxels finite; it is ignored for ``'linear'``."""
    if dose_transform == 'linear':
        return np.asarray(dose, dtype=float)
    if dose_transform == 'log10':
        return np.log10(np.asarray(dose, dtype=float) + float(floor))
    raise ValueError(
        f"unknown dose_transform '{dose_transform}'; use one of {DOSE_TRANSFORMS}.")


def _invert_transform(fit_values, dose_transform, floor):
    """Map fit-space values back to linear dose. For ``'log10'`` this is
    ``10**fit - floor``.

    WARNING: for a log fit this amplifies error in the ~9-order tail — a small
    log-space error can become an enormous linear-space one (measured >100x
    relative-L2 on the real store). The trustworthy accuracy metric for a log model
    is therefore computed *in the fit space*, not after inverting to linear."""
    if dose_transform == 'linear':
        return np.asarray(fit_values, dtype=float)
    if dose_transform == 'log10':
        return np.power(10.0, np.asarray(fit_values, dtype=float)) - float(floor)
    raise ValueError(
        f"unknown dose_transform '{dose_transform}'; use one of {DOSE_TRANSFORMS}.")


def _build_gp(input_dim, seed):
    """Construct one per-coefficient :class:`GaussianProcessRegressor`.

    Kernel = ``ConstantKernel * RBF(ARD) + WhiteKernel``. The ``WhiteKernel`` is
    the genuine, *fitted* observation-noise term correctness constraint #2
    requires for Monte-Carlo-noisy dose — do NOT drop it or clamp it to ~0, that
    would reinstate the interpolating prior the constraint forbids. ARD (one
    length scale per β dimension) lets irrelevant bins grow long length scales.
    ``normalize_y`` handles the per-coefficient scale (leading modes are far
    larger than trailing ones)."""
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import (
        RBF, ConstantKernel, WhiteKernel)

    kernel = (ConstantKernel(1.0, (1e-3, 1e3))
              * RBF(length_scale=[1.0] * input_dim,
                    length_scale_bounds=(1e-2, 1e2))
              + WhiteKernel(noise_level=1.0, noise_level_bounds=(1e-8, 1e3)))
    return GaussianProcessRegressor(
        kernel=kernel, normalize_y=True, n_restarts_optimizer=3,
        random_state=seed)


def _choose_k(singular_values, variance, k):
    """Pick the number of retained modes.

    Explicit ``k`` wins; otherwise take the smallest ``k`` whose cumulative
    energy (squared singular values) reaches ``variance`` of the total. Always
    keeps at least one mode."""
    total = int(singular_values.shape[0])
    if k is not None:
        return max(1, min(int(k), total))
    energy = singular_values ** 2
    cumulative = np.cumsum(energy) / np.sum(energy)
    # +1: searchsorted gives the first index reaching the threshold.
    return int(min(total, np.searchsorted(cumulative, variance) + 1))


class DoseSurrogate:
    """A trained PCA-GP forward dose surrogate.

    Construct with :meth:`fit` (or :meth:`load`). Attributes:

    * ``mean`` — ``(M,)`` sample-mean dose grid.
    * ``basis`` — ``(k, M)`` retained POD basis ``Φ`` (orthonormal rows).
    * ``singular_values`` — ``(k,)`` retained singular values.
    * ``gps`` — list of ``k`` fitted per-coefficient GPs.
    * ``beta_lo`` / ``beta_hi`` — ``(D,)`` training-β bounds used to map inputs
      into a unit cube before the GPs see them.
    * ``beta_names`` — ordered β column names (provenance).
    * ``kept_energy`` — fraction of total variance retained by the ``k`` modes.
    * ``dose_transform`` — ``'linear'`` or ``'log10'``; the space the PCA-GP was
      fit in (``mean`` / ``basis`` / ``gps`` all live in this space).
    * ``floor`` — strictly positive offset used by the ``'log10'`` transform to
      keep zero voxels finite (``0.0`` for a linear fit).
    * ``voxel_indices`` — ``(M, 3)`` voxel ``(ix, iy, iz)`` order the basis
      columns correspond to, or ``None`` for a model saved before this was
      recorded. Inversion needs it to reorder a target dose onto the basis
      (correctness constraint #3); see
      :func:`lume_ace3p.surrogate_data.align_to_indices`.
    """

    def __init__(self, mean, basis, singular_values, gps, beta_lo, beta_hi,
                 beta_names, kept_energy, variance_target=None,
                 dose_transform='linear', floor=0.0, voxel_indices=None):
        self.mean = np.asarray(mean, dtype=float)
        self.basis = np.asarray(basis, dtype=float)
        self.singular_values = np.asarray(singular_values, dtype=float)
        self.gps = list(gps)
        self.beta_lo = np.asarray(beta_lo, dtype=float)
        self.beta_hi = np.asarray(beta_hi, dtype=float)
        self.beta_names = list(beta_names)
        self.kept_energy = float(kept_energy)
        self.variance_target = variance_target
        if dose_transform not in DOSE_TRANSFORMS:
            raise ValueError(
                f"unknown dose_transform '{dose_transform}'; use one of "
                f"{DOSE_TRANSFORMS}.")
        self.dose_transform = dose_transform
        self.floor = float(floor)
        self.voxel_indices = (None if voxel_indices is None
                              else np.asarray(voxel_indices, dtype=int))

    # ---- construction -------------------------------------------------- #

    @classmethod
    def fit(cls, beta, dose, *, variance=0.99, k=None, seed=0,
            beta_names=None, dose_transform='linear', floor=None, n_jobs=1,
            voxel_indices=None):
        """Fit the surrogate from aligned ``β (N, D)`` and ``dose (N, M)``.

        ``variance`` is the cumulative-energy target for choosing the number of
        retained POD modes (ignored when an explicit ``k`` is given). ``seed``
        makes each GP's restart search reproducible. ``beta_names`` is recorded
        for provenance / inversion-time column alignment.

        ``dose_transform`` selects the space the whole PCA-GP is fit in:
        ``'linear'`` (default, fit raw dose) or ``'log10'`` (fit
        ``log10(dose + floor)``). The ``'log10'`` transform addresses the
        Fowler-Nordheim exponential-in-β dynamic range — dose spans ~9 orders of
        magnitude across voxels, so a linear fit is dominated by the peak voxels
        and barely learns the dose *shape*. ``floor`` is the strictly positive
        offset that keeps zero voxels finite; it defaults to the smallest positive
        dose value in the training set (ignored for ``'linear'``).

        ``n_jobs`` parallelizes the per-coefficient GP fits over CPU cores via
        joblib (``1`` = serial, the default; ``-1`` = all cores). The GPs are
        independent (one per POD coefficient), so this is an embarrassingly
        parallel loop and does **not** change the result — same ``seed`` gives
        bit-identical GPs regardless of ``n_jobs``. Each GP's own solver may also
        use BLAS threads, so on a busy machine keep ``n_jobs`` at or below the
        core count to avoid oversubscription.

        ``voxel_indices`` is the ``(M, 3)`` voxel order the ``dose`` columns are
        in (a training store's ``indices``). Recording it lets the inversion phase
        reorder an arbitrary target dose onto this basis; without it, inversion
        must be told the order some other way (constraint #3 — it never guesses)."""
        beta = np.asarray(beta, dtype=float)
        dose = np.asarray(dose, dtype=float)
        if beta.ndim != 2 or dose.ndim != 2:
            raise ValueError("fit expects 2-D beta (N,D) and dose (N,M).")
        if beta.shape[0] != dose.shape[0]:
            raise ValueError(
                f"beta has {beta.shape[0]} rows but dose has {dose.shape[0]}; "
                "they must be aligned sample-for-sample.")
        n_samples = beta.shape[0]
        if n_samples < 2:
            raise ValueError(
                "need at least 2 training samples to fit the surrogate.")

        if dose_transform not in DOSE_TRANSFORMS:
            raise ValueError(
                f"unknown dose_transform '{dose_transform}'; use one of "
                f"{DOSE_TRANSFORMS}.")
        if dose_transform == 'log10':
            positive = dose[dose > 0.0]
            if floor is None:
                # Smallest positive dose is the natural noise floor; fall back to
                # 1.0 only if every value is zero (degenerate, no signal).
                floor = float(positive.min()) if positive.size else 1.0
            floor = float(floor)
            if floor <= 0.0:
                raise ValueError("log10 dose_transform needs a positive floor.")
        else:
            floor = 0.0
        # Everything below (mean, SVD, GPs) is computed in the fit space.
        dose = _apply_transform(dose, dose_transform, floor)

        mean = dose.mean(axis=0)
        centered = dose - mean
        # Economy SVD: centered = U diag(s) Vt, Vt rows are the POD modes.
        _u, s, vt = np.linalg.svd(centered, full_matrices=False)
        n_modes = _choose_k(s, variance, k)
        basis = vt[:n_modes]                    # (k, M), orthonormal rows
        singular_values = s[:n_modes]
        kept_energy = (float(np.sum(s[:n_modes] ** 2) / np.sum(s ** 2))
                       if np.any(s) else 1.0)

        # Coefficients C = centered @ Φ^T  (k independent scalar targets).
        coeffs = centered @ basis.T             # (N, k)

        beta_lo = beta.min(axis=0)
        beta_hi = beta.max(axis=0)
        beta_unit = cls._to_unit(beta, beta_lo, beta_hi)

        # One independent GP per retained coefficient. The fits share no state,
        # so joblib parallelizes them cleanly; a fixed seed keeps each GP's
        # restart search reproducible regardless of n_jobs (result-invariant).
        def _fit_one(j):
            gp = _build_gp(beta.shape[1], seed)
            gp.fit(beta_unit, coeffs[:, j])
            return gp

        if n_jobs == 1:
            gps = [_fit_one(j) for j in range(n_modes)]
        else:
            from joblib import Parallel, delayed
            gps = Parallel(n_jobs=n_jobs)(
                delayed(_fit_one)(j) for j in range(n_modes))

        if beta_names is None:
            beta_names = [f'beta{i}' for i in range(beta.shape[1])]
        return cls(mean, basis, singular_values, gps, beta_lo, beta_hi,
                   beta_names, kept_energy, variance_target=variance,
                   dose_transform=dose_transform, floor=floor,
                   voxel_indices=voxel_indices)

    # ---- normalization helpers ---------------------------------------- #

    @staticmethod
    def _to_unit(beta, lo, hi):
        """Map β into the training [lo, hi] hypercube as [0, 1]^D. A degenerate
        (lo == hi) dimension maps to 0 rather than dividing by zero."""
        beta = np.atleast_2d(np.asarray(beta, dtype=float))
        span = np.asarray(hi, dtype=float) - np.asarray(lo, dtype=float)
        span = np.where(span == 0.0, 1.0, span)
        return (beta - lo) / span

    # ---- prediction ---------------------------------------------------- #

    @property
    def num_components(self):
        return int(self.basis.shape[0])

    def predicted_coeffs(self, beta):
        """Per-coefficient GP predictions for ``β``.

        Returns ``(mean (…, k), var (…, k))``. ``β`` may be a single vector
        ``(D,)`` or a batch ``(B, D)``; the leading batch dim is preserved. The
        variance is the GP predictive variance per coefficient — strictly > 0
        because of the fitted ``WhiteKernel`` (constraint #2)."""
        beta = np.atleast_2d(np.asarray(beta, dtype=float))
        beta_unit = self._to_unit(beta, self.beta_lo, self.beta_hi)
        means = np.empty((beta.shape[0], self.num_components))
        variances = np.empty_like(means)
        for j, gp in enumerate(self.gps):
            m, std = gp.predict(beta_unit, return_std=True)
            means[:, j] = m
            variances[:, j] = std ** 2
        return means, variances

    def predict_dose(self, beta, space='fit'):
        """Predict the full dose grid for ``β``.

        Reconstructs from the coefficient GPs in the *fit* space:
        ``mean_grid = mean + Σ_i c_i(β)·φ_i`` and, treating the modes as
        independent, ``var_grid = Σ_i Var[c_i(β)]·φ_i²`` (strictly positive —
        fitted noise term, constraint #2). A single-β input returns 1-D grids; a
        batch returns ``(B, M)``.

        ``space`` selects the output space:

        * ``'fit'`` (default) — the space the model was trained in. For a linear
          model this is dose; for a ``'log10'`` model this is ``log10(dose+floor)``.
          Returns ``(mean_grid, var_grid)``. This is the space accuracy should be
          judged in and the space :meth:`project` / :meth:`predicted_coeffs` speak.
        * ``'linear'`` — always raw dose. For a linear model identical to ``'fit'``.
          For a ``'log10'`` model the mean is mapped back with ``10**mean - floor``;
          **this amplifies tail error dramatically** (a small log error → a huge
          linear one), so only the mean is returned and no variance (a delta-method
          variance would blow up and mislead). Returns just ``mean_grid``.
        """
        beta_arr = np.atleast_2d(np.asarray(beta, dtype=float))
        single = np.asarray(beta).ndim == 1
        cmean, cvar = self.predicted_coeffs(beta_arr)
        mean_grid = self.mean + cmean @ self.basis          # (B, M), fit space
        var_grid = cvar @ (self.basis ** 2)                 # (B, M), fit space
        if space == 'linear':
            linear_mean = _invert_transform(mean_grid, self.dose_transform,
                                             self.floor)
            return linear_mean[0] if single else linear_mean
        if space != 'fit':
            raise ValueError(f"unknown space '{space}'; use 'fit' or 'linear'.")
        if single:
            return mean_grid[0], var_grid[0]
        return mean_grid, var_grid

    def project(self, dose_grid, space='linear'):
        """Project a dose grid into retained-basis coefficient space.

        ``coeffs = (transform(dose) - mean) @ Φ^T``. This is the single
        coefficient-space seam the inversion phase talks to (a target dose is
        projected here, then matched against ``predicted_coeffs``). The input is
        mapped into the model's fit space first, so for a ``'log10'`` model a raw
        linear dose is log-transformed before centering — keeping the round-trip
        ``project(predict_dose(β, 'fit')) ≈ predicted_coeffs(β)`` exact.

        ``space`` describes the space of ``dose_grid``: ``'linear'`` (default, raw
        dose — it is transformed here) or ``'fit'`` (already in the model's fit
        space — projected as-is). Accepts a single grid ``(M,)`` or a batch
        ``(B, M)``; returns ``(k,)`` or ``(B, k)`` accordingly."""
        dose_grid = np.asarray(dose_grid, dtype=float)
        single = dose_grid.ndim == 1
        grids = np.atleast_2d(dose_grid)
        if space == 'linear':
            grids = _apply_transform(grids, self.dose_transform, self.floor)
        elif space != 'fit':
            raise ValueError(f"unknown space '{space}'; use 'linear' or 'fit'.")
        coeffs = (grids - self.mean) @ self.basis.T
        return coeffs[0] if single else coeffs

    # ---- inversion (Phase 4) ------------------------------------------- #

    def coeff_misfit(self, beta, target_coeffs):
        """Coefficient-space misfit ``‖c_GP(β) − c_target‖²``.

        This is the inversion objective: the surrogate's *coefficient space* is
        the single seam inversion talks to, so the misfit is measured on the
        retained POD coefficients rather than on raw voxels. Because the
        coefficients live in the model's **fit space**, a ``'log10'`` model is
        automatically scored in log space — which is the meaningful space for
        Fowler-Nordheim dose (a linear-space residual is dominated by a handful of
        peak voxels and barely sees the profile shape).

        ``beta`` may be a single ``(D,)`` vector or a batch ``(B, D)``; returns a
        float or ``(B,)`` array."""
        target = np.asarray(target_coeffs, dtype=float).ravel()
        if target.shape[0] != self.num_components:
            raise ValueError(
                f"target has {target.shape[0]} coefficients but the model "
                f"retains {self.num_components}; project the target with this "
                "model's project().")
        single = np.asarray(beta).ndim == 1
        cmean, _ = self.predicted_coeffs(np.atleast_2d(beta))
        residual = cmean - target
        misfit = np.sum(residual ** 2, axis=1)
        return float(misfit[0]) if single else misfit

    def identifiability(self, beta, *, step=1e-5):
        """Analyse which β directions the dose actually constrains at ``beta``.

        This is the honest answer to "how do I rank the non-unique solutions?":
        usually you cannot, because the degeneracy is not a set of competing
        hypotheses but a *continuous surface* of exactly-equivalent β. This method
        measures the dimension of that surface.

        The surrogate maps β through only ``k`` retained POD coefficients, so the
        dose can constrain **at most ``k`` combinations of β**. When ``k < D``
        the inverse problem is rank-deficient *by construction* and ``D − rank``
        directions in β are invisible to the dose — moving along them changes the
        predicted dose not at all.

        Method: finite-difference the GP coefficient mean w.r.t. β in **unit-box
        coordinates** (``u = (β − beta_lo)/span``), so the singular values are
        dimensionless and comparable across β with different physical ranges, then
        take the SVD of the resulting Jacobian ``J (k, D)``. The right singular
        vectors split into the combinations the dose constrains (``identifiable``,
        most-constrained first) and those it cannot see (``null_space``).

        Returns an :class:`Identifiability`. Cost is ``2·D`` GP evaluations —
        microseconds."""
        beta = np.asarray(beta, dtype=float).ravel()
        span = self.beta_hi - self.beta_lo
        span = np.where(span == 0.0, 1.0, span)
        dim = beta.shape[0]

        def coeff_mean_at_unit(unit):
            return self.predicted_coeffs(self.beta_lo + unit * span)[0][0]

        unit0 = (beta - self.beta_lo) / span
        jacobian = np.empty((self.num_components, dim))
        for q in range(dim):
            up, down = unit0.copy(), unit0.copy()
            up[q] += step
            down[q] -= step
            jacobian[:, q] = (coeff_mean_at_unit(up)
                              - coeff_mean_at_unit(down)) / (2.0 * step)

        _u, singular_values, vt = np.linalg.svd(jacobian, full_matrices=True)
        # Standard numpy-style rank tolerance.
        tolerance = (singular_values.max() * max(jacobian.shape)
                     * np.finfo(float).eps if singular_values.size else 0.0)
        rank = int(np.sum(singular_values > tolerance))
        return Identifiability(
            beta=beta, rank=rank, singular_values=singular_values,
            identifiable=vt[:rank], null_space=vt[rank:],
            sensitivity=np.linalg.norm(jacobian, axis=0),
            beta_names=list(self.beta_names),
            num_components=self.num_components)

    def invert(self, target_coeffs, *, bounds=None, num_starts=32, seed=0,
               cluster_tol=0.02):
        """Estimate β from target coefficients by bounded multi-start descent.

        Minimizes :meth:`coeff_misfit` over β with L-BFGS-B from ``num_starts``
        scattered starting points (a reproducible Sobol design over the box via
        :func:`lume_ace3p.surrogate_data.sample_beta_doe`, plus the box center).
        The surrogate costs microseconds per evaluation, so a dense multi-start is
        essentially free — and it is what exposes **non-uniqueness**: several
        distinct β can explain one dose, and the returned
        :class:`InversionResult` reports every distinct minimum found, not just
        the best one.

        ``bounds`` defaults to the model's own training box
        ``[beta_lo, beta_hi]``. Do not widen it casually: a GP has no information
        outside the range it was trained on, so a β\\* found there is
        extrapolation, not an estimate.

        ``cluster_tol`` is the dedupe radius for "distinct" minima, as a fraction
        of each dimension's box span (default 2%)."""
        from scipy.optimize import minimize
        from lume_ace3p.surrogate_data import sample_beta_doe

        target = np.asarray(target_coeffs, dtype=float).ravel()
        if bounds is None:
            bounds = list(zip(self.beta_lo, self.beta_hi))
        bounds = [(float(lo), float(hi)) for lo, hi in bounds]
        if len(bounds) != self.beta_lo.shape[0]:
            raise ValueError(
                f"got {len(bounds)} bounds for a {self.beta_lo.shape[0]}-D β.")
        lo = np.array([b[0] for b in bounds], dtype=float)
        hi = np.array([b[1] for b in bounds], dtype=float)
        span = np.where(hi - lo == 0.0, 1.0, hi - lo)

        def objective(x):
            return self.coeff_misfit(np.asarray(x, dtype=float), target)

        # Scattered starts + the box center (a sensible deterministic anchor).
        starts = [0.5 * (lo + hi)]
        if num_starts > 1:
            starts.extend(sample_beta_doe(bounds, num_starts - 1,
                                          sampler='sobol', seed=seed))

        solutions = []
        for x0 in starts:
            try:
                res = minimize(objective, np.asarray(x0, dtype=float),
                               method='L-BFGS-B', bounds=bounds)
            except Exception:
                continue
            if res.x is None or not np.all(np.isfinite(res.x)):
                continue
            solutions.append((float(res.fun), np.asarray(res.x, dtype=float)))

        if not solutions:
            raise RuntimeError(
                "inversion failed: no starting point converged. Check that the "
                "target coefficients came from this model's project().")

        solutions.sort(key=lambda item: item[0])
        # Cluster into distinct minima: keep a solution only if it is farther than
        # cluster_tol (relative to the box span) from every better one already
        # kept. This is the non-uniqueness report.
        minima = []
        for misfit, x in solutions:
            if all(np.any(np.abs(x - kept) / span > cluster_tol)
                   for _kept_misfit, kept in minima):
                minima.append((misfit, x))

        best_misfit, best_beta = minima[0]
        return InversionResult(
            beta=best_beta, misfit=best_misfit,
            minima=[(m, b) for m, b in minima],
            num_starts=len(starts), target_coeffs=target,
            beta_names=list(self.beta_names), bounds=bounds)

    def sample_posterior(self, target_coeffs, *, bounds=None, dose_sigma=None,
                         num_warmup=1000, num_samples=2000, num_chains=4,
                         seed=0):
        """Sample a posterior over β for a target dose (Bayesian inversion).

        Where :meth:`invert` returns a point estimate — and, on a degenerate
        problem, an unrankable list of equally-good minima — this returns the
        *distribution* over β consistent with the target. That is the right object
        for this problem: with ``k`` retained POD modes the dose constrains at most
        ``k`` combinations of β, so the posterior comes out **tight along those
        directions and prior-wide along the rest**. Compare it against
        :meth:`identifiability` to read which is which.

        The likelihood is Gaussian in the model's retained-coefficient space, using
        the GP's own predictive variance plus ``dose_sigma`` (an assumed
        target-noise term, in the same coefficient space). ``dose_sigma`` defaults
        to the mean GP predictive std at the bounds' center — a scale set by the
        model itself rather than a magic constant; raise it to loosen the fit,
        lower it to pull harder toward exact agreement.

        ``bounds`` defaults to the model's training box, which also serves as the
        uniform prior. Along the flat directions the posterior *is* that prior, so
        the bounds are part of the answer, not an incidental setting.

        Sampling is NUTS (gradient-based, via a JAX re-expression of the GP
        prediction in :mod:`lume_ace3p.surrogate_jax`) because the degenerate set
        is a curved manifold that ensemble samplers explore poorly.

        ``num_chains`` defaults to **4, and lowering it is not advisable.** A
        curved degenerate manifold is exactly the geometry where one chain gets
        stuck in a slice of the flat directions: measured on the synthetic fixture,
        a single short chain gave ``r_hat = 1.61`` and reported the flat directions
        as ~0.04–0.10× the prior width — i.e. it looked as though the dose
        constrained β when it does not, which is a *wrong scientific conclusion*
        rather than merely a noisy one. Four chains gave ``r_hat = 1.006`` and the
        correct ~1.1× prior width. Always check
        :meth:`PosteriorResult.max_r_hat`.

        Returns a :class:`PosteriorResult`."""
        from lume_ace3p.surrogate_jax import sample_posterior_nuts

        target = np.asarray(target_coeffs, dtype=float).ravel()
        if target.shape[0] != self.num_components:
            raise ValueError(
                f"target has {target.shape[0]} coefficients but the model "
                f"retains {self.num_components}; project the target with this "
                "model's project().")
        if bounds is None:
            bounds = list(zip(self.beta_lo, self.beta_hi))
        bounds = [(float(lo), float(hi)) for lo, hi in bounds]
        if len(bounds) != self.beta_lo.shape[0]:
            raise ValueError(
                f"got {len(bounds)} bounds for a {self.beta_lo.shape[0]}-D β.")

        if dose_sigma is None:
            center = np.array([0.5 * (lo + hi) for lo, hi in bounds])
            _mean, var = self.predicted_coeffs(center)
            dose_sigma = float(np.mean(np.sqrt(np.maximum(var[0], 0.0))))
            # Guard a degenerate (near-zero) scale so the likelihood stays proper.
            dose_sigma = max(dose_sigma, 1e-12)

        samples, diagnostics = sample_posterior_nuts(
            self, target, bounds=bounds, dose_sigma=dose_sigma,
            num_warmup=num_warmup, num_samples=num_samples,
            num_chains=num_chains, seed=seed)
        return PosteriorResult(
            samples=samples, beta_names=list(self.beta_names), bounds=bounds,
            dose_sigma=float(dose_sigma), diagnostics=diagnostics,
            target_coeffs=target)

    # ---- persistence --------------------------------------------------- #

    def save(self, model_dir):
        """Persist the surrogate to ``model_dir`` and return the directory.

        Three artifacts: the PCA arrays to a pickle-free ``basis.npz``, the
        fitted sklearn GPs to ``gps.joblib`` (a *trusted local* artifact — unlike
        the untrusted field ``.npz`` which stays ``allow_pickle=False``), and a
        human-readable ``surrogate.json`` provenance dump (k, variance target,
        kept energy, per-GP fitted kernel) — the hyperparameter dump the plan
        calls for, analogous to ``modes._save_model``'s ``gp_parameters.txt``."""
        import joblib

        os.makedirs(model_dir, exist_ok=True)
        arrays = dict(
            mean=self.mean, basis=self.basis,
            singular_values=self.singular_values,
            beta_lo=self.beta_lo, beta_hi=self.beta_hi,
            beta_names=np.array(self.beta_names, dtype=object).astype('U'),
            dose_transform=np.array(self.dose_transform),
            floor=np.array(self.floor, dtype=float))
        if self.voxel_indices is not None:
            # The voxel order the basis columns correspond to, so inversion can
            # align an arbitrary target dose onto it (constraint #3).
            arrays['voxel_indices'] = self.voxel_indices
        np.savez(os.path.join(model_dir, BASIS_FILENAME), **arrays)
        joblib.dump(self.gps, os.path.join(model_dir, GPS_FILENAME))

        provenance = {
            'num_components': self.num_components,
            'variance_target': self.variance_target,
            'kept_energy': self.kept_energy,
            'beta_names': self.beta_names,
            'dose_transform': self.dose_transform,
            'floor': self.floor,
            'singular_values': self.singular_values.tolist(),
            'kernels': [str(gp.kernel_) for gp in self.gps],
        }
        with open(os.path.join(model_dir, PROVENANCE_FILENAME), 'w') as f:
            json.dump(provenance, f, indent=2, sort_keys=True)
            f.write('\n')
        return model_dir

    @classmethod
    def load(cls, model_dir):
        """Reload a surrogate saved by :meth:`save`. Predictions are identical to
        the in-memory model that produced the artifacts."""
        import joblib

        with np.load(os.path.join(model_dir, BASIS_FILENAME),
                     allow_pickle=False) as npz:
            mean = npz['mean']
            basis = npz['basis']
            singular_values = npz['singular_values']
            beta_lo = npz['beta_lo']
            beta_hi = npz['beta_hi']
            beta_names = [str(x) for x in npz['beta_names']]
            # Back-compat: models saved before the dose-transform feature have
            # neither key — they were fit in linear space.
            dose_transform = (str(npz['dose_transform'])
                              if 'dose_transform' in npz.files else 'linear')
            floor = (float(npz['floor']) if 'floor' in npz.files else 0.0)
            # Absent for models saved before the voxel order was recorded.
            voxel_indices = (npz['voxel_indices']
                             if 'voxel_indices' in npz.files else None)
        gps = joblib.load(os.path.join(model_dir, GPS_FILENAME))

        kept_energy = 1.0
        variance_target = None
        prov_path = os.path.join(model_dir, PROVENANCE_FILENAME)
        if os.path.isfile(prov_path):
            with open(prov_path) as f:
                prov = json.load(f)
            kept_energy = prov.get('kept_energy', 1.0)
            variance_target = prov.get('variance_target')
        return cls(mean, basis, singular_values, gps, beta_lo, beta_hi,
                   beta_names, kept_energy, variance_target=variance_target,
                   dose_transform=dose_transform, floor=floor,
                   voxel_indices=voxel_indices)


class Identifiability:
    """Which β directions a dose constrains, from :meth:`DoseSurrogate.identifiability`.

    Attributes:

    * ``beta`` — the β the analysis was performed at.
    * ``rank`` — how many independent combinations of β the dose constrains.
      Bounded above by ``num_components`` (the retained POD mode count): the dose
      reaches β only through those coefficients.
    * ``num_flat`` — ``D − rank``, the number of β directions the dose cannot see
      at all. Non-zero means the inverse problem is genuinely degenerate and no
      amount of optimizing will pin those directions down.
    * ``singular_values`` — ``(min(k,D),)`` Jacobian singular values in unit-box
      coordinates, descending. The gap between the last "large" one and the
      machine-zero tail is what sets the rank.
    * ``identifiable`` — ``(rank, D)`` β combinations the dose does constrain,
      most-constrained first.
    * ``null_space`` — ``(num_flat, D)`` β combinations it cannot. Moving β along
      any of these leaves the predicted dose unchanged.
    * ``sensitivity`` — ``(D,)`` per-β Jacobian column norm: how much the
      predicted coefficients move per unit-box move of that β.
    * ``beta_names`` / ``num_components`` — labels + the cause of a rank cap.
    """

    def __init__(self, beta, rank, singular_values, identifiable, null_space,
                 sensitivity, beta_names, num_components):
        self.beta = np.asarray(beta, dtype=float)
        self.rank = int(rank)
        self.singular_values = np.asarray(singular_values, dtype=float)
        self.identifiable = np.asarray(identifiable, dtype=float)
        self.null_space = np.asarray(null_space, dtype=float)
        self.sensitivity = np.asarray(sensitivity, dtype=float)
        self.beta_names = list(beta_names)
        self.num_components = int(num_components)

    @property
    def num_beta(self):
        return int(self.beta.shape[0])

    @property
    def num_flat(self):
        return self.num_beta - self.rank

    @property
    def is_degenerate(self):
        """True when the dose leaves at least one β direction unconstrained."""
        return self.num_flat > 0

    def summary(self):
        """One-or-two-line human summary, naming the cause of any rank cap."""
        text = (f"{self.rank} of {self.num_beta} β directions are constrained by "
                f"this dose; {self.num_flat} are flat (invisible to it)")
        if self.rank >= self.num_components and self.num_components < self.num_beta:
            text += (f". The rank is capped by the surrogate's "
                     f"{self.num_components} retained POD mode(s) — the dose "
                     f"reaches β only through those coefficients")
        return text + '.'

    def describe_direction(self, row, threshold=0.25):
        """Name the β dominating one direction, e.g. ``'beta3 - beta5'``-ish.

        Returns a compact ``+beta3 +beta4 -beta5`` style string of the components
        whose absolute weight exceeds ``threshold``, for a readable report."""
        row = np.asarray(row, dtype=float)
        parts = []
        for name, weight in zip(self.beta_names, row):
            if abs(weight) >= threshold:
                parts.append(f"{'+' if weight >= 0 else '-'}{name}")
        return ' '.join(parts) if parts else '(diffuse)'


class InversionResult:
    """The outcome of a :meth:`DoseSurrogate.invert` run.

    Attributes:

    * ``beta`` — ``(D,)`` best-misfit β estimate (β\\*).
    * ``misfit`` — its coefficient-space misfit.
    * ``minima`` — every **distinct** local minimum found, as ``(misfit, β)``
      pairs sorted best-first (``minima[0]`` is ``(misfit, beta)``). More than one
      entry means several β explain the target comparably well — the
      non-uniqueness the Bayesian phase will characterize properly.
    * ``num_starts`` — how many starting points were run.
    * ``target_coeffs`` — the projected target the misfit was measured against.
    * ``beta_names`` — ordered β names, for labelling.
    * ``bounds`` — the box the search was confined to (the model's training range
      unless overridden); β\\* is only trustworthy inside it.
    * ``identifiability`` — an :class:`Identifiability` for β\\*, set by
      ``modes.invert_optimize`` (``None`` if the analysis was skipped).

    **On ranking the minima:** ``minima`` is ordered by misfit, but that ordering
    is *not* an evidence ranking. When the misfits are all numerically ~0 (see
    :meth:`minima_are_distinguishable`) they are equally good explanations lying
    on one degenerate surface, and the ordering reflects solver convergence noise.
    Use ``identifiability`` to see how many β directions are actually pinned, and
    a posterior (``invert_bayesian``) to characterize the surface.
    """

    def __init__(self, beta, misfit, minima, num_starts, target_coeffs,
                 beta_names, bounds, identifiability=None):
        self.beta = np.asarray(beta, dtype=float)
        self.misfit = float(misfit)
        self.minima = list(minima)
        self.num_starts = int(num_starts)
        self.target_coeffs = np.asarray(target_coeffs, dtype=float)
        self.beta_names = list(beta_names)
        self.bounds = list(bounds)
        self.identifiability = identifiability

    def minima_are_distinguishable(self, floor=1e-8):
        """Whether the reported minima differ by enough misfit to be *ranked*.

        ``False`` when every minimum's misfit is below ``floor`` — they all
        explain the target equally well, so ordering them by misfit is sorting
        numerical noise, not evidence. Only when some minima are genuinely worse
        does the ranking carry information."""
        if len(self.minima) < 2:
            return False
        worst = max(m for m, _b in self.minima)
        return bool(worst > floor)

    @property
    def num_distinct(self):
        """Number of distinct local minima found (>1 signals non-uniqueness)."""
        return len(self.minima)

    def beta_dict(self):
        """β\\* as a ``{name: value}`` mapping."""
        return dict(zip(self.beta_names, self.beta.tolist()))

    def relative_l2(self, surrogate, target_dose, space='fit'):
        """Reconstruction error of β\\*'s predicted dose against ``target_dose``.

        ``target_dose`` is the aligned raw (linear) dose vector the inversion
        targeted. The comparison is done in ``space`` — default ``'fit'``, the
        model's own space, which for a ``'log10'`` model is log space. That is the
        honest metric: inverting a log prediction back to linear amplifies the
        ~9-order tail error and would report a misleadingly huge number."""
        target_dose = np.asarray(target_dose, dtype=float).ravel()
        predicted = surrogate.predict_dose(self.beta, space=space)
        if space == 'fit':
            predicted = predicted[0]        # (mean, var) -> mean
            truth = _apply_transform(target_dose, surrogate.dose_transform,
                                     surrogate.floor)
        else:
            truth = target_dose
        denominator = np.linalg.norm(truth)
        return float(np.linalg.norm(predicted - truth)
                     / (denominator if denominator else 1.0))


class PosteriorResult:
    """Posterior over β from :meth:`DoseSurrogate.sample_posterior`.

    Attributes:

    * ``samples`` — ``(S, D)`` posterior draws.
    * ``beta_names`` — ordered β names (columns of ``samples``).
    * ``bounds`` — the box used as the uniform prior. Along the β directions the
      dose cannot constrain, the posterior *is* this prior — so the bounds are part
      of the answer, not an incidental setting.
    * ``dose_sigma`` — assumed target-noise scale in coefficient space.
    * ``diagnostics`` — ``{'r_hat': (D,), 'n_eff': (D,)}`` from numpyro.
    * ``target_coeffs`` — the projected target that was conditioned on.
    """

    def __init__(self, samples, beta_names, bounds, dose_sigma, diagnostics,
                 target_coeffs):
        self.samples = np.asarray(samples, dtype=float)
        self.beta_names = list(beta_names)
        self.bounds = list(bounds)
        self.dose_sigma = float(dose_sigma)
        self.diagnostics = dict(diagnostics or {})
        self.target_coeffs = np.asarray(target_coeffs, dtype=float)

    def __len__(self):
        return int(self.samples.shape[0])

    def mean(self):
        return self.samples.mean(axis=0)

    def median(self):
        return np.median(self.samples, axis=0)

    def std(self):
        return self.samples.std(axis=0)

    def credible_interval(self, level=0.9):
        """Equal-tailed credible interval per β as ``(lo (D,), hi (D,))``."""
        tail = 0.5 * (1.0 - float(level))
        return (np.quantile(self.samples, tail, axis=0),
                np.quantile(self.samples, 1.0 - tail, axis=0))

    @property
    def prior_std(self):
        """Std of the uniform prior per β (a uniform box has width/√12)."""
        widths = np.array([hi - lo for lo, hi in self.bounds], dtype=float)
        return widths / np.sqrt(12.0)

    def max_r_hat(self):
        """Worst per-β ``r_hat``, or ``nan`` when unavailable. Values much above
        1.0 (say >1.05) mean the chains did not mix and the posterior should not be
        trusted as-is."""
        r_hat = self.diagnostics.get('r_hat')
        if r_hat is None or not np.size(r_hat):
            return float('nan')
        return float(np.nanmax(r_hat))

    def direction_widths(self, identifiability):
        """Posterior width along each identifiable / flat direction.

        Returns a list of ``(label, posterior_std, prior_std, ratio)``, projecting
        the draws onto the directions from
        :meth:`DoseSurrogate.identifiability`. This is the payoff table: the
        constrained directions should come out with a ratio far below 1 (the data
        pinned them down) while the flat ones sit near 1 (the data said nothing, so
        the posterior is the prior). A near-1 ratio on a flat direction is the
        **correct** result, not a sampling failure."""
        rows = []
        prior = self.prior_std
        groups = (('constrained', identifiability.identifiable),
                  ('flat', identifiability.null_space))
        for label, directions in groups:
            for i, row in enumerate(np.atleast_2d(directions)):
                if not row.size:
                    continue
                projected = self.samples @ row
                # Prior std along a unit direction, from independent uniforms.
                prior_along = float(np.sqrt(np.sum((row * prior) ** 2)))
                posterior_along = float(projected.std())
                ratio = (posterior_along / prior_along
                         if prior_along else float('nan'))
                rows.append((f'{label}_{i}', posterior_along, prior_along, ratio))
        return rows
