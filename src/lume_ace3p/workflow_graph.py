"""Declarative workflow builder + DAG validation (Phase 2).

This turns the YAML ``workflow:`` list (an ordered list of module entries) into
a runnable :class:`Workflow`: the modules are instantiated from
``MODULE_REGISTRY``, validated + topologically ordered by their
``requires``/``provides`` artifact edges, and executed in order against a single
:class:`RunContext` to produce artifacts and extracted ``output_parameters``.

``WorkflowInputs`` (from ``inputs.py``) is reused unchanged as the input model.

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
  ``geant4`` needs ``particle_source``). ``t3p`` was added this way — it provides
  ``td_solution``, distinct from ``em_solution``, so listing ``acdtool`` after a
  T3P solver is a validation error rather than RF postprocessing pointed at
  time-domain output. A future runnable Track3P solver that ``provides
  {track3p_particles}`` slots in the same way, with no rule change: it simply
  becomes the producer that satisfies ``particles``.
* **Decoupled from modes.** :meth:`Workflow.evaluate` runs the chain once for one
  input point and returns ``(outputs, ctx)`` — the structured output dict plus the
  :class:`~lume_ace3p.modules.RunContext` that produced it. Sweep / Xopt loops
  (the mode layer) are Phase 3+; they call ``evaluate``/``sweep_axes`` from
  outside.
* **The context is the per-evaluation carrier.** Every piece of run state — the
  artifacts, the extracted outputs, and the live module instances themselves —
  hangs off that ``ctx``, so two evaluations of the same ``Workflow`` cannot read
  each other's results. ``Workflow.modules`` is a separate prototype list that is
  never run and answers config-only questions.
* **Every evaluation records what it did.** ``evaluate`` writes a completion
  manifest into the evaluation's workdir (:mod:`lume_ace3p.state`), updated after
  each module, so a point cut off by the wall clock leaves behind how far it got.
  Writing it changes nothing about the run; Phase 4 of
  ``plans/evaluation_isolation_resume_plan.md`` is what reads it back.
"""

import os

import numpy as np

from lume_ace3p.modules import (
    Geant4Module, RunContext, acdtool_spec, build_module, STAGE_MODES, T3PModule,
)
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.paths import resolve_paths
from lume_ace3p.state import (
    COMPLETE, FAILED, config_hash, new_state, record_module, record_outputs,
    relative, write_state,
)


class WorkflowValidationError(ValueError):
    """Raised when a declared ``workflow:`` list cannot be validated into a
    runnable DAG (duplicate producer, unmet requirement, cycle, empty list)."""


# Module types whose run() invokes an ACE3P binary (cubit/omega3p/s3p/t3p/
# acdtool) vs. the Geant4 binary — used only to auto-enable dry-run when the
# matching environment is absent, mirroring the legacy per-workflow behavior.
_ACE3P_TYPES = frozenset({'cubit', 'omega3p', 's3p', 't3p', 'acdtool'})
_GEANT4_TYPES = frozenset({'geant4'})

# How an evaluation's working directory is named. ``manual`` shares one directory
# across every point; ``auto`` suffixes the swept scalar values; ``indexed``
# suffixes the point's position in the sweep — see :meth:`Workflow.point_workdir`.
WORKDIR_MODES = ('manual', 'auto', 'indexed')

# The workdir name used when no ``workdir`` is configured at all.
DEFAULT_WORKDIR_BASE = 'lume-ace3p_workflow_output'


def _scalar_str(value):
    """Render a scalar for a workdir-name suffix (numpy scalars unwrap; a
    leading ``./`` is dropped) so auto-mode workdir names stay tidy."""
    if isinstance(value, np.generic):
        value = value.item()
    s = str(value)
    if s.startswith('./'):
        s = s[2:]
    return s


def _infer_output_module(spec):
    """Route a legacy-shaped (bare) output spec to a module *type*.

    The explicit form names the module (``{module: s3p, quantity: ...}``); this
    handles the terse bare forms used by the Omega3P/Geant4 examples, where the
    spec shape alone identifies the target module:

      * anything naming a ``.rfpost`` block — a mapping with a
        ``section: RoverQ`` key, or the deprecated positional
        ``['RoverQ', '0', 'RoQ']`` — -> ``acdtool``, decided by
        :func:`lume_ace3p.modules.acdtool_spec` so that routing and translation
        cannot drift apart,
      * a mapping (``{quantity: 'S(0,0)', at: {...}}``) or an S-parameter string
        -> ``s3p``,
      * anything naming a Geant4 scoring grid — ``{section: dose, quantity:
        total}`` or the positional ``['dose'|'edep'|'scoring', ...]`` ->
        ``geant4``,
      * ``'count'``/``'total_weight'`` -> ``particles``,
      * anything naming a T3P monitor — a mapping with a ``monitor: inputPower``
        key — -> ``t3p``,
      * a T3P wakefield quantity (``'loss_factor'``, ``'W'``, ...), or a mapping
        naming one / keyed ``at: {s: ...}`` -> ``t3p``.

    Note ``acdtool``'s ``kickFactor`` section and T3P's ``kick_factor`` quantity
    are distinct spellings on purpose, so the two never collide here.

    T3P's *monitor* quantities (``'P'``, ``'V'``, ``'Ez'``, ``'t'``) are
    deliberately **not** routable bare — they are short and generic, and ``'t'``
    especially would be a trap for any future spec. A ``monitor:`` key routes them
    instead, exactly as a ``section:`` key routes an acdtool or Geant4 spec:
    naming the thing is itself the routing signal, so ``module: t3p`` need not be
    repeated. Everything else about bare routing is untouched.
    """
    # Asked first, and without warning: this is the routing question ("is this
    # acdtool's?"), not the translation, so a deprecated list form must not warn
    # once here and again in AcdtoolModule.extract.
    if acdtool_spec(spec) is not None:
        return 'acdtool'
    if isinstance(spec, dict):
        # A 'section:' that is a Geant4 scoring grid routes there. Asked before
        # the S3P fallback for the same reason the acdtool question is asked
        # first: naming a section is itself the routing signal, so 'module:' need
        # not be repeated.
        if spec.get('section') in Geant4Module.SECTIONS:
            return 'geant4'
        # A 'monitor:' names a T3P monitor and nothing else in the package uses
        # the key, so it routes on its own.
        if spec.get('monitor') is not None:
            return 't3p'
        quantity = spec.get('quantity')
        at = spec.get('at') or {}
        if quantity in T3PModule.QUANTITIES or 's' in at:
            return 't3p'
        return 's3p'
    if isinstance(spec, str):
        if spec in ('count', 'total_weight'):
            return 'particles'
        return 't3p' if spec in T3PModule.QUANTITIES else 's3p'
    if isinstance(spec, (list, tuple)) and spec:
        head = spec[0]
        if head in Geant4Module.SECTIONS:
            return 'geant4'
        if head in ('count', 'total_weight'):
            return 'particles'
        return 't3p' if head in T3PModule.QUANTITIES else 's3p'
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
    one input point and returns ``({output_name: value}, ctx)``; it is
    deliberately free of any sweep/optimize loop (that is the Phase 3+ mode
    layer)."""

    def __init__(self, entries, workflow_params=None, inputs=None,
                 output_spec=None):
        self.workflow_params = dict(workflow_params) if workflow_params else {}
        self.inputs = inputs if inputs is not None else WorkflowInputs()
        self.output_spec = dict(output_spec) if output_spec else {}

        self.entries = list(entries)
        # Deprecated output specs already warned about, shared across every
        # module list this workflow builds: the dedup is per-*config*, not
        # per-*run*, so a sweep of N points warns once rather than N times. See
        # test_modules.py "...warns once per spec...".
        self._warned_specs = set()
        # The prototype list: built once, never run. It is what answers config
        # questions (module types, a module's declared params) and what makes a
        # bad command fail in the constructor rather than mid-sweep, since
        # AcdtoolModule.__init__ resolves its command. Every *evaluation* gets
        # its own list on the RunContext (see :meth:`evaluate`).
        self.modules = self._build_modules()
        self.module_types = {m.type for m in self.modules}

        self.workdir_mode = self.workflow_params.get('workdir_mode', 'manual')
        self.stage_mode = self.workflow_params.get('stage_mode', 'copy')
        if self.stage_mode not in STAGE_MODES:
            raise ValueError(
                "Key: 'stage_mode' must be one of "
                f"{sorted(STAGE_MODES)}; got {self.stage_mode!r}.")
        self.baseworkdir = self.workflow_params.get('workdir', os.getcwd())
        # Tee each module's subprocess output into <workdir>/<module name>.log.
        # On by default: a sweep point killed by the wall clock otherwise leaves
        # nothing behind but whatever is still on the terminal. Output is teed
        # rather than redirected, so the terminal keeps everything it had (see
        # lume_ace3p.logs).
        self.capture_output = bool(
            self.workflow_params.get('capture_output', True))
        self.paths = resolve_paths(self.workflow_params.get('paths'))
        self.dry_run = self._resolve_dry_run()
        # Single-run conveniences: the workdir and RunContext of the *most
        # recent* evaluate. They are not safe to read across overlapping
        # evaluations — a concurrent or interleaved caller must use the ``ctx``
        # that ``evaluate`` returned instead.
        self.workdir = None
        self.last_context = None

    # ---- construction ----------------------------------------------------

    def _build_modules(self):
        """Instantiate and topologically order a fresh module list from the
        declared entries.

        Called once at construction for the prototype list and once per
        :meth:`evaluate` for the live list, so per-run module state (a solver's
        parsed results, acdtool's parsed output) cannot leak between
        evaluations. Validation is deterministic, so re-resolving is a repeat of
        the same answer rather than a second chance to disagree."""
        modules = _resolve_order([_build_entry(e) for e in self.entries])
        for module in modules:
            if module.type == 'acdtool':
                module._warned = self._warned_specs
        return modules

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

    # ---- workdir naming (auto suffixes the swept scalars, indexed the
    #      point's position; see WORKDIR_MODES) ----------------------------

    def point_workdir(self, point_index):
        """The workdir for point ``point_index`` under ``workdir_mode: indexed``:
        ``<workdir>_0``, ``<workdir>_1``, ….

        This is a pure naming helper, and the *mode layer* is what calls it —
        ``evaluate`` takes no point index, so ``Workflow`` stays unaware of sweep
        ordering, the same decoupling it already has from the sweep loop itself.

        ``auto`` names by swept scalar value, which is usually unique but can
        collide (two axes rendering to the same string) and grows unboundedly long
        as axes are added. An index is stable and collision-free, which is what
        the resume machinery needs to identify a point on a later run."""
        base = (self.baseworkdir if self.baseworkdir is not None
                else DEFAULT_WORKDIR_BASE)
        return f'{base}_{int(point_index)}'

    def _getworkdir(self, inputs, sweep_scalars=None):
        if self.workdir_mode == 'manual':
            return self.baseworkdir
        if self.workdir_mode == 'indexed':
            # No point index reaches Workflow by design (see point_workdir): the
            # mode layer passes the full workdir= for each point, so getting here
            # means a caller drove evaluate() directly — one point, index 0.
            return self.point_workdir(0)
        if self.workdir_mode != 'auto':
            raise ValueError("Key: 'workdir_mode' must be one of "
                             f"{list(WORKDIR_MODES)}; got "
                             f"{self.workdir_mode!r}.")
        if sweep_scalars is None:
            parts = []
            for value in (*inputs.cubit.values(), *inputs.particles.values()):
                if isinstance(value, (list, tuple, np.ndarray)):
                    raise ValueError("Workflow cannot run with non-scalar "
                                     "inputs; drive it through a sweep mode.")
                parts.append(_scalar_str(value))
        else:
            parts = [_scalar_str(v) for v in sweep_scalars]
        suffix = ''.join('_' + p for p in parts)
        if self.baseworkdir is None:
            return DEFAULT_WORKDIR_BASE + suffix
        return self.baseworkdir + suffix

    # ---- the single seam the modes call ----------------------------------

    def evaluate(self, input_scalars=None, workdir=None):
        """Run the ordered module chain once for one input point.

        ``input_scalars`` selects the input point:
          * ``None`` — use the base ``inputs`` as-is (a single run; the base
            inputs must already be scalar-valued),
          * a list aligned with :meth:`sweep_axes` — materialize that grid point,
          * a mapping — treated as cubit-parameter overrides (the shape Xopt's
            objective function passes).

        ``workdir`` overrides the ``workdir_mode`` naming entirely, which is how
        a caller that already owns a per-point directory layout (the
        training-data collector) drives one point into a directory of its choosing
        without mutating the workflow.

        Returns ``(outputs, ctx)``: ``{output_name: extracted_value}`` for the
        ``output_parameters`` spec, and the populated :class:`RunContext` that
        produced it. The context is the per-evaluation carrier — pass it back to
        :meth:`field` / :meth:`field_index` to read *this* evaluation's results.
        It is also stashed on ``self.last_context`` as a single-run convenience,
        which is only correct while evaluations do not overlap.

        A completion manifest is written into the workdir and updated after every
        module (:mod:`lume_ace3p.state`). It is a record only — nothing here reads
        it — but it is written *incrementally* because the partial file is what a
        resumed or wall-clock-killed campaign has to work from."""
        inputs, sweep_scalars = self._materialize(input_scalars)
        self.workdir = (workdir if workdir is not None
                        else self._getworkdir(inputs, sweep_scalars))
        # A fresh module list per evaluation: module instances hold run state, so
        # sharing them across points is what would let row i report row j's
        # results once two evaluations overlap.
        ctx = RunContext(self.workdir, inputs=inputs, dry_run=self.dry_run,
                         paths=self.paths, stage_mode=self.stage_mode,
                         modules=self._build_modules(),
                         capture_output=self.capture_output)
        ctx.ensure_workdir()

        state = new_state(
            config_hash=config_hash(self.entries, inputs, self.output_spec),
            point=self._point_record(inputs, sweep_scalars),
            workdir=self.workdir)
        # Written before the first module runs, so a point killed inside module 0
        # still leaves its identity and config hash behind.
        write_state(self.workdir, state)

        for module in ctx.modules:
            try:
                module.run(ctx)
            except Exception as exc:
                # An exception is a failure of *this* module and the later ones
                # never started, which is exactly the state a resume needs. A
                # KeyboardInterrupt is deliberately not caught: an interrupted
                # module is not a failed one, and leaving it unrecorded already
                # means "not complete".
                record_module(state, module, FAILED,
                              error=f'{type(exc).__name__}: {exc}')
                write_state(self.workdir, state)
                raise
            record_module(state, module, COMPLETE,
                          **self._module_record(ctx, module))
            write_state(self.workdir, state)

        outputs = {}
        for name, spec in self.output_spec.items():
            # ctx.modules, never self.modules: a prototype's extract returns the
            # dry-run NaN sentinel rather than raising, so mis-resolving here
            # would yield silently wrong numbers.
            module, cleaned = self._route_output(name, spec, ctx.modules)
            outputs[name] = module.extract(ctx, cleaned)
        ctx.outputs = outputs
        record_outputs(state, outputs)
        write_state(self.workdir, state)
        self.last_context = ctx
        return outputs, ctx

    # ---- what an evaluation records about itself --------------------------- #

    def _point_record(self, inputs, sweep_scalars):
        """The manifest's ``point`` block — which input point this workdir holds.

        In a sweep that is the swept axes and their values for this point, taken
        from :meth:`sweep_axes` so an ACE3P leaf axis (``ace3p:FrequencyScan.Start``)
        is named the same way the sweep table names it. A single run or an
        optimizer point has no axes, so it records the materialized scalar knobs
        instead.

        No point *index* is recorded: ``evaluate`` takes none — the mode layer
        owns sweep ordering and passes the full ``workdir=`` (see
        :meth:`point_workdir`) — and the axis values identify the point anyway."""
        if sweep_scalars is not None:
            axes = {label: value for (label, _values, _setter), value
                    in zip(self.sweep_axes(), sweep_scalars)}
        else:
            axes = {**inputs.cubit, **inputs.particles, **inputs.macro}
        # numpy scalars and all: new_state renders the block JSON-writable.
        return {'axes': {str(name): value for name, value in axes.items()}}

    @staticmethod
    def _module_record(ctx, module):
        """What a completed module contributes to its manifest entry: the
        artifacts it produced (workdir-relative, so the manifest describes its own
        directory rather than the machine that wrote it) and the results-directory
        name a solver resolved — the value acdtool's positional commands are given,
        and the one a later reader needs to find the output again."""
        artifacts = {kind: relative(ctx.artifacts[kind], ctx.workdir)
                     for kind in sorted(module.provides)
                     if kind in ctx.artifacts}
        names = [ctx.job_names[kind] for kind in sorted(module.provides)
                 if kind in ctx.job_names]
        record = {'artifacts': artifacts}
        # Only the solver modules register one, and they provide a single
        # solution artifact each; the plural form exists so a future producer of
        # two cannot silently lose one.
        if len(names) == 1:
            record['job_name'] = names[0]
        elif names:
            record['job_names'] = {kind: ctx.job_names[kind]
                                   for kind in sorted(module.provides)
                                   if kind in ctx.job_names}
        return record

    def sweep_axes(self):
        """Delegate to the input model — the swept axes a mode iterates over."""
        return self.inputs.sweep_axes()

    def output_modules(self):
        """Return ``{output_name: module}`` — the module that extracts each
        declared output. Lets a mode ask a module for its field index
        (:meth:`Module.field_index`) without solver-specific code.

        These are the never-run *prototypes*, so only their configuration is
        meaningful (callers use this to read ``m.type``). Anything needing run
        state must go through the ``ctx`` an :meth:`evaluate` returned."""
        return {name: self._route_output(name, spec, self.modules)[0]
                for name, spec in self.output_spec.items()}

    def field_index(self, ctx=None):
        """Return ``(label, values)`` for the shared field index (e.g. S3P's
        ``('Frequency', array)``) of the evaluation ``ctx`` describes, or ``None``
        if no module exposes one. Scans all of that evaluation's modules — the
        field index is a property of the solver in the chain, independent of which
        ``output_parameters`` were requested (so an S3P sweep with no declared
        outputs still goes long-format).

        ``ctx`` defaults to ``self.last_context``, the most recent evaluation."""
        ctx = self.last_context if ctx is None else ctx
        if ctx is None:
            return None
        for module in ctx.modules:
            idx = module.field_index(ctx)
            if idx is not None:
                return idx
        return None

    def field(self, ctx=None):
        """Return the structured *field* output of the evaluation ``ctx``
        describes (:meth:`Module.field`), or ``None`` if no module in the chain
        produces one. Scans that evaluation's modules like :meth:`field_index`,
        and defaults to ``self.last_context`` the same way. The mode layer
        persists this per row as a field artifact (see
        :mod:`lume_ace3p.results`) — the hybrid model's structured half — instead
        of flattening it into the scalar table."""
        ctx = self.last_context if ctx is None else ctx
        if ctx is None:
            return None
        for module in ctx.modules:
            fld = module.field(ctx)
            if fld is not None:
                return fld
        return None

    # ---- helpers ---------------------------------------------------------

    def _materialize(self, input_scalars):
        if input_scalars is None:
            return self.inputs, None
        if isinstance(input_scalars, dict):
            # Xopt hands us a flat {variable: value} dict; route each override to
            # the bucket where it was declared (cubit / ace3p / macro) so an
            # optimization can drive parameters across multiple codes at once.
            return self.inputs.apply_overrides(input_scalars), None
        # list/tuple of axis scalars aligned with sweep_axes()
        scalars = list(input_scalars)
        return self.inputs.materialize(scalars), scalars

    def _route_output(self, name, spec, modules):
        """Return the ``(module, cleaned_spec)`` that extracts ``spec``, selected
        from ``modules``.

        An explicit ``module`` key in a mapping spec wins; otherwise the spec
        shape is used to infer the target module type (legacy bare specs).

        ``modules`` is passed rather than defaulted precisely because the two
        callers want different lists: :meth:`evaluate` must resolve against
        ``ctx.modules`` (the live instances holding this run's results), while
        :meth:`output_modules` asks a config-only question and the prototypes are
        the right answer there."""
        if isinstance(spec, dict) and 'module' in spec:
            module_type = str(spec['module']).lower()
            cleaned = {k: v for k, v in spec.items() if k != 'module'}
        else:
            module_type = _infer_output_module(spec)
            cleaned = spec
        candidates = [m for m in modules if m.type == module_type]
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
