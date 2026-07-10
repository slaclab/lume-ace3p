"""Declarative workflow builder + DAG validation (Phase 2).

This turns the YAML ``workflow:`` list (an ordered list of module entries) into
a runnable :class:`Workflow`: the modules are instantiated from
``MODULE_REGISTRY``, validated + topologically ordered by their
``requires``/``provides`` artifact edges, and executed in order against a single
:class:`RunContext` to produce artifacts and extracted ``output_parameters``.

It does NOT touch the legacy dispatch in ``run_lume_ace3p.py`` or the
``Omega3P/S3P/Geant4Workflow`` subclasses in ``workflow.py`` — those stay live
until later phases. ``WorkflowInputs`` (from ``inputs.py``) is reused unchanged
as the input model.

Design notes
------------
* **Ordering from edges.** Each artifact kind has exactly one producer (see
  :func:`_resolve_order`), so ordering is a plain dependency sort: a module is
  scheduled once every module that provides one of its ``requires`` has run.
  The YAML list order is only a stable-sort tiebreaker.
* **Additive rules.** The only structural rules are "one producer per artifact"
  and "every requirement has a producer". The per-module ``requires``/``provides``
  sets in :mod:`lume_ace3p.modules` carry the rest (solver needs ``mesh``,
  ``acdtool`` needs ``em_solution``, ``particles`` needs ``track3p_particles``,
  ``geant4`` needs ``particle_source``). A future runnable Track3P/T3P solver
  that ``provides {track3p_particles}`` slots in with no rule change: it simply
  becomes the producer that satisfies ``particles``.
* **Decoupled from modes.** :meth:`Workflow.evaluate` runs the chain once for one
  input point and returns the structured output dict. Sweep / Xopt loops (the
  mode layer) are Phase 3+; they call ``evaluate``/``sweep_axes`` from outside.
"""

import os

import numpy as np

from lume_ace3p.modules import RunContext, build_module
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.paths import resolve_paths


class WorkflowValidationError(ValueError):
    """Raised when a declared ``workflow:`` list cannot be validated into a
    runnable DAG (duplicate producer, unmet requirement, cycle, empty list)."""


# Module types whose run() invokes an ACE3P binary (cubit/omega3p/s3p/acdtool)
# vs. the Geant4 binary — used only to auto-enable dry-run when the matching
# environment is absent, mirroring the legacy per-workflow behavior.
_ACE3P_TYPES = frozenset({'cubit', 'omega3p', 's3p', 'acdtool'})
_GEANT4_TYPES = frozenset({'geant4'})


def _scalar_str(value):
    """Render a scalar for a workdir-name suffix. Matches ``workflow._scalar_str``
    so auto-mode workdir names are identical to the legacy path."""
    if isinstance(value, np.generic):
        value = value.item()
    s = str(value)
    if s.startswith('./'):
        s = s[2:]
    return s


def _infer_output_module(spec):
    """Route a legacy-shaped (bare) output spec to a module *type*.

    The target schema names the module explicitly
    (``{module: s3p, quantity: ...}``); this handles the older bare forms so the
    three legacy chains reproduce with their existing ``output_parameters``:

      * a mapping (``{quantity: 'S(0,0)', at: {...}}``) or an S-parameter string
        -> ``s3p``,
      * ``['RoverQ'|'kickFactor'|'maxFieldsOnSurface', ...]`` -> ``acdtool``,
      * ``['dose'|'edep'|'scoring', ...]`` -> ``geant4``,
      * ``'count'``/``'total_weight'`` -> ``particles``.
    """
    if isinstance(spec, dict):
        return 's3p'
    if isinstance(spec, str):
        return 'particles' if spec in ('count', 'total_weight') else 's3p'
    if isinstance(spec, (list, tuple)) and spec:
        head = spec[0]
        if head in ('RoverQ', 'kickFactor', 'maxFieldsOnSurface'):
            return 'acdtool'
        if head in ('dose', 'edep', 'scoring'):
            return 'geant4'
        if head in ('count', 'total_weight'):
            return 'particles'
        return 's3p'
    raise WorkflowValidationError(f"cannot route output spec {spec!r}.")


def _resolve_order(modules):
    """Validate the module list and return it topologically ordered.

    Raises :class:`WorkflowValidationError` (with the offending artifact/module
    named) on a duplicate producer, an unmet requirement, or a cycle."""
    if not modules:
        raise WorkflowValidationError('workflow contains no modules.')

    # One producer per artifact kind. This is what makes "two mesh sources"
    # (cubit + a mesh file, or two mesh files) a clear error.
    producer = {}
    for i, m in enumerate(modules):
        for kind in m.provides:
            if kind in producer:
                other = modules[producer[kind]]
                raise WorkflowValidationError(
                    f"artifact '{kind}' is provided by more than one module "
                    f"('{other.name}' and '{m.name}'); a workflow may declare "
                    f"only one source for each artifact.")
            producer[kind] = i

    # Every requirement must have a producer somewhere in the list.
    for m in modules:
        for kind in m.requires:
            if kind not in producer:
                raise WorkflowValidationError(
                    f"module '{m.name}' requires artifact '{kind}' but no "
                    f"module in the workflow provides it.")

    deps = {i: {producer[k] for k in m.requires} for i, m in enumerate(modules)}

    # Stable topological sort: repeatedly schedule the first module (in YAML
    # order) whose dependencies have all been scheduled.
    ordered = []
    done = set()
    remaining = list(range(len(modules)))
    while remaining:
        for pos, i in enumerate(remaining):
            if deps[i] <= done:
                ordered.append(i)
                done.add(i)
                remaining.pop(pos)
                break
        else:
            cyc = [modules[i].name for i in remaining]
            raise WorkflowValidationError(
                f"cyclic dependency among modules: {cyc}.")
    return [modules[i] for i in ordered]


class Workflow:
    """A validated, ordered chain of modules with a single ``evaluate`` seam.

    Build it with :meth:`from_config` (from a loaded YAML) or directly from a
    list of module entries. ``evaluate(input_scalars)`` runs the chain once for
    one input point and returns ``{output_name: value}``; it is deliberately
    free of any sweep/optimize loop (that is the Phase 3+ mode layer)."""

    def __init__(self, entries, workflow_params=None, inputs=None,
                 output_spec=None):
        self.workflow_params = dict(workflow_params) if workflow_params else {}
        self.inputs = inputs if inputs is not None else WorkflowInputs()
        self.output_spec = dict(output_spec) if output_spec else {}

        self.modules = _resolve_order([_build_entry(e) for e in entries])
        self.module_types = {m.type for m in self.modules}

        self.workdir_mode = self.workflow_params.get('workdir_mode', 'manual')
        self.baseworkdir = self.workflow_params.get('workdir', os.getcwd())
        self.paths = resolve_paths(self.workflow_params.get('paths'))
        self.dry_run = self._resolve_dry_run()
        self.workdir = None
        self.last_context = None

    # ---- construction ----------------------------------------------------

    @classmethod
    def from_config(cls, yaml_data):
        """Build from a loaded LUME-ACE3P YAML mapping.

        Reads the ``workflow:`` list, ``workflow_parameters`` block, and
        ``output_parameters`` spec, and builds ``WorkflowInputs`` via
        ``inputs.build_inputs`` (reused unchanged)."""
        from lume_ace3p.inputs import build_inputs
        entries = yaml_data.get('workflow')
        if entries is None:
            raise WorkflowValidationError(
                "YAML has no 'workflow:' list to build a Workflow from.")
        return cls(entries,
                   workflow_params=yaml_data.get('workflow_parameters'),
                   inputs=build_inputs(yaml_data),
                   output_spec=yaml_data.get('output_parameters'))

    def _resolve_dry_run(self):
        """Honor an explicit ``dry_run`` in workflow_parameters; otherwise
        auto-enable it when a module needs a binary whose environment is absent
        (mirrors the legacy ACE3P/Geant4 workflow auto-enable)."""
        if 'dry_run' in self.workflow_params:
            return bool(self.workflow_params['dry_run'])
        if self.module_types & _ACE3P_TYPES and not self.paths['ace3p']:
            print('ACE3P environment not configured, enabling dry run mode.')
            return True
        if self.module_types & _GEANT4_TYPES and not (
                self.paths['geant4_app_path'] and self.paths['geant4_app_exe']):
            print('Geant4 environment not configured, enabling dry run mode.')
            return True
        return False

    # ---- workdir naming (mirrors ACE3PWorkflow._getworkdir) --------------

    def _getworkdir(self, inputs, sweep_scalars=None):
        if self.workdir_mode == 'manual':
            return self.baseworkdir
        if self.workdir_mode != 'auto':
            raise ValueError("Key: 'workdir_mode' must be either 'manual' or "
                             "'auto'.")
        if sweep_scalars is None:
            parts = []
            for value in inputs.cubit.values():
                if isinstance(value, (list, tuple, np.ndarray)):
                    raise ValueError("Workflow cannot run with non-scalar "
                                     "inputs; drive it through a sweep mode.")
                parts.append(_scalar_str(value))
        else:
            parts = [_scalar_str(v) for v in sweep_scalars]
        suffix = ''.join('_' + p for p in parts)
        if self.baseworkdir is None:
            return 'lume-ace3p_workflow_output' + suffix
        return self.baseworkdir + suffix

    # ---- the single seam the modes call ----------------------------------

    def evaluate(self, input_scalars=None):
        """Run the ordered module chain once for one input point.

        ``input_scalars`` selects the input point:
          * ``None`` — use the base ``inputs`` as-is (a single run; the base
            inputs must already be scalar-valued),
          * a list aligned with :meth:`sweep_axes` — materialize that grid point,
          * a mapping — treated as cubit-parameter overrides (the shape Xopt's
            objective function passes).

        Returns ``{output_name: extracted_value}`` for the ``output_parameters``
        spec. The populated :class:`RunContext` is kept on ``self.last_context``
        so callers can reach ``artifacts``/``outputs`` after the run."""
        inputs, sweep_scalars = self._materialize(input_scalars)
        self.workdir = self._getworkdir(inputs, sweep_scalars)
        ctx = RunContext(self.workdir, inputs=inputs, dry_run=self.dry_run,
                         paths=self.paths)
        ctx.ensure_workdir()

        for module in self.modules:
            module.run(ctx)

        outputs = {}
        for name, spec in self.output_spec.items():
            module, cleaned = self._route_output(name, spec)
            outputs[name] = module.extract(ctx, cleaned)
        ctx.outputs = outputs
        self.last_context = ctx
        return outputs

    def sweep_axes(self):
        """Delegate to the input model — the swept axes a mode iterates over."""
        return self.inputs.sweep_axes()

    def output_modules(self):
        """Return ``{output_name: module}`` — the module that extracts each
        declared output. Lets a mode ask a module for its field index
        (:meth:`Module.field_index`) without solver-specific code."""
        return {name: self._route_output(name, spec)[0]
                for name, spec in self.output_spec.items()}

    def field_index(self):
        """Return ``(label, values)`` for the shared field index (e.g. S3P's
        ``('Frequency', array)``) after an :meth:`evaluate`, or ``None`` if no
        module exposes one. Scans all modules — the field index is a property of
        the solver in the chain, independent of which ``output_parameters`` were
        requested (so an S3P sweep with no declared outputs still goes
        long-format). Reads from ``self.last_context``."""
        if self.last_context is None:
            return None
        for module in self.modules:
            idx = module.field_index(self.last_context)
            if idx is not None:
                return idx
        return None

    def field(self):
        """Return the structured *field* output of the just-run evaluation
        (:meth:`Module.field`), or ``None`` if no module in the chain produces
        one. Scans the modules like :meth:`field_index`; reads from
        ``self.last_context``. The mode layer persists this per row as a field
        artifact (see :mod:`lume_ace3p.results`) — the hybrid model's structured
        half — instead of flattening it into the scalar table."""
        if self.last_context is None:
            return None
        for module in self.modules:
            fld = module.field(self.last_context)
            if fld is not None:
                return fld
        return None

    # ---- helpers ---------------------------------------------------------

    def _materialize(self, input_scalars):
        if input_scalars is None:
            return self.inputs, None
        if isinstance(input_scalars, dict):
            merged = WorkflowInputs(cubit=dict(self.inputs.cubit),
                                    ace3p=self.inputs.ace3p,
                                    macro=dict(self.inputs.macro))
            merged.cubit.update(input_scalars)
            return merged, None
        # list/tuple of axis scalars aligned with sweep_axes()
        scalars = list(input_scalars)
        return self.inputs.materialize(scalars), scalars

    def _route_output(self, name, spec):
        """Return the ``(module, cleaned_spec)`` that extracts ``spec``.

        An explicit ``module`` key in a mapping spec wins; otherwise the spec
        shape is used to infer the target module type (legacy bare specs)."""
        if isinstance(spec, dict) and 'module' in spec:
            module_type = str(spec['module']).lower()
            cleaned = {k: v for k, v in spec.items() if k != 'module'}
        else:
            module_type = _infer_output_module(spec)
            cleaned = spec
        candidates = [m for m in self.modules if m.type == module_type]
        if not candidates:
            raise WorkflowValidationError(
                f"output '{name}' targets module type '{module_type}' but no "
                f"such module is in the workflow.")
        return candidates[-1], cleaned


def _build_entry(entry):
    """Instantiate one ``workflow:`` list entry into a Module.

    An entry is a mapping with a ``module`` key naming the registry type; the
    remaining keys are that module's config. An optional ``name`` gives the
    instance a label (defaults to the type)."""
    if not isinstance(entry, dict):
        raise WorkflowValidationError(
            f"workflow entry must be a mapping with a 'module' key, got "
            f"{entry!r}.")
    config = dict(entry)
    module_type = config.pop('module', None)
    if module_type is None:
        raise WorkflowValidationError(
            f"workflow entry {entry!r} has no 'module' key.")
    name = config.pop('name', None)
    return build_module(module_type, config=config, name=name)
