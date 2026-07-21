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


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
