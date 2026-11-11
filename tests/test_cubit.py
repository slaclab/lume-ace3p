"""Cubit journal variable matching: exact identifiers, comments skipped."""

import pytest

from lume_ace3p.cubit import Cubit

_JOURNAL = """\
reset
# radius=99 is the old value
##{radius = 98}
###{radius = 97}
#{cav_radius = 100}
#{ radius = 10 }
#{ratio=2}
length = 5
#{flag == 1}
#{flag = 3}
create sphere radius {radius}
export genesis "cavity.gen" overwrite
"""


@pytest.fixture
def journal(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'cavity.jou').write_text(_JOURNAL)
    monkeypatch.chdir(src)
    return Cubit('cavity.jou', workdir=str(tmp_path / 'wd'))


def _written(cubit):
    cubit.write_input()
    with open(cubit.workdir + '/' + cubit.input_file) as f:
        return f.read()


def test_substring_name_does_not_match_a_longer_one(journal):
    # `radius` used to hit `cav_radius=` first; it is its own declaration.
    journal.set_value({'radius': 12})
    lines = _written(journal).splitlines()
    assert '#{cav_radius = 100}' in lines
    assert '#{ radius =12}' in lines


def test_comments_are_not_declarations(journal):
    journal.set_value({'radius': 12})
    lines = _written(journal).splitlines()
    assert '# radius=99 is the old value' in lines
    assert '##{radius = 98}' in lines
    assert '###{radius = 97}' in lines


def test_get_value_reads_the_exact_declaration(journal):
    assert journal.get_value('radius') == 10
    assert journal.get_value('cav_radius') == 100
    assert journal.get_value('ratio') == 2
    assert journal.get_value('length') == 5


def test_equality_is_not_an_assignment(journal):
    assert journal.get_value('flag') == 3


def test_undeclared_name(journal, capsys):
    assert journal.get_value('rad') is None
    assert 'not found' in capsys.readouterr().out
    journal.set_value({'rad': 1})
    assert _written(journal) == _JOURNAL


def test_plain_declaration_rewrite(journal):
    journal.set_value({'length': 7})
    assert 'length =7' in _written(journal).splitlines()


# --------------------------------------------------------------------------- #
# Several files, one session.
# --------------------------------------------------------------------------- #

import os
import subprocess

from lume_ace3p.cubit import QUIT_PREFIX

_VARS = '#{radius = 10}\n#{length = 5}\nquit\n'
_GEOM = 'create cylinder height {length} radius {radius}\n  Exit\n'
_PY = 'cubit.cmd("#{radius = 12}")\nquit = 1\n'
_MESH = ('#{length = 6}\nmesh volume all\n'
         'export genesis "first.gen" overwrite\n'
         'export genesis "cavity.gen" overwrite\nquit\n')


@pytest.fixture
def commands(monkeypatch):
    """Shell lines handed to ``subprocess.run``; none executed."""
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(subprocess, 'run', fake_run)
    return seen


@pytest.fixture
def files(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    src.mkdir()
    for name, text in [('vars.jou', _VARS), ('geom.jou', _GEOM),
                       ('tweak.py', _PY), ('mesh.jou', _MESH)]:
        (src / name).write_text(text)
    monkeypatch.chdir(src)
    return src


def _session(files, names=('vars.jou', 'geom.jou', 'tweak.py', 'mesh.jou'),
             workdir=None):
    return Cubit(list(names), workdir=str(workdir or files.parent / 'wd'),
                 ace3p_path='/ace3p/', cubit_path='/cubit/', mpi_caller='srun')


def _read(cubit, name):
    with open(os.path.join(cubit.workdir, name)) as f:
        return f.read()


def test_one_batch_call_over_the_files_in_order(files, commands):
    cubit = _session(files)
    cubit.run()
    assert commands == [
        '/cubit/cubit -batch -nographics -nojournal -noecho '
        'vars.jou geom.jou tweak.py mesh.jou',
        'srun --nodes=1 --ntasks=1 /ace3p/acdtool meshconvert cavity.gen',
    ]
    assert cubit.input_file == ['vars.jou', 'geom.jou', 'tweak.py', 'mesh.jou']


def test_quit_is_commented_out_of_all_but_the_last_file(files, commands):
    cubit = _session(files)
    cubit.run()
    assert _read(cubit, 'vars.jou') == (
        '#{radius = 10}\n#{length = 5}\n' + QUIT_PREFIX + 'quit\n')
    assert _read(cubit, 'geom.jou') == (
        'create cylinder height {length} radius {radius}\n'
        + QUIT_PREFIX + '  Exit\n')
    assert _read(cubit, 'tweak.py') == _PY      # Python is never edited
    assert _read(cubit, 'mesh.jou') == _MESH    # the last file keeps its quit


def test_a_python_file_last_strips_every_journal(files, commands):
    cubit = _session(files, names=('vars.jou', 'mesh.jou', 'tweak.py'))
    cubit.run()
    assert commands[0].endswith('vars.jou mesh.jou tweak.py')
    assert _read(cubit, 'mesh.jou').endswith(QUIT_PREFIX + 'quit\n')


def test_set_value_edits_the_first_declaring_journal(files, commands):
    cubit = _session(files)
    with pytest.warns(UserWarning, match="'length' is declared in vars.jou, mesh.jou"):
        cubit.set_value({'radius': 20, 'length': 7})
    cubit.run()
    assert _read(cubit, 'vars.jou').startswith('#{radius =20}\n#{length =7}\n')
    assert _read(cubit, 'mesh.jou') == _MESH
    assert cubit.get_value('length') == 7


def test_mesh_is_the_last_export_across_the_list(files, commands):
    cubit = _session(files, names=('mesh.jou', 'geom.jou'))
    cubit.get_export()
    assert cubit.exportfile == 'cavity.gen'
    cubit.set_export('other.gen')
    cubit.get_export()
    assert cubit.exportfile == 'other.gen'
    cubit.run()
    assert 'export genesis "first.gen" overwrite\n' in _read(cubit, 'mesh.jou')
    assert commands[-1].endswith('meshconvert other.gen')


def test_no_export_skips_meshconvert(files, commands, capsys):
    cubit = _session(files, names=('vars.jou', 'geom.jou'))
    cubit.run()
    assert len(commands) == 1
    assert 'no export' in capsys.readouterr().out


def test_same_basename_is_refused(files, tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'vars.jou').write_text(_VARS)
    with pytest.raises(ValueError, match="share the basename 'vars.jou'"):
        _session(files, names=('vars.jou', str(other / 'vars.jou'), 'mesh.jou'))


def test_python_alone_is_refused(files):
    with pytest.raises(ValueError, match='at least one journal'):
        _session(files, names=('tweak.py',))


def test_workdir_holding_the_sources_writes_copies(files, commands):
    cubit = _session(files, workdir=files)
    cubit.run()
    assert commands[0].endswith('vars.jou_copy geom.jou_copy tweak.py mesh.jou_copy')
    assert (files / 'vars.jou').read_text() == _VARS
    assert (files / 'tweak.py').read_text() == _PY


def test_absolute_paths_are_written_into_the_workdir(files, commands):
    cubit = _session(files, names=[str(files / n) for n in ('vars.jou', 'mesh.jou')])
    cubit.run()
    assert commands[0].endswith(' vars.jou mesh.jou')
    assert os.path.isfile(os.path.join(cubit.workdir, 'mesh.jou'))
    assert not (files / 'mesh.jou_copy').exists()


def test_lines_is_a_single_journal_view(journal):
    assert journal.lines[0] == 'reset\n'


def test_lines_is_refused_on_several_files(files):
    with pytest.raises(AttributeError, match='per journal'):
        _session(files).lines
