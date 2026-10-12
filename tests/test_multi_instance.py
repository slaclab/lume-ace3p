"""Per-instance artifact identity and ``from:`` (Phase 2 of
``plans/multi_instance_workflow_plan.md``).

Two instances of one module type coexist in a chain: artifacts are keyed by
``(kind, producer name)`` (:class:`~lume_ace3p.modules.ByProducer`), a consumer
binds to its producer with ``from:`` or takes the only one, and
``output_parameters`` ``module:`` names an instance before a type.

The truth table of which chains validate lives in ``test_workflow_graph.py``
(``test_which_chains_validate``); this file covers what the binding does once a
chain validates. The two-instance resume and the per-instance input file run
real subprocesses through the fake binaries of ``test_resume.py``, since a dry
run writes no input file and skips nothing.
"""

import json
import os

import numpy as np
import pytest

from lume_ace3p import modes
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.modules import (
    AmbiguousArtifactError, ByProducer, EM_SOLUTION, MESH, RunContext,
    TRACK3P_PARTICLES, build_module,
)
from lume_ace3p.workflow_graph import Workflow, WorkflowValidationError

from test_resume import (
    _JOURNAL_NAME, _fake_ace3p, _invocations, _stage, posix_only,
)


_CUBIT = {'module': 'cubit', 'journal': 'x.jou'}


def _dry(entries, **kwargs):
    return Workflow(entries, workflow_params={'dry_run': True}, **kwargs)


# --------------------------------------------------------------------------- #
# ByProducer: the dict-compatible contract the hand-built contexts rely on
# --------------------------------------------------------------------------- #


def test_by_producer_reads_a_kind_with_one_producer_like_a_dict():
    table = ByProducer({MESH: 'm.gen'})
    assert table[MESH] == 'm.gen'
    assert MESH in table and EM_SOLUTION not in table
    assert table.get(EM_SOLUTION) is None
    assert table.get(EM_SOLUTION, 'x') == 'x'
    assert list(table) == [MESH] and set(table) == {MESH}
    with pytest.raises(KeyError):
        table[EM_SOLUTION]


def test_by_producer_refuses_a_kind_with_several_producers():
    """The ambiguity is not a ``KeyError``, so ``.get`` reports it instead of
    quietly returning the default."""
    table = ByProducer()
    table.record(EM_SOLUTION, 'coarse', 'a')
    table.record(EM_SOLUTION, 'fine', 'b')
    assert EM_SOLUTION in table and len(table) == 1
    assert table.of(EM_SOLUTION, 'fine') == 'b'
    with pytest.raises(AmbiguousArtifactError, match="'coarse', 'fine'"):
        table[EM_SOLUTION]
    with pytest.raises(AmbiguousArtifactError):
        table.get(EM_SOLUTION)


def test_by_producer_lookup_falls_back_to_the_anonymous_entry():
    """A bound consumer on a hand-built context: the test wrote
    ``ctx.job_names[kind] = ...`` without naming a producer."""
    table = ByProducer()
    table[EM_SOLUTION] = 'anon'
    assert table.lookup(EM_SOLUTION, 'fine') == 'anon'
    table.record(EM_SOLUTION, 'fine', 'named')
    assert table.lookup(EM_SOLUTION, 'fine') == 'named'
    assert table.lookup(TRACK3P_PARTICLES, 'fine', None) is None
    with pytest.raises(KeyError):
        table.lookup(TRACK3P_PARTICLES, 'fine')


def test_run_context_records_under_the_module_name(tmp_path):
    ctx = RunContext(str(tmp_path))
    coarse = build_module('omega3p', {'input': 'x'}, name='coarse')
    ctx.provide(coarse, EM_SOLUTION, 'wd')
    ctx.set_job_name(coarse, EM_SOLUTION, 'coarse_results')
    assert ctx.artifacts.of(EM_SOLUTION, 'coarse') == 'wd'
    assert ctx.job_names[EM_SOLUTION] == 'coarse_results'


# --------------------------------------------------------------------------- #
# Binding resolution
# --------------------------------------------------------------------------- #


def _two_omega3p(**track3p):
    return [_CUBIT,
            {'module': 'omega3p', 'name': 'coarse', 'input': 'x.omega3p',
             'results_dir': 'coarse_results'},
            {'module': 'omega3p', 'name': 'fine', 'input': 'x.omega3p',
             'results_dir': 'fine_results'},
            {'module': 'track3p', 'input': 'x.track3p', **track3p}]


def test_from_shorthand_binds_the_one_requirement():
    wf = _dry(_two_omega3p(**{'from': 'fine'}))
    track3p = wf.modules[-1]
    assert track3p.sources == {EM_SOLUTION: 'fine'}
    # The implicit bindings are recorded too.
    assert [m.sources for m in wf.modules[1:3]] == [{MESH: 'cubit'}] * 2


def test_from_full_form_binds_by_kind():
    wf = _dry(_two_omega3p(**{'from': {EM_SOLUTION: 'coarse'}}))
    assert wf.modules[-1].sources == {EM_SOLUTION: 'coarse'}


def test_from_naming_no_module_is_an_error():
    with pytest.raises(WorkflowValidationError,
                       match="no module is named 'medium'.*"
                             r"\['coarse', 'fine'\]"):
        _dry(_two_omega3p(**{'from': 'medium'}))


def test_from_naming_a_module_that_does_not_provide_the_kind():
    with pytest.raises(WorkflowValidationError,
                       match="'cubit' is a 'cubit' module, which provides "
                             r"\['mesh'\]"):
        _dry(_two_omega3p(**{'from': 'cubit'}))


def test_from_mapping_naming_a_kind_the_module_does_not_require():
    with pytest.raises(WorkflowValidationError,
                       match=r"binds \['mesh'\] with 'from:'"):
        _dry(_two_omega3p(**{'from': {MESH: 'cubit'}}))


def test_from_shorthand_on_a_module_with_no_requirement_is_an_error():
    """The shorthand names *the* requirement, so it is refused where there is
    not exactly one — here a source module, which requires nothing."""
    with pytest.raises(WorkflowValidationError,
                       match="requires nothing, so a single name does not say"):
        _dry([{'module': 'mesh', 'file': 'm.ncdf', 'from': 'cubit'},
              {'module': 'omega3p', 'input': 'x.omega3p'}])


def test_from_shorthand_on_a_module_with_two_requirements_is_an_error():
    """No shipped module type requires two kinds, so one is made for the test:
    the rule is about the shape of ``from:``, not about a particular module."""
    from lume_ace3p.workflow_graph import _binding_map
    module = build_module('omega3p', {'input': 'x'})
    module.requires = frozenset({MESH, TRACK3P_PARTICLES})
    module.binding = 'cubit'
    with pytest.raises(WorkflowValidationError,
                       match="requires mesh, track3p_particles.*mapping form"):
        _binding_map(module)


def test_a_from_binding_changes_the_config_hash():
    """``from:`` is an entry key, so it lands in the hash with the rest of the
    entry: a different binding is a different campaign (§3.7)."""
    a = _dry(_two_omega3p(**{'from': 'coarse'}))
    b = _dry(_two_omega3p(**{'from': 'fine'}))
    assert a.point_config_hash() != b.point_config_hash()


# --------------------------------------------------------------------------- #
# Disk collisions
# --------------------------------------------------------------------------- #


def test_two_instances_writing_one_results_directory_is_an_error():
    entries = [_CUBIT,
               {'module': 'omega3p', 'name': 'coarse', 'input': 'x.omega3p'},
               {'module': 'omega3p', 'name': 'fine', 'input': 'x.omega3p'}]
    with pytest.raises(WorkflowValidationError,
                       match="'coarse' and 'fine'.*'omega3p_results'.*"
                             "results_dir:"):
        _dry(entries)


def test_two_t3p_instances_need_a_jobname_not_a_results_dir():
    """T3P is not passed ``results_dir:`` on its command line, so the key cannot
    keep two instances apart, and the message says what can."""
    entries = [_CUBIT,
               {'module': 't3p', 'name': 'a', 'input': 'x.t3p',
                'results_dir': 'a_results'},
               {'module': 't3p', 'name': 'b', 'input': 'x.t3p',
                'results_dir': 'b_results'}]
    with pytest.raises(WorkflowValidationError, match="JobName leaf"):
        _dry(entries)


def test_two_cubit_journals_exporting_one_mesh_is_an_error(tmp_path):
    for name in ('a.jou', 'b.jou'):
        (tmp_path / name).write_text('export genesis "cavity.gen" overwrite\n')
    entries = [{'module': 'cubit', 'name': 'a', 'journal': str(tmp_path / 'a.jou')},
               {'module': 'cubit', 'name': 'b', 'journal': str(tmp_path / 'b.jou')}]
    with pytest.raises(WorkflowValidationError,
                       match="'a' and 'b' both export 'cavity.gen'"):
        _dry(entries)
    (tmp_path / 'b.jou').write_text('export genesis "other.gen" overwrite\n')
    _dry(entries)


# --------------------------------------------------------------------------- #
# Output routing by name
# --------------------------------------------------------------------------- #


def _p_refinement():
    return [_CUBIT,
            {'module': 'omega3p', 'name': 'coarse', 'input': 'x.omega3p',
             'results_dir': 'coarse_results'},
            {'module': 'omega3p', 'name': 'fine', 'input': 'x.omega3p',
             'results_dir': 'fine_results'}]


def test_output_module_names_an_instance():
    wf = _dry(_p_refinement(), output_spec={
        'f_coarse': {'module': 'coarse', 'quantity': 'Frequency'},
        'f_fine': {'module': 'fine', 'quantity': 'Frequency'},
    })
    assert {k: m.name for k, m in wf.output_modules().items()} == {
        'f_coarse': 'coarse', 'f_fine': 'fine'}


def test_output_module_type_matching_several_instances_is_an_error():
    wf = _dry(_p_refinement(), output_spec={
        'f': {'module': 'omega3p', 'quantity': 'Frequency'}})
    with pytest.raises(WorkflowValidationError,
                       match=r"2 modules have \(\['coarse', 'fine'\]\).*"
                             "'module: <name>'"):
        wf.output_modules()


def test_output_module_type_still_works_with_one_instance():
    wf = _dry([_CUBIT, {'module': 'omega3p', 'name': 'eig', 'input': 'x'}],
              output_spec={'f': {'module': 'omega3p', 'quantity': 'Frequency'}})
    assert wf.output_modules()['f'].name == 'eig'


def test_a_dry_run_records_each_instance_s_own_job_name(tmp_path):
    wf = Workflow(_p_refinement(),
                  workflow_params={'dry_run': True,
                                   'workdir': str(tmp_path / 'wd')},
                  output_spec={'f_fine': {'module': 'fine',
                                          'quantity': 'Frequency'}})
    outputs, ctx = wf.evaluate()
    assert np.isnan(outputs['f_fine'])
    assert ctx.job_names.of(EM_SOLUTION, 'coarse') == 'coarse_results'
    assert ctx.job_names.of(EM_SOLUTION, 'fine') == 'fine_results'
    with open(tmp_path / 'wd' / 'lume_ace3p_state.json') as file:
        manifest = json.load(file)
    by_name = {m['name']: m for m in manifest['modules']}
    assert by_name['coarse']['job_name'] == 'coarse_results'
    assert by_name['fine']['job_name'] == 'fine_results'


# --------------------------------------------------------------------------- #
# Consumers read the bound producer
# --------------------------------------------------------------------------- #


def test_track3p_field_dir_comes_from_the_bound_producer(tmp_path):
    """The dry-run marker names the FieldDir track3p would inject: the bound
    instance's results directory, not the first em_solution producer's."""
    for binding, expected in (('coarse', 'coarse_results'),
                              ('fine', 'fine_results')):
        workdir = tmp_path / binding
        wf = Workflow(_two_omega3p(**{'from': binding}),
                      workflow_params={'dry_run': True,
                                       'workdir': str(workdir)})
        wf.evaluate()
        marker = (workdir / 'DRY_RUN.txt').read_text()
        assert f'Track3P FieldDir: ./{expected}\n' in marker


def test_acdtool_jobname_comes_from_the_bound_producer(tmp_path):
    """Two T3P instances (kept apart by a JobName leaf, since t3p's
    ``results_dir:`` only says where to read) and a transwake bound to the
    second: its injected jobname is that instance's directory."""
    for name in ('a', 'b'):
        (tmp_path / f'{name}.t3p').write_text(f'JobName : {name}_results\n')
    entries = [_CUBIT,
               {'module': 't3p', 'name': 'a', 'input': str(tmp_path / 'a.t3p'),
                'results_dir': 'a_results'},
               {'module': 't3p', 'name': 'b', 'input': str(tmp_path / 'b.t3p'),
                'results_dir': 'b_results'},
               {'module': 'acdtool', 'command': 'postprocess transwake',
                'args': [0.0, 0.0, 0.0, 0.0125], 'from': 'b'}]
    wf = Workflow(entries, workflow_params={'dry_run': True,
                                            'workdir': str(tmp_path / 'wd')})
    wf.evaluate()
    marker = (tmp_path / 'wd' / 'DRY_RUN.txt').read_text()
    assert 'Acdtool jobname: b_results\n' in marker


# --------------------------------------------------------------------------- #
# Real subprocesses: per-instance input file, two-instance resume
# --------------------------------------------------------------------------- #


@pytest.fixture
def staged(tmp_path, monkeypatch):
    root = _stage(tmp_path)
    monkeypatch.chdir(root)
    return root


_SCOPED_ORDER = """
input_parameters :
  cubit :
    'radius' : 100.0
  ace3p :
    coarse :
      'FiniteElement' : { 'Order' : 1 }
    fine :
      'FiniteElement' : { 'Order' : 2 }
"""


def _p_refinement_workflow(root, scoped=False, **params):
    inputs = WorkflowInputs(cubit={'radius': 100.0})
    if scoped:
        # Through the real loader: the ace3p block is read as duplicate-key
        # pairs, not as a plain mapping.
        from lume_ace3p.inputs import build_inputs, load_yaml
        path = root / 'scoped.yaml'
        path.write_text(_SCOPED_ORDER)
        inputs = build_inputs(load_yaml(str(path)),
                              module_names=['cubit', 'coarse', 'fine'])
    return Workflow(
        [{'module': 'cubit', 'journal': _JOURNAL_NAME, 'meshconvert': False},
         {'module': 'omega3p', 'name': 'coarse', 'input': 'cavity.omega3p',
          'results_dir': 'coarse_results'},
         {'module': 'omega3p', 'name': 'fine', 'input': 'cavity.omega3p',
          'results_dir': 'fine_results'}],
        workflow_params={'workdir': str(root / 'wd'), 'dry_run': False,
                         'paths': _fake_ace3p(root), **params},
        inputs=inputs,
        output_spec={
            'f_coarse': {'module': 'coarse', 'quantity': 'Frequency',
                         'at': {'mode': 0}},
            'f_fine': {'module': 'fine', 'quantity': 'Frequency',
                       'at': {'mode': 0}}})


@posix_only
def test_two_instances_on_one_input_file_write_separate_copies(staged):
    """The p-refinement case: both read ``cavity.omega3p``, each with its own
    scoped override, and each writes its merged copy as
    ``<stem>_<name><ext>`` so neither overwrites the other."""
    wf = _p_refinement_workflow(staged, scoped=True)
    outputs, _ctx = wf.evaluate()
    assert outputs == {'f_coarse': 100.0, 'f_fine': 100.0}
    wd = staged / 'wd'
    coarse = (wd / 'cavity_coarse.omega3p').read_text()
    fine = (wd / 'cavity_fine.omega3p').read_text()
    assert 'Order : 1' in coarse and 'Order : 2' not in coarse
    assert 'Order : 2' in fine and 'Order : 1' not in fine
    for name in ('coarse', 'fine'):
        with open(wd / f'{name}.log') as file:
            assert f'cavity_{name}.omega3p {name}_results' in file.read()


@posix_only
def test_one_instance_keeps_the_input_basename(staged):
    """With no other solver sharing the basename, the written name is the one
    it has always been."""
    wf = Workflow(
        [{'module': 'cubit', 'journal': _JOURNAL_NAME, 'meshconvert': False},
         {'module': 'omega3p', 'input': 'cavity.omega3p'}],
        workflow_params={'workdir': str(staged / 'wd'), 'dry_run': False,
                         'paths': _fake_ace3p(staged)},
        inputs=WorkflowInputs(cubit={'radius': 100.0}),
        output_spec={})
    wf.evaluate()
    assert not any('_omega3p' in name for name in os.listdir(staged / 'wd'))


@posix_only
def test_a_resume_restarts_at_the_second_instance(staged):
    """The manifest is keyed by name, so a chain whose second omega3p failed
    resumes there: the mesh and the first eigensolve are not repeated."""
    wf = _p_refinement_workflow(staged)
    wf.evaluate()
    wd = str(staged / 'wd')
    state_path = os.path.join(wd, 'lume_ace3p_state.json')
    with open(state_path) as file:
        manifest = json.load(file)
    # Mark 'fine' failed, as a wall-clock kill inside it would leave it.
    for entry in manifest['modules']:
        if entry['name'] == 'fine':
            entry['status'] = 'failed'
    with open(state_path, 'w') as file:
        json.dump(manifest, file)

    outputs, _ctx = _p_refinement_workflow(staged).evaluate(resume=True)
    assert outputs == {'f_coarse': 100.0, 'f_fine': 100.0}
    assert _invocations(wd, 'cubit') == 1
    assert _invocations(wd, 'coarse') == 1
    assert _invocations(wd, 'fine') == 2
    with open(state_path) as file:
        by_name = {m['name']: m for m in json.load(file)['modules']}
    assert by_name['coarse'].get('resumed') is True
    assert 'resumed' not in by_name['fine']
    assert by_name['fine']['job_name'] == 'fine_results'


@posix_only
def test_a_p_refinement_sweep_tabulates_both_instances(staged):
    """Both outputs narrowed to scalars, so the table is wide: one row per
    point, one column per instance."""
    wf = Workflow(
        [{'module': 'cubit', 'journal': _JOURNAL_NAME, 'meshconvert': False},
         {'module': 'omega3p', 'name': 'coarse', 'input': 'cavity.omega3p',
          'results_dir': 'coarse_results'},
         {'module': 'omega3p', 'name': 'fine', 'input': 'cavity.omega3p',
          'results_dir': 'fine_results'}],
        workflow_params={'workdir': str(staged / 'wd'),
                         'workdir_mode': 'indexed', 'dry_run': False,
                         'paths': _fake_ace3p(staged)},
        inputs=WorkflowInputs(cubit={'radius': np.array([100.0, 101.0])}),
        output_spec={
            'f_coarse': {'module': 'coarse', 'quantity': 'Frequency',
                         'at': {'mode': 0}},
            'f_fine': {'module': 'fine', 'quantity': 'Frequency',
                       'at': {'mode': 0}}})
    df = modes.parameter_sweep(wf)
    assert df['f_coarse'].tolist() == [100.0, 101.0]
    assert df['f_fine'].tolist() == [100.0, 101.0]


