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
    """

    def __init__(self, mean, basis, singular_values, gps, beta_lo, beta_hi,
                 beta_names, kept_energy, variance_target=None):
        self.mean = np.asarray(mean, dtype=float)
        self.basis = np.asarray(basis, dtype=float)
        self.singular_values = np.asarray(singular_values, dtype=float)
        self.gps = list(gps)
        self.beta_lo = np.asarray(beta_lo, dtype=float)
        self.beta_hi = np.asarray(beta_hi, dtype=float)
        self.beta_names = list(beta_names)
        self.kept_energy = float(kept_energy)
        self.variance_target = variance_target

    # ---- construction -------------------------------------------------- #

    @classmethod
    def fit(cls, beta, dose, *, variance=0.99, k=None, seed=0,
            beta_names=None):
        """Fit the surrogate from aligned ``β (N, D)`` and ``dose (N, M)``.

        ``variance`` is the cumulative-energy target for choosing the number of
        retained POD modes (ignored when an explicit ``k`` is given). ``seed``
        makes each GP's restart search reproducible. ``beta_names`` is recorded
        for provenance / inversion-time column alignment."""
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

        gps = []
        for j in range(n_modes):
            gp = _build_gp(beta.shape[1], seed)
            gp.fit(beta_unit, coeffs[:, j])
            gps.append(gp)

        if beta_names is None:
            beta_names = [f'beta{i}' for i in range(beta.shape[1])]
        return cls(mean, basis, singular_values, gps, beta_lo, beta_hi,
                   beta_names, kept_energy, variance_target=variance)

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

    def predict_dose(self, beta):
        """Predict the full dose grid for ``β``.

        Returns ``(mean_grid, var_grid)`` reconstructed from the coefficient GPs:
        ``mean_grid = mean + Σ_i c_i(β)·φ_i`` and, treating the modes as
        independent, ``var_grid = Σ_i Var[c_i(β)]·φ_i²``. The variance is
        strictly positive (fitted noise term, constraint #2). A single-β input
        returns 1-D grids; a batch returns ``(B, M)``."""
        beta_arr = np.atleast_2d(np.asarray(beta, dtype=float))
        single = np.asarray(beta).ndim == 1
        cmean, cvar = self.predicted_coeffs(beta_arr)
        mean_grid = self.mean + cmean @ self.basis          # (B, M)
        var_grid = cvar @ (self.basis ** 2)                 # (B, M)
        if single:
            return mean_grid[0], var_grid[0]
        return mean_grid, var_grid

    def project(self, dose_grid):
        """Project a dose grid into retained-basis coefficient space.

        ``coeffs = (dose - mean) @ Φ^T``. This is the single coefficient-space
        seam the inversion phase talks to (a target dose is projected here, then
        matched against ``predicted_coeffs``). Accepts a single grid ``(M,)`` or
        a batch ``(B, M)``; returns ``(k,)`` or ``(B, k)`` accordingly."""
        dose_grid = np.asarray(dose_grid, dtype=float)
        single = dose_grid.ndim == 1
        grids = np.atleast_2d(dose_grid)
        coeffs = (grids - self.mean) @ self.basis.T
        return coeffs[0] if single else coeffs

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
        np.savez(
            os.path.join(model_dir, BASIS_FILENAME),
            mean=self.mean, basis=self.basis,
            singular_values=self.singular_values,
            beta_lo=self.beta_lo, beta_hi=self.beta_hi,
            beta_names=np.array(self.beta_names, dtype=object).astype('U'))
        joblib.dump(self.gps, os.path.join(model_dir, GPS_FILENAME))

        provenance = {
            'num_components': self.num_components,
            'variance_target': self.variance_target,
            'kept_energy': self.kept_energy,
            'beta_names': self.beta_names,
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
                   beta_names, kept_energy, variance_target=variance_target)
