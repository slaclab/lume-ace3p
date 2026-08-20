"""Phase-4a tests: the ``invert_optimize`` mode — given a target dose profile,
estimate the β that produced it (see plans/geant4_surrogate_inversion_plan.md,
Phase 4).

Verification (Phase 4a done when):

* **Recovery:** a dose generated from a known β inverts to a β that reproduces
  that dose (fit-space relative-L2 small) and recovers the *identifiable*
  combinations of β. The synthetic fixture's amplitudes depend on β only through
  group means, so individual β components inside a group are genuinely
  non-identifiable — the honest bar is dose-space recovery plus recovery of the
  identifiable combinations, not naive per-component equality. (That is the real
  physics too: non-uniqueness is a project-level finding, not a bug.)
* **Perfect-target sanity:** inverting a noiseless dose drives the coefficient
  misfit to ~0.
* **Alignment:** a row-shuffled target file inverts identically to the in-order
  one; a target on a different mesh hard-fails (constraint #3).
* Both target sources (stored ``field.npz`` and a raw Geant4 dose file) agree.
* A ``log10`` surrogate inverts correctly (misfit measured in log space).
* Non-uniqueness is reported: >1 distinct minimum, one table row each.
* Mode + ``run_mode`` dispatch work and a fixed seed is reproducible.
* Back-compat: a surrogate saved without ``voxel_indices`` still loads, and
  inversion either uses a store-supplied order or fails clearly.

Reuses the synthetic β→dose fixture from tests/test_surrogate.py, so everything
runs locally with no Geant4 environment.
"""

import os
import textwrap

import numpy as np
import pandas as pd
import pytest

from lume_ace3p import surrogate_data
from lume_ace3p.modes import invert_optimize, run_mode, train_surrogate
from lume_ace3p.surrogate import DoseSurrogate, InversionResult
from tests.test_surrogate import (
    BETA_NAMES, _NZ, _collect_store, dose_of_beta)


VOXEL_INDICES = np.stack(
    [np.zeros(_NZ, int), np.zeros(_NZ, int), np.arange(_NZ)], axis=1)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _trained(tmp_path, num_samples=48, noise=0.02, dose_transform='linear',
             **fit_kwargs):
    """Collect a synthetic store and fit a surrogate carrying the voxel order."""
    store = _collect_store(tmp_path, num_samples=num_samples, noise=noise)
    ts = surrogate_data.load_training_store(store)
    model = DoseSurrogate.fit(ts.beta, ts.dose, variance=0.999, seed=0,
                              beta_names=ts.beta_names,
                              dose_transform=dose_transform,
                              voxel_indices=ts.indices, **fit_kwargs)
    return model, ts, store


def _write_dose_file(path, values, indices, shuffle=False, seed=0,
                     comma=False):
    """Write a Geant4-style ``ix iy iz value`` scoring file."""
    order = np.arange(len(values))
    if shuffle:
        order = np.random.default_rng(seed).permutation(order)
    sep = ', ' if comma else ' '
    lines = ['# mesh name: doseMesh', '# iX, iY, iZ, total(value) [Gy]']
    for j in order:
        ix, iy, iz = indices[j]
        lines.append(sep.join([str(int(ix)), str(int(iy)), str(int(iz)),
                               repr(float(values[j]))]))
    path.write_text('\n'.join(lines) + '\n')
    return str(path)


def _identifiable(beta):
    """The combinations of β the synthetic fixture actually responds to (the
    three group means driving its spatial-mode amplitudes)."""
    b = np.atleast_2d(np.asarray(beta, dtype=float))
    return np.vstack([b[:, 0:3].mean(axis=1),
                      b[:, 3:6].mean(axis=1),
                      b[:, 6:8].mean(axis=1)]).T[0]


# --------------------------------------------------------------------------- #
# Target loading + voxel alignment (constraint #3, inversion side)
# --------------------------------------------------------------------------- #


def test_read_dose_file_roundtrip(tmp_path):
    values = np.linspace(1.0, 2.0, _NZ)
    path = _write_dose_file(tmp_path / 'dose.txt', values, VOXEL_INDICES)
    grid = surrogate_data.read_dose_file(path)
    assert grid['indices'].shape == (_NZ, 3)
    assert np.allclose(grid['values'], values)
    # Comma-separated variant (with extra columns ignored) parses the same.
    path2 = _write_dose_file(tmp_path / 'dose2.txt', values, VOXEL_INDICES,
                             comma=True)
    assert np.allclose(surrogate_data.read_dose_file(path2)['values'], values)


def test_read_dose_file_missing_or_empty(tmp_path):
    assert surrogate_data.read_dose_file(str(tmp_path / 'nope.txt')) is None
    empty = tmp_path / 'empty.txt'
    empty.write_text('# only a comment\n\n')
    assert surrogate_data.read_dose_file(str(empty)) is None


def test_align_reorders_shuffled_target(tmp_path):
    values = np.linspace(1.0, 2.0, _NZ)
    path = _write_dose_file(tmp_path / 'shuffled.txt', values, VOXEL_INDICES,
                            shuffle=True, seed=3)
    grid = surrogate_data.read_dose_file(path)
    # Rows are genuinely out of order, but alignment restores the basis order.
    assert not np.allclose(grid['values'], values)
    aligned = surrogate_data.align_to_indices(
        grid['values'], grid['indices'], VOXEL_INDICES)
    assert np.allclose(aligned, values)


def test_align_rejects_different_mesh():
    values = np.linspace(1.0, 2.0, _NZ)
    # A target missing voxels the training grid has -> different mesh.
    with pytest.raises(ValueError, match='constraint #3'):
        surrogate_data.align_to_indices(values[:-3], VOXEL_INDICES[:-3],
                                        VOXEL_INDICES)
    # Same count but different voxel coordinates.
    drifted = VOXEL_INDICES.copy()
    drifted[:, 2] += 100
    with pytest.raises(ValueError, match='constraint #3'):
        surrogate_data.align_to_indices(values, drifted, VOXEL_INDICES)


def test_align_rejects_duplicate_voxels():
    values = np.ones(_NZ)
    duplicated = VOXEL_INDICES.copy()
    duplicated[1] = duplicated[0]
    with pytest.raises(ValueError, match='more than once'):
        surrogate_data.align_to_indices(values, duplicated, VOXEL_INDICES)


def test_load_target_dose_from_npz_and_file(tmp_path):
    from lume_ace3p.results import save_field
    values = np.linspace(1.0, 2.0, _NZ)
    npz = save_field({'dose': {'indices': VOXEL_INDICES, 'values': values}},
                     str(tmp_path / 'field.npz'))
    v1, i1 = surrogate_data.load_target_dose(npz)
    assert np.allclose(v1, values) and i1.shape == (_NZ, 3)

    path = _write_dose_file(tmp_path / 'dose.txt', values, VOXEL_INDICES)
    v2, i2 = surrogate_data.load_target_dose(path)
    assert np.allclose(v2, values) and np.array_equal(i2, i1)


def test_load_target_dose_errors(tmp_path):
    with pytest.raises(ValueError, match='target'):
        surrogate_data.load_target_dose(None)
    with pytest.raises(FileNotFoundError):
        surrogate_data.load_target_dose(str(tmp_path / 'missing.txt'))
    junk = tmp_path / 'junk.txt'
    junk.write_text('# no data rows\n')
    with pytest.raises(ValueError, match='no dose data rows'):
        surrogate_data.load_target_dose(str(junk))


# --------------------------------------------------------------------------- #
# Inversion core — recovery + non-uniqueness
# --------------------------------------------------------------------------- #


def test_recovery_of_known_beta(tmp_path):
    """The headline bar: a dose from a known β inverts to a β reproducing it."""
    model, _ts, _store = _trained(tmp_path, num_samples=48)
    truth_beta = np.array([52.0, 47.0, 55.0, 44.0, 58.0, 49.0, 46.0, 53.0])
    target = dose_of_beta(truth_beta)                 # noiseless truth
    result = model.invert(model.project(target), num_starts=24, seed=0)

    # Dose-space recovery: β* reproduces the target profile.
    assert result.relative_l2(model, target) < 0.05
    # The identifiable combinations (group means) are recovered.
    assert np.allclose(_identifiable(result.beta), _identifiable(truth_beta),
                       rtol=0.05)


def test_perfect_target_drives_misfit_to_zero(tmp_path):
    model, ts, _store = _trained(tmp_path)
    # Project a training sample's own dose and invert it.
    result = model.invert(model.project(ts.dose[0]), num_starts=16, seed=0)
    assert result.misfit < 1e-6


def test_reports_multiple_distinct_minima(tmp_path):
    """The synthetic map depends on β only through group means, so many distinct
    β explain one dose — multi-start must surface that non-uniqueness."""
    model, _ts, _store = _trained(tmp_path)
    truth_beta = np.full(8, 50.0)
    result = model.invert(model.project(dose_of_beta(truth_beta)),
                          num_starts=24, seed=0)
    assert result.num_distinct > 1
    # Every reported minimum explains the target comparably well.
    assert all(m < 1e-4 for m, _b in result.minima)


def test_invert_is_reproducible_and_bounded(tmp_path):
    model, _ts, _store = _trained(tmp_path)
    coeffs = model.project(dose_of_beta(np.full(8, 51.0)))
    a = model.invert(coeffs, num_starts=12, seed=0)
    b = model.invert(coeffs, num_starts=12, seed=0)
    assert np.allclose(a.beta, b.beta)
    # β* stays inside the search box (the model's training range by default).
    assert np.all(a.beta >= model.beta_lo - 1e-9)
    assert np.all(a.beta <= model.beta_hi + 1e-9)


def test_invert_respects_custom_bounds(tmp_path):
    model, _ts, _store = _trained(tmp_path)
    coeffs = model.project(dose_of_beta(np.full(8, 50.0)))
    tight = [(48.0, 52.0)] * 8
    result = model.invert(coeffs, bounds=tight, num_starts=8, seed=0)
    assert np.all(result.beta >= 48.0 - 1e-9)
    assert np.all(result.beta <= 52.0 + 1e-9)


def test_coeff_misfit_shapes_and_guard(tmp_path):
    model, ts, _store = _trained(tmp_path)
    coeffs = model.project(ts.dose[0])
    assert isinstance(model.coeff_misfit(ts.beta[0], coeffs), float)
    assert model.coeff_misfit(ts.beta[:3], coeffs).shape == (3,)
    with pytest.raises(ValueError, match='coefficients'):
        model.coeff_misfit(ts.beta[0], np.zeros(model.num_components + 2))


def test_log10_model_inverts(tmp_path):
    """A log10-space surrogate (the real-model configuration) inverts correctly,
    with the misfit measured in log space."""
    model, _ts, _store = _trained(tmp_path, num_samples=48,
                                  dose_transform='log10')
    assert model.dose_transform == 'log10'
    truth_beta = np.array([51.0, 48.0, 54.0, 45.0, 57.0, 50.0, 47.0, 52.0])
    target = dose_of_beta(truth_beta)
    result = model.invert(model.project(target, space='linear'),
                          num_starts=24, seed=0)
    assert result.relative_l2(model, target, space='fit') < 0.05
    assert np.allclose(_identifiable(result.beta), _identifiable(truth_beta),
                       rtol=0.10)


# --------------------------------------------------------------------------- #
# invert_optimize mode
# --------------------------------------------------------------------------- #


def test_mode_inverts_npz_target(tmp_path):
    store = _collect_store(tmp_path, num_samples=32)
    train_surrogate({'type': 'train_surrogate', 'store': store,
                     'variance': 0.999, 'seed': 0})
    # A stored training sample's own field artifact is the target.
    target = os.path.join(store, 'sample_00000', 'field.npz')
    result = invert_optimize({'type': 'invert_optimize', 'store': store,
                              'target': target, 'num_starts': 12, 'seed': 0})
    assert isinstance(result, InversionResult)
    table = os.path.join(store, 'inversion_result.txt')
    assert os.path.isfile(table)
    df = pd.read_csv(table, sep='\t')
    assert list(df.columns) == (['rank', 'misfit', 'relative_l2'] + BETA_NAMES)
    assert len(df) == result.num_distinct
    assert df['misfit'].is_monotonic_increasing


def test_mode_inverts_raw_dose_file_identically(tmp_path):
    """Both target sources agree, and a shuffled raw file gives the same β*."""
    store = _collect_store(tmp_path, num_samples=32)
    train_surrogate({'type': 'train_surrogate', 'store': store,
                     'variance': 0.999, 'seed': 0})
    npz_target = os.path.join(store, 'sample_00000', 'field.npz')
    values, indices = surrogate_data.load_target_dose(npz_target)

    from_npz = invert_optimize({'type': 'invert_optimize', 'store': store,
                                'target': npz_target, 'num_starts': 8,
                                'seed': 0})
    raw = _write_dose_file(tmp_path / 'target.txt', values, indices)
    from_raw = invert_optimize({'type': 'invert_optimize', 'store': store,
                                'target': raw, 'num_starts': 8, 'seed': 0,
                                'output_file': str(tmp_path / 'raw.txt')})
    shuffled = _write_dose_file(tmp_path / 'shuffled.txt', values, indices,
                                shuffle=True, seed=5)
    from_shuffled = invert_optimize(
        {'type': 'invert_optimize', 'store': store, 'target': shuffled,
         'num_starts': 8, 'seed': 0,
         'output_file': str(tmp_path / 'shuf.txt')})

    assert np.allclose(from_npz.beta, from_raw.beta, atol=1e-6)
    assert np.allclose(from_raw.beta, from_shuffled.beta, atol=1e-10)


def test_mode_custom_bounds_and_output_file(tmp_path):
    store = _collect_store(tmp_path, num_samples=24)
    train_surrogate({'type': 'train_surrogate', 'store': store, 'seed': 0})
    out = str(tmp_path / 'custom_inversion.txt')
    result = invert_optimize({
        'type': 'invert_optimize', 'store': store,
        'target': os.path.join(store, 'sample_00001', 'field.npz'),
        'bounds': {n: [48.0, 52.0] for n in BETA_NAMES},
        'num_starts': 6, 'seed': 0, 'output_file': out})
    assert os.path.isfile(out)
    assert np.all(result.beta >= 48.0 - 1e-9)
    assert np.all(result.beta <= 52.0 + 1e-9)


def test_run_mode_dispatches_invert_optimize(tmp_path):
    store = _collect_store(tmp_path, num_samples=16)
    train_surrogate({'type': 'train_surrogate', 'store': store, 'seed': 0})
    result = run_mode({'type': 'invert_optimize', 'store': store,
                       'target': os.path.join(store, 'sample_00000',
                                              'field.npz'),
                       'num_starts': 6, 'seed': 0}, workflow=None)
    assert isinstance(result, InversionResult)


def test_mode_requires_model_and_target(tmp_path):
    with pytest.raises(ValueError, match='model_dir'):
        invert_optimize({'type': 'invert_optimize', 'target': 'x.npz'})
    store = _collect_store(tmp_path, num_samples=16)
    with pytest.raises(ValueError, match='train_surrogate first'):
        invert_optimize({'type': 'invert_optimize', 'store': store,
                         'target': 'x.npz'})


# --------------------------------------------------------------------------- #
# Voxel-order provenance + back-compat
# --------------------------------------------------------------------------- #


def test_trained_model_records_voxel_order(tmp_path):
    store = _collect_store(tmp_path, num_samples=16)
    model = train_surrogate({'type': 'train_surrogate', 'store': store,
                             'seed': 0})
    assert model.voxel_indices is not None
    reloaded = DoseSurrogate.load(os.path.join(store, 'surrogate'))
    assert np.array_equal(reloaded.voxel_indices, model.voxel_indices)


def test_legacy_model_without_voxel_order(tmp_path):
    """A surrogate saved before voxel_indices existed still loads; inversion then
    uses a store-supplied order, or fails clearly when there is none."""
    store = _collect_store(tmp_path, num_samples=16)
    ts = surrogate_data.load_training_store(store)
    legacy = DoseSurrogate.fit(ts.beta, ts.dose, seed=0,
                               beta_names=ts.beta_names)   # no voxel_indices
    model_dir = str(tmp_path / 'legacy')
    legacy.save(model_dir)
    reloaded = DoseSurrogate.load(model_dir)
    assert reloaded.voxel_indices is None

    target = os.path.join(store, 'sample_00000', 'field.npz')
    # With a store to supply the voxel order, inversion proceeds.
    result = invert_optimize({'type': 'invert_optimize', 'store': store,
                              'model_dir': model_dir, 'target': target,
                              'num_starts': 6, 'seed': 0,
                              'output_file': str(tmp_path / 'ok.txt')})
    assert isinstance(result, InversionResult)
    # Without one, it hard-fails rather than guessing an order.
    with pytest.raises(ValueError, match='voxel order'):
        invert_optimize({'type': 'invert_optimize', 'model_dir': model_dir,
                         'target': target, 'num_starts': 4, 'seed': 0,
                         'output_file': str(tmp_path / 'no.txt')})


# --------------------------------------------------------------------------- #
# Identifiability — how many β directions the dose actually constrains.
#
# This is the substantive answer to "can the non-unique solutions be ranked?":
# usually not, because the degeneracy is a continuous SURFACE of exactly-equivalent
# β rather than competing hypotheses. The surrogate reaches β only through its k
# retained POD coefficients, so the dose constrains at most k combinations of β —
# with k < D the problem is rank-deficient by construction.
# --------------------------------------------------------------------------- #


def test_identifiability_reports_rank_and_flat_directions(tmp_path):
    """The synthetic fixture is driven by 3 spatial modes, so a k=3 surrogate
    constrains exactly 3 of the 8 β directions and leaves 5 flat."""
    model, _ts, _store = _trained(tmp_path, num_samples=48)
    assert model.num_components == 3
    truth = np.array([52., 47., 55., 44., 58., 49., 46., 53.])
    result = model.invert(model.project(dose_of_beta(truth)), num_starts=12,
                          seed=0)
    ident = model.identifiability(result.beta)

    assert ident.rank == 3
    assert ident.num_flat == 5
    assert ident.is_degenerate
    assert ident.identifiable.shape == (3, 8)
    assert ident.null_space.shape == (5, 8)
    # The summary names the cause (the retained-mode cap), not just the symptom.
    assert 'POD mode' in ident.summary()


def test_null_space_is_genuinely_flat(tmp_path):
    """The proof the basis is meaningful: stepping β along a null direction leaves
    the misfit ~0, while stepping the same distance along the best-constrained
    direction raises it by orders of magnitude."""
    model, _ts, _store = _trained(tmp_path, num_samples=48)
    target_coeffs = model.project(dose_of_beta(np.full(8, 50.0)))
    result = model.invert(target_coeffs, num_starts=12, seed=0)
    beta0 = result.beta
    span = model.beta_hi - model.beta_lo
    step = 0.03                                  # in unit-box coordinates

    def misfit_after(direction):
        moved = np.clip(beta0 + step * np.asarray(direction) * span,
                        model.beta_lo, model.beta_hi)
        return model.coeff_misfit(moved, target_coeffs)

    flat_misfits = [misfit_after(row) for row in model.identifiability(
        beta0).null_space]
    constrained = misfit_after(model.identifiability(beta0).identifiable[0])

    assert max(flat_misfits) < 1e-3              # flat: dose barely notices
    assert constrained > 100 * max(flat_misfits)  # constrained: dose cares a lot


def test_identifiability_rank_is_capped_by_num_components(tmp_path):
    """The structural claim: rank <= k. A k=1 surrogate constrains exactly one
    combination of β no matter how many β there are."""
    store = _collect_store(tmp_path, num_samples=32)
    ts = surrogate_data.load_training_store(store)
    model = DoseSurrogate.fit(ts.beta, ts.dose, k=1, seed=0,
                              beta_names=ts.beta_names,
                              voxel_indices=ts.indices)
    ident = model.identifiability(np.full(8, 50.0))
    assert model.num_components == 1
    assert ident.rank == 1
    assert ident.num_flat == 7


def test_minima_are_not_distinguishable_when_all_misfits_vanish(tmp_path):
    """Equal-misfit minima must NOT be presented as an evidence ranking."""
    model, _ts, _store = _trained(tmp_path, num_samples=48)
    result = model.invert(model.project(dose_of_beta(np.full(8, 51.0))),
                          num_starts=16, seed=0)
    assert result.num_distinct > 1
    assert result.minima_are_distinguishable() is False


def test_mode_writes_identifiability_report(tmp_path):
    store = _collect_store(tmp_path, num_samples=32)
    train_surrogate({'type': 'train_surrogate', 'store': store,
                     'variance': 0.999, 'seed': 0})
    result = invert_optimize({
        'type': 'invert_optimize', 'store': store,
        'target': os.path.join(store, 'sample_00000', 'field.npz'),
        'num_starts': 8, 'seed': 0})

    assert result.identifiability is not None
    report = os.path.join(store, 'identifiability.txt')
    assert os.path.isfile(report)
    text = open(report).read()
    # The preamble explains how to read it and names the flat directions.
    assert 'constrained' in text and 'flat' in text
    assert 'Retained POD modes' in text
    df = pd.read_csv(report, sep='\t', comment='#')
    assert list(df['beta']) == BETA_NAMES
    assert 'sensitivity' in df.columns
    # One column per constrained + flat direction.
    assert sum(c.startswith('constrained_') for c in df.columns) == \
        result.identifiability.rank
    assert sum(c.startswith('flat_') for c in df.columns) == \
        result.identifiability.num_flat


def test_mode_can_skip_identifiability(tmp_path):
    store = _collect_store(tmp_path, num_samples=16)
    train_surrogate({'type': 'train_surrogate', 'store': store, 'seed': 0})
    result = invert_optimize({
        'type': 'invert_optimize', 'store': store,
        'target': os.path.join(store, 'sample_00000', 'field.npz'),
        'num_starts': 4, 'seed': 0, 'identifiability': False})
    assert result.identifiability is None
    assert not os.path.isfile(os.path.join(store, 'identifiability.txt'))


# --------------------------------------------------------------------------- #
# Minimal YAMLs: the store-consuming modes need no 'workflow:' block at all.
# --------------------------------------------------------------------------- #


def _write_yaml(path, text):
    path.write_text(textwrap.dedent(text))
    return str(path)


def test_store_consuming_modes_run_without_a_workflow_block(tmp_path):
    """Both store-consuming YAMLs execute end-to-end through the CLI entry path
    with NO 'workflow:' key — the config declares only what the mode reads."""
    from lume_ace3p.inputs import load_yaml
    from lume_ace3p.run_lume_ace3p import _run_declarative

    store = _collect_store(tmp_path, num_samples=20)
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        train_yaml = _write_yaml(tmp_path / 'train.yaml', f"""\
            mode :
              type : train_surrogate
              store : '{store}'
              variance : 0.999
              seed : 0
        """)
        model = _run_declarative(load_yaml(train_yaml))
        assert isinstance(model, DoseSurrogate)

        invert_yaml = _write_yaml(tmp_path / 'invert.yaml', f"""\
            mode :
              type : invert_optimize
              store : '{store}'
              target : '{os.path.join(store, 'sample_00000', 'field.npz')}'
              num_starts : 6
              seed : 0
        """)
        result = _run_declarative(load_yaml(invert_yaml))
        assert isinstance(result, InversionResult)
    finally:
        os.chdir(cwd)


def test_shipped_example_yamls_carry_no_workflow(tmp_path):
    """The shipped train/invert examples are store-consuming and must stay free of
    a 'workflow:' block (that block was previously carried but never executed)."""
    from lume_ace3p.inputs import load_yaml
    from lume_ace3p.modes import is_store_consuming

    example_dir = os.path.join(os.path.dirname(__file__), '..', 'examples',
                               'geant4_beta_surrogate')
    for name in ('geant4_beta_surrogate_train.yaml',
                 'geant4_beta_surrogate_invert.yaml'):
        data = load_yaml(os.path.join(example_dir, name))
        assert data.get('workflow') is None, name
        assert is_store_consuming(data.get('mode')), name

    # The collection example DOES drive the chain, so it keeps its workflow.
    collect = load_yaml(os.path.join(example_dir, 'geant4_beta_surrogate.yaml'))
    assert collect.get('workflow') is not None
    assert not is_store_consuming(collect.get('mode'))


def test_non_store_modes_still_require_a_workflow(tmp_path):
    """Regression: relaxing the gate must not let a chain-driving mode through
    without a workflow."""
    from lume_ace3p.inputs import load_yaml
    from lume_ace3p.run_lume_ace3p import _run_declarative
    from lume_ace3p.workflow_graph import WorkflowValidationError

    for mode_type in ('single', 'parameter_sweep', 'collect_training_data'):
        path = _write_yaml(tmp_path / f'{mode_type}.yaml', f"""\
            mode :
              type : {mode_type}
        """)
        with pytest.raises(WorkflowValidationError):
            _run_declarative(load_yaml(path))


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
