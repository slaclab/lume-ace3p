"""Additive per-bin forward dose surrogate.

Geant4 transports each primary independently and every particle's
Fowler-Nordheim weight depends on the β of its own emission bin only, so the
dose is a sum over bins (``plans/geant4_dose_campaign_plan.md`` §1, verified in
its Phase 0):

    D(β) = Σ_b a_b(β_b) · S_b

with ``a_b`` the total dose bin ``b`` deposits (smooth and near-exponential in
β) and ``S_b`` its normalized shape (sum 1, constant in β within the Monte Carlo
noise, Phase 1 review). The model is trained from one 1-D sweep per bin, with
the other bins switched off, rather than from an 8-D design.

:class:`AdditiveSurrogate` has the public interface of
:class:`lume_ace3p.surrogate.DoseSurrogate`, so ``invert_optimize`` and
``invert_bayesian`` run against it unchanged. Its *coefficient space* is the
eight per-bin amplitudes. :meth:`AdditiveSurrogate.project` fits them to a
target by noise-weighted least squares, so a projected target carries both the
amplitudes and their precision; see the method for the packed layout.

Three findings from the campaign are built in:

* **Noise comes from the model, not from the target's own variance column.** The
  target's per-voxel MC variance is predicted from per-bin relative variance per
  event (``unit_var``), scaled by the target's primaries and
  ``histories_per_macro``. Weighting by the target's ``val^2`` instead gave four
  times the β error on the 16 held-out samples.
* **A per-voxel sigma floor** (``sigma_floor`` × the largest voxel sigma). A voxel
  expected to see under one event gets a near-zero predicted sigma, and one stray
  hit there swamps every other voxel.
* **The Cramér-Rao sigma is about 2× optimistic**, because the noise model is
  diagonal and Geant4 voxel noise is correlated. ``noise_scale`` (default 2)
  inflates every reported error and the posterior likelihood by that factor.
"""

import json
import os

import numpy as np


ADDITIVE_FILENAME = 'additive.npz'
PROVENANCE_FILENAME = 'surrogate.json'
KIND = 'additive'

# Iterations of the reweighted fit in project(); Phase 2 converged in three.
N_IRLS = 3


class AdditiveSurrogate:
    """A trained additive per-bin dose surrogate.

    Construct with :meth:`fit` (or :meth:`load`). Attributes:

    * ``beta_grid`` — ``(n,)`` β values every bin was swept over.
    * ``log_amp`` — ``(B, n)`` log10 of each bin's total dose on the grid.
    * ``shapes`` — ``(B, M)`` normalized per-bin dose shapes ``S_b``.
    * ``shape_var`` — ``(B, M)`` MC variance of each shape.
    * ``rho`` — ``(B,)`` relative amplitude interpolation error (leave-one-out).
    * ``unit_var`` — ``(B, n, M)`` relative variance per event,
      ``var / total² × events``, at each grid point.
    * ``primaries`` — ``(B, n)`` primaries the training run at each grid point
      launched. Predicts the target's primaries per bin.
    * ``beta_names`` / ``voxel_indices`` — as for ``DoseSurrogate``.
    * ``sigma_floor``, ``noise_scale``, ``beta_resolution`` — see :meth:`fit`.
    * ``target_histories_per_macro`` — the ``histories_per_macro`` of the dose
      being inverted (default 1). It sets the predicted target noise, so set it
      to match the target before :meth:`project` / :meth:`identifiability`.
    """

    dose_transform = 'linear'
    floor = 0.0

    def __init__(self, beta_grid, log_amp, shapes, shape_var, rho, unit_var,
                 primaries, beta_names, voxel_indices=None, sigma_floor=1e-3,
                 noise_scale=2.0, beta_resolution=1.0,
                 target_histories_per_macro=1):
        from scipy.interpolate import PchipInterpolator

        self.beta_grid = np.asarray(beta_grid, dtype=float)
        self.log_amp = np.asarray(log_amp, dtype=float)
        self.shapes = np.asarray(shapes, dtype=float)
        self.shape_var = np.asarray(shape_var, dtype=float)
        self.rho = np.asarray(rho, dtype=float)
        self.unit_var = np.asarray(unit_var, dtype=float)
        self.primaries = np.asarray(primaries, dtype=float)
        self.beta_names = list(beta_names)
        self.voxel_indices = (None if voxel_indices is None
                              else np.asarray(voxel_indices, dtype=int))
        self.sigma_floor = float(sigma_floor)
        self.noise_scale = float(noise_scale)
        self.beta_resolution = float(beta_resolution)
        self.target_histories_per_macro = int(target_histories_per_macro)
        # Monotone cubic in log10(amplitude): no overshoot between grid points,
        # so a_b stays invertible (project() and invert() rely on that).
        self._log_amp_fns = [PchipInterpolator(self.beta_grid, row)
                             for row in self.log_amp]
        self._dlog_amp_fns = [f.derivative() for f in self._log_amp_fns]

    # ---- construction -------------------------------------------------- #

    @classmethod
    def fit(cls, beta_grid, dose, variance, primaries, histories_per_macro, *,
            beta_names=None, voxel_indices=None, shape_beta_min=None,
            sigma_floor=1e-3, noise_scale=2.0, beta_resolution=1.0):
        """Fit from one bin-isolated sweep per bin.

        ``beta_grid`` is the ``(n,)`` β grid every bin was swept over (each run
        sets one bin to the grid value and switches the others off).
        ``dose`` and ``variance`` are ``(B, n, M)``: run ``(b, i)``'s dose grid
        and its per-voxel MC variance, all on one voxel order (``voxel_indices``).
        ``primaries`` and ``histories_per_macro`` are ``(B, n)`` and give each
        run's fidelity: its events are their product (constraint #4).

        ``shape_beta_min`` drops runs below that β from the shape average. Runs
        just above the emission onset have few primaries repeated many times, so
        their histories are correlated and the variance column understates their
        noise (the campaign uses 44 on a 40 to 60 grid). They still enter the
        amplitude curve.

        ``sigma_floor`` is the per-voxel sigma floor as a fraction of the largest
        voxel sigma. ``noise_scale`` multiplies the predicted noise in every
        reported error and in the posterior. ``beta_resolution`` is the
        Cramér-Rao sigma (in β units, after ``noise_scale``) below which a β
        direction counts as constrained in :meth:`identifiability`."""
        beta_grid = np.asarray(beta_grid, dtype=float).ravel()
        dose = np.asarray(dose, dtype=float)
        variance = np.asarray(variance, dtype=float)
        primaries = np.asarray(primaries, dtype=float)
        hpm = np.asarray(histories_per_macro, dtype=float)
        if dose.ndim != 3:
            raise ValueError("fit expects dose as (bins, grid points, voxels).")
        num_bins, num_points, _num_voxels = dose.shape
        if variance.shape != dose.shape:
            raise ValueError(
                f"variance has shape {variance.shape} but dose has {dose.shape}.")
        if primaries.shape != (num_bins, num_points) or hpm.shape != primaries.shape:
            raise ValueError(
                "primaries and histories_per_macro must be (bins, grid points) = "
                f"{(num_bins, num_points)}.")
        if beta_grid.shape[0] != num_points or num_points < 2:
            raise ValueError(
                f"beta_grid has {beta_grid.shape[0]} points but dose has "
                f"{num_points} per bin; need at least 2.")
        if np.any(np.diff(beta_grid) <= 0):
            raise ValueError("beta_grid must be strictly increasing.")

        totals = dose.sum(axis=2)                               # (B, n)
        for b in range(num_bins):
            empty = beta_grid[totals[b] <= 0.0]
            if empty.size:
                raise ValueError(
                    f"bin {b} deposits no dose at β = {empty.tolist()}: the grid "
                    "starts below where the bin emits any particle. Start the "
                    "grid higher (constraint #7: size a β range against the "
                    "dump).")
        log_amp = np.log10(totals)
        for b in range(num_bins):
            if np.any(np.diff(log_amp[b]) <= 0):
                raise ValueError(
                    f"bin {b}'s total dose does not rise strictly with β "
                    f"({totals[b].tolist()}); the amplitude curve must be "
                    "invertible. Check that each run isolates its bin.")

        events = primaries * hpm
        shapes = np.empty(dose.shape[::2])
        shape_var = np.empty_like(shapes)
        keep = (np.ones(num_points, bool) if shape_beta_min is None
                else beta_grid >= float(shape_beta_min))
        if not np.any(keep):
            raise ValueError(
                f"shape_beta_min={shape_beta_min} leaves no runs for the shape.")
        for b in range(num_bins):
            shapes[b], shape_var[b] = _mean_shape(
                dose[b, keep], variance[b, keep], primaries[b, keep],
                hpm[b, keep])

        unit_var = variance / totals[:, :, None] ** 2 * events[:, :, None]
        rho = np.array([_loo_amplitude_error(beta_grid, row) for row in log_amp])

        if beta_names is None:
            beta_names = [f'beta{b}' for b in range(num_bins)]
        if len(beta_names) != num_bins:
            raise ValueError(
                f"{len(beta_names)} beta_names for {num_bins} bins.")
        return cls(beta_grid, log_amp, shapes, shape_var, rho, unit_var,
                   primaries, beta_names, voxel_indices=voxel_indices,
                   sigma_floor=sigma_floor, noise_scale=noise_scale,
                   beta_resolution=beta_resolution)

    # ---- the forward model --------------------------------------------- #

    @property
    def num_bins(self):
        return int(self.shapes.shape[0])

    @property
    def num_components(self):
        """Size of the coefficient space: one amplitude per bin."""
        return self.num_bins

    @property
    def beta_lo(self):
        return np.full(self.num_bins, self.beta_grid[0])

    @property
    def beta_hi(self):
        return np.full(self.num_bins, self.beta_grid[-1])

    def amplitudes(self, beta):
        """Per-bin total dose ``a_b(β_b)``, ``(B,)`` or ``(N, B)``. β is clipped
        to the training grid: the curves are not extrapolated."""
        beta = np.clip(np.asarray(beta, dtype=float), self.beta_grid[0],
                       self.beta_grid[-1])
        single = beta.ndim == 1
        beta = np.atleast_2d(beta)
        amp = np.stack([10.0 ** f(beta[:, b])
                        for b, f in enumerate(self._log_amp_fns)], axis=1)
        return amp[0] if single else amp

    def amplitude_derivatives(self, beta):
        """``d a_b / d β_b`` per bin, ``(B,)``, at one β inside the grid."""
        beta = np.clip(np.asarray(beta, dtype=float).ravel(),
                       self.beta_grid[0], self.beta_grid[-1])
        amp = self.amplitudes(beta)
        slope = np.array([f(beta[b]) for b, f in enumerate(self._dlog_amp_fns)])
        return amp * np.log(10.0) * slope

    def beta_from_amplitudes(self, amplitudes):
        """Invert the monotone ``a_b`` per bin. An amplitude outside a bin's
        training range maps to the nearest grid end."""
        from scipy.optimize import brentq

        amplitudes = np.asarray(amplitudes, dtype=float).ravel()
        lo, hi = self.beta_grid[0], self.beta_grid[-1]
        beta = np.empty(self.num_bins)
        for b, (amp, f) in enumerate(zip(amplitudes, self._log_amp_fns)):
            if amp <= 10.0 ** self.log_amp[b, 0]:
                beta[b] = lo
            elif amp >= 10.0 ** self.log_amp[b, -1]:
                beta[b] = hi
            else:
                level = np.log10(amp)
                beta[b] = brentq(lambda x: f(x) - level, lo, hi, xtol=1e-12)
        return beta

    def predict_dose(self, beta, space='fit'):
        """Predict the dose grid for ``β``: ``Σ_b a_b(β_b)·S_b``.

        The model is linear, so ``'fit'`` and ``'linear'`` are the same space.
        ``'fit'`` returns ``(mean, var)``, with ``var`` the model's own
        uncertainty (shape noise plus amplitude interpolation error, not the
        target's MC noise); ``'linear'`` returns the mean only, as
        ``DoseSurrogate`` does. A single β gives 1-D grids, a batch ``(N, M)``."""
        amp = self.amplitudes(beta)
        mean = amp @ self.shapes
        if space == 'linear':
            return mean
        if space != 'fit':
            raise ValueError(f"unknown space '{space}'; use 'fit' or 'linear'.")
        return mean, (amp ** 2) @ self._model_shape_var

    @property
    def _model_shape_var(self):
        return self.shape_var + (self.rho ** 2)[:, None] * self.shapes ** 2

    def target_variance(self, beta, histories_per_macro=None):
        """Predicted per-voxel MC variance of a full-chain run at ``β``.

        ``Σ_b a_b² · U_b / (P_b · H)``: the relative variance per event at the
        nearest grid point, scaled by the primaries the bin launches at that β
        (interpolated from the training runs) and the target's
        ``histories_per_macro`` ``H``."""
        hpm = (self.target_histories_per_macro if histories_per_macro is None
               else int(histories_per_macro))
        beta = np.clip(np.asarray(beta, dtype=float).ravel(),
                       self.beta_grid[0], self.beta_grid[-1])
        amp = self.amplitudes(beta)
        var = np.zeros(self.shapes.shape[1])
        for b in range(self.num_bins):
            k = int(np.argmin(np.abs(self.beta_grid - beta[b])))
            launched = np.interp(beta[b], self.beta_grid, self.primaries[b])
            var += amp[b] ** 2 * self.unit_var[b, k] / (launched * hpm)
        return var

    def noise_sigma(self, beta, histories_per_macro=None):
        """Per-voxel sigma of ``target − model`` at ``β``, before
        ``noise_scale``: target MC noise plus model noise, with the sigma floor.
        :meth:`project` weights by it and :meth:`identifiability` whitens by it."""
        var = (self.target_variance(beta, histories_per_macro)
               + self.predict_dose(beta)[1])
        return np.sqrt(var + (self.sigma_floor * np.sqrt(var.max())) ** 2)

    # ---- coefficient space --------------------------------------------- #

    def project(self, dose_grid, space='linear'):
        """Fit the per-bin amplitudes to a target dose.

        Iteratively reweighted: a nonnegative fit onto the shapes gives a first β
        (through the inverse amplitude curves), whose predicted noise
        (:meth:`noise_sigma`) weights the next fit, three times. The returned
        amplitudes are the unconstrained weighted least-squares solution ``â``
        under the final weights, and ``R`` is the upper Cholesky factor of their
        precision ``F = S W Sᵀ``. Then ``‖R (a − â)‖²`` equals the voxel
        chi-square of any amplitude vector ``a`` up to a constant, so
        :meth:`coeff_misfit` and the posterior score in eight dimensions what
        the voxels say.

        Returns the packed ``(B + B²,)`` vector ``[â, R.ravel()]`` (a batch of
        grids gives ``(N, B + B²)``). ``space`` is accepted for interface
        parity; the model is linear, so both spaces are dose."""
        if space not in ('linear', 'fit'):
            raise ValueError(f"unknown space '{space}'; use 'linear' or 'fit'.")
        dose_grid = np.asarray(dose_grid, dtype=float)
        if dose_grid.ndim == 2:
            return np.stack([self.project(row) for row in dose_grid])
        if dose_grid.shape != (self.shapes.shape[1],):
            raise ValueError(
                f"target has {dose_grid.shape[0]} voxels but the model has "
                f"{self.shapes.shape[1]}; align it with align_to_indices first.")
        from scipy.optimize import nnls

        design = self.shapes.T                                  # (M, B)
        sigma = np.ones_like(dose_grid)
        for _ in range(N_IRLS):
            amp, _ = nnls(design / sigma[:, None], dose_grid / sigma)
            sigma = self.noise_sigma(self.beta_from_amplitudes(amp))
        weighted = design / sigma[:, None]
        precision = weighted.T @ weighted
        amp_hat = np.linalg.solve(precision, weighted.T @ (dose_grid / sigma))
        upper = np.linalg.cholesky(precision).T
        return np.concatenate([amp_hat, upper.ravel()])

    def unpack(self, target_coeffs):
        """Split a :meth:`project` vector into ``(â (B,), R (B, B))``."""
        target = np.asarray(target_coeffs, dtype=float).ravel()
        num = self.num_bins
        if target.shape[0] != num + num * num:
            raise ValueError(
                f"target has {target.shape[0]} coefficients but this additive "
                f"model's project() gives {num + num * num} (B amplitudes and "
                "their B×B precision factor); project the target with this "
                "model.")
        return target[:num], target[num:].reshape(num, num)

    def coeff_misfit(self, beta, target_coeffs):
        """Amplitude-space chi-square ``‖R (a(β) − â)‖² / noise_scale²``.
        A single β gives a float, a batch ``(N,)``."""
        amp_hat, upper = self.unpack(target_coeffs)
        single = np.asarray(beta).ndim == 1
        amp = np.atleast_2d(self.amplitudes(beta))
        misfit = np.sum(((amp - amp_hat) @ upper.T) ** 2, axis=1)
        misfit = misfit / self.noise_scale ** 2
        return float(misfit[0]) if single else misfit

    # ---- inversion ----------------------------------------------------- #

    def _bounds(self, bounds):
        lo, hi = self.beta_grid[0], self.beta_grid[-1]
        if bounds is None:
            return [(float(lo), float(hi))] * self.num_bins
        bounds = [(float(b_lo), float(b_hi)) for b_lo, b_hi in bounds]
        if len(bounds) != self.num_bins:
            raise ValueError(f"got {len(bounds)} bounds for {self.num_bins} bins.")
        for name, (b_lo, b_hi) in zip(self.beta_names, bounds):
            if b_lo < lo or b_hi > hi or b_lo >= b_hi:
                raise ValueError(
                    f"bounds for {name} are [{b_lo}, {b_hi}], but the model is "
                    f"trained on [{lo}, {hi}] only and does not extrapolate.")
        return bounds

    def invert(self, target_coeffs, *, bounds=None, num_starts=None, seed=None,
               cluster_tol=None):
        """Estimate β from a projected target.

        The misfit is a convex quadratic in the amplitudes, and a β box maps to
        an amplitude box because each ``a_b`` is monotone, so the problem has a
        single minimum. It is solved exactly as a bounded linear least-squares
        problem in amplitude space and mapped back to β. A bin the dose cannot
        see comes back somewhere along its flat direction (often a bound); read
        it through :meth:`identifiability`, not as an estimate.

        ``num_starts``, ``seed`` and ``cluster_tol`` are accepted for interface
        parity and unused: there are no local minima to search."""
        from scipy.optimize import lsq_linear
        from lume_ace3p.surrogate import InversionResult

        amp_hat, upper = self.unpack(target_coeffs)
        bounds = self._bounds(bounds)
        amp_lo = self.amplitudes(np.array([b[0] for b in bounds]))
        amp_hi = self.amplitudes(np.array([b[1] for b in bounds]))
        # Solve in amplitudes scaled to their box, so bins whose amplitudes
        # differ by orders of magnitude are conditioned alike.
        span = amp_hi - amp_lo
        solution = lsq_linear(upper * span, upper @ (amp_hat - amp_lo),
                              bounds=(0.0, 1.0), method='bvls')
        amp = amp_lo + np.clip(solution.x, 0.0, 1.0) * span
        beta = np.clip(self.beta_from_amplitudes(amp),
                       [b[0] for b in bounds], [b[1] for b in bounds])
        misfit = self.coeff_misfit(beta, target_coeffs)
        return InversionResult(
            beta=beta, misfit=misfit, minima=[(misfit, beta)], num_starts=1,
            target_coeffs=np.asarray(target_coeffs, dtype=float).ravel(),
            beta_names=list(self.beta_names), bounds=bounds)

    def identifiability(self, beta, *, step=None):
        """Which β directions the dose constrains at ``β``, and how well.

        The whitened Jacobian is ``R(β) · diag(d a_b / d β_b) / noise_scale``,
        with ``R(β)`` from the predicted noise at ``β`` and the target's
        ``histories_per_macro``. Its inverse Fisher matrix gives the Cramér-Rao
        sigma of each β (``beta_sigma``), and a direction counts as constrained
        when its sigma is below ``beta_resolution``. This rank is a statement
        about the noise, not about the model: every bin moves the dose, but a bin
        far below the dominant one moves it by less than the noise. ``step`` is
        accepted for interface parity and unused (the Jacobian is analytic)."""
        from lume_ace3p.surrogate import Identifiability

        beta = np.asarray(beta, dtype=float).ravel()
        sigma = self.noise_sigma(beta) * self.noise_scale
        weighted = self.shapes.T / sigma[:, None]
        jacobian = (weighted @ np.diag(self.amplitude_derivatives(beta)))
        _u, singular_values, vt = np.linalg.svd(jacobian, full_matrices=True)
        fisher = jacobian.T @ jacobian
        beta_sigma = np.sqrt(np.clip(np.diag(np.linalg.pinv(fisher)), 0.0, None))
        # A direction with singular value s has Cramér-Rao sigma 1/s along it.
        rank = int(np.sum(singular_values * self.beta_resolution > 1.0))
        return Identifiability(
            beta=beta, rank=rank, singular_values=singular_values,
            identifiable=vt[:rank], null_space=vt[rank:],
            sensitivity=np.linalg.norm(jacobian, axis=0),
            beta_names=list(self.beta_names),
            num_components=self.num_components, beta_sigma=beta_sigma,
            resolution=self.beta_resolution)

    def sample_posterior(self, target_coeffs, *, bounds=None, dose_sigma=None,
                         num_warmup=1000, num_samples=2000, num_chains=4,
                         seed=0):
        """Sample ``P(β | target)`` with NUTS.

        Likelihood ``â ~ Normal(a(β), (RᵀR)⁻¹ · s²)`` in amplitude space, with
        ``s = dose_sigma`` if given and ``noise_scale`` otherwise: here
        ``dose_sigma`` is a multiplier on the predicted noise, not an absolute
        scale. Prior uniform on ``bounds`` (default the training grid). See
        :meth:`lume_ace3p.surrogate.DoseSurrogate.sample_posterior` for why four
        chains."""
        from lume_ace3p.surrogate import PosteriorResult
        from lume_ace3p.surrogate_jax import sample_additive_posterior_nuts

        amp_hat, upper = self.unpack(target_coeffs)
        bounds = self._bounds(bounds)
        scale = self.noise_scale if dose_sigma is None else float(dose_sigma)
        if scale <= 0.0:
            raise ValueError(f"dose_sigma must be positive, got {scale}.")
        samples, diagnostics = sample_additive_posterior_nuts(
            self, amp_hat, upper / scale, bounds=bounds,
            num_warmup=num_warmup, num_samples=num_samples,
            num_chains=num_chains, seed=seed)
        return PosteriorResult(
            samples=samples, beta_names=list(self.beta_names), bounds=bounds,
            dose_sigma=scale, diagnostics=diagnostics,
            target_coeffs=np.asarray(target_coeffs, dtype=float).ravel())

    # ---- persistence --------------------------------------------------- #

    def save(self, model_dir):
        """Write ``additive.npz`` (pickle-free) and a ``surrogate.json``
        provenance file that names the model kind; return ``model_dir``."""
        os.makedirs(model_dir, exist_ok=True)
        arrays = dict(
            beta_grid=self.beta_grid, log_amp=self.log_amp, shapes=self.shapes,
            shape_var=self.shape_var, rho=self.rho, unit_var=self.unit_var,
            primaries=self.primaries,
            beta_names=np.array(self.beta_names, dtype=object).astype('U'),
            sigma_floor=np.array(self.sigma_floor),
            noise_scale=np.array(self.noise_scale),
            beta_resolution=np.array(self.beta_resolution))
        if self.voxel_indices is not None:
            arrays['voxel_indices'] = self.voxel_indices
        np.savez(os.path.join(model_dir, ADDITIVE_FILENAME), **arrays)
        provenance = {
            'kind': KIND,
            'num_bins': self.num_bins,
            'beta_names': self.beta_names,
            'beta_grid': self.beta_grid.tolist(),
            'amplitude_rel_err': self.rho.tolist(),
            'sigma_floor': self.sigma_floor,
            'noise_scale': self.noise_scale,
            'beta_resolution': self.beta_resolution,
        }
        with open(os.path.join(model_dir, PROVENANCE_FILENAME), 'w') as f:
            json.dump(provenance, f, indent=2, sort_keys=True)
            f.write('\n')
        return model_dir

    @classmethod
    def load(cls, model_dir):
        """Reload a model saved by :meth:`save`; predictions are identical."""
        with np.load(os.path.join(model_dir, ADDITIVE_FILENAME),
                     allow_pickle=False) as npz:
            return cls(
                npz['beta_grid'], npz['log_amp'], npz['shapes'],
                npz['shape_var'], npz['rho'], npz['unit_var'], npz['primaries'],
                [str(x) for x in npz['beta_names']],
                voxel_indices=(npz['voxel_indices']
                               if 'voxel_indices' in npz.files else None),
                sigma_floor=float(npz['sigma_floor']),
                noise_scale=float(npz['noise_scale']),
                beta_resolution=float(npz['beta_resolution']))


def _mean_shape(dose, variance, primaries, hpm):
    """Weighted mean normalized shape of one bin's runs, and its variance.

    Runs with the same primaries and ``histories_per_macro`` launch the same
    particle file with the same event count, and the dose-npass binary has no
    seed, so they are one Monte Carlo realization reweighted, not independent
    samples (plan constraint #6, as amended in the Phase 1 review). Their shapes
    are averaged into one realization first. Realizations are then weighted by a
    scalar ``1 / Σ var(shape)``: per-voxel inverse-variance weights bias
    low-dose voxels down when the variances are themselves noisy estimates."""
    totals = dose.sum(axis=1)
    shapes = dose / totals[:, None]
    shape_vars = variance / totals[:, None] ** 2
    groups = {}
    for i, key in enumerate(zip(primaries.tolist(), hpm.tolist())):
        groups.setdefault(key, []).append(i)
    reals = [(shapes[idx].mean(axis=0), shape_vars[idx].mean(axis=0))
             for idx in groups.values()]
    weights = np.array([1.0 / max(v.sum(), np.finfo(float).tiny)
                        for _s, v in reals])
    weights /= weights.sum()
    mean = sum(w * s for w, (s, _v) in zip(weights, reals))
    var = sum(w ** 2 * v for w, (_s, v) in zip(weights, reals))
    return mean, var


def _loo_amplitude_error(beta_grid, log_amp):
    """Relative amplitude interpolation error of one bin: the RMS leave-one-out
    error of the log10 curve at interior grid points, as a natural-log relative
    error. Conservative, since leaving a point out doubles the local spacing.
    Zero with fewer than three points (nothing to leave out)."""
    from scipy.interpolate import PchipInterpolator

    if beta_grid.shape[0] < 3:
        return 0.0
    errors = []
    for i in range(1, beta_grid.shape[0] - 1):
        keep = np.arange(beta_grid.shape[0]) != i
        f = PchipInterpolator(beta_grid[keep], log_amp[keep])
        errors.append(float(f(beta_grid[i]) - log_amp[i]))
    return float(np.log(10.0) * np.sqrt(np.mean(np.square(errors))))
