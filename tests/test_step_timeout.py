"""A step that runs past its ``timeout:`` is stopped (Phase 4 of
``plans/parallel_sweep_plan.md``).

``timeout`` on a module entry bounds each external-tool step it launches. On
expiry :func:`lume_ace3p.logs.run_logged` stops the step's whole process group,
its log ends with a line saying so, and the wrapper raises
:class:`~lume_ace3p.logs.ToolExitError` naming the timeout. From there it is an
ordinary failed module: the manifest records it with ``timed out after N s``,
``on_error: skip`` gives the point a NaN row, and ``resume: true`` re-runs it.

These use the fake binaries of ``test_resume.py``, with an Omega3P that sleeps.
"""

import os
import time

import numpy as np
import pytest

from lume_ace3p import logs, modes, state
from lume_ace3p.logs import ToolExitError, run_logged
from lume_ace3p.modules import build_module
from lume_ace3p.results import STATUS_COLUMN

from test_resume import (_invocations, _workflow, posix_only,
                         staged)  # noqa: F401  (fixture)


# The shared fake Omega3P after a sleep of FAKE_OMEGA3P_SLEEP seconds (0 when
# unset), so one staged binary serves both the run that times out and the one
# that does not.
_SLEEPY_OMEGA3P = """#!/bin/sh
echo "omega3p: starting"
sleep ${FAKE_OMEGA3P_SLEEP:-0}
dir=${2:-omega3p_results}
radius=`cat *.gen 2>/dev/null | head -1`
mkdir -p "$dir"
{ echo 'Mode : {'
  echo '  ModeIndex : 0'
  echo "  Frequency : $radius"
  echo '  QualityFactor : 5000.0'
  echo '}'
} > "$dir/omega3p.out"
echo "omega3p: wrote $dir/omega3p.out"
"""


@pytest.fixture
def sleepy(staged, monkeypatch):
    monkeypatch.setattr(logs, 'KILL_GRACE', 1.0)
    return staged


def _timed_workflow(root, radii, timeout):
    wf = _workflow(root, radii, entries=[
        {'module': 'cubit', 'journal': 'cavity.jou', 'meshconvert': False},
        {'module': 'omega3p', 'input': 'cavity.omega3p', 'timeout': timeout},
    ])
    # After _workflow, which (re)writes the shared fakes.
    script = root / 'bin' / 'omega3p'
    script.write_text(_SLEEPY_OMEGA3P)
    script.chmod(0o755)
    return wf


def _workdir(root, i):
    return str(root / 'wd') + f'_{i}'


def _recorded(workdir):
    return {e['name']: e for e in state.read_state(workdir)['modules']}


# --------------------------------------------------------------------------- #
# The sweep: failed module, NaN row, resume with a longer timeout
# --------------------------------------------------------------------------- #


@posix_only
def test_a_step_past_its_timeout_fails_and_resumes_with_a_longer_one(
        sleepy, monkeypatch):
    monkeypatch.setenv('FAKE_OMEGA3P_SLEEP', '30')
    started = time.monotonic()
    df = modes.parameter_sweep(_timed_workflow(sleepy, [100.0, 101.0], 0.5))
    assert time.monotonic() - started < 10       # not the 2 x 30 s of the sleep

    assert df[STATUS_COLUMN].tolist() == ['failed', 'failed']
    assert np.isnan(df['f0']).all()
    workdir = _workdir(sleepy, 0)
    recorded = _recorded(workdir)
    assert recorded['cubit']['status'] == state.COMPLETE
    assert recorded['omega3p']['status'] == state.FAILED
    assert 'omega3p timed out after 0.5 s' in recorded['omega3p']['error']
    with open(os.path.join(workdir, 'omega3p.log')) as f:
        log = f.read()
    assert 'omega3p: starting' in log and 'omega3p: wrote' not in log
    assert log.endswith('lume-ace3p: timed out after 0.5 s; the step was '
                        'stopped.\n')

    # The timeout is a launch key, so a longer one re-runs only the solver.
    monkeypatch.setenv('FAKE_OMEGA3P_SLEEP', '0')
    df = modes.parameter_sweep(_timed_workflow(sleepy, [100.0, 101.0], 60),
                               resume=True)
    assert df['f0'].tolist() == [100.0, 101.0]
    assert STATUS_COLUMN not in df         # only a table with a failure has one
    assert _invocations(workdir, 'cubit') == 1
    assert _invocations(workdir, 'omega3p') == 2


@posix_only
def test_a_timeout_under_raise_names_the_timeout(sleepy, monkeypatch):
    monkeypatch.setenv('FAKE_OMEGA3P_SLEEP', '30')
    with pytest.raises(ToolExitError, match=r'omega3p timed out after 0\.5 s '
                                            r'and was killed'):
        modes.parameter_sweep(_timed_workflow(sleepy, [100.0], 0.5),
                              on_error='raise')


@posix_only
def test_a_step_inside_its_timeout_is_untouched(sleepy):
    df = modes.parameter_sweep(_timed_workflow(sleepy, [100.0], 60))
    assert df['f0'].tolist() == [100.0]
    with open(os.path.join(_workdir(sleepy, 0), 'omega3p.log')) as f:
        assert 'timed out' not in f.read()


def test_timeout_is_a_launch_key_on_every_tool_module():
    """Changing it must not invalidate a campaign's completed modules."""
    entries = {'cubit': {'journal': 'x.jou'},
               'omega3p': {'input': 'x.omega3p'},
               'acdtool': {'input': 'x.rfpost'},
               'geant4': {'geant4_input': 'x.in'}}
    for kind, config in entries.items():
        module = build_module(kind, {**config, 'timeout': 30})
        assert module.timeout == 30
        assert 'timeout' in module._launch_keys, kind


@pytest.mark.parametrize('bad', [0, -5, '1h', True, float('inf')])
def test_a_timeout_that_is_not_a_positive_number_is_refused(bad):
    with pytest.raises(ValueError, match="'timeout' is a step's limit"):
        build_module('omega3p', {'input': 'x.omega3p',
                      'timeout': bad})


# --------------------------------------------------------------------------- #
# run_logged itself
# --------------------------------------------------------------------------- #


@posix_only
def test_run_logged_stops_the_whole_process_group(tmp_path, monkeypatch):
    """A grandchild that ignores SIGTERM is SIGKILLed after the grace period,
    and none of the group outlives the call."""
    monkeypatch.setattr(logs, 'KILL_GRACE', 0.5)
    pidfile = tmp_path / 'pid'
    command = (f"sh -c 'trap \"\" TERM; echo $$ > {pidfile}; "
               f"while :; do sleep 0.1; done' & wait")
    completed = run_logged(command, log_file=str(tmp_path / 'x.log'),
                           tee=False, timeout=0.3)
    assert isinstance(completed, logs.TimedOut)
    assert completed.returncode == -9
    pid = int(pidfile.read_text())
    # Orphaned by the killed shell, it is reaped by init, not by us; until then
    # it is a zombie, which is dead but still answers kill(pid, 0).
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def _alive(pid):
    try:
        with open(f'/proc/{pid}/stat') as f:
            return f.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


@posix_only
def test_run_logged_without_a_log_honors_the_timeout_too(capfd):
    completed = run_logged('sleep 30', timeout=0.2)
    assert logs.timeout_note(completed) == 'timed out after 0.2 s'
    assert 'timed out after 0.2 s' in capfd.readouterr().err


@posix_only
def test_run_logged_passes_a_fast_exit_status_through(tmp_path):
    completed = run_logged('exit 3', log_file=str(tmp_path / 'x.log'),
                           timeout=30)
    assert completed.returncode == 3
    assert logs.timeout_note(completed) is None
