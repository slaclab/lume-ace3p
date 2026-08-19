"""Characterization tests over the real CW23 T3P monitor fixtures (see
`docs/t3p_monitor_plan.md`, Phase 0).

T3P writes six kinds of monitor output; before this plan LUME-ACE3P read one
(``WakeField``). These tests pin what the *files* look like — column counts,
first and last rows, header presence, the ``(Type, Name)`` pairs the input
declares — **before** any monitor table exists, so Phase 1 has ground truth to
build against rather than an assumed layout.

Everything here runs against files frozen from two CW23 runs:

* ``BPM`` — six monitors of five different types on one run (two ``Point``, one
  ``Power``, one ``ModeVoltage``, one ``Volume``, one ``WakeField``), which is
  the multi-type case;
* ``SIBC`` — three ``Power`` monitors and **no** ``WakeField``, which is both
  the multi-instance case and the case that proves ``Name`` rather than ``Type``
  has to be the selector.

Two Phase-0 claims are load-bearing for the whole plan and are asserted here:

1. **The series monitors are headerless**, so their column names come from
   ``references/t3p-commands.pdf`` and not from the file — which is exactly what
   ``parse_column_file(path, columns=...)`` already exists for. No new parsing
   code is needed, and :func:`test_point_monitor_is_read_by_the_existing_reader`
   demonstrates that with no monitor table in play at all.
2. **T3P normalizes keys**: ``BPM.t3p`` writes ``Start contour: -0.0055`` with a
   space and the ``t3p.out`` echo reports ``Startcontour``. The input file is
   what a workflow can validate before a run; the echo is what the run used.

Provenance and truncation for every file is in
``tests/fixtures/acdtool/SOURCES.md``; per-``Type`` coverage is in
``tests/fixtures/acdtool/COVERAGE.md``. No ACE3P binary is needed.
"""

import os

import numpy as np
import pytest

from lume_ace3p.ace3p import parse_ace3p, parse_column_file

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'acdtool')
T3P_OUT = os.path.join(FIXTURES, 't3p_outputs')

# The Phase-0 file list. The four Phase-2 files already in this directory
# (cavity-half.wakefield.out and friends) are inventoried by
# ``test_acdtool_fixtures.py::test_fixture_inventory``; these are the nine this
# plan adds.
PHASE0_FIXTURES = [
    'BPM.point.out', 'BPM.port.out', 'BPM.modecoeff.out', 'BPM.Bunch0.out',
    'SIBC.inputPower.out', 'SIBC.wallossPower.out',
    'BPM.t3p', 'SIBC.t3p', 'BPM.t3p.out',
]

# The 7 columns a Point monitor writes, per the reference: "the output is the
# format (t Hx Hy Hz Ex Ey Ez) in SI". Spelled out here rather than imported so
# this Phase-0 test stands on the *reference* plus the file, independently of
# ``ace3p.POINT_COLUMNS``; Phase 1 asserts the two agree
# (``test_monitor_table_column_names_match_the_reference``).
REFERENCE_POINT_COLUMNS = ('t', 'Hx', 'Hy', 'Hz', 'Ex', 'Ey', 'Ez')

# One row per series fixture: (columns, data rows, first row, last row).
# Truncation is recorded in SOURCES.md -- a short file here is a deliberate copy
# of the original's first rows, not a parser artifact.
SERIES_SHAPES = {
    'BPM.point.out': (
        7, 20,
        (5.000000000e-13, 1.099514507e-31, 2.567913389e-31, -1.171526205e-31,
         2.533940240e-28, -1.697614252e-28, -1.087379539e-28),
        (1.000000000e-11, -1.246777407e-25, -5.296409425e-26, 1.381850692e-25,
         3.646628684e-23, -1.026576424e-22, -1.681890720e-23),
    ),
    'BPM.port.out': (
        2, 20,
        (5.000000000000e-13, -1.569750240406e-150),
        (1.000000000000e-11, 8.352398137202e-99),
    ),
    'BPM.modecoeff.out': (
        2, 20,
        (5.000000000000e-13, 5.530888972044e-77),
        (1.000000000000e-11, -3.449835387040e-50),
    ),
    'BPM.Bunch0.out': (
        2, 20,
        (5.00000000e-13, 1.03510387e-07),
        (1.00000000e-11, 1.49278482e-06),
    ),
    'SIBC.inputPower.out': (
        2, 20,
        (1.000000000000e-11, -4.664513771111e-08),
        (2.000000000000e-10, -1.721342128824e-04),
    ),
    'SIBC.wallossPower.out': (
        2, 20,
        (1.000000000000e-11, 0.0),
        (2.000000000000e-10, 0.0),
    ),
}

# The six monitors BPM declares, in file order: five types on one run.
BPM_MONITORS = [
    ('WakeField', 'wakefield'),
    ('Point', 'point'),
    ('Point', 'coaxpoint'),
    ('Volume', 'volume'),
    ('Power', 'port'),
    ('ModeVoltage', 'modecoeff'),
]

# SIBC: one Volume plus three Power monitors, and no WakeField at all. Three
# instances of one Type is why 'Name' has to be the selector.
SIBC_MONITORS = [
    ('Volume', 'field'),
    ('Power', 'inputPower'),
    ('Power', 'outputPower'),
    ('Power', 'wallossPower'),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _lines(name):
    with open(os.path.join(T3P_OUT, name)) as f:
        return f.read().splitlines()


def _data_rows(name):
    """Numeric rows of a monitor file (everything not a '#'-commented line)."""
    return [line.split() for line in _lines(name)
            if line.strip() and not line.lstrip().startswith('#')]


def _monitor_pairs(tree):
    """``[(Type, Name)]`` for every ``Monitor`` section of a parsed tree, in
    file order — the pairing Phase 1's ``T3P.monitors()`` has to reproduce."""
    return [(section.get_leaf('Type'), section.get_leaf('Name'))
            for section in tree.children('Monitor')]


# --------------------------------------------------------------------------- #
# Fixture inventory -- a missing fixture must fail loudly, not skip silently
# --------------------------------------------------------------------------- #


def test_phase0_fixture_inventory():
    missing = [name for name in PHASE0_FIXTURES
               if not os.path.isfile(os.path.join(T3P_OUT, name))]
    assert missing == []


# --------------------------------------------------------------------------- #
# Confirmed fact 1 -- the series monitors are headerless
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('name', sorted(
    n for n in SERIES_SHAPES if n != 'BPM.Bunch0.out'))
def test_series_monitor_output_has_no_header_of_any_kind(name):
    """``point.out``, ``port.out``, ``modecoeff.out`` and the SIBC power files
    carry no header line at all — not commented, not uncommented. So their
    column names can only come from ``references/t3p-commands.pdf``, which is
    what ``parse_column_file(path, columns=...)`` is for.

    ``Bunch0.out`` is the one exception and is asserted separately."""
    lines = _lines(name)
    assert lines                                   # not an empty fixture
    assert not any(line.lstrip().startswith(('#', '%')) for line in lines)
    # Every line parses as floats, i.e. no line is a name row.
    for line in lines:
        [float(token) for token in line.split()]


@pytest.mark.parametrize('name,shape', sorted(SERIES_SHAPES.items()))
def test_series_fixture_shapes_and_endpoints(name, shape):
    """Column count and the first/last data row of every series fixture, pinned
    against the frozen bytes. The truncation is recorded in SOURCES.md."""
    columns, rows, first, last = shape
    data = _data_rows(name)
    assert len(data) == rows
    assert {len(row) for row in data} == {columns}
    assert [float(token) for token in data[0]] == pytest.approx(first, rel=0,
                                                               abs=0)
    assert [float(token) for token in data[-1]] == pytest.approx(last, rel=0,
                                                                abs=0)


def test_bunch0_is_the_one_monitor_file_with_a_header():
    """``Bunch0.out`` is emitted by every run and declared by no monitor — the
    structural twin of acdtool's ``[scaling]``. It is also the only T3P monitor
    file that names its columns, in a ``##`` comment.

    But it names them **with units** (``t[sec]``, ``I[A]``), and its *other*
    comment line (``## Bunch distribution``) happens to have the same two-token
    width the data rows do. Both are why Phase 1 supplies this file's column
    names explicitly rather than letting the header-driven reader guess — see
    ``test_bunch0_header_names_carry_units``."""
    lines = _lines('BPM.Bunch0.out')
    comments = [line for line in lines if line.startswith('#')]
    assert comments == ['## Bunch distribution', '## t[sec]    I[A]']


def test_bunch0_header_names_carry_units():
    """The header-driven reader picks the *last* comment line whose token count
    matches the data width — here ``t[sec] I[A]``. Correct as a reading of the
    file, and wrong as a table column name: the module layer's index axis is
    ``t``. Pinned so the reason Phase 1 passes ``columns=('t', 'I')`` is on the
    record rather than looking like an oversight."""
    inferred = parse_column_file(os.path.join(T3P_OUT, 'BPM.Bunch0.out'))
    assert list(inferred) == ['t[sec]', 'I[A]']
    # Both comment lines are two tokens wide, so 'last match wins' is the only
    # thing keeping 'Bunch distribution' from becoming the column names.
    assert len('## Bunch distribution'.lstrip('#').split()) == 2


def test_point_monitor_is_read_by_the_existing_reader():
    """**No new parsing code is needed.** The reader that already handles
    ``coaxsignal``'s headerless ``signal.out`` reads a ``Point`` monitor too,
    given the reference's column names — asserted here with no monitor table
    in play, which is what makes Phase 1 a table plus a loop rather than a
    parser."""
    data = parse_column_file(os.path.join(T3P_OUT, 'BPM.point.out'),
                             columns=REFERENCE_POINT_COLUMNS)

    assert list(data) == list(REFERENCE_POINT_COLUMNS)
    assert len(data) == 7
    assert {len(values) for values in data.values()} == {20}
    assert data['t'][0] == pytest.approx(5.0e-13)
    assert data['Ez'][0] == pytest.approx(-1.087379539e-28)
    # The time grid is TimeStepping's DT (0.5e-12), one sample per step.
    assert np.allclose(np.diff(data['t']), 0.5e-12)


def test_power_and_modevoltage_share_the_series_shape():
    """``Power`` [W] and ``ModeVoltage`` [V] differ only in what the second
    column means, so one shape covers both and the table supplies the name."""
    power = parse_column_file(os.path.join(T3P_OUT, 'BPM.port.out'),
                              columns=('t', 'P'))
    voltage = parse_column_file(os.path.join(T3P_OUT, 'BPM.modecoeff.out'),
                                columns=('t', 'V'))

    assert list(power) == ['t', 'P']
    assert list(voltage) == ['t', 'V']
    assert np.allclose(power['t'], voltage['t'])          # same time grid
    assert power['P'][-1] == pytest.approx(8.352398137202e-99)
    assert voltage['V'][-1] == pytest.approx(-3.449835387040e-50)


def test_sibc_power_monitors_share_a_time_grid_but_not_values():
    """The multi-instance case: three ``Power`` monitors on one run, indexed by
    the same ``t``. This is the workflow the package could not express — and the
    reason ``Type`` alone cannot address a monitor."""
    files = {'inputPower': 'SIBC.inputPower.out',
             'wallossPower': 'SIBC.wallossPower.out'}
    read = {name: parse_column_file(os.path.join(T3P_OUT, path),
                                    columns=('t', 'P'))
            for name, path in files.items()}

    assert np.allclose(read['inputPower']['t'], read['wallossPower']['t'])
    # SIBC's DT is 10 ps, ten times BPM's.
    assert np.allclose(np.diff(read['inputPower']['t']), 1.0e-11)
    # Power flows in before any wall loss appears, so the columns differ.
    assert not np.allclose(read['inputPower']['P'], read['wallossPower']['P'])
    assert np.all(read['wallossPower']['P'] == 0.0)       # still zero at 200 ps


# --------------------------------------------------------------------------- #
# The input files -- the monitor list, and the normalized-key hazard
# --------------------------------------------------------------------------- #


def test_bpm_input_declares_six_monitors_of_five_types():
    """``parse_ace3p`` already reads the monitor list out of a ``.t3p`` file, so
    Phase 1 walks ``Monitor`` sections rather than adding a parser. Five of the
    six documented types on one run."""
    with open(os.path.join(T3P_OUT, 'BPM.t3p')) as f:
        tree = parse_ace3p(f.read())

    assert _monitor_pairs(tree) == BPM_MONITORS
    assert len({monitor_type for monitor_type, _ in BPM_MONITORS}) == 5
    # Names are unique here, which is what makes them usable as a selector...
    assert len({name for _, name in BPM_MONITORS}) == 6


def test_bpm_wakefield_monitor_keeps_the_space_in_its_key():
    """``BPM.t3p`` writes ``Start contour: -0.0055`` **with a space**, where the
    reference spells it ``StartContour`` and the ``t3p.out`` echo reports
    ``Startcontour``. The input parser keeps the key verbatim; nothing in the
    monitor table may key off a contour name."""
    with open(os.path.join(T3P_OUT, 'BPM.t3p')) as f:
        tree = parse_ace3p(f.read())
    wake = tree.find('Monitor', Type='WakeField')

    assert wake.get_leaf('Start contour') == '-0.0055'
    assert wake.get_leaf('End contour') == '0.0055'
    assert wake.get_leaf('StartContour') is None          # not normalized here
    assert wake.get_leaf('Smax') == '0.35'


def test_bpm_modevoltage_monitor_carries_a_nested_port_block():
    """A ``ModeVoltage`` monitor nests ``Port: { ESolver: { ... } }``. Its output
    is still two columns, so the nesting matters only in that a monitor's own
    entries are not all leaves."""
    with open(os.path.join(T3P_OUT, 'BPM.t3p')) as f:
        tree = parse_ace3p(f.read())
    mode_voltage = tree.find('Monitor', Type='ModeVoltage')
    port = mode_voltage.find('Port')

    assert port.get_leaf('ReferenceNumber') == '5'
    assert port.find('ESolver').get_leaf('NumberOfModes') == '1'


def test_sibc_input_declares_three_power_monitors_and_no_wakefield():
    """Confirmed fact 6: a run may have no ``WakeField`` monitor at all. SIBC has
    none, and three ``Power`` monitors distinguished only by ``Name`` and
    ``ReferenceNumber`` — which is what forces the ``s``-or-``t`` axis rule."""
    with open(os.path.join(T3P_OUT, 'SIBC.t3p')) as f:
        tree = parse_ace3p(f.read())

    assert _monitor_pairs(tree) == SIBC_MONITORS
    assert tree.find('Monitor', Type='WakeField') is None
    powers = [section for section in tree.children('Monitor')
              if section.get_leaf('Type') == 'Power']
    assert [section.get_leaf('ReferenceNumber') for section in powers] == \
        ['4', '5', '3']
    # find() returns the FIRST match, so it cannot address the other two.
    assert tree.find('Monitor', Type='Power').get_leaf('Name') == 'inputPower'


# --------------------------------------------------------------------------- #
# t3p.out -- the resolved-input echo
# --------------------------------------------------------------------------- #


def test_t3p_out_echo_is_readable_by_parse_ace3p():
    """``t3p.out``'s ``Input :`` section is a normalized KVC echo of the whole
    input, monitors included, so ``parse_ace3p`` reads it with no changes. This
    is the cross-check on what the run actually used; the input file remains the
    primary source, because no echo exists under dry-run."""
    with open(os.path.join(T3P_OUT, 'BPM.t3p.out')) as f:
        tree = parse_ace3p(f.read())
    echo = tree.find('Input')

    assert echo is not None
    assert _monitor_pairs(echo) == BPM_MONITORS
    # The solver resolves its own results directory into the echo, which is the
    # evidence behind the job-name fallback (see SOURCES.md).
    assert echo.get_leaf('JobName') == 't3p_results'


def test_t3p_out_echo_normalizes_keys_the_input_wrote_with_spaces():
    """The disagreement worth recording: ``Start contour`` in the input file is
    ``Startcontour`` in the echo. Both are the same monitor, so a cross-check
    between the two may compare only ``(Type, Name)``."""
    with open(os.path.join(T3P_OUT, 'BPM.t3p.out')) as f:
        echo = parse_ace3p(f.read()).find('Input')
    wake = echo.find('Monitor', Type='WakeField')

    assert wake.get_leaf('Startcontour') == '-0.0055'
    assert wake.get_leaf('Start contour') is None
    assert wake.get_leaf('Endcontour') == '0.0055'


def test_t3p_out_banner_does_not_break_the_echo():
    """Same hazard as ``omega3p.out``: the leading ``/* ... */`` block comment
    and the trailing license banner are absorbed into key names, because
    ``_tokenize`` strips only ``//`` comments. Garbage, and harmless — ``Input``
    is still reachable, which is all Phase 1 needs. Do not 'clean it up'."""
    with open(os.path.join(T3P_OUT, 'BPM.t3p.out')) as f:
        tree = parse_ace3p(f.read())

    assert 'KVC syntax' in tree.entries[0][0]      # banner swallowed into a key
    assert tree.find('Input') is not None
