"""Resume for the Xopt modes (Phase B of ``plans/xopt_resume_workdir_plan.md``).

A 200-evaluation optimization killed at evaluation 190 used to throw away all 190 —
worse than the sweep case Phase 4 of the previous plan fixed, because in an
optimization the *evaluations* are the expensive part. ``mode: {resume: true}`` now
continues from ``xopt_state.yml``.

The mechanism is deliberately **not** the completion manifest the table modes
resume from. An optimization has no fixed set of points to have finished part of, so
what is saved and restored is the optimizer's whole state: the trajectory *and* the
generator's own internal state.

What is pinned here:

1. **A run stopped after k evaluations and resumed calls its objective exactly
   (budget − k) more times.** Counted on the objective itself, which is the
   load-bearing assertion — a row count in ``sim_output.txt`` cannot distinguish
   "inherited" from "re-evaluated".
2. **A resumed ``NelderMeadGenerator`` continues its simplex.** Its proposals differ
   from those a fresh generator makes when handed the same data via ``add_data``, and
   the ``add_data`` route re-proposes a point it already has while the full-state
   restore does not. This is the test that distinguishes a full-state restore from a
   data-only one, so it is the one that has to exist.
3. A resumed **Bayesian** run (``ExpectedImprovement``, ``BayesianExploration``)
   restores and continues, with the GP refit from data and no torch serialization.
4. **Resuming a finished optimization evaluates nothing** — the iteration budgets are
   campaign totals, which is what makes resume idempotent.
5. An **absent, truncated or disagreeing** state file degrades to "no state": the run
   starts over, says why, and does not raise. A ``vocs`` disagreement names the
   disagreement, because resuming a ``MINIMIZE`` campaign into a ``MAXIMIZE`` config
   would silently optimize against the inherited data.
6. A resumed run's **iteration workdirs continue the numbering** (``_k``, ``_k+1``, …)
   rather than overwriting the inherited evaluations' directories.
7. **``--status``** reports the evaluation count and best objective of a
   half-finished campaign, and agrees with the final log on a finished one.
8. The state file is written whether or not ``resume`` is set, and is **excluded from
   the frozen baselines** the way the run manifest is.

These use a counting workflow double rather than the fake-ACE3P harness: every claim
above is about *how many times the objective was called* and *what the optimizer
proposed next*, which a double counts exactly and a subprocess chain only makes
slower. Test 6, the one claim that is about files, drives a real ``Workflow``'s
naming.
"""

import os

import pandas as pd
import pytest

import baseline_utils as bu
from lume_ace3p import modes, run_lume_ace3p, xopt_state
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.workflow_graph import Workflow
from lume_ace3p.xopt_state import (
    REJECTED_SUFFIX, STATE_FILE, read_xopt_state,
)


VOCS = {'variables': {'x': [0.0, 1.0]}, 'objectives': {'y': 'MINIMIZE'}}


def _nelder_mead(num_step):
    return {'generator': 'NelderMeadGenerator', 'num_random': 0,
            'num_step': num_step}


class Counter:
    """A workflow double that counts its evaluations.

    ``evaluate(input_dict)`` is the whole seam the Xopt modes use, and a smooth
    quadratic in ``x`` gives the generator real signal. Each instance counts only
    *its own* calls, so "how many evaluations did this process perform" is exactly
    ``len(counter.calls)`` — the number every claim below is about. ``inputs`` keeps
    the full dicts, for the one test that is about what the mode passed in."""

    def __init__(self):
        self.calls = []
        self.inputs = []

    def evaluate(self, input_dict):
        x = float(input_dict['x'])
        self.calls.append(x)
        self.inputs.append(dict(input_dict))
        return {'y': (x - 0.3) ** 2}, None


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    """A working directory for one campaign; ``xopt_state.yml`` lands in it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(workflow, xopt_dict, resume=False, log_file='sim_output.txt'):
    return modes.scalar_optimize(workflow, VOCS, xopt_dict, log_file=log_file,
                                 resume=resume)


# --------------------------------------------------------------------------- #
# 1. No evaluation is repeated
# --------------------------------------------------------------------------- #


def test_a_resumed_optimization_repeats_no_evaluation(campaign):
    """The headline claim, counted on the objective: an optimization stopped after
    k evaluations and resumed to a budget of n calls the objective exactly n − k
    more times.

    The interruption is simulated the way a wall clock makes one — run part of the
    campaign and stop. Nothing about the *table* would show this: it holds n rows
    either way. Only the call count distinguishes inheriting 4 evaluations from
    re-running them."""
    first = Counter()
    _run(first, _nelder_mead(4))
    assert len(first.calls) == 4
    assert os.path.isfile(STATE_FILE)

    resumed = Counter()
    X = _run(resumed, _nelder_mead(7), resume=True)

    assert len(resumed.calls) == 3, 'a resumed run re-evaluated inherited points'
    assert len(X.data) == 7, 'the campaign is not the full budget long'
    # ...and the points it did evaluate are new ones, not the inherited four.
    assert not set(resumed.calls) & set(first.calls)


def test_without_resume_the_campaign_starts_over(campaign):
    """``resume`` is opt-in: the state file is there, and a run that did not ask to
    continue behaves exactly as it did before any of this existed."""
    first = Counter()
    _run(first, _nelder_mead(4))

    second = Counter()
    X = _run(second, _nelder_mead(4))

    assert len(second.calls) == 4
    assert len(X.data) == 4
    assert second.calls == first.calls


# --------------------------------------------------------------------------- #
# 2. Full state, not just the data — the distinguishing test
# --------------------------------------------------------------------------- #


def _data_only_continuation(state, steps):
    """The route deliberately *not* taken: a fresh generator handed the recorded
    table via ``add_data``, stepped ``steps`` times. Returns the points it proposed.

    This is what resuming from ``sim_output.txt`` alone would buy — the data without
    the generator. For a Bayesian generator it is nearly equivalent (the model is
    refit either way); for Nelder-Mead the simplex *is* the state."""
    from xopt import Xopt
    from xopt.evaluator import Evaluator
    from xopt.vocs import VOCS as XoptVOCS
    from xopt.generators.sequential.neldermead import NelderMeadGenerator

    counter = Counter()
    vocs = XoptVOCS(variables={'x': [0.0, 1.0]}, objectives={'y': 'MINIMIZE'})
    inherited = pd.DataFrame(state['data'])
    X = Xopt(evaluator=Evaluator(function=lambda p: counter.evaluate(p)[0]),
             generator=NelderMeadGenerator(vocs=vocs, initial_point={'x': 0.5}),
             vocs=vocs)
    X.add_data(inherited)
    for _ in range(steps):
        X.step()
    return X.data['x'].tolist()[len(inherited):]


def test_a_resumed_neldermead_continues_its_simplex(campaign):
    """A restored Nelder-Mead run continues its search; a data-only restore restarts
    it on top of old data and re-proposes a point it already has.

    This is the test that distinguishes the two mechanisms, and the reason
    ``add_data`` was not the route taken: the simplex is not recoverable from the
    table, so a data-only continuation *looks* like it is working while quietly
    spending an expensive evaluation on a point it already knows."""
    first = Counter()
    _run(first, _nelder_mead(4))
    state = read_xopt_state(STATE_FILE)
    inherited = pd.DataFrame(state['data'])['x'].tolist()

    resumed = Counter()
    X = _run(resumed, _nelder_mead(7), resume=True)
    full_state = X.data['x'].tolist()[len(inherited):]
    data_only = _data_only_continuation(state, len(full_state))

    assert full_state != data_only, (
        'the restore did not carry the generator state — a data-only restore '
        'would propose these same points')
    # The concrete cost of the route not taken.
    assert [x for x in data_only if x in inherited], (
        'fixture check: the data-only route is supposed to duplicate a point')
    assert not [x for x in full_state if x in inherited], (
        'the full-state continuation re-proposed an inherited point')


# --------------------------------------------------------------------------- #
# 3. The Bayesian generators
# --------------------------------------------------------------------------- #


def test_a_resumed_bayesian_run_continues_with_the_model_refit(campaign):
    """``ExpectedImprovementGenerator`` restores and continues. Its GP is refit from
    the restored data, so no torch state has to be serialized — which is why
    ``serialize_torch`` stays off."""
    first = Counter()
    _run(first, {'generator': 'ExpectedImprovementGenerator',
                 'num_random': 3, 'num_step': 1})
    assert len(first.calls) == 4

    state = read_xopt_state(STATE_FILE)
    assert state['serialize_torch'] is False

    resumed = Counter()
    X = _run(resumed, {'generator': 'ExpectedImprovementGenerator',
                       'num_random': 3, 'num_step': 3}, resume=True)

    assert len(resumed.calls) == 2, 'the seeding was repeated'
    assert len(X.data) == 6
    assert X.generator.model is not None, 'the GP was not refit from the data'


def test_a_resumed_gp_parameter_sweep_continues(campaign):
    """The exploration mode resumes the same way, including its own seeding loop —
    which previously wrote nothing until after the first *step*, so a run killed
    during seeding lost all of it."""
    sweep = {'x': {'min': 0.0, 'max': 1.0, 'num': 4}}
    first = Counter()
    modes.gp_parameter_sweep(first, sweep, {'variables': VOCS['variables'],
                                            'objectives': {'y': 'explore'}},
                             {'num_random': 3, 'max_steps': 1})
    banked = len(first.calls)
    assert banked >= 4

    resumed = Counter()
    X = modes.gp_parameter_sweep(
        resumed, sweep, {'variables': VOCS['variables'],
                         'objectives': {'y': 'explore'}},
        {'num_random': 3, 'max_steps': 3}, resume=True)

    assert len(resumed.calls) < banked, 'the exploration started over'
    assert len(X.data) == banked + len(resumed.calls)
    assert os.path.isfile('sweep_output.txt'), 'the GP posterior sweep was not emitted'


def test_the_seeding_loop_persists_state_as_it_goes(campaign, monkeypatch):
    """State is written after each seeding evaluation, not only after the first
    *step*. Without that, an exploration killed during seeding had nothing to come
    back to — the gap decision 6 of the plan closes."""
    original = modes._log_xopt
    seen = []

    def spy(filename, xopt_obj, *args, **kwargs):
        seen.append(modes._evaluated(xopt_obj))
        return original(filename, xopt_obj, *args, **kwargs)
    monkeypatch.setattr(modes, '_log_xopt', spy)

    modes.gp_parameter_sweep(
        Counter(), {'x': {'min': 0.0, 'max': 1.0, 'num': 3}},
        {'variables': VOCS['variables'], 'objectives': {'y': 'explore'}},
        {'num_random': 3, 'max_steps': 0})

    # One write per seeding evaluation (1, 2, 3), then the steps.
    assert seen[:3] == [1, 2, 3]
    assert xopt_state.evaluation_count(read_xopt_state(STATE_FILE)) >= 3


# --------------------------------------------------------------------------- #
# 4. Idempotence
# --------------------------------------------------------------------------- #


def test_resuming_a_finished_optimization_evaluates_nothing(campaign):
    """The budgets are campaign totals, so a completed campaign resumed does no
    work — the same contract the sweep has."""
    first = Counter()
    _run(first, _nelder_mead(5))

    resumed = Counter()
    X = _run(resumed, _nelder_mead(5), resume=True)

    assert resumed.calls == []
    assert len(X.data) == 5


def test_a_resumed_run_continues_to_the_same_finish_line(campaign):
    """Three interruptions cost the same total evaluations as one straight-through
    run, because every budget is read as a total rather than as this process's
    share."""
    total = 0
    for budget in (2, 2, 5, 5):
        counter = Counter()
        _run(counter, _nelder_mead(budget), resume=True)
        total += len(counter.calls)

    assert total == 5


# --------------------------------------------------------------------------- #
# 5. Degrading safely
# --------------------------------------------------------------------------- #


def test_an_absent_state_file_starts_over_quietly(campaign):
    """Nothing to resume from is not an error — it is a first run."""
    counter = Counter()
    X = _run(counter, _nelder_mead(3), resume=True)

    assert len(counter.calls) == 3
    assert len(X.data) == 3


def test_a_truncated_state_file_starts_over_and_says_so(campaign, capsys):
    """A dump killed mid-write is exactly what a resume would read. It often still
    parses as YAML, so the structural check is what catches it — and the run starts
    over rather than raising or, worse, continuing from half a campaign."""
    first = Counter()
    _run(first, _nelder_mead(4))
    text = open(STATE_FILE).read()
    with open(STATE_FILE, 'w') as file:
        file.write(text[:len(text) // 2])

    assert read_xopt_state(STATE_FILE) is None

    counter = Counter()
    X = _run(counter, _nelder_mead(3), resume=True)
    output = capsys.readouterr().out

    assert len(counter.calls) == 3, 'a truncated state was partly adopted'
    assert len(X.data) == 3
    assert 'incomplete' in output and STATE_FILE in output


def test_a_flipped_objective_direction_is_refused_by_name(campaign, capsys):
    """The load-bearing refusal: resuming a MINIMIZE campaign into a MAXIMIZE config
    would optimize *against* the inherited data while looking like it was working.
    This is Phase B's ``config_hash``, so it must name the disagreement rather than
    just decline."""
    first = Counter()
    _run(first, _nelder_mead(4))

    flipped = {'variables': VOCS['variables'], 'objectives': {'y': 'MAXIMIZE'}}
    counter = Counter()
    X = modes.scalar_optimize(counter, flipped, _nelder_mead(3),
                              log_file='sim_output.txt', resume=True)
    output = capsys.readouterr().out

    assert len(counter.calls) == 3, 'the mismatched state was adopted'
    assert len(X.data) == 3
    assert 'MinimizeObjective' in output and 'MaximizeObjective' in output


def test_moved_variable_bounds_are_refused(campaign, capsys):
    """A different search box is a different problem: the inherited points may lie
    outside the one now being searched."""
    first = Counter()
    _run(first, _nelder_mead(4))

    counter = Counter()
    moved = {'variables': {'x': [0.0, 2.0]}, 'objectives': {'y': 'MINIMIZE'}}
    modes.scalar_optimize(counter, moved, _nelder_mead(3),
                          log_file='sim_output.txt', resume=True)
    output = capsys.readouterr().out

    assert len(counter.calls) == 3
    assert '[0.0, 2.0]' in output and '[0.0, 1.0]' in output


def _hashing_workflow(root, offset=0.3, journal='a.jou', fixed=1.0,
                      nominal_x=0.5):
    """A real :class:`Workflow` — for its `campaign_config_hash` — whose `evaluate`
    is a counter. The chain never runs; what varies between calls is the *config*
    the hash is taken over."""
    workflow = Workflow(
        [{'module': 'cubit', 'journal': journal}],
        workflow_params={'workdir': str(root / 'wd'), 'workdir_mode': 'auto',
                         'dry_run': True},
        inputs=WorkflowInputs(cubit={'x': nominal_x, 'fixed': fixed}))
    counter = Counter()

    def evaluate(input_dict, workdir=None, resume=False):
        x = float(input_dict['x'])
        counter.calls.append(x)
        return {'y': (x - offset) ** 2}, None
    workflow.evaluate = evaluate
    workflow.counter = counter
    return workflow


def test_a_changed_workflow_is_refused_even_with_the_same_vocs(campaign, capsys):
    """The check the VOCS one cannot make. Same variables, same objective, same
    generator — but a different Cubit journal, so the recorded evaluations describe a
    different model. Without this, resume silently mixes two geometries into one
    table."""
    first = _hashing_workflow(campaign, journal='a.jou')
    _run(first, _nelder_mead(4))

    second = _hashing_workflow(campaign, journal='b.jou')
    X = _run(second, _nelder_mead(6), resume=True)
    output = capsys.readouterr().out

    assert len(second.counter.calls) == 6, 'the changed workflow was resumed'
    assert len(X.data) == 6
    assert 'different workflow configuration' in output


def test_a_changed_fixed_input_is_refused(campaign, capsys):
    """A non-optimized input is part of the physics, and the VOCS never sees it."""
    _run(_hashing_workflow(campaign, fixed=1.0), _nelder_mead(4))

    second = _hashing_workflow(campaign, fixed=2.0)
    _run(second, _nelder_mead(6), resume=True)

    assert len(second.counter.calls) == 6
    assert 'different workflow configuration' in capsys.readouterr().out


def test_editing_an_optimized_variables_nominal_value_still_resumes(campaign):
    """The optimizer overrides its variables every evaluation, so their nominal
    values in `input_parameters` are not part of the campaign's identity — editing
    one must not throw away a campaign."""
    _run(_hashing_workflow(campaign, nominal_x=0.5), _nelder_mead(4))

    second = _hashing_workflow(campaign, nominal_x=0.9)
    _run(second, _nelder_mead(5), resume=True)

    assert len(second.counter.calls) == 1, 'a nominal-value edit invalidated it'


def test_a_refused_resume_keeps_the_campaign_it_declined(campaign, capsys):
    """Declining to resume must not also destroy what was being resumed.

    The run is about to start a fresh campaign in the same directory, and the files
    it would overwrite are the trajectory and state the user explicitly asked to
    continue — in a real run, hours of solves. A correct refusal that costs the user
    their data is not a correct outcome."""
    first = _hashing_workflow(campaign, journal='a.jou')
    _run(first, _nelder_mead(6))
    rows = len(open('sim_output.txt').readlines())

    second = _hashing_workflow(campaign, journal='b.jou')
    _run(second, _nelder_mead(2), resume=True)
    output = capsys.readouterr().out

    assert os.path.isfile('sim_output.txt' + REJECTED_SUFFIX)
    assert os.path.isfile(STATE_FILE + REJECTED_SUFFIX)
    assert len(open('sim_output.txt' + REJECTED_SUFFIX).readlines()) == rows
    assert 'the refused campaign is kept' in output
    # The fresh campaign is there too, in the original place.
    assert len(open('sim_output.txt').readlines()) < rows


def test_a_second_refusal_does_not_overwrite_the_first_backup(campaign):
    """Otherwise a second mistake finishes the job the first was prevented from
    doing."""
    _run(_hashing_workflow(campaign, fixed=1.0), _nelder_mead(4))
    _run(_hashing_workflow(campaign, fixed=2.0), _nelder_mead(2), resume=True)
    _run(_hashing_workflow(campaign, fixed=3.0), _nelder_mead(2), resume=True)

    assert os.path.isfile(STATE_FILE + REJECTED_SUFFIX)
    assert os.path.isfile(STATE_FILE + REJECTED_SUFFIX + '.1')


def test_no_state_found_is_not_a_refusal(campaign):
    """"Nothing to resume from" moves nothing aside — there is nothing there, and a
    stray `.rejected` file would imply a mistake the user did not make."""
    workflow = _hashing_workflow(campaign)
    _run(workflow, _nelder_mead(3), resume=True)

    assert len(workflow.counter.calls) == 3
    assert not [name for name in os.listdir(campaign)
                if name.endswith(REJECTED_SUFFIX)]


def test_a_reduced_budget_says_there_is_nothing_to_do(campaign, capsys):
    """Correct (the budgets are campaign totals) but silent otherwise, and a resumed
    run that prints nothing after "resuming…" reads like a hang."""
    _run(_hashing_workflow(campaign), _nelder_mead(5))

    second = _hashing_workflow(campaign)
    _run(second, _nelder_mead(2), resume=True)
    output = capsys.readouterr().out

    assert second.counter.calls == []
    assert 'nothing to do' in output


def test_a_state_with_no_recorded_hash_resumes_with_a_note(campaign, capsys):
    """A campaign driven by a workflow that cannot produce a hash (a test double)
    records none. A later run that *can* hash says the workflow went unchecked rather
    than either refusing or staying quiet."""
    _run(Counter(), _nelder_mead(4))            # the double records no hash
    assert xopt_state.recorded_config_hash(read_xopt_state(STATE_FILE)) is None
    capsys.readouterr()

    second = _hashing_workflow(campaign)
    _run(second, _nelder_mead(5), resume=True)
    output = capsys.readouterr().out

    assert len(second.counter.calls) == 1, 'the unhashed state was not adopted'
    assert 'records no configuration hash' in output


def test_a_dump_failure_is_reported_once(campaign, capsys):
    """The state is dumped after every evaluation, so a per-failure warning would
    print one identical line per evaluation and bury the run's real output."""
    class Undumpable:
        data = pd.DataFrame({'x': [0.5]})

        def dump(self, path):
            raise RuntimeError('disk full')

    path = os.path.join(str(campaign), 'once.yml')
    for _ in range(5):
        assert xopt_state.write_xopt_state(path, Undumpable()) is None
    warnings = [line for line in capsys.readouterr().out.splitlines()
                if 'could not write the Xopt resume state' in line]

    assert len(warnings) == 1


def test_a_different_generator_is_refused(campaign, capsys):
    """A Nelder-Mead simplex is not an Expected-Improvement GP; neither can continue
    the other's search."""
    first = Counter()
    _run(first, _nelder_mead(4))

    counter = Counter()
    modes.scalar_optimize(counter, VOCS,
                          {'generator': 'ExpectedImprovementGenerator',
                           'num_random': 2, 'num_step': 1},
                          log_file='sim_output.txt', resume=True)
    output = capsys.readouterr().out

    assert len(counter.calls) == 3
    assert 'neldermead' in output and 'expected_improvement' in output


def test_a_resumed_multifidelity_run_continues(campaign):
    """``MultiFidelityGenerator`` is the one generator whose state needs help to load:
    its own VOCS validator reaches for VOCS attributes on whatever it is handed
    (xopt 3.0.0), so a raw mapping raises there and the restore pre-validates.

    Driven with ``num_step`` rather than the ``cost_budget`` this generator normally
    uses — the cost loop terminates on *measured runtimes*, which for a synthetic
    objective are microseconds, so it is unrunnable in a test (see the note in
    ``test_run_xopt_compat.py``). The restore is the same either way."""
    xopt_dict = {'generator': 'MultiFidelityGenerator',
                 'fidelity_variable': 'mesh_fidelity', 'num_random': 2,
                 'num_step': 1}
    first = Counter()
    _run(first, xopt_dict)
    banked = len(first.calls)
    # The fidelity axis reaches the workflow under the user's variable name.
    assert 'mesh_fidelity' in first.inputs[-1]

    resumed = Counter()
    X = _run(resumed, dict(xopt_dict, num_step=2), resume=True)

    assert len(resumed.calls) == 1, 'the multi-fidelity state was not adopted'
    assert len(X.data) == banked + 1


def test_the_multifidelity_fidelity_axis_is_not_demanded_of_the_workflow(campaign):
    """``MultiFidelityGenerator``'s VOCS validator **mutates the VOCS it is handed**,
    adding the fidelity axis ``s`` as a variable *and* an objective (xopt 3.0.0).

    The objective closure snapshots ``vocs.output_names`` when it is built, so
    building it after the generator makes it demand an ``s`` output no workflow
    produces and none can declare. Restructuring `scalar_optimize` for resume moved
    that line; this pins the ordering so it cannot move back."""
    vocs_dict = {'variables': {'x': [0.0, 1.0]}, 'objectives': {'y': 'MINIMIZE'}}
    vocs = modes._make_vocs(vocs_dict)
    counter = Counter()

    _state, fresh, _resumed = modes._objectives_for(
        counter, vocs, {'fidelity_variable': 'mesh_fidelity'}, STATE_FILE,
        resume=False)
    modes._build_generator(vocs, vocs_dict,
                           {'generator': 'MultiFidelityGenerator'}, False)

    assert 's' in vocs.objectives, 'fixture check: the generator no longer mutates'
    # Built before that mutation, so it asks the workflow only for 'y' — and renames
    # the fidelity axis to the user's variable on the way in.
    assert fresh({'x': 0.5, 's': 0.25}) == {'y': pytest.approx(0.04)}
    assert counter.inputs[-1] == {'x': 0.5, 'mesh_fidelity': 0.25}


# --------------------------------------------------------------------------- #
# 6. Iteration workdirs continue the numbering
# --------------------------------------------------------------------------- #


def _naming_workflow(root):
    """A real :class:`Workflow` — for its ``point_workdir`` naming and
    ``workdir_mode`` — whose ``evaluate`` writes a file into whichever workdir the
    mode hands it. The chain itself is never run; what is under test is where the
    files land."""
    workflow = Workflow(
        [{'module': 'cubit', 'journal': 'x.jou'}],
        workflow_params={'workdir': str(root / 'wd'), 'workdir_mode': 'auto',
                         'dry_run': True},
        inputs=WorkflowInputs(cubit={'x': 0.5}))
    counter = Counter()

    def evaluate(input_dict, workdir=None, resume=False):
        outputs, _ctx = counter.evaluate(input_dict)
        os.makedirs(workdir, exist_ok=True)
        with open(os.path.join(workdir, 'evaluation.txt'), 'w') as file:
            file.write(repr(float(input_dict['x'])))
        return outputs, None
    workflow.evaluate = evaluate
    workflow.counter = counter
    return workflow


def _evaluation_value(workdir):
    with open(os.path.join(workdir, 'evaluation.txt')) as file:
        return float(file.read())


def test_a_resumed_run_continues_the_workdir_numbering(campaign):
    """Phase A numbers each evaluation's workdir by iteration; a resumed run has to
    start that counter past the evaluations it inherited, or it would overwrite
    ``_0…_k-1`` — the very files the campaign was interrupted to keep."""
    first = _naming_workflow(campaign)
    _run(first, _nelder_mead(4))
    inherited = {index: _evaluation_value(campaign / f'wd_{index}')
                 for index in range(4)}
    mtimes = {index: os.path.getmtime(campaign / f'wd_{index}' / 'evaluation.txt')
              for index in range(4)}

    second = _naming_workflow(campaign)
    X = _run(second, _nelder_mead(7), resume=True)

    directories = sorted((name for name in os.listdir(campaign)
                          if name.startswith('wd_')),
                         key=lambda name: int(name.rsplit('_', 1)[1]))
    assert directories == [f'wd_{i}' for i in range(7)]

    # The inherited directories still describe the evaluations that produced them.
    for index, value in inherited.items():
        assert _evaluation_value(campaign / f'wd_{index}') == value
        assert os.path.getmtime(
            campaign / f'wd_{index}' / 'evaluation.txt') == mtimes[index]
    # ...and the new ones are this run's evaluations, in order.
    for offset, value in enumerate(second.counter.calls):
        assert _evaluation_value(campaign / f'wd_{4 + offset}') == value
    assert len(X.data) == 7


# --------------------------------------------------------------------------- #
# 7. --status
# --------------------------------------------------------------------------- #


def test_status_reports_a_half_finished_optimization(campaign, capsys):
    counter = Counter()
    _run(counter, _nelder_mead(4))
    capsys.readouterr()

    state = run_lume_ace3p._report_status(
        {'mode': {'type': 'scalar_optimize'}})
    output = capsys.readouterr().out

    assert state is not None
    assert '4 evaluation(s)' in output
    assert "best 'y'" in output
    # The promise, stated where the numbers are.
    assert 'does not reproduce the trajectory' in output


def test_status_agrees_with_the_final_log(campaign, capsys):
    """On a finished campaign ``--status`` reports the same best objective the run
    logged, because it reads the state the run wrote rather than recomputing."""
    counter = Counter()
    X = _run(counter, _nelder_mead(6))
    logged_best = float(X.data['y'].min())
    logged_at = float(X.data.loc[X.data['y'].idxmin(), 'x'])

    state = read_xopt_state(STATE_FILE)
    name, value, variables = xopt_state.best_point(state)

    assert name == 'y'
    assert value == pytest.approx(logged_best)
    assert variables['x'] == pytest.approx(logged_at)


def test_status_reports_the_real_objective_of_a_multifidelity_run(campaign):
    """``MultiFidelityGenerator`` adds the fidelity axis as an objective named ``s``,
    which sorts before a real objective — so reporting "the first objective" naively
    would report the *fidelity* as the best point. `s` is machinery, not something a
    user asked to optimize."""
    counter = Counter()
    _run(counter, {'generator': 'MultiFidelityGenerator',
                   'fidelity_variable': 'mesh_fidelity', 'num_random': 2,
                   'num_step': 1})
    state = read_xopt_state(STATE_FILE)

    assert xopt_state.objective_names(state) == ['y']
    name, _value, variables = xopt_state.best_point(state)
    assert name == 'y'
    # The fidelity axis is still reported as the variable it is.
    assert 's' in variables


def test_status_ignores_a_tracked_observable(campaign):
    """An observable is tracked, not optimized, so it is not a candidate for "the
    best point" — `omega3p_optimization` tracks `mode_freq` that way."""
    class WithObservable(Counter):
        def evaluate(self, input_dict):
            outputs, ctx = super().evaluate(input_dict)
            return {**outputs, 'tracked': 1.0}, ctx

    modes.scalar_optimize(
        WithObservable(),
        {'variables': VOCS['variables'], 'objectives': {'y': 'MINIMIZE'},
         'observables': ['tracked']},
        _nelder_mead(3), log_file='sim_output.txt')
    state = read_xopt_state(STATE_FILE)

    assert xopt_state.objective_names(state) == ['y']


def test_a_state_with_no_recorded_objective_has_no_best_point():
    """`--status` reports "nothing yet" rather than raising on a state whose data or
    VOCS it cannot rank."""
    assert xopt_state.best_point(None) is None
    assert xopt_state.best_point({}) is None
    assert xopt_state.best_point({'data': {'y': {}}}) is None
    # Every recorded value is NaN — a run whose evaluations all failed.
    nan_state = {'data': {'y': {'0': float('nan')}},
                 'generator': {'name': 'neldermead',
                               'vocs': {'variables': {}, 'objectives': {}}}}
    assert xopt_state.best_point(nan_state) is None


def test_status_on_an_optimization_that_has_not_started(campaign, capsys):
    state = run_lume_ace3p._report_status({'mode': {'type': 'scalar_optimize'}})
    output = capsys.readouterr().out

    assert state is None
    assert 'has not recorded any evaluation yet' in output


def test_status_still_refuses_the_modes_that_have_no_progress(campaign):
    with pytest.raises(ValueError, match='train_surrogate'):
        run_lume_ace3p._report_status({'mode': {'type': 'invert_optimize'}})


def test_status_covers_both_kinds_of_resumable_mode():
    assert run_lume_ace3p.STATUS_MODES == (
        'single', 'parameter_sweep', 'scalar_optimize', 'gp_parameter_sweep')


# --------------------------------------------------------------------------- #
# 8. Always written, never a baseline artifact
# --------------------------------------------------------------------------- #


def test_the_state_file_is_written_without_resume_being_set(campaign):
    """The decision to resume is made *after* the interruption, so the record has to
    already exist — the same reason the run manifest is always written."""
    counter = Counter()
    _run(counter, _nelder_mead(3))

    assert os.path.isfile(STATE_FILE)
    assert xopt_state.evaluation_count(read_xopt_state(STATE_FILE)) == 3


def test_the_state_file_lands_beside_the_output_file(campaign):
    """Derived from the mode's ``output_file`` so ``--status`` finds it from the same
    config with nothing to keep in sync."""
    os.makedirs('runs', exist_ok=True)
    counter = Counter()
    _run(counter, _nelder_mead(2), log_file=os.path.join('runs', 'sim_output.txt'))

    assert os.path.isfile(os.path.join('runs', STATE_FILE))
    assert not os.path.isfile(STATE_FILE)
    assert xopt_state.xopt_state_path('runs/sim_output.txt') == f'runs/{STATE_FILE}'
    assert xopt_state.xopt_state_path('sim_output.txt') == STATE_FILE


def test_the_state_file_is_excluded_from_the_baselines():
    """It carries measured per-evaluation runtimes and the import path of the
    evaluator closure, so it can never be stable run-to-run — and what it computed is
    already compared as ``sim_output.txt``."""
    assert STATE_FILE in bu.BASELINE_EXCLUDED


def test_a_dump_failure_does_not_fail_the_run(campaign, capsys):
    """Persisting state is a service to a *later* run. Failing an expensive campaign
    in progress over it would be the wrong trade."""
    class Undumpable:
        data = pd.DataFrame({'x': [0.5]})

        def dump(self, path):
            raise RuntimeError('disk full')

    assert xopt_state.write_xopt_state(STATE_FILE, Undumpable()) is None
    assert 'could not write the Xopt resume state' in capsys.readouterr().out
    assert not os.path.isfile(STATE_FILE)
