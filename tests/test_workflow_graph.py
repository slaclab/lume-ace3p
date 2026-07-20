"""Declarative Workflow build + DAG validation + evaluate tests.

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
3. **Chain evaluate** — the three chains, expressed as ``workflow:`` lists, run
   end-to-end in dry-run and produce the expected extracted output values and
   artifacts (S3P/Omega3P: NaN under dry-run; Geant4: the real-compute
   ``particles.data`` digest matches the Phase-0.5 baseline).
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


def test_s3p_chain_evaluate(tmp_path):
    """cubit -> s3p, dry-run. The declared workflow reaches the solver step with
    a mesh present and returns the NaN sentinel for an S-parameter output."""
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
        # Auto-mode workdir name suffixes the swept scalars.
        assert wf.workdir == 'lume-ace3p_s3p_workdir_12.0_4.0'
        # Extracted value under dry-run is the NaN sentinel.
        assert np.isnan(out['refl']).all()
    finally:
        os.chdir(cwd)


def test_omega3p_chain_evaluate(tmp_path):
    """cubit -> omega3p -> acdtool, dry-run. Extracted outputs are all NaN when
    acdtool is dry-run; the chain orders and reaches rf_post."""
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
        for name in output_spec:
            assert np.isnan(out[name]), name
    finally:
        os.chdir(cwd)


def test_geant4_chain_evaluate_and_baseline(tmp_path):
    """track3p_source -> particles -> geant4, dry-run geant4 with real particle
    weighting. The declared workflow produces particles.data whose numeric digest
    matches the Phase-0.5 beta=40 baseline (the real-compute equivalence check)."""
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

    # module path == Phase-0.5 frozen baseline (real-compute particles.data)
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


# --------------------------------------------------------------------------- #
# _materialize routes dict overrides to the declaring bucket (optimize path)
# --------------------------------------------------------------------------- #


def _mixed_workflow():
    """A dry-run cubit->s3p workflow whose inputs span cubit + an ace3p leaf, so
    a VOCS-style override dict can be routed across buckets."""
    from lume_ace3p.ace3p import Section
    ace = Section()
    fs = Section()
    fs.append('Start', '9.4e9')
    ace.append('FrequencyScan', fs)
    inputs = WorkflowInputs(cubit={'cornercut': 14.0}, ace3p=ace)
    entries = [
        {'module': 'cubit', 'journal': 'x.jou'},
        {'module': 's3p', 'input': 'x.s3p'},
    ]
    return Workflow(entries,
                    workflow_params={'workdir_mode': 'manual', 'dry_run': True},
                    inputs=inputs,
                    output_spec={'refl': {'module': 's3p', 'quantity': 'S(0,0)'}})


def test_materialize_routes_ace3p_override_to_ace3p_bucket():
    wf = _mixed_workflow()
    materialized, sweep = wf._materialize({'ace3p:FrequencyScan.Start': '12e9'})
    assert sweep is None
    fs = [c for k, c in materialized.ace3p.entries if k == 'FrequencyScan'][0]
    assert fs.entries == [('Start', '12e9')]
    # cubit base value is preserved (not overwritten by the ace3p override).
    assert materialized.cubit['cornercut'] == 14.0


def test_materialize_routes_bare_cubit_override():
    wf = _mixed_workflow()
    materialized, _ = wf._materialize({'cornercut': 15.0})
    assert materialized.cubit['cornercut'] == 15.0


# --------------------------------------------------------------------------- #
# stage_mode — validated at build, propagated into the RunContext
# --------------------------------------------------------------------------- #


def test_stage_mode_defaults_to_copy():
    wf = Workflow([{'module': 'track3p_source', 'file': 'dump.txt'}],
                  workflow_params={'dry_run': True})
    assert wf.stage_mode == 'copy'


def test_stage_mode_invalid_rejected():
    with pytest.raises(ValueError, match='stage_mode'):
        Workflow([{'module': 'track3p_source', 'file': 'dump.txt'}],
                 workflow_params={'dry_run': True, 'stage_mode': 'bogus'})


def test_stage_mode_propagates_to_run_context(tmp_path):
    """The workflow's stage_mode reaches the per-evaluation RunContext, so every
    workdir stages with the configured strategy."""
    src = tmp_path / 'dump.txt'
    src.write_text('payload')
    entries = [{'module': 'track3p_source', 'file': str(src)}]
    wf = Workflow(entries,
                  workflow_params={'workdir_mode': 'manual',
                                   'workdir': str(tmp_path / 'wd'),
                                   'stage_mode': 'symlink'},
                  output_spec={})
    wf.evaluate(None)
    assert wf.last_context.stage_mode == 'symlink'
    staged = os.path.join(str(tmp_path / 'wd'), 'dump.txt')
    assert os.path.islink(staged)


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
