"""The ``tem3p`` module (Phase 2 of ``plans/tem3p_module_plan.md``).

Covers the optional ``em_solution`` requirement and how it binds, the
build-time checks and lint (every entry TEM3P ignores or rejects without
saying why), the leaves the module injects (``Directory`` on every RF-coupled
entry, ``EMMeshInputDir`` resolved to the *base* EM run for an appended pair),
the quantities, and ``verify``/``clean``/resume. The real-run tests drive a fake
``tem3p`` that replays the Phase 0 fixture outputs, so no ACE3P binary is
needed.
"""

import os
import re
import shutil
import warnings

import numpy as np
import pytest

from lume_ace3p.ace3p import Tem3P, parse_ace3p
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.modules import (
    EM_SOLUTION, MESH, MODULE_REGISTRY, RunContext, TEM3P_SOLUTION,
    Tem3PModule, build_module,
)
from lume_ace3p.workflow_graph import (
    Workflow, WorkflowValidationError, _infer_output_module,
)

from test_resume import _fake_ace3p, posix_only

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'tem3p')


def _fixture(case, name):
    with open(os.path.join(FIXTURES, case, 'inputs', name)) as file:
        return file.read()


def _fixed(text):
    """A CW23 input with the spellings TEM3P ignores corrected, so it passes
    the lint; the fixtures themselves stay verbatim."""
    text = text.replace('NeumanValue', 'NeumannValue')
    text = text.replace('Abs tolerance', 'AbsTolerance')
    text = text.replace('Rel tolerance', 'RelTolerance')
    return ''.join(line for line in text.splitlines(keepends=True)
                   if 'AbsoluteToleran' not in line)


def _journal(path, export):
    path.write_text(f'create brick x 1\nexport genesis "{export}" overwrite\n')
    return path.name


def _stage(root, tem3p='SRFCell.tem3p', case='srfcell', text=None,
           vacuum='SRFCellVacuum.ncdf', body='SRFCellShell.ncdf',
           em_input='SRFCell.omega3p'):
    """Write the inputs of an LFD chain into ``root``: the two journals, the
    Omega3P input and the TEM3P input (corrected unless ``text`` is given)."""
    root.mkdir(parents=True, exist_ok=True)
    _journal(root / 'vacuum.jou', vacuum)
    _journal(root / 'body.jou', body)
    if em_input.endswith('.omega3p'):
        (root / em_input).write_text(_fixture('srfcell', 'SRFCell.omega3p'))
    else:
        (root / em_input).write_text(_fixture('srfcoupler', em_input))
    (root / tem3p).write_text(
        text if text is not None else _fixed(_fixture(case, tem3p)))
    return root


def _lfd(tem3p='SRFCell.tem3p', em='omega3p', em_input='SRFCell.omega3p',
         deformed=True, **tem3p_entry):
    """cubit(vacuum) -> em -> tem3p -> omega3p_deformed, as YAML entries."""
    entries = [
        {'module': 'cubit', 'name': 'vacuum', 'journal': 'vacuum.jou',
         'meshconvert': False},
        {'module': 'cubit', 'name': 'body', 'journal': 'body.jou',
         'meshconvert': False},
        {'module': em, 'input': em_input, 'from': 'vacuum'},
        {'module': 'tem3p', 'input': tem3p,
         'from': {'mesh': 'body', 'em_solution': em}, **tem3p_entry},
    ]
    if deformed:
        entries.append({'module': 'omega3p', 'name': 'omega3p_deformed',
                        'input': 'SRFCell.omega3p', 'from': 'tem3p',
                        'results_dir': 'omega3p_results_deformed'})
    return entries


def _dry(root, entries, **kwargs):
    return Workflow(entries, workflow_params={'dry_run': True,
                                              'workdir': str(root / 'wd')},
                    **kwargs)


def _marker(root):
    return (root / 'wd' / 'DRY_RUN.txt').read_text()


# --------------------------------------------------------------------------- #
# Registry and the optional requirement
# --------------------------------------------------------------------------- #


def test_tem3p_edges():
    module = build_module('tem3p')
    assert MODULE_REGISTRY['tem3p'] is Tem3PModule
    assert set(module.requires) == {MESH}
    assert set(module.optional_requires) == {EM_SOLUTION}
    assert set(module.provides) == {TEM3P_SOLUTION, MESH}
    assert module.mesh_output == 'DeformedVacuumMesh.ncdf'


def test_no_other_module_has_an_optional_requirement():
    """Plain ``requires`` is unchanged for every other type, so their bindings,
    and the config hashes built from them, do not move."""
    for kind, cls in MODULE_REGISTRY.items():
        if kind != 'tem3p':
            assert cls.optional_requires == frozenset(), kind


def test_optional_requirement_binds_the_one_producer(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    wf = _dry(tmp_path, _lfd(**{'from': {'mesh': 'body'}}, deformed=False))
    assert wf.modules[-1].sources == {MESH: 'body', EM_SOLUTION: 'omega3p'}


def test_optional_requirement_without_a_producer_stays_unbound(tmp_path,
                                                               monkeypatch):
    """Mechanical eigenmodes read no RF solution: the chain is a mesh and a
    tem3p, and builds without an em_solution producer."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'TeslaCavity.tem3p').write_text(
        _fixture('teslacavity', 'TeslaCavity.tem3p'))
    _journal(tmp_path / 'body.jou', 'TeslaCavity.ncdf')
    wf = _dry(tmp_path, [{'module': 'cubit', 'journal': 'body.jou',
                          'meshconvert': False},
                         {'module': 'tem3p', 'input': 'TeslaCavity.tem3p'}])
    assert wf.modules[-1].sources == {MESH: 'cubit'}


def test_optional_requirement_with_several_producers_must_be_named(
        tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    with pytest.raises(WorkflowValidationError,
                       match=r"(?s)requires artifact 'em_solution', which 2 "
                             r"modules provide.*e\.g\.\n    from: "
                             r"\{em_solution: omega3p\}\n\(the mapping form"):
        _dry(tmp_path, _lfd(**{'from': {'mesh': 'body'}}))


def test_from_shorthand_on_tem3p_is_an_error(tmp_path, monkeypatch):
    """tem3p reads two kinds, counting the optional one, so a single name does
    not say which it binds."""
    monkeypatch.chdir(_stage(tmp_path))
    with pytest.raises(WorkflowValidationError,
                       match=r"requires mesh, em_solution \(optional\).*"
                             "mapping form"):
        _dry(tmp_path, _lfd(**{'from': 'body'}, deformed=False))


def test_from_naming_the_optional_kind_is_checked(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    with pytest.raises(WorkflowValidationError,
                       match="reads em_solution 'from: body'.*'cubit' module"):
        _dry(tmp_path, _lfd(**{'from': {'mesh': 'body',
                                        'em_solution': 'body'}}))


def test_the_lfd_chain_binds_and_orders(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    wf = _dry(tmp_path, _lfd())
    order = [m.name for m in wf.modules]
    assert order.index('omega3p') < order.index('tem3p') \
        < order.index('omega3p_deformed')
    by_name = {m.name: m for m in wf.modules}
    assert by_name['omega3p_deformed'].sources == {MESH: 'tem3p'}


# --------------------------------------------------------------------------- #
# Build-time checks
# --------------------------------------------------------------------------- #


def _problems(excinfo):
    return str(excinfo.value)


def test_cw23_srfcell_input_verbatim_fails_with_every_spelling_listed(
        tmp_path, monkeypatch):
    """The user's decision (2026-11-12): ignored spellings are errors, even
    when the ignored value equals the default, and all are listed at once."""
    monkeypatch.chdir(_stage(tmp_path, text=_fixture('srfcell',
                                                     'SRFCell.tem3p')))
    with pytest.raises(WorkflowValidationError) as excinfo:
        _dry(tmp_path, _lfd())
    message = _problems(excinfo)
    assert message.startswith('3 problem(s) found before running')
    assert "ElasticProblem Boundary Id 3: 'NeumanValue' is not a key TEM3P " \
           "reads" in message
    assert "Boundary Id 4: 'NeumanValue'" in message
    assert "spell it 'NeumannValue'" in message
    assert "ElasticProblem LinearSolver: 'AbsoluteTolerante'" in message
    assert "'Tolerance' (a relative tolerance) only" in message


def test_cw23_srfcoupler_tolerance_spellings(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path, tem3p='FPC-Body.tem3p',
                             text=_fixture('srfcoupler', 'FPC-Body.tem3p'),
                             body='2D-TTF3-FPC-Body.ncdf',
                             em_input='FPC-Vacuum.s3p'))
    with pytest.raises(WorkflowValidationError) as excinfo:
        _dry(tmp_path, _lfd(tem3p='FPC-Body.tem3p', em='s3p',
                            em_input='FPC-Vacuum.s3p', deformed=False))
    message = _problems(excinfo)
    assert "NonlinearSolver: 'Abs tolerance'" in message
    assert "spell it 'AbsTolerance'" in message
    assert "spell it 'RelTolerance'" in message
    assert message.count("'AbsoluteTolerance' is not a key") == 2


def test_corrected_cw23_inputs_pass(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    _dry(tmp_path, _lfd())


def _one_problem(tmp_path, monkeypatch, edit, entries=None, **stage):
    """Build the corrected SRFCell chain with one edit to the TEM3P input and
    return the error message."""
    text = edit(_fixed(_fixture('srfcell', 'SRFCell.tem3p')))
    monkeypatch.chdir(_stage(tmp_path, text=text, **stage))
    with pytest.raises(WorkflowValidationError) as excinfo:
        _dry(tmp_path, entries or _lfd())
    return _problems(excinfo)


@pytest.mark.parametrize('mixed, ok', [
    ('NEUMANN DIRICHLET NEUMANN', True),
    ('NEUMANN, DIRICHLET, NEUMANN', True),
    ('neumann DIRICHLET NEUMANN', False),
    ('NEUMANN DIRICHLET', False),
    ('NEUMANN DIRICHLET NEUMANN ROBIN', False),
])
def test_mixed_type_must_be_three_upper_case_entries(tmp_path, monkeypatch,
                                                     mixed, ok):
    text = _fixed(_fixture('srfcell', 'SRFCell.tem3p')).replace(
        'MixedType: NEUMANN DIRICHLET NEUMANN', f'MixedType: {mixed}')
    monkeypatch.chdir(_stage(tmp_path, text=text))
    if ok:
        _dry(tmp_path, _lfd())
        return
    with pytest.raises(WorkflowValidationError,
                       match="Boundary Id 1: 'MixedType: .*exactly three"):
        _dry(tmp_path, _lfd())


def test_lfdetuning_without_method(tmp_path, monkeypatch):
    message = _one_problem(tmp_path, monkeypatch,
                           lambda t: t.replace('Method: Gradient', ''))
    assert "Boundary Id 5: an LFDetuning boundary needs 'Method'" in message


def test_gradient_without_endpoint(tmp_path, monkeypatch):
    message = _one_problem(
        tmp_path, monkeypatch,
        lambda t: re.sub(r'EndPoint:.*\n', '', t))
    assert "'Method: Gradient' integrates the gradient along a line, so it " \
           "needs 'EndPoint'" in message


def test_mesh_dump_switch_must_be_lower_case_on(tmp_path, monkeypatch):
    """The binary compares the value with the exact string 'on'."""
    message = _one_problem(
        tmp_path, monkeypatch,
        lambda t: t.replace('WriteDeformedEMMesh: on', 'WriteDeformedEMMesh: On'))
    assert "'WriteDeformedEMMesh: On' is off" in message
    # Off, so the downstream omega3p reads a mesh that will not be written.
    assert "read the deformed vacuum mesh from this module, but its input " \
           "does not write one" in message


def test_lint_sees_the_scoped_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    inputs = WorkflowInputs(scoped={'tem3p': {'ace3p': parse_ace3p(
        'ElasticProblem: { MeshDump: { WriteDeformedEMMesh: ON } }')}})
    with pytest.raises(WorkflowValidationError,
                       match="'WriteDeformedEMMesh: ON' is off"):
        _dry(tmp_path, _lfd(), inputs=inputs)


def test_rf_coupled_input_without_an_em_run_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    entries = [{'module': 'cubit', 'journal': 'body.jou',
                'meshconvert': False},
               {'module': 'tem3p', 'input': 'SRFCell.tem3p'}]
    with pytest.raises(WorkflowValidationError) as excinfo:
        _dry(tmp_path, entries)
    message = _problems(excinfo)
    assert 'reads an RF solution (ElasticProblem Boundary Id 5 (LFDetuning), ' \
           'ElasticProblem MeshDump WriteDeformedEMMesh: on)' in message
    assert 'no EM solution is bound' in message


def test_em_run_bound_but_never_read_warns(tmp_path, monkeypatch):
    text = _fixed(_fixture('srfcell', 'SRFCell.tem3p'))
    text = text.replace('ConditionType: LFDetuning', 'ConditionType: Neumann')
    text = text.replace('WriteDeformedEMMesh: on', 'WriteDeformedEMMesh: off')
    monkeypatch.chdir(_stage(tmp_path, text=text))
    with pytest.warns(UserWarning, match="never reads it"):
        _dry(tmp_path, _lfd(deformed=False))


def test_downstream_mesh_from_an_eigen_run_is_an_error(tmp_path, monkeypatch):
    message = _one_problem(
        tmp_path, monkeypatch,
        lambda t: t.replace('LinearSolver: {',
                            'EigenSolver: { NumEigenvalues: 2 }\n'
                            '  LinearSolver: {'))
    assert "runs an EigenSolver, whose deformed meshes are per mode" in message


def test_missing_omega3p_id_with_a_consumer_is_an_error(tmp_path, monkeypatch):
    """P3: the mesh would come out undeformed without an error."""
    message = _one_problem(
        tmp_path, monkeypatch,
        lambda t: re.sub(r'Omega3PId:.*\n', '', t))
    assert "no LFDetuning or Neumann boundary of ElasticProblem sets " \
           "'Omega3PId'" in message
    assert 'comes out undeformed' in message


def test_missing_omega3p_id_without_a_consumer_warns(tmp_path, monkeypatch):
    text = re.sub(r'Omega3PId:.*\n', '',
                  _fixed(_fixture('srfcell', 'SRFCell.tem3p')))
    monkeypatch.chdir(_stage(tmp_path, text=text))
    with pytest.warns(UserWarning, match='comes out undeformed'):
        _dry(tmp_path, _lfd(deformed=False))


def test_mesh_file_must_be_what_the_producer_exports(tmp_path, monkeypatch):
    message = _one_problem(tmp_path, monkeypatch, lambda t: t,
                           body='Other.ncdf')
    assert "ElasticProblem 'MeshFile: SRFCellShell.ncdf' is not the mesh its " \
           "producer 'body' makes (Other.ncdf)" in message


def test_mesh_file_accepts_the_meshconvert_output(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path, body='SRFCellShell.gen'))
    entries = _lfd()
    entries[1]['meshconvert'] = True
    _dry(tmp_path, entries)


def test_mesh_file_against_a_mesh_source(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    entries = _lfd()
    entries[1] = {'module': 'mesh', 'name': 'body', 'file': 'meshes/Other.ncdf'}
    with pytest.raises(WorkflowValidationError,
                       match=r"is not the mesh its producer 'body' makes "
                             r"\(Other.ncdf\)"):
        _dry(tmp_path, entries)


def test_body_mesh_from_a_tem3p_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    entries = _lfd(deformed=False)
    entries.append({'module': 'tem3p', 'name': 'tem3p_2',
                    'input': 'SRFCell.tem3p', 'results_dir': 'tem3p_2',
                    'from': {'mesh': 'tem3p', 'em_solution': 'omega3p'}})
    with pytest.raises(WorkflowValidationError,
                       match="reads its body mesh from 'tem3p', a tem3p "
                             "module"):
        _dry(tmp_path, entries)


def test_unreadable_input_is_left_to_run_time(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    _dry(tmp_path, _lfd(tem3p='missing.tem3p'))


# --------------------------------------------------------------------------- #
# Injection
# --------------------------------------------------------------------------- #


def test_dry_run_marker_names_the_injected_leaves(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    wf = _dry(tmp_path, _lfd())
    _, ctx = wf.evaluate()
    marker = _marker(tmp_path)
    assert 'TEM3P ElasticProblem Boundary Id 5 Directory: omega3p_results\n' \
        in marker
    assert 'TEM3P ElasticProblem MeshDump EMMeshInputDir: omega3p_results\n' \
        in marker
    assert ctx.artifacts.of(MESH, 'tem3p') == os.path.join(
        str(tmp_path / 'wd'), 'tem3p_results', 'DeformedVacuumMesh.ncdf')
    assert ctx.job_names.of(MESH, 'tem3p') == 'tem3p_results'


def test_appended_pair_deforms_the_original_mesh(tmp_path, monkeypatch):
    """P4: the second tem3p reads its fields from the deformed run, but its
    EMMeshInputDir is the *original* EM run, found through the first tem3p."""
    monkeypatch.chdir(_stage(tmp_path))
    entries = _lfd() + [
        {'module': 'tem3p', 'name': 'tem3p_2', 'input': 'SRFCell.tem3p',
         'results_dir': 'tem3p_2',
         'from': {'mesh': 'body', 'em_solution': 'omega3p_deformed'}},
        {'module': 'omega3p', 'name': 'omega3p_d2', 'input': 'SRFCell.omega3p',
         'results_dir': 'omega3p_d2', 'from': 'tem3p_2'}]
    _dry(tmp_path, entries).evaluate()
    marker = _marker(tmp_path)
    second = marker[marker.rindex('TEM3P step skipped'):]
    assert 'Boundary Id 5 Directory: omega3p_results_deformed\n' in second
    assert 'MeshDump EMMeshInputDir: omega3p_results\n' in second


def test_rfgun_injects_both_problems(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path, tem3p='RfGunBody.tem3p', case='rfgun',
                             body='RfGunBody.ncdf'))
    _dry(tmp_path, _lfd(tem3p='RfGunBody.tem3p')).evaluate()
    marker = _marker(tmp_path)
    assert 'ThermostaticProblem Boundary Id 6 Directory: omega3p_results\n' \
        in marker
    assert 'ElasticProblem Boundary Id 6 Directory: omega3p_results\n' in marker


def test_srfcoupler_injects_the_heat_source(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path, tem3p='FPC-Body.tem3p',
                             case='srfcoupler', body='2D-TTF3-FPC-Body.ncdf',
                             em_input='FPC-Vacuum.s3p'))
    _, ctx = _dry(tmp_path, _lfd(tem3p='FPC-Body.tem3p', em='s3p',
                                 em_input='FPC-Vacuum.s3p',
                                 deformed=False)).evaluate()
    marker = _marker(tmp_path)
    assert 'ThermostaticProblem HeatSource Id 5 Directory: s3p_results\n' \
        in marker
    assert marker.count('Directory: s3p_results') == 5
    assert 'EMMeshInputDir' not in marker
    assert not ctx.artifacts.has(MESH, 'tem3p')


# --------------------------------------------------------------------------- #
# Extraction (over the Phase 0 fixtures)
# --------------------------------------------------------------------------- #


def _module_on(tmp_path, case, job_dir='tem3p_results'):
    """A Tem3PModule holding a wrapper whose results are a fixture copy."""
    workdir = tmp_path / 'wd'
    shutil.copytree(os.path.join(FIXTURES, case, job_dir),
                    workdir / 'tem3p_results')
    inputs = os.path.join(FIXTURES, case, 'inputs')
    name = next(n for n in sorted(os.listdir(inputs)) if n.endswith('.tem3p'))
    module = build_module('tem3p', {'input': os.path.join(inputs, name)})
    solver = Tem3P(os.path.join(inputs, name), workdir=str(workdir))
    solver.output_parser()
    module._solver = solver
    return module, RunContext(str(workdir))


def test_extract_srfcell(tmp_path):
    module, ctx = _module_on(tmp_path, 'srfcell')
    assert module.extract(ctx, 'u_mag_max') == pytest.approx(1.9466783e-06)
    assert module.extract(ctx, {'quantity': 'u_z_min'}) == \
        pytest.approx(-1.9415996e-06)
    assert module.extract(ctx, 'scaling_factor') == \
        pytest.approx(1986361833765.0857)
    assert module.extract(ctx, 'n_dofs') == 205443
    assert module.extract(ctx, 'run_time') == 34
    assert np.isnan(module.extract(ctx, 'newton_iterations'))
    assert module.field_index(ctx) is None
    with pytest.raises(ValueError, match='no thermal block'):
        module.extract(ctx, 'max_temperature')
    with pytest.raises(ValueError, match='no eigenmode table'):
        module.extract(ctx, 'mechanical_frequency')


def test_extract_rfgun_per_problem(tmp_path):
    module, ctx = _module_on(tmp_path, 'rfgun')
    assert module.extract(ctx, 'max_temperature') == pytest.approx(28.595)
    assert module.extract(ctx, {'quantity': 'sideset_power',
                                'at': {'sideset': 5}}) == pytest.approx(213.111)
    with pytest.raises(ValueError, match='both problems'):
        module.extract(ctx, 'scaling_factor')
    assert module.extract(ctx, {'quantity': 'scaling_factor',
                                'at': {'problem': 'thermal'}}) == \
        pytest.approx(41050781.146822527)
    assert module.extract(ctx, {'quantity': 'n_dofs',
                                'at': {'problem': 'elastic'}}) == 1013412
    with pytest.raises(ValueError, match='is not one of'):
        module.extract(ctx, {'quantity': 'n_dofs', 'at': {'problem': 'eigen'}})


def test_extract_tesla_eigen(tmp_path):
    module, ctx = _module_on(tmp_path, 'teslacavity')
    label, modes = module.field_index(ctx)
    assert label == 'Mode' and list(modes) == [1, 2, 3, 4, 5]
    assert module.extract(ctx, 'mechanical_frequency') == pytest.approx(
        [62.822941, 167.12759, 242.46092, 307.64515, 468.56376])
    assert module.extract(ctx, {'quantity': 'mechanical_frequency',
                                'at': {'mode': 2}}) == pytest.approx(167.12759)
    assert len(module.extract(ctx, 'u_mag_max')) == 5
    with pytest.raises(ValueError, match='found no mode 9'):
        module.extract(ctx, {'quantity': 'u_mag_max', 'at': {'mode': 9}})


def test_extract_srfcoupler(tmp_path):
    module, ctx = _module_on(tmp_path, 'srfcoupler')
    assert module.extract(ctx, 'max_temperature') == pytest.approx(324.352)
    assert module.extract(ctx, 'newton_iterations') == 9
    assert module.extract(ctx, 'scaling_factor') == 437.5
    with pytest.raises(ValueError, match=r"needs 'at: \{sideset: <id>\}'"):
        module.extract(ctx, 'sideset_power')
    with pytest.raises(ValueError, match='no power for sideset 12'):
        module.extract(ctx, {'quantity': 'sideset_power',
                             'at': {'sideset': 12}})
    with pytest.raises(ValueError, match='no displacement row'):
        module.extract(ctx, 'u_mag_max')


def test_extract_rejects_unknown_names(tmp_path):
    module, ctx = _module_on(tmp_path, 'srfcell')
    with pytest.raises(ValueError, match="Unknown tem3p quantity 'Max T'"):
        module.extract(ctx, 'Max T')
    with pytest.raises(ValueError, match=r"narrows on .*not \['block'\]"):
        module.extract(ctx, {'quantity': 'u_mag_max', 'at': {'block': 1}})


def test_extract_under_dry_run_is_nan():
    module = build_module('tem3p', {'input': 'x.tem3p'})
    assert np.isnan(module.extract(RunContext('wd'), 'u_mag_max'))


@pytest.mark.parametrize('spec', [
    'u_mag_max', 'max_temperature', 'mechanical_frequency', ['u_x_min'],
    {'quantity': 'sideset_power', 'at': {'sideset': 4}},
    {'quantity': 'scaling_factor', 'at': {'problem': 'thermal'}},
])
def test_bare_specs_route_to_tem3p(spec):
    assert _infer_output_module(spec) == 'tem3p'


@pytest.mark.parametrize('spec', ['run_time', 'n_dofs'])
def test_generic_names_do_not_route_bare(spec):
    assert _infer_output_module(spec) != 'tem3p'


# --------------------------------------------------------------------------- #
# Mesh hand-off (§3.3) and disk collisions
# --------------------------------------------------------------------------- #


def test_dry_run_marker_names_the_deformed_mesh(tmp_path, monkeypatch):
    """Only the Omega3P bound to the tem3p is pointed at the deformed mesh; the
    one bound to a cubit keeps its input's own ModelInfo.File."""
    monkeypatch.chdir(_stage(tmp_path))
    _dry(tmp_path, _lfd()).evaluate()
    marker = _marker(tmp_path)
    line = 'Omega3P ModelInfo.File: ./tem3p_results/DeformedVacuumMesh.ncdf\n'
    assert marker.count(line) == 1
    assert marker.index(line) > marker.index('TEM3P step skipped')
    assert marker.count('ModelInfo.File') == 1


def test_appended_pair_hands_on_its_own_mesh(tmp_path, monkeypatch):
    monkeypatch.chdir(_stage(tmp_path))
    entries = _lfd() + [
        {'module': 'tem3p', 'name': 'tem3p_2', 'input': 'SRFCell.tem3p',
         'results_dir': 'tem3p_2',
         'from': {'mesh': 'body', 'em_solution': 'omega3p_deformed'}},
        {'module': 'omega3p', 'name': 'omega3p_d2', 'input': 'SRFCell.omega3p',
         'results_dir': 'omega3p_d2', 'from': 'tem3p_2'}]
    _dry(tmp_path, entries).evaluate()
    marker = _marker(tmp_path)
    assert 'ModelInfo.File: ./tem3p_2/DeformedVacuumMesh.ncdf\n' in \
        marker[marker.rindex('TEM3P step skipped'):]


def test_no_cubit_or_mesh_source_declares_a_mesh_output():
    """Only tem3p hands on a mesh inside its job directory, so no existing
    workflow's ModelInfo.File is touched."""
    declaring = sorted(name for name, cls in MODULE_REGISTRY.items()
                       if getattr(cls, 'mesh_output', None))
    assert declaring == ['tem3p']


def test_tem3p_sharing_another_solver_s_directory_is_an_error(tmp_path,
                                                              monkeypatch):
    """Different types, one job directory: tem3p's clean would remove the EM
    run's results, and its mesh would land among them."""
    monkeypatch.chdir(_stage(tmp_path))
    with pytest.raises(WorkflowValidationError,
                       match=r"'omega3p' \('omega3p'\) and 'tem3p' \('tem3p'\) "
                             r"would both write into 'omega3p_results'"):
        _dry(tmp_path, _lfd(results_dir='omega3p_results'))


def test_two_meshconverts_onto_one_ncdf_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _journal(tmp_path / 'a.jou', 'cav.gen')
    _journal(tmp_path / 'b.jou', 'cav.exo')
    entries = [{'module': 'cubit', 'name': 'a', 'journal': 'a.jou'},
               {'module': 'cubit', 'name': 'b', 'journal': 'b.jou'}]
    with pytest.raises(WorkflowValidationError,
                       match=r"'a' and 'b' both put a mesh named 'cav.ncdf'.*"
                             r"meshconvert of cav.gen; meshconvert of cav.exo"):
        _dry(tmp_path, entries)
    entries[1]['meshconvert'] = False
    _dry(tmp_path, entries)


def test_a_mesh_source_onto_a_cubit_export_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _journal(tmp_path / 'a.jou', 'cav.gen')
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'cav.ncdf').write_text('')
    entries = [{'module': 'cubit', 'name': 'a', 'journal': 'a.jou'},
               {'module': 'mesh', 'name': 'm', 'file': 'src/cav.ncdf'}]
    with pytest.raises(WorkflowValidationError,
                       match=r"'a' and 'm' both put a mesh named 'cav.ncdf'"):
        _dry(tmp_path, entries)


def test_two_mesh_sources_may_stage_one_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'cav.ncdf').write_text('')
    (tmp_path / 'other').mkdir()
    (tmp_path / 'other' / 'cav.ncdf').write_text('')
    entries = [{'module': 'mesh', 'name': 'm1', 'file': 'cav.ncdf'},
               {'module': 'mesh', 'name': 'm2', 'file': 'cav.ncdf'}]
    _dry(tmp_path, entries)
    entries[1]['file'] = 'other/cav.ncdf'
    with pytest.raises(WorkflowValidationError, match="'m1' and 'm2'"):
        _dry(tmp_path, entries)


# --------------------------------------------------------------------------- #
# Real runs, through a fake tem3p replaying the fixtures
# --------------------------------------------------------------------------- #

# 'omega3p <input> [dir]': an eigenmode, and the VECTOR/postprocess.in a real
# run writes, naming the input's mesh by absolute path.
_OMEGA3P = """#!/bin/sh
dir=${2:-omega3p_results}
mkdir -p "$dir/VECTOR"
printf 'Mode : {\\n  ModeIndex : 0\\n  Frequency : 1.3e9\\n}\\n' > "$dir/omega3p.out"
mesh=`grep -E '^[ \t]*File' "$1" | head -1 | sed 's/.*:[ \t]*//; s/[ \t]*$//'`
printf 'ModelInfo : {\\n  File : %s/%s\\n}\\nJobName : %s\\n' "$PWD" "$mesh" \
  "$dir" > "$dir/VECTOR/postprocess.in"
"""

# 'tem3p <input> [dir]': appends the fixture ResultFile and copies its log, as
# the real binary appends and overwrites; writes the deformed mesh when the
# input asks for it, unless FAKE_TEM3P_NO_MESH is set.
_TEM3P = """#!/bin/sh
dir=${2:-tem3p_results}
mkdir -p "$dir"
cp "$1" "$dir/input.echo"
cat "$FAKE_TEM3P_FIXTURE/ResultFile.first_run" >> "$dir/ResultFile"
cp "$FAKE_TEM3P_FIXTURE/tem3p.log" "$dir/tem3p.log"
if grep -q 'WriteDeformedEMMesh *: *on' "$1" && [ -z "$FAKE_TEM3P_NO_MESH" ]; then
  echo deformed > "$dir/DeformedVacuumMesh.ncdf"
fi
echo "tem3p: wrote $dir"
"""


@pytest.fixture
def real(tmp_path, monkeypatch):
    """A staged LFD chain, fake binaries, and the fixture the fake replays."""
    root = _stage(tmp_path)
    monkeypatch.chdir(root)
    paths = _fake_ace3p(root)
    for name, body in (('omega3p', _OMEGA3P), ('tem3p', _TEM3P)):
        script = root / 'bin' / name
        script.write_text(body)
        script.chmod(0o755)
    monkeypatch.setenv('FAKE_TEM3P_FIXTURE',
                       os.path.join(FIXTURES, 'srfcell', 'tem3p_results'))
    return root, paths


def _real_workflow(root, paths, entries=None, **params):
    entries = entries or _lfd()
    output_spec = {'f0': {'module': 'omega3p', 'quantity': 'Frequency',
                          'at': {'mode': 0}},
                   'umax': 'u_mag_max',
                   'lf': {'module': 'tem3p', 'quantity': 'scaling_factor'}}
    if any(e.get('name') == 'omega3p_deformed' for e in entries):
        output_spec['f1'] = {'module': 'omega3p_deformed',
                             'quantity': 'Frequency', 'at': {'mode': 0}}
    return Workflow(
        entries,
        workflow_params={'workdir': str(root / 'wd'), 'dry_run': False,
                         'paths': paths, **params},
        output_spec=output_spec)


@posix_only
def test_lfd_chain_runs_and_extracts(real):
    root, paths = real
    outputs, ctx = _real_workflow(root, paths).evaluate()
    assert outputs['umax'] == pytest.approx(1.9466783e-06)
    assert outputs['lf'] == pytest.approx(1986361833765.0857)
    assert outputs['f0'] == outputs['f1'] == 1.3e9
    mesh = root / 'wd' / 'tem3p_results' / 'DeformedVacuumMesh.ncdf'
    assert ctx.artifacts.of(MESH, 'tem3p') == str(mesh)
    assert ctx.artifacts.of(TEM3P_SOLUTION, 'tem3p') == str(root / 'wd')

    written = parse_ace3p((root / 'wd' / 'tem3p_results' /
                           'input.echo').read_text())
    elastic = written.find('ElasticProblem')
    assert elastic.find('Boundary', Id='5').get_leaf('Directory') == \
        'omega3p_results'
    assert elastic.find('MeshDump').get_leaf('EMMeshInputDir') == \
        'omega3p_results'


@posix_only
def test_a_different_directory_in_the_input_is_replaced(real, capsys):
    """The workdir holds other EM runs, so a Directory naming one of them is
    not kept because it exists: it is replaced by the bound run, with a note."""
    root, paths = real
    text = (root / 'SRFCell.tem3p').read_text().replace(
        'Directory: omega3p_results', 'Directory: omega3p_results_deformed')
    (root / 'SRFCell.tem3p').write_text(text)
    _real_workflow(root, paths).evaluate()
    written = (root / 'wd' / 'tem3p_results' / 'input.echo').read_text()
    assert 'omega3p_results_deformed' not in written
    assert 'Directory: omega3p_results_deformed -> omega3p_results' in \
        capsys.readouterr().out


@posix_only
def test_a_fresh_run_starts_a_fresh_result_file(real):
    """ResultFile appends (P2); clean removes the job directory before every
    fresh run, so a re-run reads one run, not two."""
    root, paths = real
    wf = _real_workflow(root, paths)
    wf.evaluate()
    _, ctx = wf.evaluate()
    tem3p = next(m for m in ctx.modules if m.name == 'tem3p')
    assert tem3p._solver.output_data['runs'] == 1


@posix_only
def test_moved_em_run_is_caught_before_launching(real):
    """P9: the EM run's postprocess.in names a mesh that is gone. The module
    raises naming it rather than letting TEM3P abort with no message."""
    root, paths = real
    wf = _real_workflow(root, paths, entries=_lfd(deformed=False))
    wf.evaluate()
    post = root / 'wd' / 'omega3p_results' / 'VECTOR' / 'postprocess.in'
    post.write_text(post.read_text().replace(str(root), '/moved/away'))
    ctx = wf.last_context
    tem3p = next(m for m in ctx.modules if m.name == 'tem3p')
    with pytest.raises(FileNotFoundError,
                       match="no longer exists.*re-run the EM step"):
        tem3p.run(ctx)


@posix_only
def test_em_run_without_postprocess_in_is_caught(real):
    root, paths = real
    wf = _real_workflow(root, paths, entries=_lfd(deformed=False))
    wf.evaluate()
    os.remove(root / 'wd' / 'omega3p_results' / 'VECTOR' / 'postprocess.in')
    ctx = wf.last_context
    tem3p = next(m for m in ctx.modules if m.name == 'tem3p')
    with pytest.raises(FileNotFoundError, match="Run the EM step first"):
        tem3p.run(ctx)


@posix_only
def test_a_run_that_writes_no_deformed_mesh_fails(real, monkeypatch):
    root, paths = real
    monkeypatch.setenv('FAKE_TEM3P_NO_MESH', '1')
    wf = _real_workflow(root, paths)
    with pytest.raises(RuntimeError, match='wrote no DeformedVacuumMesh.ncdf'):
        wf.evaluate()


@posix_only
def test_verify_and_clean(real):
    root, paths = real
    wf = _real_workflow(root, paths)
    _, ctx = wf.evaluate()
    tem3p = next(m for m in ctx.modules if m.name == 'tem3p')
    job = root / 'wd' / 'tem3p_results'
    assert tem3p.verify(ctx) is True

    mesh = job / 'DeformedVacuumMesh.ncdf'
    mesh.rename(job / 'moved.ncdf')
    assert tem3p.verify(ctx) is False
    (job / 'moved.ncdf').rename(mesh)

    log = job / 'tem3p.log'
    text = log.read_text()
    log.write_text(re.sub(r'Ending run at.*\n', '', text))
    assert tem3p.verify(ctx) is False
    log.write_text(text)
    assert tem3p.verify(ctx) is True

    tem3p.clean(ctx)
    assert not job.exists()
    assert tem3p.verify(ctx) is False


@posix_only
def test_resume_reparses_without_relaunching(real):
    root, paths = real
    wf = _real_workflow(root, paths, workdir_mode='indexed')
    first, _ = wf.evaluate(workdir=str(root / 'wd'))
    echo = root / 'wd' / 'tem3p_results' / 'input.echo'
    stamp = echo.stat().st_mtime_ns
    second, ctx = wf.evaluate(workdir=str(root / 'wd'), resume=True)
    assert echo.stat().st_mtime_ns == stamp
    assert second == first
    assert ctx.artifacts.of(MESH, 'tem3p').endswith('DeformedVacuumMesh.ncdf')


@posix_only
def test_the_deformed_omega3p_reads_the_deformed_mesh(real, capsys):
    """One shared Omega3P input: the first instance keeps its own mesh, the
    second is pointed at tem3p's, with a note naming the value it replaced."""
    root, paths = real
    _real_workflow(root, paths).evaluate()
    wd = root / 'wd'

    def mesh_of(name):
        return parse_ace3p((wd / name).read_text()).find(
            'ModelInfo').get_leaf('File')

    assert mesh_of('SRFCell_omega3p.omega3p') == 'SRFCellVacuum.ncdf'
    assert mesh_of('SRFCell_omega3p_deformed.omega3p') == \
        './tem3p_results/DeformedVacuumMesh.ncdf'
    out = capsys.readouterr().out
    assert ("'omega3p_deformed' reads the mesh 'tem3p' writes, so its "
            "ModelInfo.File: SRFCellVacuum.ncdf is replaced by "
            "./tem3p_results/DeformedVacuumMesh.ncdf") in out
    # The deformed run's own postprocess.in names the deformed mesh, which is
    # what an appended tem3p's pre-flight reads.
    post = (wd / 'omega3p_results_deformed' / 'VECTOR' /
            'postprocess.in').read_text()
    assert 'tem3p_results/DeformedVacuumMesh.ncdf' in post


@posix_only
def test_a_mesh_leaf_already_naming_the_deformed_mesh_is_kept(real, capsys):
    """The CW23 SRFCellDeformed.omega3p spells the path itself."""
    root, paths = real
    (root / 'SRFCellDeformed.omega3p').write_text(
        _fixture('srfcell', 'SRFCellDeformed.omega3p'))
    entries = _lfd()
    entries[-1]['input'] = 'SRFCellDeformed.omega3p'
    _real_workflow(root, paths, entries=entries).evaluate()
    assert 'is replaced by' not in capsys.readouterr().out
    written = parse_ace3p((root / 'wd' / 'SRFCellDeformed.omega3p').read_text())
    assert written.find('ModelInfo').get_leaf('File') == \
        './tem3p_results/DeformedVacuumMesh.ncdf'


@posix_only
def test_an_appended_pair_runs(real):
    """Use case B end to end: the second tem3p reads the deformed run's fields
    and the original mesh, and the last Omega3P reads the second tem3p's mesh."""
    root, paths = real
    entries = _lfd() + [
        {'module': 'tem3p', 'name': 'tem3p_2', 'input': 'SRFCell.tem3p',
         'results_dir': 'tem3p_2',
         'from': {'mesh': 'body', 'em_solution': 'omega3p_deformed'}},
        {'module': 'omega3p', 'name': 'omega3p_d2', 'input': 'SRFCell.omega3p',
         'results_dir': 'omega3p_d2', 'from': 'tem3p_2'}]
    Workflow(entries,
             workflow_params={'workdir': str(root / 'wd'), 'dry_run': False,
                              'paths': paths},
             output_spec={'u2': {'module': 'tem3p_2',
                                 'quantity': 'u_mag_max'}}).evaluate()
    wd = root / 'wd'
    echo = parse_ace3p((wd / 'tem3p_2' / 'input.echo').read_text())
    elastic = echo.find('ElasticProblem')
    assert elastic.find('Boundary', Id='5').get_leaf('Directory') == \
        'omega3p_results_deformed'
    assert elastic.find('MeshDump').get_leaf('EMMeshInputDir') == \
        'omega3p_results'
    last = parse_ace3p((wd / 'SRFCell_omega3p_d2.omega3p').read_text())
    assert last.find('ModelInfo').get_leaf('File') == \
        './tem3p_2/DeformedVacuumMesh.ncdf'
