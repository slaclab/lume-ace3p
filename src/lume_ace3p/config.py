"""Configuration-shape checks — telling a user about keys nothing reads.

Nothing in the pipeline used to compare a config's keys against the set it actually
consumes, so a near-miss was silent: ``num_steps`` for ``num_step`` produced a run
with no termination criterion, a ``resume:`` misplaced into a ``train_surrogate``
block did nothing, ``sweep_output`` for ``sweep_output_file`` wrote the posterior-mean
table to the default path. Each of those is a config that *looks* right and a run that
quietly is not what was asked for.

:func:`warn_unrecognized` is the one check, applied to the blocks whose key sets are
**closed** — the top-level blocks, ``mode:``, ``workflow_parameters``,
``vocs_parameters`` and ``xopt_parameters``. It deliberately does not touch
``input_parameters`` / ``output_parameters``, whose keys are the user's own variable
and output names.

Three properties it holds to:

* **It warns, it never raises.** A config with a harmless extra key runs today, and
  the goal is to catch typos rather than to police a schema. Failing would break
  working setups for no safety gain.
* **The recognized set lives with the code that reads it,** not in a central table
  here. This module supplies the comparison; each caller supplies its own set, next to
  where the keys are consumed, so the two cannot drift apart silently.
* **It suggests the near miss.** The whole class of bug is a typo, so
  :func:`difflib.get_close_matches` earns its place: naming ``num_step`` next to
  ``num_steps`` is the difference between a useful warning and one more line of
  output.
"""

import difflib


def warn_unrecognized(block, config, recognized):
    """Warn about the keys of ``config`` that nothing in the pipeline reads, and
    return them (sorted).

    ``block`` names the block for the message (``"'xopt_parameters'"``,
    ``"mode 'train_surrogate'"``), ``recognized`` is the set of keys the code
    consuming it actually reads. A non-mapping ``config`` — an absent block, or one
    given the wrong shape — is not this check's business and returns ``[]``; the code
    that reads it will complain in terms of what it wanted."""
    if not isinstance(config, dict):
        return []
    known = sorted(str(key) for key in recognized)
    unrecognized = sorted(str(key) for key in config if str(key) not in known)
    if not unrecognized:
        return []

    described = []
    for key in unrecognized:
        close = difflib.get_close_matches(key, known, n=1, cutoff=0.75)
        described.append(f"'{key}'" + (f" (did you mean '{close[0]}'?)"
                                       if close else ''))
    plural = 's' if len(unrecognized) > 1 else ''
    print(f"Warning: {block} has key{plural} that nothing reads: "
          + ', '.join(described) + f". Ignored. Recognized here: {', '.join(known)}.")
    return unrecognized
