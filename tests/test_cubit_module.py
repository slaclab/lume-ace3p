"""CubitModule over a list of journals plus staged ``files:``
(plans/cubit_multi_file_plan.md, Phase 3)."""

import os
import subprocess

import pytest

from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.modules import MESH, CubitModule, RunContext
from lume_ace3p.workflow_graph import (
    WorkflowValidationError, _validate_disk_collisions,
)

_VARS = '#{r = 1.0}\n#{h = 2.0}\nquit\n'
_GEOM = 'import step "cavity.step" heal\nexport genesis "early.gen"\n'
_MESH = 'mesh volume all\nexport genesis "cavity.gen" overwrite\nquit\n'
_PY = 'cubit.cmd("#{h = 3.0}")\n'
_LIST = ['vars.jou', 'geom.jou', 'tweak.py', 'mesh.jou']


@pytest.fixture
def src(tmp_path, monkeypatch):
    d = tmp_path / 'src'
    d.mkdir()
    for name, text in [('vars.jou', _VARS), ('geom.jou', _GEOM),
                       ('tweak.py', _PY), ('mesh.jou', _MESH),
                       ('cavity.step', 'ISO-10303-21;\n')]:
        (d / name).write_text(text)
    monkeypatch.chdir(d)
    return d


@pytest.fixture
def commands(monkeypatch):
    """Shell lines handed to ``subprocess.run``; a cubit line writes the mesh."""
    seen = []

    def fake_run(cmd, cwd=None, **kwargs):
        seen.append((cmd, sorted(os.listdir(cwd))))
        if 'cubit -batch' in cmd:
            open(os.path.join(cwd, 'cavity.gen'), 'w').close()
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(subprocess, 'run', fake_run)
    return seen


def _module(**config):
    return CubitModule({'journal': _LIST, 'files': ['cavity.step'],
                        'meshconvert': False, **config}, name='cubit')


def test_a_string_journal_keeps_its_hash(tmp_path, monkeypatch):
    """A string ``journal:`` hashes exactly as it did in 0.9.2."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'cavity.jou').write_text(
        '#{r = 1.0}\nexport genesis "cav.gen" overwrite\nquit\n')
    m = CubitModule({'journal': 'cavity.jou', 'meshconvert': False},
                    name='cubit')
    assert m.input_files() == ['cavity.jou']
    assert m.module_hash(WorkflowInputs(cubit={'r': 2.0}), {}) == (
        'sha256:6cf1ff5519c15fa1b70111b3bb501b5d9f37fd6cb668c07dfb1f8c7e1cab3eb6')


@pytest.mark.parametrize('edited', ['vars.jou', 'tweak.py', 'mesh.jou',
                                    'cavity.step'])
def test_the_hash_covers_every_listed_file(src, edited):
    inputs = WorkflowInputs(cubit={'r': 2.0})
    before = _module().module_hash(inputs, {})
    with open(src / edited, 'a') as f:
        f.write('# edited\n')
    assert _module().module_hash(inputs, {}) != before


def test_required_files_lists_journals_and_files(src):
    assert _module().required_files() == _LIST + ['cavity.step']
    one = CubitModule({'journal': 'vars.jou', 'files': 'cavity.step'})
    assert one.required_files() == ['vars.jou', 'cavity.step']


def test_run_stages_files_and_runs_the_list_in_order(src, commands):
    ctx = RunContext(str(src.parent / 'wd'),
                     inputs=WorkflowInputs(cubit={'r': 5.0}))
    _module().run(ctx)
    (cmd, present), = commands
    assert cmd == ('cubit -batch -nographics -nojournal -noecho '
                   'vars.jou geom.jou tweak.py mesh.jou')
    # The STEP file is in the workdir before Cubit starts.
    assert 'cavity.step' in present
    assert ctx.artifacts[MESH] == os.path.join(ctx.workdir, 'cavity.gen')
    with open(os.path.join(ctx.workdir, 'vars.jou')) as f:
        assert f.read().startswith('#{r =5.0}\n')


def test_mesh_name_is_the_last_export_across_the_list(src):
    assert _module()._mesh_name() == 'cavity.gen'
    # The last journal has no export: the one before it names the mesh.
    (src / 'mesh.jou').write_text('mesh volume all\n')
    assert _module()._mesh_name() == 'early.gen'
    # A Python file is never read, even if it is the only one 'exporting'.
    (src / 'tweak.py').write_text('export genesis "py.gen"\n')
    assert _module()._mesh_name() == 'early.gen'


def test_resume_verify_and_clean_use_the_list_export(src, commands):
    wd = src.parent / 'wd'
    ctx = RunContext(str(wd))
    m = _module()
    assert m.verify(ctx) is False
    m.run(ctx)
    assert m.verify(ctx) is True
    resumed = RunContext(str(wd))
    m.run(resumed, skip_execution=True)
    assert resumed.artifacts[MESH] == str(wd / 'cavity.gen')
    m.clean(ctx)
    assert not (wd / 'cavity.gen').exists()
    assert (wd / 'cavity.step').exists()  # staged inputs stay


def test_dry_run_names_the_exported_mesh(src):
    ctx = RunContext(str(src.parent / 'wd'), dry_run=True)
    _module().run(ctx)
    assert ctx.artifacts[MESH] == os.path.join(ctx.workdir, 'cavity.gen')
    marker = open(os.path.join(ctx.workdir, 'DRY_RUN.txt')).read()
    assert "Cubit files: ['cavity.step']" in marker
    assert not os.path.exists(os.path.join(ctx.workdir, 'cavity.step'))


def test_a_missing_staged_file_fails(src, commands):
    m = _module(files=['nope.step'])
    with pytest.raises(FileNotFoundError, match='nope.step'):
        m.run(RunContext(str(src.parent / 'wd')))
    assert commands == []


def test_a_journal_and_a_file_of_one_basename_are_refused(src, commands):
    (src / 'sub').mkdir()
    (src / 'sub' / 'geom.jou').write_text('reset\n')
    m = _module(files=['sub/geom.jou'])
    with pytest.raises(ValueError, match="basename 'geom.jou'"):
        m.run(RunContext(str(src.parent / 'wd')))
    assert commands == []


def test_two_modules_exporting_one_mesh_across_lists_collide(src):
    a = CubitModule({'journal': ['vars.jou', 'mesh.jou']}, name='a')
    b = CubitModule({'journal': 'mesh.jou'}, name='b')
    with pytest.raises(WorkflowValidationError, match="'cavity.gen'"):
        _validate_disk_collisions([a, b])


# ---- Phase 4: validation at build time ---------------------------------- #

from lume_ace3p.workflow_graph import Workflow  # noqa: E402


def _workflow(cubit_inputs, *entries):
    entries = entries or ({'module': 'cubit', 'journal': _LIST,
                           'files': ['cavity.step']},)
    return Workflow.from_config({
        'workflow': list(entries),
        'input_parameters': {'cubit': cubit_inputs},
        'workflow_parameters': {'dry_run': True}})


def test_a_variable_declared_in_any_journal_of_the_list_passes(src):
    _workflow({'r': [1.0, 2.0], 'h': 3.0})


def test_an_undeclared_variable_is_refused(src):
    with pytest.raises(WorkflowValidationError) as err:
        _workflow({'r': 1.0, 'radius': 2.0})
    message = str(err.value)
    assert "'radius'" in message and "'r'" not in message
    assert 'searched vars.jou, geom.jou, mesh.jou' in message


def test_a_multi_assignment_line_reads_as_undeclared(src):
    (src / 'vars.jou').write_text('#{r = 1.0, w = 2.0}\n')
    with pytest.raises(WorkflowValidationError, match='one variable per line'):
        _workflow({'w': 1.0})


def test_a_commented_declaration_does_not_count(src):
    (src / 'vars.jou').write_text('##{w = 1.0}\n# w = 2.0\n#{r = 1.0}\n')
    with pytest.raises(WorkflowValidationError, match="'w'"):
        _workflow({'w': 1.0})


def test_a_shared_variable_needs_only_one_cubit_module(src):
    (src / 'other.jou').write_text('#{w = 1}\nexport genesis "o.gen"\n')
    _workflow({'r': 1.0, 'w': 2.0},
              {'module': 'cubit', 'name': 'a', 'journal': _LIST},
              {'module': 'cubit', 'name': 'b', 'journal': 'other.jou'})


def test_a_scoped_variable_must_be_in_its_own_module(src):
    (src / 'other.jou').write_text('#{w = 1}\nexport genesis "o.gen"\n')
    entries = ({'module': 'cubit', 'name': 'a', 'journal': _LIST},
               {'module': 'cubit', 'name': 'b', 'journal': 'other.jou'})
    _workflow({'b': {'w': 2.0}}, *entries)
    with pytest.raises(WorkflowValidationError, match="cubit: a:.*'w'"):
        _workflow({'a': {'w': 2.0}}, *entries)


def test_a_cubit_input_with_no_cubit_module_is_refused(src):
    with pytest.raises(WorkflowValidationError, match='no cubit module'):
        _workflow({'r': 1.0}, {'module': 'mesh', 'file': 'cavity.step'})


def test_a_legacy_beta_under_cubit_is_allowed(src):
    (src / 'dump.txt').write_text('x InitialNormalField InitialFaceArea\n')
    _workflow({'b1': 50.0},
              {'module': 'track3p_source', 'file': 'dump.txt'},
              {'module': 'field_emission', 'beta_input': 'b1', 'num_bins': 1})


def test_an_unreadable_journal_skips_the_check(src):
    _workflow({'anything': 1.0}, {'module': 'cubit', 'journal': 'gone.jou'})


@pytest.mark.parametrize('files', [['sub/geom.jou'], ['cavity.step',
                                                      'cavity.step']])
def test_a_basename_clash_within_a_module_is_refused_at_build(src, files):
    (src / 'sub').mkdir()
    (src / 'sub' / 'geom.jou').write_text('reset\n')
    with pytest.raises(WorkflowValidationError, match='basename|twice'):
        _workflow({}, {'module': 'cubit', 'journal': _LIST, 'files': files})


def test_modules_may_share_a_source_file_in_one_role(src):
    (src / 'other.jou').write_text('export genesis "o.gen"\n')
    _workflow({}, {'module': 'cubit', 'name': 'a', 'journal': _LIST,
                   'files': 'cavity.step'},
              {'module': 'cubit', 'name': 'b',
               'journal': ['vars.jou', 'other.jou'], 'files': 'cavity.step'})


def test_modules_writing_different_sources_to_one_name_collide(src):
    (src / 'sub').mkdir()
    (src / 'sub' / 'cavity.step').write_text('other\n')
    (src / 'other.jou').write_text('export genesis "o.gen"\n')
    with pytest.raises(WorkflowValidationError,
                       match="'a' and 'b' both write 'cavity.step'.*Rename"):
        _workflow({}, {'module': 'cubit', 'name': 'a', 'journal': _LIST,
                       'files': 'cavity.step'},
                  {'module': 'cubit', 'name': 'b', 'journal': 'other.jou',
                   'files': 'sub/cavity.step'})


def test_a_journal_of_one_module_staged_by_another_collides(src):
    (src / 'other.jou').write_text('export genesis "o.gen"\n')
    with pytest.raises(WorkflowValidationError, match='not both'):
        _workflow({}, {'module': 'cubit', 'name': 'a', 'journal': _LIST},
                  {'module': 'cubit', 'name': 'b', 'journal': 'other.jou',
                   'files': 'vars.jou'})
