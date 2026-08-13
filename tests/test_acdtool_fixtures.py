"""Characterization tests over the real CW23 fixtures (see
`docs/acdtool_rework_plan.md`, Phase 0).

**These tests assert what the code does TODAY, wrong answers included.** They
are regression anchors captured *before* the parser rework, not statements about
what correct behavior is. Four confirmed defects are pinned here deliberately;
each is marked ``DEFECT n`` and names the phase that inverts it:

* **DEFECT 1** — multi-line ``{ ... }`` values are silently dropped
  (:func:`test_defect1_multiline_brace_value_is_truncated`, inverted in Phase 2).
* **DEFECT 2** — ``.acdtool`` files use the ``:`` dialect and parse to nothing
  (:func:`test_defect2_acdtool_dialect_parses_to_empty_blocks`, Phase 2).
* **DEFECT 3** — the S3P ``[scaling]`` block ships unclosed, so ``}``-based end
  detection is unreliable (:func:`test_defect3_window_scaling_block_is_unclosed`,
  Phase 3).
* **DEFECT 4** — ``--nodes=1 --ntasks=1`` is hardcoded and srun-only
  (:func:`test_defect4_run_hardcodes_srun_only_flags`, Phase 2).

A test that pins a *correct* current behavior is the other kind: those must keep
passing unchanged through the rework. The ``[RoverQ]`` numbers in
:func:`test_roverq_values_from_real_output` are the load-bearing ones — Phase 3
refactors a working parser, so those values must not move.

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

from lume_ace3p.acdtool import Acdtool
from lume_ace3p.ace3p import S3P, parse_ace3p

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'acdtool')

RFPOST_IN = os.path.join(FIXTURES, 'rfpost_inputs')
RFPOST_OUT = os.path.join(FIXTURES, 'rfpost_outputs')
CURVES = os.path.join(FIXTURES, 'curves')
ACDTOOL_IN = os.path.join(FIXTURES, 'acdtool_inputs')
SOLVER_OUT = os.path.join(FIXTURES, 'solver_outputs')

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


def test_defect1_multiline_brace_value_is_truncated(tmp_path):
    """DEFECT 1 (inverted in Phase 2): filled multi-line list values are lost.

    ``portID`` keeps only the opening brace, ``porta``/``portb`` vanish outright,
    and nothing is raised or warned. The section *count* is unaffected, so there
    is no signal at all that data was dropped.
    """
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'coaxport-multiline.rfpost'))

    assert acd.input_data['coaxPort']['portID'] == '{'   # contents discarded
    assert 'porta' not in acd.input_data['coaxPort']     # swallowed entirely
    assert 'portb' not in acd.input_data['coaxPort']
    # Values that never crossed a brace are fine.
    assert acd.input_data['coaxPort']['ionoff'] == '1'
    assert acd.input_data['coaxPort']['modeID2'] == '-1'
    # The stray '}' ends the block early, yet the next section still opens, so
    # the loss is invisible from the section list.
    assert list(acd.input_data) == ['coaxPort', 'maxFieldsOnSurface']
    assert acd.input_data['maxFieldsOnSurface'] == {'ionoff': '1',
                                                    'surfaceID': '6'}


def test_defect1_roundtrip_writes_unbalanced_braces(tmp_path):
    """DEFECT 1, second half (Phase 2): the truncated value is written back
    verbatim, so ``write_input`` emits a structurally invalid file."""
    acd = _acdtool(tmp_path, os.path.join(RFPOST_IN, 'coaxport-multiline.rfpost'))
    acd.write_input('roundtrip.rfpost')
    with open(os.path.join(tmp_path, acd.input_file)) as f:
        text = f.read()
    assert 'portID = {\n' in text
    assert 'porta' not in text
    assert text.count('{') == text.count('}') + 1   # unbalanced


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
    """The load-bearing anchor: ``[RoverQ]`` as parsed from real output today.

    Phase 3 replaces the hand-indexed ``modeline[3]`` access with a
    header-driven reader; it is a refactor of working code, so every number here
    must come out identical.
    """
    acd = _acdtool(tmp_path,
                   os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'),
                   os.path.join(RFPOST_OUT, name + '.rfpost.out'))

    expected = ROVERQ_EXPECTED[name]
    assert set(acd.output_data) == {'RoverQ'}
    assert acd.output_data['RoverQ']['ModeIDs'] == sorted(expected)
    for mode_id, columns in expected.items():
        assert acd.output_data['RoverQ'][mode_id] == columns


def test_scaling_block_is_never_parsed(tmp_path):
    """``[scaling]`` is emitted by every run but declared by no input block, so
    the ionoff-driven ``load_output`` never looks at it -- and with it goes
    ``m_factor``, the only normalized-to-physical field conversion acdtool
    reports. Phase 3 adds this."""
    for name in ROVERQ_EXPECTED:
        path = os.path.join(RFPOST_OUT, name + '.rfpost.out')
        with open(path) as f:
            assert '[scaling]' in f.read()

    acd = _acdtool(tmp_path,
                   os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'),
                   os.path.join(RFPOST_OUT, 'pillbox-rtop+coax.rfpost.out'))
    assert 'scaling' not in acd.output_data


def test_curve_block_output_is_not_parsed(tmp_path, capsys):
    """The window run's only enabled block is ``ALLFieldOnLine``, whose output
    goes to separate ``field1_*`` files. No ``[ALLFieldOnLine]`` section exists
    in rfpost.out, so the parser reports 'not found' and yields nothing -- the
    curve files on disk are simply never read. Phase 3 adds the reader."""
    acd = _acdtool(tmp_path,
                   os.path.join(RFPOST_IN, 'window.rfpost'),
                   os.path.join(RFPOST_OUT, 'window.rfpost.out'))

    assert acd.output_data == {}
    assert 'ALLFieldOnLine' in capsys.readouterr().out


def test_unimplemented_section_reports_and_yields_nothing(tmp_path):
    """A section flagged on but with no parser is reported to stdout and left
    absent from ``output_data`` -- there is no exception and no warning. Phase 3
    requires this become a raise or a warning naming the section."""
    text = 'powerThroughSurface\n{\n   ionoff = 1\n}\n'
    input_path = os.path.join(tmp_path, 'unimplemented.rfpost')
    with open(input_path, 'w') as f:
        f.write(text)
    shutil.copy(os.path.join(RFPOST_OUT, 'pillbox-rtop+coax.rfpost.out'),
                os.path.join(tmp_path, 'rfpost.out'))

    acd = Acdtool(input_path, workdir=str(tmp_path))
    acd.output_file = 'rfpost.out'
    acd.load_output()
    assert acd.output_data == {}


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


def test_defect4_run_hardcodes_srun_only_flags(tmp_path, monkeypatch):
    """DEFECT 4 (fixed in Phase 2): ``--nodes=1 --ntasks=1`` are srun flags,
    emitted regardless of what MPI_CALLER is. ``ace3p.py`` guards ``--cpu-bind``
    against non-srun callers; there is no equivalent here, so a non-srun caller
    gets an unparseable command line."""
    commands = _capture_run(monkeypatch)
    workdir = str(tmp_path)
    input_path = os.path.join(workdir, 'pillbox-rtop+coax.rfpost')
    shutil.copy(os.path.join(RFPOST_IN, 'pillbox-rtop+coax.rfpost'), input_path)
    acd = Acdtool(input_path, workdir=workdir,
                  ace3p_path='/ace3p/', mpi_caller='mpirun')
    with open(os.path.join(workdir, 'rfpost.out'), 'w'):
        pass
    acd.run()

    assert commands[0].startswith('mpirun --nodes=1 --ntasks=1 /ace3p/acdtool ')


def test_run_rejects_unknown_extension(tmp_path, monkeypatch):
    """A ``.acdtool`` input -- a real, supported acdtool command -- cannot be run
    at all: no subprocess is launched and ``output_file`` is never even set, so a
    later ``load_output`` raises AttributeError. Phase 2 adds the dispatch."""
    commands = _capture_run(monkeypatch)
    acd = _acdtool(tmp_path, os.path.join(ACDTOOL_IN, 'Pillbox.acdtool'))

    assert acd.run() is None
    assert commands == []
    assert not hasattr(acd, 'output_file')


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


def _s3p(tmp_path, results_dir='s3p_results', files=None):
    """Parse the 90DegreeBend S-parameters from `results_dir` under tmp_path."""
    workdir = str(tmp_path)
    target = os.path.join(workdir, results_dir)
    os.makedirs(target, exist_ok=True)
    src = os.path.join(SOLVER_OUT, 's3p_90DegreeBend')
    for name in (files if files is not None else os.listdir(src)):
        shutil.copy(os.path.join(src, name), target)
    input_path = os.path.join(workdir, 'dummy.s3p')
    with open(input_path, 'w'):
        pass
    s3p = S3P(input_path, workdir=workdir)
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


def test_s3p_hardcodes_results_dir(tmp_path):
    """``output_parser`` hardcodes ``s3p_results/`` while ``T3P.results_dir()``
    reads JobName from the input tree. A .s3p that sets JobName therefore
    raises. Phase 2 gives S3P a ``results_dir()``."""
    with pytest.raises(FileNotFoundError):
        _s3p(tmp_path, results_dir='custom_results')
