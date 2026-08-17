"""Characterization tests over the real CW23 fixtures (see
`docs/acdtool_rework_plan.md`, Phase 0).

**Most of these tests assert what the code does TODAY, wrong answers included.**
They are regression anchors captured *before* the parser rework, not statements
about what correct behavior is. The four confirmed defects were pinned here
deliberately; each is marked ``DEFECT n`` and names the phase that inverts it:

* **DEFECT 1** — multi-line ``{ ... }`` values were silently dropped.
  **Inverted in Phase 2**: :func:`test_defect1_multiline_brace_value_is_parsed`
  and :func:`test_defect1_roundtrip_writes_balanced_braces`.
* **DEFECT 2** — ``.acdtool`` files use the ``:`` dialect and parse to nothing
  (:func:`test_defect2_acdtool_dialect_parses_to_empty_blocks`). **Still a
  characterization test**: Phase 2 narrowed the fix to failing loudly rather than
  routing the second dialect, so that test stands and
  :func:`test_acdtool_input_raises_naming_the_unsupported_command` is the new
  behavior alongside it.
* **DEFECT 3** — the S3P ``[scaling]`` block ships unclosed, so ``}``-based end
  detection is unreliable. **Fixed in Phase 3**:
  :func:`test_defect3_window_scaling_block_is_unclosed` stays as the assertion
  about the *fixture*, and
  :func:`test_defect3_unclosed_scaling_does_not_swallow_the_next_block` is the
  new behavior.
* **DEFECT 4** — ``--nodes=1 --ntasks=1`` was hardcoded and srun-only.
  **Inverted in Phase 2**:
  :func:`test_defect4_non_srun_caller_gets_a_runnable_command`.

Phase 2 also added the command-dispatch tests (the 19-command table, jobname
injection, argument-count validation) and the transwake ordering-hazard test in
``tests/test_modules.py``. Phase 3 added the shape-reader tests below: one per
output shape, plus the two readers (``[scaling]`` and the curve files) that were
previously not reached at all.

A test that pins a *correct* current behavior is the other kind: those must keep
passing unchanged through the rework. The ``[RoverQ]`` numbers in
:func:`test_roverq_values_from_real_output` are the load-bearing ones — Phase 3
refactored a working parser, and those values did not move.

Also anchored: the Omega3P and S3P outputs that Phases 1 and 5 build on, so the
"before" state of each is recorded rather than reconstructed later.

Provenance for every fixture is in `tests/fixtures/acdtool/SOURCES.md`; per-block
real-output coverage is in `tests/fixtures/acdtool/COVERAGE.md`. No ACE3P binary
is needed — nothing here runs a solver.
"""

import os
import shutil
import subprocess

import pytest

from lume_ace3p.acdtool import (
    Acdtool, AcdtoolOutputWarning, COMMANDS, CURVE, GRID, INPUT_JOBNAME,
    MODE_TABLE, SECTIONS, SIGNAL_COLUMNS, SURFACE, field_sections,
    parse_column_file, read_mode_table, read_scaling, split_output_sections,
)
from lume_ace3p.ace3p import S3P, parse_ace3p, parse_wakefield

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'acdtool')

RFPOST_IN = os.path.join(FIXTURES, 'rfpost_inputs')
RFPOST_OUT = os.path.join(FIXTURES, 'rfpost_outputs')
CURVES = os.path.join(FIXTURES, 'curves')
ACDTOOL_IN = os.path.join(FIXTURES, 'acdtool_inputs')
SOLVER_OUT = os.path.join(FIXTURES, 'solver_outputs')
T3P_OUT = os.path.join(FIXTURES, 't3p_outputs')
TRACK3P_OUT = os.path.join(FIXTURES, 'track3p_outputs')

# CW23's .rfpost template, in file order: RFField (configuration) plus 18
# postprocess blocks. This is an OLDER acdtool build than the user guide
# describes -- the guide documents five blocks absent here (pointRoverQ, dFSlater,
# RoverQRoverQT, IMPACTMap, OpenPMD_IMPACT) and omits three present here (Track,
# TrackScan, coaxPort). See COVERAGE.md; the union is 24 blocks.
TEMPLATE_SECTIONS = [
    'RFField', 'RoverQ', 'RoverQT', 'kickFactor', 'VFFT', 'GBZFFT', 'Multipole',
    'Track', 'TrackScan', 'FieldMap', 'FieldAtPoint', 'ALLFieldAtPoint',
    'FieldOnLine', 'ALLFieldOnLine', 'fieldOn2DBoundary', 'fieldOnSurface',
    'maxFieldsOnSurface', 'powerThroughSurface', 'coaxPort',
]

# The five omega3p runs that enabled RoverQ, with the values the current parser
# produces. Phase 3 must reproduce these exactly.
ROVERQ_EXPECTED = {
    'pillbox+recWG': {
        '0': {'Frequency': 1162076400.0, 'Qext': 348.15, 'V_r': -4.1849,
              'V_i': 0.14117, 'absV': 4.1873, 'RoQ': 135.604},
    },
    'pillbox+recWG+load': {
        '0': {'Frequency': 1161987500.0, 'Qext': 4801.92, 'V_r': 2.5381,
              'V_i': -3.3673, 'absV': 4.21668, 'RoQ': 137.525},
        '1': {'Frequency': 1175080900.0, 'Qext': 6.67743, 'V_r': -1.1485e-06,
              'V_i': 5.021e-08, 'absV': 1.14959e-06, 'RoQ': 1.01078e-11},
    },
    'pillbox-rtop': {
        '0': {'Frequency': 1313810000.0, 'Qext': 0.0, 'V_r': -2.2982,
              'V_i': -3.4926, 'absV': 4.18085, 'RoQ': 119.574},
        '1': {'Frequency': 2329138600.0, 'Qext': 0.0, 'V_r': 0.00068065,
              'V_i': -0.00069209, 'absV': 0.000970705, 'RoQ': 3.63598e-06},
    },
    'pillbox-rtop+coax': {
        '0': {'Frequency': 1313756100.0, 'Qext': 1024240.0, 'V_r': 2.7121,
              'V_i': 1.1767, 'absV': 2.9564, 'RoQ': 119.587},
    },
    # Negative Qext -- pins that the parser makes no positivity assumption.
    'dlwg-pbc': {
        '0': {'Frequency': 11360436000.0, 'Qext': -8.58381e+16, 'V_r': -10.029,
              'V_i': 5.1675, 'absV': 11.2819, 'RoQ': 100.695},
    },
}

# Curve fixtures: (data rows here, column count). field1_0.ec is the only one
# kept at full length; the rest are head -n 22 of the original 302-line files.
CURVE_SHAPES = {
    'field1_0': (20, 10),
    'field1_0.ec': (300, 16),
    'field1_0.bc': (20, 16),
    'field1_1': (20, 10),
    'field1_1.ec': (20, 16),
    'field1_1.bc': (20, 16),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _acdtool(tmp_path, input_fixture, output_fixture=None):
    """Build an ``Acdtool`` over a copy of `input_fixture` in `tmp_path`, and
    optionally stage `output_fixture` as ``rfpost.out`` and parse it.

    The fixture is copied rather than used in place because ``Acdtool.__init__``
    copies its input into the workdir and ``write_input`` rewrites it.
    """
    workdir = str(tmp_path)
    input_path = os.path.join(workdir, os.path.basename(input_fixture))
    shutil.copy(input_fixture, input_path)
    if output_fixture is not None:
        shutil.copy(output_fixture, os.path.join(workdir, 'rfpost.out'))
    acd = Acdtool(input_path, workdir=workdir)
    if output_fixture is not None:
        acd.output_file = 'rfpost.out'
        acd.load_output()
    return acd


def _capture_run(monkeypatch):
    """Replace ``subprocess.run`` so ``Acdtool.run`` builds its command line
    without invoking acdtool. Returns the list commands land in."""
    commands = []
    monkeypatch.setattr(subprocess, 'run',
                        lambda cmd, **kwargs: commands.append(cmd))
    return commands


def _data_rows(path):
    """Numeric rows of a curve file (everything not a '#' comment)."""
    with open(path) as f:
        return [ln.split() for ln in f if ln.strip() and not ln.startswith('#')]


def _scaling_body(name, next_section):
    """Lines of an rfpost.out between the ``[scaling]`` header and the
    `next_section` header that follows it -- i.e. where a closing ``}`` belongs."""
    with open(os.path.join(RFPOST_OUT, name + '.rfpost.out')) as f:
        lines = f.read().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith('[scaling]'))
    tail = lines[start + 1:]
    end = next(i for i, ln in enumerate(tail) if ln.strip() == next_section)
    return tail[:end]


# --------------------------------------------------------------------------- #
# Fixture inventory -- a missing fixture must fail loudly, not skip silently
# --------------------------------------------------------------------------- #


def test_fixture_inventory():
    expected = [
        'SOURCES.md',
        'COVERAGE.md',
        'rfpost_inputs/pillbox-rtop+coax.rfpost',
        'rfpost_inputs/window.rfpost',
        'rfpost_inputs/coaxport-multiline.rfpost',
        'rfpost_outputs/pillbox+recWG.rfpost.out',
        'rfpost_outputs/pillbox+recWG+load.rfpost.out',
        'rfpost_outputs/pillbox-rtop.rfpost.out',
        'rfpost_outputs/pillbox-rtop+coax.rfpost.out',
        'rfpost_outputs/dlwg-pbc.rfpost.out',
        'rfpost_outputs/window.rfpost.out',
        'curves/field1_0', 'curves/field1_0.ec', 'curves/field1_0.bc',
        'curves/field1_1', 'curves/field1_1.ec', 'curves/field1_1.bc',
        'acdtool_inputs/Pillbox.acdtool',
        'solver_outputs/omega3p/pillbox.omega3p.out',
        'solver_outputs/omega3p/pillbox-rtop+coax.omega3p.out',
        'solver_outputs/s3p_90DegreeBend/Reflection.out',
        'solver_outputs/s3p_90DegreeBend/SParameter.out',
        'solver_outputs/s3p_90DegreeBend/PortRef7_0.out',
        # Phase-0 addendum, copied in Phase 2.
        't3p_outputs/cavity-half.wakefield.out',
        't3p_outputs/cavity-half.postprocess.in',
        't3p_outputs/BPM.signal.out',
        'track3p_outputs/Pillbox-2.3MV.en',
    ]
    missing = [p for p in expected
               if not os.path.isfile(os.path.join(FIXTURES, p))]
    assert missing == []


@pytest.mark.parametrize('name,shape', sorted(CURVE_SHAPES.items()))
def test_curve_fixture_shapes_match_sources_md(name, shape):
    """The truncation recorded in SOURCES.md, asserted. A short file here is a
    deliberate copy of the original's first 20 rows, not a parser artifact."""
    n_rows, n_cols = shape
    rows = _data_rows(os.path.join(CURVES, name))
    assert len(rows) == n_rows
    assert {len(r) for r in rows} == {n_cols}


def test_only_field1_0_ec_is_full_length():
    """field1_0.ec keeps all 300 rows (matching `npoint = 300` in the
    generating ALLFieldOnLine block) so Phase 3 has one real row count."""
    full = [n for n, (rows, _) in CURVE_SHAPES.items() if rows == 300]
    assert full == ['field1_0.ec']


# --------------------------------------------------------------------------- #
# .rfpost input parsing -- the '=' dialect
# --------------------------------------------------------------------------- #


def test_rfpost_template_sections(tmp_path):
    """The 19-block template parses to 19 sections in file order."""
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'))
    assert list(acd.input_data) == TEMPLATE_SECTIONS


def test_rfpost_template_only_roverq_enabled(tmp_path):
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'))
    enabled = [k for k, v in acd.input_data.items() if v.get('ionoff') == '1']
    assert enabled == ['RoverQ']
    # RFField is configuration and carries no ionoff at all.
    assert 'ionoff' not in acd.input_data['RFField']


def test_rfpost_values_are_strings_with_comments_stripped(tmp_path):
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'))
    rffield = acd.input_data['RFField']
    assert rffield['ResultDir'] == 'omega3p_results'   # trailing '// Jobname'
    assert rffield['cavityBeta'] == '1.00000'          # trailing '//for R/Q...'
    assert rffield['xsymmetry'] == 'none'
    assert rffield['gradient'] == '2.00000e+07'


def test_window_rfpost_input(tmp_path):
    """The S3P case: two sections, ALLFieldOnLine enabled, and an RFField
    pointing at s3p_results rather than omega3p_results."""
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'window.rfpost'))
    assert list(acd.input_data) == ['RFField', 'ALLFieldOnLine']
    assert acd.input_data['RFField']['ResultDir'] == 's3p_results'
    assert acd.input_data['RFField']['FreqScanID'] == '2'
    assert acd.input_data['RFField']['gradient'] == '-1'   # point-scaled variant
    assert acd.input_data['ALLFieldOnLine']['ionoff'] == '1'
    assert acd.input_data['ALLFieldOnLine']['filename'] == 'field1'


def test_empty_brace_lists_roundtrip(tmp_path):
    """CONTROL for DEFECT 1: the single-line empty list form CW23 actually
    ships survives the parse/write round trip. This is why the defect went
    unnoticed -- every real file takes this path."""
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'))
    coax = acd.input_data['coaxPort']
    assert coax['portID'] == '{  }'
    assert coax['porta'] == '{  }'
    assert coax['portb'] == '{  }'

    acd.write_input('roundtrip.rfpost')
    with open(os.path.join(tmp_path, acd.input_file)) as f:
        text = f.read()
    assert 'portID = {  }' in text
    assert text.count('{') == text.count('}')


def test_defect1_multiline_brace_value_is_parsed(tmp_path):
    """DEFECT 1, **fixed in Phase 2** (this test was inverted).

    Multi-line list values are accumulated across lines into one balanced
    single-line value instead of being truncated at the opening brace. Before the
    fix ``portID`` kept only ``'{'``, ``porta``/``portb`` vanished outright, and
    the stray ``}`` closed the block early — all silently.
    """
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'coaxport-multiline.rfpost'))
    coax = acd.input_data['coaxPort']

    assert coax['portID'] == '{ 7 8 }'
    assert coax['porta'] == '{ 0.01000 0.01000 }'
    assert coax['portb'] == '{ 0.02300 0.02300 }'
    # Values that never crossed a brace are unaffected.
    assert coax['ionoff'] == '1'
    assert coax['modeID2'] == '-1'
    # The block now ends where it really ends, and the next section is intact.
    assert list(acd.input_data) == ['coaxPort', 'maxFieldsOnSurface']
    assert acd.input_data['maxFieldsOnSurface'] == {'ionoff': '1',
                                                    'surfaceID': '6'}


def test_defect1_roundtrip_writes_balanced_braces(tmp_path):
    """DEFECT 1, second half, **fixed in Phase 2** (inverted).

    ``write_input`` used to emit the truncated ``portID = {`` verbatim, producing
    a file with unbalanced braces that acdtool cannot read. Now the values
    survive the round trip and the file is structurally valid; re-parsing it
    yields the same values, so the form written is a fixed point."""
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'coaxport-multiline.rfpost'))
    before = {k: dict(v) for k, v in acd.input_data.items()}
    acd.write_input('roundtrip.rfpost')
    with open(os.path.join(tmp_path, acd.input_file)) as f:
        text = f.read()

    assert 'portID = { 7 8 }' in text
    assert 'porta = { 0.01000 0.01000 }' in text
    assert text.count('{') == text.count('}')       # balanced

    acd.load_input_file()                            # re-read what we wrote
    assert acd.input_data == before


def test_write_input_always_balances_braces(tmp_path):
    """A value assigned programmatically with an unclosed brace is repaired on
    write rather than emitted as-is: ``write_input``'s output is structurally
    valid by construction, which is the general form of defect 1's write half."""
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'))
    acd.input_data['coaxPort']['portID'] = '{ 7'
    acd.write_input('repaired.rfpost')
    with open(os.path.join(tmp_path, acd.input_file)) as f:
        text = f.read()
    assert 'portID = { 7 }' in text
    assert text.count('{') == text.count('}')


# --------------------------------------------------------------------------- #
# .acdtool input parsing -- the ':' dialect
# --------------------------------------------------------------------------- #


def test_defect2_acdtool_dialect_parses_to_empty_blocks(tmp_path):
    """DEFECT 2 (inverted in Phase 2): ``Acdtool`` splits on '=', but
    ``.acdtool`` files are KVC (``key : value``). Every key-value pair is lost
    and the block names keep their trailing colon. No error is raised."""
    acd = _acdtool(tmp_path, os.path.join(ACDTOOL_IN, 'Pillbox.acdtool'))
    assert acd.input_data == {'EnhancementCounter:': {}, 'Trajectory:': {}}


def test_parse_ace3p_reads_acdtool_dialect_correctly():
    """CONTROL for DEFECT 2: the parser Phase 2 should route to already handles
    this file. It is not a new parser that is needed, only dispatch."""
    with open(os.path.join(ACDTOOL_IN, 'Pillbox.acdtool')) as f:
        tree = parse_ace3p(f.read())

    assert [name for name, _ in tree.entries] == ['EnhancementCounter',
                                                  'Trajectory']
    counter = tree.find('EnhancementCounter')
    assert counter.get_leaf('Token') == 'on'
    assert counter.get_leaf('SEYFileName1') == 'copper.dat'
    assert counter.get_leaf('BoundarySurfaceID1') == '6'
    assert counter.get_leaf('MinimumEC') == '0.01'
    assert counter.get_leaf('OutputFile') == 'en'
    # A space-separated list value is kept verbatim as one leaf.
    assert tree.find('Trajectory').get_leaf('ParticleID') == '16100 15400'


# --------------------------------------------------------------------------- #
# rfpost.out output parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('name', sorted(ROVERQ_EXPECTED))
def test_roverq_values_from_real_output(tmp_path, name):
    """The load-bearing anchor: ``[RoverQ]`` as parsed from real output.

    Phase 3 replaced the hand-indexed ``modeline[3]`` access with a
    header-driven reader. It is a refactor of working code, so every number here
    comes out identical; what changed is that ``[scaling]`` is now read too (it
    is emitted by every run and declared by no input block).
    """
    acd = _acdtool(tmp_path,
                   os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'),
                   os.path.join(RFPOST_OUT, name + '.rfpost.out'))

    expected = ROVERQ_EXPECTED[name]
    assert set(acd.output_data) == {'RoverQ', 'scaling'}
    assert acd.output_data['RoverQ']['ModeIDs'] == sorted(expected)
    for mode_id, columns in expected.items():
        assert acd.output_data['RoverQ'][mode_id] == columns


def test_mode_table_reader_is_header_driven(tmp_path):
    """The point of the refactor: column *names* come from the header row, not
    from a per-section list of hand-counted positions.

    A build that reorders the columns or adds one is read correctly, where the
    old ``modeline[3]``-style access would have silently mislabeled every value.
    """
    data = read_mode_table([
        ' ModeID   RoQ(ohm/cavity)   Qext          V_r, V_i          |V|   New\n',
        '    0     1.00695e+02      -8.58381e+16  -1.0e+01,  5.2e+00  11.3  7\n',
    ], 'RoverQ')

    assert data['ModeIDs'] == ['0']
    assert data['0'] == {'RoQ': 100.695, 'Qext': -8.58381e+16, 'V_r': -10.0,
                         'V_i': 5.2, 'absV': 11.3, 'New': 7.0}


def test_mode_table_without_a_header_warns_naming_the_section():
    """No ``ModeID`` header row means the columns cannot be named, which for the
    six mode-indexed sections with no real-output fixture is the likely failure.
    It warns naming itself rather than returning a plausible-looking dict."""
    with pytest.warns(AcdtoolOutputWarning, match='dFSlater'):
        assert read_mode_table(['   0  1.0  2.0\n'], 'dFSlater') == {}


def test_scaling_block_is_parsed_from_both_variants(tmp_path):
    """``[scaling]`` is emitted by every run and declared by no input block, so
    the ionoff-driven loop never looked at it -- and with it went ``m_factor``,
    the only normalized-to-physical field conversion acdtool reports. Phase 3
    reads it outside that loop, in both variants."""
    for name in ROVERQ_EXPECTED:
        path = os.path.join(RFPOST_OUT, name + '.rfpost.out')
        with open(path) as f:
            assert '[scaling]' in f.read()

    # gradient-normalized: V (complex) and ga, from an omega3p run.
    acd = _acdtool(tmp_path,
                   os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'),
                   os.path.join(RFPOST_OUT, 'pillbox-rtop+coax.rfpost.out'))
    scaling = acd.output_data['scaling']
    assert scaling['Variant'] == 'gradient'
    assert scaling['ModeID'] == '0'
    assert scaling['V'] == pytest.approx(-2.6113)
    assert scaling['V_imag'] == pytest.approx(-1.0557)
    assert scaling['ga'] == pytest.approx(40.2375)
    assert scaling['m_factor'] == pytest.approx(4.56042e+05)
    assert scaling['m_factor_imag'] == pytest.approx(-1.97695e+05)
    assert scaling['m_factor_amplitude'] == pytest.approx(4.97049e+05)
    assert scaling['m_factor_phase_deg'] == pytest.approx(-23.4368)
    # The integration bounds the scaling was computed over.
    assert (scaling['gz1'], scaling['gz2']) == (-0.035, 0.035)
    assert 'Ez_scaled_to' not in scaling

    # point-scaled: what `gradient = -1` ("no scaling") selects, from the S3P
    # run. Its ALLFieldOnLine block is enabled but its curve files are not
    # staged here, which is the warning tested just below.
    with pytest.warns(AcdtoolOutputWarning, match='ALLFieldOnLine'):
        acd = _acdtool(tmp_path,
                       os.path.join(RFPOST_IN, 'window.rfpost'),
                       os.path.join(RFPOST_OUT, 'window.rfpost.out'))
    scaling = acd.output_data['scaling']
    assert scaling['Variant'] == 'point'
    assert scaling['Ez_from_O3P'] == 0.0
    assert scaling['Ez_from_O3P_imag'] == 0.0
    assert scaling['Ez_scaled_to'] == 1.0
    assert scaling['m_factor'] == 1.0
    assert (scaling['x0'], scaling['y0'], scaling['z0']) == (0.0, 0.0, 0.0)
    assert 'ga' not in scaling


def test_curve_files_are_read_when_present(tmp_path):
    """The window run's only enabled block is ``ALLFieldOnLine``, whose output
    goes to separate ``field1_*`` files rather than into an
    ``[ALLFieldOnLine]`` section. Phase 3 reads them, keyed by filename, and
    exposes them as a field artifact rather than as table columns."""
    for name in os.listdir(CURVES):
        shutil.copy(os.path.join(CURVES, name), os.path.join(tmp_path, name))
    acd = _acdtool(tmp_path,
                   os.path.join(RFPOST_IN, 'window.rfpost'),
                   os.path.join(RFPOST_OUT, 'window.rfpost.out'))

    curves = acd.output_data['ALLFieldOnLine']
    assert sorted(curves) == sorted(CURVE_SHAPES)
    # The per-mode file carries E, B and Sz together; the complex ones split
    # each component into real/imag/amplitude/phase.
    assert list(curves['field1_0']) == ['x', 'y', 'z', 'Ex', 'Ey', 'Ez',
                                        'Bx', 'By', 'Bz', 'Sz']
    assert list(curves['field1_0.ec'])[:5] == ['x', 'y', 'z', 'Ex_r', 'Ex_i']
    assert curves['field1_0.ec']['E_amp'][0] == pytest.approx(2.0126e+03)
    for name, (rows, columns) in CURVE_SHAPES.items():
        assert len(curves[name]) == columns, name
        assert all(len(values) == rows for values in curves[name].values()), name

    # Curves are field artifacts, never table columns (design decision 4).
    assert sorted(field_sections(acd.output_data)) == ['ALLFieldOnLine']


def test_curve_block_with_no_files_warns_naming_the_section(tmp_path):
    """An enabled curve block that wrote nothing is a failed run, not an empty
    result: it warns naming the section and the filenames it looked for. This
    used to print 'ALLFieldOnLine not found in output file', which was
    misleading -- the block never writes into rfpost.out at all."""
    with pytest.warns(AcdtoolOutputWarning, match='ALLFieldOnLine'):
        acd = _acdtool(tmp_path,
                       os.path.join(RFPOST_IN, 'window.rfpost'),
                       os.path.join(RFPOST_OUT, 'window.rfpost.out'))
    assert set(acd.output_data) == {'scaling'}


def test_unreadable_section_warns_naming_itself(tmp_path):
    """A section flagged on whose output cannot be read used to print to stdout
    and leave a silent hole in ``output_data``. Phase 3 requires a warning naming
    the section; here the block is enabled but the output has no matching
    ``surfaceID``, so there is nothing to attribute its scalars to."""
    text = ('powerThroughSurface\n{\n   ionoff = 1\n   surfaceID = 6\n}\n')
    input_path = os.path.join(tmp_path, 'unimplemented.rfpost')
    with open(input_path, 'w') as f:
        f.write(text)
    with open(os.path.join(tmp_path, 'rfpost.out'), 'w') as f:
        f.write('[powerThroughSurface]\n{\n   nothing readable here\n}\n')

    acd = Acdtool(input_path, workdir=str(tmp_path))
    acd.output_file = 'rfpost.out'
    with pytest.warns(AcdtoolOutputWarning, match='powerThroughSurface'):
        acd.load_output()
    assert acd.output_data['powerThroughSurface'] == {'SurfaceIDs': []}


def test_absent_section_still_reports_to_stdout(tmp_path, capsys):
    """An enabled block whose section is simply not in the output file keeps the
    original 'not found' report -- that is a normal outcome (the run may have
    been configured differently), not an unreadable format."""
    text = 'powerThroughSurface\n{\n   ionoff = 1\n   surfaceID = 6\n}\n'
    input_path = os.path.join(tmp_path, 'absent.rfpost')
    with open(input_path, 'w') as f:
        f.write(text)
    shutil.copy(os.path.join(RFPOST_OUT, 'pillbox-rtop+coax.rfpost.out'),
                os.path.join(tmp_path, 'rfpost.out'))

    acd = Acdtool(input_path, workdir=str(tmp_path))
    acd.output_file = 'rfpost.out'
    acd.load_output()
    assert 'powerThroughSurface' not in acd.output_data
    assert 'powerThroughSurface' in capsys.readouterr().out


def test_unknown_section_warns_rather_than_failing_silently(tmp_path):
    """A newer acdtool build ships blocks this package has never seen. The input
    round-trips them untouched (Phase 2), and an *enabled* one warns that its
    output shape is unknown rather than vanishing."""
    text = 'somethingBrandNew\n{\n   ionoff = 1\n}\n'
    input_path = os.path.join(tmp_path, 'future.rfpost')
    with open(input_path, 'w') as f:
        f.write(text)
    shutil.copy(os.path.join(RFPOST_OUT, 'pillbox-rtop+coax.rfpost.out'),
                os.path.join(tmp_path, 'rfpost.out'))

    acd = Acdtool(input_path, workdir=str(tmp_path))
    acd.output_file = 'rfpost.out'
    with pytest.warns(AcdtoolOutputWarning, match='somethingBrandNew'):
        acd.load_output()
    assert set(acd.output_data) == {'scaling'}


def test_vfft_printgroup_nterm_is_rejected_by_name(tmp_path):
    """``VFFT``'s ``printGroup`` changes the grouping of its output: ``nterm``
    groups by multipole component, which is not a mode-indexed table. Only
    ``ModeID`` is read, and the other is rejected naming the key -- rather than
    read as though it were mode-indexed."""
    text = ('VFFT\n{\n   ionoff = 1\n   printGroup = nterm\n}\n')
    input_path = os.path.join(tmp_path, 'vfft.rfpost')
    with open(input_path, 'w') as f:
        f.write(text)
    shutil.copy(os.path.join(RFPOST_OUT, 'pillbox-rtop+coax.rfpost.out'),
                os.path.join(tmp_path, 'rfpost.out'))

    acd = Acdtool(input_path, workdir=str(tmp_path))
    acd.output_file = 'rfpost.out'
    with pytest.warns(AcdtoolOutputWarning, match='printGroup'):
        acd.load_output()
    assert 'VFFT' not in acd.output_data


def test_surface_scalars_split_a_complex_power(tmp_path):
    """``powerThroughSurface``'s power is complex [W], the real part being the
    average flow from the complex Poynting vector -- so it gets the same
    real/imag split Omega3P's complex eigenfrequency does, not a plain float.

    **The output format is unverified**: no tutorial run enabled this block (see
    COVERAGE.md), so this pins the reader's handling of the value forms, not the
    surrounding layout.
    """
    text = 'powerThroughSurface\n{\n   ionoff = 1\n   surfaceID = 6\n}\n'
    input_path = os.path.join(tmp_path, 'power.rfpost')
    with open(input_path, 'w') as f:
        f.write(text)
    with open(os.path.join(tmp_path, 'rfpost.out'), 'w') as f:
        f.write('[powerThroughSurface]\n{\n   surfaceID :   6\n'
                '   Power = ( 1.25000e+03, -4.00000e+01)\n}\n')

    acd = Acdtool(input_path, workdir=str(tmp_path))
    acd.output_file = 'rfpost.out'
    acd.load_output()
    assert acd.output_data['powerThroughSurface'] == {
        '6': {'Power': 1250.0, 'Power_imag': -40.0}, 'SurfaceIDs': ['6']}


def test_field_at_point_has_no_index_axis(tmp_path):
    """``FieldAtPoint`` is its own shape: no ``modeID1``/``modeID2``, so it
    evaluates only the ``ModeID`` named in ``RFField`` and resolves straight to
    scalars -- unlike the mode-indexed ``ALLFieldAtPoint``. Format unverified."""
    text = 'FieldAtPoint\n{\n   ionoff = 1\n}\n'
    input_path = os.path.join(tmp_path, 'point.rfpost')
    with open(input_path, 'w') as f:
        f.write(text)
    with open(os.path.join(tmp_path, 'rfpost.out'), 'w') as f:
        f.write('[FieldAtPoint]\n{\n   Ez = 2.00000e+07\n'
                '   Bz = ( 1.00000e-03,  2.00000e-04)\n}\n')

    acd = Acdtool(input_path, workdir=str(tmp_path))
    acd.output_file = 'rfpost.out'
    acd.load_output()
    assert acd.output_data['FieldAtPoint'] == {
        'Ez': 2.0e+07, 'Bz': 1.0e-03, 'Bz_imag': 2.0e-04}


def test_grid_block_records_filenames_without_parsing(tmp_path):
    """Grid output is recorded, not parsed: ``FieldMap`` writes **fixed**
    ``Efield-map.dat`` / ``Bfield-map.dat`` (the block has no ``filename`` key at
    all) and ``OpenPMD_IMPACT`` writes HDF5, so reading them is deferred."""
    text = 'FieldMap\n{\n   ionoff = 1\n   nx = 20\n}\n'
    input_path = os.path.join(tmp_path, 'map.rfpost')
    with open(input_path, 'w') as f:
        f.write(text)
    shutil.copy(os.path.join(RFPOST_OUT, 'pillbox-rtop+coax.rfpost.out'),
                os.path.join(tmp_path, 'rfpost.out'))
    for name in ['Efield-map.dat', 'Bfield-map.dat']:
        with open(os.path.join(tmp_path, name), 'w') as f:
            f.write('not parsed\n')

    acd = Acdtool(input_path, workdir=str(tmp_path))
    acd.output_file = 'rfpost.out'
    acd.load_output()
    assert acd.output_data['FieldMap'] == {
        'files': ['Bfield-map.dat', 'Efield-map.dat']}
    assert 'FieldMap' in field_sections(acd.output_data)


def test_section_table_covers_the_documented_block_surface():
    """All 24 blocks (``RFField`` + 23 postprocess) plus ``[scaling]``, which is
    emitted but never declared. The union of the tutorial template and the
    reference -- neither is a superset of the other."""
    assert set(SECTIONS) == set(TEMPLATE_SECTIONS) | {
        # Documented in the reference, absent from the tutorial's template.
        'pointRoverQ', 'dFSlater', 'RoverQRoverQT', 'IMPACTMap',
        'OpenPMD_IMPACT',
        # Emitted by every run, declared by no block.
        'scaling',
    }
    assert len(SECTIONS) == 25          # 24 blocks + '[scaling]'
    # The blocks carrying modeID1/modeID2 are exactly the mode-indexed ones.
    assert {n for n, s in SECTIONS.items() if s.shape == MODE_TABLE} == {
        'RoverQ', 'RoverQT', 'RoverQRoverQT', 'kickFactor', 'pointRoverQ',
        'dFSlater', 'VFFT', 'ALLFieldAtPoint', 'coaxPort'}
    assert {n for n, s in SECTIONS.items() if s.shape == SURFACE} == {
        'maxFieldsOnSurface', 'powerThroughSurface'}
    # Only three shapes have a real acdtool output behind them -- COVERAGE.md.
    assert {n for n, s in SECTIONS.items() if s.validated} == {
        'RoverQ', 'ALLFieldOnLine', 'scaling'}
    # Every curve/grid block names the files it writes, and the schemes differ.
    for name, section in SECTIONS.items():
        if section.shape in (CURVE, GRID):
            assert section.files, name
    assert SECTIONS['FieldOnLine'].files == ('{filename}.e', '{filename}.b',
                                             '{filename}.ec', '{filename}.bc')
    assert SECTIONS['ALLFieldOnLine'].files == ('{filename}_*',)


def test_defect3_window_scaling_block_is_unclosed():
    """DEFECT 3 (fixed in Phase 3): in the S3P output the ``[scaling]`` block
    has no closing ``}``, so ``startswith('}')`` end detection cannot bound it.

    Asserted on the fixture text directly: the current parser never reads
    ``[scaling]``, so the damage is only reachable once Phase 3 does.
    """
    # No '}' between '[scaling]' and the section that follows it.
    assert not any(ln.startswith('}') for ln in
                   _scaling_body('window', 'ALLFieldOnLine'))

    # Every other fixture closes the block, which is why the bug is S3P-only.
    for name in ROVERQ_EXPECTED:
        assert any(ln.startswith('}') for ln in
                   _scaling_body(name, 'RoverQ')), name


def test_defect3_unclosed_scaling_does_not_swallow_the_next_block():
    """DEFECT 3, **fixed in Phase 3**: a section now ends at whichever comes
    first -- a ``}`` in column 0, the next ``[section]`` header, or the start of
    an input-echo block.

    The S3P output's unclosed ``[scaling]`` is followed by the ``ALLFieldOnLine``
    echo, so ``}``-only detection ran on into it. Asserted both ways: the echo's
    keys stay out of the scaling result, and the section body stops at the echo.
    """
    with open(os.path.join(RFPOST_OUT, 'window.rfpost.out')) as f:
        lines = f.readlines()
    sections = split_output_sections(lines)

    assert list(sections) == ['scaling']
    body = ''.join(sections['scaling'])
    assert 'm_factor' in body
    assert 'ALLFieldOnLine' not in body      # the echo that follows is not ours
    assert 'filename' not in body

    scaling = read_scaling(sections['scaling'])
    assert scaling['Variant'] == 'point'
    for swallowed in ['ionoff', 'rot180', 'npoint', 'filename', 'rfphase']:
        assert swallowed not in scaling, swallowed


def test_split_output_sections_ignores_indented_brackets():
    """``[z0 = 0.00000]`` inside the RFField echo is not a section header --
    only column 0 counts. Every fixture has two RFField echoes carrying it."""
    with open(os.path.join(RFPOST_OUT, 'pillbox-rtop+coax.rfpost.out')) as f:
        lines = f.readlines()
    assert any('[z0' in line for line in lines)

    sections = split_output_sections(lines)
    assert list(sections) == ['scaling', 'RoverQ']     # in file order
    # And the RoverQ body is bounded by its own '}', not by the file's end.
    assert 'coaxPort' not in ''.join(sections['RoverQ'])


def test_both_scaling_variants_are_present():
    """Phase 3 needs both: gradient-normalized (V / ga) from the omega3p runs
    and point-scaled (Ez from O3P / Ez scaled to) from the window run, which is
    what `gradient = -1` selects."""
    with open(os.path.join(RFPOST_OUT, 'pillbox-rtop+coax.rfpost.out')) as f:
        normalized = f.read()
    assert 'ga    =' in normalized
    assert 'E,B m_factor' in normalized
    assert 'Ez scaled to' not in normalized

    with open(os.path.join(RFPOST_OUT, 'window.rfpost.out')) as f:
        point_scaled = f.read()
    assert 'Ez from O3P' in point_scaled
    assert 'Ez scaled to' in point_scaled
    assert 'E,B m_factor' in point_scaled


# --------------------------------------------------------------------------- #
# run() command construction
# --------------------------------------------------------------------------- #


def test_run_infers_postprocess_rf_from_extension(tmp_path, monkeypatch):
    """Extension inference is the only dispatch today. Phase 2 makes the command
    explicit but must keep this default so existing configs run untouched."""
    commands = _capture_run(monkeypatch)
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'))
    with open(os.path.join(tmp_path, 'rfpost.out'), 'w'):
        pass
    acd.run()

    assert len(commands) == 1
    assert 'acdtool postprocess rf ' in commands[0]
    assert acd.output_file == 'rfpost.out'
    # run() writes the input through write_input first, which renames it to
    # avoid clobbering the original in the same directory.
    assert commands[0].endswith('_copy')


def test_defect4_non_srun_caller_gets_a_runnable_command(tmp_path, monkeypatch):
    """DEFECT 4, **fixed in Phase 2** (inverted).

    ``--nodes=1 --ntasks=1`` were srun-only flags emitted regardless of the
    caller. The command line now follows the ``ace3p.py`` convention
    (``-n <tasks> -c <cores>``), which mpirun also accepts."""
    commands = _capture_run(monkeypatch)
    workdir = str(tmp_path)
    input_path = os.path.join(workdir, 'pillbox-rtop+coax.rfpost')
    shutil.copy(os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'), input_path)
    acd = Acdtool(input_path, workdir=workdir,
                  ace3p_path='/ace3p/', mpi_caller='mpirun')
    with open(os.path.join(workdir, 'rfpost.out'), 'w'):
        pass
    acd.run()

    assert commands[0].startswith('mpirun -n 1 -c 1 /ace3p/acdtool '
                                  'postprocess rf ')
    assert '--nodes=' not in commands[0]
    assert '--ntasks=' not in commands[0]


def test_no_mpi_caller_omits_the_rank_flags(tmp_path, monkeypatch):
    """With no MPI caller there is nothing to consume ``-n``/``-c``, so they are
    left off entirely rather than emitted as bare leading arguments."""
    commands = _capture_run(monkeypatch)
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'))
    with open(os.path.join(tmp_path, 'rfpost.out'), 'w'):
        pass
    acd.run()

    assert commands[0].startswith('acdtool postprocess rf ')


def test_cpu_bind_opts_guarded_against_non_srun(tmp_path, monkeypatch):
    """The same guard ``ace3p.py`` applies: ``--cpu-bind`` is srun-only."""
    commands = _capture_run(monkeypatch)
    workdir = str(tmp_path)
    input_path = os.path.join(workdir, 'pillbox-rtop+coax.rfpost')
    shutil.copy(os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'), input_path)
    with open(os.path.join(workdir, 'rfpost.out'), 'w'):
        pass
    for caller, expect in [('srun', True), ('mpirun', False)]:
        acd = Acdtool(input_path, workdir=workdir, mpi_caller=caller,
                      acdtool_opts='--cpu-bind=cores')
        acd.run()
        assert ('--cpu-bind=cores' in commands[-1]) is expect, caller


def test_serial_command_pins_one_rank_but_not_the_cpu_count(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """Only ``postprocess rf`` / ``volmontomode`` run in parallel, so a serial
    command's rank count is forced to 1 with a warning. The CPU count is left
    alone — the tutorial runs the serial ``transwake`` as ``srun -n 1 -c 256``."""
    commands = _capture_run(monkeypatch)
    acd = Acdtool(None, workdir=str(tmp_path), mpi_caller='srun',
                  acdtool_command='postprocess transwake',
                  acdtool_tasks=16, acdtool_cores=256)
    acd.run(args=[0.0, 0.0, 0.0, 0.0125])

    assert commands[0].startswith('srun -n 1 -c 256 acdtool '
                                  'postprocess transwake t3p_results ')
    assert 'runs on a single rank' in capsys.readouterr().out


def test_rf_accepts_a_configurable_rank_count(tmp_path, monkeypatch):
    commands = _capture_run(monkeypatch)
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'))
    acd.MPI_CALLER = 'srun'
    acd.acdtool_tasks, acd.acdtool_cores = 4, 8
    with open(os.path.join(tmp_path, 'rfpost.out'), 'w'):
        pass
    acd.run()

    assert commands[0].startswith('srun -n 4 -c 8 acdtool postprocess rf ')


def test_acdtool_input_raises_naming_the_unsupported_command(tmp_path,
                                                             monkeypatch):
    """DEFECT 2, narrowed scope, **fixed in Phase 2**: a ``.acdtool`` input can no
    longer be run silently to nothing.

    ``run()`` used to launch no subprocess and never set ``output_file``, so a
    later ``load_output`` raised AttributeError. It now raises an error naming
    ``postprocess track3p`` and why it is unsupported. The KVC dialect itself is
    deliberately still unrouted — see
    :func:`test_defect2_acdtool_dialect_parses_to_empty_blocks`, which stays a
    characterization test."""
    commands = _capture_run(monkeypatch)
    acd = _acdtool(tmp_path, os.path.join(ACDTOOL_IN, 'Pillbox.acdtool'))

    with pytest.raises(ValueError, match='postprocess track3p'):
        acd.run()
    assert commands == []
    # No AttributeError waiting for a later caller: the attribute always exists.
    assert acd.output_file is None


def test_unknown_command_lists_the_known_ones(tmp_path):
    with pytest.raises(ValueError, match='postprocess transwake'):
        Acdtool(None, workdir=str(tmp_path),
                acdtool_command='postprocess wiggle')


def test_wrong_argument_count_is_rejected(tmp_path, monkeypatch):
    """transwake takes four coordinates besides its jobname."""
    _capture_run(monkeypatch)
    acd = Acdtool(None, workdir=str(tmp_path),
                  acdtool_command='postprocess transwake')
    with pytest.raises(ValueError, match='takes 4 argument'):
        acd.run(args=[0.0, 0.0])


def test_track3p_row_carries_the_corrected_signature():
    """DEFECT 6: the second argument is a ``<jobname>``, not a field level. The
    tutorial's ``acdtool postprocess track3p Pillbox.acdtool 2.3MV`` names its
    jobname after the field level it was run at, which is what made the
    misreading plausible."""
    spec = COMMANDS['postprocess track3p']
    assert spec.form == INPUT_JOBNAME
    assert spec.jobname is True
    assert spec.nargs == (0, 0)          # no field level, no extra arguments
    assert spec.default_jobname == 'track3p_results'
    assert spec.requires == 'track3p_particles'
    assert spec.dispatch is False        # needs the ':' dialect


@pytest.mark.parametrize('command,operands', [
    ('postprocess transwake', 't3p_results 0.0 0.0 0.0 0.0125'),
    ('postprocess coaxsignal', 't3p_results'),
    ('postprocess volmontomode', 't3p_results'),
    ('postprocess wake_new', 't3p_results 0.0 0.0125'),
])
def test_positional_commands_inject_the_default_jobname(tmp_path, monkeypatch,
                                                        command, operands):
    """The positional commands take the solver's job name as their first
    argument; with nothing else supplying one, the documented per-solver default
    is used. Matches the tutorial's own invocations."""
    commands = _capture_run(monkeypatch)
    args = [a for a in operands.split()[1:]]
    acd = Acdtool(None, workdir=str(tmp_path), acdtool_command=command)
    acd.run(args=args)

    assert commands[0] == 'acdtool ' + command + ' ' + operands
    # No input file is fabricated for a positional command.
    assert acd.input_file is None
    assert acd.input_data == {}


def test_jobname_override_reaches_the_command_line(tmp_path, monkeypatch):
    commands = _capture_run(monkeypatch)
    acd = Acdtool(None, workdir=str(tmp_path),
                  acdtool_command='postprocess coaxsignal',
                  jobname='my_results')
    acd.run()

    assert commands[0].endswith('postprocess coaxsignal my_results')
    # output_file is set on every dispatch path, so a failed run reports a
    # missing output rather than raising AttributeError.
    assert acd.output_file == 'my_results/OUTPUT/signal.out'


def test_mesh_deform_is_invocable(tmp_path, monkeypatch):
    """Design decision 3: ``mesh deform`` stays invocable but is not wired as a
    mesh producer. It takes three positional arguments and no jobname."""
    commands = _capture_run(monkeypatch)
    acd = Acdtool(None, workdir=str(tmp_path), acdtool_command='mesh deform')
    acd.run(args=['./tem3p_results/DeformedMesh.ncdf', 'Scaled5000Mesh.ncdf',
                  5000])

    assert commands[0] == ('acdtool mesh deform '
                           './tem3p_results/DeformedMesh.ncdf '
                           'Scaled5000Mesh.ncdf 5000')
    assert acd.output_file is None


def test_command_table_covers_the_documented_surface():
    """All 19 commands from ``references/acdtool-commands.pdf``, and the
    serial/parallel split it states."""
    assert len(COMMANDS) == 19
    assert set(COMMANDS) == {
        'meshconvert', 'meshconvertdirect', 'resource',
        'mesh stats', 'mesh check', 'mesh fix', 'mesh deform',
        'mesh warpsurface',
        'postprocess rf', 'postprocess eigentomode',
        'postprocess volmontomode', 'postprocess wake_new',
        'postprocess wake_direct', 'postprocess transwake',
        'postprocess coaxsignal', 'postprocess pic3pstats',
        'postprocess pic3pconvert', 'postprocess track3p',
        'postprocess project',
    }
    # "acdtool submodules run serially with the exception of acdtool postprocess
    # volmontomode and acdtool postprocess rf."
    assert {n for n, s in COMMANDS.items() if s.parallel} == {
        'postprocess rf', 'postprocess volmontomode'}
    # Every command held back names why, so the reason reaches the user.
    for name, spec in COMMANDS.items():
        assert spec.wired or spec.note, name


def test_unknown_rfpost_block_roundtrips_untouched(tmp_path):
    """The block set varies by acdtool build — a newer template carries blocks we
    have never seen — so the input parser must carry unknown sections through
    rather than enumerate a fixed list."""
    text = ('RFField\n{\n   ResultDir = omega3p_results\n}\n\n'
            'somethingBrandNew\n{\n   ionoff = 0\n   knob = 1.25\n'
            '   list = {\n      1\n      2\n   }\n}\n')
    input_path = os.path.join(tmp_path, 'future.rfpost')
    with open(input_path, 'w') as f:
        f.write(text)

    acd = Acdtool(input_path, workdir=str(tmp_path))
    assert list(acd.input_data) == ['RFField', 'somethingBrandNew']
    assert acd.input_data['somethingBrandNew'] == {
        'ionoff': '0', 'knob': '1.25', 'list': '{ 1 2 }'}

    acd.write_input('future_out.rfpost')
    with open(os.path.join(tmp_path, acd.input_file)) as f:
        written = f.read()
    assert 'somethingBrandNew' in written
    assert written.count('{') == written.count('}')


# --------------------------------------------------------------------------- #
# Omega3P output -- the Phase 1 starting point
# --------------------------------------------------------------------------- #


def _omega3p_tree(name):
    with open(os.path.join(SOLVER_OUT, 'omega3p', name + '.omega3p.out')) as f:
        return parse_ace3p(f.read())


def test_omega3p_real_eigenvalues_parse_today():
    """`omega3p.out` is KVC, so ``parse_ace3p`` already reads it unmodified.
    Phase 1 only has to walk the Mode sections -- no new parser."""
    tree = _omega3p_tree('pillbox')
    modes = tree.children('Mode')
    assert len(modes) == 2
    assert modes[0].get_leaf('Frequency') == '1191208622.7814'
    assert modes[0].get_leaf('QualityFactor') == '24860.103403403'
    assert modes[1].get_leaf('Frequency') == '2064484143.7759'
    # Real-eigenvalue runs report no ExternalQ.
    assert modes[0].get_leaf('ExternalQ') is None


def test_omega3p_complex_eigenvalues_parse_today():
    """The lossy/port case: Frequency and TotalEnergy arrive as 'real , imag'
    pairs and ExternalQ appears. Phase 1 splits these."""
    tree = _omega3p_tree('pillbox-rtop+coax')
    modes = tree.children('Mode')
    assert len(modes) == 1
    assert modes[0].get_leaf('Frequency') == '1313756106.8639 , 641.33468780722'
    assert modes[0].get_leaf('TotalEnergy') == '4.4270939088102e-12 , 0.0'
    assert modes[0].get_leaf('ExternalQ') == '1024235.9659009'


def test_omega3p_banner_does_not_break_parsing():
    """The license banner inside ``Version`` gets absorbed into the first
    top-level key name -- garbage, but harmless: the Mode sections are still
    found. Phase 1 must keep ignoring it rather than trying to clean it up."""
    for name in ['pillbox', 'pillbox-rtop+coax']:
        tree = _omega3p_tree(name)
        first_key = tree.entries[0][0]
        assert 'KVC syntax' in first_key      # banner swallowed into the key
        assert tree.children('Mode')          # ...and Mode is still reachable


def test_omega3p_top_level_order_differs_between_runs():
    """Why Phase 1 must search sections by name, never by position."""
    order = {n: [k for k, _ in _omega3p_tree(n).entries]
             for n in ['pillbox', 'pillbox-rtop+coax']}
    assert order['pillbox'].index('Mode') == 5
    assert order['pillbox-rtop+coax'].index('Mode') == 1
    assert order['pillbox'] != order['pillbox-rtop+coax']


# --------------------------------------------------------------------------- #
# S3P output -- the Phase 5 starting point
# --------------------------------------------------------------------------- #


def _s3p(tmp_path, results_dir='s3p_results', files=None, override=None,
         input_text=''):
    """Parse the 90DegreeBend S-parameters from `results_dir` under tmp_path.

    `override` is the wrapper's ``results_dir`` argument (the module-level
    ``results_dir:`` YAML key); `input_text` is the ``.s3p`` body, so a
    ``JobName`` leaf can be planted in it."""
    workdir = str(tmp_path)
    target = os.path.join(workdir, results_dir)
    os.makedirs(target, exist_ok=True)
    src = os.path.join(SOLVER_OUT, 's3p_90DegreeBend')
    for name in (files if files is not None else os.listdir(src)):
        shutil.copy(os.path.join(src, name), target)
    input_path = os.path.join(workdir, 'dummy.s3p')
    with open(input_path, 'w') as f:
        f.write(input_text)
    s3p = S3P(input_path, workdir=workdir, results_dir=override)
    s3p.output_parser()
    return s3p


def test_s3p_magnitudes_from_real_output(tmp_path):
    """Phase 5 adds complex data under new keys; these magnitudes must not
    move -- the baselines depend on them."""
    s3p = _s3p(tmp_path)

    assert len(s3p.output_data['Frequency']) == 13
    assert s3p.output_data['Frequency'][0] == 9.424e+09
    assert s3p.output_data['Frequency'][-1] == 1.2424e+10
    assert s3p.output_data['S(0,0)'][0] == 0.0323077414
    assert s3p.output_data['S(0,0)'][-1] == 0.162381324
    assert s3p.output_data['S(0,2)'][0] == 0.999477918
    assert s3p.output_data['S(3,3)'][-1] == 0.999897413
    assert s3p.output_data['IndexMap']['0'] == {
        'Port': '7', 'Mode': '0', 'Type': 'TE', 'Cutoff': 6557190000.0}
    assert s3p.output_data['IndexMap']['3'] == {
        'Port': '8', 'Mode': '1', 'Type': 'TE', 'Cutoff': 13115500000.0}


def test_s3p_ignores_sparameter_and_portref_files(tmp_path):
    """Only Reflection.out is read. SParameter.out (the same matrix with phase)
    and PortRef*.out (port mode profiles) sit unread next to it -- Phase 5."""
    s3p = _s3p(tmp_path)
    expected = {'IndexMap', 'Frequency'} | {
        'S({},{})'.format(i, j) for i in range(4) for j in range(4)}
    assert set(s3p.output_data) == expected


def test_s3p_phase_is_recoverable_from_the_unread_file():
    """Motivation for Phase 5, read straight off the fixture: SParameter.out
    holds (real, imag) whose magnitude matches Reflection.out. Nothing in src/
    reads this yet."""
    src = os.path.join(SOLVER_OUT, 's3p_90DegreeBend')
    with open(os.path.join(src, 'SParameter.out')) as f:
        row = [ln for ln in f if not ln.startswith('#')][0]
    assert row.split()[0] == '9.42400000e+09'
    real, imag = 8.74038681e-03, 3.11029869e-02      # S(0,0) at that frequency
    assert abs(complex(real, imag)) == pytest.approx(0.0323077414, rel=1e-8)


def test_s3p_default_results_dir_is_s3p_results(tmp_path):
    """``s3p_results`` is the authoritative default — the S3P reference documents
    no ``JobName`` container and no shipped example sets one — so results written
    anywhere else are not found unless something declares where."""
    with pytest.raises(FileNotFoundError):
        _s3p(tmp_path, results_dir='custom_results')


def test_s3p_results_dir_override_is_honored(tmp_path):
    """Phase 2: ``output_parser`` no longer hardcodes ``s3p_results/``. The
    supported override is the module-level ``results_dir:`` key, because the
    directory is really chosen by the batch job submission script's job name,
    outside the input file."""
    s3p = _s3p(tmp_path, results_dir='custom_results', override='custom_results')
    assert s3p.results_dir() == 'custom_results'
    assert s3p.output_data['S(0,0)'][0] == 0.0323077414


def test_s3p_input_tree_jobname_is_a_fallback_only(tmp_path):
    """A ``JobName`` leaf in the ``.s3p`` file is consulted, for symmetry with
    ``T3P``. **This is unverified against a real run**: the key is undocumented
    for S3P, so if the solver ignores it a real run writes to ``s3p_results``
    while this looks in ``custom_results``. It costs nothing and may be real —
    the assertion here is about our resolution order, not solver behavior."""
    s3p = _s3p(tmp_path, results_dir='custom_results',
               input_text='JobName : custom_results\n')
    assert s3p.results_dir() == 'custom_results'
    assert s3p.output_data['S(0,0)'][0] == 0.0323077414

    # The explicit override wins over the input-tree leaf.
    s3p = _s3p(tmp_path, results_dir='custom_results', override='custom_results',
               input_text='JobName : ignored_results\n')
    assert s3p.results_dir() == 'custom_results'


# --------------------------------------------------------------------------- #
# Phase-0-addendum fixtures -- the positional commands' own outputs
# --------------------------------------------------------------------------- #


def test_transwake_output_is_read_by_parse_wakefield():
    """DEFECT 7: ``transwake`` writes its result *over*
    ``<jobname>/OUTPUT/wakefield.out``, and ``parse_wakefield`` already reads the
    transverse header form. So ``AcdtoolModule`` parses nothing for transwake --
    ``T3PModule`` owns the result, before and after acdtool runs.

    The ordering hazard that follows from this is tested at the module level
    (``test_modules.py::test_transwake_reparses_the_producer``)."""
    data = parse_wakefield(os.path.join(T3P_OUT, 'cavity-half.wakefield.out'))

    assert data['WakeType'] == 'transverse'
    assert data['KickFactor'] == pytest.approx(9.64058337896157e-02)
    assert data['Offset'] == pytest.approx(1.25e-02)
    assert data['TransversePoints'] == [(0.0, 0.0), (0.0, 1.25e-02)]
    assert len(data['s']) == 20            # truncated; see SOURCES.md
    assert 'LossFactor' not in data


def test_transwake_output_path_matches_the_reference():
    """The reference: "The output file is stored as
    <jobname>/OUTPUT/wakefield.out" -- the same path T3P's own wake monitor
    writes, which is why the overwrite is by design and not a collision."""
    for command in ['postprocess transwake', 'postprocess wake_new',
                    'postprocess wake_direct']:
        spec = COMMANDS[command]
        assert spec.resolve_output('t3p_results') == \
            't3p_results/OUTPUT/wakefield.out'
        assert spec.mutates == 'td_solution'


def test_coaxsignal_output_is_headerless():
    """``signal.out`` is three columns ``t V I`` with **no** header row at all, so
    Phase 3's header-driven curve reader cannot handle it -- the column names come
    from the reference. Frozen here so Phase 3 does not have to rediscover it."""
    with open(os.path.join(T3P_OUT, 'BPM.signal.out')) as f:
        lines = f.read().splitlines()

    assert not any(ln.startswith('#') for ln in lines)
    assert {len(ln.split()) for ln in lines} == {3}
    assert len(lines) == 20                # truncated; see SOURCES.md
    # Unlike wakefield.out, this is a NEW file, so no ordering hazard.
    assert COMMANDS['postprocess coaxsignal'].mutates is None
    assert COMMANDS['postprocess coaxsignal'].resolve_output('t3p_results') == \
        't3p_results/OUTPUT/signal.out'


def test_enhancement_counter_output_has_a_header_row():
    """``EnhancementCounter``'s output goes through a normal header-driven reader
    (7 columns), unlike ``signal.out``. The reference says it is "dumped under
    ./jobname/", which is where the command table points."""
    with open(os.path.join(TRACK3P_OUT, 'Pillbox-2.3MV.en')) as f:
        lines = f.read().splitlines()

    assert lines[0].split() == [
        'fieldlevel', 'ID', 'enhancement', 'averageEnhancement',
        'maxEnhancement', 'maxEnhancementImpactNum', 'totalImpactNum']
    assert {len(ln.split()) for ln in lines[1:]} == {7}
    assert COMMANDS['postprocess track3p'].resolve_output('2.3MV') == '2.3MV/en'


def test_signal_out_is_read_with_the_reference_columns(tmp_path, monkeypatch):
    """Phase 3: ``coaxsignal``'s headerless output gets its own reader, with the
    column names supplied from the reference rather than read from the file.

    Driven end to end through ``run()`` so the reader dispatch is covered too:
    the command table routes ``postprocess coaxsignal`` to the signal reader and
    ``postprocess rf`` to the sectioned one.
    """
    _capture_run(monkeypatch)
    results = os.path.join(str(tmp_path), 't3p_results', 'OUTPUT')
    os.makedirs(results)
    shutil.copy(os.path.join(T3P_OUT, 'BPM.signal.out'),
                os.path.join(results, 'signal.out'))

    acd = Acdtool(None, workdir=str(tmp_path),
                  acdtool_command='postprocess coaxsignal')
    acd.run()

    signal = acd.output_data['signal']
    assert list(signal) == list(SIGNAL_COLUMNS) == ['t', 'V', 'I']
    assert len(signal['t']) == 20               # truncated; see SOURCES.md
    assert signal['t'][0] == pytest.approx(5.0e-13)
    assert signal['I'][0] == pytest.approx(1.035103870000000e-07)
    # A curve, so it rides as a field artifact rather than a table column.
    assert sorted(field_sections(acd.output_data)) == ['signal']


def test_enhancement_counter_output_is_read_by_the_curve_reader():
    """``en``'s header row is *not* ``#``-commented, so the header-driven reader
    takes any non-numeric line before the first data row as column names. The
    command itself stays unwired (it needs the ``:`` dialect), but its output
    format costs nothing to support once the reader is header-driven."""
    data = parse_column_file(os.path.join(TRACK3P_OUT, 'Pillbox-2.3MV.en'))

    assert list(data) == [
        'fieldlevel', 'ID', 'enhancement', 'averageEnhancement',
        'maxEnhancement', 'maxEnhancementImpactNum', 'totalImpactNum']
    assert len(data['ID']) == 20                # truncated; see SOURCES.md
    assert data['fieldlevel'][0] == pytest.approx(2.3e+07)
    assert data['ID'][0] == 200


def test_postprocess_in_confirms_jobname_resolution():
    """The KVC echo T3P writes of its own resolved input carries
    ``JobName : t3p_results`` even though the ``.t3p`` file never set one -- which
    is the one piece of evidence that the solver has an internal JobName the
    per-solver default supplies. It is inference, not documentation, which is why
    the input-tree lookup stays a fallback and ``results_dir:`` is the mechanism.
    """
    with open(os.path.join(T3P_OUT, 'cavity-half.postprocess.in')) as f:
        tree = parse_ace3p(f.read())

    assert tree.get_leaf('JobName') == 't3p_results'
    # And it is the same directory the tutorial passed to transwake by hand:
    #   acdtool postprocess transwake t3p_results 0. 0.00 0. 0.0125
    assert COMMANDS['postprocess transwake'].default_jobname == 't3p_results'
