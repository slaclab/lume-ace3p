"""Phase-3 tests: the mode layer (``single`` + ``parameter_sweep``) driving a
declarative :class:`~lume_ace3p.workflow_graph.Workflow` and returning pandas
DataFrames (see docs/workflow_module_refactor_plan.md).

Verification (Phase 3 done when):

* ``parameter_sweep`` over each legacy chain (S3P long-format, Omega3P wide,
  Omega3P+ACE3P-axis wide, Geant4 beta-broadcast) produces a DataFrame whose
  numeric content matches the Phase-0.5 baseline sweep table (column *layout*
  may differ — clean break; we diff via ``baseline_utils.compare_tables`` which
  compares parsed column sets + numeric values).
* The Geant4 β-broadcast sweep (``beta_input``) runs through the mode and yields
  the expected per-point ``particles.data`` (digest match vs the frozen
  per-beta baselines).
* ``single`` mode round-trips one evaluation.

Modes are workflow-agnostic: every test drives the workflow only through
``run_mode`` / ``single`` / ``parameter_sweep`` — no solver-specific calls.
"""

import os

import numpy as np
import pandas as pd
import pytest

import baseline_utils as bu
from lume_ace3p.inputs import build_inputs, load_yaml, WorkflowInputs
from lume_ace3p.workflow_graph import Workflow
from lume_ace3p.modes import run_mode, single, parameter_sweep, write_table


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _staged(example, yaml_name):
    """Stage an example into a temp dir, chdir into it, and return
    ``(loaded_yaml_data, produced_inputs)``. Caller restores cwd."""
    staged = bu._stage_example(example)
    os.chdir(staged)
    data = load_yaml(yaml_name)
    return data, build_inputs(data)


def _build(entries, inputs, workdir, output_spec=None):
    """Build a dry-run declarative Workflow with auto workdir naming — the shape
    every sweep test uses."""
    return Workflow(entries,
                    workflow_params={'workdir': workdir, 'workdir_mode': 'auto',
                                     'dry_run': True},
                    inputs=inputs, output_spec=output_spec)


# --------------------------------------------------------------------------- #
# parameter_sweep — numeric match vs Phase-0.5 baselines
# --------------------------------------------------------------------------- #


def test_s3p_sweep_matches_baseline(tmp_path):
    """cubit -> s3p, dry-run. S3P exposes a Frequency field index, so the sweep
    is emitted long-format: one row per (cornercut, rcorner2, Frequency). The
    example declares no output_parameters, so the columns are exactly the
    swept inputs + Frequency — matching the legacy WriteS3PDataTable baseline."""
    cwd = os.getcwd()
    try:
        _data, inputs = _staged('s3p_sweep', 's3p_sweep.yaml')
        entries = [
            {'module': 'cubit', 'journal': 'bend-90degree.jou'},
            {'module': 's3p', 'input': 'bend-90degree.s3p', 'tasks': 16,
             'cores': 4, 'opts': '--cpu-bind=cores'},
        ]
        wf = _build(entries, inputs, 'lume-ace3p_s3p_workdir')
        df = parameter_sweep(wf)

        assert list(df.columns) == ['cornercut', 'rcorner2', 'Frequency']
        out = os.path.join(str(tmp_path), 's3p_sweep_output.txt')
        write_table(df, out)
        baseline = os.path.join(bu.BASELINE_DIR, 's3p_sweep',
                                's3p_sweep_output.txt')
        ok, msg = bu.compare_tables(baseline, out)
        assert ok, msg
    finally:
        os.chdir(cwd)


def test_omega3p_sweep_matches_baseline(tmp_path):
    """cubit -> omega3p -> acdtool, dry-run. Wide/scalar table: one row per
    (cav_radius, ellipticity) with NaN outputs (acdtool absent under dry-run),
    matching the legacy WriteOmega3PDataTable baseline."""
    cwd = os.getcwd()
    try:
        data, inputs = _staged('omega3p_sweep', 'omega3p_sweep.yaml')
        entries = [
            {'module': 'cubit', 'journal': 'pillbox-rtop.jou'},
            {'module': 'omega3p', 'input': 'pillbox-rtop.omega3p', 'tasks': 12,
             'cores': 8, 'opts': '--cpu-bind=cores'},
            {'module': 'acdtool', 'input': 'pillbox-rtop.rfpost'},
        ]
        wf = _build(entries, inputs, 'lume-ace3p_omega3p_workdir',
                    output_spec=data.get('output_parameters'))
        df = parameter_sweep(wf)

        assert list(df.columns) == [
            'cav_radius', 'ellipticity', 'R/Q', 'Mode_freq', 'E_max',
            'loc_x', 'loc_y', 'loc_z']
        assert len(df) == 16
        out = os.path.join(str(tmp_path), 'omega3p_sweep_output.txt')
        write_table(df, out)
        baseline = os.path.join(bu.BASELINE_DIR, 'omega3p_sweep',
                                'omega3p_sweep_output.txt')
        ok, msg = bu.compare_tables(baseline, out)
        assert ok, msg
    finally:
        os.chdir(cwd)


def test_omega3p_ace3p_axis_sweep_matches_baseline(tmp_path):
    """The ACE3P Sigma list [5.8e7, 1.04e7] is a third sweep axis (4x4x2 = 32
    runs). The mode iterates sweep_axes() generically, so the ACE3P axis rides
    alongside the two cubit axes with no special-casing — matching the legacy
    32-row baseline (and its ace3p: Sigma column)."""
    cwd = os.getcwd()
    try:
        data, inputs = _staged('omega3p_ace3p_param_sweep',
                               'omega3p_ace3p_param_sweep.yaml')
        entries = [
            {'module': 'cubit', 'journal': 'pillbox-rtop.jou'},
            {'module': 'omega3p', 'input': 'pillbox-rtop.omega3p', 'tasks': 12,
             'cores': 8, 'opts': '--cpu-bind=cores'},
            {'module': 'acdtool', 'input': 'pillbox-rtop.rfpost'},
        ]
        wf = _build(entries, inputs, 'lume-ace3p_omega3p_workdir',
                    output_spec=data.get('output_parameters'))
        df = parameter_sweep(wf)

        assert len(df) == 32  # 4 x 4 x 2
        assert 'ace3p:ModelInfo.SurfaceMaterial.Sigma' in df.columns
        out = os.path.join(str(tmp_path), 'omega3p_sweep_output.txt')
        write_table(df, out)
        baseline = os.path.join(bu.BASELINE_DIR, 'omega3p_ace3p_param_sweep',
                                'omega3p_sweep_output.txt')
        ok, msg = bu.compare_tables(baseline, out)
        assert ok, msg
    finally:
        os.chdir(cwd)


def test_geant4_beta_broadcast_sweep(tmp_path):
    """track3p_source -> particles -> geant4, dry-run geant4 with real particle
    weighting. 'beta_input' broadcasts the swept beta scalar to all 8 bins, so
    each grid point writes a distinct particles.data. The per-point beta=40 and
    beta=60 outputs must match the Phase-0.5 digests, proving the mode drives
    the beta-broadcast correctly end-to-end."""
    cwd = os.getcwd()
    try:
        _data, _inputs = _staged('geant4_track3p_beta',
                                 'geant4_track3p_beta.yaml')
        entries = [
            {'module': 'track3p_source',
             'file': 'sample_track3p_particles.txt'},
            {'module': 'particles', 'impact_order': 1, 'impact_face_id': 6,
             'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
             'beta_input': 'beta', 'output_format': 'geant4',
             'output': 'particles.data'},
            {'module': 'geant4', 'geant4_input': 'input_7cell.geant4'},
        ]
        # beta sweeps 40 -> 60 in 5 steps (the example's input_parameters).
        inputs = WorkflowInputs(cubit={'beta': np.linspace(40.0, 60.0, 5)})
        wf = _build(entries, inputs, 'lume-ace3p_geant4_workdir')
        df = parameter_sweep(wf)

        # One row per swept beta; the beta grid matches the baseline table.
        assert list(df.columns) == ['beta']
        assert df['beta'].tolist() == [40.0, 45.0, 50.0, 55.0, 60.0]

        # Per-point particle files land in per-beta workdirs and match the
        # frozen digests (the numeric proof the beta-broadcast reached Particles).
        for beta, fixture in ((40.0, 'particles_beta40.digest.json'),
                              (60.0, 'particles_beta60.digest.json')):
            produced = os.path.join(f'lume-ace3p_geant4_workdir_{beta}',
                                    'particles.data')
            assert os.path.isfile(produced), produced
            baseline = bu.load_json(os.path.join(
                bu.BASELINE_DIR, 'geant4_track3p_beta', fixture))
            ok, msg = bu.compare_digests(baseline, bu.numeric_digest(produced))
            assert ok, f'beta={beta}: {msg}'
    finally:
        os.chdir(cwd)


# --------------------------------------------------------------------------- #
# single — one evaluation round-trips
# --------------------------------------------------------------------------- #


def test_single_wide_round_trip(tmp_path):
    """single over cubit -> omega3p -> acdtool: one scalar point in, one row
    out, with the input knobs and (dry-run NaN) outputs as columns."""
    cwd = os.getcwd()
    try:
        data, _inputs = _staged('omega3p_sweep', 'omega3p_sweep.yaml')
        entries = [
            {'module': 'cubit', 'journal': 'pillbox-rtop.jou'},
            {'module': 'omega3p', 'input': 'pillbox-rtop.omega3p'},
            {'module': 'acdtool', 'input': 'pillbox-rtop.rfpost'},
        ]
        inputs = WorkflowInputs(cubit={'cav_radius': 95.0, 'ellipticity': 0.6})
        wf = Workflow(entries,
                      workflow_params={'workdir': str(tmp_path / 'wd'),
                                       'workdir_mode': 'manual',
                                       'dry_run': True},
                      inputs=inputs, output_spec=data.get('output_parameters'))
        df = single(wf)

        assert len(df) == 1
        assert df.loc[0, 'cav_radius'] == 95.0
        assert df.loc[0, 'ellipticity'] == 0.6
        assert np.isnan(df.loc[0, 'R/Q'])
    finally:
        os.chdir(cwd)


def test_single_s3p_long_round_trip(tmp_path):
    """single over cubit -> s3p: the frequency field index makes single emit one
    row per index value (here the dry-run [0.0] sentinel)."""
    cwd = os.getcwd()
    try:
        _data, _inputs = _staged('s3p_sweep', 's3p_sweep.yaml')
        entries = [
            {'module': 'cubit', 'journal': 'bend-90degree.jou'},
            {'module': 's3p', 'input': 'bend-90degree.s3p'},
        ]
        inputs = WorkflowInputs(cubit={'cornercut': 13.0, 'rcorner2': 5.0})
        wf = Workflow(entries,
                      workflow_params={'workdir': str(tmp_path / 'wd'),
                                       'workdir_mode': 'manual',
                                       'dry_run': True},
                      inputs=inputs,
                      output_spec={'refl': {'module': 's3p',
                                            'quantity': 'S(0,0)'}})
        df = single(wf)

        assert list(df.columns) == ['cornercut', 'rcorner2', 'Frequency', 'refl']
        assert len(df) == 1
        assert df.loc[0, 'Frequency'] == 0.0
        assert np.isnan(df.loc[0, 'refl'])
    finally:
        os.chdir(cwd)


# --------------------------------------------------------------------------- #
# run_mode dispatch + writer
# --------------------------------------------------------------------------- #


def test_run_mode_dispatch_and_write(tmp_path):
    """run_mode reads the mode type (target-schema 'type' key), drives the
    sweep, and writes the output file when one is named."""
    cwd = os.getcwd()
    try:
        _data, inputs = _staged('s3p_sweep', 's3p_sweep.yaml')
        entries = [
            {'module': 'cubit', 'journal': 'bend-90degree.jou'},
            {'module': 's3p', 'input': 'bend-90degree.s3p'},
        ]
        wf = _build(entries, inputs, 'lume-ace3p_s3p_workdir')
        out = os.path.join(str(tmp_path), 'out.txt')
        df = run_mode({'type': 'parameter_sweep', 'output_file': out}, wf)
        assert os.path.isfile(out)
        # The written file round-trips to the same frame.
        reread = pd.read_csv(out, sep='\t')
        assert list(reread.columns) == list(df.columns)
        assert len(reread) == len(df)
    finally:
        os.chdir(cwd)


def test_run_mode_rejects_xopt_mode(tmp_path):
    """Phase 3 handles only single | parameter_sweep; an Xopt mode is a clear
    error (those route through the legacy path until Phase 4)."""
    cwd = os.getcwd()
    try:
        _data, inputs = _staged('s3p_sweep', 's3p_sweep.yaml')
        entries = [{'module': 'cubit', 'journal': 'bend-90degree.jou'},
                   {'module': 's3p', 'input': 'bend-90degree.s3p'}]
        wf = _build(entries, inputs, 'lume-ace3p_s3p_workdir')
        with pytest.raises(ValueError, match='parameter_sweep'):
            run_mode({'type': 'scalar_optimize'}, wf)
    finally:
        os.chdir(cwd)


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
