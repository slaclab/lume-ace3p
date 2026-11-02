"""Concurrent points (Phase 3 of ``plans/parallel_sweep_plan.md``).

``mode: {workers: N}`` runs N sweep points, or training samples, at once in a
thread pool. What is pinned is that nothing but the wall clock changes: the
table, the manifests and the store are the serial run's, ``on_error`` means
what it meant, and ``resume`` picks up a concurrent run's workdirs. Under
``workers > 1`` the tools' output goes only to the per-module logs, and the
job's stdout carries point events and progress lines instead.

These use the fake binaries of ``test_resume.py``, with an Omega3P that sleeps
and records when it ran, so overlap is an observation. No Slurm is involved:
outside a job ``worker_plan`` gives no slice, and the command lines are the
serial ones.
"""

import os
import re
import shutil

import numpy as np
import pytest

from lume_ace3p import modes, state
from lume_ace3p.modules import RunContext
from lume_ace3p.results import STATUS_COLUMN, write_table

from test_on_error import _break_cubit
from test_resume import (_invocations, _workflow, posix_only,
                         staged)  # noqa: F401  (fixture)


_RADII = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]

# The shared fake Omega3P, plus a sleep and a 'start end' line in times.txt.
_SLOW_OMEGA3P = """#!/bin/sh
start=`date +%s.%N`
sleep 0.6
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
echo "$start `date +%s.%N`" > times.txt
"""


def _slow(root):
    script = root / 'bin' / 'omega3p'
    script.write_text(_SLOW_OMEGA3P)
    script.chmod(0o755)


def _slow_workflow(root, radii=_RADII, broken=False):
    wf = _workflow(root, radii)
    _slow(root)
    if broken:
        _break_cubit(root)
    return wf


def _workdir(root, i):
    return str(root / 'wd') + f'_{i}'


def _status(root, i):
    return state.point_status(state.read_state(_workdir(root, i)),
                              module_names=('cubit', 'omega3p'))[0]


def _table_bytes(df, path):
    write_table(df, str(path))
    return path.read_bytes()


# --------------------------------------------------------------------------- #
# The table and the manifests are the serial run's
# --------------------------------------------------------------------------- #


@posix_only
def test_three_workers_give_the_serial_table_and_really_overlap(staged):
    serial = _table_bytes(modes.parameter_sweep(_slow_workflow(staged)),
                          staged / 'serial.txt')
    for i in range(len(_RADII)):
        shutil.rmtree(_workdir(staged, i))

    df = modes.parameter_sweep(_slow_workflow(staged), workers=3)

    # Same workdirs, so even the field-artifact paths match byte for byte.
    assert _table_bytes(df, staged / 'parallel.txt') == serial
    spans = []
    for i in range(len(_RADII)):
        assert _status(staged, i) == state.COMPLETE
        with open(os.path.join(_workdir(staged, i), 'times.txt')) as f:
            spans.append(tuple(map(float, f.read().split())))
    # At most three at once, and at some moment at least two.
    in_flight = [sum(1 for s, e in spans if s <= t < e) for t, _ in spans]
    assert 2 <= max(in_flight) <= 3


@posix_only
def test_a_failed_point_under_skip_is_the_serial_nan_row(staged):
    serial = modes.parameter_sweep(_slow_workflow(staged, broken=True))
    parallel = modes.parameter_sweep(_slow_workflow(staged, broken=True),
                                     workers=3)
    assert parallel[STATUS_COLUMN].tolist() == serial[STATUS_COLUMN].tolist()
    assert parallel[STATUS_COLUMN].tolist()[1] == 'failed'
    assert np.isnan(parallel['f0'][1])
    assert parallel['f0'].drop(index=1).tolist() == \
        serial['f0'].drop(index=1).tolist()


@posix_only
def test_a_failure_under_raise_starts_no_further_point(staged):
    """Point 1's Cubit fails at once while point 0 is in its slow solve. No
    later point starts, point 0 still finishes (its manifest is complete), and
    the failure is what is raised."""
    from lume_ace3p.logs import ToolExitError

    with pytest.raises(ToolExitError, match='cubit'):
        modes.parameter_sweep(_slow_workflow(staged, broken=True),
                              on_error='raise', workers=2)
    assert _status(staged, 0) == state.COMPLETE
    assert _status(staged, 1) == state.FAILED
    for i in range(2, len(_RADII)):
        assert not os.path.exists(_workdir(staged, i)), f'point {i} started'


@posix_only
def test_a_cut_off_concurrent_sweep_resumes_only_its_unfinished_points(staged):
    """The first four points ran (concurrently) before the wall clock; point 3's
    solve was lost. A resumed run with three workers runs Omega3P for points 3
    to 6 only, and re-meshes nothing that was meshed."""
    modes.parameter_sweep(_slow_workflow(staged, _RADII[:4]), workers=3)
    os.remove(os.path.join(_workdir(staged, 3), 'omega3p_results',
                           'omega3p.out'))

    df = modes.parameter_sweep(_slow_workflow(staged), resume=True, workers=3)

    assert df['f0'].tolist() == _RADII
    for i in range(len(_RADII)):
        assert _invocations(_workdir(staged, i), 'cubit') == 1
        assert _invocations(_workdir(staged, i), 'omega3p') == (2 if i == 3
                                                                else 1)


# --------------------------------------------------------------------------- #
# What the job's stdout carries
# --------------------------------------------------------------------------- #


@posix_only
def test_stdout_has_events_and_progress_and_no_solver_output(staged, capsys):
    modes.parameter_sweep(_slow_workflow(staged, broken=True), workers=3)
    out = capsys.readouterr().out

    assert 'omega3p: wrote' not in out and 'cubit: wrote' not in out
    for i in range(len(_RADII)):
        assert re.search(rf'^ - point {i} started \(radius=\S+\) in \S+_{i} '
                         r'\(worker [123]\)$', out, re.M)
        verdict = 'failed' if i == 1 else 'complete'
        assert re.search(rf'^ - point {i} {verdict} in \d+ s', out, re.M)
    assert re.search(r'^ - point 1 failed in \d+ s: ToolExitError: cubit '
                     r'exited with status 4', out, re.M)
    # A progress line per finish, naming the busy workers and their points.
    progress = re.findall(r'^ - (\d+) of 7 done.*$', out, re.M)
    assert [int(n) for n in progress] == list(range(1, 8))
    assert re.search(r'^ - 1 of 7 done, 1 failed; running: worker \d point \d '
                     r'\(radius=\S+\), worker \d point \d', out, re.M)
    assert re.search(r'^ - 7 of 7 done, 1 failed$', out, re.M)
    # The tools' output is all in the per-module logs.
    for i in range(len(_RADII)):
        if i == 1:
            continue
        with open(os.path.join(_workdir(staged, i), 'omega3p.log')) as f:
            assert 'omega3p: wrote' in f.read()


@posix_only
def test_one_worker_keeps_the_live_solver_output(staged, capsys):
    modes.parameter_sweep(_slow_workflow(staged, _RADII[:2]))
    out = capsys.readouterr().out
    assert 'omega3p: wrote' in out
    assert 'started' not in out and 'of 2 done' not in out


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_concurrent_points_sharing_a_workdir_are_refused(staged):
    wf = _workflow(staged, [100.0, 101.0], workdir_mode='manual')
    with pytest.raises(ValueError, match='workdir per point'):
        modes.parameter_sweep(wf, workers=2)


class _NoModules:
    modules = []


def test_run_mode_hands_the_plan_to_the_sweep(monkeypatch):
    seen = {}
    monkeypatch.setattr(modes, 'parameter_sweep',
                        lambda wf, **kw: seen.update(kw) or None)
    monkeypatch.setattr(modes.os, 'environ', {})
    modes.run_mode({'type': 'parameter_sweep', 'workers': 3}, _NoModules())
    assert seen['workers'] == 3 and seen['slice'] is None


def test_workers_is_a_collect_training_data_key():
    assert 'workers' in modes.MODE_KEYS['collect_training_data']


# --------------------------------------------------------------------------- #
# collect_training_data
# --------------------------------------------------------------------------- #

_BETAS = ['beta1', 'beta2']


class _SampleFlow:
    """A thread-safe stand-in for the collector's workflow: the dose grid is a
    function of the β the evaluation was given, carried on its own ``ctx``
    (not on the workflow, which every worker shares)."""

    class _Particles:
        type = 'field_emission'
        params = {'num_bins': 2, 'beta_inputs': _BETAS,
                  'bin_edges': [0.0, 1.0, 2.0]}

    def __init__(self, fail_at=None):
        self.modules = [self._Particles()]
        self.workdir_mode = 'manual'
        self.baseworkdir = None
        self.dry_run = False
        self.fail_at = fail_at

    def evaluate(self, overrides, workdir=None, resume=False, tee=True):
        assert tee is False
        if self.fail_at is not None and workdir.endswith(self.fail_at):
            raise RuntimeError('deliberate')
        return {}, RunContext(workdir, outputs=overrides)

    def field(self, ctx):
        s = sum(ctx.outputs.values())
        return {'dose': {'indices': np.array([[0, 0, 0], [1, 0, 0]]),
                         'values': np.array([s, s + 1.0])}}


class _SerialSampleFlow(_SampleFlow):
    def evaluate(self, overrides, workdir=None, resume=False, tee=True):
        assert tee is True
        return {}, RunContext(workdir, outputs=overrides)


def _training_cfg(store, **kw):
    return {'type': 'collect_training_data', 'store': str(store),
            'num_samples': 7, 'seed': 0,
            'variables': {name: [40.0, 60.0] for name in _BETAS}, **kw}


def test_collect_training_data_with_workers_writes_the_serial_store(tmp_path):
    from lume_ace3p import surrogate_data
    store = tmp_path / 'store'
    table = store / surrogate_data.TABLE_FILENAME

    modes.collect_training_data(_training_cfg(store), _SerialSampleFlow())
    serial = table.read_bytes()
    fields = [(store / f'sample_{i:05d}' / 'field.npz').read_bytes()
              for i in range(7)]
    shutil.rmtree(store)

    modes.collect_training_data(_training_cfg(store, workers=3), _SampleFlow(),
                                plan=(3, None))
    assert table.read_bytes() == serial
    assert [(store / f'sample_{i:05d}' / 'field.npz').read_bytes()
            for i in range(7)] == fields


def test_collect_training_data_skips_a_failed_sample_under_workers(tmp_path,
                                                                    capsys):
    df = modes.collect_training_data(_training_cfg(tmp_path / 'store'),
                                     _SampleFlow(fail_at='sample_00002'),
                                     plan=(3, None))
    assert df[STATUS_COLUMN].tolist() == ['complete'] * 2 + ['failed'] + \
        ['complete'] * 4
    out = capsys.readouterr().out
    assert re.search(r'^ - sample 2 failed in \d+ s: RuntimeError: deliberate',
                     out, re.M)
