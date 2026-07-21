import sys

from lume_ace3p import __version__
from lume_ace3p.inputs import load_yaml
from lume_ace3p.workflow_graph import Workflow
from lume_ace3p.modes import run_mode


def _run_declarative(lume_ace3p_data):
    """Build a :class:`Workflow` from the ``workflow:`` list and drive it through
    the workflow-agnostic mode layer.

    The pipeline is a declarative ``workflow:`` list of modules (validated into a
    runnable DAG by artifact dependencies); the ``mode:`` block selects how it is
    driven — ``single`` / ``parameter_sweep`` / ``scalar_optimize`` /
    ``gp_parameter_sweep``. Output extraction is declared per-module in
    ``output_parameters`` and performed inside :meth:`Workflow.evaluate`, so no
    solver-specific parsing lives in the driver."""
    workflow = Workflow.from_config(lume_ace3p_data)
    mode_cfg = lume_ace3p_data.get('mode') or {}
    mode_type = str(mode_cfg.get('type') or mode_cfg.get('mode', '')).lower()
    if mode_type not in ('single', 'parameter_sweep', 'collect_training_data',
                         'train_surrogate', 'scalar_optimize',
                         'gp_parameter_sweep'):
        raise ValueError(
            f"workflow mode '{mode_type}' is not handled "
            "(single | parameter_sweep | collect_training_data | "
            "train_surrogate | scalar_optimize | gp_parameter_sweep).")
    return run_mode(mode_cfg, workflow,
                    output_spec=lume_ace3p_data.get('output_parameters'),
                    vocs=lume_ace3p_data.get('vocs_parameters'),
                    xopt=lume_ace3p_data.get('xopt_parameters'),
                    sweep=lume_ace3p_data.get('sweep_parameters'))


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
              "       run-lume-ace3p --version\n\n"
              "Runs a LUME-ACE3P workflow from a declarative YAML config (a "
              "'workflow:' list of modules plus a 'mode:' block). See the "
              "examples/ directory and docs/yaml_reference.md.")
        # Exit non-zero when no input file was given (a usage error), zero for -h.
        sys.exit(0 if args else 1)

    input_file = args[0]
    print(f"lume-ace3p {__version__}", file=sys.stderr)

    try:
        lume_ace3p_data = load_yaml(input_file)
    except Exception as exc:
        print(exc)
        sys.exit(1)

    if lume_ace3p_data.get('workflow') is None:
        if _is_legacy_format(lume_ace3p_data):
            print(_legacy_removal_notice())
        else:
            print("Error: the YAML has no top-level 'workflow:' list. LUME-ACE3P "
                  "uses the declarative module/mode schema — declare an ordered "
                  "'workflow:' list of modules plus a 'mode:' block. See the "
                  "examples/ directory and docs/yaml_reference.md.")
        sys.exit(1)

    # A top-level 'workflow:' list is present, but the driver may still be written
    # the old way (mode nested in workflow_parameters, no top-level 'mode:' block).
    if lume_ace3p_data.get('mode') is None and _is_legacy_format(lume_ace3p_data):
        print(_legacy_removal_notice())
        sys.exit(1)

    _run_declarative(lume_ace3p_data)


if __name__ == '__main__':
    main()
