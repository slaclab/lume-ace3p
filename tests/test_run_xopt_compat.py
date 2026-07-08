"""Environment-independent smoke tests that the S3P Xopt driver paths
construct, iterate, and write output files under xopt 3.0.0.

These do NOT require the ACE3P environment: `S3PWorkflow` is replaced with a
fake whose `.run()` returns a deterministic frequency/S-parameter table, so the
generator selection, random/step loops, and output writers in
`run_xopt` / `run_lf_sweep` are exercised directly.

Run with:  python -m pytest tests/test_run_xopt_compat.py -v
or standalone:  python tests/test_run_xopt_compat.py
"""
import os
import numpy as np

import lume_ace3p.run_xopt as rx


# Frequencies referenced by the example YAMLs so integer-index lookups succeed.
_FREQS = np.array([11.324e9, 11.424e9, 11.524e9, 12.0e9])


class FakeS3PWorkflow:
    """Stand-in for S3PWorkflow: returns a fixed frequency grid and smooth,
    input-dependent S-parameter values so the optimizer sees signal but never
    touches Cubit/S3P."""

    def __init__(self, workflow_dict, input_dict):
        self.input_dict = input_dict

    def run(self):
        # Deterministic response: a mild quadratic in the summed inputs, one
        # column per S-parameter the examples reference.
        x = float(sum(v for v in self.input_dict.values()))
        base = 0.01 * (1.0 + np.cos(_FREQS / 1e9 - x))
        return {
            'IndexMap': {},
            'Frequency': _FREQS,
            'S(0,0)': base,
            'S(1,1)': base * 0.9,
        }


def _run_in_tmp(tmp_path, fn):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return fn()
    finally:
        os.chdir(cwd)


def _patch(monkeypatch):
    monkeypatch.setattr(rx, 'S3PWorkflow', FakeS3PWorkflow)


def test_neldermead_no_random(tmp_path, monkeypatch):
    """scalar_optimize / NelderMead with num_random:0 must seed an initial
    point (the s3p_optimization.yaml case)."""
    _patch(monkeypatch)
    workflow_dict = {}
    vocs_dict = {
        'variables': {'cornercut': [14, 17], 'rcorner1': [0.5, 2.5]},
        'objectives': {'s_parameter': 'S(0,0)', 'frequency': 12.0e9,
                       'optimization': 'MINIMIZE'},
        'constraints': {}, 'observables': [], 'constants': {},
    }
    xopt_dict = {'generator': 'NelderMeadGenerator', 'num_random': 0, 'num_step': 5}
    _run_in_tmp(tmp_path, lambda: rx.run_xopt(workflow_dict, vocs_dict, xopt_dict))
    assert (tmp_path / 'sim_output.txt').exists()
    assert (tmp_path / 'sim_output_all_values.txt').exists()


def test_expected_improvement(tmp_path, monkeypatch):
    _patch(monkeypatch)
    workflow_dict = {}
    vocs_dict = {
        'variables': {'cornercut': [14, 17], 'rcorner1': [0.5, 2.5]},
        'objectives': {'s_parameter': 'S(0,0)', 'frequency': 12.0e9,
                       'optimization': 'MINIMIZE'},
        'constraints': {}, 'observables': [], 'constants': {},
    }
    xopt_dict = {'generator': 'ExpectedImprovementGenerator',
                 'num_random': 2, 'num_step': 3, 'save_model': True}
    _run_in_tmp(tmp_path, lambda: rx.run_xopt(workflow_dict, vocs_dict, xopt_dict))
    assert (tmp_path / 'sim_output.txt').exists()
    assert (tmp_path / 'Binary_gp_model.pt').exists()
    assert (tmp_path / 'gp_parameters.txt').exists()


def test_mobo(tmp_path, monkeypatch):
    _patch(monkeypatch)
    workflow_dict = {}
    vocs_dict = {
        'variables': {'R1': [31, 34], 'L1': [12, 15]},
        'objectives': {'s_parameter': ['S(0,0)', 'S(1,1)'],
                       'frequency': [11.324e9, 12.0e9],
                       'optimization': ['MINIMIZE', 'MINIMIZE']},
        'constraints': {}, 'observables': [], 'constants': {},
    }
    xopt_dict = {'generator': 'ExpectedHypervolumeImprovementGenerator',
                 'generator_options': {'reference_point': {'S(0,0)_11324000000.0': 1.0,
                                                            'S(1,1)_12000000000.0': 1.0}},
                 'num_random': 2, 'num_step': 2}
    _run_in_tmp(tmp_path, lambda: rx.run_xopt(workflow_dict, vocs_dict, xopt_dict))
    assert (tmp_path / 'sim_output.txt').exists()


def test_multifidelity(tmp_path, monkeypatch):
    """MultiFidelityGenerator via the cost-budget path (s3p_mf_optimization)."""
    _patch(monkeypatch)
    workflow_dict = {}
    vocs_dict = {
        'variables': {'cornercut': [12.5, 13.5], 'wgwidth': [21, 22]},
        'objectives': {'s_parameter': 'S(1,1)', 'frequency': 12.0e9,
                       'optimization': 'MINIMIZE', 'tolerance': 1e-3},
        'constraints': {}, 'observables': [], 'constants': {},
    }
    xopt_dict = {'generator': 'MultiFidelityGenerator',
                 'fidelity_variable': 'mesh_fidelity',
                 'cost_function': 'exponential',
                 'cost_budget': 5.0, 'num_random': 3}
    _run_in_tmp(tmp_path, lambda: rx.run_xopt(workflow_dict, vocs_dict, xopt_dict))
    assert (tmp_path / 'sim_output.txt').exists()


def test_gp_parameter_sweep(tmp_path, monkeypatch):
    """gp_parameter_sweep / BayesianExploration with an 'explore' objective
    (the s3p_bayesian_sweep.yaml case)."""
    _patch(monkeypatch)
    workflow_dict = {}
    sweep_dict = {'cornercut': {'min': 12.5, 'max': 13.5, 'num': 4},
                  'wgwidth': {'min': 21, 'max': 22, 'num': 4}}
    vocs_dict = {
        'variables': {'cornercut': [12.5, 13.5], 'wgwidth': [21, 22]},
        'objectives': {'S(1,1)_12.0e+09': 'explore'},
        'constraints': {}, 'observables': [], 'constants': {},
    }
    xopt_dict = {'num_random': 2, 'max_steps': 3}
    _run_in_tmp(tmp_path,
                lambda: rx.run_lf_sweep(workflow_dict, sweep_dict, vocs_dict, xopt_dict))
    assert (tmp_path / 'sweep_output.txt').exists()
    assert (tmp_path / 'sim_output.txt').exists()


if __name__ == '__main__':
    import tempfile
    import traceback

    class _MP:
        def __init__(self): self._saved = []
        def setattr(self, obj, name, val):
            self._saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._saved):
                setattr(obj, name, val)
            self._saved = []

    tests = [test_neldermead_no_random, test_expected_improvement, test_mobo,
             test_multifidelity, test_gp_parameter_sweep]
    passed = 0
    for t in tests:
        mp = _MP()
        with tempfile.TemporaryDirectory() as d:
            try:
                from pathlib import Path
                t(Path(d), mp)
                print(f"PASS  {t.__name__}")
                passed += 1
            except Exception:
                print(f"FAIL  {t.__name__}")
                traceback.print_exc()
            finally:
                mp.undo()
    print(f"\n{passed}/{len(tests)} passed")
    raise SystemExit(0 if passed == len(tests) else 1)
