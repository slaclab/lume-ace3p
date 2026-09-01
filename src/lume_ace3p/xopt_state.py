"""Resume state for the Xopt modes (Phase B of
``plans/xopt_resume_workdir_plan.md``).

The table modes resume from a per-point completion manifest
(:mod:`lume_ace3p.state`): their points are a fixed, knowable set, so "which ones
are already done" is a question with an answer. An optimization has no such set —
the generator proposes each point as the run proceeds — so this is a **different
mechanism**, not an extension of that one. What is saved and restored here is the
optimizer's whole state: the trajectory it has evaluated *and* the generator's own
internal state.

One file, ``xopt_state.yml``, written beside the mode's ``output_file`` after every
evaluation, by :func:`write_xopt_state`. A 200-evaluation optimization killed at
190 used to throw away all 190 — which is worse than the sweep case, because the
*evaluations* are the expensive part.

Why the generator's state and not just the data
-----------------------------------------------
``sim_output.txt`` already holds the full trajectory, so ``X.add_data(table)`` into
a freshly built generator would restore the *data*. For a Bayesian generator that
is nearly equivalent — the GP is refit from data either way. For
``NelderMeadGenerator`` it is **not**: the simplex *is* the state, so a data-only
restore silently restarts the search on top of old data, and re-proposes points it
already has. Verified against ``xopt 3.0.0``, continuing the same 4-evaluation run
three steps:

.. code-block:: text

   full-state continuation : 0.35,  0.2,   0.275
   data-only continuation  : 0.425, 0.375, 0.275     # 0.425 is a duplicate

So the whole state is restored. ``add_data`` remains the fallback for a run whose
state file is gone but whose table survives — at the cost above.

Why not ``Xopt.from_file``
--------------------------
It re-imports the evaluator function from the dotted path recorded in the dump, and
ours is a closure over the workflow
(:func:`lume_ace3p.modes._objective_from_workflow`)::

    ModuleNotFoundError: No module named 'lume_ace3p.modes.make_objective'

:func:`restore_xopt` instead swaps the **live** callable into the loaded mapping and
reconstructs ``Xopt`` around it, which restores data and generator state both.

What a resume does and does not promise
---------------------------------------
**No evaluation is repeated, and the search continues from the same data.** That is
the promise. It is deliberately *weaker* than the table modes', which produce an
identical table: restoring history makes the generator propose from an equally
informed state, not from the same state a straight-through run would have been in —
the torch/numpy RNG streams alone break that. Do not expect two ``sim_output.txt``
files to diff clean.

Degrading safely
----------------
This mirrors :func:`lume_ace3p.state.read_state`'s contract: an absent, unreadable
or truncated state file degrades to "no state", which means *start over* rather than
crash or misread. A state file whose generator or VOCS disagrees with the config is
**refused** rather than adopted — resuming a ``MINIMIZE`` campaign into a
``MAXIMIZE`` config would optimize against the old data. That check is this phase's
``config_hash``.
"""

import copy
import os


# Written beside the mode's ``output_file`` (``sim_output.txt`` by default) rather
# than in a workdir: an optimization's workdirs are per *evaluation*, and this
# describes the campaign.
STATE_FILE = 'xopt_state.yml'

# Our own block inside the dump. ``X.dump`` writes xopt's model fields and nothing
# else, so the campaign hash (which xopt has no notion of) is appended under a
# namespaced key and stripped again before the mapping is handed back to ``Xopt``.
LUME_KEY = 'lume_ace3p'
CONFIG_HASH_KEY = 'campaign_config_hash'

# Suffix a refused campaign's files are moved to rather than overwritten.
REJECTED_SUFFIX = '.rejected'


def xopt_state_path(log_file):
    """``xopt_state.yml`` beside ``log_file`` (the mode's ``output_file``).

    Derived rather than configurable so ``--status`` computes the same path from the
    same config with nothing to keep in sync."""
    directory = os.path.dirname(log_file) if log_file else ''
    return os.path.join(directory, STATE_FILE) if directory else STATE_FILE


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


# Paths already reported as unwritable. The state is dumped after *every*
# evaluation, so without this a read-only directory would print one identical
# warning per evaluation and bury the run's real output.
_WARNED_PATHS = set()


def write_xopt_state(path, xopt_obj, config_hash=None):
    """Dump ``xopt_obj``'s full state to ``path``, atomically. Returns the path,
    or ``None`` if nothing could be written.

    Written after **every** evaluation, so an interrupted write is a real
    possibility — and a truncated state file is precisely what a resume would read.
    The dump goes to a temporary beside the target and is then :func:`os.replace`\\ d,
    so a reader sees either the previous state or the new one.

    This is why xopt's own ``dump_file`` auto-dump is not used: it has the right
    *granularity* (it writes inside ``evaluate_data``) but writes in place, so a kill
    mid-write leaves exactly the truncated file a resume would then read.

    ``config_hash`` is the campaign's resolved-configuration hash
    (:meth:`~lume_ace3p.workflow_graph.Workflow.campaign_config_hash`), appended
    under :data:`LUME_KEY` because ``X.dump`` writes xopt's own fields and nothing
    else. :func:`restore_xopt` refuses a state whose hash disagrees.

    A dump failure is reported **once per path** and swallowed. Persisting state is a
    service to a *later* run; failing the optimization in progress over it would
    trade an expensive campaign for a recoverability feature it is not using yet."""
    if not path:
        return None
    import yaml
    temporary = path + '.tmp'
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        xopt_obj.dump(temporary)
        if config_hash:
            # A separate top-level key appended to the dump's block mapping, written
            # through the YAML dumper rather than as raw text so it cannot produce a
            # file that only *looks* valid.
            with open(temporary, 'a') as file:
                file.write(yaml.safe_dump({LUME_KEY:
                                           {CONFIG_HASH_KEY: config_hash}}))
        os.replace(temporary, path)
    except Exception as exc:                              # noqa: BLE001
        if path not in _WARNED_PATHS:
            _WARNED_PATHS.add(path)
            print(f"Warning: could not write the Xopt resume state to '{path}' "
                  f"({type(exc).__name__}: {exc}). The optimization continues; it "
                  "just will not be resumable. Reported once per file.")
        return None
    return path


def set_aside(paths):
    """Move each existing path in ``paths`` out of the way, returning what moved as
    ``[(original, moved_to), …]``.

    Used when a resume is **refused**: the run is about to start a fresh campaign in
    the same place and would otherwise overwrite the trajectory and state the user
    was explicitly asking to continue. Losing hours of solves to a moved variable
    bound is not an acceptable outcome of a *correct* refusal, so they are renamed
    to ``<name>.rejected`` — and to ``.rejected.1``, ``.rejected.2``, … rather than
    over an existing one, so a second refusal does not finish the job the first was
    prevented from doing."""
    moved = []
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        target = path + REJECTED_SUFFIX
        suffix = 0
        while os.path.exists(target):
            suffix += 1
            target = f'{path}{REJECTED_SUFFIX}.{suffix}'
        try:
            os.replace(path, target)
        except OSError:
            continue
        moved.append((path, target))
    return moved


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def read_xopt_state(path):
    """The state mapping at ``path``, or ``None`` when there is none to use.

    ``None`` covers absent, unreadable, not-a-mapping, and **truncated** — a dump
    cut off mid-write often still parses as YAML, just without the keys that make it
    a state, so the structural check is what catches it rather than the parse. Every
    one of those degrades to "no recorded state", i.e. start over: the safe
    direction, since the alternative is continuing a campaign from a file we could
    not understand."""
    if not path or not os.path.isfile(path):
        return None
    import yaml
    try:
        with open(path) as file:
            state = yaml.safe_load(file)
    except (OSError, ValueError, yaml.YAMLError):
        return None
    if not isinstance(state, dict):
        return None
    generator = state.get('generator')
    if not isinstance(state.get('evaluator'), dict) \
            or not isinstance(generator, dict) or not generator.get('name'):
        print(f"Warning: the Xopt resume state at '{path}' is incomplete "
              "(probably a run killed mid-write); starting the optimization from "
              "scratch.")
        return None
    return state


def evaluation_count(state):
    """How many evaluations ``state`` records.

    ``X.dump`` writes the data table column-oriented (``{column: {row: value}}``),
    so the count is the longest column rather than ``len``."""
    data = (state or {}).get('data') or {}
    if not isinstance(data, dict) or not data:
        return 0
    return max((len(column) for column in data.values()
                if hasattr(column, '__len__')), default=0)


def generator_name(state):
    """The name of the generator ``state`` was written by, or ``''``."""
    return str(((state or {}).get('generator') or {}).get('name') or '')


def _recorded_vocs(state):
    """The VOCS ``state`` records, as a :class:`~xopt.vocs.VOCS`, or ``None``.

    It lives under ``generator.vocs`` — ``Xopt`` no longer carries a top-level
    ``vocs`` — and is what the generator was actually built with, so for
    ``MultiFidelityGenerator`` it already includes the fidelity axis ``s`` its
    validator adds.

    ⚠️ Validated against a **deep copy**. ``VOCS.model_validate`` consumes the
    ``type`` discriminator out of the mapping it is handed, in place, so validating
    the state's own dict would leave it unvalidatable — and every caller here reads
    the state more than once (``--status`` asks for the objectives and then the best
    point; :func:`restore_xopt` compares and then restores)."""
    from xopt.vocs import VOCS
    recorded = ((state or {}).get('generator') or {}).get('vocs')
    if not isinstance(recorded, dict):
        return None
    try:
        return VOCS.model_validate(copy.deepcopy(recorded))
    except Exception:                                     # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Restoring
# --------------------------------------------------------------------------- #


def recorded_config_hash(state):
    """The campaign hash ``state`` records, or ``None`` when it records none (a state
    written before this was recorded, or by a caller with no workflow to hash)."""
    block = (state or {}).get(LUME_KEY)
    if not isinstance(block, dict):
        return None
    return block.get(CONFIG_HASH_KEY) or None


def restore_xopt(state, sim_function, generator=None, config_hash=None):
    """Reconstruct an :class:`xopt.Xopt` from ``state`` around the live
    ``sim_function``, or ``None`` when ``state`` cannot be used.

    ``generator`` is the generator the *current* configuration builds; it is used
    only to check that the state agrees with the config, and is discarded in favor
    of the restored one on success (the whole point being that the restored
    generator carries state the fresh one does not). ``config_hash`` is the current
    campaign's resolved-configuration hash, compared against the recorded one.

    Returns ``None`` rather than raising for every refusal, after saying which, so a
    run whose state cannot be used starts fresh instead of dying:

    * a different generator — a Nelder-Mead simplex is not an Expected-Improvement
      GP, and neither can continue the other's search;
    * a different VOCS — resuming a ``MINIMIZE`` campaign into a ``MAXIMIZE`` config
      would optimize *against* the inherited data while looking like it was working,
      and moved variable bounds mean the inherited points may be outside the box now
      being searched;
    * a different **workflow** — the check the VOCS one cannot make. Same variables
      and same objective over a different mesh, solver input or extraction spec is a
      different campaign, and the inherited evaluations describe a different model;
    * a validation failure — a state from an incompatible xopt, or a corrupted one
      that still parsed."""
    if not state:
        return None
    from xopt import Xopt

    recorded_vocs = _recorded_vocs(state)
    recorded_hash = recorded_config_hash(state)
    if config_hash and recorded_hash and recorded_hash != config_hash:
        print("Warning: the Xopt resume state was written for a different workflow "
              "configuration than this config declares — the module chain, the "
              "'output_parameters' spec or a fixed (non-optimized) input value "
              "changed, so the recorded evaluations describe a different model. "
              "Starting from scratch.\n"
              f"    recorded: {recorded_hash}\n"
              f"    config:   {config_hash}")
        return None
    if config_hash and not recorded_hash:
        print("Note: the Xopt resume state records no configuration hash, so its "
              "workflow could not be checked against this config. Resuming anyway; "
              "confirm the module chain and 'output_parameters' have not changed.")
    if generator is not None:
        recorded_name = generator_name(state)
        if recorded_name and recorded_name != getattr(generator, 'name', None):
            print(f"Warning: the Xopt resume state was written by the "
                  f"'{recorded_name}' generator but this config asks for "
                  f"'{getattr(generator, 'name', None)}'; the two cannot continue "
                  "each other's search. Starting from scratch.")
            return None
        if recorded_vocs is not None and recorded_vocs != generator.vocs:
            print("Warning: the Xopt resume state was written for a different "
                  "problem than this config declares, so continuing it would "
                  "optimize against the recorded data. Starting from scratch.\n"
                  f"    recorded: {_vocs_summary(recorded_vocs)}\n"
                  f"    config:   {_vocs_summary(generator.vocs)}")
            return None

    # Xopt's validators consume keys out of the mappings they are handed (the
    # generator's ``name``, VOCS's ``type`` discriminators), so it gets copies and
    # the caller's ``state`` — which ``--status`` and the iteration counter also
    # read — is left intact.
    restored = {key: value for key, value in state.items() if key != LUME_KEY}
    restored['evaluator'] = {**(state.get('evaluator') or {}),
                             'function': sim_function}
    if recorded_vocs is not None:
        # Pre-validated because MultiFidelityGenerator's own vocs validator reaches
        # for VOCS attributes on whatever it is handed (xopt 3.0.0), so a raw
        # mapping raises there. Every other generator accepts either.
        restored['generator'] = {**copy.deepcopy(state.get('generator') or {}),
                                 'vocs': recorded_vocs}
    try:
        X = Xopt(**restored)
    except Exception as exc:                              # noqa: BLE001
        print(f"Warning: the Xopt resume state could not be loaded "
              f"({type(exc).__name__}: {exc}); starting the optimization from "
              "scratch.")
        return None
    return X


def _vocs_summary(vocs):
    """A one-line rendering of a VOCS's variables + objectives, for the mismatch
    message — enough to see *what* disagreed without printing the whole model."""
    variables = ', '.join(
        f'{name}={list(getattr(spec, "domain", spec))}'
        for name, spec in sorted((vocs.variables or {}).items()))
    objectives = ', '.join(f'{name}={type(spec).__name__}'
                           for name, spec in sorted((vocs.objectives or {}).items()))
    return f'variables [{variables}] objectives [{objectives}]'


# --------------------------------------------------------------------------- #
# Reporting (--status)
# --------------------------------------------------------------------------- #


# MultiFidelityGenerator adds the fidelity axis as an objective named 's' (it
# maximizes fidelity subject to the cost budget). It is machinery rather than
# something a user asked to optimize, so it is not what --status should report as
# "the best point" — the run's own objective is.
FIDELITY_OBJECTIVE = 's'


def objective_names(state):
    """The objective names ``state`` records, in a stable order, with the fidelity
    axis dropped whenever a real objective is present.

    Observables are deliberately *not* included: they are tracked, not optimized, so
    "the best point" is not a question about them."""
    vocs = _recorded_vocs(state)
    if vocs is None:
        return []
    names = sorted(vocs.objectives or {})
    return [name for name in names if name != FIDELITY_OBJECTIVE] or names


def best_point(state, objective=None):
    """The best evaluation ``state`` records, as
    ``(objective_name, value, {variable: value})`` — or ``None`` when there is
    nothing to report.

    ``objective`` picks which objective to rank by, defaulting to the first the
    state records. The *direction* is read from the state's own VOCS rather than
    passed in, so ``--status`` reports the campaign the way it was actually run;
    a config that has since flipped ``MINIMIZE`` to ``MAXIMIZE`` is a mismatch
    :func:`restore_xopt` refuses, not something to silently re-rank here.

    Rows whose value is missing or NaN are skipped — a failed evaluation is
    recorded with the rest, and it is not the best point."""
    data = (state or {}).get('data') or {}
    names = objective_names(state)
    objective = objective or (names[0] if names else None)
    column = data.get(objective) if objective else None
    if not isinstance(column, dict) or not column:
        return None

    vocs = _recorded_vocs(state)
    spec = (vocs.objectives or {}).get(objective) if vocs is not None else None
    maximize = 'maximize' in type(spec).__name__.lower() if spec is not None \
        else False

    finite = {key: float(value) for key, value in column.items()
              if isinstance(value, (int, float)) and float(value) == float(value)}
    if not finite:
        return None
    best_key = (max if maximize else min)(finite, key=finite.get)

    variables = {} if vocs is None else {
        name: data[name][best_key]
        for name in sorted(vocs.variables or {})
        if isinstance(data.get(name), dict) and best_key in data[name]}
    return objective, finite[best_key], variables
