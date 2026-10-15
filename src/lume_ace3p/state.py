"""The per-evaluation completion manifest (Phases 3-4 of
``plans/evaluation_isolation_resume_plan.md``).

Every evaluation writes one JSON file, ``lume_ace3p_state.json``, into its own
workdir, recording which modules ran, what each produced, and the extracted
outputs. It is written **incrementally — after each module, not once at the
end** — because the partial file is the whole point: a sweep point cut off by
the wall clock is exactly the case that needs a record of how far it got.

Phase 3 wrote it and read nothing back. Phase 4 reads it: under
``mode: {resume: true}`` :meth:`lume_ace3p.workflow_graph.Workflow.evaluate`
resumes a point from the first module this file does not record as complete, and
``run-lume-ace3p --status`` summarizes a half-finished campaign from these files
alone (:func:`point_status`).

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
mapping), the materialized input point, the ``output_parameters`` spec and,
when one is declared, the ``derived_parameters`` block (a ``python:`` callable by
its source text).
Deliberately excluded — none of these is passed in, so the exclusion is
structural rather than a filter that can rot:

* ``paths`` and the executable environment: site-specific, and the same workdir
  must be resumable on a different machine,
* ``dry_run`` and ``workdir`` / ``workdir_mode``: they say how and where to run,
  not what the answer is,
* YAML comments and key order: the hash is taken over parsed values, so
  reformatting a config does not invalidate a campaign.

``config_hash`` identifies the point. What a resume reuses is decided per module
by the module ``hash`` beside each entry.

Per-module hashes
-----------------
Each module entry records ``hash`` (:func:`module_hash`), over:

1. the module's entry (type + config mapping) without its launch keys —
   ``tasks`` / ``cores`` / ``opts`` and the like, which size the run but do not
   change its answer,
2. the input buckets the module reads (``cubit`` for Cubit, ``ace3p`` for a
   solver, ...), with its own scoped block laid over them,
3. the contents of its input files — the journal, a solver's input deck, the
   ``.rfpost``, the Geant4 input. Data files (a supplied mesh or Track3P dump, a
   solver's ``files:``, Geant4 geometry) count by path only: they can be
   hundreds of MB, and are not hashed,
4. the hash of each upstream module it reads from.

(4) makes it a chain: a changed journal changes the Cubit hash and with it every
hash downstream. ``output_parameters`` and ``derived_parameters`` are in no
module hash, so changing what is extracted re-runs only the parsers.

A manifest without module hashes (written by 0.7.x) resumes by the older rule:
the whole point is reused only when its ``config_hash`` matches.

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

# The per-*point* verdicts :func:`point_status` reports, on top of the two above.
# ``COMPLETE`` and ``FAILED`` double as point verdicts: a point is complete when
# every module in its chain is, and failed when any module is.
ABSENT = 'absent'      # no manifest at all — the point has not been started
STALE = 'stale'        # a manifest for a *different* configuration
PARTIAL = 'partial'    # some modules complete, none failed, the rest unattempted


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
    ``artifacts={kind: path}``, ``job_name='omega3p_results'``, ``error=...``,
    the module ``hash=`` (:func:`module_hash`) — and empty values are dropped
    rather than written as nulls.

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
    """The recorded entry for the module called ``name``, or ``None``.

    Keyed on the module's **name**, which a chain is required to make unique
    (:func:`lume_ace3p.workflow_graph._resolve_order` rejects a duplicate) —
    otherwise "resume from the first non-complete module" could not tell two
    steps apart, and they would already be sharing one log."""
    for entry in (state or {}).get('modules') or []:
        if entry.get('name') == name:
            return entry
    return None


def is_complete(state, name):
    """Whether ``state`` records the module called ``name`` as complete.

    The manifest is authoritative for "did it run" and the module for "is the
    output still there" (:meth:`lume_ace3p.modules.Module.verify`) — design
    decision 2. Absent and ``failed`` are both "not complete" here; they differ
    only in what is worth reporting to a user."""
    entry = module_entry(state, name)
    return entry is not None and entry.get('status') == COMPLETE


def recorded_output(entry):
    """The clearest description a completed module's entry gives of what it left
    behind: a solver's results directory, else the artifacts it recorded.

    Used to name what is missing when a module is recorded complete but
    :meth:`~lume_ace3p.modules.Module.verify` says its output is gone. Returns
    ``''`` when the entry describes nothing locatable — a solver whose only
    artifact is the workdir itself records ``'.'``, which says nothing."""
    entry = entry or {}
    if entry.get('job_name'):
        return str(entry['job_name'])
    paths = sorted(str(path) for path in (entry.get('artifacts') or {}).values()
                   if path not in ('.', '', None))
    return ', '.join(paths)


def has_module_hashes(state):
    """Whether ``state`` records per-module hashes — every manifest this version
    writes does; one written by 0.7.x does not, and resumes by ``config_hash``."""
    return any('hash' in entry for entry in (state or {}).get('modules') or [])


def hash_matches(state, name, current):
    """Whether the module called ``name`` was recorded with hash ``current``.

    ``True`` when there is nothing to compare — no current hash was given, or
    the entry predates module hashes (its point was already matched on
    ``config_hash``)."""
    recorded = (module_entry(state, name) or {}).get('hash')
    return current is None or recorded is None or recorded == current


def point_status(state, config_hash=None, module_names=(), module_hashes=None):
    """Summarize one point's manifest as ``(verdict, complete, pending)``.

    ``pending`` is the first module of ``module_names`` a resume would run: the
    first not recorded complete, or recorded under a different module hash than
    ``module_hashes`` (``{name: hash}`` for the current configuration). ``None``
    when there is none. ``complete`` counts the modules before it — the ones a
    resume reuses.

    ``verdict`` is :data:`ABSENT` (nothing recorded); :data:`STALE` (the first
    module is recorded complete for a different configuration, so nothing is
    reused); :data:`FAILED` (some module recorded a failure, or the point
    recorded an ``error`` and no module would re-run — its modules completed but
    extracting its outputs did not, and what needs fixing is the configuration or
    the files); :data:`COMPLETE`; or :data:`PARTIAL`.

    A manifest without module hashes is judged by ``config_hash`` alone, as
    before module hashes existed: a mismatch is :data:`STALE`. ``config_hash`` is
    the hash the *current* configuration produces for this point; pass ``None``
    (and no ``module_hashes``) to skip the comparisons. This is what
    ``run-lume-ace3p --status`` reports and what makes a half-finished campaign
    legible without running anything."""
    names = list(module_names)
    if state is None:
        return ABSENT, 0, (names[0] if names else None)
    hashed = has_module_hashes(state)
    if (not hashed and config_hash is not None
            and state.get('config_hash') != config_hash):
        return STALE, 0, (names[0] if names else None)
    hashes = (module_hashes or {}) if hashed else {}

    def reusable(name):
        return (is_complete(state, name)
                and hash_matches(state, name, hashes.get(name)))

    pending = next((name for name in names if not reusable(name)), None)
    complete = names.index(pending) if pending is not None else len(names)
    if pending is not None and pending == names[0] and is_complete(state, pending):
        return STALE, 0, pending
    failed = any(entry.get('status') == FAILED
                 for entry in state.get('modules') or [])
    if failed:
        return FAILED, complete, pending
    if state.get('error') and pending is None:
        # Extraction failed. Under the same configuration it will again; under
        # another (an edited output_parameters) a resume re-extracts and
        # launches nothing.
        if config_hash is None or state.get('config_hash') == config_hash:
            return FAILED, complete, None
        return PARTIAL, complete, None
    if pending is None and names:
        return COMPLETE, complete, None
    return PARTIAL, complete, pending


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


def config_hash(entries, inputs, output_spec, derived=None):
    """``'sha256:<hex>'`` over the resolved per-point configuration.

    ``entries`` is the ``workflow:`` list (each entry's ``module`` type plus its
    config mapping), ``inputs`` the **materialized** :class:`WorkflowInputs` for
    this point, ``output_spec`` the ``output_parameters`` mapping, and
    ``derived`` the ``derived_parameters`` block as
    :meth:`~lume_ace3p.derived.DerivedSpec.canonical` renders it. ``derived`` is
    left out of the payload when ``None``, so a config without the block hashes
    exactly as it did before the block existed. See the module docstring for
    what is deliberately *not* in here and why.

    Mappings are hashed order-insensitively (keys are sorted) while sequences
    keep their order — so re-ordering the keys of a module entry is not a change
    but re-ordering the ``workflow:`` list is, and an ACE3P ``Section``'s entry
    order, which the input file's semantics depend on, is preserved."""
    payload = {'entries': _canonical(entries),
               'inputs': _canonical(inputs),
               'output_parameters': _canonical(output_spec)}
    if derived is not None:
        payload['derived'] = _canonical(derived)
    text = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


def campaign_hash(entries, inputs, output_spec, derived=None):
    """``'sha256:<hex>'`` over a *campaign's* resolved configuration.

    Identical machinery to :func:`config_hash` — a different name because it answers
    a different question, and mixing the two would let a campaign hash be compared
    against a point hash. A sweep point is identified by its own input values, so
    :func:`config_hash` includes them. An optimization has no fixed point: the
    generator chooses each one, so what must not change mid-campaign is everything
    *else* — the module chain, the extraction spec, and the input leaves the
    optimizer is **not** driving.

    The caller is what makes that last distinction, by materializing the optimizer's
    own variables to a fixed value before hashing (see
    :meth:`~lume_ace3p.workflow_graph.Workflow.campaign_config_hash`). So editing a
    variable's nominal starting value does not invalidate a campaign, while editing a
    fixed input that the physics depends on does."""
    return config_hash(entries, inputs, output_spec, derived)


def module_hash(entry, inputs, files, upstream):
    """``'sha256:<hex>'`` for one module of a chain — see "Per-module hashes" in
    the module docstring.

    ``entry`` is the module's type and config mapping with its launch keys
    already removed, ``inputs`` ``{bucket: value}`` for the buckets it reads,
    ``files`` ``{path: digest}`` for its input files (:func:`file_digest`), and
    ``upstream`` ``{kind: hash}`` for the producers it is bound to."""
    payload = {'entry': _canonical(entry),
               'inputs': _canonical(inputs),
               'files': _canonical(files),
               'upstream': _canonical(upstream)}
    text = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


# (absolute path, size, mtime_ns) -> digest, so a sweep reads each input file
# once per process rather than once per point.
_DIGESTS = {}


def file_digest(path):
    """``'sha256:<hex>'`` of the file's bytes, or ``None`` when it cannot be
    read (a missing input is run time's error to report, not the hash's).

    Cached on ``(path, size, mtime)``: an edit moves the mtime and is re-read,
    while the digest itself is over contents, so a ``touch`` or a copy that
    preserves the bytes does not change it."""
    try:
        info = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    key = (os.path.abspath(path), info.st_size, info.st_mtime_ns)
    if key not in _DIGESTS:
        digest = hashlib.sha256()
        try:
            with open(path, 'rb') as file:
                for block in iter(lambda: file.read(1 << 20), b''):
                    digest.update(block)
        except OSError:
            return None
        _DIGESTS[key] = 'sha256:' + digest.hexdigest()
    return _DIGESTS[key]


def _canonical(value):
    """A JSON-serializable, comparison-stable rendering of a config value.

    Deliberately duck-typed rather than importing the types it renders: an
    object with a ``__dict__`` (a :class:`~lume_ace3p.inputs.WorkflowInputs`, an
    :class:`~lume_ace3p.ace3p.Section`) is rendered from its attributes, keyed by
    its class name so two different shapes cannot collide; one with a
    ``canonical()`` method is rendered from what that returns. That keeps this module
    free of a dependency on the input model — and means a new field on either
    class is picked up here without a change, which is the safe default for a
    hash whose job is to notice that something moved."""
    if value is None or isinstance(value, (bool, int, float, str)):
        # bool/int/float subclasses (numpy scalars, ruamel's ScalarFloat) land
        # here and serialize as their value.
        return value
    if hasattr(value, 'tolist'):                 # numpy array / scalar
        return value.tolist()
    if callable(getattr(value, 'canonical', None)):
        # An object that chooses its own rendering — WorkflowInputs, so a field
        # added for a new feature can stay out of the hash of a config that does
        # not use it.
        return _canonical(value.canonical())
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
