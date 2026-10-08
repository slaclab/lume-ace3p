"""``derived_parameters`` (Phase D of ``plans/multi_instance_workflow_plan.md``).

What is pinned here:

1. **The grammar is closed.** Every allowed node evaluates; every rejected one
   (attribute, subscript, lambda, import, unknown call, walrus, ...) fails at parse
   time naming what it was.
2. **Names are checked at build time**: a misspelling, a forward reference, an
   unquoted non-identifier and a collision with ``output_parameters`` each fail in
   ``Workflow.__init__``, before anything runs.
3. **Arrays work elementwise and reduce** with ``min`` / ``max`` / ``mean``.
4. **The ``python:`` hatch** is called with the outputs only, and its *source
   text* is its hash identity: editing the function changes the hash.
5. **Hash stability (§3.7).** The Phase-0 pinned hashes do not move without the
   block (``test_state.py`` owns the literals); with one, moving a constant moves
   both the point and the campaign hash.
6. **It reaches the modes**: a dry-run ``single`` carries a NaN derived column after
   the extracted ones, and ``scalar_optimize`` minimizes a derived objective over
   the fake-ACE3P harness.
"""

import os
import re

import numpy as np
import pytest

from lume_ace3p import modes
from lume_ace3p.derived import (
    FUNCTIONS, DerivedParameterError, DerivedSpec, parse_expression,
)
from lume_ace3p.inputs import WorkflowInputs, load_yaml
from lume_ace3p.workflow_graph import Workflow

import baseline_utils as bu
from test_resume import _fake_ace3p, posix_only, staged          # noqa: F401
from test_state import _PINNED_POINT_HASHES, _staged_example_workflow
from test_workdirs import _ace3p_workflow


def _value(text, **namespace):
    return parse_expression(text).evaluate(namespace)


# --------------------------------------------------------------------------- #
# 1. The grammar
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('text, expected', [
    ('1 + 2', 3.0), ('7 - 2', 5.0), ('3 * 4', 12.0), ('1 / 4', 0.25),
    ('2 ** 10', 1024.0), ('7 % 3', 1.0), ('-x', -2.0), ('+x', 2.0),
    ('x < 3', 1.0), ('x >= 3', 0.0), ('x == 2', 1.0), ('x != 2', 0.0),
    ('1 < x < 3', 1.0), ('1 < x < 2', 0.0),
    ('x > 1 and x < 3', 1.0), ('x > 5 or x < 0', 0.0), ('not x > 5', 1.0),
    ('abs(-x)', 2.0), ('sqrt(16)', 4.0), ('exp(0)', 1.0), ('log(1)', 0.0),
    ('log10(1000)', 3.0), ('min(x, 1)', 1.0), ('max(x, 5)', 5.0),
    ('sum(x)', 2.0), ('mean(x)', 2.0), ('where(x > 1, 10, 20)', 10.0),
    ('clip(x, 0, 1)', 1.0), ('1e6 * x', 2e6),
])
def test_every_allowed_node_evaluates(text, expected):
    assert _value(text, x=2.0) == pytest.approx(expected)


def test_nan_is_a_name_and_propagates():
    assert np.isnan(_value('nan'))
    assert np.isnan(_value('x + 1', x=np.nan))


@pytest.mark.parametrize('text, what', [
    ('x.real', 'attribute access'),
    ('x[0]', 'subscripts'),
    ('lambda: 1', 'lambda'),
    ('__import__("os")', "'__import__' is not an allowed function"),
    ('open("f")', "'open' is not an allowed function"),
    ('(y := 1)', 'assignment'),
    ('x if x else 1', 'conditional'),
    ('[x]', 'list literals'),
    ('"text"', 'numeric literals'),
    ('True', 'numeric literals'),
    ('x << 1', 'LShift'),
    ('x in x', 'In'),
    ('abs(x=1)', 'positional arguments only'),
    ('abs(1, 2)', 'takes 1 argument'),
    ('__x__', "reserved"),
    ('import os', 'not valid'),
    ('`unclosed', 'unclosed backtick'),
])
def test_every_rejected_node_names_itself(text, what):
    with pytest.raises(DerivedParameterError, match='(?i)' + re.escape(what)):
        parse_expression(text)


def test_the_function_table_is_the_documented_one():
    """A test to update with the docs: the call table is the whole of it."""
    assert sorted(FUNCTIONS) == ['abs', 'clip', 'exp', 'log', 'log10', 'max',
                                 'mean', 'min', 'sqrt', 'sum', 'where']


def test_backticks_quote_a_name_that_is_not_an_identifier():
    expr = parse_expression('`R/Q` / 100 + `S(0,0)`')
    assert expr.names == {'R/Q', 'S(0,0)'}
    assert expr.evaluate({'R/Q': 200.0, 'S(0,0)': 0.5}) == pytest.approx(2.5)


def test_free_names_exclude_functions_and_constants():
    assert parse_expression('abs(a - b) + nan').names == {'a', 'b'}


# --------------------------------------------------------------------------- #
# 2. Names checked at build time
# --------------------------------------------------------------------------- #


def test_a_constant_only_block():
    spec = DerivedSpec.from_config({'f_target': 1.3e9, 'n': 3}, [])
    assert spec.compute({}) == {'f_target': 1.3e9, 'n': 3.0}


def test_entries_see_outputs_and_earlier_entries_in_order():
    spec = DerivedSpec.from_config(
        {'f_target': 1.3e9, 'f_error': 'abs(f - f_target)',
         'f_ppm': '1e6 * f_error / f_target'}, ['f'])
    got = spec.compute({'f': 1.3013e9})
    assert list(got) == ['f_target', 'f_error', 'f_ppm']
    assert got['f_ppm'] == pytest.approx(1000.0)


def test_a_forward_reference_is_an_error():
    with pytest.raises(DerivedParameterError, match=r"reads \['b'\]"):
        DerivedSpec.from_config({'a': 'b + 1', 'b': 2.0}, [])


def test_a_misspelled_output_is_an_error_listing_the_known_names():
    with pytest.raises(DerivedParameterError, match='mode_freq'):
        DerivedSpec.from_config({'e': 'abs(mode_frq - 1)'}, ['mode_freq'])


def test_an_unquoted_non_identifier_name_points_at_backticks():
    with pytest.raises(DerivedParameterError, match='backticks'):
        DerivedSpec.from_config({'r': 'R/Q / 100'}, ['R/Q'])


def test_a_collision_with_an_output_name_is_an_error():
    with pytest.raises(DerivedParameterError, match='also an output_parameters'):
        DerivedSpec.from_config({'f': 1.0}, ['f'])


@pytest.mark.parametrize('value', [True, None, [1, 2], {'python': 'm:f',
                                                         'extra': 1}])
def test_a_malformed_entry_is_an_error(value):
    with pytest.raises(DerivedParameterError, match="'x'"):
        DerivedSpec.from_config({'x': value}, [])


def test_the_workflow_rejects_a_bad_block_before_running_anything(tmp_path):
    """``from_config`` is where a typo surfaces — not at the end of point 0."""
    data = {'workflow': [{'module': 'mesh', 'file': 'x.ncdf'},
                         {'module': 'omega3p', 'input': 'x.omega3p'}],
            'workflow_parameters': {'workdir': str(tmp_path), 'dry_run': True},
            'output_parameters': {'f': {'module': 'omega3p',
                                        'quantity': 'Frequency'}},
            'derived_parameters': {'e': 'abs(g - 1)'}}
    with pytest.raises(DerivedParameterError, match=r"reads \['g'\]"):
        Workflow.from_config(data)
    assert not os.listdir(tmp_path)


def test_derived_parameters_is_a_recognized_top_level_block():
    from lume_ace3p.inputs import TOP_LEVEL_KEYS
    assert 'derived_parameters' in TOP_LEVEL_KEYS


# --------------------------------------------------------------------------- #
# 3. Arrays
# --------------------------------------------------------------------------- #


def test_arrays_work_elementwise_and_reduce():
    spec = DerivedSpec.from_config(
        {'w2': 'w * 2', 'peak': 'max(w)', 'floor': 'min(w)', 'avg': 'mean(w)',
         'gap': 'abs(w - 2)', 'pair': 'max(w, 2)'}, ['w'])
    got = spec.compute({'w': np.array([1.0, 2.0, 3.0])})
    np.testing.assert_allclose(got['w2'], [2.0, 4.0, 6.0])
    assert (got['peak'], got['floor'], got['avg']) == (3.0, 1.0, 2.0)
    np.testing.assert_allclose(got['gap'], [1.0, 0.0, 1.0])
    np.testing.assert_allclose(got['pair'], [2.0, 2.0, 3.0])


def test_a_list_output_counts_as_an_array():
    """A recorded output comes back from JSON as a list."""
    assert _value('sum(w)', w=[1.0, 2.0]) == 3.0


def test_a_non_numeric_output_is_a_clear_error():
    spec = DerivedSpec.from_config({'x': 'kind + 1'}, ['kind'])
    with pytest.raises(DerivedParameterError, match="'kind'"):
        spec.compute({'kind': 'Longitudinal'})


# --------------------------------------------------------------------------- #
# 4. The python: hatch
# --------------------------------------------------------------------------- #


_HELPER = '''
def balance(outputs):
    return outputs['a'] - outputs['b']
'''


def test_the_python_hatch_sees_the_outputs_and_earlier_entries(tmp_path):
    (tmp_path / 'helper_ok.py').write_text(_HELPER.replace("'b'", "'k'"))
    spec = DerivedSpec.from_config(
        {'k': 1.0, 'bal': {'python': 'helper_ok:balance'}}, ['a'],
        base_dir=str(tmp_path))
    assert spec.compute({'a': 3.0}) == {'k': 1.0, 'bal': 2.0}


def test_the_python_hatch_is_not_imported_at_build_time(tmp_path):
    """``--status`` builds the workflow and must never execute user code."""
    (tmp_path / 'helper_side.py').write_text(
        "raise RuntimeError('imported')\n" + _HELPER)
    spec = DerivedSpec.from_config({'bal': {'python': 'helper_side:balance'}},
                                   ['a', 'b'], base_dir=str(tmp_path))
    with pytest.raises(RuntimeError, match='imported'):
        spec.compute({'a': 1.0, 'b': 0.0})


def test_the_python_hatch_hashes_by_source_text(tmp_path):
    path = tmp_path / 'helper_src.py'
    path.write_text(_HELPER)
    block = {'bal': {'python': 'helper_src:balance'}}
    first = DerivedSpec.from_config(block, ['a', 'b'], base_dir=str(tmp_path))
    assert 'outputs' in first.canonical()[0][2]['source']

    path.write_text(_HELPER + '\n# a comment elsewhere in the file\n')
    same = DerivedSpec.from_config(block, ['a', 'b'], base_dir=str(tmp_path))
    assert same.canonical() == first.canonical()

    path.write_text(_HELPER.replace("- outputs['b']", "+ outputs['b']"))
    edited = DerivedSpec.from_config(block, ['a', 'b'], base_dir=str(tmp_path))
    assert edited.canonical() != first.canonical()
    assert edited.compute({'a': 3.0, 'b': 1.0})['bal'] == 4.0


@pytest.mark.parametrize('target, match', [
    ('no_colon', "must be 'module:callable'"),
    ('no_such_module_xyz:f', "no module 'no_such_module_xyz'"),
    ('helper_missing:nope', "defines no 'nope'"),
])
def test_a_bad_python_target_is_a_build_time_error(tmp_path, target, match):
    (tmp_path / 'helper_missing.py').write_text(_HELPER)
    with pytest.raises(DerivedParameterError, match=match):
        DerivedSpec.from_config({'x': {'python': target}}, [],
                                base_dir=str(tmp_path))


# --------------------------------------------------------------------------- #
# 5. Hash stability
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('example', sorted(_PINNED_POINT_HASHES))
def test_a_derived_block_moves_the_pinned_hash_and_a_constant_moves_it_again(
        example, monkeypatch):
    """No block: the literal pin (test_state.py). An *empty* block: still the
    pin. A block: a different hash, and moving its constant another."""
    from lume_ace3p.modes import _input_tensor

    data, wf = _staged_example_workflow(example, monkeypatch)
    point = _input_tensor(wf.sweep_axes())[0].tolist()
    assert wf.point_config_hash(point) == _PINNED_POINT_HASHES[example]

    def hashed(block):
        return Workflow.from_config(
            {**data, 'derived_parameters': block}).point_config_hash(point)

    assert hashed({}) == _PINNED_POINT_HASHES[example]
    first = hashed({'target': 1.0})
    assert first != _PINNED_POINT_HASHES[example]
    assert hashed({'target': 2.0}) not in (first,
                                           _PINNED_POINT_HASHES[example])


def test_the_campaign_hash_covers_the_block(monkeypatch):
    data, wf = _staged_example_workflow('s3p_optimization', monkeypatch)
    variables = list(data['vocs_parameters']['variables'])
    pinned = wf.campaign_config_hash(variables)

    def hashed(block):
        return Workflow.from_config(
            {**data, 'derived_parameters': block}).campaign_config_hash(variables)

    assert hashed({'k': 1.0}) not in (pinned, hashed({'k': 2.0}))


# --------------------------------------------------------------------------- #
# 6. Through the modes
# --------------------------------------------------------------------------- #


def test_a_dry_run_single_carries_a_nan_derived_column(tmp_path, monkeypatch):
    """Derived columns follow the extracted ones, in declaration order, and a
    dry run's NaN propagates through the arithmetic."""
    monkeypatch.chdir(bu._stage_example('omega3p_sweep'))
    data = load_yaml('omega3p_sweep.yaml')
    wf = Workflow(
        data['workflow'],
        workflow_params={'workdir': str(tmp_path / 'wd'),
                         'workdir_mode': 'manual', 'dry_run': True},
        inputs=WorkflowInputs(cubit={'cav_radius': 95.0, 'ellipticity': 0.6}),
        output_spec=data['output_parameters'],
        derived_spec={'f_target': 1.3e9, 'RoQ_norm': '`R/Q` / 100'})
    df = modes.single(wf)

    extracted = list(data['output_parameters'])
    assert list(df.columns) == (['cav_radius', 'ellipticity'] + extracted
                                + ['f_target', 'RoQ_norm'])
    assert df.loc[0, 'f_target'] == 1.3e9
    assert np.isnan(df.loc[0, 'RoQ_norm'])


def test_the_manifest_records_the_derived_outputs(tmp_path):
    from lume_ace3p import state

    (tmp_path / 'x.ncdf').write_text('')
    wf = Workflow([{'module': 'mesh', 'file': str(tmp_path / 'x.ncdf')},
                   {'module': 'omega3p', 'input': 'x.omega3p'}],
                  workflow_params={'workdir': str(tmp_path / 'wd'),
                                   'dry_run': True},
                  output_spec={'f': {'module': 'omega3p',
                                     'quantity': 'Frequency', 'at': {'mode': 0}}},
                  derived_spec={'k': 2.0, 'f2': 'f * k'})
    outputs, ctx = wf.evaluate()
    assert list(outputs) == ['f', 'k', 'f2']
    recorded = state.read_state(ctx.workdir)['outputs']
    assert recorded['k'] == 2.0 and np.isnan(recorded['f2'])


@posix_only
def test_scalar_optimize_minimizes_a_derived_objective(staged):
    """The frequency-targeting study §3.6 exists for: the objective is a derived
    name that is not in ``output_parameters`` at all. The fake Omega3P reports
    the ``FrequencyScan.Start`` leaf it was given as the mode frequency, so the
    distance from 2.6 GHz is a well-posed minimization over that leaf."""
    workflow = _ace3p_workflow(staged)
    workflow = Workflow(workflow.entries, workflow_params=workflow.workflow_params,
                        inputs=workflow.inputs, output_spec=workflow.output_spec,
                        derived_spec={'f_target': 2.6e9,
                                      'f_error': 'abs(f0 - f_target) / 1e9'})
    vocs = {'variables': {'ace3p:FrequencyScan.Start': [1.0e9, 3.0e9]},
            'objectives': {'f_error': 'MINIMIZE'}}
    X = modes.scalar_optimize(
        workflow, vocs,
        {'generator': 'NelderMeadGenerator', 'num_random': 0, 'num_step': 12},
        log_file='sim_output.txt')

    data = X.data
    np.testing.assert_allclose(
        data['f_error'],
        np.abs(data['ace3p:FrequencyScan.Start'] - 2.6e9) / 1e9, rtol=1e-6)
    assert data['f_error'].min() < data['f_error'].iloc[0]
