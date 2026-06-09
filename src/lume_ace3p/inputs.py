"""Structured representation of YAML-driven workflow inputs.

The pipeline previously flattened every YAML entry into a single dict whose
keys encoded the (subsystem, nested path, discriminator) triple as a string
(e.g. ``ACE3PModelInfo_SurfaceMaterial?LILA?6?LILA&_Sigma``). That worked but
required several layers of escape sentinels and made it impossible to express
duplicate-named ACE3P sections cleanly.

This module replaces that with a small, explicit data model:

  WorkflowInputs(
      cubit  = {var_name: scalar | numpy.ndarray, ...},
      ace3p  = Section(...),                   # tree of (name, child) pairs
      macro  = {macro_cmd: scalar | numpy.ndarray, ...},
  )

`sweep_axes()` walks all three buckets and surfaces array-valued leaves as
named sweep axes. `materialize(axis_values)` returns a fresh ``WorkflowInputs``
with each swept leaf collapsed to a scalar — that's what the workflow hands
to the per-iteration `set_value` calls.
"""

import numpy as np
from ruamel.yaml import YAML

from lume_ace3p.ace3p import Section


class WorkflowInputs:
    def __init__(self, cubit=None, ace3p=None, macro=None):
        self.cubit = dict(cubit) if cubit else {}
        self.ace3p = ace3p if ace3p is not None else Section()
        self.macro = dict(macro) if macro else {}

    # ---- sweep machinery -------------------------------------------------

    def sweep_axes(self):
        """Yield (label, values, setter) for every array-valued leaf.

        `label` is a stable, human-readable identifier used in workdir names
        and sweep_data tuple keys. `setter(materialized, scalar)` mutates the
        materialized copy at the leaf's location.
        """
        axes = []

        for name, value in self.cubit.items():
            if _is_array(value):
                axes.append((name, np.asarray(value),
                             _make_cubit_setter(name)))

        for path, value in _walk_ace3p(self.ace3p):
            if _is_array(value):
                axes.append((_label_ace3p_path(path), np.asarray(value),
                             _make_ace3p_setter(path)))

        for name, value in self.macro.items():
            if _is_array(value):
                axes.append((name, np.asarray(value),
                             _make_macro_setter(name)))

        return axes

    def materialize(self, axis_scalars):
        """Return a copy with each swept leaf replaced by the given scalar.

        `axis_scalars` is a list aligned with `sweep_axes()`.
        """
        copy = WorkflowInputs(
            cubit=dict(self.cubit),
            ace3p=_clone_section(self.ace3p),
            macro=dict(self.macro),
        )
        for (label, values, setter), scalar in zip(self.sweep_axes(), axis_scalars):
            setter(copy, scalar)
        return copy


# ---- YAML loading --------------------------------------------------------


def load_yaml(path):
    """Load a LUME-ACE3P YAML, returning the raw mapping.

    `ace3p_input_parameters` is unique in allowing duplicate keys (one block
    per same-named ACE3P section). We extract that block, parse it as a list
    of pairs, and parse the remainder of the file as a normal mapping.
    """
    with open(path) as f:
        text = f.read()
    raw_ace3p, remainder = _extract_top_block(text, 'ace3p_input_parameters')
    yaml = YAML(typ='safe')
    data = yaml.load(remainder) or {}
    if raw_ace3p is not None:
        data['ace3p_input_parameters'] = _load_pairs(raw_ace3p)
    return data


def build_inputs(yaml_data):
    """Translate a loaded YAML mapping into a WorkflowInputs."""
    cubit = {}
    _collect_scalar_block(yaml_data.get('cubit_input_parameters'), cubit)
    _collect_scalar_block(yaml_data.get('input_parameters'), cubit)

    macro = {}
    _collect_scalar_block(yaml_data.get('geant4_input_parameters'), macro)

    ace3p = _build_section(yaml_data.get('ace3p_input_parameters') or [])

    return WorkflowInputs(cubit=cubit, ace3p=ace3p, macro=macro)


# ---- internal helpers ----------------------------------------------------


def _is_array(value):
    return isinstance(value, np.ndarray) or (
        isinstance(value, (list, tuple)) and len(value) > 1
        and all(np.isscalar(v) for v in value)
    )


def _extract_top_block(text, key):
    """Split `text` into (block, remainder). `block` is the indented body
    of `key:` with leading indentation stripped (so the inner mapping starts
    at column 0). `remainder` is the original text with that block removed
    (the `key:` header line is also dropped). Returns (None, text) if the
    key isn't found at the top level."""
    lines = text.split('\n')
    header = None
    for i, line in enumerate(lines):
        s = line.lstrip()
        if not s or s.startswith('#'):
            continue
        if line.startswith(key) and ':' in line[:len(key) + 5]:
            header = i
            break
    if header is None:
        return None, text
    end = len(lines)
    for j in range(header + 1, len(lines)):
        line = lines[j]
        if line and not line[0].isspace() and not line.lstrip().startswith('#'):
            end = j
            break
    body_lines = lines[header + 1:end]
    # Find common leading indent and strip it
    indents = [len(l) - len(l.lstrip()) for l in body_lines if l.strip()]
    pad = min(indents) if indents else 0
    block = '\n'.join(l[pad:] if len(l) >= pad else l for l in body_lines)
    remainder = '\n'.join(lines[:header] + lines[end:])
    return block, remainder


def _load_pairs(text):
    """Parse a YAML mapping, returning a list of (key, value) pairs that
    preserves duplicate keys. Nested mappings are recursively pairs too."""
    from ruamel.yaml.constructor import SafeConstructor

    def construct_pairs(loader, node):
        result = []
        for k, v in node.value:
            key = loader.construct_object(k, deep=True)
            value = loader.construct_object(v, deep=True)
            result.append((key, value))
        return result

    # Subclass per-call so add_constructor doesn't bleed into other YAML loads
    PairsConstructor = type('PairsConstructor', (SafeConstructor,), {
        'yaml_constructors': dict(SafeConstructor.yaml_constructors),
        'yaml_multi_constructors': dict(SafeConstructor.yaml_multi_constructors),
    })
    PairsConstructor.add_constructor('tag:yaml.org,2002:map', construct_pairs)

    yaml = YAML(typ='safe')
    yaml.Constructor = PairsConstructor
    return yaml.load(text) or []


def _build_section(pairs):
    """Recursively turn a list of (key, value) pairs into a Section tree.
    Leaves are stringified to match the .ace3p text representation."""
    section = Section()
    for key, value in pairs:
        if isinstance(value, list) and value and all(
            isinstance(p, tuple) and len(p) == 2 for p in value
        ):
            section.append(str(key), _build_section(value))
        elif isinstance(value, list):
            # YAML list: either a sweep (>1 element of scalars) or a literal
            # comma-joined value (e.g. two ports: `Waveguide: 7,8`).
            if len(value) > 1 and all(np.isscalar(v) for v in value):
                section.append(str(key), value)  # array — sweep axis
            else:
                section.append(str(key), ', '.join(str(v) for v in value))
        else:
            section.append(str(key), str(value))
    return section


def _collect_scalar_block(block, out):
    """Translate a `{name: value}` or `{name: {min, max, num}}` block into
    flat scalar/array entries in `out`."""
    if not block:
        return
    for key, value in block.items():
        if isinstance(value, dict) and {'min', 'max', 'num'} <= set(value):
            out[key] = np.linspace(value['min'], value['max'], value['num'])
        elif isinstance(value, list):
            out[key] = value if len(value) > 1 else value[0]
        else:
            out[key] = value


# ---- ACE3P tree walking --------------------------------------------------


def _walk_ace3p(section, prefix=()):
    """Yield (path, leaf_value) for every leaf in a Section tree.

    `path` is a tuple of (name, discriminator) where discriminator is the
    same-named-sibling index (0-based) — needed because two ``Port`` blocks
    must address distinctly.
    """
    seen = {}
    for name, child in section.entries:
        idx = seen.get(name, 0)
        seen[name] = idx + 1
        if isinstance(child, Section):
            yield from _walk_ace3p(child, prefix + ((name, idx),))
        else:
            yield prefix + ((name, idx),), child


def _label_ace3p_path(path):
    parts = []
    for name, idx in path:
        parts.append(f'{name}[{idx}]' if idx > 0 else name)
    return 'ace3p:' + '.'.join(parts)


def _clone_section(section):
    out = Section()
    for name, child in section.entries:
        if isinstance(child, Section):
            out.append(name, _clone_section(child))
        else:
            out.append(name, child)
    return out


def _make_cubit_setter(name):
    def setter(inputs, value):
        inputs.cubit[name] = value
    return setter


def _make_macro_setter(name):
    def setter(inputs, value):
        inputs.macro[name] = value
    return setter


def _make_ace3p_setter(path):
    def setter(inputs, value):
        section = inputs.ace3p
        for name, idx in path[:-1]:
            same_named = [v for k, v in section.entries if k == name]
            section = same_named[idx]
        leaf_name, leaf_idx = path[-1]
        # Replace the n-th same-named leaf in section
        count = -1
        for i, (k, v) in enumerate(section.entries):
            if k == leaf_name:
                count += 1
                if count == leaf_idx:
                    section.entries[i] = (k, str(value))
                    return
    return setter
