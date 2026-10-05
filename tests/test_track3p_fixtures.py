"""Characterization tests over the real Track3P fixtures (see
`plans/track3p_module_plan.md`, Phase 0).

These pin what Track3P's *files* look like — the two 17-column ``ImpactsInfo``
layouts, the postprocess tables, the log's terminator and scalar lines, the
input containers Lixin Ge's LCLS-II inputs use — before a ``track3p`` module
exists, so Phase 1 builds against ground truth rather than an assumed layout.

Everything runs against files frozen from real runs: the CW23 Pillbox probe runs
on S3DF (group binary, 2026-09-14) and Lixin Ge's cavity-3 dump. Provenance,
truncation and row selection for every file is in
``tests/fixtures/track3p/SOURCES.md``. No ACE3P binary is needed.

Three Phase-0 claims are load-bearing for the plan and are asserted here:

1. **There are two 17-column dumps and they are different files.** The default
   ``OutputImpacts: on`` layout has a bare header and ``NumElectrons`` /
   ``FaceID`` / ``volID``; the ``OutputImpactsInfo: { Type: Initials-Impacts }``
   layout has a ``#`` header and ``InitialNormalField`` / ``InitialFaceArea``.
   ``particles.Particles.load`` reads only the second — on the first it eats the
   header as a data row — which is why the format is opt-in on the module.
2. **The postprocess tables are read by the existing header-driven reader**
   (:func:`parse_column_file`), bare header and all. No new parser is needed for
   ``enhancementCounter`` or ``resonantparticles``.
3. **The log ends ``Done!`` but not on its last line**: a field-emission run
   appends ``Total Emitted Particles = N`` after it, and Lixin's build reports
   ``Survived particles`` before it. Phase 1's ``verify`` must look for the
   terminator anywhere, not on the final line.

Phase 0 found and Phase 1 fixed one parser defect: :func:`parse_ace3p` could
not round-trip the one-line block style Lixin's generated inputs use
(``FieldScales: { Type: FieldGradient  ScanToken: 1 ... }``), because the
tokenizer read a value to end of line and so swallowed the sibling keys and the
closing brace. The round-trip tests below were strict xfails in Phase 0.
"""

import os
import shutil

import numpy as np
import pandas as pd
import pytest

from lume_ace3p.ace3p import parse_ace3p, parse_column_file, write_ace3p, Section
from lume_ace3p.particles import (
    Particles, Q_E, TRACK3P_COLUMNS, fowler_nordheim_current_density,
)

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'track3p')
INPUTS = os.path.join(FIXTURES, 'inputs')
SCAN = os.path.join(FIXTURES, 'pillbox_scan')
INITIALS = os.path.join(FIXTURES, 'pillbox_initials_impacts')
FIELDEM = os.path.join(FIXTURES, 'pillbox_fieldemission')
FIELDEM_N1 = os.path.join(FIXTURES, 'pillbox_fieldemission_n1')
LCLS = os.path.join(FIXTURES, 'lcls_c3_16MV')

# The 30 files SOURCES.md inventories, relative to the fixture root.
PHASE0_FIXTURES = [
    'inputs/Pillbox.track3p', 'inputs/Pillbox2.3MV.track3p',
    'inputs/Pillbox-w4_type7_model2_fcup.track3p',
    'inputs/Pillbox-b1_initials_impacts.track3p', 'inputs/lcls_c3_16MV.track3p',
    'pillbox_scan/track3p.log', 'pillbox_scan/track3p.warn',
    'pillbox_scan/InputParameters', 'pillbox_scan/OUTPUT/enhancementCounter',
    'pillbox_scan/OUTPUT/resonantparticles', 'pillbox_scan/ImpactsInfo_2.3e+07',
    'pillbox_scan/LostParticles_2.3e+07',
    'pillbox_initials_impacts/ImpactsInfo_2.3e+07',
    'pillbox_initials_impacts/track3p.log',
    'pillbox_fieldemission/track3p.log', 'pillbox_fieldemission/InputParameters',
    'pillbox_fieldemission/ImpactsInfo_2.3e+07',
    'pillbox_fieldemission/OUTPUT/enhancementCounter',
    'pillbox_fieldemission/OUTPUT/resonantparticles',
    'pillbox_fieldemission/OUTPUT/faradaycup_1',
    'pillbox_fieldemission/OUTPUT/faradaycup_2',
    'pillbox_fieldemission/OUTPUT/faradaycup_6',
    'lcls_c3_16MV/ImpactsInfo_1.6e+07', 'lcls_c3_16MV/c3_16MV_beta120.data',
    'lcls_c3_16MV/track3p.log', 'lcls_c3_16MV/SurvivedParticles_1.6e+07',
    # Added by Phase 3 step 2 (the probe that finally emitted).
    'inputs/Pillbox-fieldemission-n1.track3p',
    'pillbox_fieldemission_n1/ImpactsInfo_2.3e+07',
    'pillbox_fieldemission_n1/track3p.log',
    'pillbox_fieldemission_n1/InputParameters',
]

# The default ("general") layout, written by `OutputImpacts: on` alone. Spelled
# out from the file rather than imported: nothing in src/ names it yet.
DEFAULT_COLUMNS = (
    'InitialID', 'ImpactNum', 'Initial_x', 'Initial_y', 'Initial_z',
    'Impact_x', 'Impact_y', 'Impact_z', 'InitialPhase', 'ImpactPhase',
    'ImpactEnergy', 'NumElectrons', 'momentum_x', 'momentum_y', 'momentum_z',
    'FaceID', 'volID',
)

ENHANCEMENT_COLUMNS = (
    'fieldlevel', 'ID', 'enhancement', 'averageEnhancement', 'maxEnhancement',
    'maxEnhancementImpactNum', 'totalImpactNum', 'FinalImpactLocationX',
    'FinalImpactLocationY', 'FinalImpactLocationZ',
)

RESONANT_COLUMNS = (
    'Field_Level', 'ID', 'Resonant_X', 'Resonant_Y', 'Resonant_Z', 'Energy',
    'Initial_X', 'Initial_Y', 'Initial_Z', 'Total_time', 'Total_Num',
)


def _read(path):
    with open(path) as file:
        return file.read()


def _lines(path):
    return _read(path).splitlines()


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #


def test_fixture_inventory():
    """Every file SOURCES.md lists is present and nothing else is (a stray
    file here would be an undocumented fixture)."""
    present = set()
    for root, _, files in os.walk(FIXTURES):
        for name in files:
            if name.endswith('.md'):
                continue
            present.add(os.path.relpath(os.path.join(root, name), FIXTURES))
    assert present == set(PHASE0_FIXTURES)


# --------------------------------------------------------------------------- #
# Claim 1: the two ImpactsInfo layouts
# --------------------------------------------------------------------------- #


def test_default_impacts_layout_has_a_bare_header():
    lines = _lines(os.path.join(SCAN, 'ImpactsInfo_2.3e+07'))
    header = lines[0].split()
    assert not lines[0].startswith('#')
    assert tuple(header) == DEFAULT_COLUMNS
    assert len(header) == 17
    # 200 data rows, each 17 wide (truncated copy, see SOURCES.md).
    rows = [line.split() for line in lines[1:]]
    assert len(rows) == 200
    assert {len(row) for row in rows} == {17}
    # First row: an emission point (ImpactNum 0, impact == initial, 2 eV).
    first = rows[0]
    assert first[:2] == ['100', '0']
    assert first[2:5] == first[5:8]
    assert float(first[10]) == 2.0
    assert float(first[11]) == 1.0          # NumElectrons


def test_initials_impacts_layout_has_a_commented_header():
    lines = _lines(os.path.join(INITIALS, 'ImpactsInfo_2.3e+07'))
    assert lines[0].startswith('#')
    header = lines[0].lstrip('#').split()
    assert header == TRACK3P_COLUMNS
    assert len(header) == 17
    rows = [line.split() for line in lines[1:]]
    assert len(rows) == 200
    assert {len(row) for row in rows} == {17}
    # Same run physics as the default-layout file: the first emission point is
    # the same particle at the same place ...
    default_first = _lines(os.path.join(SCAN, 'ImpactsInfo_2.3e+07'))[1].split()
    assert rows[0][0] == default_first[0] == '100'
    assert np.allclose([float(v) for v in rows[0][2:8]],
                       [float(v) for v in default_first[2:8]], rtol=1e-5)
    # ... but the two layouts diverge after ImpactEnergy: NumElectrons here is
    # momentum_x there.
    assert TRACK3P_COLUMNS[11] == 'momentum_x'
    assert DEFAULT_COLUMNS[11] == 'NumElectrons'
    # Secondary-emission run: the field-emission columns are not computed.
    # InitialNormalField is 0 on 194 of 200 rows and uninitialized memory on
    # the rest (denormals like 6.4e-322, two values near 3e-4);
    # InitialFaceArea is 0 (68 rows), a 1.333130e+00 placeholder (64 rows) or
    # uninitialized memory of either sign (68 rows, from -5.8 to 5.3) — none
    # of them an area in m².
    normal_field = [float(row[15]) for row in rows]
    assert normal_field.count(0.0) == 194
    assert max(normal_field) < 1e-3
    areas = np.array([float(row[16]) for row in rows])
    assert (areas == 0).sum() == 68
    assert np.isclose(areas, 1.333130).sum() == 64
    assert (areas < 0).sum() == 52


def test_the_two_layouts_share_only_their_width():
    same = [a == b for a, b in zip(DEFAULT_COLUMNS, TRACK3P_COLUMNS)]
    # Positions 0, 2-7, 10 agree (IDs, positions, energy); the rest do not.
    assert same == [True, False, True, True, True, True, True, True, False,
                    False, True, False, False, False, False, False, False]


def test_particles_load_misreads_the_default_layout(tmp_path):
    """The defect that makes `impacts_format` opt-in: `Particles.load` reads
    the default layout with the bare header as a data row, so every column
    comes back as strings and the filter matches nothing. This is *not* fixed
    by the plan — the reader is for the `Initials-Impacts` layout, and the
    module injects that selector — so it stays pinned."""
    shutil.copy(os.path.join(SCAN, 'ImpactsInfo_2.3e+07'), tmp_path / 'dump.txt')
    particles = Particles('dump.txt', {'impact_order': 1, 'impact_face_id': 6,
                                       'work_function': 4.5, 'frequency': 1e10,
                                       'beta': [50.0]}, workdir=str(tmp_path))
    particles.load()
    assert len(particles.data) == 201            # the header became a row
    assert not any(pd.api.types.is_numeric_dtype(dtype)
                   for dtype in particles.data.dtypes)
    assert particles.data.iloc[0]['InitialID'] == 'InitialID'
    particles.filter_particles()
    assert len(particles.filtered) == 0


def test_particles_load_reads_the_initials_impacts_layout(tmp_path):
    """And the same call on the other layout is clean: numeric throughout,
    header skipped, 200 rows."""
    shutil.copy(os.path.join(INITIALS, 'ImpactsInfo_2.3e+07'),
                tmp_path / 'dump.txt')
    particles = Particles('dump.txt', {'impact_order': 1, 'impact_face_id': 6,
                                       'work_function': 4.5, 'frequency': 1e10,
                                       'beta': [50.0]}, workdir=str(tmp_path))
    particles.load()
    assert len(particles.data) == 200
    assert all(np.issubdtype(dtype, np.number) for dtype in particles.data.dtypes)
    assert list(particles.data.columns) == TRACK3P_COLUMNS


def test_lost_particles_uses_the_default_layout():
    lines = _lines(os.path.join(SCAN, 'LostParticles_2.3e+07'))
    assert tuple(lines[0].split()) == DEFAULT_COLUMNS
    rows = [line.split() for line in lines[1:]]
    assert len(rows) == 50
    # A lost particle has left the domain: no face, no volume.
    assert {(row[15], row[16]) for row in rows} == {('0', '0')}


def test_header_only_dumps_are_written_when_nothing_impacts():
    """`OutputImpacts: on` writes the header even for a run that emitted 0
    particles (the Type-7 `N: 100` run)."""
    lines = _lines(os.path.join(FIELDEM, 'ImpactsInfo_2.3e+07'))
    assert len(lines) == 1
    assert tuple(lines[0].split()) == DEFAULT_COLUMNS


# --------------------------------------------------------------------------- #
# Claim 2: the postprocess tables go through parse_column_file
# --------------------------------------------------------------------------- #


def test_enhancement_counter_is_read_by_the_existing_reader():
    table = parse_column_file(os.path.join(SCAN, 'OUTPUT', 'enhancementCounter'))
    assert tuple(table) == ENHANCEMENT_COLUMNS
    assert len(table['fieldlevel']) == 7
    levels, counts = np.unique(table['fieldlevel'], return_counts=True)
    assert np.allclose(levels, [2.3e7, 2.4e7, 2.5e7])
    assert counts.tolist() == [1, 3, 3]
    # Every enhancement counter in this run is below 1: no multipacting growth.
    assert table['maxEnhancement'].max() < 1.0
    assert table['totalImpactNum'].min() >= 1
    # 'en' (acdtool postprocess track3p) has the first 7 of these columns.
    en = parse_column_file(os.path.join(HERE, 'fixtures', 'acdtool',
                                        'track3p_outputs', 'Pillbox-2.3MV.en'))
    assert tuple(en) == ENHANCEMENT_COLUMNS[:7]


def test_resonant_particles_is_read_by_the_existing_reader():
    table = parse_column_file(os.path.join(SCAN, 'OUTPUT', 'resonantparticles'))
    assert tuple(table) == RESONANT_COLUMNS
    # 40 per level is the fixture's selection, not the run's count (SOURCES.md).
    levels, counts = np.unique(table['Field_Level'], return_counts=True)
    assert np.allclose(levels, [2.3e7, 2.4e7, 2.5e7])
    assert counts.tolist() == [40, 40, 40]
    assert table['Total_Num'].min() >= 4          # InitialImpacts default
    assert table['Energy'].min() >= 10.0          # EnergyRange default


def test_header_only_tables_keep_their_header_in_the_reader():
    """`parse_column_file` picks the header line whose width matches the data
    rows; with **no** rows there is no width to match, and in Phase 0 it
    returned an empty dict, losing the 16 Faraday-cup column names. Phase 1
    needs named empty columns from these files (a run that captured nothing
    still has a `captured_electrons` of 0), so the reader now falls back to
    the last header line."""
    header = _lines(os.path.join(FIELDEM, 'OUTPUT', 'faradaycup_1'))
    assert len(header) == 1
    columns = header[0].split()
    assert len(columns) == 16
    assert tuple(columns) == DEFAULT_COLUMNS[:16]   # 2023 layout minus volID
    for name in ('faradaycup_1', 'faradaycup_2', 'faradaycup_6'):
        table = parse_column_file(os.path.join(FIELDEM, 'OUTPUT', name))
        assert tuple(table) == DEFAULT_COLUMNS[:16]
        assert all(len(values) == 0 for values in table.values())
    assert tuple(parse_column_file(os.path.join(
        FIELDEM, 'OUTPUT', 'enhancementCounter'))) == ENHANCEMENT_COLUMNS
    assert tuple(parse_column_file(os.path.join(
        FIELDEM, 'OUTPUT', 'resonantparticles'))) == RESONANT_COLUMNS


# --------------------------------------------------------------------------- #
# Claim 3: the log
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('directory,done_line,after', [
    (SCAN, 106, []),
    (INITIALS, 104, []),
    (FIELDEM, 105, ['Total Emitted Particles = 0']),
    (FIELDEM_N1, 104, ['Total Emitted Particles = 23984']),
    (LCLS, 106, ['Total Emitted Particles = 350990']),
])
def test_log_terminator_and_what_follows_it(directory, done_line, after):
    lines = _lines(os.path.join(directory, 'track3p.log'))
    assert lines[done_line - 1] == 'Done!'
    assert lines[done_line:] == after
    assert lines.count('Done!') == 1


def test_log_scalar_lines():
    scan = _read(os.path.join(SCAN, 'track3p.log'))
    assert 'number of all emitting faces = 14' in scan
    assert 'Number of MPI processes: 16' in scan
    assert 'Total Emitted Particles' not in scan     # secondary emission
    assert 'Survived particles' not in scan
    scales = [line for line in scan.splitlines() if line.startswith('scale ')]
    assert len(scales) == 3
    assert [float(line.split()[3]) for line in scales] == [2.3e7, 2.4e7, 2.5e7]

    lcls = _read(os.path.join(LCLS, 'track3p.log'))
    assert 'number of all emitting faces = 7852' in lcls
    assert 'Survived particles (still flying at end): 11' in lcls
    assert 'Total Emitted Particles = 350990' in lcls
    assert 'Number of MPI processes: 2' in lcls
    # Same build as the Pillbox probes: the run predates Lixin's 08-31 rebuild.
    assert 'Compilation Date:          Fri Aug 28 13:07:29 PDT 2026' in lcls


def test_log_has_no_wall_time_line():
    """The plan hoped for a walltime scalar; no Track3P log has one. The only
    timing lines are the two setup timings, which are not the run."""
    for directory in (SCAN, INITIALS, FIELDEM, LCLS):
        timing = [line for line in _lines(os.path.join(directory, 'track3p.log'))
                  if line.lower().startswith('time')]
        assert [line.split(':')[0] for line in timing] == [
            'Time for reading the model',
            'Time for setting up finite element framework']


def test_log_opens_with_the_version_banner():
    lines = _lines(os.path.join(SCAN, 'track3p.log'))
    assert lines[1].strip() == 'Track3P'
    assert lines[3].startswith('ACE3P Codes Source Date:')
    assert 'b7f4a98f' in lines[5]


def test_the_warn_file_is_about_omega3p_defaults():
    warn = _lines(os.path.join(SCAN, 'track3p.warn'))
    assert len(warn) == 3
    assert all('undefined, using default value' in line for line in warn)


# --------------------------------------------------------------------------- #
# InputParameters: the build's echo, and FieldScales
# --------------------------------------------------------------------------- #


def test_input_parameters_echo_parses_and_carries_field_scales():
    tree = parse_ace3p(_read(os.path.join(SCAN, 'InputParameters')))
    scales = tree.find('FieldScales')
    assert scales is not None
    assert scales.get_leaf('Type') == 'FieldGradient'
    assert scales.get_leaf('ScanToken') == '1'
    assert float(scales.get_leaf('Minimum')) == 2.3e7
    assert float(scales.get_leaf('Maximum')) == 2.5e7
    assert float(scales.get_leaf('Interval')) == 1.0e6
    # Keys the reference PDFs never mention, echoed with their defaults.
    domain = tree.find('Domain')
    assert domain.get_leaf('MaxImpacts') == '50'
    assert domain.get_leaf('FieldDir') == './omega3p_results'
    assert tree.get_leaf('OutputImpacts') == 'on'


def test_field_emission_echo_explains_the_zero_emission():
    tree = parse_ace3p(_read(os.path.join(FIELDEM, 'InputParameters')))
    emitter = tree.find('Emitter')
    assert emitter.get_leaf('Type') == '7'
    assert emitter.get_leaf('N') == '100'
    postprocess = tree.find('Postprocess')
    # Track3P's echo is malformed here: it never closes the EnhancementCounter
    # block before writing FaradayCup, so the cup parses *inside* the counter.
    # The echo is documentation, not something the module reads for this.
    assert postprocess.find('FaradayCup') is None
    cup = postprocess.find('EnhancementCounter').find('FaradayCup')
    assert cup.get_leaf('Token') == 'on'
    assert cup.get_leaf('BoundaryID') == '1'      # the echo keeps one of three


# --------------------------------------------------------------------------- #
# The N: 1 field-emission dump (Phase 3 step 2)
# --------------------------------------------------------------------------- #


def test_n1_was_not_what_unblocked_emission():
    """`N: 1` alone still emits nothing; dropping the emitter bounding box is
    what emits.

    The plan assumed `pillbox_fieldemission/`'s `Total Emitted Particles = 0`
    was down to `N: 100` suppressing every macroparticle. It is not — the same
    case at `N: 1` also emitted 0 (probe `track3p_probe5/fe_n1`, job 39649793).
    With `N <= 1.0` the build takes `numParticles = 1` unconditionally, so the
    `this_N < 0.5 * m_N` cut cannot be the cause; `J == 0.0` skipping the face
    is, and at β = 50 the RF Fowler-Nordheim exponent underflows to exactly
    zero below ≈ 1.5e6 V/m. The probe's box selected 14 low-field faces.

    What this fixture's input changed is therefore the *box*, not `N`: the
    emitter has no `x0..z1`, so all 6 558 faces of boundary 6 emit."""
    echo = parse_ace3p(_read(os.path.join(FIELDEM_N1, 'InputParameters')))
    emitter = echo.find('Emitter')
    assert emitter.get_leaf('Type') == '7'
    assert emitter.get_leaf('N') == '1'
    # The echo prints the bounding-box defaults, which is how you can see the
    # box was left out of the input entirely.
    assert float(emitter.get_leaf('x0')) <= -1.0e10
    assert float(emitter.get_leaf('x1')) >= 1.0e10

    supplied = parse_ace3p(_read(os.path.join(
        INPUTS, 'Pillbox-fieldemission-n1.track3p')))
    box_keys = ('x0', 'x1', 'y0', 'y1', 'z0', 'z1')
    assert all(supplied.find('Emitter').get_leaf(k) is None for k in box_keys)
    # The suppressed-emission input it was derived from does carry the box.
    boxed = parse_ace3p(_read(os.path.join(
        INPUTS, 'Pillbox-w4_type7_model2_fcup.track3p')))
    assert all(boxed.find('Emitter').get_leaf(k) is not None for k in box_keys)

    log = _read(os.path.join(FIELDEM_N1, 'track3p.log'))
    assert 'number of all emitting faces = 6558' in log
    assert 'Total Emitted Particles = 23984' in log


def test_n1_dump_has_real_field_emission_columns():
    """The point of this fixture: on a true `Emitter Type: 7` run the two
    field-emission columns carry physics, where the secondary-emission dump in
    `pillbox_initials_impacts/` holds uninitialized memory."""
    path = os.path.join(FIELDEM_N1, 'ImpactsInfo_2.3e+07')
    assert _lines(path)[0].lstrip('#').split() == TRACK3P_COLUMNS
    data = np.loadtxt(path, skiprows=1)
    assert data.shape == (220, 17)
    order = data[:, 1]
    assert (order == 0).sum() == 20 and (order == 1).sum() == 200

    impacts = data[order == 1]
    field, area = impacts[:, 15], impacts[:, 16]
    assert np.isfinite(field).all() and np.isfinite(area).all()
    assert (field > 3.1e7).all() and (field < 4.5e7).all()
    assert (area > 4.5e-6).all() and (area < 9.8e-6).all()
    # One emitting boundary, and these are real dark-current energies.
    assert set(impacts[:, 14].astype(int)) == {6}
    assert impacts[:, 10].min() > 3.0e5

    # Emission-point rows, as in Lixin's dump: impact == initial.
    initial = data[order == 0]
    assert np.allclose(initial[:, 2:5], initial[:, 5:8])


def test_n1_dump_beta_sizing_is_well_below_the_lcls_study():
    """A β range belongs to its dump (the Phase 2 lesson).

    This Pillbox dump's ``InitialNormalField`` averages ≈ 3.7e7 V/m, so the
    one-electron cut bites between β = 35 and β = 50 and every macroparticle
    clears it by β = 50. The LCLS study's 100–150 would saturate every sweep
    point here and demonstrate nothing, exactly as it did for
    ``geant4_track3p_beta`` in Phase 2."""
    data = np.loadtxt(os.path.join(FIELDEM_N1, 'ImpactsInfo_2.3e+07'),
                      skiprows=1)
    impacts = data[data[:, 1] == 1]
    field, area = impacts[:, 15], impacts[:, 16]
    frequency = 1.3138172e9      # the Pillbox Omega3P mode 0

    def kept(beta):
        current = fowler_nordheim_current_density(beta * field, 4.2, 'fn')
        return int((current * area / frequency / Q_E >= 1.0).sum())

    assert kept(30) == 0
    assert kept(35) == 0
    assert 0 < kept(40) < 200
    assert 0 < kept(45) < 200
    assert kept(50) == 200
    assert kept(120) == 200       # the LCLS range: saturated, hence useless


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


def test_pillbox_scan_input_declares_three_levels():
    tree = parse_ace3p(_read(os.path.join(INPUTS, 'Pillbox.track3p')))
    scales = tree.find('FieldScales')
    assert scales.get_leaf('ScanToken') == '1'
    lo, hi, step = (float(scales.get_leaf(k))
                    for k in ('Minimum', 'Maximum', 'Interval'))
    assert np.allclose(np.arange(lo, hi + step / 2, step), [2.3e7, 2.4e7, 2.5e7])
    assert tree.find('Domain').get_leaf('FieldDir') == './omega3p_results'
    assert tree.get_leaf('OutputImpacts') == 'on'
    assert tree.find('OutputImpactsInfo') is None
    assert len(tree.children('Material')) == 3


def test_pillbox_single_level_input():
    tree = parse_ace3p(_read(os.path.join(INPUTS, 'Pillbox2.3MV.track3p')))
    scales = tree.find('FieldScales')
    assert scales.get_leaf('ScanToken') == '0'
    assert float(scales.get_leaf('Scale')) == 2.3e7
    assert scales.get_leaf('Minimum') is None


def test_initials_impacts_selector_is_a_container():
    """The selector is `OutputImpactsInfo: { Type: Initials-Impacts }` — a
    block — which `parse_ace3p` reads as a Section. The scalar spelling
    `OutputImpactsInfo: Initials-Impacts` is what a user would guess; Track3P
    ignores it silently (verified on the binary, see the plan §2.3).

    Written on one line, as the probe input and Lixin's generator both do —
    the shape the Phase-0 tokenizer misread (value to end of line, brace
    swallowed) and Phase 1 fixed."""
    tree = parse_ace3p(_read(os.path.join(INPUTS,
                                          'Pillbox-b1_initials_impacts.track3p')))
    info = tree.find('OutputImpactsInfo')
    assert isinstance(info, Section)
    assert info.get_leaf('Type') == 'Initials-Impacts'
    assert tree.get_leaf('OutputImpacts') == 'on'


def test_one_line_blocks_serialize_and_reparse():
    """The one-line style round-trips through write_ace3p (which writes one
    entry per line) and the multi-line result parses to the same tree."""
    text = _read(os.path.join(INPUTS, 'Pillbox-b1_initials_impacts.track3p'))
    tree = parse_ace3p(text)
    again = parse_ace3p(write_ace3p(tree))
    assert write_ace3p(again) == write_ace3p(tree)
    assert '}' not in write_ace3p(tree.find('OutputImpactsInfo'))


def test_field_emission_input_keys():
    tree = parse_ace3p(_read(os.path.join(
        INPUTS, 'Pillbox-w4_type7_model2_fcup.track3p')))
    emitter = tree.find('Emitter')
    assert emitter.get_leaf('Type') == '7'
    assert emitter.get_leaf('WorkFunction') == '4.2'
    assert emitter.get_leaf('Beta') == '50'
    assert tree.find('Material', Type='Secondary').get_leaf('Model') == '2'
    cup = tree.find('Postprocess').find('FaradayCup')
    assert cup.get_leaf('BoundaryID') == '1 2 6'


def test_lixin_input_round_trips_through_parse_ace3p():
    text = _read(os.path.join(INPUTS, 'lcls_c3_16MV.track3p'))
    tree = parse_ace3p(text)
    scales = tree.find('FieldScales')
    assert scales.get_leaf('Type') == 'FieldGradient'
    assert scales.get_leaf('ScanToken') == '1'
    assert scales.get_leaf('Scale') == '16.0e+6'
    domain = tree.find('Domain')
    assert domain.get_leaf('FieldDir') == './omega3p_results'
    mode = domain.find('Mode')
    assert isinstance(mode, Section)
    assert mode.get_leaf('ID') == '1'
    assert mode.get_leaf('Rz') == '2.6692'
    emitter = tree.find('Emitter')
    assert emitter.get_leaf('N') == '1'
    assert emitter.get_leaf('SuppressionFactor') == '2.0'
    assert tree.find('OutputImpactsInfo').get_leaf('Type') == 'Initials-Impacts'
    assert len(tree.children('Material')) == 3
    # Serialize and re-parse: same tree.
    again = parse_ace3p(write_ace3p(tree))
    assert write_ace3p(again) == write_ace3p(tree)
    assert again.find('Domain').find('Mode').get_leaf('Rz') == '2.6692'


def test_lixin_input_top_level_is_flat():
    """The shape of the Phase-0 defect, inverted: before the fix the whole
    `FieldScales` block collapsed into one `Type` leaf carrying the rest of the
    line, brace included, and every later block nested inside it. Now the ten
    top-level entries are siblings, in file order."""
    tree = parse_ace3p(_read(os.path.join(INPUTS, 'lcls_c3_16MV.track3p')))
    assert [name for name, _ in tree.entries] == [
        'TotalTime', 'ParticlesTrajectories', 'FieldScales', 'NormalizedField',
        'Domain', 'Emitter', 'Material', 'Material', 'Material',
        'OutputImpacts', 'OutputImpactsInfo', 'Postprocess']
    assert '}' not in tree.find('FieldScales').get_leaf('Type')
    # A value with internal spaces survives when a key follows it on the line.
    assert tree.find('NormalizedField').get_leaf('StartPoint') == '0.0 0.0 2.78596'
    assert tree.find('Material', Type='Absorber').get_leaf(
        'BoundarySurfaceID') == '1 2'


# --------------------------------------------------------------------------- #
# Lixin's dump and particle file (the Phase-2 acceptance pair)
# --------------------------------------------------------------------------- #


def test_lcls_dump_is_a_populated_field_emission_dump():
    lines = _lines(os.path.join(LCLS, 'ImpactsInfo_1.6e+07'))
    assert lines[0].lstrip('#').split() == TRACK3P_COLUMNS
    data = np.loadtxt(os.path.join(LCLS, 'ImpactsInfo_1.6e+07'), skiprows=1)
    assert data.shape == (1020, 17)
    order = data[:, 1]
    assert (order == 0).sum() == 20 and (order == 1).sum() == 1000
    # Populated field-emission columns, unlike the Pillbox secondary run.
    assert data[:, 15].min() > 1e5 and data[:, 15].max() < 1e8
    assert data[:, 16].min() > 0
    # Emission-point rows: impact == initial, a few eV.
    initial = data[order == 0]
    assert np.allclose(initial[:, 2:5], initial[:, 5:8])
    assert initial[:, 10].max() < 40.0
    # ImpactFaceID is the emitting boundary 6 on all rows but one, which has
    # -1 — and that row is the first line of Lixin's particle file, so his
    # converter does not filter on face (IMPACT_ORDER=-1 means no order filter
    # either; only the energy and weight cuts apply).
    faces = data[:, 14]
    assert (faces == 6).sum() == 1019 and (faces == -1).sum() == 1


def test_lcls_particle_file_matches_lixins_converter_on_the_excerpt():
    """Lixin's `convert_track3p.py` arithmetic, applied to the excerpt,
    reproduces the 35 matched rows of the shipped `c3_16MV_beta120.data`. This
    is the same computation Phase 2 puts into `particles.py`; here it stands
    alone so the fixture pair is proven consistent before that code exists."""
    data = np.loadtxt(os.path.join(LCLS, 'ImpactsInfo_1.6e+07'), skiprows=1)
    reference = np.loadtxt(os.path.join(LCLS, 'c3_16MV_beta120.data'))
    assert reference.shape == (35, 10)

    a_fn, b_fn = 1.541434e-6, 6.830890e9
    beta, phi, frequency, charge = 120.0, 4.2, 1.2999e9, 1.602176634e-19
    kept = data[data[:, 10] >= 1000.0]
    assert len(kept) == 138
    beta_e = beta * np.abs(kept[:, 15])
    current = (a_fn * beta_e ** 2 / phi) * np.exp(-b_fn * phi ** 1.5 / beta_e)
    electrons = current * kept[:, 16] / (frequency * charge)
    survivors = kept[electrons >= 1.0]
    assert len(survivors) == 35
    direction = survivors[:, 11:14] / np.linalg.norm(survivors[:, 11:14],
                                                     axis=1)[:, None]
    produced = np.column_stack([
        survivors[:, 5:8], np.zeros(35), survivors[:, 10],
        electrons[electrons >= 1.0], direction, np.zeros(35)])
    # %.6e output: agreement to 6 significant figures.
    assert np.allclose(produced, reference, rtol=1e-6, atol=0.0)
    assert reference[:, 3].max() == 0 and reference[:, 9].max() == 0


def test_survived_particles_file_is_recorded():
    lines = _lines(os.path.join(LCLS, 'SurvivedParticles_1.6e+07'))
    header = lines[0].lstrip('#').split()
    assert len(header) == 16
    assert header[5:8] == ['Current_x', 'Current_y', 'Current_z']
    rows = [line.split() for line in lines[1:]]
    assert len(rows) == 11
    assert {row[1] for row in rows} == {'-1'}
