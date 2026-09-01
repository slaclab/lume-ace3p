"""Mode layer for the workflow-modularization refactor (Phases 3-4).

A *mode* is how a validated :class:`~lume_ace3p.workflow_graph.Workflow` is
*driven*:

* ``single`` runs it once,
* ``parameter_sweep`` runs it over a tensor product of the swept input axes,
* ``scalar_optimize`` drives an Xopt optimization loop (Phase 4),
* ``gp_parameter_sweep`` drives an Xopt Bayesian-exploration loop and emits a
  GP-posterior-mean sweep (Phase 4).

The two table modes are also **resumable** (``mode: {resume: true}``): each point
is driven through the completion manifest in its own workdir, so a point that
already finished re-runs only its parsers, a half-finished one restarts at its
first non-complete module, and the table comes out the same either way. That is
what makes an allocation lost to the wall clock recoverable. :func:`status` is the
read-only half — it reports what those manifests say without running anything, and
is what ``run-lume-ace3p --status`` prints.

Modes are deliberately **workflow-agnostic** — they touch the workflow only
through its public seams (:meth:`Workflow.evaluate`, :meth:`Workflow.sweep_axes`,
:meth:`Workflow.field_index`) and never reach into any solver-specific code. In
particular the two Xopt modes below pull their objective scalar(s) from
``workflow.evaluate(input_dict)`` + the declarative ``output_parameters`` spec,
so no S-parameter/frequency parsing lives in the driver. This makes *any*
workflow (S3P, Geant4, a multi-step chain) optimizable/sweepable — this is the
generic-Xopt driver that absorbs the shelved Geant4 surrogate-project Phase 1.

Result container for the sweep/single modes = a pandas ``DataFrame`` (the hybrid
data model from the plan): one row per evaluation, columns = swept input
variable names + the extracted scalar outputs. Two shapes:

* **wide / scalar** (Omega3P, Geant4, ...) — one row per grid point; each
  ``output_parameters`` entry is a scalar column.
* **long / tidy** (S3P) — a module that exposes a shared field index
  (:meth:`Module.field_index`, e.g. ``('Frequency', array)``) emits one row per
  ``(grid-point, frequency)``; each S-parameter output becomes a column aligned
  to that index.

Per-run *field* outputs (S-parameter vectors, dose/edep voxel grids) are NOT
exploded into the scalar table — they stay structured. For the wide/scalar
table a module's structured field (:meth:`Workflow.field`) is persisted per row
via :func:`lume_ace3p.results.save_field` and referenced by a
:data:`~lume_ace3p.results.FIELD_ARTIFACT_COLUMN` handle; the arrays reload on
demand with :func:`lume_ace3p.results.load_field`. The S3P long-format case
above is the one tidy-frame exception (its field values *are* the rows), so it
carries no field-artifact column.

The Xopt modes log ``X.data`` (already a DataFrame) via the same shared writer
(clean break — numeric equivalence only, not file format).

Every result-producing mode routes its table through the single shared
:func:`lume_ace3p.results.write_table` (a tab-delimited ``to_csv``). The old
dict ``sweep_data`` tuple-keyed structure and the hand-rolled ``tools.py``
writers have been removed; :mod:`lume_ace3p.results` is the one and only writer.
"""

import itertools
import os
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd

from lume_ace3p.results import (
    write_table, save_field, FIELD_ARTIFACT_COLUMN,
)
from lume_ace3p import surrogate_data
from lume_ace3p.config import warn_unrecognized
from lume_ace3p.state import point_status, read_state
from lume_ace3p.xopt_state import (
    best_point, evaluation_count, read_xopt_state, restore_xopt, set_aside,
    write_xopt_state, xopt_state_path,
)


# Modes that consume an on-disk store / saved model rather than driving the
# module chain. They never call ``workflow.evaluate``, so a config for one of
# them does not need a ``workflow:`` block at all — the store already holds the
# collected data. Every other mode requires a workflow to drive.
STORE_CONSUMING_MODES = frozenset({'train_surrogate', 'invert_optimize',
                                   'invert_bayesian'})


# ``type``/``mode`` (the legacy alias) name the mode itself, so every block has them.
_COMMON_MODE_KEYS = frozenset({'type', 'mode'})

# Every key each mode reads out of its own ``mode:`` block, for the unrecognized-key
# warning (:func:`lume_ace3p.config.warn_unrecognized`). **Per mode, not a union**,
# which is the point: a ``resume:`` in a ``train_surrogate`` block does nothing, and
# only a per-mode set can say so. Kept beside the dispatcher that consumes the block
# — extend it when a mode learns a key, or the mode's own new key will warn.
MODE_KEYS = {
    'single': frozenset({'output_file', 'sweep_output_file', 'resume'}),
    'parameter_sweep': frozenset({'output_file', 'sweep_output_file', 'resume'}),
    'scalar_optimize': frozenset({'output_file', 'resume'}),
    'gp_parameter_sweep': frozenset({'output_file', 'sweep_output_file', 'resume'}),
    'collect_training_data': frozenset({
        'store', 'output_dir', 'num_samples', 'sampler', 'seed', 'fidelity',
        'variables', 'resume'}),
    'train_surrogate': frozenset({
        'store', 'output_dir', 'variance', 'num_components', 'seed', 'model_dir',
        'holdout', 'dose_transform', 'floor', 'n_jobs'}),
    'invert_optimize': frozenset({
        'store', 'model_dir', 'target', 'bounds', 'num_starts', 'seed',
        'output_file', 'identifiability', 'identifiability_file'}),
    'invert_bayesian': frozenset({
        'store', 'model_dir', 'target', 'bounds', 'seed', 'output_file',
        'summary_file', 'identifiability', 'num_warmup', 'num_samples',
        'num_chains', 'dose_sigma'}),
}

# Every key the two Xopt modes read out of ``xopt_parameters``. One set for both:
# they share ``_mc_noise_guards``, ``_build_generator`` and the objective, and a
# config that moves between them should not have to be rewritten.
XOPT_KEYS = frozenset({
    'generator', 'generator_options', 'num_random', 'num_step', 'max_iterations',
    'max_steps', 'tolerance', 'cost_budget', 'alotted_time', 'cost_function',
    'fidelity_variable', 'save_model', 'mc_noisy_objective', 'bin_edges',
    'improvement_threshold', 'patience'})

# Every key ``_make_vocs`` and the GP sweep read out of ``vocs_parameters``.
VOCS_KEYS = frozenset({'variables', 'objectives', 'constraints', 'observables',
                       'constants'})


def mode_type_of(mode_cfg):
    """The normalized ``type`` of a mode config (honoring the legacy ``mode``
    alias), or ``''`` when absent. Shared by the CLI so the workflow-optional
    decision is made from one place."""
    if not mode_cfg:
        return ''
    return str(mode_cfg.get('type') or mode_cfg.get('mode') or '').lower()


def is_store_consuming(mode_cfg):
    """Whether ``mode_cfg`` selects a mode that needs no ``workflow:`` block."""
    return mode_type_of(mode_cfg) in STORE_CONSUMING_MODES


def _deprecation_warning(message):
    """Emit a clearly-labeled deprecation notice to stderr.

    A plain :class:`DeprecationWarning` via :mod:`warnings` is suppressed by
    default in a CLI, so this prints directly to guarantee the user sees it.
    Deprecated aliases still function today but will be removed in a future
    release; this points the user at the current spelling."""
    print(f"DeprecationWarning: {message}", file=sys.stderr)


def run_mode(mode_cfg, workflow, output_spec=None, vocs=None, xopt=None,
             sweep=None):
    """Dispatch on ``mode_cfg`` type and drive ``workflow``.

    ``mode_cfg`` is the mode configuration mapping. Its type is read from a
    ``type`` key (target schema) or a legacy ``mode`` key.

    For the table modes (``single`` / ``parameter_sweep``) an output-table path
    may be given as ``output_file`` (target schema) or ``sweep_output_file``
    (legacy); when present, the result DataFrame is written there via
    :func:`write_table`. These return the result :class:`pandas.DataFrame`.

    For the Xopt modes (``scalar_optimize`` / ``gp_parameter_sweep``) the VOCS,
    Xopt and (for the GP sweep) sweep configuration blocks are passed through
    ``vocs`` / ``xopt`` / ``sweep``. These return the :class:`xopt.Xopt` object.

    ``resume: true`` means "continue rather than start over" in every mode that
    takes it, but by two different mechanisms, because the two kinds of campaign
    have different notions of "already done". A table mode picks each point up from
    its completion manifest (:func:`parameter_sweep`) and produces an identical
    table. An Xopt mode's points are chosen by the generator as it goes, so there is
    no fixed set of them; it restores the optimizer's whole state instead
    (:mod:`lume_ace3p.xopt_state`) and promises only that no evaluation is repeated
    and the search continues from the same data.

    ``output_spec`` is accepted for API symmetry but is informational only — the
    workflow already carries its ``output_parameters`` (``workflow.output_spec``)
    and does the extraction inside :meth:`Workflow.evaluate`."""
    if mode_cfg.get('type') is None and mode_cfg.get('mode') is not None:
        _deprecation_warning(
            "the 'mode:' key inside the mode block is a legacy alias for 'type:'. "
            "Rename it to 'type:' — the 'mode' alias will be removed in a future "
            "release.")
    mode_type = str(mode_cfg.get('type') or mode_cfg.get('mode')).lower()
    if mode_type in MODE_KEYS:
        warn_unrecognized(f"mode '{mode_type}'", mode_cfg,
                          MODE_KEYS[mode_type] | _COMMON_MODE_KEYS)
    resume = bool(mode_cfg.get('resume', False))
    if mode_type == 'single':
        df = single(workflow, resume=resume)
    elif mode_type == 'parameter_sweep':
        df = parameter_sweep(workflow, resume=resume)
    elif mode_type == 'collect_training_data':
        return collect_training_data(mode_cfg, workflow)
    elif mode_type == 'train_surrogate':
        return train_surrogate(mode_cfg, workflow)
    elif mode_type == 'invert_optimize':
        return invert_optimize(mode_cfg, workflow)
    elif mode_type == 'invert_bayesian':
        return invert_bayesian(mode_cfg, workflow)
    elif mode_type == 'scalar_optimize':
        return scalar_optimize(
            workflow, vocs, xopt,
            log_file=mode_cfg.get('output_file') or 'sim_output.txt',
            resume=resume)
    elif mode_type == 'gp_parameter_sweep':
        return gp_parameter_sweep(
            workflow, sweep, vocs, xopt,
            log_file=mode_cfg.get('output_file') or 'sim_output.txt',
            sweep_file=mode_cfg.get('sweep_output_file') or 'sweep_output.txt',
            resume=resume)
    else:
        raise ValueError(
            f"mode '{mode_type}' is not handled by the mode layer "
            "(single | parameter_sweep | collect_training_data | "
            "train_surrogate | invert_optimize | invert_bayesian | "
            "scalar_optimize | gp_parameter_sweep).")

    # In the table modes 'sweep_output_file:' is a legacy alias for 'output_file:'
    # (only the Xopt gp_parameter_sweep mode above, which returned early, uses it
    # as a distinct key for the GP posterior-mean grid).
    if (mode_cfg.get('output_file') is None
            and mode_cfg.get('sweep_output_file') is not None):
        _deprecation_warning(
            "'sweep_output_file:' is a legacy alias for 'output_file:' in the "
            f"'{mode_type}' mode. Rename it to 'output_file:' — the "
            "'sweep_output_file' alias will be removed in a future release.")
    output_file = mode_cfg.get('output_file') or mode_cfg.get('sweep_output_file')
    if output_file:
        write_table(df, output_file)
    return df


def single(workflow, resume=False):
    """Run the workflow once and return a one-row (or, for a field-indexed
    solver, one-row-per-index) result DataFrame.

    The base ``inputs`` must already be scalar-valued (no swept axes). Input
    columns are the scalar cubit + particles knobs; output columns are the
    extracted ``output_parameters``. When the workflow produces a structured
    field (Geant4 voxel grids, an S3P spectrum in the wide case), it is
    persisted and referenced by a field-artifact column.

    ``resume`` picks the run up from its completion manifest — see
    :func:`parameter_sweep`, which resumes the same way per point."""
    _require_resumable(workflow, resume)
    scalar_inputs = {**workflow.inputs.cubit, **workflow.inputs.particles}
    input_names = list(scalar_inputs.keys())
    scalars = [scalar_inputs[name] for name in input_names]
    outputs, ctx = _evaluate_point(workflow, None, 0, resume=resume)
    point = _PointResult(0, scalars, outputs, workflow.field_index(ctx),
                         _persist_field(workflow, ctx, 0))
    return _assemble(workflow, input_names, [point])


def parameter_sweep(workflow, resume=False):
    """Run the workflow over the tensor product of its swept axes, one row per
    grid point (or per ``(grid-point, field-index)`` for a field-indexed
    solver). Returns the result DataFrame.

    In the wide/scalar case a per-row field-artifact handle is stored (see
    :func:`_persist_field`) when a module produces a structured field; the
    long-format (S3P) case carries no field-artifact column — its field values
    already *are* the rows.

    **Execution is separated from row assembly.** Each point's results are
    collected into a list keyed by point index, and the frame is built from that
    list in index order once the loop is done (:func:`_assemble`). Today the loop
    is serial and in order, so this changes nothing; it is what makes the frame
    identical when points run out of order — a resumed campaign or a concurrent
    one — instead of silently making every baseline dependent on completion order.

    **``resume``** (``mode: {resume: true}``) drives each point through its
    completion manifest: a point already finished re-runs only its parsers and
    rebuilds its row, a half-finished one restarts at its first non-complete
    module, and an unstarted one runs normally. The table is the same table either
    way — which is the point, and what makes an allocation lost to the wall clock
    recoverable instead of thrown away. It is off by default (a sweep that silently
    adopted a stale workdir would be worse than no resume) and refused under
    ``workdir_mode: manual``, where every point shares one directory and so cannot
    carry per-point state."""
    _require_resumable(workflow, resume)
    axes = workflow.sweep_axes()
    input_names = [label for label, _values, _setter in axes]
    tensor = _input_tensor(axes)
    _require_own_workdirs(workflow, tensor.shape[0])

    points = []
    for i in range(tensor.shape[0]):
        scalars = tensor[i].tolist()
        outputs, ctx = _evaluate_point(workflow, scalars if axes else None, i,
                                       resume=resume)
        # Everything the rows need is read out here, so the per-point ``ctx`` —
        # and the whole parsed solver output hanging off it — is not held for the
        # length of the sweep.
        points.append(_PointResult(i, scalars, outputs,
                                   workflow.field_index(ctx),
                                   _persist_field(workflow, ctx, i)))
    return _assemble(workflow, input_names, points)


def _require_resumable(workflow, resume):
    """Refuse ``resume: true`` under ``workdir_mode: manual``, naming the fix.

    Resume is per point, and under ``manual`` every point runs in the *same*
    directory — so there is one manifest for the whole sweep, describing whichever
    point ran last. Skipping work on the strength of it would mean skipping point 5
    because point 4 finished. ``indexed`` gives each point its own directory and a
    stable identity across runs, which is what this needs (design decision 5)."""
    if resume and getattr(workflow, 'workdir_mode', None) == 'manual':
        raise ValueError(
            "resume is not available under workdir_mode: manual — every point "
            "shares one workdir there, so its single run manifest describes "
            "whichever point ran last rather than the point being resumed. Set "
            "workflow_parameters: {workdir_mode: indexed} (per-point directories "
            "'<workdir>_0', '<workdir>_1', … and a stable point identity across "
            "runs); 'auto' also gives per-point directories, but its names are "
            "not guaranteed unique.")


def _evaluate_point(workflow, input_scalars, point_index, resume=False):
    """Evaluate one sweep point, choosing its workdir.

    ``workdir_mode: indexed`` is implemented **here** rather than in
    ``Workflow``: the point index is the mode layer's own bookkeeping, so the
    mode resolves the name and passes the full ``workdir=``, and ``Workflow``
    stays unaware of sweep ordering. ``manual`` / ``auto`` pass no workdir at all
    and are named by the workflow exactly as before — including for a test double
    whose ``evaluate`` takes no ``workdir`` keyword.

    ``auto`` therefore still names a *sweep* point by its swept scalar values,
    which are the point's identity. On the override path (the Xopt modes) there are
    no axis scalars to name it by, so ``auto`` means the iteration index instead —
    see :func:`_iteration_workdir`."""
    workdir = (workflow.point_workdir(point_index)
               if getattr(workflow, 'workdir_mode', None) == 'indexed' else None)
    return _evaluate(workflow, input_scalars, workdir=workdir, resume=resume)


def _iteration_workdir(workflow, iteration):
    """The workdir for evaluation ``iteration`` on the **override** path, or
    ``None`` to leave the naming to the workflow.

    The override path is the one the Xopt modes drive: ``evaluate`` is handed an
    input *dict* rather than axis scalars, so there is no sweep grid and no point
    index of the kind :func:`_evaluate_point` uses — the identity of an evaluation
    is simply which one it is. Under ``auto`` or ``indexed`` that iteration number
    names its directory (:meth:`Workflow.point_workdir`); under ``manual`` every
    evaluation shares one directory, which is what
    :func:`_require_own_workdirs` warns about.

    ``auto`` is included deliberately rather than left to
    :meth:`Workflow._getworkdir`. Its value-based naming is unusable for an
    optimizer — ``lume-ace3p_workdir_14.724999999999998_1.5750000000000002`` — and
    on this path it was also *wrong*: it reads only the ``cubit`` and ``particles``
    buckets, so an optimization over an ACE3P or Geant4 knob got one unchanging
    name and every evaluation overwrote the previous one's files. An index cannot
    collide whatever bucket the variable lives in.

    The ``getattr`` is load-bearing: several test doubles expose ``evaluate`` with
    no ``workdir`` keyword and carry no ``workdir_mode``, and this keeps them
    valid (``_evaluate`` omits an argument that is ``None``)."""
    if getattr(workflow, 'workdir_mode', None) in ('auto', 'indexed'):
        return workflow.point_workdir(iteration)
    return None


def _require_own_workdirs(workflow, evaluations):
    """Warn once when a multi-evaluation run is about to do every evaluation in
    one shared directory.

    ``workdir_mode: manual`` is the default and stays the default: flipping it
    would silently relocate the output of every config that omits the key. It is
    also legal and occasionally deliberate — a single run, or a sweep whose points
    genuinely may overwrite each other. What it must not be is *silent*, because
    with N evaluations in one directory each one overwrites the previous one's
    mesh, input files, solver results, logs and run manifest, and the surviving
    files describe whichever evaluation happened to finish last."""
    if not evaluations or evaluations <= 1:
        return
    if getattr(workflow, 'workdir_mode', None) != 'manual':
        return
    print(f"Warning: workdir_mode is 'manual' and this run performs "
          f"{evaluations} evaluations, so all of them share the directory "
          f"'{workflow.baseworkdir}' — each one overwrites the previous one's "
          "mesh, input files, solver results, logs and run manifest, and only "
          "the last survives. Set workflow_parameters: {workdir_mode: auto} "
          "(or 'indexed') to give each evaluation its own directory.")


def _evaluate(workflow, input_scalars, workdir=None, resume=False):
    """Call ``workflow.evaluate``, passing only the keywords that carry something.

    An omitted ``workdir=`` means "name it yourself" and an omitted ``resume=``
    means "as always", so the default call is the one-argument form this seam has
    had since Phase 1 — which is what keeps the several test doubles whose
    ``evaluate`` predates one of these keywords working, and what keeps the
    non-resume path byte-for-byte the call it was."""
    kwargs = {}
    if workdir is not None:
        kwargs['workdir'] = workdir
    if resume:
        kwargs['resume'] = True
    return workflow.evaluate(input_scalars, **kwargs)


def _persist_field(workflow, ctx, point_index):
    """Persist the structured field (if any) of the evaluation ``ctx`` describes
    to a ``.npz`` under its workdir, and return the stored handle.

    Returns ``None`` when there is no field (dry-run, or a solver that produces
    none) or in the long-format case — where the field values are exploded into
    the rows via :meth:`Workflow.field_index`, so storing a redundant artifact
    would be wrong. The per-point filename keeps rows distinct even in a shared
    (manual) workdir."""
    if workflow.field_index(ctx) is not None:
        return None
    field = workflow.field(ctx)
    if field is None:
        return None
    # ctx.workdir, not workflow.workdir: the directory this evaluation actually
    # wrote to, which is the same distinction the ctx itself exists to make.
    path = os.path.join(ctx.workdir or '.', f'field_{point_index}.npz')
    return save_field(field, path)


# --------------------------------------------------------------------------- #
# status — what a half-finished campaign looks like, without running anything.
# Reads only the per-point manifests the runs already wrote, so it is safe to
# poll while a sweep is in progress (and is the seam a driver would poll).
# --------------------------------------------------------------------------- #


# The status table's columns after the point index and its swept inputs: the
# verdict, how much of the chain is recorded, what a resume would run next, and
# where to look.
_STATUS_COLUMNS = ('status', 'modules', 'next', 'workdir')


def status(workflow):
    """Report what the completion manifests say about the points ``workflow``
    implies, and return the report as a DataFrame.

    One row per point of the sweep grid (one row for a workflow with no swept
    axes), each carrying the point's verdict from
    :func:`lume_ace3p.state.point_status` — ``complete`` / ``failed`` /
    ``partial`` / ``absent`` / ``stale`` — how many of its modules are recorded
    complete, and the first module a resume would run.

    Nothing is executed and no manifest is written: this is the inspection half of
    the resume feature, and it is what makes a campaign that half-finished
    overnight legible. ``stale`` is the verdict worth reading closely — it means a
    manifest is there but was written for a different resolved configuration, so
    resume will re-run that point from the start."""
    axes = workflow.sweep_axes()
    labels = [label for label, _values, _setter in axes]
    tensor = _input_tensor(axes)
    names = [module.name for module in workflow.modules]

    rows = []
    for i in range(tensor.shape[0]):
        scalars = tensor[i].tolist()
        point = scalars if axes else None
        workdir = workflow.resolved_workdir(point, i)
        verdict, complete, pending = point_status(
            read_state(workdir), workflow.point_config_hash(point), names)
        row = dict(zip(labels, scalars))
        row.update(point=i, status=verdict, modules=f'{complete}/{len(names)}',
                   next=pending or '', workdir=workdir)
        rows.append(row)

    columns = ['point', *labels, *_STATUS_COLUMNS]
    df = pd.DataFrame(rows, columns=columns)
    _print_status(df)
    return df


def _print_status(df):
    """Print the status table under a one-line summary of the counts.

    The summary is what a user actually reads — "3 of 8 done, one broke" — so it
    comes first and names the counts in a fixed order rather than in whatever
    order the points happen to be in."""
    counts = df['status'].value_counts()
    summary = ', '.join(f'{int(counts[key])} {key}'
                        for key in ('complete', 'partial', 'failed', 'stale',
                                    'absent')
                        if key in counts)
    print(f" - {len(df)} point(s) implied by this configuration: {summary}")
    print(df.to_string(index=False))


def xopt_status(mode_cfg):
    """Report what an Xopt campaign's resume state records, and return it.

    The Xopt half of ``--status``. There is no per-point table to print here — the
    generator chose the points as it went, so "point 5 of 8" does not exist — and
    what a user actually wants to know about a half-finished optimization is how
    many evaluations are in the bank and how good the best one is. Both come from
    the state file the run has been writing all along
    (:mod:`lume_ace3p.xopt_state`); nothing is executed and nothing is written.

    Returns the state mapping, or ``None`` when there is no usable state — an
    optimization that has not started yet, or one whose state file was lost."""
    mode_type = mode_type_of(mode_cfg)
    state_file = xopt_state_path(mode_cfg.get('output_file') or 'sim_output.txt')
    state = read_xopt_state(state_file)
    if state is None:
        print(f" - {mode_type}: no resume state at '{state_file}' — this "
              "optimization has not recorded any evaluation yet.")
        return None

    count = evaluation_count(state)
    print(f" - {mode_type}: {count} evaluation(s) recorded in '{state_file}'")
    best = best_point(state)
    if best is None:
        print(" - no finite objective value recorded yet.")
    else:
        name, value, variables = best
        at = ', '.join(f'{key}={_number(item)}' for key, item in variables.items())
        print(f" - best '{name}' = {_number(value)}" + (f' at {at}' if at else ''))
    print(" - a resumed run continues from this data and repeats no evaluation; it "
          "does not reproduce the trajectory an uninterrupted run would have taken.")
    return state


def _number(value):
    """A short, readable rendering of a reported number (``--status`` only)."""
    try:
        return f'{float(value):.6g}'
    except (TypeError, ValueError):
        return str(value)


# --------------------------------------------------------------------------- #
# collect_training_data (Phase 2) — DOE sampler over β driving the Geant4
# workflow, persisting (β, dose_grid) pairs into a resumable training store.
# Workflow-agnostic in the same spirit as the sweep modes: it drives the chain
# only through ``workflow.evaluate`` / ``workflow.field`` and reuses the shared
# field-persistence machinery instead of a bespoke store.
# --------------------------------------------------------------------------- #


def _require_fixed_bin_edges(workflow):
    """Validate correctness constraint #1 on the resolved ``particles`` module.

    The β→dose binning is governed by ``bin_edges`` on the *particles* module
    entry (``particles.py`` reads it there), NOT by any mode-dict key — so this
    inspects the built workflow's particles module directly and hard-fails if
    ``bin_edges`` is absent or not length ``num_bins + 1``. This is deliberately
    stronger than :func:`_mc_noise_guards` (which only checks a mode-dict key and
    does not plumb into the particles module); do not substitute one for the
    other. Returns ``(beta_names, num_bins)`` — the per-bin β variable order the
    DOE must sample, taken from the module's ``beta_inputs``."""
    particles = [m for m in workflow.modules if m.type == 'particles']
    if not particles:
        raise ValueError(
            "collect_training_data requires a 'particles' module in the "
            "workflow (the β→dose weighting step).")
    if len(particles) > 1:
        raise ValueError(
            "collect_training_data expects exactly one 'particles' module; "
            f"found {len(particles)}.")
    params = particles[0].params
    num_bins = params.get('num_bins')
    if num_bins is None:
        raise ValueError(
            "the 'particles' module must set 'num_bins' for training-data "
            "collection.")
    bin_edges = params.get('bin_edges')
    if bin_edges is None:
        raise ValueError(
            "correctness constraint #1: the 'particles' module must fix "
            "'bin_edges' explicitly for training-data collection. The default "
            "data-driven edges drift per run and poison the surrogate. Provide "
            f"an explicit 'bin_edges' of length num_bins + 1 ({num_bins + 1}).")
    if len(bin_edges) != num_bins + 1:
        raise ValueError(
            f"'bin_edges' has length {len(bin_edges)} but must be num_bins + 1 "
            f"({num_bins + 1}).")

    beta_inputs = params.get('beta_inputs')
    if not beta_inputs:
        raise ValueError(
            "collect_training_data needs the 'particles' module to declare "
            "'beta_inputs: [beta0, ...]' (one input-space variable per bin) so "
            "the DOE has a per-bin β to sample. A scalar 'beta_input' broadcast "
            "collapses the 8-D design to 1-D and is not a valid training design.")
    if len(beta_inputs) != num_bins:
        raise ValueError(
            f"len(beta_inputs)={len(beta_inputs)} must equal num_bins={num_bins}.")
    return list(beta_inputs), int(num_bins)


def _geant4_input_path(workflow):
    """The geant4 module's input file path, or ``None`` if the workflow has no
    geant4 module (e.g. a synthetic test double)."""
    geant4 = [m for m in workflow.modules if m.type == 'geant4']
    if not geant4:
        return None
    return getattr(geant4[0], 'geant4_input', None)


def _require_fixed_mesh(workflow):
    """Validate correctness constraint #3 on the resolved ``geant4`` module.

    The dose scoring mesh (per-axis bin counts + physical extent) must be pinned
    for the whole campaign: PCA/POD stacks every run's dose grid into one
    ``(N, M)`` matrix, which is only meaningful if column *j* is the same
    physical voxel for every run. A drifting mesh misaligns the POD basis exactly
    the way drifting ``bin_edges`` misaligns the input map. The mesh lives in the
    geant4 input file (``mesh_nx/ny/nz``, ``mesh_cx/cy/cz``, ``mesh_x/y/z``), so
    this is a cheap parse that works under dry-run (no dose grid needed).

    Returns the mesh fingerprint dict (``{'bins', 'center', 'half'}``) when a
    geant4 module is present, hard-failing if its mesh geometry cannot be read.
    Returns ``None`` when the workflow has no geant4 module (a synthetic test
    double that emits dose grids directly) — the load-side index/fingerprint
    checks remain the backstop in that case."""
    geant4 = [m for m in workflow.modules if m.type == 'geant4']
    if not geant4:
        return None
    if len(geant4) > 1:
        raise ValueError(
            "collect_training_data expects at most one 'geant4' module; "
            f"found {len(geant4)}.")
    input_path = getattr(geant4[0], 'geant4_input', None)
    if not input_path:
        raise ValueError(
            "correctness constraint #3: the 'geant4' module must declare "
            "'geant4_input' so the dose scoring mesh can be pinned for "
            "training-data collection.")
    fingerprint = surrogate_data.read_mesh_fingerprint(input_path)
    if fingerprint is None:
        raise ValueError(
            "correctness constraint #3: could not read a dose scoring-mesh "
            f"fingerprint from the geant4 input file '{input_path}'. It must "
            "define mesh_nx/ny/nz (bin counts), mesh_cx/cy/cz (center, mm) and "
            "mesh_x/y/z (half-sizes, mm) so the β→dose voxel grid is identical "
            "across the whole campaign — a drifting mesh misaligns the PCA "
            "basis. Fix the mesh keys in the input file.")
    return fingerprint


def _doe_bounds(mode_cfg, beta_names):
    """Resolve the per-bin β bounds for the DOE from the mode config.

    ``mode_cfg['variables']`` maps each β variable name to ``[lo, hi]`` (or
    ``{min, max}``). Every name in ``beta_names`` must have a bound; extra keys
    are an error so a typo does not silently drop a dimension."""
    variables = mode_cfg.get('variables')
    if not variables:
        raise ValueError(
            "collect_training_data requires a 'variables' block in the mode "
            "config giving [lo, hi] bounds for each β variable "
            f"({beta_names}).")
    bounds = []
    for name in beta_names:
        if name not in variables:
            raise ValueError(
                f"no DOE bound for β variable '{name}' in mode 'variables'.")
        spec = variables[name]
        if isinstance(spec, dict):
            lo, hi = spec['min'], spec['max']
        else:
            lo, hi = spec[0], spec[1]
        bounds.append((float(lo), float(hi)))
    extra = [k for k in variables if k not in beta_names]
    if extra:
        raise ValueError(
            f"mode 'variables' has entries {extra} that are not β variables "
            f"({beta_names}); check for a typo.")
    return bounds


def collect_training_data(mode_cfg, workflow):
    """Generate and persist ``(β, dose_grid)`` training pairs (Phase 2).

    Samples ``num_samples`` scattered points in the D-dimensional β space via a
    Latin-Hypercube / Sobol DOE (:func:`surrogate_data.sample_beta_doe`), and
    for each point drives the declarative ``track3p_source → particles →
    geant4`` chain once through :meth:`Workflow.evaluate` (β passed as an
    override dict, one value per ``beta_inputs`` bin). The full dose/edep voxel
    grid is captured with :meth:`Workflow.field` and persisted per sample with
    :func:`results.save_field`; the β row + fidelity + field handle go into the
    shared result table. A ``manifest.json`` records the fixed ``bin_edges`` /
    ``num_bins``, β order, the dose scoring-mesh fingerprint (constraint #3), and
    DOE provenance. The mesh is validated up front and re-checked per sample so a
    mid-campaign mesh edit hard-fails rather than misaligning the PCA basis.

    **Resumable, in two layers.** Each sample runs in its own
    ``<store>/sample_NNNNN`` workdir (passed to :meth:`Workflow.evaluate` as an
    explicit ``workdir``, so the workflow's own ``workdir_mode`` is left
    untouched).

    * A sample whose ``field.npz`` is already there is skipped outright, exactly as
      before. That check predates the completion manifest and is kept as a
      **recognised legacy complete state**: it is what every training store
      collected so far records, and re-deriving those samples would invalidate
      stores that are perfectly good. A persisted dose grid *is* the sample — the
      store holds nothing else per sample — so unlike a file-presence check on a
      solver's output (which cannot distinguish an overwritten ``wakefield.out``,
      design decision 3) this one is exact.
    * ``resume: true`` additionally hands each *unfinished* sample to the shared
      mechanism, so a sample cut off midway through the chain restarts at its first
      non-complete module rather than at the mesh. Off by default, like everywhere
      else.

    Returns the training-store result :class:`pandas.DataFrame`."""
    beta_names, num_bins = _require_fixed_bin_edges(workflow)
    mesh_fingerprint = _require_fixed_mesh(workflow)
    bounds = _doe_bounds(mode_cfg, beta_names)
    resume = bool(mode_cfg.get('resume', False))

    store = mode_cfg.get('store') or mode_cfg.get('output_dir') or 'training_store'
    num_samples = int(mode_cfg.get('num_samples', 8))
    sampler = mode_cfg.get('sampler', 'sobol')
    seed = int(mode_cfg.get('seed', 0))
    fidelity = mode_cfg.get('fidelity')
    if not os.path.isdir(store):
        os.makedirs(store, exist_ok=True)

    design = surrogate_data.sample_beta_doe(bounds, num_samples,
                                            sampler=sampler, seed=seed)

    # Drive each sample into its own directory by passing the workdir straight to
    # evaluate. That used to be done by mutating workflow.workdir_mode /
    # baseworkdir and restoring them in a finally; the explicit argument means the
    # collector never writes to shared workflow state at all.
    rows = []
    mesh_shape = None
    for i in range(num_samples):
        beta_vec = design[i]
        sample_dir = os.path.join(store, f'sample_{i:05d}')
        field_path = os.path.join(sample_dir, 'field.npz')
        overrides = {name: float(v) for name, v in zip(beta_names, beta_vec)}

        if os.path.isfile(field_path):
            # Already done: the dose grid for this β is persisted, and the grid is
            # the whole of the sample. The recognised legacy state above — it
            # predates the run manifest and every existing store is in it.
            handle = field_path
        else:
            # Constraint #3: defend against a mid-campaign edit to the geant4
            # input file's scoring mesh. Re-read the fingerprint before each
            # fresh evaluation and hard-fail on drift, so a partway mesh change
            # is caught here rather than silently misaligning the PCA basis at
            # train time.
            if mesh_fingerprint is not None:
                current = surrogate_data.read_mesh_fingerprint(
                    _geant4_input_path(workflow))
                if not surrogate_data.mesh_fingerprints_match(
                        current, mesh_fingerprint):
                    raise ValueError(
                        f"dose scoring mesh changed at sample {i} "
                        f"(was {mesh_fingerprint}, now {current}); the mesh "
                        "must stay fixed for the whole campaign "
                        "(constraint #3).")
            _outputs, ctx = _evaluate(workflow, overrides, workdir=sample_dir,
                                      resume=resume)
            handle = save_field(workflow.field(ctx), field_path)

        if handle is not None and mesh_shape is None:
            mesh_shape = _mesh_shape(handle)

        row = dict(overrides)
        row[surrogate_data.FIDELITY_COLUMN] = (
            float(fidelity) if fidelity is not None else np.nan)
        if handle is not None:
            row[FIELD_ARTIFACT_COLUMN] = handle
        rows.append(row)

    columns = list(beta_names) + [surrogate_data.FIDELITY_COLUMN]
    if any(FIELD_ARTIFACT_COLUMN in r for r in rows):
        columns.append(FIELD_ARTIFACT_COLUMN)
    df = pd.DataFrame(rows, columns=columns)
    write_table(df, os.path.join(store, surrogate_data.TABLE_FILENAME))

    surrogate_data.write_manifest(store, {
        'beta_names': beta_names,
        'num_bins': num_bins,
        'bin_edges': list(_particles_params(workflow)['bin_edges']),
        'num_samples': num_samples,
        'sampler': sampler,
        'seed': seed,
        'bounds': [list(b) for b in bounds],
        'fidelity': fidelity,
        'mesh_shape': mesh_shape,
        'mesh': mesh_fingerprint,
        'dry_run': bool(workflow.dry_run),
    })
    return df


def _particles_params(workflow):
    return [m for m in workflow.modules if m.type == 'particles'][0].params


def _mesh_shape(handle):
    """Voxel count of a persisted dose/edep field (for the manifest), or
    ``None`` if the artifact has no grid."""
    from lume_ace3p.results import load_field
    field = load_field(handle)
    if not field:
        return None
    section = field.get('dose') or field.get('edep')
    if section is None:
        return None
    return [int(len(np.asarray(section['values'])))]


# --------------------------------------------------------------------------- #
# train_surrogate (Phase 3) — fit the PCA-GP forward dose surrogate from a
# collected training store. A store-consuming mode (like collect_training_data,
# it does not sweep the workflow); the ``workflow`` argument is accepted for
# dispatch symmetry but unused (the store already holds the (β, dose) pairs).
# --------------------------------------------------------------------------- #


def train_surrogate(mode_cfg, workflow=None):
    """Fit and persist the PCA-GP forward dose surrogate (Phase 3).

    Loads the Phase-2 training store named by ``mode_cfg['store']`` via
    :func:`surrogate_data.load_training_store` (which already enforces the fixed
    ``bin_edges`` / scoring-mesh invariants, constraints #1 and #3), fits a
    :class:`lume_ace3p.surrogate.DoseSurrogate` (SVD → top-k POD modes → one
    GP per coefficient, each with a genuine fitted noise term per constraint #2),
    optionally reports held-out reconstruction accuracy, and saves the model.

    Mode config keys:

    * ``store`` (required) — the training-store directory.
    * ``variance`` (default 0.99) — cumulative-energy target for choosing the
      number of retained POD modes; ignored when ``num_components`` is set.
    * ``num_components`` — explicit retained-mode count ``k`` (overrides
      ``variance``).
    * ``seed`` (default 0) — reproducible GP restart search.
    * ``model_dir`` (default ``<store>/surrogate``) — where the model is saved.
    * ``holdout`` — fraction (0<f<1) or integer count of samples to hold out for
      an accuracy report; when set, the surrogate is refit on the remaining
      samples for the report, then refit on ALL samples for the saved model.
    * ``dose_transform`` (default ``'linear'``) — space the PCA-GP is fit in:
      ``'linear'`` or ``'log10'``. ``'log10'`` fits ``log10(dose + floor)`` to
      handle the Fowler-Nordheim exponential-in-β dynamic range (a linear fit is
      dominated by the peak voxels and barely learns the dose shape); the holdout
      report's relative-L2 is then measured in that same log space.
    * ``floor`` — optional positive offset for the ``'log10'`` transform (defaults
      to the smallest positive training dose).
    * ``n_jobs`` (default 1) — parallelize the per-coefficient GP fits over CPU
      cores via joblib (``1`` = serial, ``-1`` = all cores). Result-invariant: the
      saved model is identical regardless of ``n_jobs``.

    Returns the fitted :class:`DoseSurrogate` (the saved model)."""
    from lume_ace3p.surrogate import DoseSurrogate

    store = mode_cfg.get('store') or mode_cfg.get('output_dir')
    if not store:
        raise ValueError(
            "train_surrogate requires a 'store' (the collect_training_data "
            "training store to fit the surrogate from).")

    ts = surrogate_data.load_training_store(store)
    if ts.dose is None:
        raise ValueError(
            f"training store '{store}' has no dose grids to fit a surrogate on. "
            "A dry-run collection produces β rows but no field artifacts — run a "
            "real (or synthetic) collect_training_data campaign first.")

    variance = float(mode_cfg.get('variance', 0.99))
    k = mode_cfg.get('num_components')
    seed = int(mode_cfg.get('seed', 0))
    model_dir = mode_cfg.get('model_dir') or os.path.join(store, 'surrogate')
    dose_transform = mode_cfg.get('dose_transform', 'linear')
    floor = mode_cfg.get('floor')
    n_jobs = int(mode_cfg.get('n_jobs', 1))

    holdout = mode_cfg.get('holdout')
    if holdout:
        _report_holdout(ts, variance, k, seed, holdout, store,
                        dose_transform=dose_transform, floor=floor,
                        n_jobs=n_jobs)

    # ts.indices is the voxel order the basis columns correspond to; recording it
    # in the model lets invert_optimize align an arbitrary target dose onto the
    # basis rather than assuming a row order (constraint #3).
    surrogate = DoseSurrogate.fit(ts.beta, ts.dose, variance=variance, k=k,
                                  seed=seed, beta_names=ts.beta_names,
                                  dose_transform=dose_transform, floor=floor,
                                  n_jobs=n_jobs, voxel_indices=ts.indices)
    surrogate.save(model_dir)
    print(f" - trained PCA-GP surrogate: {surrogate.num_components} modes "
          f"({surrogate.kept_energy:.4f} energy, dose_transform="
          f"{surrogate.dose_transform}) saved to {model_dir}")
    return surrogate


def _report_holdout(ts, variance, k, seed, holdout, store,
                    dose_transform='linear', floor=None, n_jobs=1):
    """Fit on a train split and report held-out reconstruction accuracy +
    predicted-variance calibration, writing a small ``train_report.txt`` to the
    store. This validates the forward map (Phase-3 bar) before the model saved
    for downstream use is fit on all samples.

    The relative-L2 is measured **in the model's fit space** (``dose_transform``):
    for a ``'log10'`` model that is log space, which is the meaningful metric —
    inverting a log fit back to linear amplifies the ~9-order tail error and would
    report a misleadingly huge number. The report's ``space`` column records which
    space each error is in."""
    from lume_ace3p.surrogate import DoseSurrogate

    n = len(ts)
    if isinstance(holdout, float) and 0.0 < holdout < 1.0:
        n_hold = max(1, int(round(holdout * n)))
    else:
        n_hold = int(holdout)
    n_hold = min(n_hold, n - 2)     # keep >=2 training samples
    if n_hold < 1:
        return

    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    hold_idx, train_idx = perm[:n_hold], perm[n_hold:]

    model = DoseSurrogate.fit(ts.beta[train_idx], ts.dose[train_idx],
                              variance=variance, k=k, seed=seed,
                              beta_names=ts.beta_names,
                              dose_transform=dose_transform, floor=floor,
                              n_jobs=n_jobs)
    # Compare in the fit space: prediction is fit-space, so transform the truth
    # with the model's own transform + fitted floor to match.
    from lume_ace3p.surrogate import _apply_transform
    pred_mean, pred_var = model.predict_dose(ts.beta[hold_idx], space='fit')
    truth = _apply_transform(ts.dose[hold_idx], model.dose_transform, model.floor)
    # Per-sample relative L2 error (in fit space).
    num = np.linalg.norm(pred_mean - truth, axis=1)
    den = np.linalg.norm(truth, axis=1)
    rel_l2 = num / np.where(den == 0.0, 1.0, den)
    mean_pred_std = float(np.mean(np.sqrt(np.maximum(pred_var, 0.0))))

    report = pd.DataFrame({
        'holdout_index': hold_idx,
        'relative_l2': rel_l2,
        'space': model.dose_transform,
    })
    write_table(report, os.path.join(store, 'train_report.txt'))
    print(f" - held-out relative-L2 ({model.dose_transform} space): "
          f"mean={rel_l2.mean():.4f} max={rel_l2.max():.4f}; "
          f"mean predicted std={mean_pred_std:.4g}")


# --------------------------------------------------------------------------- #
# invert_optimize (Phase 4) — given a target dose profile, estimate the β that
# produced it. Runs entirely against the cheap saved surrogate (NOT Geant4) and
# is deliberately NOT a direct dose→β regressor (too ill-posed): the target is
# projected into the surrogate's retained coefficient space and β is searched for
# the coefficients the GPs predict closest to it.
# --------------------------------------------------------------------------- #


def _reference_indices(surrogate, mode_cfg, store):
    """Resolve the voxel order the PCA basis columns correspond to.

    A target dose must be reordered onto this order before projection or the
    inversion is silently misaligned (constraint #3). Prefer the order recorded in
    the model; fall back to the training store's ``indices``; otherwise hard-fail
    with a fix. Never guess an order."""
    if surrogate.voxel_indices is not None:
        return surrogate.voxel_indices
    if store:
        ts = surrogate_data.load_training_store(store)
        if ts.indices is not None:
            return np.asarray(ts.indices, dtype=int)
    raise ValueError(
        "this surrogate does not record the voxel order its PCA basis was built "
        "on (it predates that being saved), so a target dose cannot be aligned "
        "to it — projecting an unaligned target would silently misalign the "
        "basis (constraint #3). Either re-run train_surrogate to save a model "
        "carrying 'voxel_indices', or give this mode a 'store:' pointing at the "
        "training store the model was fit from.")


def _load_inversion_target(mode_cfg, mode_name):
    """Shared setup for both inversion modes.

    Loads the saved surrogate, resolves the voxel order its basis was built on,
    loads + **aligns** the target dose onto that order, projects it into
    coefficient space, and resolves any bounds override. Factored out so
    ``invert_optimize`` and ``invert_bayesian`` cannot drift apart on the part that
    must be identical — the target seam, where a misalignment would silently
    invalidate the projection (constraint #3).

    Returns ``(surrogate, aligned_dose, target_coeffs, bounds_or_None,
    model_dir)``."""
    from lume_ace3p.surrogate import DoseSurrogate

    store = mode_cfg.get('store')
    model_dir = mode_cfg.get('model_dir') or (
        os.path.join(store, 'surrogate') if store else None)
    if not model_dir:
        raise ValueError(
            f"{mode_name} requires a 'model_dir' (a surrogate saved by "
            "train_surrogate) or a 'store' whose 'surrogate/' subdir holds one.")
    if not os.path.isdir(model_dir):
        raise ValueError(
            f"no saved surrogate at '{model_dir}'; run train_surrogate first.")
    surrogate = DoseSurrogate.load(model_dir)

    values, indices = surrogate_data.load_target_dose(mode_cfg.get('target'))
    reference = _reference_indices(surrogate, mode_cfg, store)
    aligned = surrogate_data.align_to_indices(values, indices, reference)

    # project() maps the raw linear dose into the model's fit space itself, so a
    # log10 model needs no special-casing here.
    target_coeffs = surrogate.project(aligned, space='linear')

    bounds = None
    if mode_cfg.get('bounds'):
        bounds = _doe_bounds({'variables': mode_cfg['bounds']},
                             surrogate.beta_names)
    return surrogate, aligned, target_coeffs, bounds, model_dir


def invert_optimize(mode_cfg, workflow=None):
    """Estimate β from a target dose profile (Phase 4, point estimate).

    Loads the saved surrogate, loads and voxel-aligns the target dose, projects it
    into coefficient space, and minimizes ``‖project(target) − c_GP(β)‖²`` over β
    by bounded multi-start L-BFGS-B (:meth:`DoseSurrogate.invert`). The search runs
    against the microsecond-cheap surrogate, so it is dense by default; the
    multi-start also surfaces **non-uniqueness** — several β profiles can explain
    one dose, and every distinct minimum found is reported.

    The misfit is measured in the model's own **fit space**, so a ``'log10'``
    surrogate is inverted in log space (the meaningful space for dose that spans
    ~9 orders of magnitude — a linear residual is dominated by a few peak voxels).

    Mode config keys:

    * ``target`` (required) — the dose profile to invert: a stored field ``.npz``
      (e.g. a held-out sample's ``field.npz``) or a raw Geant4 dose file.
    * ``model_dir`` — the saved surrogate directory. Defaults to
      ``<store>/surrogate`` when ``store`` is given.
    * ``store`` — the training store the model was fit from. Optional; used for the
      voxel order when the model does not carry one, and as the default location
      for ``model_dir`` / the output table.
    * ``num_starts`` (default 32) — multi-start count. More starts = more thorough
      non-uniqueness reporting; each is microseconds.
    * ``seed`` (default 0) — reproducible start scatter (identical β\\*).
    * ``bounds`` — optional per-β ``[lo, hi]`` (or ``{min, max}``) search box,
      keyed by β name. Defaults to the model's training range; outside it the GP
      is extrapolating and β\\* is not trustworthy.
    * ``output_file`` — where the result table goes (default
      ``<store or '.'>/inversion_result.txt``).
    * ``identifiability`` (default ``True``) — also analyse which β directions the
      dose actually constrains (:meth:`DoseSurrogate.identifiability`) and write
      the detail to ``identifiability.txt``. This is what makes a multi-minimum
      result interpretable: with ``k`` retained POD modes the dose can constrain
      at most ``k`` combinations of β, so when ``k < D`` some directions are
      invisible and the "extra" minima are samples of one degenerate surface
      rather than competing answers. Costs ``2·D`` GP evaluations.
    * ``identifiability_file`` — override that path.

    Returns the :class:`lume_ace3p.surrogate.InversionResult`."""
    surrogate, aligned, target_coeffs, bounds, model_dir = (
        _load_inversion_target(mode_cfg, 'invert_optimize'))
    target = mode_cfg.get('target')
    store = mode_cfg.get('store')

    result = surrogate.invert(target_coeffs, bounds=bounds,
                              num_starts=int(mode_cfg.get('num_starts', 32)),
                              seed=int(mode_cfg.get('seed', 0)))

    # One row per distinct minimum. NOTE 'rank' is a stable ordering by misfit,
    # NOT an evidence ranking — see the non-uniqueness reporting below.
    rows = []
    for rank, (misfit, beta_vec) in enumerate(result.minima):
        row = {'rank': rank, 'misfit': misfit,
               'relative_l2': _beta_relative_l2(surrogate, beta_vec, aligned)}
        row.update(dict(zip(surrogate.beta_names, beta_vec.tolist())))
        rows.append(row)
    columns = ['rank', 'misfit', 'relative_l2'] + list(surrogate.beta_names)
    df = pd.DataFrame(rows, columns=columns)
    output_file = mode_cfg.get('output_file') or os.path.join(
        store or '.', 'inversion_result.txt')
    write_table(df, output_file)

    best = ', '.join(f'{n}={v:.4g}' for n, v in result.beta_dict().items())
    print(f" - inverted target '{target}' against {model_dir}")
    print(f" - β* ({best})")
    print(f" - coefficient misfit={result.misfit:.4g}; dose relative-L2 "
          f"({surrogate.dose_transform} space)="
          f"{result.relative_l2(surrogate, aligned):.4f}")

    # Identifiability: how many β directions this dose actually pins down. This
    # is what makes a multi-minimum result interpretable — see _write_identifiability.
    if mode_cfg.get('identifiability', True):
        ident = surrogate.identifiability(result.beta)
        result.identifiability = ident
        print(f" - identifiability: {ident.summary()}")
        ident_file = mode_cfg.get('identifiability_file') or os.path.join(
            os.path.dirname(output_file) or '.', 'identifiability.txt')
        _write_identifiability(ident, ident_file)
        print(f" - identifiability detail written to {ident_file}")

    _report_non_uniqueness(result, output_file)
    return result


def _report_non_uniqueness(result, output_file):
    """Explain a multi-minimum result honestly.

    The key distinction: when every minimum's misfit is numerically ~0 they are
    *equally good* explanations lying on one connected degenerate surface, so
    ordering them by misfit is sorting solver noise, not evidence. Saying
    "N solutions ranked by misfit" in that case would imply a preference the data
    does not support."""
    if result.num_distinct <= 1:
        return
    ident = result.identifiability
    flat = f"; {ident.num_flat} β direction(s) are flat" if ident else ""
    if result.minima_are_distinguishable():
        print(f" - NOTE: {result.num_distinct} distinct β minima explain this "
              f"target with genuinely different misfits{flat}; they are ranked "
              f"by misfit in {output_file}.")
    else:
        print(f" - NOTE: {result.num_distinct} distinct β vectors explain this "
              "target EQUALLY well (all misfits are numerically zero), so they "
              "are NOT ranked by evidence — the ordering in "
              f"{output_file} reflects solver convergence, not preference"
              f"{flat}. They are samples from one continuous degenerate surface. "
              "Run invert_bayesian for a posterior over β, or add "
              "regularization / narrow 'bounds' to select among them on physical "
              "grounds.")


def _write_identifiability(ident, path):
    """Write the identifiability detail table with a short how-to-read preamble.

    One row per β: its sensitivity, then its weight in each identifiable and flat
    direction. Rows are β so the file reads naturally alongside the β columns of
    ``inversion_result.txt``."""
    rows = []
    for j, name in enumerate(ident.beta_names):
        row = {'beta': name, 'sensitivity': float(ident.sensitivity[j])}
        for i in range(ident.identifiable.shape[0]):
            row[f'constrained_{i}'] = float(ident.identifiable[i, j])
        for i in range(ident.null_space.shape[0]):
            row[f'flat_{i}'] = float(ident.null_space[i, j])
        rows.append(row)
    df = pd.DataFrame(rows)

    singular = ' '.join(f'{s:.6g}' for s in ident.singular_values)
    preamble = [
        '# Identifiability of the inverted dose (at the reported beta*).',
        '#',
        f'# {ident.summary()}',
        '#',
        f'# Jacobian singular values (unit-box coords, descending): {singular}',
        f'# Retained POD modes (k): {ident.num_components}  '
        f'-> the dose constrains at most k combinations of beta.',
        '#',
        '# How to read this table:',
        '#   sensitivity     how much the predicted dose coefficients move per',
        '#                   unit-box move of that beta (bigger = more visible).',
        '#   constrained_i   weight of each beta in the i-th direction the dose',
        '#                   DOES constrain (i=0 is the best-constrained).',
        '#   flat_i          weight of each beta in the i-th direction the dose',
        '#                   CANNOT see: moving beta along it leaves the predicted',
        '#                   dose unchanged, so inversion cannot determine it.',
        '#',
    ]
    for i, row in enumerate(ident.identifiable):
        preamble.append(f'# constrained_{i}: {ident.describe_direction(row)}')
    for i, row in enumerate(ident.null_space):
        preamble.append(f'# flat_{i}: {ident.describe_direction(row)}')
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(preamble) + '\n')
    # Append the table through the shared writer's formatting conventions.
    with open(path, 'a') as f:
        df.to_csv(f, sep='\t', index=False, na_rep='nan')
    return path


# --------------------------------------------------------------------------- #
# invert_bayesian (Phase 4b) — a POSTERIOR over β rather than a point estimate.
#
# This is the mode that actually *answers* the non-uniqueness invert_optimize
# reports. The dose constrains at most k combinations of β (k = retained POD
# modes), so with k < D the correct answer is not one β, nor a ranking of the
# equally-good minima, but a distribution: tight along the constrained directions
# and prior-wide along the flat ones.
# --------------------------------------------------------------------------- #


def invert_bayesian(mode_cfg, workflow=None):
    """Sample a posterior over β for a target dose profile (Phase 4b).

    Same target seam as :func:`invert_optimize` (shared via
    ``_load_inversion_target``), but instead of minimizing the coefficient misfit
    it samples ``P(β | target)`` with NUTS over the cheap surrogate — a Gaussian
    likelihood in the model's retained-coefficient space (GP predictive variance +
    an assumed ``dose_sigma``) under a uniform prior on the training box.

    **Why this and not a ranking of the minima.** ``invert_optimize`` typically
    finds many β that explain a target *equally* well (all misfits ~0), so they
    cannot be ordered by evidence. The reason is structural: the surrogate reaches
    β only through ``k`` coefficients, leaving ``D − k`` directions invisible to the
    dose. The posterior expresses that honestly — narrow where the data informs,
    as wide as the prior where it does not.

    Mode config keys:

    * ``target`` (required) — dose profile to invert (stored ``.npz`` or a raw
      Geant4 dose file). Reordered onto the training voxel order automatically.
    * ``model_dir`` / ``store`` — as for :func:`invert_optimize`.
    * ``num_warmup`` (1000), ``num_samples`` (2000) — per chain.
    * ``num_chains`` (4) — **do not lower this casually.** One chain can get stuck
      in a slice of the degenerate manifold and then reports the flat directions as
      narrow, which reads as "the dose constrains β" when it does not. Chains run
      in parallel across CPU devices.
    * ``seed`` (0) — reproducible draws.
    * ``dose_sigma`` — assumed target-noise scale in coefficient space; defaults to
      the model's own predictive std at the box center.
    * ``bounds`` — per-β ``[lo, hi]``; this is the **prior**, and along the flat
      directions the posterior equals it. Defaults to the training box.
    * ``output_file`` — posterior draws (default ``<store>/posterior_samples.txt``).
    * ``summary_file`` — per-β summary + direction widths (default
      ``posterior_summary.txt`` beside it).
    * ``identifiability`` (default ``True``) — compute the constrained/flat split
      so the summary can report posterior width per direction.

    Returns the :class:`lume_ace3p.surrogate.PosteriorResult`."""
    surrogate, aligned, target_coeffs, bounds, model_dir = (
        _load_inversion_target(mode_cfg, 'invert_bayesian'))
    store = mode_cfg.get('store')

    posterior = surrogate.sample_posterior(
        target_coeffs, bounds=bounds,
        dose_sigma=mode_cfg.get('dose_sigma'),
        num_warmup=int(mode_cfg.get('num_warmup', 1000)),
        num_samples=int(mode_cfg.get('num_samples', 2000)),
        num_chains=int(mode_cfg.get('num_chains', 4)),
        seed=int(mode_cfg.get('seed', 0)))

    output_file = mode_cfg.get('output_file') or os.path.join(
        store or '.', 'posterior_samples.txt')
    write_table(pd.DataFrame(posterior.samples, columns=surrogate.beta_names),
                output_file)

    ident = None
    if mode_cfg.get('identifiability', True):
        # At the posterior mean — a representative point on the manifold.
        ident = surrogate.identifiability(posterior.mean())

    summary_file = mode_cfg.get('summary_file') or os.path.join(
        os.path.dirname(output_file) or '.', 'posterior_summary.txt')
    _write_posterior_summary(posterior, ident, summary_file)

    lo, hi = posterior.credible_interval(0.9)
    print(f" - inverted target '{mode_cfg.get('target')}' against {model_dir}")
    print(f" - sampled posterior over β: {len(posterior)} draws "
          f"({mode_cfg.get('num_chains', 4)} chains), "
          f"dose_sigma={posterior.dose_sigma:.4g}")
    for j, name in enumerate(posterior.beta_names):
        print(f"     {name}: {posterior.mean()[j]:8.4g}  "
              f"90% CI [{lo[j]:.4g}, {hi[j]:.4g}]")
    print(f" - draws -> {output_file}; summary -> {summary_file}")

    if ident is not None:
        print(f" - identifiability: {ident.summary()}")
        widths = posterior.direction_widths(ident)
        flat = [r for label, _p, _pr, r in widths if label.startswith('flat')]
        if flat:
            print(f" - flat directions have posterior width "
                  f"{min(flat):.2f}-{max(flat):.2f}x the prior — the data does NOT "
                  "constrain them, so those β combinations are set by 'bounds', "
                  "not by the dose. This is the correct result for a "
                  "rank-deficient inverse, not a sampling failure.")

    r_hat = posterior.max_r_hat()
    if not np.isnan(r_hat) and r_hat > 1.05:
        print(f" - WARNING: max r_hat={r_hat:.3f} > 1.05 — the chains did not mix, "
              "so these credible intervals are NOT trustworthy (a stuck chain "
              "under-reports the width of the flat directions). Increase "
              "'num_warmup'/'num_samples', or 'num_chains'.")
    return posterior


def _write_posterior_summary(posterior, ident, path):
    """Write the per-β posterior summary plus the per-direction width table.

    The direction table is the interpretive payoff: it says which β combinations
    the dose pinned down and which it left at the prior. The preamble states
    explicitly that a prior-wide flat direction is the *correct* outcome, so the
    reader does not mistake it for a convergence problem."""
    lo, hi = posterior.credible_interval(0.9)
    rows = []
    for j, name in enumerate(posterior.beta_names):
        row = {'beta': name,
               'mean': float(posterior.mean()[j]),
               'median': float(posterior.median()[j]),
               'std': float(posterior.std()[j]),
               'ci5': float(lo[j]), 'ci95': float(hi[j]),
               'prior_std': float(posterior.prior_std[j])}
        for key in ('r_hat', 'n_eff'):
            values = posterior.diagnostics.get(key)
            if values is not None and j < len(np.atleast_1d(values)):
                row[key] = float(np.atleast_1d(values)[j])
        rows.append(row)
    df = pd.DataFrame(rows)

    r_hat = posterior.max_r_hat()
    preamble = [
        '# Posterior over beta for the inverted dose (invert_bayesian).',
        '#',
        f'# draws: {len(posterior)}    dose_sigma: {posterior.dose_sigma:.6g}'
        f'    max r_hat: {r_hat:.4f}',
        '#',
        '# Columns: mean/median/std and the 5-95% credible interval per beta, with',
        '# prior_std (the uniform-prior std) for scale, plus per-beta convergence',
        '# diagnostics r_hat (want ~1.0; >1.05 means the chains did not mix) and',
        '# n_eff (effective sample size).',
        '#',
    ]
    if ident is not None:
        preamble += [
            '# ---- Posterior width per direction ----',
            f'# {ident.summary()}',
            '#',
            '# ratio = posterior std / prior std along each direction.',
            '#   constrained_i: ratio << 1 means the dose pinned this beta',
            '#                  combination down.',
            '#   flat_i:        ratio ~ 1 means the dose says NOTHING about this',
            '#                  combination, so the posterior is just the prior.',
            '#                  That is the CORRECT result for a rank-deficient',
            '#                  inverse -- not a sampling failure. To constrain',
            '#                  these you must add information (narrower bounds on',
            '#                  physical grounds, regularization, or more POD modes',
            '#                  if the data supports them).',
            '#',
        ]
        for label, post_std, prior_std, ratio in posterior.direction_widths(ident):
            index = int(label.rsplit('_', 1)[1])
            row = (ident.identifiable[index] if label.startswith('constrained')
                   else ident.null_space[index])
            preamble.append(
                f'# {label}: ratio={ratio:.4f} '
                f'(posterior {post_std:.4g} vs prior {prior_std:.4g})  '
                f'{ident.describe_direction(row)}')
        preamble.append('#')

    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(preamble) + '\n')
    with open(path, 'a') as f:
        df.to_csv(f, sep='\t', index=False, na_rep='nan')
    return path


def _beta_relative_l2(surrogate, beta_vec, aligned_target):
    """Fit-space relative-L2 between a β's predicted dose and the target."""
    from lume_ace3p.surrogate import _apply_transform
    predicted, _var = surrogate.predict_dose(beta_vec, space='fit')
    truth = _apply_transform(aligned_target, surrogate.dose_transform,
                             surrogate.floor)
    denominator = np.linalg.norm(truth)
    return float(np.linalg.norm(predicted - truth)
                 / (denominator if denominator else 1.0))


# --------------------------------------------------------------------------- #
# Row / frame construction — shared by single + parameter_sweep.
# --------------------------------------------------------------------------- #


class _PointResult(NamedTuple):
    """Everything one evaluated sweep point contributes to the result table.

    Read off the evaluation's ``ctx`` as soon as the point finishes, so assembly
    needs neither the context nor the order the points completed in — see
    :func:`_assemble`."""

    point_index: int                  # position in the sweep; the sort key
    scalars: list                     # the swept input values for this point
    outputs: dict                     # extracted output_parameters
    index: object                     # this point's (label, values), or None
    handle: object                    # persisted field artifact, or None


def _assemble(workflow, input_names, points):
    """Build the result DataFrame from a list of :class:`_PointResult`.

    Rows go in **point-index order**, not in the order the points appear in
    ``points``, so the frame does not depend on completion order. That is the
    property that lets a later phase resume or parallelize the loop without
    quietly making every baseline nondeterministic."""
    rows = []
    index = None
    for point in sorted(points, key=lambda p: p.point_index):
        # The frame's index label comes from the last point in index order, which
        # is what the in-loop version resolved to when points ran in order. It is
        # the same label for every point of a run; only its presence varies (a
        # solver that produced nothing reports None).
        index = point.index
        rows.extend(_rows_for_point(workflow, point.index, input_names,
                                    point.scalars, point.outputs, point.handle))
    return _frame(workflow, input_names, rows, index)


def _rows_for_point(workflow, index, input_names, scalars, outputs,
                    field_handle=None):
    """Build the result row(s) for one evaluated point.

    ``index`` is that point's already-resolved field index (``('Frequency',
    array)`` or ``None``), passed in rather than re-derived from a context for the
    reason :func:`_frame` records: asking the workflow again reads whichever
    evaluation ran last, which stops being the same answer once points can
    complete out of order.

    Wide case: a single row of ``{input: scalar, ..., output: scalar}``, plus a
    field-artifact handle column when ``field_handle`` is not ``None``.
    Long case (a module exposes a field index, e.g. S3P frequency): one row per
    index value, each output array sampled at that index — the tidy
    ``(inputs..., Frequency, S(m,n)...)`` frame the plan calls out (no
    field-artifact column; the field values already are the rows)."""
    output_names = list(workflow.output_spec.keys())
    base = dict(zip(input_names, scalars))
    if index is None:
        row = dict(base)
        for name in output_names:
            row[name] = outputs[name]
        if field_handle is not None:
            row[FIELD_ARTIFACT_COLUMN] = field_handle
        return [row]

    label, values = index
    rows = []
    for j in range(len(values)):
        row = dict(base)
        row[label] = values[j]
        for name in output_names:
            row[name] = _sample(outputs[name], j)
        rows.append(row)
    return rows


def _frame(workflow, input_names, rows, index):
    """Assemble the ordered-column DataFrame. Column order is: swept inputs,
    then the field-index label (long case only), then outputs, then an optional
    field-artifact column — matching the left-to-right layout of the legacy
    sweep tables and appending the field reference last so it never displaces a
    baseline column.

    ``index`` is the already-resolved field index of the run(s) the rows came
    from, passed in rather than re-derived: asking the workflow again after the
    loop would read whichever point happened to run last, which stops being the
    same answer as soon as points can run out of order."""
    output_names = list(workflow.output_spec.keys())
    columns = list(input_names)
    if index is not None:
        columns.append(index[0])
    columns += output_names
    if index is None and any(FIELD_ARTIFACT_COLUMN in r for r in rows):
        columns.append(FIELD_ARTIFACT_COLUMN)
    return pd.DataFrame(rows, columns=columns)


def _input_tensor(axes):
    """Tensor product of the swept axis grids as an (N, n_axes) array. No axes
    -> a single empty-row (1, 0) tensor (one run with the base inputs)."""
    if not axes:
        return np.zeros((1, 0))
    grids = [values for _label, values, _setter in axes]
    mesh = np.meshgrid(*grids, indexing='ij')
    return np.stack([m.ravel() for m in mesh], axis=1)


def _sample(value, j):
    """Sample the j-th element of a field-indexed output array; pass a scalar
    through unchanged (so a mis-declared scalar output still lands in the row
    rather than raising)."""
    if isinstance(value, np.ndarray):
        return value[j] if value.ndim and value.shape[0] > j else value
    if isinstance(value, (list, tuple)):
        return value[j] if len(value) > j else value
    return value


# --------------------------------------------------------------------------- #
# Xopt modes (Phase 4) — the generic, workflow-agnostic optimize/GP-sweep
# driver. Objective scalars come from ``workflow.evaluate(input_dict)`` +
# the declarative ``output_parameters`` spec (extraction happens inside the
# workflow/modules), so no S-parameter/frequency parsing lives here.
# --------------------------------------------------------------------------- #


def _log_xopt(filename, xopt_obj, state_file=None, config_hash=None):
    """Log an Xopt run's data table through the shared result writer, and persist
    its resume state beside it.

    ``X.data`` is already a pandas DataFrame, so the table routes straight to
    :func:`lume_ace3p.results.write_table` — the same code path the sweep modes
    use. Overwrites each call so the file always holds the full trajectory.

    The resume state (:func:`lume_ace3p.xopt_state.write_xopt_state`) goes out from
    here because this already runs after every evaluation, which is the granularity
    resume needs; keeping the two together is what stops a state file from being one
    write behind its table. Written whether or not ``resume`` is set, for the same
    reason the run manifest is: the decision to resume is made *after* the
    interruption, so the record has to already exist. ``config_hash`` is recorded
    with it so a later resume can tell whether it is the same campaign."""
    write_table(xopt_obj.data, filename)
    if state_file:
        write_xopt_state(state_file, xopt_obj, config_hash=config_hash)


def _mc_noise_guards(xopt_dict):
    """Return whether the objective is Monte-Carlo-noisy (e.g. a Geant4 dose),
    and enforce the associated mode-config guards.

    These carry forward the Geant4 correctness constraints from the shelved
    surrogate project, expressed as declarative *mode config* (not solver
    inspection, so the mode stays workflow-agnostic):

    * ``mc_noisy_objective: true`` — the objective carries genuine statistical
      noise. The MultiFidelity path must NOT force ``use_low_noise_prior`` (that
      prior is wrong for MC dose); see :func:`_build_generator`.
    * When ``mc_noisy_objective`` is set, an explicit ``bin_edges`` must be
      provided so the noisy scalar (e.g. a dose histogram bin) is well-defined
      rather than silently inferred. Missing it is a clear error.
    """
    mc_noisy = bool(xopt_dict.get('mc_noisy_objective', False))
    if mc_noisy and 'bin_edges' not in xopt_dict:
        raise ValueError(
            "mc_noisy_objective is set (the objective is Monte-Carlo noisy, "
            "e.g. a Geant4 dose) but no explicit 'bin_edges' was provided. "
            "An MC-noisy objective must fix its binning explicitly.")
    return mc_noisy


# The generators ``_build_generator`` knows, named for the error it raises on a
# misspelling. ``BayesianExplorationGenerator`` is not here because it is not
# selectable: ``gp_parameter_sweep`` constructs it unconditionally.
SUPPORTED_GENERATORS = (
    'NelderMeadGenerator', 'ExpectedImprovementGenerator',
    'MultiFidelityGenerator', 'UpperConfidenceBoundGenerator',
    'ExpectedHypervolumeImprovementGenerator')


def _build_generator(vocs, vocs_dict, xopt_dict, mc_noisy):
    """Construct the Xopt generator named by ``xopt_dict['generator']``.

    Preserves the six generators supported today with their behavior unchanged
    (NelderMead, ExpectedImprovement, MultiFidelity, UpperConfidenceBound,
    ExpectedHypervolumeImprovement/MOBO, and — via
    :func:`gp_parameter_sweep` — BayesianExploration).

    **Raises** ``ValueError`` for an unsupported generator, and for MOBO without a
    ``reference_point``. It used to print and return ``None``, which the CLI then
    ignored — so a misspelled generator name produced a job that exited 0 having done
    nothing, which in a batch queue is indistinguishable from success."""
    name = xopt_dict['generator']
    if name == 'NelderMeadGenerator':
        from xopt.generators.sequential.neldermead import NelderMeadGenerator
        # xopt 3.0.0 requires NelderMead to have a starting point: either an
        # explicit initial_point, initial_simplex, or existing data. When the
        # config does no random seeding (num_random absent/0), seed the initial
        # point at the midpoint of each variable's bounds (read from the raw
        # config dict — VOCS.variables holds ContinuousVariable objects).
        if not xopt_dict.get('num_random', 0):
            initial_point = {vn: 0.5 * (b[0] + b[1])
                             for vn, b in vocs_dict['variables'].items()}
            return NelderMeadGenerator(vocs=vocs, initial_point=initial_point)
        return NelderMeadGenerator(vocs=vocs)
    if name == 'ExpectedImprovementGenerator':
        from xopt.generators.bayesian import ExpectedImprovementGenerator
        return ExpectedImprovementGenerator(vocs=vocs)
    if name == 'MultiFidelityGenerator':
        from xopt.generators.bayesian import MultiFidelityGenerator
        generator = MultiFidelityGenerator(vocs=vocs)
        # Geant4 guard: only force the low-noise GP prior for a smooth (e.g.
        # S-parameter) objective. An MC-noisy dose has genuine noise, so leave
        # use_low_noise_prior at its default (False) when mc_noisy_objective.
        if not mc_noisy:
            generator.gp_constructor.use_low_noise_prior = True
        return generator
    if name == 'UpperConfidenceBoundGenerator':
        from xopt.generators.bayesian import UpperConfidenceBoundGenerator
        options = xopt_dict.get('generator_options', {})
        return UpperConfidenceBoundGenerator(vocs=vocs, **options)
    if name == 'ExpectedHypervolumeImprovementGenerator':
        from xopt.generators.bayesian.mobo import (
            MOBOGenerator as ExpectedHypervolumeImprovementGenerator)
        options = xopt_dict.get('generator_options', {})
        if 'reference_point' not in options:
            raise ValueError(
                "'ExpectedHypervolumeImprovementGenerator' requires a "
                "'reference_point' in 'generator_options' — the point in objective "
                "space hypervolume is measured from. Multi-objective optimization "
                "has no meaning without it.")
        return ExpectedHypervolumeImprovementGenerator(vocs=vocs, **options)
    raise ValueError(
        f"'{name}' is not a supported Xopt generator. The name must match an Xopt "
        f"generator exactly; supported here are: {', '.join(SUPPORTED_GENERATORS)}.")


def _make_vocs(vocs_dict):
    """Build a standard Xopt :class:`~xopt.vocs.VOCS` from the declarative VOCS
    block. The objective *names* are ``output_parameters`` names — the same keys
    :meth:`Workflow.evaluate` returns — so extraction stays a workflow concern.

    Clean break: the VOCS block is the plain Xopt shape
    (``variables`` + ``objectives`` name->MINIMIZE/MAXIMIZE, with optional
    ``constraints`` / ``observables`` / ``constants``), NOT the legacy
    S-parameter/frequency triple."""
    from xopt.vocs import VOCS
    warn_unrecognized("'vocs_parameters'", vocs_dict, VOCS_KEYS)
    kwargs = {'variables': vocs_dict['variables']}
    for key in ('objectives', 'constraints', 'observables', 'constants'):
        if vocs_dict.get(key):
            kwargs[key] = vocs_dict[key]
    return VOCS(**kwargs)


def _objective_from_workflow(workflow, vocs, xopt_dict, first_index=0):
    """Return an Xopt evaluator function that drives ``workflow.evaluate`` and
    returns the VOCS output scalars, generically.

    The function pulls exactly the VOCS output names (objectives + constraints +
    observables) out of the workflow's returned outputs — no solver-specific
    parsing. When a fidelity variable is configured (MultiFidelity), the Xopt
    fidelity axis ``s`` is renamed to the user's variable name before being
    handed to the workflow (unchanged from the legacy driver).

    Each call is counted, and the count names the evaluation's workdir
    (:func:`_iteration_workdir`) so an optimization's evaluations do not overwrite
    each other. ``first_index`` is where the numbering starts — 0 for a fresh run;
    a resumed one continues past the evaluations it inherited rather than
    reoccupying their directories. Counting *calls* is only a faithful iteration
    index while evaluations are serial, which they are today
    (``Evaluator(function=...)`` runs with ``max_workers = 1``); concurrent
    evaluation will have to assign the index at proposal time instead."""
    output_names = list(vocs.output_names)
    fidelity_variable = xopt_dict.get('fidelity_variable')
    iterations = itertools.count(first_index)

    def sim_function(input_dict):
        input_dict = dict(input_dict)
        if fidelity_variable is not None and 's' in input_dict:
            input_dict[fidelity_variable] = input_dict.pop('s')
        outputs, _ctx = _evaluate(
            workflow, input_dict,
            workdir=_iteration_workdir(workflow, next(iterations)))
        missing = [n for n in output_names if n not in outputs]
        if missing:
            raise KeyError(
                f"workflow.evaluate did not return VOCS output(s) {missing}; "
                f"declare them in output_parameters. Got {list(outputs)}.")
        return {n: outputs[n] for n in output_names}

    return sim_function


def _evaluated(X):
    """How many evaluations an :class:`xopt.Xopt` already holds.

    ``X.data`` is ``None`` on a freshly constructed one — not an empty frame — so
    every campaign-total budget below reads the count through here."""
    return 0 if X.data is None else len(X.data)


def _objectives_for(workflow, vocs, xopt_dict, state_file, resume):
    """Everything a run needs decided **before** its generator is built:
    ``(state, fresh_objective, resumed_objective)``.

    The ordering is load-bearing, not stylistic. ``MultiFidelityGenerator``'s VOCS
    validator *mutates the VOCS object it is handed*, adding the fidelity axis ``s``
    as both a variable and an objective (``xopt 3.0.0``). The objective closure
    snapshots ``vocs.output_names`` when it is created, so building it after the
    generator would make it demand an ``s`` output the workflow does not produce and
    cannot declare. Both closures are therefore built here, up front.

    Two of them because the iteration counter differs: a fresh run numbers its
    workdirs from 0, a resumed one from the count of evaluations it inherits, so it
    does not reoccupy ``_0…_k-1``. That count has to come from the state *mapping*,
    since the objective must exist before there is an ``Xopt`` to restore it into.

    ``resume: false`` reads nothing at all — not even to report on it."""
    state = read_xopt_state(state_file) if resume else None
    fresh = _objective_from_workflow(workflow, vocs, xopt_dict)
    inherited = evaluation_count(state) if state is not None else 0
    resumed = (fresh if not inherited
               else _objective_from_workflow(workflow, vocs, xopt_dict,
                                             first_index=inherited))
    return state, fresh, resumed


def _campaign_hash(workflow, vocs, xopt_dict):
    """The resolved-configuration hash for this campaign, or ``None`` when the
    workflow cannot produce one.

    Every name the optimizer *drives* is excluded from the hash, so editing a
    variable's nominal starting value does not invalidate a campaign: that is the
    VOCS variables plus the fidelity variable, which reaches the workflow under the
    user's own name via the rename in :func:`_objective_from_workflow`.

    ⚠️ Must be called **before** the generator is built, for the reason
    :func:`_objectives_for` records — ``MultiFidelityGenerator`` adds ``s`` to the
    VOCS in place, and the hash has to be computed from the same variable list on
    the write and the read.

    ``None`` covers the test doubles, which expose ``evaluate`` and nothing else —
    the same ``getattr`` gate :func:`_iteration_workdir` uses, and for the same
    reason. A campaign driven by one records no hash and is not checked against one,
    so the doubles stay valid unedited."""
    resolve = getattr(workflow, 'campaign_config_hash', None)
    if resolve is None:
        return None
    driven = list(vocs.variable_names)
    fidelity = xopt_dict.get('fidelity_variable')
    if fidelity:
        driven.append(fidelity)
    return resolve(driven)


def _resume_xopt(state, objective, generator, state_file, log_file,
                 config_hash=None):
    """Restore an interrupted optimization from ``state``, or ``None`` to start
    fresh — after saying why, which :func:`~lume_ace3p.xopt_state.restore_xopt`
    does for every refusal.

    A **refused** state is moved aside rather than left to be overwritten. The run is
    about to start a fresh campaign in the same place, and the files it would
    overwrite are the trajectory and state the user explicitly asked to continue:
    declining to resume must not also destroy what was being resumed. "No state
    found" is not a refusal and moves nothing — there is nothing there."""
    if state is None:
        return None
    X = restore_xopt(state, objective, generator=generator,
                     config_hash=config_hash)
    if X is None:
        moved = set_aside([state_file, log_file])
        if moved:
            kept = ', '.join(f"'{was}' -> '{now}'" for was, now in moved)
            print(f" - the refused campaign is kept: {kept}. This run starts a "
                  "fresh one; move them back to inspect or recover it.")
        return None
    print(f" - resuming from '{state_file}': {_evaluated(X)} evaluation(s) already "
          "recorded. Budgets are campaign totals, so this run continues to the "
          "same finish line. Note a resumed search does not reproduce the "
          "trajectory an uninterrupted run would have taken — it continues from "
          "the same data, and repeats no evaluation.")
    return X


def _xopt_evaluations(xopt_dict):
    """How many evaluations a :func:`scalar_optimize` config will perform, as a
    **lower bound**.

    Only used to decide whether :func:`_require_own_workdirs` has something to warn
    about, so a lower bound is the right shape: the cost-limited (multi-fidelity)
    path stops on a measured runtime budget rather than a count, and "at least the
    seeding plus a step" is enough to know that more than one evaluation is
    coming."""
    count = (int(xopt_dict.get('num_random', 0) or 0)
             + int(xopt_dict.get('num_step', 0) or 0))
    if 'max_iterations' in xopt_dict:
        count = max(count, int(xopt_dict['max_iterations']))
    if 'cost_budget' in xopt_dict or 'alotted_time' in xopt_dict:
        count = max(count, int(xopt_dict.get('num_random', 2) or 2) + 1)
    return count


# The keys that make an optimization stop, split by what they actually do. A
# *criterion* is what ends the loop and one is required; a *refinement* narrows a
# criterion and does nothing on its own — ``tolerance`` is only tested inside the
# ``num_step`` / ``cost_budget`` loops (``check_tols``), and ``max_iterations`` is read
# only inside the ``num_step`` branch. The old message called ``tolerance`` a
# criterion, which sent a user who had supplied exactly that round in a circle.
_TERMINATION_CRITERIA = ('num_step', 'cost_budget', 'alotted_time')
_TERMINATION_REFINEMENTS = {
    'max_iterations': ("caps the total steps a 'num_step' run may take, and is "
                       "ignored without it"),
    'tolerance': ("is a stopping test applied inside whichever criterion's loop is "
                  "running, so it needs a loop to be inside"),
}


def _no_criterion_message(xopt_dict):
    """What to say when ``xopt_parameters`` has nothing that would end the run.

    Names the refinement the config *did* supply, and says what it actually does,
    because "you gave me ``max_iterations`` but no ``num_step``" is the mistake and is
    more use than a list of keys. The old message named ``tolerance`` as a criterion,
    which sent a user who had supplied exactly that round in a circle."""
    message = ("No termination criterion in 'xopt_parameters', so this optimization "
               "would never end. Provide one of: "
               + ', '.join(f"'{key}'" for key in _TERMINATION_CRITERIA)
               + " ('cost_budget'/'alotted_time' are the multi-fidelity form).")
    for key, does in _TERMINATION_REFINEMENTS.items():
        if key in xopt_dict:
            message += f" '{key}' is set, but it is not a criterion — it {does}."
    return message


def _tolerances(xopt_dict, targets):
    """Normalize an optional ``tolerance`` into ``{target: value}`` or ``None``.

    Accepts a scalar (applied to every objective) or a mapping keyed by
    objective name. Generic replacement for the legacy per-objective tolerance
    that lived inside the S-parameter objective block."""
    tol = xopt_dict.get('tolerance')
    if tol is None:
        return None
    if isinstance(tol, dict):
        return {t: tol[t] for t in targets if t in tol}
    return {t: tol for t in targets}


def scalar_optimize(workflow, vocs_dict, xopt_dict, log_file='sim_output.txt',
                    resume=False):
    """Drive an Xopt scalar optimization of ``workflow`` (Phase 4).

    Workflow-agnostic: the objective scalar(s) are whatever ``vocs_dict``
    declares as objectives, pulled from ``workflow.evaluate(input_dict)``. Any
    workflow with a matching ``output_parameters`` spec (S3P reflection, a
    Geant4 dose/weight, a multi-step chain) can be optimized with no changes
    here.

    Supports all six generators with their fidelity-variable rename,
    cost-function logic, and termination criteria; the objective is extracted
    generically from the workflow outputs and logged via the shared result
    writer. Returns the :class:`xopt.Xopt` object.

    **``resume``** (``mode: {resume: true}``) continues an interrupted campaign
    from ``xopt_state.yml`` instead of starting over — the optimizer's data *and*
    its generator's own state, so a Nelder-Mead simplex carries on rather than
    restarting on top of old data (see :mod:`lume_ace3p.xopt_state`). The promise
    is **no evaluation is repeated and the search continues from the same data**;
    unlike the table modes, a resumed run does *not* reproduce the trajectory an
    uninterrupted one would have taken. Unlike them it also works under any
    ``workdir_mode``, since it restores from the campaign's state file rather than
    from per-evaluation manifests. When the state cannot be used the run says why
    and starts fresh.

    Every iteration budget below — ``num_random``, ``num_step``,
    ``max_iterations``, ``cost_budget`` — is a total for the **campaign** rather
    than for this process, so a resumed run continues to the same finish line and
    resuming a *finished* optimization does nothing. That is the same contract the
    sweep has, where a completed campaign resumed re-runs nothing."""
    import torch
    from xopt.vocs import random_inputs as vocs_random_inputs
    from xopt.evaluator import Evaluator
    from xopt import Xopt

    warn_unrecognized("'xopt_parameters'", xopt_dict, XOPT_KEYS)
    mc_noisy = _mc_noise_guards(xopt_dict)
    _require_own_workdirs(workflow, _xopt_evaluations(xopt_dict))
    vocs = _make_vocs(vocs_dict)
    targets = list(vocs.objective_names)
    tols = _tolerances(xopt_dict, targets)

    # Both before _build_generator, which for MultiFidelity mutates ``vocs`` — see
    # _objectives_for and _campaign_hash.
    state_file = xopt_state_path(log_file)
    campaign_hash = _campaign_hash(workflow, vocs, xopt_dict)
    state, fresh, resumed = _objectives_for(workflow, vocs, xopt_dict, state_file,
                                            resume)
    generator = _build_generator(vocs, vocs_dict, xopt_dict, mc_noisy)
    X = _resume_xopt(state, resumed, generator, state_file, log_file,
                     config_hash=campaign_hash)
    inherited = None if X is None else _evaluated(X)
    if X is None:
        X = Xopt(evaluator=Evaluator(function=fresh), generator=generator,
                 vocs=vocs)

    # Not 0: on a resumed run the inherited evaluations already count against every
    # budget below, which is what makes resume idempotent. Reading a *row count* into
    # a counter the loops then increment per step is exact only while a step produces
    # one row, which is the same one-evaluation-at-a-time property the objective's
    # iteration index relies on (see _objective_from_workflow). A batch generator
    # would need both to move to per-row accounting.
    iteration_index = _evaluated(X)
    tol_achieved = False

    def check_tols():
        # All objectives must meet their tolerance for termination.
        if not tols:
            return False
        achieved = True
        for t in targets:
            if t in tols and not (X.data[t].iloc[-1] <= tols[t]):
                achieved = False
        return achieved

    # Initial random evaluations to seed the model. Skipped to the extent the
    # restored data already covers them (on a fresh run this is the whole count).
    num_random = int(xopt_dict.get('num_random', 0) or 0)
    for _ in range(max(0, num_random - iteration_index)):
        X.random_evaluate()
        _log_xopt(log_file, X, state_file, campaign_hash)
        iteration_index += 1

    if 'num_step' in xopt_dict:
        # The seeding plus the steps, as a campaign total.
        step_budget = num_random + int(xopt_dict['num_step'])
        while iteration_index < step_budget:
            X.step()
            _log_xopt(log_file, X, state_file, campaign_hash)
            iteration_index += 1
        if 'max_iterations' in xopt_dict:
            while iteration_index < xopt_dict['max_iterations'] and not tol_achieved:
                X.step()
                if tols:
                    tol_achieved = check_tols()
                _log_xopt(log_file, X, state_file, campaign_hash)
                iteration_index += 1

    # Cost-limited (multi-fidelity) termination: run until a cost budget or the
    # tolerance is reached. The fidelity axis ('s') + cost-function logic is
    # generic to MultiFidelity and preserved unchanged.
    elif 'cost_budget' in xopt_dict or 'alotted_time' in xopt_dict:
        if 'cost_budget' in xopt_dict:
            cost_budget = xopt_dict.get('cost_budget')
        else:
            hours, minutes, seconds = xopt_dict.get('alotted_time').split(':')
            cost_budget = float(hours) * 3600 + float(minutes) * 60 + float(seconds)

        # This branch seeds its own fidelity ladder, on top of whatever the
        # 'num_random' loop above contributed (which is how it has always worked:
        # a config with num_random: 3 seeds 3 random points and then 3 more along
        # the ladder). On a resume, ``seeded`` is how much of the ladder the
        # restored data already holds, so only the remainder is evaluated.
        ladder = xopt_dict.get('num_random', 2)
        seeded = max(0, min(ladder, iteration_index - num_random))
        if seeded < ladder:
            random_pts = vocs_random_inputs(vocs, ladder - seeded)
            init_fidelity = np.linspace(0, 1, ladder)
            for it in range(len(random_pts)):
                random_pts[it]['s'] = init_fidelity[seeded + it]
            X.evaluate_data(pd.DataFrame(random_pts))
            _log_xopt(log_file, X, state_file, campaign_hash)
            iteration_index = _evaluated(X)

        cost_function = xopt_dict.get('cost_function', 'exponential')
        if cost_function.lower() == 'exponential':
            p1 = X.data['xopt_runtime'][ladder - 1] / X.data['xopt_runtime'][0]

            def cost_func(x):
                val = X.data['xopt_runtime'][0] * torch.exp(
                    torch.tensor(np.log(p1)) * x)
                time_left = cost_budget - X.data['xopt_runtime'].sum()
                return val / time_left
            X.generator.cost_function = cost_func
        elif cost_function.lower() == 'gaussian_process':
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
            kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=2.0,
                                               length_scale_bounds=(1e-2, 1e2))
            gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=3,
                                          alpha=1e-4, normalize_y=True)

            def cost_func(x):
                x_np = x.detach().cpu().numpy().reshape(-1, 1)
                x_train = np.array(X.data['s']).reshape(-1, 1)
                y_train = np.array(X.data['xopt_runtime']).reshape(-1, 1)
                gp.fit(x_train, y_train)
                return torch.as_tensor(gp.predict(x_np),
                                       dtype=torch.float32).view(-1, 1, 1)
            X.generator.cost_function = cost_func
        else:
            raise ValueError(
                f"cost_function '{cost_function}' is not supported; use "
                "'exponential' (an explicit exponential cost-vs-fidelity "
                "relationship) or 'gaussian_process' (learned from the measured "
                "runtimes).")

        # The budget is the campaign's total measured cost, so the runtimes inherited
        # from a resumed run count against it exactly as this process's own do.
        while X.data['xopt_runtime'].sum() < cost_budget and not tol_achieved:
            X.step()
            if tols:
                tol_achieved = check_tols()
            _log_xopt(log_file, X, state_file, campaign_hash)
            iteration_index += 1
    else:
        raise ValueError(_no_criterion_message(xopt_dict))

    if inherited is not None and _evaluated(X) == inherited:
        # Correct (the budgets are campaign totals) but silent otherwise, and a
        # resumed run that prints nothing after "resuming…" reads like a hang.
        print(f" - nothing to do: the {inherited} recorded evaluation(s) already "
              "cover this configuration's budget. Raise 'num_step' / "
              "'max_iterations' / 'cost_budget' to continue the campaign further.")
    _save_model(X, xopt_dict)
    return X


def gp_parameter_sweep(workflow, sweep_dict, vocs_dict, xopt_dict,
                       log_file='sim_output.txt',
                       sweep_file='sweep_output.txt',
                       resume=False):
    """Drive an Xopt Bayesian-exploration loop over ``workflow`` and emit a
    GP-posterior-mean sweep over the ``sweep_parameters`` grid (Phase 4).

    Workflow-agnostic in the same way as :func:`scalar_optimize`: the explored
    quantities are the VOCS objectives (declared 'explore'), pulled from
    ``workflow.evaluate``. Returns the :class:`xopt.Xopt` object.

    **``resume``** continues an interrupted exploration from ``xopt_state.yml``
    exactly as :func:`scalar_optimize` does, with the same promise and the same
    caveat. ``num_random`` and ``max_steps`` are campaign totals. The
    convergence test (``improvement_threshold`` / ``patience``) is *not* carried
    across the interruption — it is a window over recent steps, not state — so a
    resumed run gives the search at least ``patience`` more steps before it can
    stop on it. The GP posterior-mean sweep is recomputed from the restored model
    either way."""
    import torch
    from xopt.evaluator import Evaluator
    from xopt import Xopt
    from xopt.generators.bayesian import BayesianExplorationGenerator

    warn_unrecognized("'xopt_parameters'", xopt_dict, XOPT_KEYS)
    warn_unrecognized("'vocs_parameters'", vocs_dict, VOCS_KEYS)
    _mc_noise_guards(xopt_dict)
    # At least the random seeding plus the first step; the loop's real length is
    # decided by convergence (see the improvement/patience check below).
    _require_own_workdirs(workflow,
                          int(xopt_dict.get('num_random', 5) or 0) + 1)

    # xopt 3.0.0's BayesianExplorationGenerator requires 'explore'-type
    # objectives; support the target quantities declared under 'objectives'
    # (preferred) or the older 'observables' list.
    from xopt.vocs import VOCS
    objectives = vocs_dict.get('objectives') or {}
    if objectives:
        targets = list(objectives.keys())
        vocs = VOCS(variables=vocs_dict['variables'], objectives=objectives)
    else:
        targets = list(vocs_dict.get('observables', []))
        vocs = VOCS(variables=vocs_dict['variables'], observables=targets)
    state_file = xopt_state_path(log_file)
    campaign_hash = _campaign_hash(workflow, vocs, xopt_dict)
    state, fresh, resumed = _objectives_for(workflow, vocs, xopt_dict, state_file,
                                            resume)
    generator = BayesianExplorationGenerator(vocs=vocs)
    X = _resume_xopt(state, resumed, generator, state_file, log_file,
                     config_hash=campaign_hash)
    if X is None:
        X = Xopt(evaluator=Evaluator(function=fresh), generator=generator,
                 vocs=vocs)

    num_random = xopt_dict.get('num_random', 5)
    # Logged inside the loop, not after it: without this a run killed during
    # seeding lost all of it, since the first write only came after the first step.
    for _ in range(max(0, num_random - _evaluated(X))):
        X.random_evaluate()
        _log_xopt(log_file, X, state_file, campaign_hash)

    improvement = xopt_dict.get('improvement_threshold', 0.01)
    patience = xopt_dict.get('patience', 5)
    prev_bests = []
    # A campaign total, so 'max_steps' means the same thing to a resumed run.
    steps = max(0, _evaluated(X) - num_random)
    hit_max_steps = False
    while not hit_max_steps:
        X.step()
        _log_xopt(log_file, X, state_file, campaign_hash)
        steps += 1
        if 'max_steps' in xopt_dict and steps > xopt_dict['max_steps']:
            hit_max_steps = True
        current_best = sum(X.data[o].min() for o in targets) / len(targets)
        prev_bests.append(current_best)
        if len(prev_bests) > patience:
            old = prev_bests[-(patience + 1)]
            new = prev_bests[-1]
            if np.abs(old - new) / old < improvement:
                break

    # GP posterior-mean sweep over the sweep_parameters tensor product.
    param_grid = {p: np.linspace(sweep_dict[p]['min'], sweep_dict[p]['max'],
                                 sweep_dict[p]['num'])
                  for p in sweep_dict}
    input_varname = list(param_grid)
    grids = [param_grid[v] for v in input_varname]
    # Preserve the legacy tile/repeat ordering (first axis fastest) so the rows
    # land in the same order as the Phase-0.5 baseline sweep_output.txt.
    input_tensor = np.stack(_legacy_meshorder(grids), axis=1)

    # Build the GP posterior-mean sweep as a DataFrame (columns = swept inputs +
    # explored targets) and write it through the shared result writer — the same
    # code path the scalar sweep modes and the Xopt log use.
    sweep_rows = []
    for i in range(input_tensor.shape[0]):
        row = {input_varname[j]: input_tensor[i][j]
               for j in range(len(input_varname))}
        test_points = torch.tensor(pd.DataFrame([row]).values,
                                   dtype=torch.double)
        posterior = X.generator.model.posterior(test_points).mean
        # One point in -> posterior mean shape (1, n_targets); pull each target.
        means = posterior[0]
        for k, obj in enumerate(targets):
            row[obj] = float(means[k])
        sweep_rows.append(row)
    sweep_df = pd.DataFrame(sweep_rows, columns=input_varname + targets)
    write_table(sweep_df, sweep_file)

    _save_model(X, xopt_dict)
    return X


def _legacy_meshorder(grids):
    """Reproduce the legacy ``run_lf_sweep`` tensor-product ordering (tile the
    running tensor, repeat the next axis) so the GP-sweep rows land in the same
    order as the Phase-0.5 baseline ``sweep_output.txt``."""
    input_vardim = [len(g) for g in grids]
    tensor = grids[0]
    if len(grids) == 1:
        return [tensor]
    t1 = np.tile(tensor, input_vardim[1])
    t2 = np.repeat(grids[1], input_vardim[0])
    tensor = np.vstack([t1, t2]).T
    for i in range(2, len(grids)):
        t1 = np.tile(tensor, (input_vardim[i], 1))
        t2 = np.repeat(grids[i], np.size(tensor, 0))
        tensor = np.vstack([t1.T, t2]).T
    return [tensor[:, j] for j in range(tensor.shape[1])]


def _save_model(X, xopt_dict):
    """Persist the trained GP model + hyperparameters when ``save_model`` is
    set (unchanged from the legacy driver)."""
    import torch
    if not xopt_dict.get('save_model', False):
        return
    try:
        if hasattr(X.generator, 'model') and X.generator.model is not None:
            torch.save(X.generator.model.state_dict(), "Binary_gp_model.pt")
            with open("gp_parameters.txt", "w") as f:
                f.write("Gaussian Process Hyperparameters:\n")
                f.write("=================================\n")
                for name, param in X.generator.model.named_parameters():
                    val = param.detach().cpu().numpy()
                    f.write(f"{name}: {val}\n")
        else:
            print(" - Generator has no model to save.")
    except Exception as e:
        print(f" - Error saving model: {e}")
