"""Per-evaluation isolation of the ``Workflow`` seam (Phase 1 of
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
"""

import ast
import os
import warnings

import numpy as np
import pytest

import baseline_utils as bu
# The synthetic S3P fixture lives with the module-layer tests; it is what lets a
# solver's *parsed results* be injected without a binary, which is the only way
# to distinguish two evaluations' module state locally.
from test_modules import _make_s3p_solver
from lume_ace3p import modes
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.modules import MESH
from lume_ace3p.workflow_graph import Workflow


def _module_of(modules, module_type):
    return next(m for m in modules if m.type == module_type)


def _s3p_workflow(tmp_path, cubit_inputs):
    """A dry-run ``cubit -> s3p`` workflow with auto-named workdirs under
    ``tmp_path``. ``cubit_inputs`` may hold arrays (a sweep) or scalars."""
    return Workflow(
        [{'module': 'cubit', 'journal': 'x.jou'},
         {'module': 's3p', 'input': 'x.s3p'}],
        workflow_params={'workdir': str(tmp_path / 'wd'),
                         'workdir_mode': 'auto', 'dry_run': True},
        inputs=WorkflowInputs(cubit=dict(cubit_inputs)),
        output_spec={'refl': {'module': 's3p', 'quantity': 'S(0,0)'}})


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


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
