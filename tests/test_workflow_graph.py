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
import warnings

import numpy as np
import pytest

import baseline_utils as bu
# The synthetic solver/acdtool fixtures live with the module-layer tests; the
# index-collision cases below need parsed results from two modules at once, and
# duplicating the fixtures here would let the two copies drift.
from test_modules import _make_acdtool, _make_s3p_solver
from lume_ace3p.modes import _rows_for_point
from lume_ace3p.workflow_graph import (
    Workflow, WorkflowValidationError, _resolve_order, _build_entry,
)
from lume_ace3p.modules import (
    build_module, MESH, EM_SOLUTION, TD_SOLUTION, RF_POST, TRACK3P_PARTICLES,
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


def test_order_cubit_t3p():
    """The T3P chain: declared out of order, must sort to cubit -> t3p."""
    entries = [{'module': 't3p', 'input': 'x.t3p'},
               {'module': 'cubit', 'journal': 'x.jou'}]
    wf = Workflow(entries, workflow_params={'dry_run': True})
    assert _types(wf.modules) == ['cubit', 't3p']


def test_acdtool_rf_after_t3p_is_rejected():
    """The em_solution / td_solution split doing its job: ``postprocess rf`` does
    RF postprocessing on a frequency-domain solution, so pointing it at T3P's
    time-domain output must fail validation rather than silently run.

    The guard is now per-command rather than blanket — see
    :func:`test_order_cubit_t3p_acdtool_transwake` — but it must survive for
    ``rf``, which is the command that really needs a frequency-domain solution."""
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 't3p', 'input': 'x.t3p'},
               {'module': 'acdtool', 'input': 'x.rfpost'}]
    with pytest.raises(WorkflowValidationError, match=f"'{EM_SOLUTION}'"):
        Workflow(entries, workflow_params={'dry_run': True})
    # Explicitly naming the command changes nothing.
    entries[2] = {'module': 'acdtool', 'input': 'x.rfpost',
                  'command': 'postprocess rf'}
    with pytest.raises(WorkflowValidationError, match=f"'{EM_SOLUTION}'"):
        Workflow(entries, workflow_params={'dry_run': True})


def test_order_cubit_t3p_acdtool_transwake():
    """The chain Phase 2 unblocks. ``postprocess transwake`` is a *time-domain*
    postprocessor, so it requires ``td_solution`` and the chain that used to be a
    WorkflowValidationError now orders cubit -> t3p -> acdtool.

    Declared out of dependency order, and with the T3P step's own YAML order
    reversed, to show the ordering comes from the artifact edges."""
    entries = [{'module': 'acdtool', 'command': 'postprocess transwake',
                'args': [0.0, 0.0, 0.0, 0.0125]},
               {'module': 't3p', 'input': 'x.t3p'},
               {'module': 'cubit', 'journal': 'x.jou'}]
    wf = Workflow(entries, workflow_params={'dry_run': True})
    assert _types(wf.modules) == ['cubit', 't3p', 'acdtool']


@pytest.mark.parametrize('command', ['postprocess transwake',
                                     'postprocess coaxsignal',
                                     'postprocess volmontomode'])
def test_time_domain_acdtool_commands_need_t3p_not_omega3p(command):
    """The three wired time-domain commands all require ``td_solution``: listed
    after an eigensolver instead of T3P, each fails naming that artifact."""
    args = [0.0, 0.0, 0.0, 0.0125] if command == 'postprocess transwake' else []
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 'omega3p', 'input': 'x.omega3p'},
               {'module': 'acdtool', 'command': command, 'args': args}]
    with pytest.raises(WorkflowValidationError, match=f"'{TD_SOLUTION}'"):
        Workflow(entries, workflow_params={'dry_run': True})

    entries[1] = {'module': 't3p', 'input': 'x.t3p'}
    wf = Workflow(entries, workflow_params={'dry_run': True})
    assert _types(wf.modules) == ['cubit', 't3p', 'acdtool']


def test_two_acdtool_steps_are_rejected():
    """Both provide ``rf_post``, so a chain wanting both an rf postprocess and a
    transwake is a duplicate producer today. Recorded rather than worked around:
    lifting it needs per-instance artifact identity, which design decision 3 puts
    out of scope for this plan."""
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 't3p', 'input': 'x.t3p'},
               {'module': 'acdtool', 'name': 'transwake',
                'command': 'postprocess transwake',
                'args': [0.0, 0.0, 0.0, 0.0125]},
               {'module': 'acdtool', 'name': 'coaxsignal',
                'command': 'postprocess coaxsignal'}]
    with pytest.raises(WorkflowValidationError,
                       match=f"'{RF_POST}'.*more than one"):
        Workflow(entries, workflow_params={'dry_run': True})


def test_t3p_and_s3p_together_is_allowed():
    """They provide different artifacts, so this is not a duplicate-producer
    error — a workflow may run both solvers on one mesh."""
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 's3p', 'input': 'x.s3p'},
               {'module': 't3p', 'input': 'x.t3p'}]
    wf = Workflow(entries, workflow_params={'dry_run': True})
    assert set(_types(wf.modules)) == {'cubit', 's3p', 't3p'}


def test_two_t3p_solvers_rejected():
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 't3p', 'input': 'a.t3p'},
               {'module': 't3p', 'input': 'b.t3p'}]
    with pytest.raises(WorkflowValidationError,
                       match=f"'{TD_SOLUTION}'.*more than one"):
        Workflow(entries, workflow_params={'dry_run': True})


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


def test_t3p_chain_evaluate(tmp_path):
    """cubit -> t3p, dry-run against the shipped example. Reaches the solver with
    a mesh present, records a td_solution, and returns the NaN sentinel."""
    staged = _stage('t3p_sweep')
    cwd = os.getcwd()
    os.chdir(staged)
    try:
        entries = [
            {'module': 'cubit', 'journal': 'pillboxwg.jou'},
            {'module': 't3p', 'input': 'pillboxwg-closed.t3p', 'tasks': 16,
             'cores': 16, 'opts': '--cpu-bind=cores'},
        ]
        inputs = WorkflowInputs(cubit={'cell_radius': 0.05,
                                       'iris_radius': 0.025})
        wf = Workflow(entries,
                      workflow_params={'workdir': 'lume-ace3p_t3p_workdir',
                                       'workdir_mode': 'auto', 'dry_run': True},
                      inputs=inputs,
                      output_spec={'k_loss': {'module': 't3p',
                                              'quantity': 'loss_factor'}})
        out = wf.evaluate([0.05, 0.025])

        assert MESH in wf.last_context.artifacts
        assert TD_SOLUTION in wf.last_context.artifacts
        # T3P must NOT masquerade as a frequency-domain solution.
        assert EM_SOLUTION not in wf.last_context.artifacts
        assert wf.workdir == 'lume-ace3p_t3p_workdir_0.05_0.025'
        assert np.isnan(out['k_loss']).all()
        # The wake coordinate is the field index, so a sweep goes long-format.
        assert wf.field_index()[0] == 's'
    finally:
        os.chdir(cwd)


def test_t3p_output_specs_route_to_t3p():
    """Bare T3P quantity names must reach the t3p module rather than falling
    through to the s3p default, and an 'at: {s: ...}' mapping likewise."""
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 't3p', 'input': 'x.t3p'}]
    specs = {
        'a': 'loss_factor',
        'b': ['kick_factor'],
        'c': {'quantity': 'W', 'at': {'s': 0.1}},
        'd': {'quantity': 'I_bunch'},
    }
    wf = Workflow(entries, workflow_params={'dry_run': True},
                  output_spec=specs)
    assert {name: m.type for name, m in wf.output_modules().items()} == {
        'a': 't3p', 'b': 't3p', 'c': 't3p', 'd': 't3p'}


def test_s3p_output_specs_still_route_to_s3p():
    """The router extension must not steal S3P's specs — 'at: {frequency: ...}'
    and S-parameter names stay with s3p."""
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 's3p', 'input': 'x.s3p'}]
    specs = {
        'a': 'S(0,0)',
        'b': ['S(1,1)'],
        'c': {'quantity': 'S(0,0)', 'at': {'frequency': 1.2e10}},
    }
    wf = Workflow(entries, workflow_params={'dry_run': True},
                  output_spec=specs)
    assert {name: m.type for name, m in wf.output_modules().items()} == {
        'a': 's3p', 'b': 's3p', 'c': 's3p'}


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


# --------------------------------------------------------------------------- #
# Output-spec routing + the two index collisions (Phase 4)
# --------------------------------------------------------------------------- #


def _acdtool_of(wf):
    return next(m for m in wf.modules if m.type == 'acdtool')


def test_acdtool_output_specs_route_to_acdtool():
    """Both acdtool spec forms reach the acdtool module: the mapping form by its
    ``section:`` key (no ``module:`` needed) and the deprecated positional form by
    its head. Routing is not translation, so asking *which* module owns a spec
    must not emit the deprecation — that belongs to the extraction."""
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 'omega3p', 'input': 'x.omega3p'},
               {'module': 'acdtool', 'input': 'x.rfpost'}]
    specs = {
        'a': ['RoverQ', '0', 'RoQ'],                       # deprecated list form
        'b': {'section': 'RoverQ', 'quantity': 'RoQ'},      # mapping, no module:
        'c': {'module': 'acdtool', 'section': 'maxFieldsOnSurface',
              'quantity': 'Emax', 'at': {'surface': 6}},
        'd': ['scaling', 'm_factor'],                      # a block CW23 never
    }                                                      # declares but every
    wf = Workflow(entries, workflow_params={'dry_run': True},  # run emits
                  output_spec=specs)
    with warnings.catch_warnings():
        warnings.simplefilter('error', DeprecationWarning)
        routed = {name: m.type for name, m in wf.output_modules().items()}
    assert routed == {'a': 'acdtool', 'b': 'acdtool', 'c': 'acdtool',
                      'd': 'acdtool'}


def test_s3p_acdtool_table_indexes_on_s3p_frequency(tmp_path):
    """Cross-module index collision (the CW23 ``window`` case): ``Frequency`` vs
    ``ModeID``. ``Workflow.field_index`` takes the first producer in resolved DAG
    order, so S3P wins — which is both the back-compatible answer and the right
    one, since that case is a frequency scan postprocessed at one ``FreqScanID``.
    acdtool's mode axis still exists; it rides as a field artifact."""
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 's3p', 'input': 'x.s3p'},
               {'module': 'acdtool', 'input': 'x.rfpost'}]
    wf = Workflow(entries,
                  workflow_params={'workdir': str(tmp_path / 'wd'),
                                   'dry_run': True},
                  output_spec={'S11': {'module': 's3p', 'quantity': 'S(0,0)'},
                               'R/Q': {'module': 'acdtool', 'section': 'RoverQ',
                                       'quantity': 'RoQ'}})
    wf.evaluate()
    assert _types(wf.modules) == ['cubit', 's3p', 'acdtool']

    # Give both modules real parsed results (the dry-run above ran no binary).
    s3p = next(m for m in wf.modules if m.type == 's3p')
    s3p._solver = _make_s3p_solver(wf.workdir)
    acdtool = _acdtool_of(wf)
    acdtool._acdtool = _make_acdtool(wf.workdir)

    label, values = wf.field_index()
    assert label == 'Frequency'
    assert len(values) == 3                       # the three swept frequencies
    # acdtool's own axis is ModeID, and it is NOT the table's...
    assert acdtool.field_index(wf.last_context)[0] == 'ModeID'
    # ...so the per-mode data is reachable as a field artifact instead.
    assert 'RoverQ' in acdtool.field(wf.last_context)


def test_omega3p_acdtool_table_indexes_on_modeid(tmp_path):
    """Intra-module collision — the shape ``examples/omega3p_sweep`` already has:
    one acdtool module supplying a mode-indexed section *and* a surface-indexed
    one. ``ModeID`` is the table axis, so ``RoverQ`` becomes one row per mode
    while ``maxFieldsOnSurface`` resolves to an ``at:``-narrowed scalar that
    repeats down the rows."""
    output_spec = {
        'R/Q': {'module': 'acdtool', 'section': 'RoverQ', 'quantity': 'RoQ'},
        'E_max': {'module': 'acdtool', 'section': 'maxFieldsOnSurface',
                  'quantity': 'Emax', 'at': {'surface': 6}},
    }
    entries = [{'module': 'cubit', 'journal': 'x.jou'},
               {'module': 'omega3p', 'input': 'x.omega3p'},
               {'module': 'acdtool', 'input': 'x.rfpost'}]
    wf = Workflow(entries,
                  workflow_params={'workdir': str(tmp_path / 'wd'),
                                   'dry_run': True},
                  output_spec=output_spec)
    wf.evaluate()
    acdtool = _acdtool_of(wf)
    acdtool._acdtool = _make_acdtool(wf.workdir)

    label, ids = wf.field_index()
    assert label == 'ModeID'
    assert list(ids) == [0, 1]
    outputs = {name: acdtool.extract(wf.last_context,
                                     {k: v for k, v in spec.items()
                                      if k != 'module'})
               for name, spec in output_spec.items()}
    # The result table the mode layer builds from that: one row per mode.
    rows = _rows_for_point(wf, ['cav_radius'], [90.0], outputs)
    assert [r['ModeID'] for r in rows] == [0, 1]
    assert [r['R/Q'] for r in rows] == pytest.approx([250.0, 40.0])
    assert [r['E_max'] for r in rows] == pytest.approx([1.5e6, 1.5e6])


def test_t3p_transwake_chain_evaluate(tmp_path):
    """cubit -> t3p -> acdtool(transwake), dry-run: the chain Phase 2 unblocks,
    end to end through ``evaluate``.

    The figure of merit is read by ``T3PModule``, not by acdtool — transwake
    overwrites T3P's own ``wakefield.out`` and ``parse_wakefield`` handles the
    transverse header — so the output spec names ``t3p``. Under dry-run it is the
    NaN sentinel; the real value path is
    ``test_modules.py::test_transwake_reparses_the_producer``."""
    entries = [
        {'module': 'cubit', 'journal': 'cavity.jou'},
        {'module': 't3p', 'input': 'cavity.t3p'},
        {'module': 'acdtool', 'name': 'transwake',
         'command': 'postprocess transwake', 'args': [0.0, 0.0, 0.0, 0.0125]},
    ]
    wf = Workflow(entries,
                  workflow_params={'workdir': str(tmp_path / 'wd'),
                                   'dry_run': True},
                  output_spec={'K': {'module': 't3p', 'quantity': 'kick_factor'}})
    out = wf.evaluate()

    assert _types(wf.modules) == ['cubit', 't3p', 'acdtool']
    assert {MESH, TD_SOLUTION, RF_POST} <= set(wf.last_context.artifacts)
    assert np.all(np.isnan(out['K']))
    # The jobname reached the acdtool step from the producing solver, not the YAML.
    assert wf.last_context.job_names[TD_SOLUTION] == 't3p_results'
    marker = open(os.path.join(wf.workdir, 'DRY_RUN.txt')).read()
    assert 'postprocess transwake' in marker


def test_geant4_chain_evaluate_and_baseline(tmp_path):
    """track3p_source -> particles -> geant4, dry-run geant4 with real particle
    weighting. The declared workflow produces particles.data whose numeric digest
    matches the Phase-0.5 beta=40 baseline (the real-compute equivalence check)."""
    staged = _stage('geant4_track3p_beta')
    cwd = os.getcwd()
    os.chdir(staged)
    try:
        entries = [
            {'module': 'track3p_source', 'file': '../assets/sample_track3p_particles.txt'},
            {'module': 'particles', 'impact_order': 1, 'impact_face_id': 6,
             'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
             'beta_input': 'beta', 'output_format': 'geant4',
             'output': 'particles.data'},
            {'module': 'geant4', 'geant4_input': 'input_7cell.geant4',
             'geant4_geometry_files': ['../assets/7cell_solid_whole.stl',
                                       '../assets/7cell_cavity_whole.stl']},
        ]
        inputs = WorkflowInputs(particles={'beta': 40.0})
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
