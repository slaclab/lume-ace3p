"""Characterization tests over the real TEM3P fixtures (see
`plans/tem3p_module_plan.md`, Phases 0 and 1).

These pin what TEM3P's files look like, before a ``tem3p`` module exists, so
Phase 2 builds against ground truth: the ``ResultFile`` layouts (static elastic,
thermal, thermo-elastic interleaved, eigen table), its append-on-rerun
behaviour, and the ``tem3p.log`` lines the parser reads. Everything runs on
files frozen from job 44528190 (S3DF, Rocky 9 build of 2026-11-02); provenance
is in ``tests/fixtures/tem3p/SOURCES.md``. No ACE3P binary is needed.

Claims that are load-bearing for the plan:

1. **``ResultFile`` appends.** A re-run into one job directory leaves two runs
   in the file; the parser reads the last (P2).
2. **The elastic row is one global summary, not one row per block.**
   ``block_id`` is the block holding the largest ``|u|`` (``ElasticProblem.C``).
3. **Three silent failures exit 0 with plausible output** — a missing
   ``Omega3PId`` (P3), a misspelled ``NeumanValue`` (P5), and a deformed mesh
   built from the deformed mesh (P4b) — so Phase 2 has to catch them from the
   input, not from the outputs.
4. **A moved evaluation dies loading the EM run** with no message of its own
   (P9); the parser names the cause.
"""

import os
import re
import shutil

import numpy as np
import pytest

from lume_ace3p.ace3p import (
    TEM3P_LOG, TEM3P_RESULT_FILE, Tem3P, parse_ace3p, parse_omega3p_output,
    parse_tem3p_log, parse_tem3p_result_file, parse_tem3p_results, write_ace3p,
)
from lume_ace3p.modules import ARTIFACT_KINDS, TEM3P_SOLUTION

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'tem3p')
SRFCELL = os.path.join(FIXTURES, 'srfcell')
RFGUN = os.path.join(FIXTURES, 'rfgun')
TESLA = os.path.join(FIXTURES, 'teslacavity')
COUPLER = os.path.join(FIXTURES, 'srfcoupler')

INPUTS = sorted(
    os.path.join(case, 'inputs', name)
    for case in (SRFCELL, RFGUN, TESLA, COUPLER)
    for name in os.listdir(os.path.join(case, 'inputs'))
    if name.endswith(('.tem3p', '.omega3p', '.s3p')))


def _read(*parts):
    with open(os.path.join(*parts)) as file:
        return file.read()


def _frequency(*parts):
    return parse_omega3p_output(os.path.join(*parts, 'omega3p.out'))['Frequency'][0]


def _make_tem3p(tmp_path, case, job_dir='tem3p_results', input_name=None,
                **kwargs):
    """A Tem3P wrapper whose workdir holds a copy of a fixture job directory."""
    workdir = tmp_path / 'wd'
    shutil.copytree(os.path.join(case, job_dir),
                    workdir / kwargs.get('results_dir', 'tem3p_results'))
    inputs = os.path.join(case, 'inputs')
    if input_name is None:
        input_name = next(n for n in sorted(os.listdir(inputs))
                          if n.endswith('.tem3p'))
    source = tmp_path / input_name
    source.write_text(_read(inputs, input_name))
    return Tem3P(str(source), workdir=str(workdir), **kwargs)


# --------------------------------------------------------------------------- #
# The wrapper
# --------------------------------------------------------------------------- #


def test_tem3p_class_facts():
    """Usage is 'tem3p <input> [jobname]', results default to tem3p_results,
    and the results live in the job directory itself."""
    assert Tem3P.module_name == 'tem3p'
    assert Tem3P.default_job_name == 'tem3p_results'
    assert Tem3P.accepts_results_dir_arg is True
    assert Tem3P.results_subdir == ''


def test_tem3p_solution_is_an_artifact_kind():
    assert TEM3P_SOLUTION == 'tem3p_solution'
    assert TEM3P_SOLUTION in ARTIFACT_KINDS


def test_tem3p_wrapper_reads_the_srfcell_fixture(tmp_path):
    solver = _make_tem3p(tmp_path, SRFCELL, input_name='SRFCell.tem3p')
    assert solver.output_file == TEM3P_RESULT_FILE
    assert solver.results_dir() == 'tem3p_results'
    solver.output_parser()
    data = solver.output_data
    assert set(data) == {'thermal', 'elastic', 'eigen', 'units', 'runs', 'log'}
    assert data['elastic']['u_mag_max'] == pytest.approx(1.9466783e-06)
    assert data['thermal'] is None and data['eigen'] is None
    assert data['log']['completed'] is True


def test_tem3p_wrapper_passes_results_dir_on_the_command_line(tmp_path):
    solver = _make_tem3p(tmp_path, SRFCELL, job_dir='tem3p_pair2',
                         input_name='SRFCell_pair2.tem3p',
                         results_dir='tem3p_pair2')
    assert solver.results_dir() == 'tem3p_pair2'
    command = solver.solver_command()
    assert ' tem3p ' in command and command.endswith('.tem3p tem3p_pair2')
    solver.output_parser()
    assert solver.output_data['log']['em_directory'] == 'omega3p_results_deformed'


def test_tem3p_wrapper_raises_without_a_log(tmp_path):
    """tem3p with a bad argument count prints its usage and exits 0, so a run
    that produced nothing must fail here rather than read as a result."""
    workdir = tmp_path / 'wd'
    os.makedirs(workdir / 'tem3p_results')
    source = tmp_path / 'SRFCell.tem3p'
    source.write_text(_read(SRFCELL, 'inputs', 'SRFCell.tem3p'))
    solver = Tem3P(str(source), workdir=str(workdir))
    with pytest.raises(FileNotFoundError, match='usage and exit 0'):
        solver.output_parser()


def test_tem3p_wrapper_raises_on_an_unfinished_log(tmp_path):
    solver = _make_tem3p(tmp_path, SRFCELL, input_name='SRFCell.tem3p')
    log = os.path.join(solver.workdir, 'tem3p_results', TEM3P_LOG)
    text = _read(log)
    with open(log, 'w') as file:
        file.write(re.sub(r'Ending run at.*\n', '', text))
    with pytest.raises(ValueError, match="no 'Ending run at' line"):
        solver.output_parser()


def test_tem3p_wrapper_raises_without_a_result_file(tmp_path):
    solver = _make_tem3p(tmp_path, SRFCELL, input_name='SRFCell.tem3p')
    os.remove(os.path.join(solver.workdir, 'tem3p_results', TEM3P_RESULT_FILE))
    with pytest.raises(FileNotFoundError, match='no ResultFile'):
        solver.output_parser()


# --------------------------------------------------------------------------- #
# SRFCell: Lorentz-force detuning (P1-P5)
# --------------------------------------------------------------------------- #


def test_srfcell_frequency_shift_matches_the_archive():
    """P1: the 2026 build reproduces the 2023 archive's -7172.1 Hz."""
    df = (_frequency(SRFCELL, 'omega3p_results_deformed')
          - _frequency(SRFCELL, 'omega3p_results'))
    assert df == pytest.approx(-7172.1, abs=0.01)


def test_srfcell_static_elastic_result():
    data = parse_tem3p_results(os.path.join(SRFCELL, 'tem3p_results'))
    elastic = data['elastic']
    assert list(elastic) == ['u_x_min', 'u_x_max', 'u_y_min', 'u_y_max',
                             'u_z_min', 'u_z_max', 'u_mag_max', 'block_id']
    assert elastic['u_z_min'] == pytest.approx(-1.9415996e-06)
    assert elastic['block_id'] == 1 and isinstance(elastic['block_id'], int)
    assert set(data['units'].values()) == {'m'}

    log = data['log']
    assert log['problems'] == ['elastic']
    assert log['n_dofs'] == {'elastic': 205443}
    assert log['scaling_factors'] == {
        'elastic': [pytest.approx(1986361833765.0857)]}
    assert log['newton_iterations'] == {} and log['modes'] == {}
    assert log['run_time'] == 34 and log['mpi_processes'] == 16
    assert log['em_directory'] == 'omega3p_results'
    assert log['em_mesh_input_dir'] == 'omega3p_results'
    assert log['em_loaded'] is True
    assert log['source_tag'].startswith('344672a6')


def test_result_file_appends_and_the_last_run_is_read():
    """P2: a re-run into the same job directory appends a second, identical
    header-plus-row block. tem3p.log is overwritten, so it describes only the
    second run; ResultFile must be read from the last run onward."""
    rerun = parse_tem3p_result_file(
        _read(SRFCELL, 'tem3p_results', TEM3P_RESULT_FILE))
    first = parse_tem3p_result_file(
        _read(SRFCELL, 'tem3p_results', 'ResultFile.first_run'))
    assert rerun['runs'] == 2 and first['runs'] == 1
    assert rerun['elastic'] == first['elastic']


def test_missing_omega3p_id_is_silent_in_every_output():
    """P3: without Omega3PId the body solve is unchanged, the run exits 0 and
    writes the deformed mesh, yet the mesh is undeformed. Nothing in TEM3P's own
    outputs differs, so Phase 2's guard must come from the input."""
    noid = parse_tem3p_results(os.path.join(SRFCELL, 'tem3p_noid'))
    lfd = parse_tem3p_result_file(
        _read(SRFCELL, 'tem3p_results', 'ResultFile.first_run'))
    assert noid['log']['completed'] is True
    assert noid['elastic'] == lfd['elastic']
    df = (_frequency(SRFCELL, 'omega3p_noid')
          - _frequency(SRFCELL, 'omega3p_results'))
    assert abs(df) < 1e-3


def test_appended_pair_must_deform_the_original_mesh():
    """P4: the second pass reads fields from the deformed run. Deforming the
    original vacuum mesh moves the shift by under 1%; deforming the deformed one
    roughly doubles it."""
    original = _frequency(SRFCELL, 'omega3p_results')
    first = _frequency(SRFCELL, 'omega3p_results_deformed') - original
    pair2 = _frequency(SRFCELL, 'omega3p_pair2') - original
    pair2c = _frequency(SRFCELL, 'omega3p_pair2c') - original
    assert pair2 == pytest.approx(-7240.4, abs=0.1)
    assert pair2c == pytest.approx(-14412.2, abs=0.1)
    assert abs(pair2 / first - 1) < 0.01
    assert pair2c / first > 1.9

    for job, mesh_dir in (('tem3p_pair2', 'omega3p_results'),
                          ('tem3p_pair2c', 'omega3p_results_deformed')):
        log = parse_tem3p_results(os.path.join(SRFCELL, job))['log']
        assert log['em_directory'] == 'omega3p_results_deformed'
        assert log['em_mesh_input_dir'] == mesh_dir


def test_neumann_misspelling_is_silently_ignored():
    """P5: a 1e5 Pa load spelled NeumanValue leaves ResultFile identical, to
    every digit, to the unloaded run; spelled NeumannValue it moves |u| by an
    order of magnitude."""
    unloaded = _read(SRFCELL, 'tem3p_results', 'ResultFile.first_run')
    assert _read(SRFCELL, 'tem3p_neum_typo', TEM3P_RESULT_FILE) == unloaded
    loaded = parse_tem3p_result_file(
        _read(SRFCELL, 'tem3p_neum_ok', TEM3P_RESULT_FILE))['elastic']
    base = parse_tem3p_result_file(unloaded)['elastic']
    assert loaded['u_mag_max'] > 10 * base['u_mag_max']

    typo = parse_ace3p(_read(SRFCELL, 'inputs', 'SRFCell_neum_typo.tem3p'))
    ok = parse_ace3p(_read(SRFCELL, 'inputs', 'SRFCell_neum_ok.tem3p'))
    boundary = lambda tree: tree.find('ElasticProblem').find('Boundary', Id='4')
    assert boundary(typo).get_leaf('NeumanValue') == '1.0e5'
    assert boundary(ok).get_leaf('NeumannValue') == '1.0e5'


def test_moved_workdir_dies_loading_the_em_run():
    """P9: after the evaluation directory moved, the EM run's postprocess.in
    names a mesh that is gone. TEM3P aborts (MPI_ABORT -12313, rc 137) right
    after naming the EM directory, with no message; the parser says why."""
    log = parse_tem3p_log(_read(SRFCELL, 'tem3p_moved', TEM3P_LOG))
    assert log['completed'] is False
    assert log['em_directory'] == 'omega3p_results'
    assert log['em_loaded'] is False
    with pytest.raises(ValueError, match="re-run the EM step"):
        parse_tem3p_results(os.path.join(SRFCELL, 'tem3p_moved'))


# --------------------------------------------------------------------------- #
# RfGun: thermo-elastic, interleaved ResultFile (P7)
# --------------------------------------------------------------------------- #


def test_rfgun_frequency_shift_matches_the_archive():
    df = (_frequency(RFGUN, 'omega3p_results_deformed')
          - _frequency(RFGUN, 'omega3p_results'))
    assert df == pytest.approx(47427.3, abs=0.1)


def test_rfgun_interleaved_result_file():
    """The elastic header is written before the thermal block and its row
    after it; both halves belong to one run."""
    text = _read(RFGUN, 'tem3p_results', TEM3P_RESULT_FILE)
    lines = [line.strip() for line in text.splitlines()]
    assert lines[0].startswith('#') and lines[1].startswith('=====')
    data = parse_tem3p_result_file(text)
    assert data['runs'] == 1
    assert data['thermal']['max_temperature'] == pytest.approx(28.595)
    assert data['thermal']['min_temperature'] == pytest.approx(23.1171)
    assert data['thermal']['sideset_power'] == {
        1: 2.40838e-07, 2: 0.00114157, 3: -0.0129865, 4: -0.0574297,
        5: 213.111, 6: -215.613}
    assert data['elastic']['u_z_max'] == pytest.approx(3.3106951e-06)
    assert data['elastic']['u_z_min'] == 0.0


def test_rfgun_log_is_read_per_problem():
    """Both problems run; each has its own DOF count and scaling factor. The
    vacuum-mesh DOF lines (512188) are skipped, and the log's low-precision
    temperature and sentinel stress lines are not read."""
    log = parse_tem3p_results(os.path.join(RFGUN, 'tem3p_results'))['log']
    assert log['problems'] == ['thermal', 'elastic']
    assert log['n_dofs'] == {'thermal': 337804, 'elastic': 1013412}
    assert log['scaling_factors'] == {
        'thermal': [pytest.approx(41050781.146822527)],
        'elastic': [pytest.approx(114029947630.06256)]}
    assert log['linear_iterations'] == {'thermal': [1234]}
    assert log['newton_iterations'] == {}
    assert log['run_time'] == 150
    assert set(log) >= {'em_directory', 'em_mesh_input_dir'}
    assert log['em_mesh_input_dir'] == 'omega3p_results'


# --------------------------------------------------------------------------- #
# TeslaCavity: mechanical eigenmodes (P6)
# --------------------------------------------------------------------------- #


def test_tesla_eigen_table():
    data = parse_tem3p_results(os.path.join(TESLA, 'tem3p_results'))
    eigen = data['eigen']
    assert data['elastic'] is None and data['thermal'] is None
    assert list(eigen['mode']) == [1, 2, 3, 4, 5]
    assert eigen['frequency'] == pytest.approx(
        [62.822941, 167.12759, 242.46092, 307.64515, 468.56376])
    assert eigen['block_id'].dtype.kind == 'i'
    assert eigen['u_mag_max'][0] == pytest.approx(0.44516552)
    assert data['units']['frequency'] == 'Hz'


def test_tesla_log_modes_agree_with_the_table():
    """The log's 'Mode n, Frequency:' lines carry the same frequencies at full
    precision. ARPACK's own indented 'Frequency:' lines (1.88e10 ...) are a
    different quantity and are not read."""
    data = parse_tem3p_results(os.path.join(TESLA, 'tem3p_results'))
    modes = data['log']['modes']
    assert list(modes) == [1, 2, 3, 4, 5]
    assert [modes[m] for m in data['eigen']['mode']] == pytest.approx(
        data['eigen']['frequency'], rel=1e-7)
    assert modes[1] == pytest.approx(62.82294115466939, rel=1e-15)
    assert data['log']['em_directory'] is None
    assert data['log']['em_loaded'] is None
    assert data['log']['scaling_factors'] == {}


def test_tesla_per_mode_file_names():
    """Per-mode outputs are named _Mode%03d_Freq%.2fHz."""
    listing = _read(TESLA, 'tem3p_results', 'LISTING').split()
    modes = parse_tem3p_log(_read(TESLA, 'tem3p_results', TEM3P_LOG))['modes']
    for mode, frequency in modes.items():
        suffix = f'_Mode{mode:03d}_Freq{frequency:.2f}Hz'
        assert f'Displacement{suffix}.mod' in listing
        assert f'DeformedMesh{suffix}.ncdf' in listing


# --------------------------------------------------------------------------- #
# SrfCoupler: S3P-driven nonlinear thermal (P8)
# --------------------------------------------------------------------------- #


def test_srfcoupler_thermal_only_result():
    data = parse_tem3p_results(os.path.join(COUPLER, 'tem3p_results'))
    assert data['elastic'] is None and data['eigen'] is None
    assert data['units'] == {}
    thermal = data['thermal']
    assert thermal['max_temperature'] == pytest.approx(324.352)
    assert thermal['min_temperature'] == 2.0
    assert sorted(thermal['sideset_power']) == list(range(1, 12))
    assert thermal['sideset_power'][4] == pytest.approx(0.781339)


def test_srfcoupler_log():
    """9 Newton iterations; one scaling-factor line per coupled boundary or heat
    source (5 here); the EM producer is the S3P run; no deformed mesh."""
    log = parse_tem3p_results(os.path.join(COUPLER, 'tem3p_results'))['log']
    assert log['problems'] == ['thermal']
    assert log['newton_iterations'] == {'thermal': 9}
    assert log['scaling_factors'] == {'thermal': [437.5] * 5}
    assert log['n_dofs'] == {'thermal': 101857}
    assert log['mpi_processes'] == 96
    assert log['em_directory'] == 's3p_results'
    assert log['em_mesh_input_dir'] is None
    assert log['em_loaded'] is True


# --------------------------------------------------------------------------- #
# ResultFile edge cases (synthetic)
# --------------------------------------------------------------------------- #

_HEADER = ('#    u_x min [m]     u_x max [m]     u_y min [m]     u_y max [m]'
           '     u_z min [m]     u_z max [m]   u_mag max [m]    block_id\n')
_ROW = '  -1e-07   1e-07  -2e-07   2e-07  -3e-07   3e-07   4e-07           2\n'
_RULE = ' ===================================== \n'


def _thermal(t_max):
    return (_RULE + f' Max T = {t_max}\n Min T = 2\n P (SideSet 1) = 0.5\n'
            + _RULE)


def test_appended_thermal_only_runs_read_the_last():
    data = parse_tem3p_result_file(_thermal(300) + _thermal(310))
    assert data['runs'] == 2
    assert data['thermal']['max_temperature'] == 310.0


def test_appended_thermo_elastic_runs_read_the_last():
    first = _HEADER + _thermal(300) + _ROW
    second = _HEADER + _thermal(310) + _ROW.replace('4e-07', '5e-07')
    data = parse_tem3p_result_file(first + second)
    assert data['runs'] == 2
    assert data['thermal']['max_temperature'] == 310.0
    assert data['elastic']['u_mag_max'] == 5e-07
    assert data['elastic']['block_id'] == 2


def test_elastic_after_thermal_only_is_indistinguishable_from_one_run():
    """The documented limit: an elastic-only run appended after a thermal-only
    one reads as one thermo-elastic run. The module's clean removes ResultFile
    before every fresh run, so this never reaches a result."""
    data = parse_tem3p_result_file(_thermal(300) + _HEADER + _ROW)
    assert data['runs'] == 1
    assert data['thermal'] is not None and data['elastic'] is not None


def test_empty_result_file():
    data = parse_tem3p_result_file('')
    assert data['runs'] == 0
    assert data['thermal'] is None and data['elastic'] is None


@pytest.mark.parametrize('text, message', [
    (_HEADER, 'writes exactly one'),
    (_HEADER + _ROW + _ROW, 'writes exactly one'),
    (_ROW, 'no header above it'),
    (_HEADER + '  1e-07 2e-07\n', 'header names 8 columns'),
])
def test_malformed_result_file_raises(text, message):
    with pytest.raises(ValueError, match=message):
        parse_tem3p_result_file(text)


def test_eigen_header_with_no_modes_is_an_empty_table():
    header = _HEADER.replace('#   ', '# Frequency [Hz]    ')
    data = parse_tem3p_result_file(header)
    assert data['eigen']['mode'].size == 0
    assert data['eigen']['frequency'].size == 0


# --------------------------------------------------------------------------- #
# Inputs: what Phase 2's injections and lint will read
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('path', INPUTS, ids=os.path.basename)
def test_fixture_inputs_round_trip(path):
    text = write_ace3p(parse_ace3p(_read(path)))
    assert write_ace3p(parse_ace3p(text)) == text


def test_problem_blocks_and_coupled_boundaries_are_addressable():
    """The leaves Phase 2 injects or checks are reachable through Section.find
    with an Id discriminator: Directory and Omega3PId on the coupled boundary,
    MeshDump's EMMeshInputDir, MeshFile."""
    srfcell = parse_ace3p(_read(SRFCELL, 'inputs', 'SRFCell.tem3p'))
    elastic = srfcell.find('ElasticProblem')
    lfd = elastic.find('Boundary', Id='5')
    assert lfd.get_leaf('ConditionType') == 'LFDetuning'
    assert lfd.get_leaf('Directory') == 'omega3p_results'
    assert lfd.get_leaf('Omega3PId') == '6'
    assert elastic.find('MeshDump').get_leaf('EMMeshInputDir') == 'omega3p_results'
    assert elastic.get_leaf('MeshFile') == 'SRFCellShell.ncdf'

    rfgun = parse_ace3p(_read(RFGUN, 'inputs', 'RfGunBody.tem3p'))
    assert rfgun.find('ThermostaticProblem').find(
        'Boundary', Id='6').get_leaf('ConditionType') == 'RFHeating'
    assert rfgun.find('ElasticProblem').find(
        'Boundary', Id='6').get_leaf('ConditionType') == 'LFDetuning'

    coupler = parse_ace3p(_read(COUPLER, 'inputs', 'FPC-Body.tem3p'))
    thermal = coupler.find('ThermostaticProblem')
    assert len(thermal.children('HeatSource')) == 1
    assert {b.get_leaf('Directory') for b in thermal.children('Boundary')
            if b.get_leaf('ConditionType') == 'RFHeating'} == {'s3p_results'}

    tesla = parse_ace3p(_read(TESLA, 'inputs', 'TeslaCavity.tem3p'))
    assert tesla.find('ElasticProblem').find('EigenSolver') is not None
