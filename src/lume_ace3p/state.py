"""The per-evaluation completion manifest (Phase 3 of
``plans/evaluation_isolation_resume_plan.md``).

Every evaluation writes one JSON file, ``lume_ace3p_state.json``, into its own
workdir, recording which modules ran, what each produced, and the extracted
outputs. It is written **incrementally — after each module, not once at the
end** — because the partial file is the whole point: a sweep point cut off by
the wall clock is exactly the case that needs a record of how far it got.

Phase 3 only *writes* it; Phase 4 reads it to resume. Nothing about the run
changes because it exists.

Why a manifest rather than a file-presence check
------------------------------------------------
Presence of an output file cannot decide completion in this package, and the
reason is specific to it: ``acdtool postprocess transwake`` writes its result
*over* ``<jobname>/OUTPUT/wakefield.out`` — the file :class:`T3PModule` already
wrote and parsed (:attr:`lume_ace3p.acdtool.Command.mutates`, and defect 7 of
``plans/acdtool_rework_plan.md``). A presence check would find
``wakefield.out``, declare the acdtool step complete, skip it, and report T3P's
**longitudinal** wake as a kick factor. Only a record that acdtool *ran*
distinguishes the two states, which is why :meth:`Module.verify` deliberately
returns "unknown" for a mutating command rather than checking for its output.

What ``config_hash`` covers
---------------------------
The **resolved per-point configuration**: the module entries (type + config
mapping), the materialized input point, and the ``output_parameters`` spec.
Deliberately excluded — none of these is passed in, so the exclusion is
structural rather than a filter that can rot:

* ``paths`` and the executable environment: site-specific, and the same workdir
  must be resumable on a different machine,
* ``dry_run`` and ``workdir`` / ``workdir_mode``: they say how and where to run,
  not what the answer is,
* YAML comments and key order: the hash is taken over parsed values, so
  reformatting a config does not invalidate a campaign.

A changed hash means the point must be re-run, and Phase 4 says so by name.

The manifest is **not a baseline artifact** — it carries timestamps and absolute
paths, so ``tests/baseline_utils.py`` excludes it explicitly.
"""

import datetime
import hashlib
import json
import os


# Bump only for a change a reader cannot tolerate; :func:`read_state` ignores a
# manifest of any other schema, which degrades to "this point has no state" —
# i.e. re-run it — rather than to a misread.
SCHEMA = 1

STATE_FILE = 'lume_ace3p_state.json'

# The per-module status values. A module absent from the list has not been
# attempted (or was interrupted before it recorded anything), which is
# deliberately distinct from ``FAILED``.
COMPLETE = 'complete'
FAILED = 'failed'


def state_path(workdir):
    """``<workdir>/lume_ace3p_state.json``, or ``None`` when there is no
    workdir to put it in."""
    if not workdir:
        return None
    return os.path.join(workdir, STATE_FILE)


# --------------------------------------------------------------------------- #
# Building and updating a manifest
# --------------------------------------------------------------------------- #


def new_state(config_hash=None, point=None, workdir=None):
    """A fresh manifest for one evaluation, with no modules recorded yet.

    Written before the first module runs, so a point killed inside module 0
    still leaves its identity and its ``config_hash`` behind. ``point`` is
    rendered JSON-writable here, so a caller may hand it the numpy scalars a
    materialized input point is actually made of."""
    stamp = _now()
    return {'schema': SCHEMA,
            'config_hash': config_hash,
            'point': _jsonable(point) if point else {},
            'workdir': os.path.abspath(workdir) if workdir else None,
            'started': stamp,
            'updated': stamp,
            'modules': [],
            'outputs': {}}


def record_module(state, module, status, **extra):
    """Record one module's outcome in ``state``.

    ``extra`` carries whatever the module produced that is worth writing down —
    ``artifacts={kind: path}``, ``job_name='omega3p_results'``, ``error=...`` —
    and empty values are dropped rather than written as nulls.

    An entry for a module of the same **name** is *replaced*, not appended, so a
    resumed run that re-runs a previously failed step leaves one record of it
    rather than a history. First writes keep DAG order, which is what makes the
    list readable as the chain it is.

    Module names are taken to be unique within a chain — the same assumption
    :meth:`lume_ace3p.modules.Module.log_file` already makes, since two modules of
    one type get separate logs only by having separate ``name:`` keys. Two
    identically-named modules would share one entry here, as they already share one
    log."""
    entry = {'name': module.name, 'type': module.type, 'status': status}
    for key, value in extra.items():
        if value is None or (isinstance(value, (dict, list, tuple, str))
                             and len(value) == 0):
            continue
        entry[key] = _jsonable(value)
    for index, existing in enumerate(state['modules']):
        if existing.get('name') == entry['name']:
            state['modules'][index] = entry
            return
    state['modules'].append(entry)


def record_outputs(state, outputs):
    """Record the extracted ``output_parameters`` of the evaluation.

    Array-valued outputs (an S3P spectrum, a per-mode acdtool array) are stored
    as lists; ``NaN`` is written as-is, which Python's ``json`` reads back but a
    strict JSON parser does not accept. That is the right trade here — a dry-run
    or failed extraction genuinely produced NaN, and rewriting it as ``null``
    would make it indistinguishable from an output that was never asked for."""
    state['outputs'] = {str(name): _jsonable(value)
                        for name, value in (outputs or {}).items()}


def module_entry(state, name):
    """The recorded entry for the module called ``name``, or ``None``."""
    for entry in (state or {}).get('modules') or []:
        if entry.get('name') == name:
            return entry
    return None


# --------------------------------------------------------------------------- #
# Reading and writing
# --------------------------------------------------------------------------- #


def write_state(workdir, state):
    """Write ``state`` to ``<workdir>/lume_ace3p_state.json``, atomically.

    Called after **every** module, so an interrupted write is a real
    possibility — and a truncated manifest is precisely what a resume would
    read. The file is written beside its target and then :func:`os.replace`\\ d,
    so a reader sees either the previous manifest or the new one.

    A falsy ``workdir`` is a no-op: a caller driving a chain with no directory
    of its own has nowhere to keep a manifest, and that is not an error."""
    path = state_path(workdir)
    if path is None:
        return None
    state['updated'] = _now()
    os.makedirs(workdir, exist_ok=True)
    temporary = path + '.tmp'
    with open(temporary, 'w') as file:
        json.dump(state, file, indent=2, sort_keys=True)
        file.write('\n')
    os.replace(temporary, path)
    return path


def read_state(workdir):
    """The manifest in ``workdir``, or ``None`` when there is none to read.

    ``None`` also covers an unreadable or malformed file and a manifest written
    by a different :data:`SCHEMA`. Every one of those degrades to "this point
    has no recorded state", which means *run it* — the safe direction, since the
    alternative is skipping work on the strength of a file we could not
    understand."""
    path = state_path(workdir)
    if path is None or not os.path.isfile(path):
        return None
    try:
        with open(path) as file:
            state = json.load(file)
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get('schema') != SCHEMA:
        return None
    return state


# --------------------------------------------------------------------------- #
# The configuration hash
# --------------------------------------------------------------------------- #


def config_hash(entries, inputs, output_spec):
    """``'sha256:<hex>'`` over the resolved per-point configuration.

    ``entries`` is the ``workflow:`` list (each entry's ``module`` type plus its
    config mapping), ``inputs`` the **materialized** :class:`WorkflowInputs` for
    this point, and ``output_spec`` the ``output_parameters`` mapping. See the
    module docstring for what is deliberately *not* in here and why.

    Mappings are hashed order-insensitively (keys are sorted) while sequences
    keep their order — so re-ordering the keys of a module entry is not a change
    but re-ordering the ``workflow:`` list is, and an ACE3P ``Section``'s entry
    order, which the input file's semantics depend on, is preserved."""
    payload = {'entries': _canonical(entries),
               'inputs': _canonical(inputs),
               'output_parameters': _canonical(output_spec)}
    text = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


def _canonical(value):
    """A JSON-serializable, comparison-stable rendering of a config value.

    Deliberately duck-typed rather than importing the types it renders: an
    object with a ``__dict__`` (a :class:`~lume_ace3p.inputs.WorkflowInputs`, an
    :class:`~lume_ace3p.ace3p.Section`) is rendered from its attributes, keyed by
    its class name so two different shapes cannot collide. That keeps this module
    free of a dependency on the input model — and means a new field on either
    class is picked up here without a change, which is the safe default for a
    hash whose job is to notice that something moved."""
    if value is None or isinstance(value, (bool, int, float, str)):
        # bool/int/float subclasses (numpy scalars, ruamel's ScalarFloat) land
        # here and serialize as their value.
        return value
    if hasattr(value, 'tolist'):                 # numpy array / scalar
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonical(item) for item in value]
        # A set has no order to preserve, so give it one that is reproducible.
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) \
            else items
    if hasattr(value, '__dict__'):
        return {type(value).__name__: _canonical(vars(value))}
    return str(value)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def relative(path, workdir):
    """``path`` relative to ``workdir`` when it is inside it, else unchanged.

    Artifact paths are recorded workdir-relative so a manifest describes the
    directory it sits in rather than the machine that produced it — the same
    reason ``paths`` is out of :func:`config_hash`."""
    if not path or not workdir:
        return path
    try:
        rel = os.path.relpath(path, workdir)
    except ValueError:                    # different drives (Windows)
        return path
    return path if rel.split(os.sep)[0] == os.pardir else rel


def _jsonable(value):
    """A JSON-writable rendering of a recorded value (numpy scalars unwrap,
    arrays become lists, anything else falls back to its string form)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, 'tolist'):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return str(value)


def _now():
    """A local wall-clock stamp, seconds resolution. Manifests carry these,
    which is one of the two reasons they are not baseline artifacts."""
    return datetime.datetime.now().isoformat(timespec='seconds')
