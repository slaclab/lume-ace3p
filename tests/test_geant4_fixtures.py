"""Characterization tests over the real Geant4 detector outputs (see
`plans/track3p_module_plan.md`, Phase 4 step 1).

These pin what the LCLS-II polycone application's ``detectors = on`` scoring
*files* look like — the two comma-separated, ``#``-headed CSVs — so the module's
readers are built against real output rather than an assumed layout, exactly as
Phase 0 did for Track3P's dumps.

Both fixtures are verbatim copies of the 100-pass point of Lixin Ge's
convergence study; provenance is in ``tests/fixtures/geant4/SOURCES.md``. No
Geant4 binary is needed.

Three claims are load-bearing for the phase:

1. **These are not ACE3P column tables.** They are comma-separated, so
   :func:`parse_column_file` — the header-driven reader every other table in the
   package goes through — cannot read them, which is why
   :mod:`lume_ace3p.geant4` has its own pair.
2. **A zero is a result, not a gap.** Seven of the eight detectors recorded
   ``edep_MeV = 0`` in this run while all eight counted gamma entries. A reader
   that dropped zeros, or an ``extract`` that returned NaN for one, would be
   wrong.
3. **The spectrum is ragged.** The application writes only each detector's
   non-empty energy bins, so row counts differ per detector and
   ``detector_id`` repeats down the rows — which is why the spectrum rides out
   through ``field()`` rather than becoming a detector-indexed column.
"""

import os

import numpy as np
import pytest

from lume_ace3p.ace3p import parse_column_file
from lume_ace3p.geant4 import (
    DETECTOR_DOSE_COLUMNS, GAMMA_SPECTRUM_COLUMNS,
    read_detector_dose, read_gamma_spectrum,
)

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'geant4')
DETECTOR_DOSE = os.path.join(FIXTURES, 'detector_dose.csv')
GAMMA_SPECTRUM = os.path.join(FIXTURES, 'detector_gamma_spectrum.csv')

PHASE4_FIXTURES = ['detector_dose.csv', 'detector_gamma_spectrum.csv']

# The 8 GM-tube Z positions, compiled into the application (construction.hh:42),
# one per cavity of the LCLS-II cryomodule.
DETECTOR_Z_MM = (414., 1801., 3189., 4576., 5963., 7351., 8738., 10126.)


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #


def test_fixture_inventory():
    """Every file SOURCES.md lists is present and nothing else is (a stray file
    here would be an undocumented fixture). That these are also *tracked* is
    asserted repo-wide in test_track3p_fixtures.py."""
    present = {name for name in os.listdir(FIXTURES)
               if not name.endswith('.md')}
    assert present == set(PHASE4_FIXTURES)


# --------------------------------------------------------------------------- #
# Claim 1: these need their own reader
# --------------------------------------------------------------------------- #


def test_the_detector_csvs_are_comma_separated_with_a_commented_header():
    with open(DETECTOR_DOSE) as file:
        lines = file.read().splitlines()
    assert lines[0].startswith('#')
    assert lines[0].lstrip('# ').split(',') == list(DETECTOR_DOSE_COLUMNS)
    assert len(lines) == 1 + 8                      # header + 8 detectors
    assert all(len(line.split(',')) == 4 for line in lines[1:])

    with open(GAMMA_SPECTRUM) as file:
        spectrum = file.read().splitlines()
    assert spectrum[0].startswith('#')
    assert spectrum[0].lstrip('# ').split(',') == list(GAMMA_SPECTRUM_COLUMNS)
    assert all(len(line.split(',')) == 3 for line in spectrum[1:])


def test_the_ace3p_column_reader_cannot_read_them():
    """Why :mod:`lume_ace3p.geant4` carries its own readers rather than reusing
    :func:`parse_column_file`.

    That reader splits on whitespace, so a comma-separated row is one token: it
    finds no numeric rows, falls into its header-only branch, and returns a
    single key made of the file's last line. Pinned so a future 'just reuse the
    column reader' refactor fails here instead of silently producing one garbage
    column."""
    parsed = parse_column_file(DETECTOR_DOSE)
    assert list(parsed) == ['8,10126,0.0,4']
    assert parsed['8,10126,0.0,4'].size == 0


# --------------------------------------------------------------------------- #
# The readers
# --------------------------------------------------------------------------- #


def test_read_detector_dose():
    table = read_detector_dose(DETECTOR_DOSE)
    assert sorted(table) == sorted(DETECTOR_DOSE_COLUMNS)
    assert [int(i) for i in table['detector_id']] == list(range(1, 9))
    assert table['z_mm'].tolist() == list(DETECTOR_Z_MM)
    assert table['gamma_entries'].tolist() == [5., 18., 10., 11., 9., 4., 1., 4.]


def test_a_zero_edep_is_a_result_not_a_gap():
    """Claim 2. Only detector 5 saw energy deposited; the other seven are real
    zeros, and all eight counted gammas."""
    table = read_detector_dose(DETECTOR_DOSE)
    edep = table['edep_MeV']
    assert edep[4] == pytest.approx(20107900.0)
    assert np.count_nonzero(edep) == 1
    # Every detector has a row, including the ones that deposited nothing.
    assert len(edep) == 8
    assert np.all(table['gamma_entries'] > 0)


def test_read_gamma_spectrum_is_ragged():
    """Claim 3: per-detector row counts differ, so this is a long/tidy table
    and not an 8 x 100 matrix."""
    table = read_gamma_spectrum(GAMMA_SPECTRUM)
    assert sorted(table) == sorted(GAMMA_SPECTRUM_COLUMNS)
    assert len(table['detector_id']) == 52
    counts = [int(np.count_nonzero(table['detector_id'] == d))
              for d in range(1, 9)]
    assert counts == [5, 15, 8, 8, 7, 4, 1, 4]
    assert sum(counts) == 52
    # The application's 100 log bins span 1 keV - 100 MeV; these are inside it.
    assert table['energy_MeV'].min() >= 0.001
    assert table['energy_MeV'].max() <= 100.0
    assert np.all(table['weighted_fluence'] > 0)


def test_readers_return_none_for_a_missing_file(tmp_path):
    """A run that scored no detectors wrote no CSV, which is not an error: the
    module turns this into its NaN output sentinel."""
    assert read_detector_dose(str(tmp_path / 'nope.csv')) is None
    assert read_gamma_spectrum(str(tmp_path / 'nope.csv')) is None
    assert read_detector_dose(None) is None


def test_reader_skips_a_truncated_trailing_row(tmp_path):
    """A run killed mid-write leaves a short last line. The readers skip it
    rather than raising, the same tolerance read_dose_file has for the voxel
    files of the same application."""
    path = tmp_path / 'detector_dose.csv'
    path.write_text('# detector_id,z_mm,edep_MeV,gamma_entries\n'
                    '1,414,0.0,5\n'
                    '2,1801,\n')
    table = read_detector_dose(str(path))
    assert [int(i) for i in table['detector_id']] == [1]
