"""A fresh run clears its own output first, and re-stages its inputs (Phase 2
of ``plans/failure_mitigation_plan.md``).

:meth:`Module.clean` removes what one module's tool writes, and
:meth:`Workflow.evaluate` calls it before every run that is neither resumed nor
a dry run — so a tool that crashes, or exits cleanly having written nothing,
cannot leave an earlier run's output to be read as this point's. A staged input
is replaced on a fresh run, so an edited ``files:`` entry is what the tool reads.

The chain tests run real subprocesses through the fake binaries of
``test_resume.py``; the per-module tests call ``clean`` on a hand-built context.
"""

import os

import pytest

from lume_ace3p import modes, state
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.logs import ToolExitError
from lume_ace3p.modules import (
    TD_SOLUTION, TRACK3P_PARTICLES, RunContext,
    _remove_output, _stage_file, build_module,
)

from test_resume import (_invocations, _transwake_workflow, _workflow,
                         posix_only, staged)  # noqa: F401  (fixture)
from test_tool_failures import _CUBIT_CRASH, _recorded, _replace_binary


def _touch(path, text='x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as file:
        file.write(text)


# --------------------------------------------------------------------------- #
# Through the chain
# --------------------------------------------------------------------------- #


@posix_only
def test_a_crashed_cubit_leaves_no_earlier_mesh_behind(staged):
    """The §1.1 stale-mesh probe: a mesh from an earlier run of this point is in
    the workdir and Cubit crashes. The point fails at Cubit and the old mesh is
    gone, so nothing downstream — now or on a later resume — can read it."""
    modes.parameter_sweep(_workflow(staged, [200.0]))
    workdir = str(staged / 'wd') + '_0'
    mesh = os.path.join(workdir, 'cavity.gen')
    assert os.path.isfile(mesh)

    wf = _workflow(staged, [250.0])        # rewrites the fakes, so first
    _replace_binary(staged, 'cubit', _CUBIT_CRASH)
    with pytest.raises(ToolExitError):
        modes.parameter_sweep(wf)
    assert _recorded(workdir)['cubit']['status'] == 'failed'
    assert not os.path.exists(mesh)


@posix_only
def test_a_fresh_run_clears_only_what_its_tools_write(staged):
    """A re-run without resume over a complete workdir removes the results
    directory before the solver writes it again, and leaves the log (one more
    ``$`` header), the manifest and the staged journal copy alone."""
    modes.parameter_sweep(_workflow(staged, [100.0]))
    workdir = str(staged / 'wd') + '_0'
    stale = os.path.join(workdir, 'omega3p_results', 'stale.txt')
    _touch(stale)
    mine = os.path.join(workdir, 'notes.txt')
    _touch(mine)

    df = modes.parameter_sweep(_workflow(staged, [100.0]))

    assert df['f0'].tolist() == [100.0]
    assert not os.path.exists(stale)
    assert os.path.isfile(os.path.join(workdir, 'omega3p_results', 'omega3p.out'))
    assert _invocations(workdir, 'cubit') == 2
    assert _invocations(workdir, 'omega3p') == 2
    assert os.path.isfile(os.path.join(workdir, state.STATE_FILE))
    assert os.path.isfile(os.path.join(workdir, 'cavity.jou'))
    assert os.path.isfile(mine)


@posix_only
def test_a_resumed_module_keeps_its_output(staged):
    modes.parameter_sweep(_workflow(staged, [100.0]))
    workdir = str(staged / 'wd') + '_0'
    sentinel = os.path.join(workdir, 'omega3p_results', 'kept.txt')
    _touch(sentinel)

    modes.parameter_sweep(_workflow(staged, [100.0]), resume=True)

    assert os.path.isfile(sentinel)
    assert _invocations(workdir, 'omega3p') == 1


@posix_only
def test_a_resume_that_re_runs_a_step_clears_only_that_step(staged):
    """The mesh is reused and stays; the solver re-runs and its directory is
    cleared first."""
    modes.parameter_sweep(_workflow(staged, [100.0]))
    workdir = str(staged / 'wd') + '_0'
    os.remove(os.path.join(workdir, 'omega3p_results', 'omega3p.out'))
    stale = os.path.join(workdir, 'omega3p_results', 'stale.txt')
    _touch(stale)
    mesh_mtime = os.path.getmtime(os.path.join(workdir, 'cavity.gen'))

    modes.parameter_sweep(_workflow(staged, [100.0]), resume=True)

    assert os.path.getmtime(os.path.join(workdir, 'cavity.gen')) == mesh_mtime
    assert not os.path.exists(stale)
    assert _invocations(workdir, 'omega3p') == 2


@posix_only
@pytest.mark.parametrize('stage_mode', ['copy', 'symlink', 'hardlink'])
def test_an_edited_files_entry_is_re_staged(staged, stage_mode):
    """An SEY table under ``files:`` edited between two fresh runs: the workdir
    holds the new contents. Before, ``copy`` mode kept the first run's copy."""
    (staged / 'copper.dat').write_text('old\n')
    entries = [
        {'module': 'cubit', 'journal': 'cavity.jou', 'meshconvert': False},
        {'module': 'omega3p', 'input': 'cavity.omega3p',
         'files': ['copper.dat']},
    ]
    modes.parameter_sweep(_workflow(staged, [100.0], entries=entries,
                                    stage_mode=stage_mode))
    staged_copy = os.path.join(str(staged / 'wd') + '_0', 'copper.dat')
    assert open(staged_copy).read() == 'old\n'

    # A new file rather than an in-place write, as an editor or a git checkout
    # replaces one: under hardlink an in-place write would already show.
    os.remove(staged / 'copper.dat')
    (staged / 'copper.dat').write_text('new\n')
    modes.parameter_sweep(_workflow(staged, [100.0], entries=entries,
                                    stage_mode=stage_mode))
    assert open(staged_copy).read() == 'new\n'


@posix_only
def test_a_transwake_re_run_removes_nothing(staged):
    """``postprocess transwake`` rewrites T3P's ``wakefield.out`` — the file it
    reads. Its clean must not remove it, or the re-run has no input."""
    first = modes.parameter_sweep(_transwake_workflow(staged, [100.0]))
    again = modes.parameter_sweep(_transwake_workflow(staged, [100.0]))
    assert set(first['K']) == set(again['K']) == {200.0}
    assert _invocations(str(staged / 'wd') + '_0', 'transwake') == 2


def test_a_dry_run_cleans_nothing(tmp_path):
    """A dry run over a workdir holding real results must leave them."""
    from lume_ace3p.workflow_graph import Workflow
    workdir = tmp_path / 'wd'
    results = workdir / 'omega3p_results' / 'omega3p.out'
    _touch(str(results), 'real')
    (tmp_path / 'cavity.omega3p').write_text('ModelInfo : {\n  File : m.gen\n}\n')
    wf = Workflow(
        [{'module': 'mesh', 'file': str(tmp_path / 'm.gen')},
         {'module': 'omega3p', 'input': str(tmp_path / 'cavity.omega3p')}],
        workflow_params={'workdir': str(workdir), 'workdir_mode': 'manual',
                         'dry_run': True},
        inputs=WorkflowInputs(), output_spec={})
    wf.evaluate()
    assert results.read_text() == 'real'


# --------------------------------------------------------------------------- #
# Per module
# --------------------------------------------------------------------------- #


def test_cubit_clean_removes_the_mesh_and_its_conversion(tmp_path):
    journal = tmp_path / 'cav.jou'
    journal.write_text('export genesis "cav.gen" overwrite\n')
    wd = str(tmp_path / 'wd')
    for name in ('cav.gen', 'cav.ncdf', 'other.ncdf', 'cav.jou'):
        _touch(os.path.join(wd, name))
    ctx = RunContext(wd)

    build_module('cubit', {'journal': str(journal), 'meshconvert': False}).clean(ctx)
    assert sorted(os.listdir(wd)) == ['cav.jou', 'cav.ncdf', 'other.ncdf']

    build_module('cubit', {'journal': str(journal)}).clean(ctx)
    assert sorted(os.listdir(wd)) == ['cav.jou', 'other.ncdf']


def test_cubit_clean_without_an_export_removes_nothing(tmp_path):
    journal = tmp_path / 'cav.jou'
    journal.write_text('create sphere radius 1\n')
    wd = str(tmp_path / 'wd')
    _touch(os.path.join(wd, 'cav.gen'))
    build_module('cubit', {'journal': str(journal)}).clean(RunContext(wd))
    assert os.listdir(wd) == ['cav.gen']


@pytest.mark.parametrize('module_type,config,removed,kept', [
    ('omega3p', {}, 'omega3p_results', None),
    ('omega3p', {'results_dir': 'run_a'}, 'run_a', 'omega3p_results'),
    ('s3p', {}, 's3p_results', None),
    ('track3p', {}, 'track3p_results', None),
    ('t3p', {}, 't3p_results', None),
    # T3P is not passed the override, so it writes its default and the
    # override is only where results are read from.
    ('t3p', {'results_dir': 'elsewhere'}, 't3p_results', 'elsewhere'),
])
def test_solver_clean_removes_the_directory_it_writes(tmp_path, module_type,
                                                      config, removed, kept):
    wd = str(tmp_path / 'wd')
    for name in filter(None, (removed, kept)):
        _touch(os.path.join(wd, name, 'OUTPUT', 'out.txt'))
    _touch(os.path.join(wd, 'solver.log'))
    build_module(module_type, {'input': 'x', **config}).clean(RunContext(wd))
    expected = sorted(filter(None, ['solver.log', kept]))
    assert sorted(os.listdir(wd)) == expected


def test_acdtool_clean_removes_its_output_unless_it_mutates(tmp_path):
    wd = str(tmp_path / 'wd')
    rfpost = os.path.join(wd, 'rfpost.out')
    wake = os.path.join(wd, 't3p_results', 'OUTPUT', 'wakefield.out')
    _touch(rfpost)
    _touch(wake)
    ctx = RunContext(wd)
    ctx.job_names[TD_SOLUTION] = 't3p_results'

    build_module('acdtool', {'command': 'postprocess transwake',
                             'args': [0, 0, 0, 0.01]}).clean(ctx)
    assert os.path.isfile(wake)

    build_module('acdtool', {'input': 'x.rfpost'}).clean(ctx)
    assert not os.path.exists(rfpost)


def test_field_emission_clean_removes_the_weighted_file(tmp_path):
    wd = str(tmp_path / 'wd')
    _touch(os.path.join(wd, 'dump_modified.txt'))
    _touch(os.path.join(wd, 'dump.txt'))
    ctx = RunContext(wd, artifacts={TRACK3P_PARTICLES:
                                    str(tmp_path / 'src' / 'dump.txt')})
    build_module('field_emission', {}).clean(ctx)
    assert os.listdir(wd) == ['dump.txt']

    _touch(os.path.join(wd, 'chosen.txt'))
    build_module('field_emission', {'output': 'chosen.txt'}).clean(ctx)
    assert os.listdir(wd) == ['dump.txt']


def test_field_emission_clean_with_no_dump_yet_removes_nothing(tmp_path):
    wd = str(tmp_path / 'wd')
    _touch(os.path.join(wd, 'dump_modified.txt'))
    build_module('field_emission', {}).clean(RunContext(wd))
    assert os.listdir(wd) == ['dump_modified.txt']


def test_geant4_clean_names_its_outputs_from_the_source_input(tmp_path):
    """The names come from the input file and this point's ``geant4:`` inputs,
    since ``clean`` runs before the wrapper is built."""
    source = tmp_path / 'in.geant4'
    source.write_text('output_prefix = run7\ndetectors = on\n')
    wd = str(tmp_path / 'wd')
    names = ['run7_doseDeposit.txt', 'run7_energyDeposit.txt',
             'run7_detector_dose.csv', 'run7_detector_gamma_spectrum.csv',
             'run9_doseDeposit.txt', 'in.geant4', 'particles.txt']
    for name in names:
        _touch(os.path.join(wd, name))

    build_module('geant4', {'geant4_input': str(source)}).clean(RunContext(wd))
    assert sorted(os.listdir(wd)) == sorted(names[4:])

    inputs = WorkflowInputs(macro={'output_prefix': 'run9'})
    build_module('geant4', {'geant4_input': str(source)}).clean(
        RunContext(wd, inputs=inputs))
    assert sorted(os.listdir(wd)) == sorted(names[5:])


def test_source_modules_clean_nothing(tmp_path):
    wd = str(tmp_path / 'wd')
    _touch(os.path.join(wd, 'mesh.gen'))
    for module_type in ('mesh', 'track3p_source', 'particle_source'):
        build_module(module_type, {'file': 'mesh.gen'}).clean(RunContext(wd))
    assert os.listdir(wd) == ['mesh.gen']


# --------------------------------------------------------------------------- #
# The guard and the staging rule
# --------------------------------------------------------------------------- #


@posix_only
def test_remove_output_never_leaves_the_workdir(tmp_path):
    outside = tmp_path / 'shared'
    _touch(str(outside / 'results' / 'keep.txt'))
    wd = tmp_path / 'wd'
    wd.mkdir()
    os.symlink(str(outside / 'results'), str(wd / 'linked_results'))
    os.symlink(str(outside), str(wd / 'linked_dir'))
    ctx = RunContext(str(wd))

    for name in ('../shared/results', str(outside / 'results'),
                 'linked_dir/results', '.', ''):
        _remove_output(ctx, name)
    assert (outside / 'results' / 'keep.txt').is_file()
    assert wd.is_dir()

    # A link inside the workdir goes as a link; its target stays.
    _remove_output(ctx, 'linked_results')
    assert not os.path.lexists(wd / 'linked_results')
    assert (outside / 'results' / 'keep.txt').is_file()


@pytest.mark.parametrize('mode', ['copy', 'symlink', 'hardlink'])
def test_stage_file_fresh_replaces_a_stale_copy(tmp_path, mode):
    src = tmp_path / 'sey.dat'
    src.write_text('new')
    wd = tmp_path / 'wd'
    wd.mkdir()
    (wd / 'sey.dat').write_text('old')
    ctx = RunContext(str(wd), stage_mode=mode)

    assert open(_stage_file(ctx, str(src))).read() == 'old'   # resumed: kept
    dest = _stage_file(ctx, str(src), fresh=True)
    assert open(dest).read() == 'new'
    assert os.path.islink(dest) == (mode == 'symlink')


@posix_only
def test_stage_file_fresh_keeps_a_link_that_follows_the_source(tmp_path):
    src = tmp_path / 'sey.dat'
    src.write_text('data')
    wd = tmp_path / 'wd'
    wd.mkdir()
    os.symlink(str(src), str(wd / 'sey.dat'))
    inode = os.lstat(wd / 'sey.dat').st_ino
    _stage_file(RunContext(str(wd), stage_mode='symlink'), str(src), fresh=True)
    assert os.lstat(wd / 'sey.dat').st_ino == inode


@pytest.mark.parametrize('module_type,key,old,new', [
    ('acdtool', 'input', 'RoverQ\n{\n  ionoff = 0\n}\n',
     'RoverQ\n{\n  ionoff = 1\n}\n'),
    ('geant4', 'geant4_input', 'output_prefix = old\n', 'output_prefix = new\n'),
])
def test_a_fresh_run_reads_an_edited_input_not_the_old_copy(
        tmp_path, monkeypatch, module_type, key, old, new):
    """Acdtool and Geant4 copy their input into the workdir only when no copy is
    there, then read the copy. A fresh run must start from the edited source,
    and a resumed one keeps the copy its outputs were produced from."""
    from lume_ace3p.acdtool import Acdtool
    from lume_ace3p.geant4 import Geant4
    monkeypatch.setattr(Acdtool, 'run', lambda self: None)
    monkeypatch.setattr(Acdtool, 'parse_output', lambda self, *a, **k: None)
    monkeypatch.setattr(Geant4, 'run', lambda self: None)
    # Relative, as a config names it: Acdtool joins the name onto the workdir,
    # so an absolute one would read the source itself.
    monkeypatch.chdir(tmp_path)
    name = 'in.rfpost' if module_type == 'acdtool' else 'in.geant4'
    source = tmp_path / name
    source.write_text(new)
    wd = tmp_path / 'wd'
    wd.mkdir()
    (wd / name).write_text(old)
    artifacts = ({'em_solution': str(wd)} if module_type == 'acdtool'
                 else {'particle_source': str(wd / 'p.txt')})

    def contents(skip_execution):
        module = build_module(module_type, {key: name})
        module.run(RunContext(str(wd), artifacts=dict(artifacts)),
                   skip_execution=skip_execution)
        if module_type == 'acdtool':
            return module._acdtool.input_data['RoverQ']['ionoff']
        return module.geant4_obj.get_value('output_prefix')

    def value(text):
        return text.split('= ')[1].split()[0]

    assert contents(skip_execution=True) == value(old)
    assert contents(skip_execution=False) == value(new)
