"""Environment-independent tests for the generic, workflow-agnostic Xopt modes.

These drive `lume_ace3p.modes.scalar_optimize` / `gp_parameter_sweep` through the
`Workflow.evaluate(input_dict)` + declarative `output_parameters` seam, with NO
S-parameter/frequency parsing in the driver. A synthetic workflow supplies a
deterministic, input-dependent S-parameter response, so the generator selection,
random/step loops, and result logging are exercised without an ACE3P env.

The botorch-backed GP-fitting tests (ExpectedImprovement / MOBO / MultiFidelity /
UCB constructing + stepping a real model, and the BayesianExploration GP sweep)
were **removed** in 2026-08. They asserted almost nothing about this repo — mostly
`len(X.data) == 3` after a generator stepped — while taking hours: a single run of
the MultiFidelity test did not finish in 2h at ~1000% CPU, because its
`cost_budget` loop terminates on *measured runtimes* rather than an iteration
count. They exercised xopt/botorch internals we do not edit, and their cost meant
they were never actually run, so they provided no real gate. Generator *selection*
(the part that is ours) is covered by `test_mc_noise_guard_skips_low_noise_prior`,
which asserts on `modes._build_generator` directly in well under a second.

What remains here is fast and all of it tests our own code: the NelderMead
trajectory match against the frozen baseline, a Geant4 chain as the objective, and
the MC-noise mode-config guards.

Run:  python -m pytest tests/test_run_xopt_compat.py
"""
import os
import numpy as np


def _run_in_tmp(tmp_path, fn):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return fn()
    finally:
        os.chdir(cwd)


# =========================================================================== #
# The generic, workflow-agnostic Xopt modes in `modes.py`.
#
# These drive `lume_ace3p.modes.scalar_optimize` / `gp_parameter_sweep` through
# the `Workflow.evaluate(input_dict)` + declarative `output_parameters` seam,
# with NO S-parameter/frequency parsing in the driver. Two things are checked:
#
#   * NUMERIC EQUIVALENCE — the S3P optimization trajectory reproduces the
#     Phase-0.5 baseline exactly when driven generically with a synthetic workflow
#     (the same synthetic solver the baseline was frozen with).
#   * GENERICITY — the generic driver steps a generator, and a Geant4 workflow can
#     be the objective with no S3P-specific code.
# =========================================================================== #

import pytest

import baseline_utils as bu
from lume_ace3p import modes
from lume_ace3p.workflow_graph import Workflow
from lume_ace3p.inputs import WorkflowInputs


class SynthWorkflow:
    """A stand-in :class:`Workflow` for the generic Xopt modes.

    Exposes the single seam the modes use — ``evaluate(input_dict) ->
    ({objective_name: scalar}, ctx)`` — computed from the same deterministic
    synthetic S-parameter response the Phase-0.5 baselines were frozen with
    (``baseline_utils.SyntheticWorkflow``). ``output_spec`` maps each declared
    objective name to the ``(s_parameter, frequency)`` it extracts, standing in
    for a real workflow's ``output_parameters`` + ``S3PModule.extract``. This
    keeps all S-parameter knowledge inside the (fake) *workflow*, proving the
    mode itself is workflow-agnostic."""

    def __init__(self, output_spec):
        self.output_spec = output_spec

    def evaluate(self, input_dict):
        x = float(sum(float(v) for v in input_dict.values()))
        base = 0.01 * (1.0 + np.cos(bu.SYNTH_FREQS / 1e9 - x))
        data = {'Frequency': bu.SYNTH_FREQS, 'S(0,0)': base, 'S(1,1)': base * 0.9}
        out = {}
        for name, (sparam, freq) in self.output_spec.items():
            idx = list(bu.SYNTH_FREQS).index(float(freq))
            out[name] = data[sparam][idx]
        return out, None


def _single_obj_workflow():
    return SynthWorkflow({'obj': ('S(0,0)', 12.0e9)})


_SINGLE_VOCS = {
    'variables': {'cornercut': [14, 17], 'rcorner1': [0.5, 2.5]},
    'objectives': {'obj': 'MINIMIZE'},
}


# ---- numeric equivalence vs the Phase-0.5 baselines ---------------------- #


def test_generic_s3p_optimization_matches_baseline(tmp_path):
    """scalar_optimize / NelderMead driven generically reproduces the frozen
    s3p_optimization trajectory (cornercut, rcorner1, objective) numerically."""
    # Objective name matches the baseline column so compare_tables lines up.
    objname = 'S(0,0)_' + str(12.0e+09)
    wf = SynthWorkflow({objname: ('S(0,0)', 12.0e9)})
    vocs = {'variables': {'cornercut': [14, 17], 'rcorner1': [0.5, 2.5]},
            'objectives': {objname: 'MINIMIZE'}}
    xopt = {'generator': 'NelderMeadGenerator', 'num_random': 0, 'num_step': 25}

    def run():
        bu.seed_all()
        modes.scalar_optimize(wf, vocs, xopt, log_file='sim_output.txt')
    _run_in_tmp(tmp_path, run)

    baseline = os.path.join(bu.BASELINE_DIR, 's3p_optimization', 'sim_output.txt')
    ok, msg = bu.compare_tables(baseline, str(tmp_path / 'sim_output.txt'))
    assert ok, msg


# ---- the gradient-free generator constructs + steps under the driver ----- #


def test_generic_neldermead(tmp_path):
    def run():
        bu.seed_all()
        return modes.scalar_optimize(
            _single_obj_workflow(), _SINGLE_VOCS,
            {'generator': 'NelderMeadGenerator', 'num_random': 0,
             'num_step': 3}, log_file='sim_output.txt')
    X = _run_in_tmp(tmp_path, run)
    assert 'obj' in X.data.columns and len(X.data) >= 3
    assert (tmp_path / 'sim_output.txt').exists()


# ---- Geant4 workflow as the objective (no S3P-specific code) ------------- #


def test_generic_geant4_objective_dry_run(tmp_path):
    """A Geant4 chain (track3p_source -> particles -> geant4) is driven as the
    scalar_optimize objective in dry-run. The objective (`total_weight` off the
    real Particles pre-step) is a genuine number even with the Geant4 binary
    absent, so this proves evaluate->objective wiring works with zero
    S3P-specific code in the driver."""
    staged = bu._stage_example('geant4_track3p_beta')

    def run():
        entries = [
            {'module': 'track3p_source', 'file': '../assets/sample_track3p_particles.txt'},
            {'module': 'particles', 'impact_order': 1, 'impact_face_id': 6,
             'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
             'beta_input': 'beta', 'output_format': 'geant4',
             'output': 'particles.data'},
            {'module': 'geant4', 'geant4_input': 'input_7cell.geant4',
             'geant4_geometry_files': ['../assets/7cell_solid_whole.stl',
                                       '../assets/7cell_cavity_whole.stl']},
        ]
        wf = Workflow(entries,
                      workflow_params={'workdir': 'wd', 'workdir_mode': 'auto',
                                       'dry_run': True},
                      inputs=WorkflowInputs(),
                      output_spec={'weight': {'module': 'particles',
                                              'quantity': 'total_weight'}})
        # evaluate -> objective returns a real scalar under dry-run.
        out, _ctx = wf.evaluate({'beta': 50.0})
        assert np.isfinite(out['weight']) and out['weight'] > 0
        return modes.scalar_optimize(
            wf, {'variables': {'beta': [40.0, 60.0]},
                 'objectives': {'weight': 'MINIMIZE'}},
            {'generator': 'NelderMeadGenerator', 'num_random': 0,
             'num_step': 3}, log_file='sim_output.txt')

    X = _run_in_tmp(staged, run)
    assert 'weight' in X.data.columns
    assert np.all(np.isfinite(X.data['weight'].values))
    assert 'beta' in X.data.columns


# ---- Geant4 MC-noise mode-config guards ---------------------------------- #


def test_mc_noise_guard_requires_bin_edges():
    """An MC-noisy objective must fix its binning: mc_noisy_objective without
    an explicit bin_edges is a clear error."""
    wf = _single_obj_workflow()
    with pytest.raises(ValueError, match='bin_edges'):
        modes.scalar_optimize(
            wf, _SINGLE_VOCS,
            {'generator': 'NelderMeadGenerator', 'num_step': 1,
             'mc_noisy_objective': True})


def test_mc_noise_guard_skips_low_noise_prior():
    """For an MC-noisy objective the MultiFidelity path must NOT force
    use_low_noise_prior (that prior is wrong for genuine MC noise); for a smooth
    objective it still must.

    This asserts on :func:`modes._build_generator` — *our* constraint-#2 logic —
    rather than driving a full optimization loop. Same coverage, ~0.5s instead of
    minutes, and it no longer depends on botorch actually fitting a model."""
    from xopt.vocs import VOCS

    vocs_dict = {'variables': {'cornercut': [12.5, 13.5], 'wgwidth': [21, 22]},
                 'objectives': {'obj': 'MINIMIZE'}}
    vocs = VOCS(variables=vocs_dict['variables'],
                objectives=vocs_dict['objectives'])
    xopt_dict = {'generator': 'MultiFidelityGenerator',
                 'fidelity_variable': 'mesh_fidelity'}

    noisy = modes._build_generator(vocs, vocs_dict, xopt_dict, mc_noisy=True)
    assert noisy.gp_constructor.use_low_noise_prior is False
    smooth = modes._build_generator(vocs, vocs_dict, xopt_dict, mc_noisy=False)
    assert smooth.gp_constructor.use_low_noise_prior is True


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
