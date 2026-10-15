"""A failed point does not end the run (Phase 4 of
``plans/failure_mitigation_plan.md``).

Under ``mode: {on_error: skip}``, the default, a point that fails once the run
is under way gets a NaN row, its error goes to stderr, and the run goes on; the
table gains a ``status`` column, and ``resume: true`` re-runs only the failed
points. ``on_error: raise`` ends the run at the first failure, as before.
Configuration errors — an unknown ``on_error``, an input file that is not
there — are raised before the first point either way.

These use the fake binaries of ``test_resume.py``: a fake ``cubit`` that fails
for one radius stands for a parameter value that makes a bad mesh.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

from lume_ace3p import modes, run_lume_ace3p, state
from lume_ace3p.logs import ToolExitError
from lume_ace3p.results import FIELD_ARTIFACT_COLUMN, STATUS_COLUMN
from lume_ace3p.workflow_graph import WorkflowValidationError

from test_resume import (_BETAS, _RecordingWorkflow, _collect_cfg, _fake_ace3p,
                         _invocations, _JOURNAL_NAME, _workflow, posix_only,
                         staged)  # noqa: F401  (fixture)


# A Cubit that fails for radius 101 and otherwise behaves as the shared fake.
_CUBIT_FAILS_AT_101 = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in *.jou|*.jou_copy) journal="$arg" ;; esac
done
mesh=`grep -E '^[ 	]*export' "$journal" | tail -1 | cut -d'"' -f2`
radius=`grep -o 'radius=[^}]*' "$journal" | head -1 | cut -d= -f2`
case "$radius" in 101*) echo "cubit: bad geometry" >&2; exit 4 ;; esac
echo "$radius" > "$mesh"
"""

_RADII = [100.0, 101.0, 102.0]


def _break_cubit(root):
    script = root / 'bin' / 'cubit'
    script.write_text(_CUBIT_FAILS_AT_101)
    script.chmod(0o755)


def _broken_workflow(root, radii=_RADII, **kwargs):
    wf = _workflow(root, radii, **kwargs)
    _break_cubit(root)
    return wf


# --------------------------------------------------------------------------- #
# The default: a failed point is a NaN row
# --------------------------------------------------------------------------- #


@posix_only
def test_a_failed_point_is_a_nan_row_and_the_sweep_goes_on(staged, capsys):
    df = modes.parameter_sweep(_broken_workflow(staged))
    err = capsys.readouterr().err

    assert list(df.columns) == ['radius', 'f0', FIELD_ARTIFACT_COLUMN,
                                STATUS_COLUMN]
    assert df['radius'].tolist() == _RADII
    assert df[STATUS_COLUMN].tolist() == ['complete', 'failed', 'complete']
    assert df['f0'][0] == 100.0 and df['f0'][2] == 102.0
    assert np.isnan(df['f0'][1])

    workdir = str(staged / 'wd') + '_1'
    assert f'point 1 (radius=101) failed in {workdir}' in err
    assert 'ToolExitError: cubit exited with status 4' in err
    assert '1 of 3 points failed (1)' in err
    # A known failure prints its message, not a traceback.
    assert 'Traceback' not in err

    # The failed point's manifest says where it stopped; the next points ran.
    recorded = {e['name']: e for e in state.read_state(workdir)['modules']}
    assert recorded['cubit']['status'] == 'failed'
    assert _invocations(str(staged / 'wd') + '_2', 'omega3p') == 1


@posix_only
def test_a_clean_run_has_the_columns_it_always_had(staged, capsys):
    df = modes.parameter_sweep(_workflow(staged, _RADII))
    assert list(df.columns) == ['radius', 'f0', FIELD_ARTIFACT_COLUMN]
    assert 'failed' not in capsys.readouterr().err


@posix_only
def test_an_unexpected_exception_also_prints_its_traceback(staged, capsys,
                                                         monkeypatch):
    """A ``RuntimeError`` / ``ValueError`` / ``OSError`` is what a bad point
    raises; anything else may be a bug, so its traceback is kept."""
    from lume_ace3p.modules import Omega3PModule

    def boom(self, ctx, skip_execution=False):
        raise KeyError('not a point failure')

    monkeypatch.setattr(Omega3PModule, 'run', boom)
    df = modes.parameter_sweep(_workflow(staged, [100.0]))
    err = capsys.readouterr().err
    assert df[STATUS_COLUMN].tolist() == ['failed']
    assert 'Traceback' in err and "KeyError: 'not a point failure'" in err


def test_an_interrupt_is_not_a_failed_point(tmp_path):
    class _Interrupted:
        workdir_mode = 'indexed'
        output_spec = {'f0': {}}

        def sweep_axes(self):
            return [('radius', np.array([1.0, 2.0]), None)]

        def point_workdir(self, i):
            return str(tmp_path / f'wd_{i}')

        def evaluate(self, input_scalars, workdir=None):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        modes.parameter_sweep(_Interrupted())


# --------------------------------------------------------------------------- #
# on_error: raise
# --------------------------------------------------------------------------- #


@posix_only
def test_raise_ends_the_sweep_at_the_failed_point_and_writes_no_table(staged):
    output = staged / 'table.txt'
    with pytest.raises(ToolExitError, match='status 4'):
        modes.run_mode({'type': 'parameter_sweep', 'on_error': 'raise',
                        'output_file': str(output)},
                       _broken_workflow(staged))
    assert not output.exists()
    assert state.read_state(str(staged / 'wd') + '_2') is None


def test_an_unknown_on_error_names_the_two_values(tmp_path):
    from test_resume import _dry_workflow
    with pytest.raises(ValueError, match="'skip' or 'raise'.*'continue'"):
        modes.run_mode({'type': 'parameter_sweep', 'on_error': 'continue'},
                       _dry_workflow(tmp_path, [1.0]))


# --------------------------------------------------------------------------- #
# Resume picks up exactly the failed points
# --------------------------------------------------------------------------- #


@posix_only
def test_resume_re_runs_only_the_failed_point(staged):
    modes.parameter_sweep(_broken_workflow(staged))
    _fake_ace3p(staged)                                # cubit fixed

    df = modes.parameter_sweep(_workflow(staged, _RADII), resume=True)
    assert STATUS_COLUMN not in df.columns
    assert df['f0'].tolist() == _RADII
    workdirs = [str(staged / 'wd') + f'_{i}' for i in range(3)]
    assert [_invocations(w, 'cubit') for w in workdirs] == [1, 2, 1]
    assert [_invocations(w, 'omega3p') for w in workdirs] == [1, 1, 1]


# --------------------------------------------------------------------------- #
# single, and the long-format table
# --------------------------------------------------------------------------- #


@posix_only
def test_a_failed_single_run_is_one_nan_row(staged):
    wf = _broken_workflow(staged, [101.0])
    wf.inputs.cubit['radius'] = 101.0
    df = modes.single(wf)
    assert df[STATUS_COLUMN].tolist() == ['failed']
    assert np.isnan(df['f0'][0])


@posix_only
def test_a_failed_long_format_point_is_one_row_with_a_nan_index(staged):
    entries = [{'module': 'cubit', 'journal': _JOURNAL_NAME,
                'meshconvert': False},
               {'module': 's3p', 'input': 'cavity.s3p'}]
    spec = {'refl': {'module': 's3p', 'quantity': 'S(0,0)'}}
    df = modes.parameter_sweep(
        _broken_workflow(staged, entries=entries, output_spec=spec))

    assert list(df.columns) == ['radius', 'Frequency', 'refl', STATUS_COLUMN]
    assert len(df) == 3 + 1 + 3
    failed = df[df[STATUS_COLUMN] == 'failed']
    assert failed['radius'].tolist() == [101.0]
    assert failed['Frequency'].isna().all() and failed['refl'].isna().all()
    assert df[df['radius'] == 102.0]['Frequency'].tolist() == [1e9, 2e9, 3e9]


# --------------------------------------------------------------------------- #
# What is checked before the first point
# --------------------------------------------------------------------------- #


@posix_only
def test_a_missing_input_file_fails_before_any_point_runs(staged):
    os.remove(staged / 'cavity.omega3p')
    with pytest.raises(WorkflowValidationError,
                       match=r'omega3p: cavity\.omega3p'):
        modes.parameter_sweep(_workflow(staged, _RADII))
    assert not os.path.exists(str(staged / 'wd') + '_0')


@posix_only
def test_a_missing_files_entry_fails_before_any_point_runs(staged):
    entries = [{'module': 'cubit', 'journal': _JOURNAL_NAME,
                'meshconvert': False},
               {'module': 'omega3p', 'input': 'cavity.omega3p',
                'files': ['copper.dat']}]
    with pytest.raises(WorkflowValidationError, match=r'copper\.dat'):
        modes.parameter_sweep(_workflow(staged, _RADII, entries=entries))


def test_dry_run_does_not_check_files(tmp_path, monkeypatch):
    from test_resume import _dry_workflow
    monkeypatch.chdir(tmp_path)                 # no journal, no solver input
    assert len(modes.parameter_sweep(_dry_workflow(tmp_path, [1.0]))) == 1


# --------------------------------------------------------------------------- #
# The exit status of run-lume-ace3p
# --------------------------------------------------------------------------- #


_YAML = """\
workflow_parameters :
  'workdir' : 'wd'
  'workdir_mode' : 'indexed'
  'dry_run' : False
  'paths' : {paths}

workflow :
  - module : cubit
    journal : 'cavity.jou'
    meshconvert : False
  - module : omega3p
    input : 'cavity.omega3p'

mode :
  type : parameter_sweep
  output_file : 'table.txt'

input_parameters :
  cubit :
    'radius' : {radii}

output_parameters :
  f0 : {{module : omega3p, quantity : Frequency, at : {{mode : 0}}}}
"""


def _main(staged, monkeypatch, radii):
    paths = _fake_ace3p(staged)
    _break_cubit(staged)
    (staged / 'sweep.yaml').write_text(
        _YAML.format(paths=dict(paths), radii=list(radii)))
    monkeypatch.setattr(sys, 'argv', ['run-lume-ace3p', 'sweep.yaml'])
    try:
        run_lume_ace3p.main()
    except SystemExit as exc:
        return exc.code
    return 0


@posix_only
def test_some_failed_points_exit_zero_with_the_table_written(staged,
                                                             monkeypatch):
    assert _main(staged, monkeypatch, _RADII) == 0
    table = pd.read_csv(staged / 'table.txt', sep='\t')
    assert table[STATUS_COLUMN].tolist() == ['complete', 'failed', 'complete']
    assert np.isnan(table['f0'][1])


@posix_only
def test_every_point_failed_exits_one_with_the_table_written(staged,
                                                             monkeypatch):
    assert _main(staged, monkeypatch, [101.0, 101.5]) == 1
    table = pd.read_csv(staged / 'table.txt', sep='\t')
    assert table[STATUS_COLUMN].tolist() == ['failed', 'failed']


# --------------------------------------------------------------------------- #
# collect_training_data
# --------------------------------------------------------------------------- #


class _FailingSample(_RecordingWorkflow):
    """Fails the second sample it is asked to evaluate."""

    def evaluate(self, overrides, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 2:
            raise RuntimeError('geant4 exited with status 1')
        return {}, None


def test_a_failed_sample_is_marked_and_left_out_of_the_store(tmp_path,
                                                             capsys):
    from lume_ace3p import surrogate_data

    cfg = _collect_cfg(tmp_path, num_samples=3)
    df = modes.collect_training_data(cfg, _FailingSample())
    assert df[STATUS_COLUMN].tolist() == ['complete', 'failed', 'complete']
    assert '1 of 3 samples failed (1)' in capsys.readouterr().err
    assert not os.path.exists(os.path.join(cfg['store'], 'sample_00001',
                                           'field.npz'))

    store = surrogate_data.load_training_store(cfg['store'])
    assert store.beta.shape == (2, len(_BETAS))
    assert store.dose.shape == (2, 2)

    # The next run evaluates only the sample that has no field.
    again = _RecordingWorkflow()
    df = modes.collect_training_data(cfg, again)
    assert len(again.calls) == 1
    assert STATUS_COLUMN not in df.columns


def test_raise_ends_a_collection_at_the_failed_sample(tmp_path):
    cfg = _collect_cfg(tmp_path, num_samples=3, on_error='raise')
    with pytest.raises(RuntimeError, match='status 1'):
        modes.collect_training_data(cfg, _FailingSample())


# --------------------------------------------------------------------------- #
# A fresh run over earlier workdirs says so (Phase 5)
# --------------------------------------------------------------------------- #


_NOTE = "hold a previous run's manifest"


@posix_only
def test_a_fresh_run_over_earlier_workdirs_notes_resume(staged, capsys):
    modes.parameter_sweep(_workflow(staged, _RADII[:2]))
    assert _NOTE not in capsys.readouterr().out

    modes.parameter_sweep(_workflow(staged, _RADII))
    assert "Note: 2 of 3 workdir(s) hold a previous run's manifest" in (
        capsys.readouterr().out)


@posix_only
def test_a_resumed_run_does_not_note_earlier_workdirs(staged, capsys):
    modes.parameter_sweep(_workflow(staged, _RADII))
    capsys.readouterr()
    modes.parameter_sweep(_workflow(staged, _RADII), resume=True)
    assert _NOTE not in capsys.readouterr().out


def test_manual_workdirs_are_not_noted(tmp_path, capsys):
    from test_resume import _dry_workflow
    modes.parameter_sweep(_dry_workflow(tmp_path, [1.0]))
    modes.parameter_sweep(_dry_workflow(tmp_path, [1.0]))
    assert _NOTE not in capsys.readouterr().out
