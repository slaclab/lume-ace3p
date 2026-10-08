"""Structured representation of YAML-driven workflow inputs.

The pipeline previously flattened every YAML entry into a single dict whose
keys encoded the (subsystem, nested path, discriminator) triple as a string
(e.g. ``ACE3PModelInfo_SurfaceMaterial?LILA?6?LILA&_Sigma``). That worked but
required several layers of escape sentinels and made it impossible to express
duplicate-named ACE3P sections cleanly.

This module replaces that with a small, explicit data model::

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

A fifth, optional ``scoped`` mapping holds the module-scoped blocks
(``ace3p: {fine: {…}}``) — inputs only one named module reads.
`for_module(name)` layers a scope over the shared buckets; see
plans/multi_instance_workflow_plan.md §3.1.
"""

import numpy as np
from ruamel.yaml import YAML

from lume_ace3p.ace3p import Section, merge_overrides


# The buckets a module-scoped block may carry (plans/multi_instance_workflow_plan.md
# §3.1), with the label prefix each one's axes are named under. ``particles`` is
# deliberately absent: ``field_emission`` reads its variables by name, so two such
# modules name different variables rather than scoping the same one.
_SCOPED_BUCKETS = (('cubit', 'cubit'), ('ace3p', 'ace3p'), ('macro', 'geant4'))


class WorkflowInputs:
    def __init__(self, cubit=None, ace3p=None, macro=None, particles=None,
                 scoped=None):
        self.cubit = dict(cubit) if cubit else {}
        self.ace3p = ace3p if ace3p is not None else Section()
        self.macro = dict(macro) if macro else {}
        self.particles = dict(particles) if particles else {}
        # {module name: {'cubit': dict, 'ace3p': Section, 'macro': dict}} — the
        # inputs only that module reads, layered over the shared buckets by
        # :meth:`for_module`. Every scope carries all three buckets.
        self.scoped = {}
        for name, buckets in (scoped or {}).items():
            buckets = buckets or {}
            ace3p_scope = buckets.get('ace3p')
            self.scoped[str(name)] = {
                'cubit': dict(buckets.get('cubit') or {}),
                'ace3p': ace3p_scope if ace3p_scope is not None else Section(),
                'macro': dict(buckets.get('macro') or {}),
            }

    def canonical(self):
        """The rendering :func:`lume_ace3p.state.config_hash` hashes.

        With no scopes declared this is exactly the shape the hash had before
        scopes existed (the class name over the four buckets), so the hash of
        every unscoped configuration stays where it was
        (plans/multi_instance_workflow_plan.md §3.7)."""
        fields = {'cubit': self.cubit, 'ace3p': self.ace3p,
                  'macro': self.macro, 'particles': self.particles}
        if self.scoped:
            fields['scoped'] = self.scoped
        return {type(self).__name__: fields}

    def for_module(self, name):
        """The inputs module ``name`` reads: each scoped bucket is the shared one
        with ``scoped[name]`` layered over it, the scoped value winning at the same
        path; ``particles`` is the shared bucket. A module without a scope gets
        this object itself, so an unscoped workflow is unchanged."""
        scope = self.scoped.get(name)
        if scope is None:
            return self
        ace3p = _clone_section(self.ace3p)
        merge_overrides(ace3p, scope['ace3p'])
        return WorkflowInputs(cubit={**self.cubit, **scope['cubit']},
                              ace3p=ace3p,
                              macro={**self.macro, **scope['macro']},
                              particles=self.particles)

    def _copy(self):
        return WorkflowInputs(
            cubit=dict(self.cubit),
            ace3p=_clone_section(self.ace3p),
            macro=dict(self.macro),
            particles=dict(self.particles),
            scoped={name: {'cubit': dict(scope['cubit']),
                           'ace3p': _clone_section(scope['ace3p']),
                           'macro': dict(scope['macro'])}
                    for name, scope in self.scoped.items()},
        )

    def _leaves(self):
        """Yield ``(axis label, qualified label, bare name, value, setter)`` for
        every input leaf.

        The shared buckets come first, in the order and with the labels they have
        always had (``name`` / ``ace3p:Sec.Leaf``), then each scope's leaves under
        a scope segment (``cubit:fine/name``, ``ace3p:fine/Sec.Leaf``,
        ``geant4:fine/name``). The axis label names a sweep axis; the qualified
        label is the always-unambiguous VOCS spelling, which differs only for the
        shared bare-named buckets."""
        for name, value in self.cubit.items():
            yield name, f'cubit:{name}', name, value, _make_cubit_setter(name)
        for path, value in _walk_ace3p(self.ace3p):
            label = _label_ace3p_path(path)
            yield label, label, path[-1][0], value, _make_ace3p_setter(path)
        for name, value in self.macro.items():
            yield name, f'geant4:{name}', name, value, _make_macro_setter(name)
        for name, value in self.particles.items():
            yield (name, f'particles:{name}', name, value,
                   _make_particles_setter(name))
        for scope, buckets in self.scoped.items():
            for name, value in buckets['cubit'].items():
                label = f'cubit:{scope}/{name}'
                yield (label, label, name, value,
                       _make_scoped_setter(scope, 'cubit', name))
            for path, value in _walk_ace3p(buckets['ace3p']):
                label = _label_ace3p_path(path, scope)
                yield (label, label, path[-1][0], value,
                       _make_ace3p_setter(path, scope))
            for name, value in buckets['macro'].items():
                label = f'geant4:{scope}/{name}'
                yield (label, label, name, value,
                       _make_scoped_setter(scope, 'macro', name))

    # ---- sweep machinery -------------------------------------------------

    def sweep_axes(self):
        """Yield (label, values, setter) for every array-valued leaf.

        `label` is a stable, human-readable identifier used in workdir names
        and sweep_data tuple keys. `setter(materialized, scalar)` mutates the
        materialized copy at the leaf's location. Shared axes come first, with
        the labels they have always had; a scoped leaf's label carries its module
        name (``ace3p:fine/FiniteElement.Order``).
        """
        return [(label, np.asarray(value), setter)
                for label, _qualified, _bare, value, setter in self._leaves()
                if _is_array(value)]

    def materialize(self, axis_scalars):
        """Return a copy with each swept leaf replaced by the given scalar.

        `axis_scalars` is a list aligned with `sweep_axes()`.
        """
        copy = self._copy()
        for (label, values, setter), scalar in zip(self.sweep_axes(), axis_scalars):
            setter(copy, scalar)
        return copy

    def scoped_scalars(self, buckets=('cubit', 'macro')):
        """``{label: value}`` for every scoped leaf of ``buckets`` (of ``cubit`` /
        ``ace3p`` / ``macro``), labelled as its sweep axis would be. What a workdir
        name and a manifest's ``point`` record add for a scoped configuration;
        empty without scopes."""
        prefixes = tuple(f'{prefix}:' for bucket, prefix in _SCOPED_BUCKETS
                         if bucket in buckets)
        return {label: value
                for label, _qualified, _bare, value, _setter in self._leaves()
                if '/' in label and label.startswith(prefixes)}

    # ---- variable routing (optimize) -------------------------------------

    def _route_registry(self):
        """Build the map from a VOCS variable name to a `(bucket, setter)`.

        Each declared variable is registered under its fully-qualified label
        (``cubit:name`` / ``ace3p:Section.Leaf`` / ``geant4:name`` /
        ``particles:name``, and for a scoped leaf ``cubit:fine/name`` /
        ``ace3p:fine/Section.Leaf`` / ``geant4:fine/name``) — always
        unambiguous — and, when the *bare* name is unique across all buckets and
        scopes, under that bare name too. A bare name declared more than once
        is recorded in `ambiguous` and left out of the bare routes, so a bare
        reference to it is a hard error (the caller must qualify it).

        Returns `(routes, ambiguous)` where `routes` maps name -> setter and
        `ambiguous` maps a colliding bare name -> sorted list of its qualified
        labels."""
        qualified = {}   # qualified label -> setter
        bare_hits = {}   # bare name -> list of (qualified label, setter)

        for _label, label, bare, _value, setter in self._leaves():
            qualified[label] = setter
            bare_hits.setdefault(bare, []).append((label, setter))

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
        buckets and scopes) or a fully-qualified label (``cubit:…`` /
        ``ace3p:…`` / ``geant4:…`` / ``particles:…``, with a ``<module>/``
        segment for a scoped leaf). A bare name declared more than once raises
        a `ValueError`. A name not declared in any bucket falls
        back to the cubit bucket (back-compat with configs that declare VOCS
        variables but no `input_parameters`)."""
        copy = self._copy()
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

# Every top-level block a LUME-ACE3P config may carry, for the unrecognized-key
# warning. Closed, and a typo in one is silent otherwise: an 'output_parameter'
# (singular) block is simply never read, so the run extracts nothing and says nothing.
TOP_LEVEL_KEYS = frozenset({
    'workflow', 'workflow_parameters', 'mode', 'input_parameters',
    'output_parameters', 'derived_parameters', 'vocs_parameters', 'xopt_parameters', 'sweep_parameters',
    # Deprecated flat aliases, still honored by build_inputs below.
    'cubit_input_parameters', 'ace3p_input_parameters',
    'geant4_input_parameters', 'particles_input_parameters'})


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


def _warn_partial_buckets(block):
    """Warn when an ``input_parameters:`` block looks like the nested bucket notation
    but has an unrecognized bucket name.

    :func:`_is_nested_input_parameters` requires *every* key to be a bucket, so one
    typo ('qubit:') silently reinterprets the **whole block** as the legacy flat cubit
    block — every bucket name becomes a Cubit variable and every real parameter is
    dropped. That is the most destructive silent misroute in the config surface, and
    it is invisible: the run proceeds with no parameters applied."""
    if not isinstance(block, dict) or not block:
        return
    unknown = [str(key) for key in block if str(key) not in _INPUT_BUCKETS]
    if not unknown or len(unknown) == len(block):
        # All buckets (fine), or none of them (the legacy flat cubit block, which is
        # a documented shape rather than a mistake).
        return
    print(f"Warning: 'input_parameters' mixes bucket names with "
          f"{', '.join(repr(key) for key in sorted(unknown))}, which is not a "
          f"recognized bucket ({', '.join(_INPUT_BUCKETS)}). The whole block is "
          "therefore read as the legacy flat cubit block, so the bucket names become "
          "Cubit variables and their contents are dropped. Fix the spelling.")


def build_inputs(yaml_data, module_names=()):
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

    `module_names` are the workflow's module names. A key directly under
    `cubit:` / `ace3p:` / `geant4:` that equals one of them and holds a mapping
    (other than a `{min, max, num}` range) is a *scope*: its contents reach that
    module only, layered over the shared bucket (see
    :meth:`WorkflowInputs.for_module`). Called with no names, nothing is scoped
    and the result is what it has always been.
    """
    module_names = {str(name) for name in module_names}
    cubit = {}
    macro = {}
    particles = {}
    scoped = {}

    def collect(block, out, bucket):
        _collect_scalar_block(
            _split_scopes(block, module_names, scoped, bucket), out)

    input_params = yaml_data.get('input_parameters')
    _warn_partial_buckets(input_params)
    if _is_nested_input_parameters(input_params):
        collect(input_params.get('cubit'), cubit, 'cubit')
        collect(input_params.get('geant4'), macro, 'macro')
        _warn_scoped_particles(input_params.get('particles'), module_names)
        _collect_scalar_block(input_params.get('particles'), particles)
        # The nested `ace3p:` sub-block was lifted into `ace3p_input_parameters`
        # by load_yaml (duplicate-key aware), so it is read below.
    else:
        # Legacy: a bare `input_parameters` block is the cubit bucket.
        collect(input_params, cubit, 'cubit')

    # Deprecated flat aliases (still honored for back-compat).
    collect(yaml_data.get('cubit_input_parameters'), cubit, 'cubit')
    collect(yaml_data.get('geant4_input_parameters'), macro, 'macro')
    _warn_scoped_particles(yaml_data.get('particles_input_parameters'),
                           module_names)
    _collect_scalar_block(yaml_data.get('particles_input_parameters'), particles)

    shared_pairs = []
    for key, value in yaml_data.get('ace3p_input_parameters') or []:
        if str(key) in module_names and _is_pairs(value):
            scope = scoped.setdefault(str(key), {})
            scope.setdefault('ace3p', Section()).entries.extend(
                _build_section(value).entries)
        else:
            shared_pairs.append((key, value))
    ace3p = _build_section(shared_pairs)

    return WorkflowInputs(cubit=cubit, ace3p=ace3p, macro=macro,
                          particles=particles, scoped=scoped)


def _is_range(value):
    return isinstance(value, dict) and {'min', 'max', 'num'} <= set(value)


def _split_scopes(block, module_names, scoped, bucket):
    """`block` without its scope keys; each scope's contents are collected into
    ``scoped[name][bucket]``."""
    if not block or not module_names or not isinstance(block, dict):
        return block
    shared = {}
    for key, value in block.items():
        if str(key) in module_names and isinstance(value, dict) \
                and not _is_range(value):
            out = scoped.setdefault(str(key), {}).setdefault(bucket, {})
            _collect_scalar_block(value, out)
        else:
            shared[key] = value
    return shared


def _warn_scoped_particles(block, module_names):
    """A scope-shaped key under `particles:` is not a scope (§3.1 of
    plans/multi_instance_workflow_plan.md): `field_emission` reads its variables
    by name, so the key would be taken as one variable holding a mapping."""
    if not isinstance(block, dict):
        return
    for key, value in block.items():
        if str(key) in module_names and isinstance(value, dict) \
                and not _is_range(value):
            print(f"Warning: 'particles:' has a block named '{key}', which is a "
                  "module name, but the particles bucket is not scoped by module "
                  "— a field_emission module reads its variables by name "
                  "('beta_input' / 'beta_inputs'), so give each module its own "
                  "variable name instead.")


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
        # De-indent by the body's own indent, but only lines that actually carry
        # it. The block span runs to the next *non-comment* line at or above the
        # header's indent (comments are skipped when looking for the end), so it
        # can contain a column-0 comment belonging to the following top-level
        # key -- and slicing `pad` characters off that would eat its '#' and turn
        # a comment into a broken YAML key.
        block = '\n'.join(l[pad:] if _indent(l) >= pad else l
                          for l in body_lines)
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


def _is_pairs(value):
    """True for a nested mapping as :func:`_load_pairs` renders it."""
    return isinstance(value, list) and bool(value) and all(
        isinstance(p, tuple) and len(p) == 2 for p in value)


def _build_section(pairs):
    """Recursively turn a list of (key, value) pairs into a Section tree.
    Leaves are stringified to match the .ace3p text representation."""
    section = Section()
    for key, value in pairs:
        if _is_pairs(value):
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


def _label_ace3p_path(path, scope=None):
    parts = []
    for name, idx in path:
        parts.append(f'{name}[{idx}]' if idx > 0 else name)
    return 'ace3p:' + (f'{scope}/' if scope else '') + '.'.join(parts)


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


def _make_scoped_setter(scope, bucket, name):
    def setter(inputs, value):
        inputs.scoped[scope][bucket][name] = value
    return setter


def _make_ace3p_setter(path, scope=None):
    def setter(inputs, value):
        section = (inputs.ace3p if scope is None
                   else inputs.scoped[scope]['ace3p'])
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
