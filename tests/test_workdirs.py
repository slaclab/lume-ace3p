"""Per-evaluation workdirs on the override path (Phase A of
``plans/xopt_resume_workdir_plan.md``).

The *override* path is the one the Xopt modes drive: ``Workflow.evaluate`` is
handed an input **dict** rather than a list of swept axis scalars, so there is no
sweep grid and none of the three ``workdir_mode`` values used to give an
optimization a usable directory layout. ``manual`` shared one directory across
every evaluation, ``indexed`` had no point index to work from, and ``auto`` — which
names by input *value* — read only the ``cubit`` and ``particles`` buckets, so an
optimization over an **ACE3P** or Geant4 knob produced one unchanging name and
every evaluation silently overwrote the previous one's mesh, results, logs and run
manifest while the directory still *looked* per-point.

What is pinned here:

1. **An optimization over an ACE3P variable gets one directory per evaluation**,
   each holding its own solve. This is the bug above, asserted on the directory
   list and the numbers inside those directories rather than on a message.
2. The directories are ``<workdir>_0``, ``<workdir>_1``, … **in evaluation
   order** — each one's solved frequency is the value on the matching row of the
   Xopt data table.
3. **Two evaluations at the same point still get two directories** — the property
   value-based naming cannot have, and the one a Nelder-Mead simplex needs.
4. With **no ``workdir:`` configured** the directories land *inside* the working
   directory, not as siblings of it.
5. ``workdir_mode: manual`` plus a multi-evaluation run **warns exactly once**;
   a single-evaluation run and a sweep under ``auto`` do not.
6. A workflow **double** that exposes neither ``workdir_mode`` nor a ``workdir``
   keyword is still driven unchanged — the gate the Xopt test doubles (and the
   frozen ``s3p_optimization`` baseline produced through one) rest on.

Tests 1-4 run **real subprocesses through fake binaries** (the harness from
``test_resume``): the naming being per-evaluation is only interesting if each
directory really holds that evaluation's files, which a dry run — launching
nothing — cannot show. The fake Omega3P is swapped for one that reports the
``FrequencyScan.Start`` leaf it was given, so the objective is driven by the
*ACE3P* variable and the collision above is the one being exercised.
"""

import os

import numpy as np
import pytest

from lume_ace3p import modes
from lume_ace3p.ace3p import Section
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.workflow_graph import Workflow, DEFAULT_WORKDIR_BASE

# The fake-ACE3P harness lives with the resume tests that introduced it; these
# reuse it rather than growing a second copy of the shell stand-ins. ``staged`` is
# a fixture, imported so pytest can find it here too.
from test_resume import (                                       # noqa: F401
    _fake_ace3p, _JOURNAL_NAME, posix_only, staged,
)


# 'omega3p <input> [results_dir]' -- reports the ACE3P input file's own
# 'FrequencyScan.Start' leaf as the mode frequency. test_resume's fake reads the
# swept radius out of the *mesh*, which would make the objective of an
# ACE3P-variable optimization constant; this one makes the ACE3P leaf the thing
# the objective actually sees, which is the case that used to collide.
_OMEGA3P_FREQSCAN = """#!/bin/sh
dir=${2:-omega3p_results}
start=`grep -E '^[ 	]*Start[ 	]*:' "$1" | tail -1 | sed 's/.*: *//'`
mkdir -p "$dir"
{ echo 'Mode : {'
  echo '  ModeIndex : 0'
  echo "  Frequency : $start"
  echo '  QualityFactor : 5000.0'
  echo '}'
} > "$dir/omega3p.out"
echo "omega3p: wrote $dir/omega3p.out (start=$start)"
"""

_START = 'ace3p:FrequencyScan.Start'

_ACE3P_VOCS = {'variables': {_START: [1.0e9, 3.0e9]},
               'objectives': {'f0': 'MINIMIZE'}}

# ``workdir=_MISSING`` omits the key entirely, which is a different case from any
# value it could be given (its default is the working directory).
_MISSING = object()


def _ace3p_workflow(root, workdir=None, workdir_mode='auto', **params):
    """A real (non-dry-run) ``cubit -> omega3p`` chain whose optimization knob is
    an **ACE3P leaf**, not a Cubit variable — the bucket ``auto``'s value-based
    naming never looked at."""
    paths = _fake_ace3p(root)
    solver = root / 'bin' / 'omega3p'
    solver.write_text(_OMEGA3P_FREQSCAN)
    solver.chmod(0o755)

    frequency_scan = Section()
    frequency_scan.append('Start', 1.0e9)
    ace3p = Section()
    ace3p.append('FrequencyScan', frequency_scan)

    workflow_params = {'workdir_mode': workdir_mode, 'dry_run': False,
                       'paths': paths, **params}
    if workdir is not _MISSING:
        workflow_params['workdir'] = (str(root / 'wd') if workdir is None
                                      else workdir)
    return Workflow(
        [{'module': 'cubit', 'journal': _JOURNAL_NAME, 'meshconvert': False},
         {'module': 'omega3p', 'input': 'cavity.omega3p'}],
        workflow_params=workflow_params,
        inputs=WorkflowInputs(cubit={'radius': 100.0}, ace3p=ace3p),
        output_spec={'f0': {'module': 'omega3p', 'quantity': 'Frequency',
                            'at': {'mode': 0}}})


def _optimize(workflow, num_step=4, **xopt):
    return modes.scalar_optimize(
        workflow, _ACE3P_VOCS,
        {'generator': 'NelderMeadGenerator', 'num_random': 0,
         'num_step': num_step, **xopt},
        log_file='sim_output.txt')


def _iteration_dirs(root, base='wd'):
    """The per-evaluation directories under ``root``, in index order."""
    names = [name for name in os.listdir(root)
             if name.startswith(base + '_') and os.path.isdir(root / name)]
    return sorted(names, key=lambda name: int(name.rsplit('_', 1)[1]))


def _solved_frequency(workdir):
    """The frequency the fake Omega3P wrote in ``workdir`` — i.e. the ACE3P
    ``Start`` leaf *that* evaluation was given, read back off disk."""
    path = os.path.join(workdir, 'omega3p_results', 'omega3p.out')
    with open(path) as file:
        for line in file:
            if line.strip().startswith('Frequency'):
                return float(line.split(':', 1)[1])
    raise AssertionError(f'no Frequency in {path}')


# --------------------------------------------------------------------------- #
# 1. The bug: an ACE3P-variable optimization used to run every evaluation in one
#    directory while looking per-point.
# --------------------------------------------------------------------------- #


@posix_only
def test_an_ace3p_variable_optimization_gets_one_directory_per_evaluation(staged):
    """Every evaluation of an optimization over an ACE3P leaf has its own workdir,
    holding its own solve.

    Before Phase A this produced a single directory (``wd_100.0``, named from the
    unchanging Cubit radius) that every evaluation overwrote — so the mesh, the
    solver output and the run manifest left behind described whichever evaluation
    happened to run last."""
    X = _optimize(_ace3p_workflow(staged))
    evaluations = len(X.data)
    assert evaluations >= 4

    assert _iteration_dirs(staged) == [f'wd_{i}' for i in range(evaluations)]

    frequencies = []
    for index in range(evaluations):
        workdir = staged / f'wd_{index}'
        assert (workdir / 'cavity.gen').is_file(), 'no mesh for this evaluation'
        frequencies.append(_solved_frequency(workdir))

    # The directories hold *different* solves, which is what a single shared
    # directory could not show: it would have held one, repeatedly overwritten.
    assert len(set(frequencies)) > 1


# --------------------------------------------------------------------------- #
# 2. ...numbered in evaluation order
# --------------------------------------------------------------------------- #


@posix_only
def test_iteration_workdirs_follow_the_evaluation_order(staged):
    """``wd_<n>`` holds the n-th evaluation: its solved frequency is the value on
    row n of the Xopt data table.

    Asserted against the artifacts each subprocess actually wrote, so this pins
    the workdir-to-row correspondence rather than the naming scheme alone."""
    X = _optimize(_ace3p_workflow(staged))
    proposed = X.data[_START].tolist()

    for index, value in enumerate(proposed):
        assert _solved_frequency(staged / f'wd_{index}') == pytest.approx(value)


# --------------------------------------------------------------------------- #
# 3. The property value-naming cannot have
# --------------------------------------------------------------------------- #


@posix_only
def test_two_evaluations_at_the_same_point_get_two_directories(staged):
    """The same proposed point, evaluated twice, occupies two directories.

    A Nelder-Mead simplex does re-propose a point it has already evaluated, and
    value-based naming would put both in one directory — the second silently
    overwriting the first. The objective is driven directly here so the duplicate
    is the test's own doing rather than a hope about the generator."""
    workflow = _ace3p_workflow(staged)
    vocs = modes._make_vocs(_ACE3P_VOCS)
    objective = modes._objective_from_workflow(workflow, vocs, {})

    point = {_START: 2.0e9}
    first = objective(dict(point))
    second = objective(dict(point))

    assert first == second == {'f0': pytest.approx(2.0e9)}
    assert _iteration_dirs(staged) == ['wd_0', 'wd_1']
    assert _solved_frequency(staged / 'wd_0') == pytest.approx(2.0e9)
    assert _solved_frequency(staged / 'wd_1') == pytest.approx(2.0e9)


# --------------------------------------------------------------------------- #
# 4. No 'workdir:' configured -> inside the working directory, not beside it
# --------------------------------------------------------------------------- #


@posix_only
def test_with_no_workdir_the_directories_land_under_the_cwd(staged):
    """``workdir`` defaults to the working directory, and a per-evaluation name
    *appends* to its base — so without this the directories would be siblings of
    the working directory (``/path/to/run_0`` beside ``/path/to``) rather than
    inside it. The fallback base is the relative ``DEFAULT_WORKDIR_BASE``."""
    X = _optimize(_ace3p_workflow(staged, workdir=_MISSING), num_step=2)

    assert _iteration_dirs(staged, base=DEFAULT_WORKDIR_BASE) == [
        f'{DEFAULT_WORKDIR_BASE}_{i}' for i in range(len(X.data))]
    # Nothing named '<this directory>_<n>' appeared next to the working directory.
    siblings = [name for name in os.listdir(staged.parent)
                if name.startswith(staged.name + '_')]
    assert siblings == []


def test_an_auto_sweep_with_no_workdir_also_stays_under_the_cwd(tmp_path,
                                                               monkeypatch):
    """The same latent behavior on the sweep path, fixed with it: a value-named
    ``auto`` directory with no configured ``workdir`` is relative too."""
    monkeypatch.chdir(tmp_path)
    workflow = Workflow(
        [{'module': 'cubit', 'journal': 'x.jou'}],
        workflow_params={'workdir_mode': 'auto', 'dry_run': True},
        inputs=WorkflowInputs(cubit={'radius': 100.0}))

    assert workflow.resolved_workdir() == f'{DEFAULT_WORKDIR_BASE}_100.0'
    assert workflow.point_workdir(2) == f'{DEFAULT_WORKDIR_BASE}_2'


def test_a_configured_workdir_is_still_the_base(tmp_path):
    """The fallback applies only when the key is absent — a configured ``workdir``
    is used as given, including one that happens to name the cwd."""
    workflow = Workflow(
        [{'module': 'cubit', 'journal': 'x.jou'}],
        workflow_params={'workdir': str(tmp_path / 'wd'), 'workdir_mode': 'auto',
                         'dry_run': True},
        inputs=WorkflowInputs(cubit={'radius': 100.0}))

    assert workflow.point_workdir(0) == str(tmp_path / 'wd') + '_0'
    assert workflow.resolved_workdir() == str(tmp_path / 'wd') + '_100.0'


# --------------------------------------------------------------------------- #
# 5. 'manual' with many evaluations is a hazard, so say so once
# --------------------------------------------------------------------------- #


def _dry_workflow(tmp_path, radius, workdir_mode='manual'):
    """A dry-run single-module workflow — enough to reach the warning, and fast."""
    return Workflow(
        [{'module': 'cubit', 'journal': 'x.jou'}],
        workflow_params={'workdir': str(tmp_path / 'wd'),
                         'workdir_mode': workdir_mode, 'dry_run': True},
        inputs=WorkflowInputs(cubit={'radius': radius}))


def _warnings(captured):
    return [line for line in captured.out.splitlines()
            if line.startswith('Warning:') and 'workdir_mode' in line]


def test_manual_warns_once_for_a_multi_point_sweep(tmp_path, capsys):
    modes.parameter_sweep(_dry_workflow(tmp_path, np.array([1.0, 2.0, 3.0])))
    warnings = _warnings(capsys.readouterr())

    assert len(warnings) == 1
    assert '3 evaluations' in warnings[0]
    assert str(tmp_path / 'wd') in warnings[0]
    assert 'auto' in warnings[0]


def test_manual_does_not_warn_for_a_single_evaluation(tmp_path, capsys):
    modes.single(_dry_workflow(tmp_path, 1.0))
    assert _warnings(capsys.readouterr()) == []

    modes.parameter_sweep(_dry_workflow(tmp_path, np.array([1.0])))
    assert _warnings(capsys.readouterr()) == []


def test_a_sweep_with_its_own_directories_does_not_warn(tmp_path, capsys):
    for workdir_mode in ('auto', 'indexed'):
        modes.parameter_sweep(_dry_workflow(tmp_path, np.array([1.0, 2.0]),
                                            workdir_mode=workdir_mode))
        assert _warnings(capsys.readouterr()) == [], workdir_mode


@posix_only
def test_manual_warns_once_for_an_optimization(staged, capsys):
    """The Xopt modes warn too, and keep sharing the one directory — Phase A
    changes where files go, not the default."""
    X = _optimize(_ace3p_workflow(staged, workdir=str(staged / 'wd'),
                                  workdir_mode='manual'), num_step=3)
    warnings = _warnings(capsys.readouterr())

    assert len(warnings) == 1
    assert 'manual' in warnings[0]
    assert _iteration_dirs(staged) == []
    assert (staged / 'wd' / 'cavity.gen').is_file()
    assert len(X.data) >= 3


def test_the_evaluation_count_an_optimization_is_warned_about():
    """A lower bound, because the cost-limited path stops on a measured runtime
    budget rather than a count — but never a *zero* that would silence the
    warning for a run that does evaluate repeatedly."""
    assert modes._xopt_evaluations({'num_random': 0, 'num_step': 25}) == 25
    assert modes._xopt_evaluations({'num_random': 3, 'num_step': 5}) == 8
    assert modes._xopt_evaluations({'num_step': 5, 'max_iterations': 40}) == 40
    assert modes._xopt_evaluations({'num_random': 3,
                                    'alotted_time': '00:30:00'}) == 4
    assert modes._xopt_evaluations({'cost_budget': 600}) == 3


# --------------------------------------------------------------------------- #
# 6. The workflow doubles keep working unedited
# --------------------------------------------------------------------------- #


class _Double:
    """The shape both Xopt test doubles have: ``evaluate(input_dict)`` with no
    ``workdir`` keyword and no ``workdir_mode`` attribute."""

    def __init__(self):
        self.calls = []

    def evaluate(self, input_dict):
        self.calls.append(dict(input_dict))
        return {'obj': float(input_dict['x'])}, None


def test_a_double_with_no_workdir_mode_is_driven_with_no_workdir_keyword():
    """The ``getattr`` gate in ``_iteration_workdir`` is load-bearing: it is what
    keeps the doubles — and the frozen ``s3p_optimization`` baseline produced
    through one — valid without editing them."""
    double = _Double()
    assert modes._iteration_workdir(double, 3) is None

    vocs = modes._make_vocs({'variables': {'x': [0.0, 1.0]},
                             'objectives': {'obj': 'MINIMIZE'}})
    objective = modes._objective_from_workflow(double, vocs, {})

    assert objective({'x': 0.5}) == {'obj': 0.5}
    assert double.calls == [{'x': 0.5}]


def test_manual_leaves_the_naming_to_the_workflow(tmp_path):
    """Under ``manual`` the override path passes no workdir at all, so every
    evaluation shares the configured directory exactly as before."""
    workflow = _dry_workflow(tmp_path, 1.0, workdir_mode='manual')
    assert modes._iteration_workdir(workflow, 7) is None


def test_indexed_and_auto_both_number_by_iteration(tmp_path):
    for workdir_mode in ('auto', 'indexed'):
        workflow = _dry_workflow(tmp_path, 1.0, workdir_mode=workdir_mode)
        assert modes._iteration_workdir(workflow, 7) == str(tmp_path / 'wd') + '_7'


def test_the_iteration_counter_can_start_past_earlier_evaluations(tmp_path):
    """``first_index`` is what a resumed optimization (Phase B) will use so it does
    not reoccupy the directories of the evaluations it inherited."""
    workflow = _dry_workflow(tmp_path, 1.0, workdir_mode='auto')
    seen = []

    def evaluate(input_dict, workdir=None, resume=False):
        seen.append(workdir)
        return {'obj': 0.0}, None
    workflow.evaluate = evaluate

    vocs = modes._make_vocs({'variables': {'x': [0.0, 1.0]},
                             'objectives': {'obj': 'MINIMIZE'}})
    objective = modes._objective_from_workflow(workflow, vocs, {}, first_index=5)
    objective({'x': 0.5})
    objective({'x': 0.6})

    assert seen == [str(tmp_path / 'wd') + '_5', str(tmp_path / 'wd') + '_6']
