"""Tests for per-bin training-data collection and the additive training mode
(plans/geant4_dose_campaign_plan.md, Phase 3 item 2).

The workflow double runs the real ``field_emission`` weighting on a small
synthetic Track3P dump, so the planned primaries, the written particle source
and bin isolation are all real. Geant4 is replaced by a dose grid that is
additive by construction: each primary deposits its weight into a Gaussian
centred on its emission bin, scored with a ``total(val^2)`` column.
"""

import os

import numpy as np
import pandas as pd
import pytest

from lume_ace3p import surrogate_data
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.modes import collect_training_data, invert_optimize, train_surrogate
from lume_ace3p.modules import (
    PARTICLE_SOURCE, FieldEmissionModule, RunContext, Track3PSourceModule)
from lume_ace3p.particles import TRACK3P_COLUMNS
from lume_ace3p.results import save_field
from lume_ace3p.surrogate import load_surrogate
from lume_ace3p.surrogate_additive import AdditiveSurrogate


NB = 4
EDGES = list(np.linspace(-0.125, 0.125, NB + 1))
GRID = [44.0, 48.0, 52.0, 56.0, 60.0]
BETA_NAMES = [f'beta{b}' for b in range(NB)]
NZ = 32
INDICES = np.stack([np.zeros(NZ, int), np.zeros(NZ, int), np.arange(NZ)], axis=1)
FE_PARAMS = {'impact_order': 1, 'impact_face_id': 6, 'work_function': 4.5,
             'fn_model': 'wang-loew', 'frequency': 1.0e10, 'num_bins': NB,
             'beta_inputs': BETA_NAMES, 'bin_edges': EDGES,
             'output_format': 'geant4', 'output': 'particles.data'}


def _write_dump(path, n=4000, seed=0):
    """A Track3P Initials-Impacts dump with the real example's field and area
    ranges, so the one-electron cut bites below beta ~52 as it does there."""
    rng = np.random.default_rng(seed)
    cols = {c: np.zeros(n) for c in TRACK3P_COLUMNS}
    z = rng.uniform(-0.12, 0.12, n)
    cols.update(ImpactOrder=np.ones(n), ImpactFaceID=np.full(n, 6.0),
                Initial_z=z, Impact_z=z, momentum_z=np.ones(n),
                ImpactEnergy=rng.uniform(1e3, 1e5, n),
                InitialNormalField=rng.uniform(3.15e7, 4.45e7, n),
                InitialFaceArea=rng.uniform(1e-7, 1.3e-6, n))
    np.savetxt(path, np.column_stack([cols[c] for c in TRACK3P_COLUMNS]))
    return str(path)


def _shape(z):
    """Dose profile of a primary emitted at axial position ``z``."""
    centre = (np.asarray(z)[:, None] + 0.125) / 0.25 * (NZ - 1)
    return np.exp(-0.5 * ((np.arange(NZ) - centre) / 2.0) ** 2) * 1e-12


class _PerBinWorkflow:
    """The real field_emission step, then a synthetic scorer with MC noise.

    ``evaluate`` records the overrides and the particle file it wrote, and
    ``field`` scores it: each event deposits one primary's weighted profile,
    ``hpm`` events per primary, with lognormal per-event noise."""

    def __init__(self, dump, **params):
        self.dump = dump
        self.fe = FieldEmissionModule(dict(FE_PARAMS, **params))
        self.modules = [Track3PSourceModule({'file': dump}), self.fe]
        self.dry_run = False
        self.calls = []

    def evaluate(self, overrides, workdir=None, resume=False):
        inputs = WorkflowInputs(particles={n: 50.0 for n in BETA_NAMES},
                                macro={}).apply_overrides(overrides)
        ctx = RunContext(workdir=workdir, inputs=inputs, stage_mode='copy')
        self.modules[0].run(ctx)
        self.fe.run(ctx)
        self.calls.append((dict(overrides), workdir))
        self._ctx = ctx
        self._hpm = int(inputs.macro['histories_per_macro'])
        return {}, ctx

    def field(self, ctx):
        rows = np.loadtxt(ctx.artifacts[PARTICLE_SOURCE], ndmin=2)
        weight, z = rows[:, 5], rows[:, 2]
        profiles = _shape(z) * weight[:, None]
        rng = np.random.default_rng(len(rows) * 7 + self._hpm)
        # hpm events per primary, each with weight W/hpm (dose-npass's rule).
        noise = rng.lognormal(0.0, 0.5, size=(self._hpm, len(rows)))
        noise /= np.exp(0.125)                       # unit mean
        per_event = profiles[None] * noise[:, :, None] / self._hpm
        values = per_event.sum(axis=(0, 1))
        val2 = (per_event ** 2).sum(axis=(0, 1))
        return {'dose': {'indices': INDICES, 'values': values, 'val2': val2}}


def _cfg(store, **overrides):
    cfg = {'type': 'collect_training_data', 'design': 'per_bin',
           'store': str(store), 'grid': GRID, 'events_per_run': 4000,
           'min_histories_per_macro': 2}
    cfg.update(overrides)
    return cfg


@pytest.fixture
def collected(tmp_path):
    dump = _write_dump(tmp_path / 'dump.txt')
    workflow = _PerBinWorkflow(dump)
    store = tmp_path / 'store'
    df = collect_training_data(_cfg(store), workflow)
    return store, df, workflow


# --------------------------------------------------------------------------- #
# Planning and collection
# --------------------------------------------------------------------------- #


def test_plan_counts_rows_per_bin_and_rises_to_saturation(tmp_path):
    dump = _write_dump(tmp_path / 'dump.txt')
    primaries = surrogate_data.plan_per_bin_primaries(
        dump, FE_PARAMS, np.array(GRID), 1.0)
    assert primaries.shape == (NB, len(GRID))
    assert np.all(np.diff(primaries, axis=1) >= 0)
    assert np.all(primaries[:, 0] < primaries[:, -1])      # onset below 52


def test_plan_rejects_a_grid_below_emission_and_a_leaky_off_beta(tmp_path):
    dump = _write_dump(tmp_path / 'dump.txt')
    with pytest.raises(ValueError, match='launch no primaries'):
        surrogate_data.plan_per_bin_primaries(dump, FE_PARAMS,
                                              np.array([20.0, 60.0]), 1.0)
    with pytest.raises(ValueError, match='does not switch bins off'):
        surrogate_data.plan_per_bin_primaries(dump, FE_PARAMS,
                                              np.array([50.0, 60.0]), 50.0)


def test_collection_runs_every_bin_and_records_fidelity(collected):
    store, df, workflow = collected
    assert len(df) == NB * len(GRID)
    assert list(df.columns[:1 + NB]) == ['bin'] + BETA_NAMES
    assert (df['events'] == df['primaries'] * df['histories_per_macro']).all()
    # Events per run near the target, hpm floored at 2.
    assert df['events'].min() >= 4000
    assert df['histories_per_macro'].min() >= 2
    # Each run isolates its bin, and its overrides reached the inputs.
    for overrides, workdir in workflow.calls:
        b = int(os.path.basename(os.path.dirname(workdir))[3:])
        on = [n for n in BETA_NAMES if overrides[f'particles:{n}'] != 1.0]
        assert on == [f'beta{b}']
        assert 'geant4:histories_per_macro' in overrides
    manifest = surrogate_data.load_per_bin_store(str(store)).manifest
    assert manifest['design'] == 'per_bin' and manifest['grid'] == GRID
    assert manifest['bin_edges'] == EDGES


def test_collection_skips_finished_runs(collected):
    store, df, _ = collected
    again = _PerBinWorkflow(_write_dump(os.path.join(store, '..', 'd2.txt')))
    collect_training_data(_cfg(store), again)
    assert again.calls == []


def test_fixed_hpm_and_bad_keys(tmp_path):
    dump = _write_dump(tmp_path / 'dump.txt')
    df = collect_training_data(
        _cfg(tmp_path / 's', events_per_run=None, histories_per_macro=3,
             grid={'min': 52.0, 'max': 60.0, 'num': 2}),
        _PerBinWorkflow(dump))
    assert (df['histories_per_macro'] == 3).all() and len(df) == 2 * NB
    with pytest.raises(ValueError, match='exactly one'):
        collect_training_data(_cfg(tmp_path / 't', histories_per_macro=3),
                              _PerBinWorkflow(dump))
    with pytest.raises(ValueError, match='unknown design'):
        collect_training_data(_cfg(tmp_path / 'u', design='tensor'),
                              _PerBinWorkflow(dump))
    with pytest.raises(ValueError, match='strictly increasing'):
        collect_training_data(_cfg(tmp_path / 'v', grid=[60.0, 50.0]),
                              _PerBinWorkflow(dump))


def test_written_source_must_match_the_plan(tmp_path, monkeypatch):
    """A run that weights the dump differently from the plan fails, so its
    recorded primaries cannot be wrong."""
    dump = _write_dump(tmp_path / 'dump.txt')
    workflow = _PerBinWorkflow(dump)
    real_plan = surrogate_data.plan_per_bin_primaries
    # The plan sees a lower work function than the run will use, so it
    # promises more primaries than the run writes.
    monkeypatch.setattr(
        surrogate_data, 'plan_per_bin_primaries',
        lambda path, params, grid, off: real_plan(
            path, dict(params, work_function=4.3), grid, off))
    with pytest.raises(ValueError, match='plan from the dump'):
        collect_training_data(_cfg(tmp_path / 's', on_error='raise'), workflow)


# --------------------------------------------------------------------------- #
# Loading and training
# --------------------------------------------------------------------------- #


def test_store_loads_as_fit_arrays(collected):
    store, df, _ = collected
    ps = surrogate_data.load_per_bin_store(str(store))
    assert ps.dose.shape == ps.variance.shape == (NB, len(GRID), NZ)
    assert np.all(ps.variance >= 0) and np.any(ps.variance > 0)
    for _, row in df.iterrows():
        b = int(row['bin'])
        i = GRID.index(row[f'beta{b}'])
        assert ps.primaries[b, i] == row['primaries']
        assert ps.histories_per_macro[b, i] == row['histories_per_macro']


def test_store_with_a_gap_names_the_missing_run(collected):
    store, df, _ = collected
    os.remove(os.path.join(store, 'bin2', 'beta48', 'field.npz'))
    table = df.copy()
    table.loc[(table['bin'] == 2) & (table['beta2'] == 48.0),
              'field_artifact'] = np.nan
    table.to_csv(os.path.join(store, 'training_table.txt'), sep='\t',
                 index=False)
    with pytest.raises(ValueError, match=r'\(2, 48.0\)'):
        surrogate_data.load_per_bin_store(str(store))


def test_doe_store_is_not_a_per_bin_store(tmp_path):
    surrogate_data.write_manifest(str(tmp_path), {'beta_names': BETA_NAMES})
    pd.DataFrame({'beta0': [1.0]}).to_csv(tmp_path / 'training_table.txt',
                                          sep='\t', index=False)
    with pytest.raises(ValueError, match="design 'doe'"):
        surrogate_data.load_per_bin_store(str(tmp_path))


def test_train_additive_and_invert_end_to_end(collected, tmp_path):
    store, _df, workflow = collected
    model = train_surrogate({'type': 'train_surrogate', 'model': 'additive',
                             'store': str(store), 'shape_beta_min': 48})
    assert isinstance(model, AdditiveSurrogate)
    assert isinstance(load_surrogate(os.path.join(store, 'surrogate')),
                      AdditiveSurrogate)
    # A full-chain target at known beta, scored the same way.
    truth = [58.0, 46.0, 55.0, 46.0]
    inputs = {f'particles:{n}': v for n, v in zip(BETA_NAMES, truth)}
    inputs['geant4:histories_per_macro'] = 20
    _out, ctx = workflow.evaluate(inputs, workdir=str(tmp_path / 'target'))
    target = str(tmp_path / 'target.npz')
    save_field(workflow.field(ctx), target)
    result = invert_optimize({'type': 'invert_optimize',
                              'model_dir': os.path.join(store, 'surrogate'),
                              'target': target, 'target_histories_per_macro': 20,
                              'output_file': str(tmp_path / 'inv.txt')})
    sigma = result.identifiability.beta_sigma
    visible = sigma < 1.0
    assert visible[0] and visible[2]
    assert np.all(np.abs(result.beta - truth)[visible] < 3 * sigma[visible] + 0.2)


def test_train_rejects_unknown_model(collected):
    store, _df, _ = collected
    with pytest.raises(ValueError, match="unknown model"):
        train_surrogate({'type': 'train_surrogate', 'model': 'kriging',
                         'store': str(store)})


# --------------------------------------------------------------------------- #
# Parser and override plumbing
# --------------------------------------------------------------------------- #


def test_read_dose_file_keeps_val2(tmp_path):
    path = tmp_path / 'dose.txt'
    path.write_text('# iX, iY, iZ, total(value) [Gy], total(val^2), entry\n'
                    '0, 0, 0, 2.0, 1.5, 3\n0, 0, 1, 1.0, 0.25, 1\n')
    grid = surrogate_data.read_dose_file(str(path))
    assert np.allclose(grid['val2'], [1.5, 0.25])
    assert np.allclose(surrogate_data.voxel_variance(grid['values'],
                                                     grid['val2'], 4),
                       [0.5, 0.0])
    plain = tmp_path / 'plain.txt'
    plain.write_text('0 0 0 2.0\n')
    assert 'val2' not in surrogate_data.read_dose_file(str(plain))


def test_qualified_override_reaches_an_undeclared_bucket():
    out = WorkflowInputs().apply_overrides({'geant4:histories_per_macro': 7,
                                            'particles:beta9': 1.0})
    assert out.macro == {'histories_per_macro': 7}
    assert out.particles == {'beta9': 1.0}
    assert out.cubit == {}
