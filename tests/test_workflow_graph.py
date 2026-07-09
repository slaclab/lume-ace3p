"""Phase-2 tests: declarative Workflow build + DAG validation + evaluate.

Three groups (see docs/workflow_module_refactor_plan.md):

1. **Build / order** — a declared ``workflow:`` list is instantiated via the
   registry and topologically ordered by artifact edges, independent of the
   YAML list order. The two runnable multi-step chains
   (``track3p_source→particles→geant4`` and ``cubit→s3p→acdtool``) validate and
   order correctly.
2. **Validation errors** — invalid graphs fail with a clear message naming the
   missing/duplicate artifact: missing mesh source, acdtool before a solver,
   particles with no track3p source, geant4 with no particle source, two mesh
   sources.
3. **Legacy equivalence** — the three legacy chains, expressed as ``workflow:``
   lists, run end-to-end in dry-run and produce the same extracted output values
   and artifacts as the current ``S3PWorkflow`` / ``Omega3PWorkflow`` /
   ``Geant4Workflow`` single ``run()``, and match the Phase-0.5 baselines
   (the Geant4 ``particles.data`` digest is the real-compute check).
"""

import os

import numpy as np
import pytest

import baseline_utils as bu
from lume_ace3p.workflow_graph import (
    Workflow, WorkflowValidationError, _resolve_order, _build_entry,
)
from lume_ace3p.modules import (
    build_module, MESH, EM_SOLUTION, RF_POST, TRACK3P_PARTICLES,
    PARTICLE_SOURCE, DOSE_GRID, EDEP_GRID,
)
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.workflow import S3PWorkflow, Omega3PWorkflow, Geant4Workflow


# --------------------------------------------------------------------------- #
# Build / ordering
# --------------------------------------------------------------------------- #


def test_build_entry_requires_module_key():
    with pytest.raises(WorkflowValidationError):
        _build_entry({'journal': 'x.jou'})
    with pytest.raises(WorkflowValidationError):
        _build_entry('cubit')


def test_build_entry_strips_module_and_name():
    m = _build_entry({'module': 'cubit', 'name': 'mesher', 'journal': 'x.jou'})
    assert m.type == 'cubit'
    assert m.name == 'mesher'
    assert m.journal == 'x.jou'


def _types(modules):
    return [m.type for m in modules]


def test_order_cubit_s3p_acdtool():
    # Declared out of dependency order; must sort to cubit -> s3p -> acdtool.
    entries = [
        {'module': 'acdtool', 'input': 'x.rfpost'},
        {'module': 's3p', 'input': 'x.s3p'},
        {'module': 'cubit', 'journal': 'x.jou'},
    ]
    wf = Workflow(entries, workflow_params={'dry_run': True})
    assert _types(wf.modules) == ['cubit', 's3p', 'acdtool']


def test_order_track3p_particles_geant4():
    entries = [
        {'module': 'geant4', 'geant4_input': 'in.geant4'},
        {'module': 'particles', 'num_bins': 1, 'beta': [1.0],
         'work_function': 4.5, 'dt': 1e-10},
        {'module': 'track3p_source', 'file': 'dump.txt'},
    ]
    wf = Workflow(entries, workflow_params={'dry_run': True})
    assert _types(wf.modules) == ['track3p_source', 'particles', 'geant4']


def test_order_is_stable_tiebreak():
    # Two independent source modules keep their YAML relative order.
    modules = [build_module('mesh', {'file': 'm.ncdf'}),
               build_module('track3p_source', {'file': 'd.txt'})]
    ordered = _resolve_order(list(modules))
    assert _types(ordered) == ['mesh', 'track3p_source']


# --------------------------------------------------------------------------- #
# Validation errors — clear, artifact-naming messages
# --------------------------------------------------------------------------- #


def test_empty_workflow_rejected():
    with pytest.raises(WorkflowValidationError, match='no modules'):
        Workflow([], workflow_params={'dry_run': True})


def test_missing_mesh_source():
    with pytest.raises(WorkflowValidationError, match=f"'{MESH}'"):
        Workflow([{'module': 's3p', 'input': 'x.s3p'}],
                 workflow_params={'dry_run': True})


def test_acdtool_before_solver():
    # acdtool with no em_solution producer.
    with pytest.raises(WorkflowValidationError, match=f"'{EM_SOLUTION}'"):
        Workflow([{'module': 'acdtool', 'input': 'x.rfpost'}],
                 workflow_params={'dry_run': True})


def test_particles_no_track3p_source():
    with pytest.raises(WorkflowValidationError, match=f"'{TRACK3P_PARTICLES}'"):
        Workflow([{'module': 'particles', 'num_bins': 1, 'beta': [1.0],
                   'work_function': 4.5, 'dt': 1e-10}],
                 workflow_params={'dry_run': True})


def test_geant4_no_particle_source():
    with pytest.raises(WorkflowValidationError, match=f"'{PARTICLE_SOURCE}'"):
        Workflow([{'module': 'geant4', 'geant4_input': 'in.geant4'}],
                 workflow_params={'dry_run': True})


def test_two_mesh_sources():
    # cubit journal XOR mesh file — declaring both is a duplicate producer.
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 'mesh', 'file': 'prebuilt.ncdf'}]
    with pytest.raises(WorkflowValidationError, match=f"'{MESH}'.*more than one"):
        Workflow(entries, workflow_params={'dry_run': True})


def test_output_targets_absent_module(tmp_path):
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 's3p', 'input': 'x.s3p'}]
    wf = Workflow(entries,
                  workflow_params={'dry_run': True,
                                   'workdir': str(tmp_path / 'wd')},
                  output_spec={'R/Q': ['RoverQ', '0', 'RoQ']})  # needs acdtool
    with pytest.raises(WorkflowValidationError, match='acdtool'):
        wf.evaluate()


# --------------------------------------------------------------------------- #
# Legacy-chain equivalence (dry-run) + Phase-0.5 baseline diff
# --------------------------------------------------------------------------- #


def _stage(example):
    """Stage an example's files into a temp dir and chdir into it. Returns the
    dir; caller restores cwd."""
    return bu._stage_example(example)


def test_s3p_chain_matches_legacy(tmp_path):
    """cubit -> s3p, dry-run. The declared workflow reaches the solver step
    with a mesh present and returns the same NaN sentinel the legacy
    S3PWorkflow.evaluate produces for an S-parameter output."""
    staged = _stage('s3p_sweep')
    cwd = os.getcwd()
    os.chdir(staged)
    try:
        entries = [
            {'module': 'cubit', 'journal': 'bend-90degree.jou'},
            {'module': 's3p', 'input': 'bend-90degree.s3p', 'tasks': 16,
             'cores': 4, 'opts': '--cpu-bind=cores'},
        ]
        inputs = WorkflowInputs(cubit={'cornercut': 12.0, 'rcorner2': 4.0})
        wf = Workflow(entries,
                      workflow_params={'workdir': 'lume-ace3p_s3p_workdir',
                                       'workdir_mode': 'auto', 'dry_run': True},
                      inputs=inputs,
                      output_spec={'refl': {'module': 's3p',
                                            'quantity': 'S(0,0)'}})
        out = wf.evaluate([12.0, 4.0])

        # Mesh + em_solution artifacts present at the final step.
        assert MESH in wf.last_context.artifacts
        assert EM_SOLUTION in wf.last_context.artifacts
        # Workdir name matches the legacy auto-mode naming.
        assert wf.workdir == 'lume-ace3p_s3p_workdir_12.0_4.0'

        # Extracted value matches legacy S3PWorkflow dry-run evaluate (NaN).
        legacy = S3PWorkflow(
            {'workdir': 'legacy_s3p', 'workdir_mode': 'manual',
             'dry_run': True}, inputs)
        legacy.run(inputs, output_dict={'refl': 'S(0,0)'})
        legacy_out = legacy.evaluate({'refl': 'S(0,0)'})
        assert np.isnan(out['refl']).all()
        assert np.isnan(legacy_out['refl']).all()
    finally:
        os.chdir(cwd)


def test_omega3p_chain_matches_legacy(tmp_path):
    """cubit -> omega3p -> acdtool, dry-run. Extracted outputs match the legacy
    Omega3PWorkflow.evaluate (all NaN when acdtool is dry-run)."""
    staged = _stage('omega3p_sweep')
    cwd = os.getcwd()
    os.chdir(staged)
    try:
        output_spec = {
            'R/Q': ['RoverQ', '0', 'RoQ'],
            'Mode_freq': ['RoverQ', '0', 'Frequency'],
            'E_max': ['maxFieldsOnSurface', '6', 'Emax'],
            'loc_x': ['maxFieldsOnSurface', '6', 'Emax_location', 'x'],
        }
        entries = [
            {'module': 'cubit', 'journal': 'pillbox-rtop.jou'},
            {'module': 'omega3p', 'input': 'pillbox-rtop.omega3p', 'tasks': 12,
             'cores': 8, 'opts': '--cpu-bind=cores'},
            {'module': 'acdtool', 'input': 'pillbox-rtop.rfpost'},
        ]
        inputs = WorkflowInputs(cubit={'cav_radius': 90.0, 'ellipticity': 0.5})
        wf = Workflow(entries,
                      workflow_params={'workdir': 'lume-ace3p_omega3p_workdir',
                                       'workdir_mode': 'auto', 'dry_run': True},
                      inputs=inputs, output_spec=output_spec)
        out = wf.evaluate([90.0, 0.5])

        assert _types(wf.modules) == ['cubit', 'omega3p', 'acdtool']
        assert {MESH, EM_SOLUTION, RF_POST} <= set(wf.last_context.artifacts)
        assert wf.workdir == 'lume-ace3p_omega3p_workdir_90.0_0.5'

        legacy = Omega3PWorkflow(
            {'rfpost_input': 'pillbox-rtop.rfpost', 'workdir': 'legacy_o3p',
             'workdir_mode': 'manual', 'dry_run': True}, inputs, output_spec)
        legacy_out = legacy.run(inputs, output_dict=output_spec)
        for name in output_spec:
            assert np.isnan(out[name]) and np.isnan(legacy_out[name]), name
    finally:
        os.chdir(cwd)


def test_geant4_chain_matches_legacy_and_baseline(tmp_path):
    """track3p_source -> particles -> geant4, dry-run geant4 with real particle
    weighting. The declared workflow reproduces the legacy Geant4Workflow's
    particles.data byte-for-byte and matches the Phase-0.5 beta=40 digest."""
    # --- module path ---
    staged = _stage('geant4_track3p_beta')
    cwd = os.getcwd()
    os.chdir(staged)
    try:
        entries = [
            {'module': 'track3p_source', 'file': 'sample_track3p_particles.txt'},
            {'module': 'particles', 'impact_order': 1, 'impact_face_id': 6,
             'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
             'beta_input': 'beta', 'output_format': 'geant4',
             'output': 'particles.data'},
            {'module': 'geant4', 'geant4_input': 'input_7cell.geant4'},
        ]
        inputs = WorkflowInputs(cubit={'beta': 40.0})
        wf = Workflow(entries,
                      workflow_params={'workdir': 'lume-ace3p_geant4_workdir',
                                       'workdir_mode': 'auto', 'dry_run': True},
                      inputs=inputs)
        wf.evaluate([40.0])

        assert _types(wf.modules) == ['track3p_source', 'particles', 'geant4']
        assert wf.workdir == 'lume-ace3p_geant4_workdir_40.0'
        module_particles = os.path.join(wf.workdir, 'particles.data')
        assert os.path.isfile(module_particles)
        # dose/edep grid artifacts recorded in dry-run (named in the input file).
        assert {DOSE_GRID, EDEP_GRID} <= set(wf.last_context.artifacts)
        module_digest = bu.numeric_digest(module_particles)
    finally:
        os.chdir(cwd)

    # --- legacy path, same single point ---
    legacy_staged = _stage('geant4_track3p_beta')
    os.chdir(legacy_staged)
    try:
        wf_legacy = Geant4Workflow(
            {'geant4_input': 'input_7cell.geant4',
             'particle_input': 'sample_track3p_particles.txt',
             'particle_output': 'particles.data',
             'workdir': 'lume-ace3p_geant4_workdir', 'workdir_mode': 'auto',
             'dry_run': True},
            WorkflowInputs(cubit={'beta': 40.0}), None,
            particle_params={'impact_order': 1, 'impact_face_id': 6,
                             'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
                             'beta_input': 'beta', 'output_format': 'geant4'})
        wf_legacy.run(sweep_scalars=[40.0])
        legacy_particles = os.path.join('lume-ace3p_geant4_workdir_40.0',
                                        'particles.data')
        assert os.path.isfile(legacy_particles)
        legacy_digest = bu.numeric_digest(legacy_particles)
    finally:
        os.chdir(cwd)

    # module path == legacy path
    ok, msg = bu.compare_digests(legacy_digest, module_digest)
    assert ok, f'module vs legacy particles.data: {msg}'

    # module path == Phase-0.5 frozen baseline
    baseline = bu.load_json(os.path.join(
        bu.BASELINE_DIR, 'geant4_track3p_beta', 'particles_beta40.digest.json'))
    ok, msg = bu.compare_digests(baseline, module_digest)
    assert ok, f'module vs Phase-0.5 baseline: {msg}'


# --------------------------------------------------------------------------- #
# from_config
# --------------------------------------------------------------------------- #


def test_from_config_builds_ordered_workflow():
    data = {
        'workflow_parameters': {'workdir_mode': 'manual', 'dry_run': True},
        'workflow': [
            {'module': 's3p', 'input': 'x.s3p'},
            {'module': 'cubit', 'journal': 'x.jou'},
        ],
        'input_parameters': {'cornercut': 15.0},
    }
    wf = Workflow.from_config(data)
    assert _types(wf.modules) == ['cubit', 's3p']
    assert wf.inputs.cubit['cornercut'] == 15.0


def test_from_config_requires_workflow_list():
    with pytest.raises(WorkflowValidationError, match="no 'workflow:'"):
        Workflow.from_config({'workflow_parameters': {}})


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
