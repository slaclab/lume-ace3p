"""Phase-5 tests: the consolidated hybrid result model (see
docs/workflow_module_refactor_plan.md).

Verification (Phase 5 done when):

* **All three modes emit their result DataFrame via one shared code path.**
  ``parameter_sweep`` / ``single`` write through ``results.write_table``, the
  Xopt log (``modes._log_xopt``) writes through it, and ``gp_parameter_sweep``
  builds its sweep as a DataFrame written through it. Here we assert every mode
  routes through the single ``results.write_table`` seam (patched to observe the
  calls), with no S3P-specific hand-rolled writer left in the path.
* **Field artifacts for a given row load back to the same arrays.**
  ``results.save_field`` / ``load_field`` round-trip both an S3P spectrum
  (frequency + S-parameter arrays + nested IndexMap) and a Geant4 voxel grid
  ({indices, values}) exactly; a sweep that produces a field records a
  ``field_artifact`` handle per row that reloads to the module's field.
* **No remaining callers of the old dict ``sweep_data`` tuple-keyed structure**
  in the new (module/workflow/mode/results) code path — only the legacy
  ``workflow.py`` subclasses (kept callable through Phase 5) still use it.
"""

import os

import numpy as np
import pandas as pd
import pytest

import baseline_utils as bu
from lume_ace3p import results
from lume_ace3p.results import (
    write_table, save_field, load_field, FIELD_ARTIFACT_COLUMN,
)
from lume_ace3p.modules import (
    RunContext, S3PModule, Geant4Module, PARTICLE_SOURCE,
)
from lume_ace3p.workflow_graph import Workflow
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p import modes


# --------------------------------------------------------------------------- #
# Fixtures reused from the Phase-1 module tests (synthetic solver outputs).
# --------------------------------------------------------------------------- #

S3P_REFLECTION = """\
#Index information
#0  Port 1, Mode 0, Type: (TE) cutoff: 1.000000e9 Hz
#1  Port 2, Mode 0, Type: (TE) cutoff: 1.000000e9 Hz
#Frequency  S(0,0) S(0,1) S(1,0) S(1,1)
12.0e9 0.10 0.20 0.30 0.40
12.5e9 0.50 0.60 0.70 0.80
13.0e9 0.90 1.00 1.10 1.20
"""

GEANT4_INPUT = """\
particles = particles.data
nthreads = 4
output_dose = dose.out
output_edep = edep.out
"""
DOSE_OUT = "0 0 0 1.0\n0 0 1 2.0\n1 0 0 5.0\n"
EDEP_OUT = "0 0 0 0.5\n0 0 1 1.5\n1 0 0 3.0\n"


def _write(path, text):
    with open(path, 'w') as f:
        f.write(text)


def _make_s3p_solver(workdir):
    from lume_ace3p.ace3p import S3P
    os.makedirs(os.path.join(workdir, 's3p_results'), exist_ok=True)
    _write(os.path.join(workdir, 's3p_results', 'Reflection.out'), S3P_REFLECTION)
    dummy = os.path.join(workdir, 'dummy.s3p')
    _write(dummy, '')
    s3p = S3P(dummy, workdir=workdir)
    s3p.output_parser()
    return s3p


def _stage_geant4(workdir):
    os.makedirs(workdir, exist_ok=True)
    _write(os.path.join(workdir, 'input.geant4'), GEANT4_INPUT)
    _write(os.path.join(workdir, 'dose.out'), DOSE_OUT)
    _write(os.path.join(workdir, 'edep.out'), EDEP_OUT)
    psrc = os.path.join(workdir, 'particles.data')
    _write(psrc, '0.0 0.0 0.0 0.0 1.0 1 0 0 1 6\n')
    return os.path.join(workdir, 'input.geant4'), psrc


# --------------------------------------------------------------------------- #
# Field-artifact round-trip
# --------------------------------------------------------------------------- #


def test_save_load_field_s3p_spectrum(tmp_path):
    """An S3P spectrum (numeric arrays + a nested IndexMap dict) round-trips
    through save_field/load_field to the same arrays."""
    wd = str(tmp_path / 'wd')
    s3p = _make_s3p_solver(wd)
    module = S3PModule({'input': 'dummy.s3p'})
    module._solver = s3p
    field = module.field(RunContext(wd))
    assert field is not None

    handle = save_field(field, os.path.join(str(tmp_path), 'field_0'))
    assert os.path.isfile(handle)
    loaded = load_field(handle)

    assert np.allclose(loaded['Frequency'], field['Frequency'])
    for skey in ('S(0,0)', 'S(0,1)', 'S(1,0)', 'S(1,1)'):
        assert np.allclose(loaded[skey], field[skey]), skey
    # Nested IndexMap dict survives (values rehydrated).
    assert set(loaded['IndexMap'].keys()) == set(field['IndexMap'].keys())


def test_save_load_field_geant4_grid(tmp_path):
    """A Geant4 voxel grid ({indices, values} per section) round-trips exactly."""
    wd = str(tmp_path / 'wd')
    input_path, psrc = _stage_geant4(wd)
    ctx = RunContext(wd, inputs=WorkflowInputs(),
                     artifacts={PARTICLE_SOURCE: psrc}, dry_run=True,
                     paths={'ace3p': '', 'mpi': '', 'geant4_app_path': '',
                            'geant4_app_exe': ''})
    module = Geant4Module({'geant4_input': input_path})
    module.run(ctx)
    field = module.field(ctx)
    assert set(field) == {'dose', 'edep'}

    handle = save_field(field, os.path.join(str(tmp_path), 'g4'))
    loaded = load_field(handle)
    for section in ('dose', 'edep'):
        assert np.array_equal(loaded[section]['indices'],
                              field[section]['indices'])
        assert np.allclose(loaded[section]['values'], field[section]['values'])


def test_save_field_empty_returns_none(tmp_path):
    assert save_field(None, str(tmp_path / 'x')) is None
    assert save_field({}, str(tmp_path / 'x')) is None


def test_load_field_empty_handle_is_none():
    assert load_field(None) is None
    assert load_field('') is None
    assert load_field(float('nan')) is None


# --------------------------------------------------------------------------- #
# The three modes route through the one shared writer
# --------------------------------------------------------------------------- #


def _s3p_workflow(tmp_path, dry_run=True):
    entries = [{'module': 'cubit', 'journal': 'bend-90degree.jou'},
               {'module': 's3p', 'input': 'bend-90degree.s3p'}]
    return Workflow(entries,
                    workflow_params={'workdir': str(tmp_path / 'wd'),
                                     'workdir_mode': 'manual',
                                     'dry_run': dry_run},
                    inputs=WorkflowInputs(cubit={'cornercut': 13.0}),
                    output_spec={})


def test_parameter_sweep_writes_through_shared_path(tmp_path, monkeypatch):
    """run_mode(parameter_sweep) writes its table through results.write_table —
    the single shared seam — not a bespoke writer."""
    calls = []
    real = results.write_table
    monkeypatch.setattr(results, 'write_table',
                        lambda df, fn: calls.append((df, fn)) or real(df, fn))
    # modes imported write_table by name; patch there too so the mode's call is
    # observed.
    monkeypatch.setattr(modes, 'write_table',
                        lambda df, fn: calls.append((df, fn)) or real(df, fn))

    wf = _s3p_workflow(tmp_path)
    out = str(tmp_path / 'sweep.txt')
    modes.run_mode({'type': 'parameter_sweep', 'output_file': out}, wf)
    assert calls, 'parameter_sweep did not route through the shared writer'
    assert os.path.isfile(out)
    assert isinstance(calls[-1][0], pd.DataFrame)


def test_single_writes_through_shared_path(tmp_path, monkeypatch):
    calls = []
    real = results.write_table
    monkeypatch.setattr(modes, 'write_table',
                        lambda df, fn: calls.append((df, fn)) or real(df, fn))
    wf = _s3p_workflow(tmp_path)
    out = str(tmp_path / 'single.txt')
    modes.run_mode({'type': 'single', 'output_file': out}, wf)
    assert calls and os.path.isfile(out)


def test_log_xopt_uses_shared_writer(tmp_path, monkeypatch):
    """The Xopt log path (scalar_optimize / gp_parameter_sweep) writes through
    the same shared writer."""
    calls = []
    real = results.write_table
    monkeypatch.setattr(modes, 'write_table',
                        lambda df, fn: calls.append((df, fn)) or real(df, fn))

    class FakeX:
        data = pd.DataFrame({'a': [1, 2], 'obj': [0.1, 0.2]})
    out = str(tmp_path / 'sim.txt')
    modes._log_xopt(out, FakeX())
    assert len(calls) == 1
    assert calls[0][1] == out
    ok = pd.read_csv(out, sep=r'\s+')
    assert list(ok.columns) == ['a', 'obj']


def test_gp_sweep_frame_matches_baseline(tmp_path):
    """The GP posterior-mean sweep, now built as a DataFrame and written through
    the shared writer, still reproduces the Phase-0.5 s3p_bayesian_sweep
    baseline numerically (single-target, 10x10 grid)."""
    # This is the same check as test_run_xopt_compat's generic GP-sweep test but
    # asserts the *shared-path* output; it fits GPs so it is a slower test.
    objname = 'S(1,1)_12.0e+09'

    class SynthWorkflow:
        output_spec = {objname: ('S(1,1)', 12.0e9)}

        def evaluate(self, input_dict):
            x = float(sum(float(v) for v in input_dict.values()))
            base = 0.01 * (1.0 + np.cos(bu.SYNTH_FREQS / 1e9 - x))
            data = {'Frequency': bu.SYNTH_FREQS, 'S(1,1)': base * 0.9}
            idx = list(bu.SYNTH_FREQS).index(12.0e9)
            return {objname: data['S(1,1)'][idx]}

    sweep = {'cornercut': {'min': 12.5, 'max': 13.5, 'num': 10},
             'wgwidth': {'min': 21, 'max': 22, 'num': 10}}
    vocs = {'variables': {'cornercut': [12.5, 13.5], 'wgwidth': [21, 22]},
            'objectives': {objname: 'explore'}}
    xopt = {'num_step': 3}

    cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        bu.seed_all()
        modes.gp_parameter_sweep(SynthWorkflow(), sweep, vocs, xopt,
                                 log_file='sim_output.txt',
                                 sweep_file='sweep_output.txt')
    finally:
        os.chdir(cwd)

    base_dir = os.path.join(bu.BASELINE_DIR, 's3p_bayesian_sweep')
    ok, msg = bu.compare_tables(os.path.join(base_dir, 'sweep_output.txt'),
                                str(tmp_path / 'sweep_output.txt'))
    assert ok, f'sweep_output: {msg}'


# --------------------------------------------------------------------------- #
# Field-artifact column in a real (Geant4) sweep
# --------------------------------------------------------------------------- #


def test_geant4_sweep_records_loadable_field_artifacts(tmp_path):
    """A wide sweep whose workflow produces a structured field records a
    ``field_artifact`` handle per row; each handle reloads to the grid the
    module produced. Uses a stub workflow with a real Geant4 field so the check
    does not need the Geant4 binary."""
    wd = str(tmp_path / 'wd')
    input_path, psrc = _stage_geant4(wd)
    ctx = RunContext(wd, inputs=WorkflowInputs(),
                     artifacts={PARTICLE_SOURCE: psrc}, dry_run=True,
                     paths={'ace3p': '', 'mpi': '', 'geant4_app_path': '',
                            'geant4_app_exe': ''})
    g4 = Geant4Module({'geant4_input': input_path})
    g4.run(ctx)
    expected = g4.field(ctx)

    class StubWorkflow:
        """Minimal Workflow surface the sweep modes use, with two grid points
        and a fixed Geant4 field (the binary is absent, so we reuse the parsed
        grid for each point)."""
        output_spec = {}

        def __init__(self):
            self.workdir = wd

        def sweep_axes(self):
            def setter(materialized, scalar):
                pass
            return [('p', np.array([1.0, 2.0]), setter)]

        def evaluate(self, scalars):
            return {}

        def field_index(self):
            return None

        def field(self):
            return expected

    df = modes.parameter_sweep(StubWorkflow())
    assert FIELD_ARTIFACT_COLUMN in df.columns
    assert len(df) == 2
    for handle in df[FIELD_ARTIFACT_COLUMN]:
        loaded = load_field(handle)
        for section in ('dose', 'edep'):
            assert np.allclose(loaded[section]['values'],
                               expected[section]['values'])


def test_s3p_long_format_has_no_field_artifact_column(tmp_path):
    """The S3P long-format sweep explodes its field into rows (via
    field_index), so it must NOT also carry a field-artifact column."""
    wf = _s3p_workflow(tmp_path)  # dry-run S3P -> field_index present
    df = modes.parameter_sweep(wf)
    assert 'Frequency' in df.columns
    assert FIELD_ARTIFACT_COLUMN not in df.columns


# --------------------------------------------------------------------------- #
# No sweep_data in the new code path
# --------------------------------------------------------------------------- #


def test_new_code_path_has_no_sweep_data():
    """The old dict `sweep_data` tuple-keyed structure must not be *used* in the
    module/workflow/mode/results code path (only the legacy workflow.py
    subclasses, kept callable through Phase 5, still use it).

    We scan for actual code references — an attribute/name ``sweep_data`` in a
    non-comment, non-docstring line — rather than the word in prose (this file
    and the mode docstring legitimately mention the retired structure by name)."""
    import ast

    import lume_ace3p.modes as m
    import lume_ace3p.results as r
    import lume_ace3p.modules as mod
    import lume_ace3p.workflow_graph as wg

    def names_used(module):
        tree = ast.parse(open(module.__file__).read())
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
        return used

    for module in (m, r, mod, wg):
        assert 'sweep_data' not in names_used(module), (
            f'{module.__name__} still uses the retired sweep_data structure')


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
