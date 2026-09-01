"""Per-evaluation isolation of the ``Workflow`` seam (Phases 1-2 of
``plans/evaluation_isolation_resume_plan.md``).

Phase 1 changes no behavior; it moves per-evaluation state off the shared
``Workflow`` and onto the :class:`~lume_ace3p.modules.RunContext` that
``evaluate`` now returns, so that resume (Phases 3-4) and concurrency after it
have somewhere to stand. These tests pin the properties that move made true —
none of which held before it, and none of which any other test covers:

1. **Each evaluation owns its module instances.** Module instances hold run state
   (a solver's parsed results, acdtool's parsed output), so a shared list means
   ``field()``/``extract`` answer for whichever point ran last. The failure mode
   is *wrong data, not a crash*: row *i* gets row *j*'s artifact.
2. **The prototypes are never run.** ``Workflow.modules`` stays a config-only
   list, which is what makes "resolve run state from ``ctx.modules``" a
   structural guarantee rather than a convention. It matters because a prototype's
   ``extract`` returns the *dry-run NaN sentinel* instead of raising, so a
   mis-resolution would be silent.
3. **Rebuilding modules per evaluation does not multiply warnings.** The acdtool
   deprecation dedup set is per-*config*, not per-*run* — a 25-point sweep over a
   legacy positional spec must warn once, not 25 times.
4. **The mode layer never writes workflow state.** ``collect_training_data`` used
   to assign ``workflow.baseworkdir`` inside its loop; it now passes ``workdir=``
   to ``evaluate`` instead.

Phase 2 adds the two things resume needs on top of that — a **stable point
identity** and **assembly decoupled from execution order** — plus per-evaluation
subprocess logs, the one intentional user-visible change of Phases 1-2:

5. **``workdir_mode: indexed``** names points ``<base>_0``, ``<base>_1``, … so a
   point has a collision-free identity across runs, and produces the same table
   as the same sweep under ``auto``.
6. **Rows go in point-index order**, not completion order, so the frame is
   identical whether points ran in order, out of order, or were resumed.
7. **Each module's subprocess output is teed** to ``<workdir>/<module>.log``: a
   point killed by the wall clock leaves its solver's own message on disk, and
   the terminal keeps everything it had — stderr included, on stderr.
"""

import ast
import os
import warnings

import numpy as np
import pandas as pd
import pytest

import baseline_utils as bu
# The synthetic S3P fixture lives with the module-layer tests; it is what lets a
# solver's *parsed results* be injected without a binary, which is the only way
# to distinguish two evaluations' module state locally.
from test_modules import _make_s3p_solver
from lume_ace3p import modes
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.logs import run_logged
from lume_ace3p.modules import AcdtoolModule, MESH, RunContext
from lume_ace3p.workflow_graph import Workflow


def _module_of(modules, module_type):
    return next(m for m in modules if m.type == module_type)


def _s3p_workflow(tmp_path, cubit_inputs, workdir_mode='auto'):
    """A dry-run ``cubit -> s3p`` workflow with auto-named workdirs under
    ``tmp_path``. ``cubit_inputs`` may hold arrays (a sweep) or scalars."""
    return Workflow(
        [{'module': 'cubit', 'journal': 'x.jou'},
         {'module': 's3p', 'input': 'x.s3p'}],
        workflow_params={'workdir': str(tmp_path / 'wd'),
                         'workdir_mode': workdir_mode, 'dry_run': True},
        inputs=WorkflowInputs(cubit=dict(cubit_inputs)),
        output_spec={'refl': {'module': 's3p', 'quantity': 'S(0,0)'}})


def _particles_workflow(root, workdir_mode, betas):
    """A ``track3p_source -> particles`` sweep over β, rooted at ``root``.

    Chosen over a solver chain wherever a test needs the *table* to carry real
    numbers: the field-emission weighting is pure Python, so it produces genuine
    per-point values with no ACE3P binary, while a dry-run solver would make
    every point NaN and any table comparison vacuous."""
    source = os.path.join(bu.EXAMPLES_DIR, 'assets',
                          'sample_track3p_particles.txt')
    return Workflow(
        [{'module': 'track3p_source', 'file': source},
         {'module': 'particles', 'impact_order': 1, 'impact_face_id': 6,
          'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
          'beta_input': 'beta', 'output_format': 'geant4',
          'output': 'particles.data'}],
        workflow_params={'workdir': str(root / 'wd'),
                         'workdir_mode': workdir_mode, 'dry_run': True},
        inputs=WorkflowInputs(particles={'beta': np.array(betas)}),
        output_spec={'weight': {'module': 'particles',
                                'quantity': 'total_weight'},
                     'count': {'module': 'particles', 'quantity': 'count'}})


# --------------------------------------------------------------------------- #
# 1. Each evaluation owns its module instances
# --------------------------------------------------------------------------- #


def test_each_evaluate_returns_its_own_context_and_modules(tmp_path):
    """Two evaluations of one workflow produce two independent contexts: distinct
    workdirs, distinct artifact paths, and distinct live module instances."""
    wf = _s3p_workflow(tmp_path, {'cornercut': 12.0, 'rcorner2': 4.0})
    _out_a, ctx_a = wf.evaluate([12.0, 4.0])
    _out_b, ctx_b = wf.evaluate([13.0, 5.0])

    assert ctx_a is not ctx_b
    assert ctx_a.workdir.endswith('_12.0_4.0')
    assert ctx_b.workdir.endswith('_13.0_5.0')
    # Each context's mesh artifact lives in that context's own workdir.
    assert ctx_a.artifacts[MESH].startswith(ctx_a.workdir)
    assert ctx_b.artifacts[MESH].startswith(ctx_b.workdir)

    for module_type in ('cubit', 's3p'):
        assert (_module_of(ctx_a.modules, module_type)
                is not _module_of(ctx_b.modules, module_type))
    # ...and neither is the prototype the workflow was built with.
    assert not (set(map(id, ctx_a.modules)) & set(map(id, wf.modules)))


def test_context_still_reports_its_own_results_after_a_later_evaluation(tmp_path):
    """The property Phase 1 exists to create, and the one that was false before.

    Give evaluation A a parsed solver and leave B without one (standing in for two
    points whose solver output differs), then ask the workflow about each. With
    module state on the shared ``Workflow`` both questions got the same answer —
    whichever point ran last. Reading through the ``ctx`` they do not."""
    wf = _s3p_workflow(tmp_path, {'cornercut': 12.0, 'rcorner2': 4.0})
    _out_a, ctx_a = wf.evaluate([12.0, 4.0])
    _out_b, ctx_b = wf.evaluate([13.0, 5.0])

    _module_of(ctx_a.modules, 's3p')._solver = _make_s3p_solver(ctx_a.workdir)

    # A has a real, frequency-indexed spectrum...
    label, values = wf.field_index(ctx_a)
    assert label == 'Frequency'
    assert len(values) == 3
    assert 'S(0,0)' in wf.field(ctx_a)
    assert wf.field(ctx_a)['S(0,0)'] == pytest.approx([0.10, 0.50, 0.90])

    # ...and B, evaluated *after* A, still reports its own (solver-less) state.
    assert wf.field_index(ctx_b) == ('Frequency', pytest.approx(np.array([0.0])))
    assert wf.field(ctx_b) is None

    # The zero-argument form keeps working, resolving to the most recent
    # evaluation (B) — that is what makes this a back-compatible change.
    assert wf.field() is None
    assert wf.last_context is ctx_b


def test_explicit_workdir_overrides_workdir_mode(tmp_path):
    """``evaluate(workdir=...)`` is what lets a caller own the directory layout
    without mutating ``workflow.baseworkdir`` / ``workdir_mode``."""
    wf = _s3p_workflow(tmp_path, {'cornercut': 12.0, 'rcorner2': 4.0})
    target = str(tmp_path / 'chosen')
    _out, ctx = wf.evaluate([12.0, 4.0], workdir=target)
    assert ctx.workdir == target
    # The workflow's own naming configuration is untouched.
    assert wf.workdir_mode == 'auto'
    assert wf.baseworkdir == str(tmp_path / 'wd')


# --------------------------------------------------------------------------- #
# 2. The prototypes are never run
# --------------------------------------------------------------------------- #


# Every attribute in which a module parks run state. A prototype holding one of
# these would mean the never-run invariant had been broken somewhere.
_RUN_STATE_ATTRS = ('_solver', '_acdtool', '_filtered', '_cubit', 'geant4_obj')


def test_prototype_modules_hold_no_run_state_after_a_sweep(tmp_path):
    """``Workflow.modules`` is still the never-run prototype list after a full
    sweep. This is the invariant that makes sourcing run state from
    ``ctx.modules`` safe: a prototype's ``extract`` returns the dry-run NaN
    sentinel rather than raising, so reaching one would be silent.

    The chain is ``track3p_source -> particles`` on purpose. Under dry-run the
    solver and acdtool modules park no state at all (they set their handle to
    ``None`` and write a marker), so a sweep of those would satisfy this
    vacuously; the ``particles`` step does genuine work with no binary, so its
    ``_filtered`` set is real run state and its absence from the prototype is a
    real observation."""
    source = os.path.join(bu.EXAMPLES_DIR, 'assets',
                          'sample_track3p_particles.txt')
    wf = Workflow(
        [{'module': 'track3p_source', 'file': source},
         {'module': 'particles', 'impact_order': 1, 'impact_face_id': 6,
          'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
          'beta_input': 'beta', 'output_format': 'geant4',
          'output': 'particles.data'}],
        workflow_params={'workdir': str(tmp_path / 'wd'),
                         'workdir_mode': 'auto', 'dry_run': True},
        inputs=WorkflowInputs(particles={'beta': np.array([40.0, 60.0])}),
        output_spec={'weight': {'module': 'particles',
                                'quantity': 'total_weight'}})
    df = modes.parameter_sweep(wf)
    assert len(df) == 2
    # The sweep really did compute something, so there was state to leak.
    assert np.all(np.isfinite(df['weight'].to_numpy()))
    assert df['weight'].iloc[0] != df['weight'].iloc[1]

    for module in wf.modules:
        for attr in _RUN_STATE_ATTRS:
            assert getattr(module, attr, None) is None, (
                f'prototype {module.name!r} carries run state in {attr}')
    # The last evaluation's own particles module does hold it — the state exists,
    # it just lives on the context.
    assert _module_of(wf.last_context.modules, 'particles')._filtered is not None


# --------------------------------------------------------------------------- #
# 3. Rebuilding modules per evaluation does not multiply warnings
# --------------------------------------------------------------------------- #


def test_sweep_warns_once_for_a_deprecated_output_spec(tmp_path):
    """A sweep of N points over a deprecated *positional* acdtool output spec
    emits exactly one ``DeprecationWarning``.

    ``AcdtoolModule`` dedups warned specs in a per-instance set, so building the
    module list per evaluation would reset it and turn a 25-point sweep into 25
    identical notices. The set is owned by the ``Workflow`` and shared in, making
    the dedup per-*config* rather than per-*run*.

    No other test catches this: ``test_modules.py``'s warn-once test drives the
    module directly, and the shipped examples were all migrated off the positional
    form, so the examples-raise-no-deprecations test passes either way."""
    wf = Workflow(
        [{'module': 'cubit', 'journal': 'x.jou'},
         {'module': 'omega3p', 'input': 'x.omega3p'},
         {'module': 'acdtool', 'input': 'x.rfpost'}],
        workflow_params={'workdir': str(tmp_path / 'wd'),
                         'workdir_mode': 'auto', 'dry_run': True},
        inputs=WorkflowInputs(cubit={'cav_radius': np.array([90.0, 91.0, 92.0])}),
        output_spec={'R/Q': ['RoverQ', '0', 'RoQ']})     # deprecated list form

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        modes.parameter_sweep(wf)
    ours = [w for w in caught if issubclass(w.category, DeprecationWarning)
            and 'RoverQ' in str(w.message)]
    assert len(ours) == 1, (
        f'expected one deprecation notice for the whole sweep, got {len(ours)}: '
        + '; '.join(str(w.message) for w in ours))


# --------------------------------------------------------------------------- #
# 4. The mode layer never writes workflow state
# --------------------------------------------------------------------------- #


# Workflow attributes the mode layer must never assign to. ``workdir`` /
# ``last_context`` are the workflow's own single-run bookkeeping and
# ``baseworkdir`` / ``workdir_mode`` are its naming configuration; a mode that
# writes any of them is steering other evaluations.
_FORBIDDEN_MODE_ASSIGNMENTS = frozenset({
    'baseworkdir', 'workdir_mode', 'workdir', 'last_context'})


def test_mode_layer_never_assigns_workflow_state():
    """``collect_training_data`` used to save/mutate/restore
    ``workflow.baseworkdir`` around each sample; it passes ``workdir=`` to
    ``evaluate`` instead. Asserted against the parsed source so the mutation
    cannot come back in a path no test happens to exercise."""
    import lume_ace3p.modes as mode_module

    tree = ast.parse(open(mode_module.__file__).read())
    offenders = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            for leaf in ([target] if not isinstance(target, ast.Tuple)
                         else target.elts):
                if (isinstance(leaf, ast.Attribute)
                        and leaf.attr in _FORBIDDEN_MODE_ASSIGNMENTS):
                    offenders.append(f'line {leaf.lineno}: .{leaf.attr}')
    assert not offenders, (
        'modes.py assigns per-evaluation workflow state: '
        + '; '.join(offenders)
        + ". Pass workdir= to Workflow.evaluate and read the returned ctx "
          "instead.")


# --------------------------------------------------------------------------- #
# 5. workdir_mode: indexed — a stable, collision-free point identity
# --------------------------------------------------------------------------- #


def test_indexed_sweep_produces_the_same_table_as_auto(tmp_path):
    """The Phase-2 bar for ``indexed``: it changes *names*, not results.

    Both sweeps run the same three β points through the same chain; only the
    directory naming differs. The β chain is used rather than a dry-run solver so
    the compared columns hold real, per-point-distinct numbers."""
    betas = [40.0, 50.0, 60.0]
    auto_root, indexed_root = tmp_path / 'auto', tmp_path / 'indexed'
    auto = modes.parameter_sweep(_particles_workflow(auto_root, 'auto', betas))
    indexed = modes.parameter_sweep(
        _particles_workflow(indexed_root, 'indexed', betas))

    pd.testing.assert_frame_equal(auto, indexed)
    # The table is worth comparing only because it is not all NaN.
    assert np.all(np.isfinite(auto['weight'].to_numpy()))
    assert auto['weight'].nunique() == 3

    # ...and the naming is the whole difference: 'auto' by swept value, 'indexed'
    # by position — stable, collision-free, and bounded in length however many
    # axes the sweep grows.
    assert sorted(os.listdir(auto_root)) == ['wd_40.0', 'wd_50.0', 'wd_60.0']
    assert sorted(os.listdir(indexed_root)) == ['wd_0', 'wd_1', 'wd_2']


def test_indexed_mode_names_a_bare_evaluate_as_point_zero(tmp_path):
    """``evaluate`` takes no point index — the mode layer owns sweep ordering and
    passes the full ``workdir=``. A caller driving ``evaluate`` directly under
    ``indexed`` is therefore one point, and it is point 0."""
    wf = _s3p_workflow(tmp_path, {'cornercut': 12.0, 'rcorner2': 4.0},
                       workdir_mode='indexed')
    _out, ctx = wf.evaluate([12.0, 4.0])
    assert ctx.workdir == str(tmp_path / 'wd') + '_0'
    assert wf.point_workdir(7) == str(tmp_path / 'wd') + '_7'


def test_an_unknown_workdir_mode_is_rejected_at_construction(tmp_path):
    """Naming the valid values, and failing *before* anything is built or any
    directory created — the same point `stage_mode` has always failed at. It used to
    surface only when `_getworkdir` happened to reach its `auto` branch, so a typo
    ('index', 'Auto') constructed fine and died mid-evaluation."""
    with pytest.raises(ValueError, match="'manual', 'auto', 'indexed'"):
        _s3p_workflow(tmp_path, {'cornercut': 12.0}, workdir_mode='index')


# --------------------------------------------------------------------------- #
# 6. Row assembly is decoupled from execution order
# --------------------------------------------------------------------------- #


class _OutputsOnly:
    """The only part of the ``Workflow`` surface :func:`modes._assemble` reads."""

    output_spec = {'y': 'quantity'}


def test_rows_are_assembled_in_point_index_order():
    """Assembly sorts by point index rather than trusting list order.

    Fed a deliberately shuffled results list — which is what a resumed or
    concurrent sweep would hand it — the frame still comes out in sweep order.
    Without this, enabling either later would make every baseline depend on
    completion order, silently."""
    points = [modes._PointResult(i, [float(i)], {'y': 10.0 + i}, None, None)
              for i in range(5)]
    shuffled = [points[3], points[0], points[4], points[1], points[2]]

    df = modes._assemble(_OutputsOnly(), ['x'], shuffled)
    assert list(df['x']) == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert list(df['y']) == [10.0, 11.0, 12.0, 13.0, 14.0]


def test_long_format_rows_are_assembled_in_point_index_order():
    """The same property for the long-format (S3P/T3P) shape, where each point
    contributes several rows: the *blocks* must stay in point order and each
    block's index values with their own point."""
    points = [
        modes._PointResult(0, [1.0], {'y': np.array([10.0, 11.0])},
                           ('Frequency', np.array([1e9, 2e9])), None),
        modes._PointResult(1, [2.0], {'y': np.array([20.0, 21.0])},
                           ('Frequency', np.array([1e9, 2e9])), None),
    ]
    df = modes._assemble(_OutputsOnly(), ['x'], list(reversed(points)))
    assert list(df['x']) == [1.0, 1.0, 2.0, 2.0]
    assert list(df['Frequency']) == [1e9, 2e9, 1e9, 2e9]
    assert list(df['y']) == [10.0, 11.0, 20.0, 21.0]


# --------------------------------------------------------------------------- #
# 7. Per-evaluation subprocess logs
# --------------------------------------------------------------------------- #


# A stand-in for Cubit: writes to both streams and fails. Cubit is the one
# wrapper whose command line carries no MPI-caller prefix, so a fake binary on a
# ``paths: {cubit: ...}`` override is enough to exercise a *real* subprocess
# through the module layer without an ACE3P environment.
_FAKE_CUBIT = """#!/bin/sh
echo "meshing $*"
echo "ERROR: could not open the display" >&2
exit 1
"""

_JOURNAL = '## journal\ncreate brick x 1\nexport genesis "mesh.gen" overwrite\n'

posix_only = pytest.mark.skipif(os.name != 'posix',
                                reason='the fake solver is a shell script')


def _cubit_workflow(tmp_path, **params):
    """A real (non-dry-run) one-step cubit workflow pointed at the fake binary.

    ``ace3p`` is overridden alongside ``cubit`` only to keep path resolution from
    falling through to its ``$HOME``-wide autodetect glob."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = bin_dir / 'cubit'
    fake.write_text(_FAKE_CUBIT)
    fake.chmod(0o755)
    (tmp_path / 'cavity.jou').write_text(_JOURNAL)
    return Workflow(
        [{'module': 'cubit', 'journal': 'cavity.jou', 'meshconvert': False}],
        workflow_params={'workdir': str(tmp_path / 'wd'),
                         'workdir_mode': 'manual', 'dry_run': False,
                         'paths': {'cubit': str(bin_dir) + os.sep,
                                   'ace3p': str(bin_dir) + os.sep, 'mpi': ''},
                         **params},
        inputs=WorkflowInputs(), output_spec={})


@posix_only
def test_a_failing_step_leaves_its_error_in_the_module_log(tmp_path, monkeypatch,
                                                           capsys):
    """A step that fails leaves the tool's own message in
    ``<workdir>/<module>.log`` **and** on the parent's streams.

    Both halves matter. The log is what a wall-clock-killed sweep point leaves
    behind; the terminal half is why this is a tee rather than the redirect the
    plan first considered — a redirected solver failure would become invisible
    exactly when it matters most."""
    monkeypatch.chdir(tmp_path)
    wf = _cubit_workflow(tmp_path)
    _out, ctx = wf.evaluate()

    with open(os.path.join(ctx.workdir, 'cubit.log')) as f:
        logged = f.read()
    assert 'ERROR: could not open the display' in logged
    assert 'meshing -nographics -nojournal -noecho cavity.jou' in logged
    # The command line that produced it, so a log with several invocations in it
    # says which is which.
    assert '$ ' in logged and 'cubit -nographics' in logged

    captured = capsys.readouterr()
    assert 'ERROR: could not open the display' in captured.err
    assert 'meshing -nographics' in captured.out


@posix_only
def test_capture_output_false_writes_no_log(tmp_path, monkeypatch, capfd):
    """The documented opt-out is a real fallback, not an approximation: no log is
    written and the child's own file descriptors are inherited, which is exactly
    what happened before this phase. ``capfd`` (not ``capsys``) is what
    distinguishes the two — it captures the child's fds rather than the parent's
    Python-level streams."""
    monkeypatch.chdir(tmp_path)
    wf = _cubit_workflow(tmp_path, capture_output=False)
    _out, ctx = wf.evaluate()

    assert not os.path.exists(os.path.join(ctx.workdir, 'cubit.log'))
    captured = capfd.readouterr()
    assert 'ERROR: could not open the display' in captured.err


def test_each_module_instance_gets_its_own_log(tmp_path):
    """Logs are keyed on the module's *instance name*, so a chain with two
    acdtool steps does not merge them into one file — while one module's several
    invocations (cubit's mesher, then meshconvert) share one, since they are one
    pipeline step."""
    ctx = RunContext(str(tmp_path), capture_output=True)
    rf = AcdtoolModule({'input': 'x.rfpost'}, name='rf')
    transwake = AcdtoolModule({'command': 'postprocess transwake',
                               'args': [0.0, 0.0, 0.0, 0.0125]},
                              name='transwake')
    assert rf.log_file(ctx) == os.path.join(str(tmp_path), 'rf.log')
    assert transwake.log_file(ctx) == os.path.join(str(tmp_path),
                                                  'transwake.log')

    # A hand-built context captures nothing, so every module unit test and any
    # direct driver keeps the pre-Phase-2 inherited-stream behavior.
    bare = RunContext(str(tmp_path))
    assert bare.capture_output is False
    assert rf.log_file(bare) is None


@posix_only
def test_run_logged_reports_the_exit_status(tmp_path):
    """The seam that *can* report a failure does: ``run_logged`` returns the
    child's status. Nothing above it raises on a nonzero exit — that is the
    wrappers' long-standing behavior, and an ACE3P failure surfaces when the
    parser finds no results — but the status is not swallowed here."""
    log = tmp_path / 'step.log'
    result = run_logged('echo out; echo err 1>&2; exit 3', cwd=str(tmp_path),
                        log_file=str(log))
    assert result.returncode == 3
    logged = log.read_text()
    assert 'out' in logged and 'err' in logged


@posix_only
def test_run_logged_keeps_stderr_on_stderr(tmp_path, capsys):
    """Teeing must not merge the streams: a caller redirecting ``2>errors``
    keeps working. Both still land in the one log, which is what makes the log
    the readable chronological record."""
    log = tmp_path / 'step.log'
    run_logged('echo to-stdout; echo to-stderr 1>&2', cwd=str(tmp_path),
               log_file=str(log))
    captured = capsys.readouterr()
    assert 'to-stdout' in captured.out and 'to-stdout' not in captured.err
    assert 'to-stderr' in captured.err and 'to-stderr' not in captured.out
    logged = log.read_text()
    assert 'to-stdout' in logged and 'to-stderr' in logged


@posix_only
def test_run_logged_appends_each_invocation(tmp_path):
    """Appending, not truncating: one module may launch several subprocesses, and
    under ``workdir_mode: manual`` every sweep point shares one workdir — so
    truncating would keep only the last."""
    log = tmp_path / 'cubit.log'
    run_logged('echo first', cwd=str(tmp_path), log_file=str(log))
    run_logged('echo second', cwd=str(tmp_path), log_file=str(log))
    assert log.read_text().splitlines() == [
        '$ echo first', 'first', '$ echo second', 'second']


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
