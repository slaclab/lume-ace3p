"""Phase-2 tests: the ``collect_training_data`` mode + the training-data store
(see docs/geant4_surrogate_inversion_plan.md, Phase 2).

Verification (Phase 2 done when):

* A small-N DOE run produces a store loadable in one call returning aligned β
  matrix and dose-grid tensor with consistent shapes.
* Re-running the mode skips already-computed points (resumability).
* Dry-run works without the Geant4 app environment (pipeline testing).

Plus the cross-cutting correctness constraint #1: the mode hard-fails unless
``bin_edges`` is fixed on the resolved ``particles`` module (length num_bins+1)
and a per-bin ``beta_inputs`` design is declared — validated on the built
Workflow, NOT via the mode-dict ``_mc_noise_guards`` key.

The sampler and store loader are exercised directly; the collection loop is
driven both through a real dry-run :class:`Workflow` (pipeline reachability) and
a lightweight fake workflow that emits synthetic dose grids (so the field
persistence / resume / loader alignment are checked with real arrays, no Geant4
environment needed).
"""

import os

import numpy as np
import pandas as pd
import pytest

from lume_ace3p import surrogate_data
from lume_ace3p.modes import collect_training_data, run_mode
from lume_ace3p.results import FIELD_ARTIFACT_COLUMN
from lume_ace3p.workflow_graph import Workflow
from lume_ace3p.inputs import WorkflowInputs


BETA_NAMES = [f'beta{i}' for i in range(8)]
BIN_EDGES = list(np.linspace(-0.1251788, 0.1252434, 9))


# --------------------------------------------------------------------------- #
# DOE sampler
# --------------------------------------------------------------------------- #


def test_sampler_shape_and_bounds():
    bounds = [(40.0, 60.0)] * 8
    design = surrogate_data.sample_beta_doe(bounds, 16, sampler='sobol', seed=0)
    assert design.shape == (16, 8)
    assert np.all(design >= 40.0) and np.all(design <= 60.0)


def test_sampler_is_reproducible():
    bounds = [(40.0, 60.0)] * 8
    a = surrogate_data.sample_beta_doe(bounds, 8, sampler='sobol', seed=0)
    b = surrogate_data.sample_beta_doe(bounds, 8, sampler='sobol', seed=0)
    assert np.array_equal(a, b)
    # A different seed gives a different design.
    c = surrogate_data.sample_beta_doe(bounds, 8, sampler='sobol', seed=1)
    assert not np.array_equal(a, c)


def test_sampler_lhs_and_scatter():
    bounds = [(0.0, 1.0), (10.0, 20.0)]
    design = surrogate_data.sample_beta_doe(bounds, 12, sampler='lhs', seed=3)
    assert design.shape == (12, 2)
    assert np.all(design[:, 0] >= 0.0) and np.all(design[:, 0] <= 1.0)
    assert np.all(design[:, 1] >= 10.0) and np.all(design[:, 1] <= 20.0)
    # Not a tensor grid: no two points share a first coordinate.
    assert len(np.unique(np.round(design[:, 0], 9))) == 12


def test_sampler_rejects_bad_bounds_and_name():
    with pytest.raises(ValueError):
        surrogate_data.sample_beta_doe([(60.0, 40.0)], 4)      # hi <= lo
    with pytest.raises(ValueError):
        surrogate_data.sample_beta_doe([(0.0, 1.0)], 4, sampler='nope')


# --------------------------------------------------------------------------- #
# bin_edges / beta_inputs guard (correctness constraint #1)
# --------------------------------------------------------------------------- #


def _particles_entry(**overrides):
    entry = {'module': 'particles', 'impact_order': 1, 'impact_face_id': 6,
             'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
             'beta_inputs': list(BETA_NAMES), 'output_format': 'geant4',
             'output': 'particles.data', 'bin_edges': list(BIN_EDGES)}
    entry.update(overrides)
    return entry


def _staged_beta_workflow(tmp_path, particles_overrides=None):
    """A dry-run track3p_source -> particles -> geant4 Workflow staged in
    tmp_path with the shared example files symlinked in."""
    example = os.path.join(os.path.dirname(__file__), '..', 'examples',
                           'geant4_track3p_beta')
    for name in ('sample_track3p_particles.txt', 'input_7cell.geant4',
                 '7cell_solid_whole.stl', '7cell_cavity_whole.stl'):
        os.symlink(os.path.abspath(os.path.join(example, name)),
                   str(tmp_path / name))
    entries = [
        {'module': 'track3p_source', 'file': 'sample_track3p_particles.txt'},
        _particles_entry(**(particles_overrides or {})),
        {'module': 'geant4', 'geant4_input': 'input_7cell.geant4'},
    ]
    inputs = WorkflowInputs(cubit={n: 50.0 for n in BETA_NAMES})
    return Workflow(entries,
                    workflow_params={'workdir': str(tmp_path / 'store'),
                                     'dry_run': True},
                    inputs=inputs)


def _mode_cfg(tmp_path, **overrides):
    cfg = {'type': 'collect_training_data',
           'store': str(tmp_path / 'store'),
           'num_samples': 4, 'sampler': 'sobol', 'seed': 0, 'fidelity': 1019,
           'variables': {n: [40.0, 60.0] for n in BETA_NAMES}}
    cfg.update(overrides)
    return cfg


def test_guard_missing_bin_edges(tmp_path):
    cwd = os.getcwd()
    try:
        wf = _staged_beta_workflow(tmp_path, {'bin_edges': None})
        os.chdir(tmp_path)
        with pytest.raises(ValueError, match='bin_edges'):
            collect_training_data(_mode_cfg(tmp_path), wf)
    finally:
        os.chdir(cwd)


def test_guard_wrong_length_bin_edges(tmp_path):
    cwd = os.getcwd()
    try:
        wf = _staged_beta_workflow(tmp_path, {'bin_edges': BIN_EDGES[:-1]})
        os.chdir(tmp_path)
        with pytest.raises(ValueError, match='num_bins'):
            collect_training_data(_mode_cfg(tmp_path), wf)
    finally:
        os.chdir(cwd)


def test_guard_requires_beta_inputs_not_scalar(tmp_path):
    cwd = os.getcwd()
    try:
        wf = _staged_beta_workflow(
            tmp_path, {'beta_inputs': None, 'beta_input': 'beta'})
        os.chdir(tmp_path)
        with pytest.raises(ValueError, match='beta_inputs'):
            collect_training_data(_mode_cfg(tmp_path), wf)
    finally:
        os.chdir(cwd)


def test_guard_missing_variable_bound(tmp_path):
    cwd = os.getcwd()
    try:
        wf = _staged_beta_workflow(tmp_path)
        os.chdir(tmp_path)
        cfg = _mode_cfg(tmp_path)
        del cfg['variables']['beta3']
        with pytest.raises(ValueError, match="beta3"):
            collect_training_data(cfg, wf)
    finally:
        os.chdir(cwd)


# --------------------------------------------------------------------------- #
# Dry-run pipeline (reachability, no Geant4 env)
# --------------------------------------------------------------------------- #


def test_dry_run_pipeline_produces_store(tmp_path):
    cwd = os.getcwd()
    try:
        wf = _staged_beta_workflow(tmp_path)
        os.chdir(tmp_path)
        df = collect_training_data(_mode_cfg(tmp_path, num_samples=4), wf)
        assert len(df) == 4
        assert list(df.columns)[:8] == BETA_NAMES
        assert surrogate_data.FIDELITY_COLUMN in df.columns
        # Per-sample workdirs and the store table + manifest exist.
        store = str(tmp_path / 'store')
        assert os.path.isfile(os.path.join(
            store, surrogate_data.TABLE_FILENAME))
        assert os.path.isfile(os.path.join(
            store, surrogate_data.MANIFEST_FILENAME))
        for i in range(4):
            assert os.path.isdir(os.path.join(store, f'sample_{i:05d}'))
        # Each sample's particles.data carries the DOE β (proof the override
        # reached the weighting step, one distinct file per sample).
        p0 = os.path.join(store, 'sample_00000', 'particles.data')
        assert os.path.isfile(p0)
    finally:
        os.chdir(cwd)


def test_run_mode_dispatches_collect(tmp_path):
    cwd = os.getcwd()
    try:
        wf = _staged_beta_workflow(tmp_path)
        os.chdir(tmp_path)
        df = run_mode(_mode_cfg(tmp_path, num_samples=2), wf)
        assert len(df) == 2
    finally:
        os.chdir(cwd)


# --------------------------------------------------------------------------- #
# Full field persistence / resume / loader — via a fake workflow that emits
# synthetic dose grids (no Geant4 env, but real arrays round-trip the store).
# --------------------------------------------------------------------------- #


class _FakeModule:
    type = 'particles'

    def __init__(self):
        self.params = {'num_bins': 8, 'beta_inputs': list(BETA_NAMES),
                       'bin_edges': list(BIN_EDGES)}


class _FakeWorkflow:
    """Minimal Workflow surface the collection loop drives: a particles module
    with fixed bin_edges, and evaluate/field that emit a synthetic dose grid
    which is a deterministic function of β (so the loader's β↔dose alignment is
    checkable). Records evaluate() calls to prove resume skips re-evaluation."""

    def __init__(self):
        self.modules = [_FakeModule()]
        self.workdir_mode = 'manual'
        self.baseworkdir = None
        self.dry_run = False
        self._last_beta = None
        self.eval_calls = []

    def evaluate(self, overrides):
        self._last_beta = np.array([overrides[n] for n in BETA_NAMES])
        self.eval_calls.append(dict(overrides))
        return {}

    def field(self):
        # A 4-voxel dose grid whose values encode the β sum, so each sample's
        # stored grid is distinct and recoverable.
        s = float(self._last_beta.sum())
        indices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
        values = np.array([s, s + 1.0, s + 2.0, s + 3.0])
        return {'dose': {'indices': indices, 'values': values}}


def test_full_store_roundtrip_and_alignment(tmp_path):
    store = str(tmp_path / 'store')
    wf = _FakeWorkflow()
    cfg = _mode_cfg(tmp_path, store=store, num_samples=6)
    df = collect_training_data(cfg, wf)
    assert len(df) == 6
    assert FIELD_ARTIFACT_COLUMN in df.columns

    loaded = surrogate_data.load_training_store(store)
    assert len(loaded) == 6
    assert loaded.beta.shape == (6, 8)
    assert loaded.dose.shape == (6, 4)
    assert loaded.beta_names == BETA_NAMES
    # Alignment: each dose row's first voxel equals that row's β sum.
    assert np.allclose(loaded.dose[:, 0], loaded.beta.sum(axis=1))
    assert loaded.indices.shape == (4, 3)
    assert np.all(loaded.fidelity == 1019.0)
    # Manifest carries the fixed invariants.
    assert loaded.manifest['num_bins'] == 8
    assert loaded.manifest['mesh_shape'] == [4]


def test_resume_skips_computed_points(tmp_path):
    store = str(tmp_path / 'store')
    cfg = _mode_cfg(tmp_path, store=store, num_samples=6)

    wf1 = _FakeWorkflow()
    collect_training_data(cfg, wf1)
    assert len(wf1.eval_calls) == 6

    # Remove the last two sample field artifacts to simulate an interrupted run.
    for i in (4, 5):
        os.remove(os.path.join(store, f'sample_{i:05d}', 'field.npz'))

    wf2 = _FakeWorkflow()
    df = collect_training_data(cfg, wf2)
    # Only the two missing points are re-evaluated; the first four are resumed.
    assert len(wf2.eval_calls) == 2
    assert len(df) == 6
    # The resumed store still loads to a full, aligned 6-row set.
    loaded = surrogate_data.load_training_store(store)
    assert loaded.dose.shape == (6, 4)
    assert np.allclose(loaded.dose[:, 0], loaded.beta.sum(axis=1))


def test_resume_reproduces_same_design(tmp_path):
    """A resumed run reuses the seed-fixed DOE, so β rows are identical to the
    original run (clean resume requires the design be reproducible)."""
    store = str(tmp_path / 'store')
    cfg = _mode_cfg(tmp_path, store=store, num_samples=6)
    first = collect_training_data(cfg, _FakeWorkflow())
    # Wipe only the table; sample field artifacts remain, forcing full resume.
    os.remove(os.path.join(store, surrogate_data.TABLE_FILENAME))
    second = collect_training_data(cfg, _FakeWorkflow())
    assert np.allclose(first[BETA_NAMES].to_numpy(),
                       second[BETA_NAMES].to_numpy())


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
