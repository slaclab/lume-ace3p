# Incomplete / non-working examples

The configs in this folder are **not runnable as-is** and are kept only as
reference templates for their respective Xopt generators. They are excluded from
the runnable `examples/` set and from the end-to-end verification path. Do not
copy them expecting a working pipeline — start from a runnable example
(`examples/s3p_optimization/`, `examples/s3p_mf_optimization/`) instead.

Two reasons they don't run:

1. **Legacy schema.** Both still use the pre-refactor layout, where the
   pipeline and driver were selected by `mode:`/`module:` keys nested inside
   `workflow_parameters:`. That schema was removed in the module/workflow/mode
   refactor; the current loader rejects it. See `docs/yaml_reference.md` for the
   `workflow:` + `mode:` schema.
2. **Missing geometry files.** Both reference `load.jou` / `load.s3p`, which were
   never shipped with the repository, so the Cubit/S3P steps have no inputs. The
   VOCS variable names (`R1`, `L1`, `r10`) and objective frequencies do not match
   any shipped journal, so they cannot simply be pointed at an existing example's
   geometry without inventing a new problem.

## `MOBO_ExpectedHypervolume_Example.yaml`

Multi-objective Bayesian optimization with `ExpectedHypervolumeImprovementGenerator`
(three `S(0,0)` objectives at different frequencies, with a `reference_point`).
The EHVI generator construction/stepping is covered by the synthetic-workflow
baseline (`tests/baseline/MOBO_ExpectedHypervolume_Example/`) and by
`tests/test_run_xopt_compat.py`, so the generator wiring is exercised even though
this YAML itself does not run.

## `UCB_Example.yaml`

`UpperConfidenceBoundGenerator` template. Beyond the two issues above, the
shipped config declares **three objectives**, but xopt 3.0.0's UCB generator
rejects multi-objective VOCS (`VOCSError: "this generator does not support
multi-objective optimization"`). Even with geometry files and the new schema, it
would need to be reduced to a single objective to run.

## Migrating one of these

To turn either into a runnable example: rewrite it to the `workflow:` + `mode:`
schema (see `examples/s3p_optimization/s3p_optimization.yaml`), declare the
optimization knobs under `input_parameters.cubit`, and supply a Cubit journal
and S3P input whose variable names match the VOCS `variables`. For UCB, also
reduce the objective set to one.
