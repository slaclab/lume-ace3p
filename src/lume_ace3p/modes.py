"""Mode layer for the workflow-modularization refactor (Phases 3-4).

A *mode* is how a validated :class:`~lume_ace3p.workflow_graph.Workflow` is
*driven*:

* ``single`` runs it once,
* ``parameter_sweep`` runs it over a tensor product of the swept input axes,
* ``scalar_optimize`` drives an Xopt optimization loop (Phase 4),
* ``gp_parameter_sweep`` drives an Xopt Bayesian-exploration loop and emits a
  GP-posterior-mean sweep (Phase 4).

Modes are deliberately **workflow-agnostic** — they touch the workflow only
through its public seams (:meth:`Workflow.evaluate`, :meth:`Workflow.sweep_axes`,
:meth:`Workflow.field_index`) and never reach into any solver-specific code. In
particular the two Xopt modes below pull their objective scalar(s) from
``workflow.evaluate(input_dict)`` + the declarative ``output_parameters`` spec,
replacing the S-parameter/frequency parsing hardwired into the legacy
``run_xopt``. This makes *any* workflow (S3P, Geant4, a multi-step chain)
optimizable/sweepable — this is the generic-Xopt driver that absorbs the shelved
Geant4 surrogate-project Phase 1.

Result container for the sweep/single modes = a pandas ``DataFrame`` (the hybrid
data model from the plan): one row per evaluation, columns = swept input
variable names + the extracted scalar outputs. Two shapes:

* **wide / scalar** (Omega3P, Geant4, ...) — one row per grid point; each
  ``output_parameters`` entry is a scalar column. This replaces the legacy
  ``WriteOmega3PDataTable`` path.
* **long / tidy** (S3P) — a module that exposes a shared field index
  (:meth:`Module.field_index`, e.g. ``('Frequency', array)``) emits one row per
  ``(grid-point, frequency)``; each S-parameter output becomes a column aligned
  to that index. This replaces the legacy ``WriteS3PDataTable`` path.

Per-run *field* outputs (S-parameter vectors, dose/edep voxel grids) are NOT
exploded into the scalar table — they stay as structured objects / files
referenced from ``workflow.last_context`` — except the S3P long-format case
above, which the plan calls out as the one tidy-frame exception.

The Xopt modes log ``X.data`` (already a DataFrame) via ``to_csv``; the legacy
``WriteXoptData`` string-dump and the ``WriteS3PDataTable`` xopt-append path are
dropped (clean break — numeric equivalence only, not file format).

The scalar-table writer is ``DataFrame.to_csv`` (tab-delimited); the manual
``tools.py`` writers are removed in Phase 6, not here.
"""

import numpy as np
import pandas as pd


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

    ``output_spec`` is accepted for API symmetry but is informational only — the
    workflow already carries its ``output_parameters`` (``workflow.output_spec``)
    and does the extraction inside :meth:`Workflow.evaluate`."""
    mode_type = str(mode_cfg.get('type') or mode_cfg.get('mode')).lower()
    if mode_type == 'single':
        df = single(workflow)
    elif mode_type == 'parameter_sweep':
        df = parameter_sweep(workflow)
    elif mode_type == 'scalar_optimize':
        return scalar_optimize(
            workflow, vocs, xopt,
            log_file=mode_cfg.get('output_file') or 'sim_output.txt')
    elif mode_type == 'gp_parameter_sweep':
        return gp_parameter_sweep(
            workflow, sweep, vocs, xopt,
            log_file=mode_cfg.get('output_file') or 'sim_output.txt',
            sweep_file=mode_cfg.get('sweep_output_file') or 'sweep_output.txt')
    else:
        raise ValueError(
            f"mode '{mode_type}' is not handled by the mode layer "
            "(single | parameter_sweep | scalar_optimize | gp_parameter_sweep).")

    output_file = mode_cfg.get('output_file') or mode_cfg.get('sweep_output_file')
    if output_file:
        write_table(df, output_file)
    return df


def single(workflow):
    """Run the workflow once and return a one-row (or, for a field-indexed
    solver, one-row-per-index) result DataFrame.

    The base ``inputs`` must already be scalar-valued (no swept axes). Input
    columns are the scalar cubit knobs; output columns are the extracted
    ``output_parameters``."""
    input_names = list(workflow.inputs.cubit.keys())
    scalars = [workflow.inputs.cubit[name] for name in input_names]
    outputs = workflow.evaluate(None)
    rows = _rows_for_point(workflow, input_names, scalars, outputs)
    return _frame(workflow, input_names, rows)


def parameter_sweep(workflow):
    """Run the workflow over the tensor product of its swept axes, one row per
    grid point (or per ``(grid-point, field-index)`` for a field-indexed
    solver). Returns the result DataFrame."""
    axes = workflow.sweep_axes()
    input_names = [label for label, _values, _setter in axes]
    tensor = _input_tensor(axes)

    rows = []
    for i in range(tensor.shape[0]):
        scalars = tensor[i].tolist()
        outputs = workflow.evaluate(scalars if axes else None)
        rows.extend(_rows_for_point(workflow, input_names, scalars, outputs))
    return _frame(workflow, input_names, rows)


# --------------------------------------------------------------------------- #
# Row / frame construction — shared by single + parameter_sweep.
# --------------------------------------------------------------------------- #


def _rows_for_point(workflow, input_names, scalars, outputs):
    """Build the result row(s) for one evaluation.

    Wide case: a single row of ``{input: scalar, ..., output: scalar}``.
    Long case (a module exposes a field index, e.g. S3P frequency): one row per
    index value, each output array sampled at that index — the tidy
    ``(inputs..., Frequency, S(m,n)...)`` frame the plan calls out."""
    output_names = list(workflow.output_spec.keys())
    base = dict(zip(input_names, scalars))
    index = workflow.field_index()
    if index is None:
        row = dict(base)
        for name in output_names:
            row[name] = outputs[name]
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


def _frame(workflow, input_names, rows):
    """Assemble the ordered-column DataFrame. Column order is: swept inputs,
    then the field-index label (long case only), then outputs — matching the
    left-to-right layout of the legacy sweep tables."""
    output_names = list(workflow.output_spec.keys())
    index = workflow.field_index()
    columns = list(input_names)
    if index is not None:
        columns.append(index[0])
    columns += output_names
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
# Writer — the DataFrame.to_csv replacement for the manual tools.py writers.
# --------------------------------------------------------------------------- #


def write_table(df, filename):
    """Write a result DataFrame to a tab-delimited text file. NaNs are rendered
    as ``nan`` (not blank) so the file round-trips through a whitespace reader
    without column drift."""
    df.to_csv(filename, sep='\t', index=False, na_rep='nan')


# --------------------------------------------------------------------------- #
# Xopt modes (Phase 4) — the generic, workflow-agnostic optimize/GP-sweep
# driver. Objective scalars come from ``workflow.evaluate(input_dict)`` +
# the declarative ``output_parameters`` spec (extraction happens inside the
# workflow/modules), so no S-parameter/frequency parsing lives here.
# --------------------------------------------------------------------------- #


def _log_xopt(filename, xopt_obj):
    """Log an Xopt run's data table. ``X.data`` is already a pandas DataFrame,
    so this is a plain ``to_csv`` (tab-delimited) — the clean-break replacement
    for the legacy ``WriteXoptData`` string-dump. Overwrites each call so the
    file always holds the full trajectory."""
    xopt_obj.data.to_csv(filename, sep='\t', index=False, na_rep='nan')


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


def _build_generator(vocs, vocs_dict, xopt_dict, mc_noisy):
    """Construct the Xopt generator named by ``xopt_dict['generator']``.

    Preserves the six generators supported today with their behavior unchanged
    (NelderMead, ExpectedImprovement, MultiFidelity, UpperConfidenceBound,
    ExpectedHypervolumeImprovement/MOBO, and — via
    :func:`gp_parameter_sweep` — BayesianExploration). Returns ``None`` with a
    printed message for an unsupported generator (matching the legacy contract).
    """
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
            print("Error: 'reference_point' is required for Multi-Objective "
                  "optimization.")
            return None
        return ExpectedHypervolumeImprovementGenerator(vocs=vocs, **options)
    print("That generator is not supported. Ensure that the generator name "
          "specified in the yaml file matches exactly with the Xopt generator "
          "name of choice. Exiting the program.")
    return None


def _make_vocs(vocs_dict):
    """Build a standard Xopt :class:`~xopt.vocs.VOCS` from the declarative VOCS
    block. The objective *names* are ``output_parameters`` names — the same keys
    :meth:`Workflow.evaluate` returns — so extraction stays a workflow concern.

    Clean break: the VOCS block is the plain Xopt shape
    (``variables`` + ``objectives`` name->MINIMIZE/MAXIMIZE, with optional
    ``constraints`` / ``observables`` / ``constants``), NOT the legacy
    S-parameter/frequency triple."""
    from xopt.vocs import VOCS
    kwargs = {'variables': vocs_dict['variables']}
    for key in ('objectives', 'constraints', 'observables', 'constants'):
        if vocs_dict.get(key):
            kwargs[key] = vocs_dict[key]
    return VOCS(**kwargs)


def _objective_from_workflow(workflow, vocs, xopt_dict):
    """Return an Xopt evaluator function that drives ``workflow.evaluate`` and
    returns the VOCS output scalars, generically.

    The function pulls exactly the VOCS output names (objectives + constraints +
    observables) out of the workflow's returned outputs — no solver-specific
    parsing. When a fidelity variable is configured (MultiFidelity), the Xopt
    fidelity axis ``s`` is renamed to the user's variable name before being
    handed to the workflow (unchanged from the legacy driver)."""
    output_names = list(vocs.output_names)
    fidelity_variable = xopt_dict.get('fidelity_variable')

    def sim_function(input_dict):
        input_dict = dict(input_dict)
        if fidelity_variable is not None and 's' in input_dict:
            input_dict[fidelity_variable] = input_dict.pop('s')
        outputs = workflow.evaluate(input_dict)
        missing = [n for n in output_names if n not in outputs]
        if missing:
            raise KeyError(
                f"workflow.evaluate did not return VOCS output(s) {missing}; "
                f"declare them in output_parameters. Got {list(outputs)}.")
        return {n: outputs[n] for n in output_names}

    return sim_function


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


def scalar_optimize(workflow, vocs_dict, xopt_dict, log_file='sim_output.txt'):
    """Drive an Xopt scalar optimization of ``workflow`` (Phase 4).

    Workflow-agnostic: the objective scalar(s) are whatever ``vocs_dict``
    declares as objectives, pulled from ``workflow.evaluate(input_dict)``. Any
    workflow with a matching ``output_parameters`` spec (S3P reflection, a
    Geant4 dose/weight, a multi-step chain) can be optimized with no changes
    here.

    Preserves the legacy driver's generator set, fidelity-variable rename,
    cost-function logic, and termination criteria; only the objective extraction
    (was S-parameter/frequency parsing) and logging (was ``WriteXoptData`` +
    ``WriteS3PDataTable``) are replaced. Returns the :class:`xopt.Xopt` object."""
    import torch
    from xopt.vocs import random_inputs as vocs_random_inputs
    from xopt.evaluator import Evaluator
    from xopt import Xopt

    mc_noisy = _mc_noise_guards(xopt_dict)
    vocs = _make_vocs(vocs_dict)
    targets = list(vocs.objective_names)
    tols = _tolerances(xopt_dict, targets)

    sim_function = _objective_from_workflow(workflow, vocs, xopt_dict)
    generator = _build_generator(vocs, vocs_dict, xopt_dict, mc_noisy)
    if generator is None:
        return None
    evaluator = Evaluator(function=sim_function)
    X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)

    iteration_index = 0
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

    # Initial random evaluations to seed the model.
    if 'num_random' in xopt_dict:
        for _ in range(xopt_dict['num_random']):
            X.random_evaluate()
            _log_xopt(log_file, X)
            iteration_index += 1

    if 'num_step' in xopt_dict:
        for _ in range(xopt_dict['num_step']):
            X.step()
            _log_xopt(log_file, X)
            iteration_index += 1
        if 'max_iterations' in xopt_dict:
            while iteration_index < xopt_dict['max_iterations'] and not tol_achieved:
                X.step()
                if tols:
                    tol_achieved = check_tols()
                _log_xopt(log_file, X)
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

        num_random = xopt_dict.get('num_random', 2)
        random_pts = vocs_random_inputs(vocs, num_random)
        init_fidelity = np.linspace(0, 1, num_random)
        for it in range(len(random_pts)):
            random_pts[it]['s'] = init_fidelity[it]
        X.evaluate_data(pd.DataFrame(random_pts))
        _log_xopt(log_file, X)

        cost_function = xopt_dict.get('cost_function', 'exponential')
        if cost_function.lower() == 'exponential':
            p1 = X.data['xopt_runtime'][num_random - 1] / X.data['xopt_runtime'][0]

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
            print("Cost function type: '" + cost_function + "' not supported.")
            return None

        iteration_index += num_random
        while X.data['xopt_runtime'].sum() < cost_budget and not tol_achieved:
            X.step()
            if tols:
                tol_achieved = check_tols()
            _log_xopt(log_file, X)
            iteration_index += 1
    else:
        print("No termination criteria specified for Xopt. Provide a criterion "
              "such as 'num_step', 'tolerance', or 'cost_budget' (for "
              "multi-fidelity).")
        return None

    _save_model(X, xopt_dict)
    return X


def gp_parameter_sweep(workflow, sweep_dict, vocs_dict, xopt_dict,
                       log_file='sim_output.txt',
                       sweep_file='sweep_output.txt'):
    """Drive an Xopt Bayesian-exploration loop over ``workflow`` and emit a
    GP-posterior-mean sweep over the ``sweep_parameters`` grid (Phase 4).

    Workflow-agnostic in the same way as :func:`scalar_optimize`: the explored
    quantities are the VOCS objectives (declared 'explore'), pulled from
    ``workflow.evaluate``. Returns the :class:`xopt.Xopt` object."""
    import torch
    from xopt.evaluator import Evaluator
    from xopt import Xopt
    from xopt.generators.bayesian import BayesianExplorationGenerator

    _mc_noise_guards(xopt_dict)

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
    generator = BayesianExplorationGenerator(vocs=vocs)

    sim_function = _objective_from_workflow(workflow, vocs, xopt_dict)
    evaluator = Evaluator(function=sim_function)
    X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)

    num_random = xopt_dict.get('num_random', 5)
    for _ in range(num_random):
        X.random_evaluate()

    improvement = xopt_dict.get('improvement_threshold', 0.01)
    patience = xopt_dict.get('patience', 5)
    prev_bests = []
    steps = 0
    hit_max_steps = False
    while not hit_max_steps:
        X.step()
        _log_xopt(log_file, X)
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

    with open(sweep_file, 'w') as sweepfile:
        for iv in input_varname:
            sweepfile.write(iv + '\t')
        for obj in targets:
            sweepfile.write(obj + '\t')
        sweepfile.write('\n')
        for i in range(input_tensor.shape[0]):
            row = {input_varname[j]: input_tensor[i][j]
                   for j in range(len(input_varname))}
            test_points = torch.tensor(pd.DataFrame([row]).values,
                                       dtype=torch.double)
            posterior = X.generator.model.posterior(test_points).mean
            for value in input_tensor[i]:
                sweepfile.write(str(value) + '\t')
            for data_point in posterior:
                sweepfile.write(str(float(data_point[0])) + '\t')
            sweepfile.write('\n')

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
