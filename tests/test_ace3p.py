"""Tests for the ACE3P input-text parser and the T3P output parser.

The parser tests here are the regression guard for a bug that made T3P
unusable: ``_tokenize`` only skipped spaces and tabs before testing for a block's
``{``, so an input written brace-on-next-line ::

    ModelInfo:
    {
      File: ./mesh.ncdf

parsed into flat entries with mangled keys (``'{\\n  File'``) and **no nesting**,
which meant no parameter override could reach a nested leaf and re-serializing
emitted unclosed braces. Both brace styles are legal ACE3P input and appear in
the shipped tutorial examples (S3P/Omega3P use same-line, T3P uses next-line), so
both are covered here, along with the byte-stability of the same-line style that
the frozen baselines depend on.
"""

import os

import numpy as np
import pytest

from lume_ace3p.ace3p import (
    Section, parse_ace3p, write_ace3p, merge_overrides, parse_wakefield, T3P,
)


# --------------------------------------------------------------------------- #
# Fixtures — the same content in both brace styles.
# --------------------------------------------------------------------------- #

# Braces on their own line (T3P tutorial style). Also exercises a key containing
# spaces ('Number of sigmas'), duplicate same-named sections (two Monitors), a
# trailing '//' comment, and an inline comment after a value.
NEXT_LINE_BRACE = """\
ModelInfo:
{
  File: ./pillboxwg.ncdf
  BoundaryCondition:
  {
    Exterior: 6 5
    Absorbing: 3 4
  }
}

LoadingInfo:
{
  Bunch:
  {
    Type: Gaussian
    Sigma: 0.01
    Number of sigmas: 5
  }
  SymmetryFactor: 4 //matches bc
  StartPoint: 0.0, 0.0, -0.075
}

Monitor:
{
  Type: Volume
  Name: mymon
}

Monitor:
{
  Type: WakeField
  Name: wakefield
  Smax: 1.4
}

//END
"""

# The same structure with braces on the key's line (S3P/Omega3P style).
SAME_LINE_BRACE = """\
ModelInfo: {
  File: ./pillboxwg.ncdf
  BoundaryCondition: {
    Exterior: 6 5
    Absorbing: 3 4
  }
}
LoadingInfo: {
  Bunch: {
    Type: Gaussian
    Sigma: 0.01
    Number of sigmas: 5
  }
  SymmetryFactor: 4
  StartPoint: 0.0, 0.0, -0.075
}
Monitor: {
  Type: Volume
  Name: mymon
}
Monitor: {
  Type: WakeField
  Name: wakefield
  Smax: 1.4
}
"""


# --------------------------------------------------------------------------- #
# Nesting — the regression guard
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('text', [NEXT_LINE_BRACE, SAME_LINE_BRACE],
                         ids=['next_line_brace', 'same_line_brace'])
def test_both_brace_styles_nest_identically(text):
    """Both styles must produce the same tree. Before the fix the next-line
    style produced flat entries keyed '{\\n  File' and find() returned None."""
    tree = parse_ace3p(text)

    model = tree.find('ModelInfo')
    assert model is not None
    assert model.get_leaf('File') == './pillboxwg.ncdf'
    assert model.find('BoundaryCondition').get_leaf('Exterior') == '6 5'

    bunch = tree.find('LoadingInfo').find('Bunch')
    assert bunch.get_leaf('Sigma') == '0.01'
    # A key containing spaces is preserved verbatim (T3P strips whitespace in
    # keys internally, but the file's spelling is what an override must match).
    assert bunch.get_leaf('Number of sigmas') == '5'
    # An inline '//' comment is stripped from the value.
    assert tree.find('LoadingInfo').get_leaf('SymmetryFactor') == '4'

    # Top-level leaves are not swallowed into the preceding block.
    assert tree.find('LoadingInfo').get_leaf('StartPoint') == '0.0, 0.0, -0.075'


@pytest.mark.parametrize('text', [NEXT_LINE_BRACE, SAME_LINE_BRACE],
                         ids=['next_line_brace', 'same_line_brace'])
def test_duplicate_sections_are_discriminable(text):
    """Two same-named Monitor sections stay separate entries, addressable by a
    discriminator leaf."""
    tree = parse_ace3p(text)
    assert len(tree.children('Monitor')) == 2
    assert tree.find('Monitor', Type='WakeField').get_leaf('Smax') == '1.4'
    assert tree.find('Monitor', Type='Volume').get_leaf('Name') == 'mymon'


def test_next_line_brace_serializes_with_balanced_braces():
    """The bug's worst symptom: re-serializing a next-line-brace file emitted
    opening braces with no closes, producing input the solver cannot read.

    Six blocks: ModelInfo, its BoundaryCondition, LoadingInfo, its Bunch, and the
    two Monitors."""
    text = write_ace3p(parse_ace3p(NEXT_LINE_BRACE))
    assert text.count('{') == text.count('}') == 6
    # And the result re-parses to the same tree.
    assert write_ace3p(parse_ace3p(text)) == text


@pytest.mark.parametrize('text', [NEXT_LINE_BRACE, SAME_LINE_BRACE],
                         ids=['next_line_brace', 'same_line_brace'])
def test_serialization_is_idempotent(text):
    """A parse/write round-trip must reach a fixed point on the first pass, in
    both brace styles — the frozen baselines depend on a solver's input file not
    drifting each time it is rewritten.

    Note ``write_ace3p`` emits its own canonical ``name : value`` spacing, so the
    output is not byte-identical to arbitrary input; what must hold is that
    writing again changes nothing."""
    once = write_ace3p(parse_ace3p(text))
    assert write_ace3p(parse_ace3p(once)) == once


def test_both_brace_styles_serialize_to_the_same_text():
    """The two styles are the same document, so they must converge on one
    canonical serialization."""
    assert (write_ace3p(parse_ace3p(NEXT_LINE_BRACE))
            == write_ace3p(parse_ace3p(SAME_LINE_BRACE)))


def test_empty_value_followed_by_key_is_not_a_block():
    """A key with no value must stay a leaf: the brace peek looks across
    newlines, so it must not mistake the *next key* for a block opener."""
    tree = parse_ace3p('Empty:\nNext: 5\nAfter: {\n  Inner: 1\n}\n')
    assert tree.get_leaf('Empty') == ''
    assert tree.get_leaf('Next') == '5'
    assert tree.find('After').get_leaf('Inner') == '1'


def test_nested_override_reaches_a_next_line_brace_leaf():
    """The practical consequence of the fix: a swept ACE3P parameter can address
    a nested leaf. Before it, merge_overrides had no tree to merge into."""
    tree = parse_ace3p(NEXT_LINE_BRACE)

    overrides = Section()
    loading, bunch = Section(), Section()
    bunch.set_leaf('Sigma', 0.004)
    loading.append('Bunch', bunch)
    overrides.append('LoadingInfo', loading)
    merge_overrides(tree, overrides)

    reparsed = parse_ace3p(write_ace3p(tree))
    assert reparsed.find('LoadingInfo').find('Bunch').get_leaf('Sigma') == '0.004'
    # Untouched siblings survive.
    assert reparsed.find('LoadingInfo').find('Bunch').get_leaf('Type') == 'Gaussian'
    assert reparsed.find('ModelInfo').get_leaf('File') == './pillboxwg.ncdf'


# --------------------------------------------------------------------------- #
# T3P wakefield output parsing
# --------------------------------------------------------------------------- #

# Excerpt of a real longitudinal wakefield.out (ACE3P tutorial t3p/cavity-quarter).
WAKEFIELD_LONGITUDINAL = """\
# T3P wakefield results at transverse point:
#(0.00000000000000e+00,0.00000000000000e+00)
# Loss factor = -3.88576373282202e-01 V/pC
#          s[m]        W_long(s)[V/pC]     I_bunch(s)[C/m]
0.00000000000000e+00 -4.64474318043810e-07 0.00000000000000e+00
5.99584916000000e-04 -7.75095377990907e-07 2.00284274868363e-16
1.19916983200000e-03 -1.16500432771300e-06 4.00568549736726e-16
"""

# Excerpt of a real transverse wakefield.out (tutorial t3p/cavity-half).
WAKEFIELD_TRANSVERSE = """\
# T3P transverse wakefield result using transverse points:
# (0.00000000000000e+00,0.00000000000000e+00) and
# (0.00000000000000e+00,1.25000000000000e-02)
# with offset 1.25000000000000e-02 m
# Kick factor = 9.64058337896157e-02 V/pC
#          s[m]        W_trans(s)[V/pC]     I_bunch(s)[C/m]
0.00000000000000e+00 2.51010484604505e-09 0.00000000000000e+00
5.99584916000000e-04 9.42341092375687e-09 2.00284274868363e-16
"""


def _write(path, text):
    with open(path, 'w') as f:
        f.write(text)
    return str(path)


def test_parse_wakefield_longitudinal(tmp_path):
    data = parse_wakefield(_write(tmp_path / 'wakefield.out',
                                 WAKEFIELD_LONGITUDINAL))
    assert data['WakeType'] == 'longitudinal'
    assert data['LossFactor'] == pytest.approx(-3.88576373282202e-01)
    assert 'KickFactor' not in data
    assert np.allclose(data['s'], [0.0, 5.99584916e-04, 1.199169832e-03])
    assert np.allclose(data['W'], [-4.64474318043810e-07,
                                   -7.75095377990907e-07,
                                   -1.16500432771300e-06])
    assert np.allclose(data['I_bunch'], [0.0, 2.00284274868363e-16,
                                         4.00568549736726e-16])


def test_parse_wakefield_transverse(tmp_path):
    data = parse_wakefield(_write(tmp_path / 'wakefield.out',
                                 WAKEFIELD_TRANSVERSE))
    assert data['WakeType'] == 'transverse'
    assert data['KickFactor'] == pytest.approx(9.64058337896157e-02)
    assert 'LossFactor' not in data
    assert data['Offset'] == pytest.approx(1.25e-02)
    assert data['TransversePoints'] == [(0.0, 0.0), (0.0, 1.25e-02)]
    assert np.allclose(data['s'], [0.0, 5.99584916e-04])


# --------------------------------------------------------------------------- #
# T3P wrapper — output locations are read from the input file, not hardcoded
# --------------------------------------------------------------------------- #

T3P_INPUT = """\
ModelInfo:
{
  File: ./mesh.ncdf
}

Monitor:
{
  Type: Volume
  Name: mymon
}

Monitor:
{
  Type: WakeField
  Name: wakefield
  Smax: 1.4
}
"""


def _make_t3p(tmp_path, text=T3P_INPUT, job_name=None):
    """Build a T3P wrapper over an input file, without running any binary."""
    if job_name is not None:
        text = f'JobName: {job_name}\n\n' + text
    source = tmp_path / 'model.t3p'
    _write(source, text)
    workdir = tmp_path / 'wd'
    os.makedirs(workdir, exist_ok=True)
    return T3P(str(source), workdir=str(workdir))


def test_t3p_resolves_default_results_dir(tmp_path):
    t3p = _make_t3p(tmp_path)
    assert t3p.results_dir() == os.path.join('t3p_results', 'OUTPUT')
    assert t3p.wake_monitor_name() == 'wakefield'


def test_t3p_honors_job_name_and_monitor_name(tmp_path):
    """T3P names its output directory after 'JobName' and its wakefield files
    after the WakeField monitor's own 'Name', so neither can be hardcoded."""
    text = T3P_INPUT.replace('Name: wakefield', 'Name: mywake')
    t3p = _make_t3p(tmp_path, text=text, job_name='run17')
    assert t3p.results_dir() == os.path.join('run17', 'OUTPUT')
    assert t3p.wake_monitor_name() == 'mywake'


def test_t3p_output_parser_reads_named_monitor_file(tmp_path):
    t3p = _make_t3p(tmp_path, job_name='run17')
    results = os.path.join(t3p.workdir, 'run17', 'OUTPUT')
    os.makedirs(results, exist_ok=True)
    _write(os.path.join(results, 'wakefield.out'), WAKEFIELD_LONGITUDINAL)

    t3p.output_parser()
    assert t3p.output_data['LossFactor'] == pytest.approx(-3.88576373282202e-01)
    assert len(t3p.output_data['s']) == 3


def test_t3p_output_parser_tolerates_no_wake_monitor(tmp_path):
    """A T3P run with no WakeField monitor is legitimate (e.g. a pulse-
    propagation run monitoring only power), so this leaves output_data empty
    rather than raising the way S3P.output_parser asserts."""
    text = """\
ModelInfo:
{
  File: ./mesh.ncdf
}

Monitor:
{
  Type: Power
  ReferenceNumber: 4
  Name: inputPower
}
"""
    t3p = _make_t3p(tmp_path, text=text)
    assert t3p.wake_monitor_name() is None
    t3p.output_parser()
    assert t3p.output_data == {}


def test_t3p_output_parser_tolerates_missing_file(tmp_path):
    """Monitor declared but the file absent (an interrupted run) — empty, not a
    crash. The module layer raises when a workflow actually asks for a
    quantity."""
    t3p = _make_t3p(tmp_path)
    t3p.output_parser()
    assert t3p.output_data == {}
