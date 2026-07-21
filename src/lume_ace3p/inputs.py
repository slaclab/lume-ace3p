"""Structured representation of YAML-driven workflow inputs.

The pipeline previously flattened every YAML entry into a single dict whose
keys encoded the (subsystem, nested path, discriminator) triple as a string
(e.g. ``ACE3PModelInfo_SurfaceMaterial?LILA?6?LILA&_Sigma``). That worked but
required several layers of escape sentinels and made it impossible to express
duplicate-named ACE3P sections cleanly.

This module replaces that with a small, explicit data model:

  WorkflowInputs(
      cubit     = {var_name: scalar | numpy.ndarray, ...},
      ace3p     = Section(...),                # tree of (name, child) pairs
      macro     = {macro_cmd: scalar | numpy.ndarray, ...},
      particles = {var_name: scalar | numpy.ndarray, ...},
  )

`sweep_axes()` walks all four buckets and surfaces array-valued leaves as
named sweep axes. `materialize(axis_values)` returns a fresh ``WorkflowInputs``
with each swept leaf collapsed to a scalar — that's what the workflow hands
to the per-iteration `set_value` calls.
"""

import numpy as np
from ruamel.yaml import YAML

from lume_ace3p.ace3p import Section


class WorkflowInputs:
    def __init__(self, cubit=None, ace3p=None, macro=None, particles=None):
        self.cubit = dict(cubit) if cubit else {}
        self.ace3p = ace3p if ace3p is not None else Section()
        self.macro = dict(macro) if macro else {}
        self.particles = dict(particles) if particles else {}

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

        for name, value in self.particles.items():
            if _is_array(value):
                axes.append((name, np.asarray(value),
                             _make_particles_setter(name)))

        return axes

    def materialize(self, axis_scalars):
        """Return a copy with each swept leaf replaced by the given scalar.

        `axis_scalars` is a list aligned with `sweep_axes()`.
        """
        copy = WorkflowInputs(
            cubit=dict(self.cubit),
            ace3p=_clone_section(self.ace3p),
            macro=dict(self.macro),
            particles=dict(self.particles),
        )
        for (label, values, setter), scalar in zip(self.sweep_axes(), axis_scalars):
            setter(copy, scalar)
        return copy

    # ---- variable routing (optimize) -------------------------------------

    def _route_registry(self):
        """Build the map from a VOCS variable name to a `(bucket, setter)`.

        Each declared variable is registered under its fully-qualified label
        (``cubit:name`` / ``ace3p:Section.Leaf`` / ``geant4:name`` /
        ``particles:name``) — always
        unambiguous — and, when the *bare* name is unique across all buckets,
        under that bare name too. A bare name declared in more than one bucket
        is recorded in `ambiguous` and left out of the bare routes, so a bare
        reference to it is a hard error (the caller must qualify it).

        Returns `(routes, ambiguous)` where `routes` maps name -> setter and
        `ambiguous` maps a colliding bare name -> sorted list of its qualified
        labels."""
        qualified = {}   # qualified label -> setter
        bare_hits = {}   # bare name -> list of (qualified label, setter)

        def add(bare, qualified_label, setter):
            qualified[qualified_label] = setter
            bare_hits.setdefault(bare, []).append((qualified_label, setter))

        for name in self.cubit:
            add(name, f'cubit:{name}', _make_cubit_setter(name))
        for path, _ in _walk_ace3p(self.ace3p):
            label = _label_ace3p_path(path)          # e.g. 'ace3p:FrequencyScan.Start'
            bare = path[-1][0]                       # terminal leaf name
            add(bare, label, _make_ace3p_setter(path))
        for name in self.macro:
            add(name, f'geant4:{name}', _make_macro_setter(name))
        for name in self.particles:
            add(name, f'particles:{name}', _make_particles_setter(name))

        routes = dict(qualified)
        ambiguous = {}
        for bare, hits in bare_hits.items():
            if len(hits) == 1:
                routes[bare] = hits[0][1]
            else:
                ambiguous[bare] = sorted(label for label, _ in hits)
        return routes, ambiguous

    def apply_overrides(self, overrides):
        """Return a copy with each `{name: value}` override applied to the bucket
        where `name` is declared. Used by the optimize / DOE modes, whose variable
        names route to the cubit / ace3p / macro / particles buckets.

        `name` may be a bare variable name (allowed only when unique across
        buckets) or a fully-qualified label (``cubit:…`` / ``ace3p:…`` /
        ``geant4:…`` / ``particles:…``). A bare name declared in more than one
        bucket raises a `ValueError`. A name not declared in any bucket falls
        back to the cubit bucket (back-compat with configs that declare VOCS
        variables but no `input_parameters`)."""
        copy = WorkflowInputs(
            cubit=dict(self.cubit),
            ace3p=_clone_section(self.ace3p),
            macro=dict(self.macro),
            particles=dict(self.particles),
        )
        routes, ambiguous = self._route_registry()
        for name, value in overrides.items():
            if name in routes:
                routes[name](copy, value)
            elif name in ambiguous:
                raise ValueError(
                    f"variable '{name}' is declared in more than one input "
                    f"bucket; qualify it as one of {ambiguous[name]}.")
            else:
                copy.cubit[name] = value
        return copy


# ---- YAML loading --------------------------------------------------------


# Reserved sub-block names under a nested `input_parameters:` mapping. Each maps
# to one WorkflowInputs bucket (geant4 -> macro).
_INPUT_BUCKETS = ('cubit', 'ace3p', 'geant4', 'particles')


def load_yaml(path):
    """Load a LUME-ACE3P YAML, returning the raw mapping.

    The ACE3P inputs are unique in allowing duplicate keys (one block per
    same-named ACE3P section). We extract that block textually, parse it as a
    list of pairs, and parse the remainder of the file as a normal mapping. The
    ACE3P block may be given either as the nested `input_parameters: {ace3p: …}`
    sub-block (the standard notation) or as the flat top-level
    `ace3p_input_parameters:` key (a deprecated back-compat alias); both land in
    the canonical `ace3p_input_parameters` slot of the returned mapping.
    """
    with open(path) as f:
        text = f.read()
    raw_ace3p, text = _extract_nested_block(text, ['input_parameters', 'ace3p'])
    if raw_ace3p is None:
        raw_ace3p, text = _extract_nested_block(text, ['ace3p_input_parameters'])
    yaml = YAML(typ='safe')
    data = yaml.load(text) or {}
    if raw_ace3p is not None:
        data['ace3p_input_parameters'] = _load_pairs(raw_ace3p)
    return data


def _is_nested_input_parameters(block):
    """True if an `input_parameters:` value uses the nested bucket notation
    (`{cubit: …, ace3p: …, geant4: …, particles: …}`) rather than the legacy
    flat cubit block. A non-empty mapping whose keys are all reserved bucket
    names is treated as nested."""
    return isinstance(block, dict) and bool(block) and all(
        key in _INPUT_BUCKETS for key in block)


def build_inputs(yaml_data):
    """Translate a loaded YAML mapping into a WorkflowInputs.

    Accepts the standard nested notation ::

        input_parameters:
          cubit:     {…}   # -> cubit bucket
          ace3p:     {…}   # -> ace3p bucket (duplicate-key aware)
          geant4:    {…}   # -> macro bucket
          particles: {…}   # -> particles bucket (e.g. field-enhancement β)

    as well as the deprecated flat aliases (`cubit_input_parameters`,
    `ace3p_input_parameters`, `geant4_input_parameters`,
    `particles_input_parameters`, and a bare `input_parameters` treated as the
    cubit block).
    """
    cubit = {}
    macro = {}
    particles = {}

    input_params = yaml_data.get('input_parameters')
    if _is_nested_input_parameters(input_params):
        _collect_scalar_block(input_params.get('cubit'), cubit)
        _collect_scalar_block(input_params.get('geant4'), macro)
        _collect_scalar_block(input_params.get('particles'), particles)
        # The nested `ace3p:` sub-block was lifted into `ace3p_input_parameters`
        # by load_yaml (duplicate-key aware), so it is read below.
    else:
        # Legacy: a bare `input_parameters` block is the cubit bucket.
        _collect_scalar_block(input_params, cubit)

    # Deprecated flat aliases (still honored for back-compat).
    _collect_scalar_block(yaml_data.get('cubit_input_parameters'), cubit)
    _collect_scalar_block(yaml_data.get('geant4_input_parameters'), macro)
    _collect_scalar_block(yaml_data.get('particles_input_parameters'), particles)

    ace3p = _build_section(yaml_data.get('ace3p_input_parameters') or [])

    return WorkflowInputs(cubit=cubit, ace3p=ace3p, macro=macro,
                          particles=particles)


# ---- internal helpers ----------------------------------------------------


def _is_array(value):
    return isinstance(value, np.ndarray) or (
        isinstance(value, (list, tuple)) and len(value) > 1
        and all(np.isscalar(v) for v in value)
    )


def _indent(line):
    return len(line) - len(line.lstrip())


def _line_key_matches(line, key):
    """True if `line` is the `key:` mapping header (allowing `key :`)."""
    s = line.strip()
    if not s.startswith(key):
        return False
    return s[len(key):].lstrip().startswith(':')


def _extract_nested_block(text, path):
    """Split `text` into (block, remainder), where `block` is the indented body
    of the mapping key reached by following `path` (a list of keys, each nested
    one level under the previous). `block` has its common leading indentation
    stripped so the inner mapping starts at column 0. `remainder` is the original
    text with that block removed (its header line is dropped too). Returns
    (None, text) if the path is not found.

    A single-element path locates a top-level key; a two-element path
    (e.g. ``['input_parameters', 'ace3p']``) locates a direct sub-block. This
    textual pre-extraction is what lets the ACE3P block preserve duplicate keys
    (see :func:`_load_pairs`)."""
    lines = text.split('\n')
    lo, hi = 0, len(lines)
    expected_indent = 0
    for depth, key in enumerate(path):
        header_idx = None
        for i in range(lo, hi):
            line = lines[i]
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if _indent(line) == expected_indent and _line_key_matches(line, key):
                header_idx = i
                break
        if header_idx is None:
            return None, text
        # Body runs until the next line at or above the header's indent level.
        body_lo = header_idx + 1
        body_hi = hi
        for j in range(header_idx + 1, hi):
            l = lines[j]
            if l.strip() and not l.lstrip().startswith('#') \
                    and _indent(l) <= expected_indent:
                body_hi = j
                break
        body_lines = lines[body_lo:body_hi]
        indents = [_indent(l) for l in body_lines
                   if l.strip() and not l.lstrip().startswith('#')]
        if depth < len(path) - 1:
            # Descend: the next key sits at the body's shallowest indent.
            if not indents:
                return None, text
            expected_indent = min(indents)
            lo, hi = body_lo, body_hi
            continue
        # Target reached — return its de-indented body and the remainder.
        pad = min(indents) if indents else 0
        block = '\n'.join(l[pad:] if len(l) >= pad else l for l in body_lines)
        remainder = '\n'.join(lines[:header_idx] + lines[body_hi:])
        return block, remainder
    return None, text


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


def _make_particles_setter(name):
    def setter(inputs, value):
        inputs.particles[name] = value
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
