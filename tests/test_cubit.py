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
