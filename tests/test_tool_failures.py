"""A tool that fails is a failed step (Phase 1 of
``plans/failure_mitigation_plan.md``).

Every wrapper raises :class:`lume_ace3p.logs.ToolExitError` on a nonzero exit,
so the module loop of :meth:`Workflow.evaluate` records the step ``failed`` with
the status and the command in ``error``. Two failures a clean exit does not
cover are caught too: a Cubit run that wrote no mesh, and a point whose modules
all completed but whose outputs could not be extracted — recorded as a
point-level ``error`` on the manifest.

These run real subprocesses through the fake binaries of ``test_resume.py``,
with one binary replaced per test by a failing variant.
"""

import os

import pytest

from lume_ace3p import modes, state
from lume_ace3p.logs import ToolExitError, check_exit, run_logged

from test_resume import (_invocations, _transwake_workflow, _workflow,
                         posix_only, staged)  # noqa: F401  (fixture)


def _replace_binary(root, name, body):
    script = root / 'bin' / name
    script.write_text('#!/bin/sh\n' + body)
    script.chmod(0o755)


def _recorded(workdir):
    return {e['name']: e for e in state.read_state(workdir)['modules']}


# A Cubit that dies before writing anything.
_CUBIT_CRASH = 'echo "cubit: segmentation fault" >&2\nexit 3\n'

# A Cubit that exits cleanly but writes nothing — what a journal whose export
# line names the wrong file, or whose geometry commands failed, amounts to.
_CUBIT_NO_MESH = 'echo "cubit: nothing to export"\n'

# An Omega3P that writes its output and then crashes on the way out.
_OMEGA3P_LATE_CRASH = """dir=${2:-omega3p_results}
mkdir -p "$dir"
printf 'Mode : {\\n  ModeIndex : 0\\n  Frequency : 1.0\\n}\\n' > "$dir/omega3p.out"
exit 9
"""

# An Omega3P that exits 0 and writes nothing.
_OMEGA3P_SILENT = 'echo "omega3p: done"\n'


@posix_only
def test_a_crashed_cubit_is_recorded_failed(staged):
    wf = _workflow(staged, [100.0])
    _replace_binary(staged, 'cubit', _CUBIT_CRASH)
    with pytest.raises(ToolExitError, match='cubit exited with status 3'):
        modes.parameter_sweep(wf, on_error='raise')

    workdir = str(staged / 'wd') + '_0'
    recorded = _recorded(workdir)
    assert recorded['cubit']['status'] == 'failed'
    assert 'ToolExitError: cubit exited with status 3' in recorded['cubit']['error']
    assert 'cubit -nographics' in recorded['cubit']['error']
    assert 'omega3p' not in recorded


@posix_only
def test_a_crashed_cubit_does_not_reuse_an_earlier_mesh(staged):
    """The silent-wrong-data case: a mesh from an earlier run of this point sits
    in the workdir, Cubit crashes. Before, the solver read the old mesh and the
    row held its answer; now the point fails at Cubit."""
    modes.parameter_sweep(_workflow(staged, [200.0]))
    workdir = str(staged / 'wd') + '_0'
    assert os.path.isfile(os.path.join(workdir, 'cavity.gen'))

    wf = _workflow(staged, [250.0])
    _replace_binary(staged, 'cubit', _CUBIT_CRASH)
    with pytest.raises(ToolExitError):
        modes.parameter_sweep(wf, on_error='raise')
    assert _recorded(workdir)['cubit']['status'] == 'failed'
    assert _invocations(workdir, 'omega3p') == 1     # the first run's only


@posix_only
def test_a_cubit_that_writes_no_mesh_is_recorded_failed(staged):
    wf = _workflow(staged, [100.0])
    _replace_binary(staged, 'cubit', _CUBIT_NO_MESH)
    with pytest.raises(RuntimeError, match='wrote no mesh'):
        modes.parameter_sweep(wf, on_error='raise')

    workdir = str(staged / 'wd') + '_0'
    recorded = _recorded(workdir)
    assert recorded['cubit']['status'] == 'failed'
    assert os.path.join(workdir, 'cavity.gen') in recorded['cubit']['error']
    assert 'cubit.log' in recorded['cubit']['error']
    assert 'omega3p' not in recorded


@posix_only
def test_a_solver_exit_wins_over_the_output_it_wrote(staged):
    wf = _workflow(staged, [100.0])
    _replace_binary(staged, 'omega3p', _OMEGA3P_LATE_CRASH)
    with pytest.raises(ToolExitError, match='omega3p exited with status 9'):
        modes.parameter_sweep(wf, on_error='raise')

    workdir = str(staged / 'wd') + '_0'
    recorded = _recorded(workdir)
    assert recorded['cubit']['status'] == 'complete'
    assert recorded['omega3p']['status'] == 'failed'
    assert 'refused the step or the solver crashed' in recorded['omega3p']['error']
    assert os.path.isfile(os.path.join(workdir, 'omega3p_results', 'omega3p.out'))


@posix_only
def test_an_extraction_failure_is_a_point_level_error(staged, capsys):
    """Omega3P exits 0 and writes nothing. Both modules did complete — marking
    one failed would make a resume re-run a solve — so the manifest records the
    *point* as failed, and ``--status`` says so with nothing left to run."""
    wf = _workflow(staged, [100.0])
    _replace_binary(staged, 'omega3p', _OMEGA3P_SILENT)
    with pytest.raises(ValueError, match='no Omega3P eigenmode results'):
        modes.parameter_sweep(wf, on_error='raise')

    workdir = str(staged / 'wd') + '_0'
    recorded = state.read_state(workdir)
    assert {e['name']: e['status'] for e in recorded['modules']} == {
        'cubit': 'complete', 'omega3p': 'complete'}
    assert recorded['error'].startswith('ValueError: no Omega3P eigenmode')
    assert recorded['outputs'] == {}

    df = modes.status(_workflow(staged, [100.0]))
    assert df['status'].tolist() == ['failed']
    assert df['modules'].tolist() == ['2/2']
    assert df['next'].tolist() == ['']


@posix_only
def test_a_failing_acdtool_binary_is_recorded_failed(staged, monkeypatch):
    """The real-exit counterpart of ``test_resume``'s injected transwake
    failure."""
    monkeypatch.setenv('FAKE_ACDTOOL_FAIL', '1')
    with pytest.raises(ToolExitError,
                       match='acdtool postprocess transwake exited with status 1'):
        modes.parameter_sweep(_transwake_workflow(staged, [100.0]),
                              on_error='raise')
    recorded = _recorded(str(staged / 'wd') + '_0')
    assert {name: e['status'] for name, e in recorded.items()} == {
        'cubit': 'complete', 't3p': 'complete', 'transwake': 'failed'}


@posix_only
def test_a_successful_point_records_no_error(staged):
    modes.parameter_sweep(_workflow(staged, [100.0]))
    assert 'error' not in state.read_state(str(staged / 'wd') + '_0')


@posix_only
def test_check_exit(tmp_path):
    log = str(tmp_path / 'step.log')
    ok = run_logged('true', cwd=str(tmp_path), log_file=log)
    assert check_exit('step', ok, log) is ok

    failed = run_logged('exit 4', cwd=str(tmp_path), log_file=log)
    with pytest.raises(ToolExitError) as excinfo:
        check_exit('step', failed, log)
    assert excinfo.value.returncode == 4
    assert excinfo.value.command == 'exit 4'
    assert excinfo.value.log_file == log
    assert str(excinfo.value) == ('step exited with status 4 (command: exit 4) '
                                  f'— check {log}.')


def test_point_status_reports_a_point_level_error():
    recorded = state.new_state(config_hash='h')
    recorded['modules'] = [{'name': 'cubit', 'status': 'complete'}]
    assert state.point_status(recorded, 'h', ['cubit']) == ('complete', 1, None)
    recorded['error'] = 'ValueError: nothing to extract'
    assert state.point_status(recorded, 'h', ['cubit']) == ('failed', 1, None)
