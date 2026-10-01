"""Fowler–Nordheim reweighting (`plans/track3p_module_plan.md`, Phase 2).

The `particles` step used a Wang–Loew FN form with a user time step and
integer rounding; Lixin Ge's LCLS-II reference converter uses the plain FN form
with the RF period and real weights. The two differ by ~2 orders of magnitude at
βE ≈ 2e9 V/m. The repo standardised on the reference (`fn_model: fn`, default)
and kept the old form as `wang-loew` so existing studies still reproduce.

The load-bearing test is the acceptance pair from Phase 0:
``tests/fixtures/track3p/lcls_c3_16MV/`` holds a 1 020-row excerpt of a real
cavity-3 dump and the 35 rows of the shipped ``c3_16MV_beta120.data`` those rows
produce. `Particles` on the excerpt must reproduce them.
"""

import os
import shutil
import warnings

import numpy as np
import pytest

from lume_ace3p.particles import (
    FN_MODELS, Particles, Q_E, TRACK3P_COLUMNS, default_output_name,
    fowler_nordheim_current_density,
)

HERE = os.path.dirname(os.path.abspath(__file__))
LCLS = os.path.join(HERE, 'fixtures', 'track3p', 'lcls_c3_16MV')
SAMPLE = os.path.join(HERE, '..', 'examples', 'assets', 'test_particles.txt')

# Lixin's converter settings for the shipped beta-120 file.
LIXIN = {'work_function': 4.2, 'frequency': 1.2999e9, 'beta': 120.0,
         'min_energy_ev': 1000.0, 'output_format': 'geant4'}


def _stage(tmp_path, source, name='dump.txt'):
    os.makedirs(tmp_path, exist_ok=True)
    shutil.copy(source, tmp_path / name)
    return name


def _run(tmp_path, params, source=os.path.join(LCLS, 'ImpactsInfo_1.6e+07'),
         output='particles.data'):
    name = _stage(tmp_path, source)
    particles = Particles(name, dict(params), output_file=output,
                          workdir=str(tmp_path))
    filtered = particles.run()
    return particles, filtered, os.path.join(str(tmp_path), output)


# --------------------------------------------------------------------------- #
# Acceptance: the reference converter's output, bit for bit at %.6e
# --------------------------------------------------------------------------- #


def test_fn_model_reproduces_lixins_particle_file(tmp_path):
    particles, filtered, output = _run(tmp_path, LIXIN)
    reference = np.loadtxt(os.path.join(LCLS, 'c3_16MV_beta120.data'))
    produced = np.loadtxt(output)
    assert produced.shape == reference.shape == (35, 10)
    # Position, energy, weight and unit direction agree to the file's own six
    # significant figures.
    physical = [0, 1, 2, 4, 5, 6, 7, 8]
    assert np.allclose(produced[:, physical], reference[:, physical],
                       rtol=1e-6, atol=0.0)
    # Columns 4 and 10 are ignored by the Geant4 reader; Lixin writes 0, this
    # writes the impact phase (RF cycles) and the face ID.
    assert reference[:, 3].max() == 0 and reference[:, 9].max() == 0
    assert produced[:, 3].min() > 0
    assert set(produced[:, 9]) <= {6.0, -1.0}
    # The filter chain: 1020 -> 138 (energy) -> 35 (>= 1 electron).
    assert len(filtered) == 138
    assert (filtered['ParticleWeight'] >= 1.0).sum() == 35
    assert particles.emission_time == pytest.approx(1.0 / 1.2999e9)
    assert particles.impact_order is None and particles.impact_face_id is None


def test_fn_weights_are_real_and_wang_loew_weights_are_whole(tmp_path):
    _, fn, _ = _run(tmp_path, LIXIN)
    _, wl, _ = _run(tmp_path / 'b', dict(LIXIN, fn_model='wang-loew'))
    assert not np.allclose(fn['ParticleWeight'], np.round(fn['ParticleWeight']))
    assert np.allclose(wl['ParticleWeight'], np.round(wl['ParticleWeight']))
    # And they are different models: about two orders of magnitude apart.
    ratio = wl['ParticleWeight'].sum() / fn['ParticleWeight'].sum()
    assert 10 < ratio < 1000


def test_current_density_models():
    beta_e = np.array([1e9, 2e9, 4e9])
    fn = fowler_nordheim_current_density(beta_e, 4.2, 'fn')
    # Plain FN by hand.
    expected = 1.541434e-6 / 4.2 * beta_e ** 2 * np.exp(-6.830890e9 * 4.2 ** 1.5 / beta_e)
    assert np.allclose(fn, expected, rtol=1e-12)
    wl = fowler_nordheim_current_density(beta_e, 4.2, 'wang-loew')
    assert (wl > fn).all()
    # A vanishing field gives the clipped floor exp(-500), not NaN or an
    # overflow -- the reference converter's own behaviour, and small enough that
    # the resulting weight is far below the one-electron cut either way.
    assert fowler_nordheim_current_density(0.0, 4.2) == pytest.approx(np.exp(-500))
    assert np.isfinite(fowler_nordheim_current_density([0.0, -1e9], 4.2)).all()
    assert set(FN_MODELS) == {'fn', 'wang-loew'}


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


def test_dt_is_a_deprecated_alias_for_the_emission_time(tmp_path):
    name = _stage(tmp_path, SAMPLE)
    base = {'work_function': 4.5, 'beta': [50.0], 'impact_order': 1,
            'impact_face_id': 6}
    with pytest.warns(DeprecationWarning, match='frequency = 10000000000.0'):
        old = Particles(name, dict(base, dt=1e-10), workdir=str(tmp_path))
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        new = Particles(name, dict(base, frequency=1e10), workdir=str(tmp_path))
    assert old.emission_time == new.emission_time == 1e-10
    with pytest.raises(ValueError, match='not both'):
        Particles(name, dict(base, dt=1e-10, frequency=1e10),
                  workdir=str(tmp_path))
    with pytest.raises(ValueError, match="'frequency'"):
        Particles(name, dict(base), workdir=str(tmp_path))
    with pytest.raises(ValueError, match='positive'):
        Particles(name, dict(base, frequency=0.0), workdir=str(tmp_path))


def test_required_and_validated_keys(tmp_path):
    name = _stage(tmp_path, SAMPLE)
    with pytest.raises(ValueError, match='work_function'):
        Particles(name, {'frequency': 1e9, 'beta': 1.0}, workdir=str(tmp_path))
    with pytest.raises(ValueError, match="'beta'"):
        Particles(name, {'frequency': 1e9, 'work_function': 4.2},
                  workdir=str(tmp_path))
    with pytest.raises(ValueError, match='unknown fn_model'):
        Particles(name, {'frequency': 1e9, 'work_function': 4.2, 'beta': 1.0,
                         'fn_model': 'murphy-good'}, workdir=str(tmp_path))


def test_scalar_beta_is_one_bin(tmp_path):
    particles, filtered, _ = _run(tmp_path, LIXIN)
    assert particles.num_bins == 1
    assert particles.beta.tolist() == [120.0]
    assert set(filtered['Bin']) == {0}


def test_filters_are_optional_and_composable(tmp_path):
    params = {'work_function': 4.5, 'frequency': 1e10, 'beta': [50.0],
              'output_format': 'track3p'}
    _, unfiltered, _ = _run(tmp_path, params, source=SAMPLE, output='a.txt')
    assert len(unfiltered) == 2000
    _, by_order, _ = _run(tmp_path / 'b', dict(params, impact_order=1),
                          source=SAMPLE, output='b.txt')
    assert set(by_order['ImpactOrder']) == {1}
    _, by_face, _ = _run(tmp_path / 'c', dict(params, impact_face_id=[6]),
                         source=SAMPLE, output='c.txt')
    assert set(by_face['ImpactFaceID']) == {6}
    _, by_energy, _ = _run(tmp_path / 'd', dict(params, min_energy_ev=1e4),
                           source=SAMPLE, output='d.txt')
    assert by_energy['ImpactEnergy'].min() >= 1e4
    assert 0 < len(by_energy) < 2000
    _, combined, _ = _run(tmp_path / 'e',
                          dict(params, impact_order=1, impact_face_id=6,
                               min_energy_ev=1e4),
                          source=SAMPLE, output='e.txt')
    assert len(combined) == len(set(by_order.index) & set(by_face.index)
                                & set(by_energy.index))


# --------------------------------------------------------------------------- #
# Output files
# --------------------------------------------------------------------------- #


def test_geant4_file_drops_sub_electron_rows_and_normalises_direction(tmp_path):
    _, filtered, output = _run(tmp_path, LIXIN)
    produced = np.loadtxt(output)
    assert len(produced) == (filtered['ParticleWeight'] >= 1.0).sum()
    assert produced[:, 5].min() >= 1.0
    assert np.allclose(np.linalg.norm(produced[:, 6:9], axis=1), 1.0, atol=1e-6)


def test_track3p_output_keeps_nineteen_columns(tmp_path):
    _, filtered, output = _run(tmp_path, dict(LIXIN, output_format='track3p'),
                               output='weighted.txt')
    with open(output) as file:
        header = file.readline()
    assert header.startswith('#')
    assert header.lstrip('#').split() == TRACK3P_COLUMNS + ['Bin', 'ParticleWeight']
    table = np.loadtxt(output)
    assert table.shape == (138, 19)
    # Every filtered row is written, sub-electron weights included: this is
    # the study file, not the Geant4 source.
    assert (table[:, 18] < 1.0).sum() == 103
    assert np.allclose(table[:, 18], filtered['ParticleWeight'], rtol=1e-6)


def test_empty_filter_result_writes_an_empty_file(tmp_path):
    _, filtered, output = _run(tmp_path, dict(LIXIN, impact_face_id=99))
    assert len(filtered) == 0
    assert os.path.getsize(output) == 0


# --------------------------------------------------------------------------- #
# The default output name (`plans/track3p_module_plan.md` Phase 3 step 1)
# --------------------------------------------------------------------------- #


def test_default_output_name_never_equals_the_input():
    # The '.txt' names every shipped example uses are unchanged.
    assert default_output_name('sample.txt') == 'sample_modified.txt'
    assert default_output_name('a/b/sample.txt') == 'a/b/sample_modified.txt'
    assert default_output_name('sample.data') == 'sample_modified.data'
    # A Track3P dump's trailing '.3e+07' is a field level, not an extension:
    # the old replace('.txt', ...) returned the input name unchanged here.
    assert (default_output_name('ImpactsInfo_2.3e+07')
            == 'ImpactsInfo_2.3e+07_modified')
    assert (default_output_name('track3p_results/ImpactsInfo_1.6e+07')
            == 'track3p_results/ImpactsInfo_1.6e+07_modified')
    assert default_output_name('ImpactsInfo_23') == 'ImpactsInfo_23_modified'


def test_derived_output_name_leaves_a_track3p_dump_alone(tmp_path):
    """The default name for an extensionless dump is a new file next to it."""
    name = _stage(tmp_path, os.path.join(LCLS, 'ImpactsInfo_1.6e+07'),
                  name='ImpactsInfo_1.6e+07')
    before = (tmp_path / name).read_bytes()
    particles = Particles(name, dict(LIXIN), workdir=str(tmp_path))
    assert particles.output_file == 'ImpactsInfo_1.6e+07_modified'
    particles.run()
    assert (tmp_path / 'ImpactsInfo_1.6e+07_modified').is_file()
    assert (tmp_path / name).read_bytes() == before


def test_output_equal_to_the_input_is_refused(tmp_path):
    """Belt and braces: an explicit `output:` naming the dump is refused at
    construction, before anything can overwrite the staged file."""
    name = _stage(tmp_path, SAMPLE, name='ImpactsInfo_2.3e+07')
    with pytest.raises(ValueError, match="'output:'"):
        Particles(name, dict(LIXIN), output_file=name, workdir=str(tmp_path))
    # Compared as paths, not strings.
    with pytest.raises(ValueError, match="'output:'"):
        Particles(name, dict(LIXIN), output_file='./' + name,
                  workdir=str(tmp_path))
    with pytest.raises(ValueError, match="'output:'"):
        Particles('sub/dump.txt', dict(LIXIN), output_file='sub/../sub/dump.txt',
                  workdir=str(tmp_path))
    # A different name is fine.
    assert Particles(name, dict(LIXIN), output_file='particles.data',
                     workdir=str(tmp_path)).output_file == 'particles.data'
