"""The TEM3P problem types beyond LFD (Phase 4 of ``plans/tem3p_module_plan.md``).

Three Phase 0 cases, each run through a workflow on fake binaries that replay
the fixture outputs:

* **RfGun**: thermo-elastic (Robin cooling, RF heating and LFD in one input),
  ``omega3p -> tem3p -> omega3p_deformed``, with per-problem quantities and the
  derived frequency shift;
* **SrfCoupler**: S3P upstream, the nonlinear thermal solve, and the k(T) and
  Rs(T) tables named ``functions/<name>``, staged by a ``files:`` directory;
* **TeslaCavity**: mechanical eigenmodes, so the table goes long over ``Mode``.

Also covers the directory form of a solver's ``files:`` that SrfCoupler needs,
and the build-time check on the table files a TEM3P input names.
"""

import os
import shutil

import pandas as pd
import pytest

from lume_ace3p import modes
from lume_ace3p.ace3p import parse_ace3p
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.logs import ToolExitError
from lume_ace3p.modules import RunContext, _stage_dir, build_module
from lume_ace3p.workflow_graph import Workflow, WorkflowValidationError

from test_resume import _fake_ace3p, posix_only
from test_tem3p_module import (
    FIXTURES, _OMEGA3P, _TEM3P, _fixed, _fixture, _journal, _lfd, _stage,
)

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

# The Omega3P fake of test_tem3p_module, except that a results directory the
# replayed case holds an omega3p.out for gets that file, so the derived
# frequency shift is the real one.
_OMEGA3P_REPLAY = _OMEGA3P.replace(
    "printf 'Mode : {",
    'if [ -f "$FAKE_OMEGA3P_FIXTURE/$dir/omega3p.out" ]; then\n'
    '  cp "$FAKE_OMEGA3P_FIXTURE/$dir/omega3p.out" "$dir/omega3p.out"\n'
    'else\n'
    "  printf 'Mode : {", 1).replace(
    '> "$dir/omega3p.out"\n', '> "$dir/omega3p.out"\nfi\n', 1)

# 's3p <input> [dir]': one scan point, and the VECTOR/postprocess.in a real S3P
# run writes (job 44528190), naming its mesh by absolute path. That file is what
# the tem3p pre-flight reads.
_S3P = """#!/bin/sh
dir=${2:-s3p_results}
mkdir -p "$dir/VECTOR"
{ echo '#Index mapping:'
  echo '#          0 : Port 4, Mode 0, Type: TEM (cutoff: 0.0e+00 Hz)'
  echo '#Frequency[Hz]          S(0,0)'
  echo '1.30000000e+09  2.00000000e-02'
} > "$dir/Reflection.out"
{ echo '#Frequency[Hz]          S(0,0)'
  echo '1.30000000e+09  ( 2.00000000e-02,  0.00000000e+00)'
} > "$dir/SParameter.out"
mesh=`grep -E '^[ \t]*File' "$1" | head -1 | sed 's/.*:[ \t]*//; s/[ \t]*$//'`
printf 'ModelInfo : {\\n  File : %s/%s\\n}\\n' "$PWD" "$mesh" \
  > "$dir/VECTOR/postprocess.in"
echo "s3p: wrote $dir"
"""


def _install(root, case, **fakes):
    """The fake binaries under ``root/bin``, with ``fakes`` ({name: script})
    over the defaults, replaying fixture ``case``."""
    paths = _fake_ace3p(root)
    for name, body in {'omega3p': _OMEGA3P_REPLAY, 'tem3p': _TEM3P,
                       **fakes}.items():
        script = root / 'bin' / name
        script.write_text(body)
        script.chmod(0o755)
    os.environ['FAKE_TEM3P_FIXTURE'] = os.path.join(FIXTURES, case,
                                                    'tem3p_results')
    os.environ['FAKE_OMEGA3P_FIXTURE'] = os.path.join(FIXTURES, case)
    return paths


@pytest.fixture(autouse=True)
def _fake_env(monkeypatch):
    """Undo _install's environment after each test."""
    monkeypatch.delenv('FAKE_TEM3P_FIXTURE', raising=False)
    monkeypatch.delenv('FAKE_OMEGA3P_FIXTURE', raising=False)


def _run(root, paths, entries, output_spec, derived_spec=None, **params):
    return Workflow(
        entries,
        workflow_params={'workdir': str(root / 'wd'), 'dry_run': False,
                         'paths': paths, **params},
        output_spec=output_spec, derived_spec=derived_spec).evaluate()


def _echo(root, job='tem3p_results'):
    """The input the fake tem3p was given, parsed."""
    return parse_ace3p((root / 'wd' / job / 'input.echo').read_text())


# --------------------------------------------------------------------------- #
# RfGun: thermo-elastic, two coupled problems, a real frequency shift
# --------------------------------------------------------------------------- #


def _rfgun(root):
    root.mkdir(parents=True, exist_ok=True)
    _journal(root / 'vacuum.jou', 'RfGunVacuum.ncdf')
    _journal(root / 'body.jou', 'RfGunBody.ncdf')
    for name in ('RfGunVacuum.omega3p', 'DeformedRfGunVacuum.omega3p'):
        (root / name).write_text(_fixture('rfgun', name))
    (root / 'RfGunBody.tem3p').write_text(
        _fixed(_fixture('rfgun', 'RfGunBody.tem3p')))
    entries = _lfd(tem3p='RfGunBody.tem3p', em_input='RfGunVacuum.omega3p')
    entries[-1]['input'] = 'DeformedRfGunVacuum.omega3p'
    return root, entries


_RFGUN_OUTPUTS = {
    'f0': {'module': 'omega3p', 'quantity': 'Frequency', 'at': {'mode': 0}},
    'f1': {'module': 'omega3p_deformed', 'quantity': 'Frequency',
           'at': {'mode': 0}},
    't_max': 'max_temperature',
    'p_cooling': {'quantity': 'sideset_power', 'at': {'sideset': 5}},
    'umax': 'u_mag_max',
    'heat_scale': {'quantity': 'scaling_factor', 'at': {'problem': 'thermal'}},
    'lf_scale': {'quantity': 'scaling_factor', 'at': {'problem': 'elastic'}},
    'dofs_elastic': {'module': 'tem3p', 'quantity': 'n_dofs',
                     'at': {'problem': 'elastic'}},
}


@posix_only
def test_rfgun_chain_runs_with_both_problems(tmp_path, monkeypatch):
    root, entries = _rfgun(tmp_path)
    monkeypatch.chdir(root)
    paths = _install(root, 'rfgun')
    outputs, ctx = _run(root, paths, entries, _RFGUN_OUTPUTS,
                        derived_spec={'df': 'f1 - f0'})

    # P7: the archived +47.4 kHz, from the two real omega3p.out files.
    assert outputs['df'] == pytest.approx(47427.3, abs=0.1)
    assert outputs['t_max'] == pytest.approx(28.595)
    assert outputs['p_cooling'] == pytest.approx(213.111)
    assert outputs['heat_scale'] == pytest.approx(41050781.146822527)
    assert outputs['dofs_elastic'] == 1013412
    assert outputs['umax'] > 0
    assert outputs['lf_scale'] > 0 and outputs['lf_scale'] != \
        outputs['heat_scale']

    # Both problems' RF boundaries read the bound run, and the deformed mesh
    # starts from it.
    echo = _echo(root)
    for block in ('ThermostaticProblem', 'ElasticProblem'):
        assert echo.find(block).find('Boundary', Id='6').get_leaf(
            'Directory') == 'omega3p_results'
    assert echo.find('ElasticProblem').find('MeshDump').get_leaf(
        'EMMeshInputDir') == 'omega3p_results'
    # The CW23 deformed input already names tem3p's mesh, and is kept.
    deformed = parse_ace3p(
        (root / 'wd' / 'DeformedRfGunVacuum.omega3p').read_text())
    assert deformed.find('ModelInfo').get_leaf('File') == \
        './tem3p_results/DeformedVacuumMesh.ncdf'


@posix_only
def test_rfgun_scaling_factor_needs_a_problem(tmp_path, monkeypatch):
    """Two problems log a factor each, so an unnarrowed request fails the
    point rather than picking one."""
    root, entries = _rfgun(tmp_path)
    monkeypatch.chdir(root)
    paths = _install(root, 'rfgun')
    with pytest.raises(ValueError, match='both problems'):
        _run(root, paths, entries, {'lf': 'scaling_factor'})


def test_rfgun_needs_its_em_run(tmp_path, monkeypatch):
    """Both problems couple to RF, so both are named when nothing is bound."""
    root, entries = _rfgun(tmp_path)
    monkeypatch.chdir(root)
    entries = [e for e in entries if e['module'] == 'cubit'] + [
        {'module': 'tem3p', 'input': 'RfGunBody.tem3p',
         'from': {'mesh': 'body'}}]
    with pytest.raises(WorkflowValidationError) as excinfo:
        Workflow(entries, workflow_params={'dry_run': True,
                                           'workdir': str(root / 'wd')})
    message = str(excinfo.value)
    assert 'ThermostaticProblem Boundary Id 6 (RFHeating)' in message
    assert 'ElasticProblem Boundary Id 6 (LFDetuning)' in message


# --------------------------------------------------------------------------- #
# SrfCoupler: S3P upstream, nonlinear, functions/ staged by files:
# --------------------------------------------------------------------------- #


def _coupler(root, files=('functions',)):
    _stage(root, tem3p='FPC-Body.tem3p', case='srfcoupler',
           vacuum='2D-TTF3-FPC-Vacuum.ncdf', body='2D-TTF3-FPC-Body.ncdf',
           em_input='FPC-Vacuum.s3p')
    extra = {'files': list(files)} if files is not None else {}
    return _lfd(tem3p='FPC-Body.tem3p', em='s3p', em_input='FPC-Vacuum.s3p',
                deformed=False, **extra)


_COUPLER_OUTPUTS = {
    't_max': 'max_temperature',
    'newton': 'newton_iterations',
    'scale': 'scaling_factor',
    'p_70k': {'quantity': 'sideset_power', 'at': {'sideset': 4}},
}


@posix_only
@pytest.mark.parametrize('stage_mode', ['copy', 'symlink', 'hardlink'])
def test_srfcoupler_chain_runs_with_its_tables(tmp_path, monkeypatch,
                                               stage_mode):
    root = tmp_path
    entries = _coupler(root)
    monkeypatch.chdir(root)
    paths = _install(root, 'srfcoupler', s3p=_S3P)
    outputs, _ = _run(root, paths, entries, _COUPLER_OUTPUTS,
                      stage_mode=stage_mode)

    assert outputs['t_max'] == pytest.approx(324.352)
    assert outputs['newton'] == 9
    assert outputs['scale'] == 437.5
    assert outputs['p_70k'] == pytest.approx(0.781339)

    staged = root / 'wd' / 'functions'
    assert sorted(os.listdir(staged)) == sorted(
        os.listdir(root / 'functions'))
    assert staged.is_symlink() is (stage_mode == 'symlink')
    if stage_mode == 'hardlink':
        assert os.path.samefile(staged / 'NbTi', root / 'functions' / 'NbTi')
    elif stage_mode == 'copy':
        assert not os.path.samefile(staged / 'NbTi',
                                    root / 'functions' / 'NbTi')

    # The heat source and the four RF boundaries all read the S3P run, and
    # nothing writes a deformed mesh.
    echo = _echo(root)
    thermal = echo.find('ThermostaticProblem')
    assert thermal.find('HeatSource', Id='5').get_leaf('Directory') == \
        's3p_results'
    for ident in ('6', '7', '8', '9'):
        assert thermal.find('Boundary', Id=ident).get_leaf('Directory') == \
            's3p_results'


@posix_only
def test_srfcoupler_without_its_tables_cannot_run(tmp_path, monkeypatch):
    """What the build check prevents: past it (the tables are removed after
    the workflow is built), the fake, like TEM3P, has no table to read."""
    root = tmp_path
    entries = _coupler(root)
    monkeypatch.chdir(root)
    paths = _install(root, 'srfcoupler', s3p=_S3P)
    wf = Workflow(entries, workflow_params={'workdir': str(root / 'wd'),
                                            'dry_run': False, 'paths': paths},
                  output_spec=_COUPLER_OUTPUTS)
    shutil.rmtree(root / 'functions')
    (root / 'functions').mkdir()
    with pytest.raises(ToolExitError, match='tem3p exited with status 3'):
        wf.evaluate()
    assert 'cannot read functions/CURRR100' in \
        (root / 'wd' / 'tem3p.log').read_text()
    assert not (root / 'wd' / 'tem3p_results' / 'ResultFile').exists()


@posix_only
def test_a_fresh_run_restages_an_edited_table(tmp_path, monkeypatch):
    root = tmp_path
    entries = _coupler(root)
    monkeypatch.chdir(root)
    paths = _install(root, 'srfcoupler', s3p=_S3P)
    _run(root, paths, entries, _COUPLER_OUTPUTS)
    (root / 'functions' / 'NbTi').write_text('edited\n')
    _run(root, paths, entries, _COUPLER_OUTPUTS)
    assert (root / 'wd' / 'functions' / 'NbTi').read_text() == 'edited\n'


# ---- the build check on table files ---------------------------------------


def _build(root, entries):
    return Workflow(entries, workflow_params={'dry_run': True,
                                              'workdir': str(root / 'wd')})


def test_cw23_tables_without_files_are_one_error(tmp_path, monkeypatch):
    entries = _coupler(tmp_path, files=None)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WorkflowValidationError) as excinfo:
        _build(tmp_path, entries)
    message = str(excinfo.value)
    assert message.startswith('1 problem(s) found before running')
    assert ("10 table entries name files in the directory 'functions' "
            "(ThermostaticProblem ThermalConductivity Id 221: "
            "'Function: functions/CURRR100', …)") in message
    assert "Add 'files: [functions]'" in message


def test_listing_the_table_files_themselves_is_not_enough(tmp_path,
                                                          monkeypatch):
    """A file entry lands as its basename, not as functions/<name>."""
    names = sorted(os.listdir(os.path.join(FIXTURES, 'srfcoupler', 'inputs',
                                           'functions')))
    entries = _coupler(tmp_path, files=[f'functions/{n}' for n in names])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WorkflowValidationError,
                       match="stages a directory 'functions'"):
        _build(tmp_path, entries)


def test_bare_table_names_with_their_files_pass(tmp_path, monkeypatch):
    names = sorted(os.listdir(os.path.join(FIXTURES, 'srfcoupler', 'inputs',
                                           'functions')))
    entries = _coupler(tmp_path, files=[f'functions/{n}' for n in names])
    body = tmp_path / 'FPC-Body.tem3p'
    body.write_text(body.read_text().replace('functions/', ''))
    monkeypatch.chdir(tmp_path)
    _build(tmp_path, entries)


def test_a_bare_table_name_needs_its_file(tmp_path, monkeypatch):
    entries = _coupler(tmp_path)
    body = tmp_path / 'FPC-Body.tem3p'
    body.write_text(body.read_text().replace('functions/NbTi', 'NbTi'))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WorkflowValidationError) as excinfo:
        _build(tmp_path, entries)
    message = str(excinfo.value)
    assert message.startswith('1 problem(s)')
    assert ("ThermalConductivity Id 2: 'Function: NbTi' names a table TEM3P "
            "reads from its working directory, and no 'files:' entry puts "
            "'NbTi' there") in message


def test_a_table_missing_from_the_staged_directory(tmp_path, monkeypatch):
    entries = _coupler(tmp_path)
    os.remove(tmp_path / 'functions' / 'al300')
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WorkflowValidationError,
                       match=r"Id 5: 'Function: functions/al300': the staged "
                             r"directory 'functions' has no 'al300'"):
        _build(tmp_path, entries)


def test_trailing_separator_and_numeric_values(tmp_path, monkeypatch):
    """'functions/' names the same directory; a numeric SurfaceResistance is
    a value, not a file."""
    entries = _coupler(tmp_path, files=['functions/'])
    body = tmp_path / 'FPC-Body.tem3p'
    body.write_text(body.read_text().replace(
        'SurfaceResistance: functions/SRinnerNbTi', 'SurfaceResistance: 1e-8'))
    monkeypatch.chdir(tmp_path)
    _build(tmp_path, entries)


def test_a_missing_files_directory_is_reported_once(tmp_path, monkeypatch):
    """A files: entry that is neither file nor directory is check_files' to
    report; the table check stays quiet about it."""
    entries = _coupler(tmp_path)
    shutil.rmtree(tmp_path / 'functions')
    monkeypatch.chdir(tmp_path)
    wf = Workflow(entries, workflow_params={'dry_run': False,
                                            'workdir': str(tmp_path / 'wd')})
    with pytest.raises(WorkflowValidationError,
                       match=r"1 file\(s\).*\n    tem3p: functions$"):
        wf.check_files()


def test_an_existing_files_directory_passes_check_files(tmp_path,
                                                        monkeypatch):
    entries = _coupler(tmp_path)
    monkeypatch.chdir(tmp_path)
    Workflow(entries, workflow_params={'dry_run': False,
                                       'workdir': str(tmp_path / 'wd')}
             ).check_files()


# ---- _stage_dir ------------------------------------------------------------


@pytest.mark.parametrize('name', ['.', '..', './', os.sep])
def test_a_directory_with_no_name_is_refused(tmp_path, monkeypatch, name):
    """Its basename would be the workdir itself, which a fresh re-stage would
    remove."""
    monkeypatch.chdir(tmp_path)
    ctx = RunContext(str(tmp_path / 'wd'))
    with pytest.raises(ValueError, match='no name of its own'):
        _stage_dir(ctx, name, fresh=True)
    assert (tmp_path / 'wd').is_dir()


def test_a_symlinked_directory_is_kept_on_a_fresh_run(tmp_path):
    src = tmp_path / 'functions'
    src.mkdir()
    (src / 'k').write_text('1\n')
    ctx = RunContext(str(tmp_path / 'wd'), stage_mode='symlink')
    dest = _stage_dir(ctx, str(src))
    assert os.path.islink(dest)
    assert _stage_dir(ctx, str(src), fresh=True) == dest
    assert os.path.islink(dest)


def test_a_solver_files_directory_is_staged_for_any_solver(tmp_path,
                                                           monkeypatch):
    """The directory form belongs to every solver's files:, not only tem3p."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'tables').mkdir()
    (tmp_path / 'tables' / 'sey.dat').write_text('0 0\n')
    (tmp_path / 'cav.ncdf').write_text('')
    (tmp_path / 'cav.omega3p').write_text('ModelInfo : {\n  File : cav.ncdf\n}\n')
    Workflow([{'module': 'mesh', 'file': 'cav.ncdf'},
              {'module': 'omega3p', 'input': 'cav.omega3p',
               'files': ['tables']}],
             workflow_params={'dry_run': True,
                              'workdir': str(tmp_path / 'wd')}).evaluate()
    assert (tmp_path / 'wd' / 'tables' / 'sey.dat').is_file()
    assert build_module('omega3p', {'input': 'cav.omega3p',
                                    'files': ['tables']}).required_files() == \
        ['cav.omega3p']


# --------------------------------------------------------------------------- #
# TeslaCavity: mechanical eigenmodes, a table long over Mode
# --------------------------------------------------------------------------- #

_TESLA_JOURNAL = """## the body mesh, its swept size on an APREPRO line
#{radius=1.0}
create brick x {radius}
export genesis "TeslaCavity.ncdf" overwrite
"""

_TESLA_OUTPUTS = {
    'f_mech': 'mechanical_frequency',
    'umax': 'u_mag_max',
    'f_first': {'quantity': 'mechanical_frequency', 'at': {'mode': 1}},
}


def _tesla(root, radii, outputs=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'body.jou').write_text(_TESLA_JOURNAL)
    (root / 'TeslaCavity.tem3p').write_text(
        _fixture('teslacavity', 'TeslaCavity.tem3p'))
    paths = _install(root, 'teslacavity')
    return Workflow(
        [{'module': 'cubit', 'journal': 'body.jou', 'meshconvert': False},
         {'module': 'tem3p', 'input': 'TeslaCavity.tem3p'}],
        workflow_params={'workdir': str(root / 'wd'),
                         'workdir_mode': 'indexed', 'dry_run': False,
                         'paths': paths},
        inputs=WorkflowInputs(cubit={'radius': list(radii)}),
        output_spec=outputs or _TESLA_OUTPUTS)


_TESLA_FREQUENCIES = [62.822941, 167.12759, 242.46092, 307.64515, 468.56376]


@posix_only
def test_tesla_sweep_is_long_over_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    table = modes.parameter_sweep(_tesla(tmp_path, [1.0, 2.0]))
    assert list(table.columns) == ['radius', 'Mode', 'f_mech', 'umax',
                                   'f_first']
    assert table['Mode'].tolist() == [1, 2, 3, 4, 5] * 2
    assert table['radius'].tolist() == [1.0] * 5 + [2.0] * 5
    assert table['f_mech'].tolist() == pytest.approx(_TESLA_FREQUENCIES * 2)
    assert table['umax'].iloc[0] == pytest.approx(0.44516552)
    # A narrowed output is one value per point, repeated down its rows.
    assert table['f_first'].tolist() == pytest.approx([62.822941] * 10)
    # An eigen run hands on no mesh, and needs no EM run.
    assert not (tmp_path / 'wd_0' / 'tem3p_results' /
                'DeformedVacuumMesh.ncdf').exists()


@posix_only
def test_tesla_table_is_wide_when_every_output_is_narrowed(tmp_path,
                                                           monkeypatch):
    monkeypatch.chdir(tmp_path)
    table = modes.parameter_sweep(_tesla(
        tmp_path, [1.0, 2.0],
        outputs={'f2': {'quantity': 'mechanical_frequency',
                        'at': {'mode': 2}}}))
    assert len(table) == 2
    assert 'Mode' not in table.columns
    assert table['f2'].tolist() == pytest.approx([167.12759] * 2)


@posix_only
def test_tesla_resume_keeps_the_mode_axis(tmp_path, monkeypatch):
    """The mode count is a parsed result, so a resumed point must re-parse to
    keep its five rows."""
    monkeypatch.chdir(tmp_path)
    first = modes.parameter_sweep(_tesla(tmp_path, [1.0, 2.0]))
    log = tmp_path / 'wd_0' / 'tem3p_results' / 'input.echo'
    stamp = log.stat().st_mtime_ns
    resumed = modes.parameter_sweep(_tesla(tmp_path, [1.0, 2.0]), resume=True)
    pd.testing.assert_frame_equal(first, resumed)
    assert log.stat().st_mtime_ns == stamp
