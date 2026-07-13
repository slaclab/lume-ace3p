import sys

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
    if mode_type not in ('single', 'parameter_sweep', 'scalar_optimize',
                         'gp_parameter_sweep'):
        raise ValueError(
            f"workflow mode '{mode_type}' is not handled "
            "(single | parameter_sweep | scalar_optimize | gp_parameter_sweep).")
    return run_mode(mode_cfg, workflow,
                    output_spec=lume_ace3p_data.get('output_parameters'),
                    vocs=lume_ace3p_data.get('vocs_parameters'),
                    xopt=lume_ace3p_data.get('xopt_parameters'),
                    sweep=lume_ace3p_data.get('sweep_parameters'))


def main():
    input_file = sys.argv[1]

    try:
        lume_ace3p_data = load_yaml(input_file)
    except Exception as exc:
        print(exc)
        sys.exit(1)

    if lume_ace3p_data.get('workflow') is None:
        print("Error: the YAML has no top-level 'workflow:' list. LUME-ACE3P uses "
              "the declarative module/mode schema — declare an ordered 'workflow:' "
              "list of modules plus a 'mode:' block. See the examples/ directory "
              "and docs/yaml_reference.md.")
        sys.exit(1)

    _run_declarative(lume_ace3p_data)


if __name__ == '__main__':
    main()
