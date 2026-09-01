"""Per-evaluation subprocess logs (Phase 2 of
``plans/evaluation_isolation_resume_plan.md``).

Every Cubit / ACE3P / acdtool / Geant4 invocation used to run with the parent's
stdout and stderr *inherited*, so a sweep of N points produced one interleaved
stream on the terminal and nothing on disk: point 7's solver output was
indistinguishable from point 8's, and once the terminal scrolled it was gone.
That is fine for a serial sweep watched live and useless for anything else — a
batch job, a resumed campaign, or (later) concurrent evaluations.

:func:`run_logged` gives each invocation a log file under the evaluation's own
workdir *without taking anything away from the terminal*: output is **teed, not
redirected**. stdout and stderr each stay on their own stream (a caller
redirecting ``2>errors`` keeps working) and both are interleaved into the one
log, line by line as they arrive. That is the whole reason this is a tee rather
than the redirect the plan first considered: a solver failure message must not
become invisible, and a multi-hour solve must not go silent until it exits.

``workflow_parameters: {capture_output: false}`` turns it off, restoring the
plain inherited-stream behavior exactly.
"""

import os
import re
import subprocess
import sys
import threading


LOG_SUFFIX = '.log'

# A module's instance name comes from the YAML ``name:`` key, so it is not
# guaranteed to be a safe filename component. Anything outside this set is
# replaced rather than rejected: a name carrying a path separator would
# otherwise write the log outside the very workdir it is supposed to describe.
_UNSAFE = re.compile(r'[^A-Za-z0-9._-]')


def log_path(workdir, name):
    """``<workdir>/<name>.log`` — where a module's subprocess output is teed, or
    ``None`` when there is no workdir to put it in."""
    if not workdir:
        return None
    return os.path.join(workdir, _UNSAFE.sub('_', str(name)) + LOG_SUFFIX)


def run_logged(command, cwd=None, log_file=None, tee=True):
    """Run ``command`` through the shell, teeing its output to ``log_file``.

    With ``log_file`` unset this is exactly
    ``subprocess.run(command, shell=True, cwd=cwd)`` — streams inherited,
    nothing captured. That is both the pre-Phase-2 behavior and what
    ``capture_output: false`` selects, so the fallback is a real fallback and not
    an approximation of one.

    With a log file, stdout and stderr are piped, appended to the log as they
    arrive, and re-emitted on the parent's matching stream. **Appending** rather
    than truncating is deliberate: one module may launch more than one
    subprocess (``cubit`` runs the mesher and then ``acdtool meshconvert``), and
    under ``workdir_mode: manual`` every sweep point shares one workdir, so
    truncating would keep only the last invocation. Each invocation writes a
    ``$ <command>`` header line so a multi-invocation log stays readable.

    Returns the :class:`subprocess.CompletedProcess`. The exit status is
    deliberately **not** checked, matching the wrappers' long-standing behavior:
    an ACE3P failure surfaces when the output parser finds no results, and the
    tool's own message is by then in the log *and* on the terminal.
    """
    if not log_file:
        return subprocess.run(command, shell=True, cwd=cwd)
    parent = os.path.dirname(os.path.abspath(log_file))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    # stdout and stderr are pumped concurrently into one file, so every write to
    # it is serialized.
    lock = threading.Lock()
    with open(log_file, 'a', errors='replace') as log:
        log.write('$ ' + str(command) + '\n')
        log.flush()
        process = subprocess.Popen(command, shell=True, cwd=cwd,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   text=True, errors='replace', bufsize=1)
        pumps = [threading.Thread(target=_pump, args=(stream, log, lock, sink),
                                  daemon=True)
                 for stream, sink in ((process.stdout, sys.stdout if tee else None),
                                      (process.stderr, sys.stderr if tee else None))]
        for pump in pumps:
            pump.start()
        returncode = process.wait()
        # Join before leaving the ``with``: the pumps write to ``log``.
        for pump in pumps:
            pump.join()
    return subprocess.CompletedProcess(command, returncode)


def _pump(stream, log, lock, sink):
    """Forward one of the child's streams line by line — into the shared ``log``
    (under ``lock``) and back out on ``sink``, the parent's matching stream.

    Flushed per line on both sides so a long solver run stays live on the
    terminal and its log is readable while it is still running, which is what a
    user watching a wall-clock-limited job actually needs."""
    try:
        for line in stream:
            with lock:
                log.write(line)
                log.flush()
            if sink is not None:
                sink.write(line)
                sink.flush()
    finally:
        stream.close()
