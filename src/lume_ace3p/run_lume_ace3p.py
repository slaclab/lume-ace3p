import sys

from lume_ace3p import __version__
from lume_ace3p.config import warn_unrecognized
from lume_ace3p.inputs import TOP_LEVEL_KEYS, load_yaml
from lume_ace3p.workflow_graph import Workflow
from lume_ace3p.modes import (
    is_store_consuming, mode_type_of, run_mode, status, xopt_status,
)


# The modes ``--status`` can report on — the ones resume applies to. The two kinds
# report differently (see :func:`_report_status`), because their campaigns have
# different notions of progress: a table mode's points are a fixed, knowable set, an
# Xopt mode's are whatever the generator proposed. A store-consuming mode runs no
# points at all.
TABLE_STATUS_MODES = ('single', 'parameter_sweep')
XOPT_STATUS_MODES = ('scalar_optimize', 'gp_parameter_sweep')
STATUS_MODES = TABLE_STATUS_MODES + XOPT_STATUS_MODES


def _run_declarative(lume_ace3p_data):
    """Build a :class:`Workflow` from the ``workflow:`` list and drive it through
    the workflow-agnostic mode layer.

    The pipeline is a declarative ``workflow:`` list of modules (validated into a
    runnable DAG by artifact dependencies); the ``mode:`` block selects how it is
    driven — ``single`` / ``parameter_sweep`` / ``scalar_optimize`` /
    ``gp_parameter_sweep``. Output extraction is declared per-module in
    ``output_parameters`` and performed inside :meth:`Workflow.evaluate`, so no
    solver-specific parsing lives in the driver.

    **Store-consuming modes** (``train_surrogate`` / ``invert_optimize``, see
    :data:`lume_ace3p.modes.STORE_CONSUMING_MODES`) read an on-disk store or saved
    model and never drive the module chain, so no ``workflow:`` block is built (or
    required) for them — their config declares only what they actually read.

    A ``resume: true`` in the ``mode:`` block reaches the table modes and the Xopt
    modes from here through :func:`~lume_ace3p.modes.run_mode`; ``--status`` (see
    :func:`_report_status`) is the read-only counterpart and does not come through
    this function at all."""
    mode_cfg = lume_ace3p_data.get('mode') or {}
    mode_type = mode_type_of(mode_cfg)
    if mode_type not in ('single', 'parameter_sweep', 'collect_training_data',
                         'train_surrogate', 'invert_optimize',
                         'invert_bayesian', 'scalar_optimize',
                         'gp_parameter_sweep'):
        raise ValueError(
            f"workflow mode '{mode_type}' is not handled "
            "(single | parameter_sweep | collect_training_data | "
            "train_surrogate | invert_optimize | invert_bayesian | "
            "scalar_optimize | gp_parameter_sweep).")
    workflow = (None if is_store_consuming(mode_cfg)
                else Workflow.from_config(lume_ace3p_data))
    return run_mode(mode_cfg, workflow,
                    output_spec=lume_ace3p_data.get('output_parameters'),
                    vocs=lume_ace3p_data.get('vocs_parameters'),
                    xopt=lume_ace3p_data.get('xopt_parameters'),
                    sweep=lume_ace3p_data.get('sweep_parameters'))


def _report_status(lume_ace3p_data):
    """Print what a config's campaign has already recorded about itself.

    Reads only what earlier runs wrote — the per-point run manifests
    (:mod:`lume_ace3p.state`) for a table mode, the campaign's resume state
    (:mod:`lume_ace3p.xopt_state`) for an Xopt one. Nothing is executed and nothing
    is written, so it is safe to run against a campaign that is still going, and it
    is how a half-finished one is made legible.

    Two branches because progress means two different things. A table mode has a
    fixed set of points, so the report is a table of them: which are done, which
    broke and where, which never started. An optimization does not — its points were
    chosen as it went — so the report is how many evaluations are banked and what the
    best one is."""
    mode_cfg = lume_ace3p_data.get('mode') or {}
    mode_type = mode_type_of(mode_cfg)
    if mode_type in XOPT_STATUS_MODES:
        return xopt_status(mode_cfg)
    if mode_type not in TABLE_STATUS_MODES:
        raise ValueError(
            f"--status covers the resumable modes {list(STATUS_MODES)}, and this "
            f"config's mode is '{mode_type}'. The store-consuming modes "
            "(train_surrogate, invert_optimize, invert_bayesian) run no points at "
            "all, and collect_training_data records its progress as the samples "
            "already in its store.")
    return status(Workflow.from_config(lume_ace3p_data))


def _is_legacy_format(lume_ace3p_data):
    """Detect the pre-refactor YAML shape, where the pipeline and its driver were
    selected by ``module`` / ``mode`` keys nested inside ``workflow_parameters``
    (rather than a top-level ``workflow:`` list plus a ``mode:`` block)."""
    wp = lume_ace3p_data.get('workflow_parameters')
    return isinstance(wp, dict) and ('module' in wp or 'mode' in wp)


def _legacy_removal_notice():
    """Migration-focused message for configs written against the removed schema."""
    return (
        "Error: this YAML uses the pre-refactor LUME-ACE3P format, where the "
        "pipeline and driver were set by 'module'/'mode' keys inside "
        "'workflow_parameters'. That schema was REMOVED in the module/workflow/"
        "mode refactor and no longer runs.\n\n"
        "Migrate to the declarative schema:\n"
        "  workflow:   an ordered list of module blocks (each with a 'module:' key)\n"
        "  mode:       a block selecting the driver, e.g. { type: parameter_sweep }\n"
        "  workflow_parameters:  now holds only directory settings (workdir, paths)\n\n"
        "Support for the old format is fully removed — there is no compatibility "
        "shim, so existing configs must be updated to run at all. See the "
        "examples/ directory and docs/yaml_reference.md for the current schema.")


def main():
    args = sys.argv[1:]

    if args and args[0] in ('--version', '-V'):
        print(f"lume-ace3p {__version__}")
        return
    if not args or args[0] in ('--help', '-h'):
        print("usage: run-lume-ace3p <input.yaml>\n"
              "       run-lume-ace3p --status <input.yaml>\n"
              "       run-lume-ace3p --version\n\n"
              "Runs a LUME-ACE3P workflow from a declarative YAML config (a "
              "'workflow:' list of modules plus a 'mode:' block). See the "
              "examples/ directory and docs/yaml_reference.md.\n\n"
              "--status runs nothing: it reports what a campaign resumed with "
              "'mode: {resume: true}' would pick up from. For a sweep that is a "
              "per-point completion table read from each workdir's run manifest; "
              "for an optimization it is the evaluations and best objective "
              "recorded in its xopt_state.yml.")
        # Exit non-zero when no input file was given (a usage error), zero for -h.
        sys.exit(0 if args else 1)

    report_status = args[0] == '--status'
    if report_status:
        args = args[1:]
        if not args:
            print("Error: --status needs a config file: "
                  "run-lume-ace3p --status <input.yaml>")
            sys.exit(1)

    input_file = args[0]
    print(f"lume-ace3p {__version__}", file=sys.stderr)

    try:
        lume_ace3p_data = load_yaml(input_file)
    except Exception as exc:
        print(exc)
        sys.exit(1)

    # A store-consuming mode (train_surrogate / invert_optimize) reads an on-disk
    # store or saved model and never drives the module chain, so it legitimately
    # carries no 'workflow:' block. Every other mode needs one.
    if (lume_ace3p_data.get('workflow') is None
            and not is_store_consuming(lume_ace3p_data.get('mode'))):
        if _is_legacy_format(lume_ace3p_data):
            print(_legacy_removal_notice())
        else:
            print("Error: the YAML has no top-level 'workflow:' list. LUME-ACE3P "
                  "uses the declarative module/mode schema — declare an ordered "
                  "'workflow:' list of modules plus a 'mode:' block. (Only the "
                  "store-consuming modes — train_surrogate, invert_optimize, "
                  "invert_bayesian — may omit 'workflow:', since they read a "
                  "saved store/model instead of running the chain.) See the "
                  "examples/ directory and docs/yaml_reference.md.")
        sys.exit(1)

    # A top-level 'workflow:' list is present, but the driver may still be written
    # the old way (mode nested in workflow_parameters, no top-level 'mode:' block).
    if lume_ace3p_data.get('mode') is None and _is_legacy_format(lume_ace3p_data):
        print(_legacy_removal_notice())
        sys.exit(1)

    # Checked here rather than in Workflow.from_config so --status sees it too, and
    # *after* the two legacy-format rejections above: a pre-refactor config is about
    # to be refused with a message that explains the whole problem, and a list of
    # unrecognized keys on top of it would be noise.
    warn_unrecognized(f"'{input_file}'", lume_ace3p_data, TOP_LEVEL_KEYS)

    if report_status:
        try:
            _report_status(lume_ace3p_data)
        except ValueError as exc:
            print(f'Error: {exc}')
            sys.exit(1)
        return

    try:
        _run_declarative(lume_ace3p_data)
    except ValueError as exc:
        # A configuration error — an unsupported generator, no termination criterion,
        # a missing 'bin_edges' for an MC-noisy objective, an unvalidatable workflow
        # (WorkflowValidationError is a ValueError). These used to be printed and then
        # *ignored*, so the process exited 0 having done nothing: in a batch queue,
        # a job that reports success, consumes its allocation and writes no output.
        #
        # Deliberately not `except Exception`: a solver crash, a parse failure or a
        # bug should still produce a traceback. Flattening those into one line would
        # trade the diagnosis for tidiness.
        print(f'Error: {exc}')
        sys.exit(1)


if __name__ == '__main__':
    main()
