"""Unrecognized-configuration-key warnings (item 3 of
``plans/xopt_config_validation_plan.md``).

Nothing used to compare a config's keys against the set the pipeline actually reads,
so a near-miss was silent: ``num_steps`` for ``num_step`` produced a run with no
termination criterion, a ``resume:`` misplaced into a ``train_surrogate`` block did
nothing, ``output_parameter`` (singular) meant a run that extracted nothing. Each is a
config that looks right and a run that quietly is not what was asked for.

The load-bearing test here is :func:`test_every_shipped_example_is_clean`. The
recognized-key sets are hand-written next to the code that reads each block, so the
only thing that proves they were built from the code rather than guessed is running
every shipped config through them and finding nothing. It is also the test that breaks
when a mode learns a key and its set is not extended — which is the point.
"""

import os

import pytest

import baseline_utils as bu
from lume_ace3p import modes, run_lume_ace3p
from lume_ace3p.config import warn_unrecognized
from lume_ace3p.inputs import TOP_LEVEL_KEYS, build_inputs, load_yaml
from lume_ace3p.workflow_graph import WORKFLOW_PARAM_KEYS, Workflow


# --------------------------------------------------------------------------- #
# The check itself
# --------------------------------------------------------------------------- #


def test_it_names_the_key_and_suggests_the_near_miss(capsys):
    """The whole class of bug is a typo, so naming the intended key is the difference
    between a useful warning and one more line of output."""
    unrecognized = warn_unrecognized("'xopt_parameters'",
                                     {'generator': 'x', 'num_steps': 10},
                                     modes.XOPT_KEYS)
    output = capsys.readouterr().out

    assert unrecognized == ['num_steps']
    assert "'num_steps'" in output
    assert "did you mean 'num_step'?" in output
    assert 'Recognized here' in output and 'num_step,' in output


def test_it_is_quiet_when_everything_is_recognized(capsys):
    assert warn_unrecognized('x', {'generator': 'g', 'num_step': 3},
                             modes.XOPT_KEYS) == []
    assert capsys.readouterr().out == ''


def test_it_warns_without_a_suggestion_when_there_is_no_near_miss(capsys):
    warn_unrecognized('x', {'zzzzzzz': 1}, modes.XOPT_KEYS)
    output = capsys.readouterr().out

    assert "'zzzzzzz'" in output
    assert 'did you mean' not in output


def test_a_non_mapping_block_is_not_this_checks_business(capsys):
    """An absent block, or one given the wrong shape entirely — the code that reads it
    complains in terms of what it wanted, which is more useful than a key list."""
    for block in (None, [], 'text', 3):
        assert warn_unrecognized('x', block, modes.XOPT_KEYS) == []
    assert capsys.readouterr().out == ''


def test_it_never_raises():
    """A config with a harmless extra key runs today; failing it would break working
    setups for no safety gain."""
    assert warn_unrecognized('x', {'nonsense': 1}, modes.XOPT_KEYS) == ['nonsense']


# --------------------------------------------------------------------------- #
# Where it is wired in
# --------------------------------------------------------------------------- #


def test_a_mode_key_that_belongs_to_a_different_mode_warns(capsys):
    """Per-mode sets, not a union: `resume` does nothing in a `train_surrogate`
    block, and only a per-mode set can say so."""
    with pytest.raises(Exception):
        # It gets far enough to warn, then fails on the missing store — the warning
        # is what is under test, not the run.
        modes.run_mode({'type': 'train_surrogate', 'resume': True}, None)
    assert "mode 'train_surrogate'" in capsys.readouterr().out


def test_the_same_key_in_the_mode_that_reads_it_is_quiet(capsys):
    modes.run_mode({'type': 'parameter_sweep', 'resume': False},
                   _dry_workflow())
    output = capsys.readouterr().out

    assert 'nothing reads' not in output


def test_an_unrecognized_xopt_parameter_warns(capsys):
    class Counter:
        def evaluate(self, input_dict):
            return {'y': float(input_dict['x'])}, None

    modes.scalar_optimize(
        Counter(), {'variables': {'x': [0.0, 1.0]}, 'objectives': {'y': 'MINIMIZE'}},
        {'generator': 'NelderMeadGenerator', 'num_random': 0, 'num_step': 2,
         'num_steps': 5})
    output = capsys.readouterr().out

    assert "'xopt_parameters'" in output and "'num_steps'" in output


def test_an_unrecognized_vocs_key_warns(capsys):
    modes._make_vocs({'variables': {'x': [0.0, 1.0]},
                      'objectives': {'y': 'MINIMIZE'},
                      'observable': ['z']})
    output = capsys.readouterr().out

    assert "'vocs_parameters'" in output
    assert "did you mean 'observables'?" in output


def test_an_unrecognized_workflow_parameter_warns(capsys):
    _dry_workflow(sweep_output='out.txt')
    output = capsys.readouterr().out

    assert "'workflow_parameters'" in output and "'sweep_output'" in output


def test_a_removed_pre_refactor_key_in_workflow_parameters_warns(capsys):
    """The settings that used to live in this block moved onto the module entries and
    the `mode:` block, so one of them spelled here is exactly the mistake to catch —
    and it is not the legacy *shape* `_is_legacy_format` detects, since that needs
    `module:` or `mode:`."""
    _dry_workflow(ace3p_tasks=16, cubit_input='x.jou')
    output = capsys.readouterr().out

    assert "'ace3p_tasks'" in output and "'cubit_input'" in output


# --------------------------------------------------------------------------- #
# input_parameters: the one silent misroute worth its own warning
# --------------------------------------------------------------------------- #


def test_a_misspelled_input_bucket_warns_that_the_block_is_misread(capsys):
    """`_is_nested_input_parameters` needs *every* key to be a bucket, so one typo
    reinterprets the whole block as the legacy flat cubit block: the bucket names
    become Cubit variables and every real parameter is dropped. Invisible otherwise —
    the run proceeds with no parameters applied."""
    inputs = build_inputs({'input_parameters': {'cubit': {'a': 1.0},
                                                'qubit': {'b': 2.0}}})
    output = capsys.readouterr().out

    assert "'qubit'" in output and 'legacy flat cubit block' in output
    # ...and the warning is telling the truth about what happened.
    assert 'a' not in inputs.cubit
    assert sorted(inputs.cubit) == ['cubit', 'qubit']


def test_the_two_documented_input_parameter_shapes_are_quiet(capsys):
    build_inputs({'input_parameters': {'cubit': {'a': 1.0},
                                       'particles': {'b': 2.0}}})
    assert capsys.readouterr().out == ''
    # The legacy flat block: no bucket names at all, a documented shape.
    build_inputs({'input_parameters': {'cornercut': 14.0, 'rcorner1': 1.0}})
    assert capsys.readouterr().out == ''


def test_user_namespaced_blocks_are_never_inspected(capsys):
    """`input_parameters` variable names and `output_parameters` output names are the
    user's own, so there is no set to check them against."""
    build_inputs({'input_parameters': {'cubit': {'anything_at_all': 1.0}}})
    assert capsys.readouterr().out == ''
    assert 'output_parameters' in TOP_LEVEL_KEYS


# --------------------------------------------------------------------------- #
# Every shipped example is clean — the load-bearing test
# --------------------------------------------------------------------------- #


def _example_configs():
    """Every YAML under examples/, `incomplete/` included."""
    found = []
    for root, _dirs, files in os.walk(bu.EXAMPLES_DIR):
        for name in sorted(files):
            if name.endswith(('.yaml', '.yml')):
                found.append(os.path.join(root, name))
    return sorted(found)


@pytest.mark.parametrize('path', _example_configs(),
                         ids=lambda p: os.path.basename(p))
def test_every_shipped_example_is_clean(path, capsys):
    """No shipped config produces an unrecognized-key warning.

    This is what proves the recognized-key sets were built from the code that reads
    them rather than guessed — and what breaks when a mode learns a key and its set is
    not extended. It checks the config's *keys*, so it needs no ACE3P environment: the
    blocks are read, not run."""
    data = load_yaml(path)
    if run_lume_ace3p._is_legacy_format(data):
        # A pre-refactor config (examples/incomplete/) never reaches these checks:
        # the CLI refuses it first, with a message that explains the whole problem.
        # Skipped by *reason* rather than by directory, so a migrated one is covered
        # again automatically.
        pytest.skip('pre-refactor schema; rejected by the loader before any check')
    warn_unrecognized('top level', data, TOP_LEVEL_KEYS)
    build_inputs(data)

    mode_cfg = data.get('mode') or {}
    mode_type = modes.mode_type_of(mode_cfg)
    if mode_type in modes.MODE_KEYS:
        warn_unrecognized('mode', mode_cfg,
                          modes.MODE_KEYS[mode_type] | modes._COMMON_MODE_KEYS)
    warn_unrecognized('workflow_parameters', data.get('workflow_parameters') or {},
                      WORKFLOW_PARAM_KEYS)
    warn_unrecognized('vocs_parameters', data.get('vocs_parameters') or {},
                      modes.VOCS_KEYS)
    warn_unrecognized('xopt_parameters', data.get('xopt_parameters') or {},
                      modes.XOPT_KEYS)

    assert capsys.readouterr().out == '', f'{os.path.basename(path)} warns'


def test_the_examples_actually_cover_every_mode():
    """The test above is only as good as its coverage: assert the shipped examples
    exercise every mode with a key set, so a wrong set cannot hide behind a mode no
    example uses."""
    covered = {modes.mode_type_of(load_yaml(path).get('mode') or {})
               for path in _example_configs()}

    missing = sorted(set(modes.MODE_KEYS) - covered)
    assert missing == [], f'no shipped example uses {missing}'


# --------------------------------------------------------------------------- #


def _dry_workflow(**params):
    return Workflow(
        [{'module': 'cubit', 'journal': 'x.jou'}],
        workflow_params={'dry_run': True, **params},
        inputs=None)


# --------------------------------------------------------------------------- #
# A misconfigured run must not exit 0 (item 1), and must be told the truth
# about what would terminate it (item 2)
# --------------------------------------------------------------------------- #


class _Objective:
    """The `evaluate` seam, enough to reach the loops."""

    def evaluate(self, input_dict):
        return {'y': (float(input_dict['x']) - 0.3) ** 2}, None


_VOCS = {'variables': {'x': [0.0, 1.0]}, 'objectives': {'y': 'MINIMIZE'}}


def test_a_misspelled_generator_raises_and_lists_the_supported_ones():
    """It used to print "Exiting the program" and return `None`, which the CLI
    ignored — so the process exited 0 having done nothing."""
    with pytest.raises(ValueError, match='not a supported Xopt generator') as info:
        modes.scalar_optimize(_Objective(), _VOCS,
                              {'generator': 'NelderMeedGenerator', 'num_step': 2})
    for name in modes.SUPPORTED_GENERATORS:
        assert name in str(info.value)


def test_mobo_without_a_reference_point_raises():
    with pytest.raises(ValueError, match='reference_point'):
        modes.scalar_optimize(
            _Objective(), _VOCS,
            {'generator': 'ExpectedHypervolumeImprovementGenerator',
             'num_step': 1})


def test_an_unsupported_cost_function_raises():
    with pytest.raises(ValueError, match="cost_function 'linear'"):
        modes.scalar_optimize(
            _Objective(), _VOCS,
            {'generator': 'NelderMeadGenerator', 'num_random': 2,
             'cost_budget': 60, 'cost_function': 'linear'})


@pytest.mark.parametrize('xopt_dict, mentioned', [
    ({'generator': 'NelderMeadGenerator'}, None),
    ({'generator': 'NelderMeadGenerator', 'tolerance': 1e-3}, 'tolerance'),
    ({'generator': 'NelderMeadGenerator', 'max_iterations': 10}, 'max_iterations'),
])
def test_no_criterion_names_the_criteria_that_actually_terminate(xopt_dict,
                                                                mentioned):
    """`tolerance` and `max_iterations` are refinements, not criteria. The old
    message listed `tolerance` *as* a criterion, so a user who had supplied exactly
    that was sent round in a circle; `max_iterations` was not mentioned at all even
    though the docs presented it as one."""
    with pytest.raises(ValueError, match='No termination criterion') as info:
        modes.scalar_optimize(_Objective(), _VOCS, xopt_dict)
    message = str(info.value)

    for criterion in modes._TERMINATION_CRITERIA:
        assert f"'{criterion}'" in message
    if mentioned:
        assert f"'{mentioned}' is set, but it is not a criterion" in message
    else:
        assert 'is set, but it is not a criterion' not in message


def test_max_iterations_alone_is_still_ignored_rather_than_honored():
    """Deliberately unchanged: making it work standalone would silently start
    running configs that today run nothing. The message is what got fixed."""
    assert 'max_iterations' not in modes._TERMINATION_CRITERIA
    assert 'max_iterations' in modes._TERMINATION_REFINEMENTS


def test_a_valid_optimization_still_returns_its_xopt_object():
    X = modes.scalar_optimize(_Objective(), _VOCS,
                              {'generator': 'NelderMeadGenerator',
                               'num_random': 0, 'num_step': 2},
                              log_file=os.devnull)
    assert X is not None and len(X.data) == 2


# --------------------------------------------------------------------------- #
# ...and the CLI turns that into a non-zero exit status
# --------------------------------------------------------------------------- #


_BAD_CONFIG = """workflow_parameters :
    'dry_run' : True
    'workdir_mode' : 'auto'
workflow :
  - module : cubit
    journal : 'x.jou'
mode : {{ type : scalar_optimize }}
input_parameters : {{ cubit : {{ 'x' : 0.5 }} }}
output_parameters : {{ 'y' : {{ module: cubit, quantity: 'nothing' }} }}
vocs_parameters :
    'variables' : {{ 'x' : [0.0, 1.0] }}
    'objectives' : {{ 'y' : 'MINIMIZE' }}
xopt_parameters :
{}
"""


def _cli(tmp_path, monkeypatch, xopt_block):
    (tmp_path / 'x.jou').write_text('## nothing\n')
    (tmp_path / 'c.yaml').write_text(_BAD_CONFIG.format(xopt_block))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['run-lume-ace3p', 'c.yaml'])
    with pytest.raises(SystemExit) as info:
        run_lume_ace3p.main()
    return info.value.code


@pytest.mark.parametrize('xopt_block, expected', [
    ("    'generator' : 'NelderMeedGenerator'\n    'num_step' : 2",
     'not a supported Xopt generator'),
    ("    'generator' : 'NelderMeadGenerator'",
     'No termination criterion'),
])
def test_a_misconfigured_run_exits_non_zero(tmp_path, monkeypatch, capsys,
                                           xopt_block, expected):
    """The load-bearing assertion of item 1: the **exit status**, not the message.

    A batch job that reports success, consumes its allocation and writes no output is
    indistinguishable from one still queued — and `--status` afterwards says "has not
    recorded any evaluation yet" either way."""
    code = _cli(tmp_path, monkeypatch, xopt_block)
    output = capsys.readouterr().out

    assert code == 1
    assert output.startswith('Error:') or '\nError:' in output
    assert expected in output


def test_a_bug_is_not_flattened_into_a_one_line_error(tmp_path, monkeypatch):
    """The handler is for *configuration* errors. A solver crash, a parse failure or a
    bug must still produce a traceback — flattening those would trade the diagnosis
    for tidiness."""
    (tmp_path / 'x.jou').write_text('## nothing\n')
    (tmp_path / 'c.yaml').write_text(_BAD_CONFIG.format(
        "    'generator' : 'NelderMeadGenerator'\n    'num_step' : 2"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['run-lume-ace3p', 'c.yaml'])

    # 'cubit' exposes no extractable quantities, so the objective raises
    # NotImplementedError, which xopt wraps in an XoptError carrying the original
    # traceback. Neither is a ValueError, so it reaches the user as itself.
    from xopt.errors import XoptError
    assert not issubclass(XoptError, ValueError), (
        'XoptError became a ValueError; the handler in main() would now swallow a '
        'failing objective into a one-line Error:')
    with pytest.raises(XoptError, match='exposes no extractable quantities'):
        run_lume_ace3p.main()
