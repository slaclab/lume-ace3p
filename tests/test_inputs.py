"""Parsing and variable-routing tests for the input-parameter notation.

Covers the standardized nested ``input_parameters`` schema (``cubit:`` /
``ace3p:`` / ``geant4:`` sub-blocks), back-compat with the deprecated flat keys
(``cubit_input_parameters`` / ``ace3p_input_parameters`` /
``geant4_input_parameters`` and a bare ``input_parameters`` cubit block), and the
bucket-aware VOCS variable routing used by the optimize modes
(:meth:`WorkflowInputs.apply_overrides`).
"""

import numpy as np
import pytest

from lume_ace3p.inputs import build_inputs, load_yaml, WorkflowInputs


def _write(tmp_path, text):
    path = tmp_path / 'in.yaml'
    path.write_text(text)
    return str(path)


def _ace3p_leaves(inp):
    """Flatten the ace3p Section into {dotted_path: value} for assertions."""
    out = {}

    def walk(section, prefix):
        for name, child in section.entries:
            if hasattr(child, 'entries'):
                walk(child, prefix + [name])
            else:
                out['.'.join(prefix + [name])] = child
    walk(inp.ace3p, [])
    return out


# --------------------------------------------------------------------------- #
# Nested notation parsing
# --------------------------------------------------------------------------- #


NESTED_YAML = """
workflow_parameters :
  workdir : wd
input_parameters :
  cubit :
    cornercut : {min: 12.0, max: 16.0, num: 3}
    rcorner2 : 5.0
  ace3p :
    FrequencyScan :
      Start : 9.424e9
    Port :
      ReferenceNumber : 7
    Port :
      ReferenceNumber : 8
  geant4 :
    nthreads : 8
  particles :
    beta0 : 50.0
"""


def test_nested_parses_into_four_buckets(tmp_path):
    inp = build_inputs(load_yaml(_write(tmp_path, NESTED_YAML)))
    assert set(inp.cubit) == {'cornercut', 'rcorner2'}
    np.testing.assert_allclose(inp.cubit['cornercut'], [12.0, 14.0, 16.0])
    assert inp.cubit['rcorner2'] == 5.0
    assert inp.macro == {'nthreads': 8}
    assert inp.particles == {'beta0': 50.0}


def test_nested_ace3p_preserves_duplicate_keys(tmp_path):
    inp = build_inputs(load_yaml(_write(tmp_path, NESTED_YAML)))
    ports = [child for name, child in inp.ace3p.entries if name == 'Port']
    assert len(ports) == 2
    assert ports[0].entries == [('ReferenceNumber', '7')]
    assert ports[1].entries == [('ReferenceNumber', '8')]


def test_nested_only_array_leaves_become_sweep_axes(tmp_path):
    inp = build_inputs(load_yaml(_write(tmp_path, NESTED_YAML)))
    labels = [label for label, _, _ in inp.sweep_axes()]
    # Only cornercut is array-valued; everything else is scalar.
    assert labels == ['cornercut']


def test_nested_ace3p_array_leaf_is_a_sweep_axis(tmp_path):
    text = """
input_parameters :
  cubit :
    cav_radius : {min: 90.0, max: 120.0, num: 4}
  ace3p :
    ModelInfo :
      SurfaceMaterial :
        ReferenceNumber : 6
        Sigma : [5.8e7, 1.04e7]
"""
    inp = build_inputs(load_yaml(_write(tmp_path, text)))
    labels = [label for label, _, _ in inp.sweep_axes()]
    assert 'cav_radius' in labels
    assert 'ace3p:ModelInfo.SurfaceMaterial.Sigma' in labels


# --------------------------------------------------------------------------- #
# Back-compat with the deprecated flat keys
# --------------------------------------------------------------------------- #


def test_flat_cubit_input_parameters_still_parses(tmp_path):
    text = """
cubit_input_parameters :
  cornercut : {min: 12.0, max: 16.0, num: 3}
geant4_input_parameters :
  nthreads : 8
ace3p_input_parameters :
  FrequencyScan :
    Start : 9.424e9
"""
    inp = build_inputs(load_yaml(_write(tmp_path, text)))
    np.testing.assert_allclose(inp.cubit['cornercut'], [12.0, 14.0, 16.0])
    assert inp.macro == {'nthreads': 8}
    # Leaf scalars are stringified through _build_section (str(float)).
    leaves = _ace3p_leaves(inp)
    assert list(leaves) == ['FrequencyScan.Start']
    assert float(leaves['FrequencyScan.Start']) == pytest.approx(9.424e9)


def test_bare_input_parameters_is_cubit(tmp_path):
    text = """
input_parameters :
  cornercut : {min: 12.0, max: 16.0, num: 3}
  rcorner2 : 5.0
"""
    inp = build_inputs(load_yaml(_write(tmp_path, text)))
    assert set(inp.cubit) == {'cornercut', 'rcorner2'}
    assert not inp.macro


def test_nested_and_flat_agree(tmp_path):
    """The nested and flat spellings produce identical WorkflowInputs."""
    nested = build_inputs(load_yaml(_write(tmp_path, NESTED_YAML)))
    flat_text = """
cubit_input_parameters :
  cornercut : {min: 12.0, max: 16.0, num: 3}
  rcorner2 : 5.0
geant4_input_parameters :
  nthreads : 8
particles_input_parameters :
  beta0 : 50.0
ace3p_input_parameters :
  FrequencyScan :
    Start : 9.424e9
  Port :
    ReferenceNumber : 7
  Port :
    ReferenceNumber : 8
"""
    flat = build_inputs(load_yaml(_write(tmp_path, flat_text)))
    np.testing.assert_allclose(nested.cubit['cornercut'], flat.cubit['cornercut'])
    assert nested.cubit['rcorner2'] == flat.cubit['rcorner2']
    assert nested.macro == flat.macro
    assert nested.particles == flat.particles
    assert _ace3p_leaves(nested) == _ace3p_leaves(flat)


def test_reserved_word_disambiguation(tmp_path):
    """A bare input_parameters whose keys are NOT all reserved bucket names is
    the legacy flat cubit block, even if one key happens to be a bucket name."""
    text = """
input_parameters :
  cubit : 3.0
  cornercut : 5.0
"""
    inp = build_inputs(load_yaml(_write(tmp_path, text)))
    # 'cubit' here is a cubit knob literally named 'cubit', not a sub-block.
    assert inp.cubit == {'cubit': 3.0, 'cornercut': 5.0}


# --------------------------------------------------------------------------- #
# Bucket-aware VOCS routing (apply_overrides)
# --------------------------------------------------------------------------- #


def _mixed_inputs():
    """cubit={cornercut}, ace3p leaf FrequencyScan.start, macro={nthreads};
    'start' is deliberately declared in BOTH cubit and ace3p to force a
    collision."""
    from lume_ace3p.ace3p import Section
    ace = Section()
    fs = Section()
    fs.append('start', '9.4e9')
    ace.append('FrequencyScan', fs)
    return WorkflowInputs(cubit={'cornercut': 14.0, 'start': 1.0},
                          ace3p=ace, macro={'nthreads': 8},
                          particles={'beta0': 50.0})


def test_bare_unique_name_routes_to_declaring_bucket():
    inp = _mixed_inputs()
    out = inp.apply_overrides({'cornercut': 15.0})
    assert out.cubit['cornercut'] == 15.0


def test_qualified_ace3p_label_routes_to_ace3p():
    inp = _mixed_inputs()
    out = inp.apply_overrides({'ace3p:FrequencyScan.start': '10e9'})
    assert _ace3p_leaves(out) == {'FrequencyScan.start': '10e9'}
    # cubit 'start' untouched
    assert out.cubit['start'] == 1.0


def test_qualified_geant4_label_routes_to_macro():
    inp = _mixed_inputs()
    out = inp.apply_overrides({'geant4:nthreads': 16})
    assert out.macro['nthreads'] == 16


def test_qualified_cubit_label_routes_to_cubit():
    inp = _mixed_inputs()
    out = inp.apply_overrides({'cubit:start': 2.0})
    assert out.cubit['start'] == 2.0


def test_bare_particles_name_routes_to_particles():
    inp = _mixed_inputs()
    out = inp.apply_overrides({'beta0': 55.0})
    assert out.particles['beta0'] == 55.0
    # cubit untouched
    assert out.cubit['cornercut'] == 14.0


def test_qualified_particles_label_routes_to_particles():
    inp = _mixed_inputs()
    out = inp.apply_overrides({'particles:beta0': 60.0})
    assert out.particles['beta0'] == 60.0


def test_bare_colliding_name_raises_with_guidance():
    inp = _mixed_inputs()
    with pytest.raises(ValueError, match="more than one input bucket"):
        inp.apply_overrides({'start': 2.0})


def test_unregistered_name_falls_back_to_cubit():
    """A VOCS variable not declared in any bucket lands in cubit (back-compat
    with configs that declare only vocs_parameters.variables)."""
    inp = WorkflowInputs()
    out = inp.apply_overrides({'newvar': 3.0})
    assert out.cubit['newvar'] == 3.0


# --------------------------------------------------------------------------- #
# Module-scoped inputs (plans/multi_instance_workflow_plan.md Phase 1)
# --------------------------------------------------------------------------- #


SCOPED_YAML = """
workflow_parameters :
  workdir : wd
workflow :
  - module : mesh
    file : 'cav.ncdf'
  - module : omega3p
    input : 'cav.omega3p'
  - module : t3p
    input : 'cav.t3p'
input_parameters :
  ace3p :
    omega3p :
      'EigenSolver' :
        'NumEigenvalues' : 5
"""


def _stage_two_solvers(tmp_path, monkeypatch):
    """A mesh + Omega3P + T3P chain whose solvers are stubbed to write their
    input file and nothing else, which is the step an override is visible in."""
    from lume_ace3p.ace3p import ACE3P

    monkeypatch.chdir(tmp_path)
    (tmp_path / 'cav.ncdf').write_text('')
    (tmp_path / 'cav.omega3p').write_text(
        'ModelInfo: {\n  File: cav.ncdf\n}\n'
        'EigenSolver: {\n  NumEigenvalues: 2\n}\n')
    (tmp_path / 'cav.t3p').write_text(
        'ModelInfo: {\n  File: cav.ncdf\n}\n'
        'TimeStepping: {\n  MaximumTime: 1e-9\n}\n')
    monkeypatch.setattr(ACE3P, 'run', lambda self: self.write_input())


def test_a_scoped_eigensolver_override_stays_out_of_the_t3p_input(
        tmp_path, monkeypatch):
    """An ``EigenSolver`` override scoped to ``omega3p`` reaches the ``.omega3p``
    file and nothing else. Unscoped, ``merge_overrides`` appends a missing
    container rather than skipping it, so the ``.t3p`` would gain a brand-new
    ``EigenSolver`` block (§1.A) — the Phase-0 characterization this replaces."""
    from lume_ace3p.workflow_graph import Workflow

    _stage_two_solvers(tmp_path, monkeypatch)
    Workflow.from_config(load_yaml(_write(tmp_path, SCOPED_YAML))).evaluate()

    assert 'NumEigenvalues : 5' in (tmp_path / 'wd' / 'cav.omega3p').read_text()
    assert 'EigenSolver' not in (tmp_path / 'wd' / 'cav.t3p').read_text()


def test_an_unscoped_container_missing_from_one_solver_warns(
        tmp_path, monkeypatch, capsys):
    """The shared spelling still merges everywhere (§3.2) — a deliberate
    addition is legal — but with two solvers it says so, naming the solver it
    is appended to and the scoped spelling."""
    from lume_ace3p.workflow_graph import Workflow

    _stage_two_solvers(tmp_path, monkeypatch)
    text = SCOPED_YAML.replace("    omega3p :\n      'EigenSolver' :\n"
                               "        'NumEigenvalues' : 5",
                               "    'EigenSolver' :\n      'NumEigenvalues' : 5")
    Workflow.from_config(load_yaml(_write(tmp_path, text)))
    out = capsys.readouterr().out
    assert "'EigenSolver' is not in the input file of 't3p'" in out
    assert 'omega3p:' in out


def test_one_solver_never_warns_about_a_missing_container(
        tmp_path, monkeypatch, capsys):
    """With a single ACE3P solver nothing changes: adding a container the file
    lacks is the no-input-file case, not a leak."""
    from lume_ace3p.workflow_graph import Workflow

    _stage_two_solvers(tmp_path, monkeypatch)
    text = """
workflow :
  - module : mesh
    file : 'cav.ncdf'
  - module : t3p
    input : 'cav.t3p'
input_parameters :
  ace3p :
    'EigenSolver' :
      'NumEigenvalues' : 5
"""
    Workflow.from_config(load_yaml(_write(tmp_path, text)))
    assert 'Warning' not in capsys.readouterr().out


def test_scoped_wins_over_shared_at_the_same_path():
    """A module's effective inputs are ``shared ⊕ scoped[name]``: a scoped leaf
    replaces the shared one at the same path, shared leaves it does not name
    survive, and a module without a scope sees the shared buckets unchanged."""
    from lume_ace3p.ace3p import Section

    shared = Section()
    fe = Section()
    fe.append('Order', '1')
    fe.append('CurvedSurfaces', 'on')
    shared.append('FiniteElement', fe)
    scoped_fe = Section()
    scoped_fe.append('Order', '2')
    scope = Section()
    scope.append('FiniteElement', scoped_fe)
    inp = WorkflowInputs(cubit={'r': 1.0}, ace3p=shared,
                         scoped={'fine': {'ace3p': scope,
                                          'cubit': {'r': 2.0}}})

    fine = inp.for_module('fine')
    assert _ace3p_leaves(fine) == {'FiniteElement.Order': '2',
                                   'FiniteElement.CurvedSurfaces': 'on'}
    assert fine.cubit == {'r': 2.0}
    assert inp.for_module('coarse') is inp
    assert _ace3p_leaves(inp)['FiniteElement.Order'] == '1'   # not mutated


def test_build_inputs_splits_scopes_only_for_declared_module_names():
    """A key under a bucket is a scope only when it names a declared module and
    holds a mapping; a ``{min, max, num}`` range named like a module is still a
    shared variable, and with no module names nothing is scoped (the call every
    existing caller makes)."""
    data = {'input_parameters': {'cubit': {
                'fine': {'radius': [1.0, 2.0]},
                'coarse': {'min': 0, 'max': 1, 'num': 2},
                'length': 3.0}},
            'ace3p_input_parameters': [
                ('fine', [('FiniteElement', [('Order', 2)])]),
                ('ModelInfo', [('File', 'cav.ncdf')])]}

    scoped = build_inputs(data, module_names=['fine', 'coarse'])
    assert set(scoped.cubit) == {'coarse', 'length'}
    assert scoped.scoped['fine']['cubit'] == {'radius': [1.0, 2.0]}
    assert [name for name, _ in scoped.ace3p.entries] == ['ModelInfo']
    assert [name for name, _ in scoped.scoped['fine']['ace3p'].entries] == \
        ['FiniteElement']

    unscoped = build_inputs(data)
    assert unscoped.scoped == {}
    assert set(unscoped.cubit) == {'fine', 'coarse', 'length'}


def test_a_scope_shaped_particles_block_warns(capsys):
    """``particles:`` is not scoped (§3.1): a block named like a module is read
    as one variable holding a mapping, so it says so."""
    build_inputs({'input_parameters': {'particles': {
        'field_emission': {'beta': 50}}}}, module_names=['field_emission'])
    assert "particles bucket is not scoped" in capsys.readouterr().out


def _scoped_inputs():
    data = {'input_parameters': {'cubit': {
                'radius': 1.0,
                'fine': {'mesh_size': [0.1, 0.2]}},
            'geant4': {'dose2': {'nthreads': 4}}},
            'ace3p_input_parameters': [
                ('coarse', [('FiniteElement', [('Order', [1, 2])])]),
                ('fine', [('FiniteElement', [('Order', 3)])]),
                ('ModelInfo', [('Sigma', 5.8e7)])]}
    return build_inputs(data, module_names=['coarse', 'fine', 'dose2'])


def test_scoped_sweep_axes_carry_the_module_segment():
    """Scoped axes are labelled ``cubit:fine/x`` / ``ace3p:fine/Sec.Leaf`` and
    follow the shared ones, whose labels do not change (§3.3); materializing a
    point sets the scoped leaf and nothing else."""
    inp = _scoped_inputs()
    labels = [label for label, _values, _setter in inp.sweep_axes()]
    assert labels == ['cubit:fine/mesh_size', 'ace3p:coarse/FiniteElement.Order']

    point = inp.materialize([0.2, 2])
    assert point.scoped['fine']['cubit']['mesh_size'] == 0.2
    assert _ace3p_leaves(point.for_module('coarse'))['FiniteElement.Order'] == '2'
    assert _ace3p_leaves(point.for_module('fine'))['FiniteElement.Order'] == '3'
    assert inp.scoped['fine']['cubit']['mesh_size'] == [0.1, 0.2]  # base intact


def test_vocs_routes_scoped_labels_and_unique_bare_leaves():
    """The VOCS registry routes every scoped leaf by its qualified label, a bare
    leaf unique across buckets *and* scopes by its bare name, and refuses a bare
    leaf two scopes share, listing both qualified spellings."""
    inp = _scoped_inputs()
    out = inp.apply_overrides({'ace3p:fine/FiniteElement.Order': 4,
                               'mesh_size': 0.15,
                               'nthreads': 8,
                               'geant4:dose2/nthreads': 9})
    assert _ace3p_leaves(out.for_module('fine'))['FiniteElement.Order'] == '4'
    assert out.scoped['fine']['cubit']['mesh_size'] == 0.15
    assert out.scoped['dose2']['macro']['nthreads'] == 9
    assert 'mesh_size' not in out.cubit

    with pytest.raises(ValueError, match='ace3p:coarse/FiniteElement.Order'):
        inp.apply_overrides({'Order': 2})


def test_canonical_is_the_pre_scope_shape_without_scopes():
    """``config_hash`` hashes :meth:`WorkflowInputs.canonical`; without scopes it
    must be exactly what rendering the attributes gave before scopes existed, or
    every resumable campaign on disk reads ``stale`` (§3.7). With one, the scope
    is in the hash."""
    from lume_ace3p import state

    plain = WorkflowInputs(cubit={'r': 1.0}, particles={'beta': 2})
    legacy = {'WorkflowInputs': state._canonical(
        {'cubit': plain.cubit, 'ace3p': plain.ace3p, 'macro': plain.macro,
         'particles': plain.particles})}
    assert state._canonical(plain) == legacy

    scoped = _scoped_inputs()
    a = state.config_hash([], scoped, {})
    scoped.scoped['fine']['cubit']['mesh_size'] = 0.3
    assert state.config_hash([], scoped, {}) != a


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
