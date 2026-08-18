"""Tests for the ACE3P input-text parser and the T3P / Omega3P / S3P output
parsers.

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

The Omega3P section (Phase 1 of ``docs/acdtool_rework_plan.md``) runs against the
*real* frozen ``omega3p.out`` fixtures in
``tests/fixtures/acdtool/solver_outputs/omega3p`` rather than synthetic text —
the license banner, the differing top-level section order and the ``'real ,
imag'`` eigenvalues are all things only a real file carries.

The S3P section at the bottom (Phase 5) does the same against
``solver_outputs/s3p_90DegreeBend``, and for the same reason plus one more: the
S3P reference documents no output file at all, so those three fixtures are the
only specification of the formats that exists. The cross-check that
``abs(S_complex)`` reproduces the ``Reflection.out`` magnitudes is what stands in
for the missing spec.
"""

import os
import shutil

import numpy as np
import pytest

from lume_ace3p.ace3p import (
    Section, parse_ace3p, write_ace3p, merge_overrides, parse_column_file,
    parse_wakefield, parse_omega3p_output, parse_sparameters, Omega3P, S3P,
    S3POutputWarning, T3P,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OMEGA3P_FIXTURES = os.path.join(HERE, 'fixtures', 'acdtool', 'solver_outputs',
                                'omega3p')


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


def test_t3p_results_dir_override_beats_input_file_job_name(tmp_path):
    """The supported override is the module-level ``results_dir``, because the
    directory is really chosen by the batch script's job name. It wins over an
    input-file ``JobName``, which is undocumented for every solver."""
    source = tmp_path / 'model.t3p'
    _write(source, 'JobName: from_input\n\n' + T3P_INPUT)
    workdir = tmp_path / 'wd'
    os.makedirs(workdir, exist_ok=True)
    t3p = T3P(str(source), workdir=str(workdir), results_dir='from_config')
    assert t3p.results_dir() == os.path.join('from_config', 'OUTPUT')


# --------------------------------------------------------------------------- #
# Omega3P eigenmode output parsing (real fixtures)
# --------------------------------------------------------------------------- #

# Minimal Omega3P input. Nothing in it names the results directory — no shipped
# tutorial input of any type sets 'JobName', so the per-solver default is the
# path a real run actually writes to.
OMEGA3P_INPUT = """\
ModelInfo: {
  File: ./pillbox.ncdf
}
EigenSolver: {
  NumEigenvalues: 2
}
"""


def _fixture(name):
    return os.path.join(OMEGA3P_FIXTURES, name + '.omega3p.out')


def _make_omega3p(tmp_path, fixture=None, job_name=None, results_dir=None):
    """Build an Omega3P wrapper over an input file, with a real ``omega3p.out``
    fixture pre-placed where the wrapper should look for it. No binary runs."""
    text = OMEGA3P_INPUT
    if job_name is not None:
        text = f'JobName: {job_name}\n\n' + text
    os.makedirs(tmp_path, exist_ok=True)
    source = tmp_path / 'model.omega3p'
    _write(source, text)
    workdir = tmp_path / 'wd'
    os.makedirs(workdir, exist_ok=True)
    omega3p = Omega3P(str(source), workdir=str(workdir), results_dir=results_dir)
    if fixture is not None:
        results = os.path.join(str(workdir), omega3p.results_dir())
        os.makedirs(results, exist_ok=True)
        shutil.copy(_fixture(fixture), os.path.join(results, 'omega3p.out'))
    return omega3p


def test_parse_omega3p_real_eigenvalues():
    """The lossless case (tutorial omega3p/pillbox): 2 modes, real
    eigenvalues, a Q per mode and no ExternalQ."""
    data = parse_omega3p_output(_fixture('pillbox'))

    assert len(data['Modes']) == 2
    assert np.array_equal(data['ModeID'], [0, 1])
    assert np.allclose(data['Frequency'], [1191208622.7814, 2064484143.7759])
    assert np.allclose(data['QualityFactor'], [24860.103403403, 21202.076560245])
    assert np.allclose(data['PowerLoss'], [1.332856826259e-06, 2.7085181866114e-06])
    # A real eigenvalue has no imaginary part reported, so no _imag column
    # exists at all, and a lossless run reports no ExternalQ.
    assert 'Frequency_imag' not in data
    assert 'ExternalQ' not in data
    # Non-numeric leaves stay strings rather than being coerced.
    assert data['File'][1].endswith('.mod')
    assert data['Modes'][0]['Frequency'] == pytest.approx(1191208622.7814)


def test_parse_omega3p_complex_eigenvalues():
    """The lossy/port case (omega3p/pillbox-rtop+coax): one mode whose
    Frequency and TotalEnergy arrive as 'real , imag' pairs, plus ExternalQ.
    Frequency keeps the real part so it stays a plottable table column."""
    data = parse_omega3p_output(_fixture('pillbox-rtop+coax'))

    assert len(data['Modes']) == 1
    assert data['Frequency'][0] == pytest.approx(1313756106.8639)
    assert data['Frequency_imag'][0] == pytest.approx(641.33468780722)
    assert data['Frequency_imag'][0] != 0.0
    assert data['TotalEnergy'][0] == pytest.approx(4.4270939088102e-12)
    assert data['TotalEnergy_imag'][0] == 0.0
    assert data['ExternalQ'][0] == pytest.approx(1024235.9659009)
    assert data['QualityFactor'][0] == pytest.approx(28815.235456204)


@pytest.mark.parametrize('name', ['pillbox', 'pillbox-rtop+coax'])
def test_parse_omega3p_survives_banner_and_section_order(name):
    """Two hazards a real file carries and a synthetic one would not: the
    license banner inside ``Version`` (absorbed into the first key's name —
    garbage that must stay ignored, not cleaned up) and top-level section order
    that differs between runs, which is why Mode sections are found by name."""
    data = parse_omega3p_output(_fixture(name))
    assert data['Modes']
    assert len(data['Frequency']) == len(data['Modes'])


def test_omega3p_output_parser_reads_default_results_dir(tmp_path):
    """'omega3p_results' is the authoritative default: the Omega3P reference
    documents no JobName container and no shipped input sets one."""
    omega3p = _make_omega3p(tmp_path, fixture='pillbox')
    assert omega3p.results_dir() == 'omega3p_results'
    omega3p.output_parser()
    assert len(omega3p.output_data['Modes']) == 2
    assert 'QualityFactor' in omega3p.output_data


def test_omega3p_output_parser_honors_results_dir_config(tmp_path):
    """The supported override — a module-level 'results_dir', matching how the
    directory is really chosen (the batch script's job name)."""
    omega3p = _make_omega3p(tmp_path, fixture='pillbox-rtop+coax',
                            results_dir='run17')
    assert omega3p.results_dir() == 'run17'
    omega3p.output_parser()
    assert omega3p.output_data['ExternalQ'][0] == pytest.approx(1024235.9659009)


def test_omega3p_input_file_job_name_is_a_fallback(tmp_path):
    """A top-level 'JobName' in the input file is honored as a best-effort
    fallback, and 'results_dir' beats it. UNVERIFIED against a real run: no
    Omega3P reference documents this key, so if the solver ignores it a real run
    writes to omega3p_results while this looks elsewhere. It is kept because it
    costs nothing, not because it is known to work."""
    omega3p = _make_omega3p(tmp_path, fixture='pillbox', job_name='from_input')
    assert omega3p.results_dir() == 'from_input'
    omega3p.output_parser()
    assert len(omega3p.output_data['Modes']) == 2

    override = _make_omega3p(tmp_path / 'other', fixture='pillbox',
                             job_name='from_input', results_dir='from_config')
    assert override.results_dir() == 'from_config'


def test_omega3p_output_parser_tolerates_missing_file(tmp_path):
    """A failed or interrupted run leaves no omega3p.out — empty output_data,
    not a crash, the same contract T3P has. The module layer raises (naming the
    expected path) when a workflow actually asks for a quantity."""
    omega3p = _make_omega3p(tmp_path)
    omega3p.output_parser()
    assert omega3p.output_data == {}


# --------------------------------------------------------------------------- #
# S3P output parsing (real fixtures) — Phase 5
# --------------------------------------------------------------------------- #

S3P_FIXTURES = os.path.join(HERE, 'fixtures', 'acdtool', 'solver_outputs',
                            's3p_90DegreeBend')


def _s3p_fixture(name):
    return os.path.join(S3P_FIXTURES, name)


def test_parse_sparameters_reads_the_magnitude_table():
    """Reflection.out: an index map, a 13-point scan, and 16 real columns named
    by the file's own header row rather than rebuilt from the index-map size."""
    index_map, frequency, columns = parse_sparameters(
        _s3p_fixture('Reflection.out'))

    assert len(index_map) == 4
    assert index_map['0'] == {'Port': '7', 'Mode': '0', 'Type': 'TE',
                              'Cutoff': 6557190000.0}
    assert len(frequency) == 13
    assert frequency[0] == 9.424e+09
    assert len(columns) == 16
    assert list(columns)[:3] == ['S(0,0)', 'S(0,1)', 'S(0,2)']
    assert columns['S(0,0)'][0] == 0.0323077414
    assert all(np.isrealobj(values) for values in columns.values())


def test_parse_sparameters_reads_the_complex_table():
    """SParameter.out is the same layout with '( real,  imag )' cells — the one
    difference between the two files, which is why one reader covers both. The
    cells contain spaces, so a whitespace split cannot parse them."""
    index_map, frequency, columns = parse_sparameters(
        _s3p_fixture('SParameter.out'))

    assert index_map == parse_sparameters(_s3p_fixture('Reflection.out'))[0]
    assert len(frequency) == 13
    assert set(columns) == {'S({},{})'.format(i, j)
                            for i in range(4) for j in range(4)}
    assert all(np.iscomplexobj(values) for values in columns.values())
    assert columns['S(0,0)'][0] == pytest.approx(complex(8.74038681e-03,
                                                         3.11029869e-02))
    # A negative real part survives the sign-carrying cell form.
    assert columns['S(0,3)'][0].real == pytest.approx(-2.18000519e-04)


def test_parse_sparameters_magnitudes_agree_with_the_complex_table():
    """The cross-check the whole phase rests on, at the parser level: the two
    undocumented files describe the same matrix, so |complex| must reproduce the
    magnitudes everywhere."""
    _, _, magnitudes = parse_sparameters(_s3p_fixture('Reflection.out'))
    _, _, complexes = parse_sparameters(_s3p_fixture('SParameter.out'))

    for name, values in complexes.items():
        assert np.allclose(np.abs(values), magnitudes[name], rtol=1e-7)


def test_parse_sparameters_falls_back_to_positional_column_names(tmp_path):
    """A header whose names do not line up with the data rows falls back to the
    row-major (id1, id2) rebuild — the naming this parser used before Phase 5, so
    a build that labels its columns differently is no worse off than before."""
    text = ('#Index mapping:\n'
            '#  0 : Port 1, Mode 0, Type: TE (cutoff: 1.0e+09 Hz)\n'
            '#  1 : Port 2, Mode 0, Type: TE (cutoff: 1.0e+09 Hz)\n'
            '#Frequency[Hz]  mystery\n'
            '1.0e+09  0.1  0.2  0.3  0.4\n')
    path = _write(tmp_path / 'Reflection.out', text)

    _, frequency, columns = parse_sparameters(path)

    assert list(frequency) == [1.0e+09]
    assert list(columns) == ['S(0,0)', 'S(0,1)', 'S(1,0)', 'S(1,1)']
    assert columns['S(1,1)'][0] == 0.4


def test_parse_column_file_accepts_a_percent_comment():
    """S3P's port mode profiles are the one ACE3P output commented with '%'
    rather than '#'. The header names the six columns."""
    profile = parse_column_file(_s3p_fixture('PortRef7_0.out'))

    assert list(profile) == ['x', 'y', 'Ex', 'Ey', 'Hx', 'Hy']
    assert len(profile['x']) == 58
    assert profile['Ex'][0] == pytest.approx(1952.231180159)


def _make_s3p(tmp_path, files=('Reflection.out', 'SParameter.out',
                               'PortRef7_0.out')):
    """Build an S3P wrapper over an empty input file with the named 90DegreeBend
    fixtures pre-placed in its results directory. No binary runs."""
    os.makedirs(tmp_path, exist_ok=True)
    source = _write(tmp_path / 'model.s3p', '')
    workdir = tmp_path / 'wd'
    s3p = S3P(str(source), workdir=str(workdir))
    results = os.path.join(str(workdir), s3p.results_dir())
    os.makedirs(results, exist_ok=True)
    for name in files:
        shutil.copy(_s3p_fixture(name), os.path.join(results, name))
    return s3p


def test_s3p_output_parser_keeps_the_magnitude_keys(tmp_path):
    """``S(m,n)`` still means |S| and still comes from Reflection.out. Every
    shipped example and every frozen baseline names it, so Phase 5 adds keys
    beside it and redefines nothing."""
    s3p = _make_s3p(tmp_path)
    s3p.output_parser()
    data = s3p.output_data

    assert data['S(0,0)'][0] == 0.0323077414
    assert data['S(3,3)'][-1] == 0.999897413
    assert np.all(data['S(0,0)'] >= 0.0)
    assert data['Frequency'][0] == 9.424e+09


def test_s3p_output_parser_adds_the_complex_split(tmp_path):
    """The complex S-parameter arrives as three real arrays rather than one
    complex column, so a result table stays plottable — the same split
    parse_omega3p_output gives a complex eigenvalue."""
    s3p = _make_s3p(tmp_path)
    s3p.output_parser()
    data = s3p.output_data

    for suffix in ('_real', '_imag', '_phase_deg'):
        values = data['S(0,0)' + suffix]
        assert np.isrealobj(values)
        assert len(values) == len(data['Frequency'])
    assert np.allclose(np.hypot(data['S(0,0)_real'], data['S(0,0)_imag']),
                       data['S(0,0)'], rtol=1e-7)
    assert np.allclose(data['S(0,0)_phase_deg'],
                       np.degrees(np.arctan2(data['S(0,0)_imag'],
                                             data['S(0,0)_real'])))


def test_s3p_output_parser_reads_every_port_profile(tmp_path):
    """Port mode profiles land under their file's stem, one nested
    ``{column: array}`` each, and are not frequency-indexed."""
    s3p = _make_s3p(tmp_path)
    # A second port file, to pin that the glob reads all of them rather than an
    # assumed PortRef7_0.
    results = os.path.join(s3p.workdir, s3p.results_dir())
    shutil.copy(_s3p_fixture('PortRef7_0.out'),
                os.path.join(results, 'PortRef8_1.out'))
    s3p.output_parser()

    assert sorted(k for k in s3p.output_data if k.startswith('PortRef')) == [
        'PortRef7_0', 'PortRef8_1']
    assert list(s3p.output_data['PortRef8_1']) == ['x', 'y', 'Ex', 'Ey',
                                                  'Hx', 'Hy']


def test_s3p_output_parser_without_sparameter_file(tmp_path):
    """Older ACE3P builds write no SParameter.out: warn naming what is missing
    and return the magnitudes, rather than raising."""
    s3p = _make_s3p(tmp_path, files=('Reflection.out',))
    with pytest.warns(S3POutputWarning, match='phase'):
        s3p.output_parser()

    assert s3p.output_data['S(0,0)'][0] == 0.0323077414
    assert 'S(0,0)_real' not in s3p.output_data


def test_s3p_output_parser_still_raises_without_reflection(tmp_path):
    """A missing Reflection.out is not a degraded read but a run that produced
    nothing, so it keeps raising — unlike Omega3P/T3P, whose parsers tolerate an
    absent output because a valid run may not write one."""
    s3p = _make_s3p(tmp_path, files=())
    with pytest.raises(FileNotFoundError):
        s3p.output_parser()
