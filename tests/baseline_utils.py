"""Shared machinery for the Phase-0.5 golden baseline (see
`docs/workflow_module_refactor_plan.md`).

The refactor is a *clean break* on output file formats, so later phases check
equivalence on **numeric content**, not bytes. This module captures the current
behavior of every shipped example as a frozen fixture under `tests/baseline/`
and provides the comparison helpers used both to freeze and to self-check.

Two producer families:

* **dry-run / real-CPU paths** — the ACE3P/Geant4 environment is absent locally,
  so the reachable baseline is the dry-run marker/table output. The pure-Python
  steps that *do* run (Cubit input mutation, the `Particles` field-emission
  weighting) produce genuine numbers and are captured for real.
* **Xopt paths** — driven with a deterministic synthetic solver (same pattern as
  `tests/test_run_xopt_compat.py`), so the optimizer trajectory / GP predictions
  are cluster-independent and seed-reproducible.

Fixtures are stored as:

* small text tables / markers — verbatim files (`files` entries),
* large numeric arrays (weighted particle dumps, Geant4 source files) — a
  compact JSON *numeric digest* (`digests` entries): row count + per-column
  sum/min/max/mean, enough to catch a numeric regression under tolerance
  without committing multi-megabyte arrays.

The `EXAMPLES` registry is the single source of truth consumed by both
`freeze_baseline.py` (writer) and `test_baseline_selfcheck.py` (verifier).
"""

import glob
import json
import os
import random
import re
import shutil
import tempfile

import numpy as np


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXAMPLES_DIR = os.path.join(REPO, 'examples')
BASELINE_DIR = os.path.join(HERE, 'baseline')


# --------------------------------------------------------------------------- #
# Deterministic synthetic solver (for the Xopt-driven paths)
# --------------------------------------------------------------------------- #

# Frequency grid referenced by the example YAMLs so integer-index lookups in
# the synthetic workflow succeed for every objective frequency the configs use.
SYNTH_FREQS = np.array([
    9.424e9, 9.674e9, 11.324e9, 11.424e9, 11.524e9, 12.0e9, 12.424e9,
])


class SyntheticWorkflow:
    """Stand-in :class:`~lume_ace3p.workflow_graph.Workflow` for the generic Xopt
    modes: exposes ``evaluate(input_dict) -> {objective_name: scalar}`` computed
    from a deterministic, input-dependent synthetic S-parameter response, so the
    optimizer sees signal but never touches Cubit/S3P.

    ``output_spec`` maps each declared objective name to the
    ``(s_parameter, frequency)`` it extracts — the S-parameter knowledge lives in
    the fake *workflow*, so the mode itself stays workflow-agnostic. This is the
    same shape as ``SynthWorkflow`` in ``tests/test_run_xopt_compat.py`` and uses
    a frequency grid covering every example's objective frequency."""

    def __init__(self, output_spec):
        self.output_spec = output_spec

    def evaluate(self, input_dict):
        x = float(sum(float(v) for v in input_dict.values()))
        base = 0.01 * (1.0 + np.cos(SYNTH_FREQS / 1e9 - x))
        data = {'Frequency': SYNTH_FREQS, 'S(0,0)': base, 'S(1,1)': base * 0.9}
        out = {}
        for name, (sparam, freq) in self.output_spec.items():
            idx = list(SYNTH_FREQS).index(float(freq))
            out[name] = data[sparam][idx]
        return out


def seed_all(seed=0):
    """Seed every RNG the Xopt path can touch (python/numpy/torch) so
    trajectories are reproducible run-to-run."""
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Numeric normalization / comparison helpers
# --------------------------------------------------------------------------- #

# Columns whose values are wall-clock timings, not simulation results. They
# vary run-to-run and are stripped before any numeric comparison.
_TIMING_COLUMNS = {'xopt_runtime', 'xopt_error'}

_FLOAT_RE = re.compile(r'[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?')


def _stage_example(example_dir):
    """Copy an example's input files into a fresh temp dir and return its path.

    Examples reference large shared inputs by relative path into a sibling
    ``examples/assets/`` (e.g. ``file: '../assets/foo.txt'``), so the example is
    staged as ``<root>/<example_dir>/`` with ``examples/assets/`` copied to
    ``<root>/assets/`` — preserving the ``../assets/`` relationship the YAMLs
    rely on. Only regular files are copied (examples have no other subdirs)."""
    src = os.path.join(EXAMPLES_DIR, example_dir)
    root = tempfile.mkdtemp(prefix='baseline_')
    tmp = os.path.join(root, os.path.basename(example_dir))
    os.mkdir(tmp)
    for name in os.listdir(src):
        path = os.path.join(src, name)
        if os.path.isfile(path):
            shutil.copy(path, tmp)
    assets_src = os.path.join(EXAMPLES_DIR, 'assets')
    if os.path.isdir(assets_src):
        assets_dst = os.path.join(root, 'assets')
        os.mkdir(assets_dst)
        for name in os.listdir(assets_src):
            path = os.path.join(assets_src, name)
            if os.path.isfile(path):
                shutil.copy(path, assets_dst)
    return tmp


def read_table(path, drop_timing=True):
    """Load a whitespace-delimited numeric table (Xopt `sim_output.txt`,
    `sweep_output.txt`, the sweep table writers) into a pandas DataFrame.
    Timing columns are dropped by default so comparisons see only results."""
    import pandas as pd
    df = pd.read_csv(path, sep=r'\s+')
    df = df.drop(columns=[c for c in df.columns if c.startswith('Unnamed')],
                 errors='ignore')
    if drop_timing:
        df = df.drop(columns=[c for c in df.columns if c in _TIMING_COLUMNS],
                     errors='ignore')
    return df


def compare_tables(baseline_path, produced_path, atol=1e-6, rtol=1e-6):
    """Compare two numeric tables with tolerance. Returns (ok, message).

    Non-numeric columns must match exactly; numeric columns are compared with
    `np.allclose`. Column set and row count must match."""
    base = read_table(baseline_path)
    prod = read_table(produced_path)
    if list(base.columns) != list(prod.columns):
        return False, (f'column mismatch: baseline={list(base.columns)} '
                       f'produced={list(prod.columns)}')
    if base.shape != prod.shape:
        return False, f'shape mismatch: baseline={base.shape} produced={prod.shape}'
    num_cols = base.select_dtypes('number').columns
    obj_cols = [c for c in base.columns if c not in num_cols]
    for c in obj_cols:
        if not base[c].equals(prod[c]):
            return False, f'non-numeric column {c!r} differs'
    if len(num_cols):
        if not np.allclose(base[num_cols].values, prod[num_cols].values,
                           atol=atol, rtol=rtol, equal_nan=True):
            diff = np.nanmax(np.abs(base[num_cols].values - prod[num_cols].values))
            return False, f'numeric columns differ (max abs diff {diff:g})'
    return True, 'ok'


def compare_marker(baseline_path, produced_path):
    """Compare a dry-run marker / free-form text file by the numbers it
    contains (paths and workdir names embed absolute temp dirs that legitimately
    differ run-to-run, so we compare the numeric content, not the raw text)."""
    base_nums = [float(x) for x in _FLOAT_RE.findall(_read(baseline_path))]
    prod_nums = [float(x) for x in _FLOAT_RE.findall(_read(produced_path))]
    if len(base_nums) != len(prod_nums):
        return False, (f'marker numeric-token count differs: '
                       f'baseline={len(base_nums)} produced={len(prod_nums)}')
    if not np.allclose(base_nums, prod_nums, atol=1e-9, rtol=1e-9):
        return False, 'marker numeric tokens differ'
    return True, 'ok'


def _read(path):
    with open(path) as f:
        return f.read()


def numeric_digest(path):
    """Compute a compact, comparison-ready digest of a large whitespace-numeric
    array file (weighted particle dump, Geant4 source file). Captures row/column
    counts and per-column sum/min/max/mean — enough to detect a numeric
    regression under tolerance without committing the multi-MB array."""
    arr = np.loadtxt(path)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    return {
        'rows': int(arr.shape[0]),
        'cols': int(arr.shape[1]),
        'col_sum': [float(v) for v in arr.sum(axis=0)],
        'col_min': [float(v) for v in arr.min(axis=0)],
        'col_max': [float(v) for v in arr.max(axis=0)],
        'col_mean': [float(v) for v in arr.mean(axis=0)],
    }


def compare_digests(baseline_digest, produced_digest, atol=1e-6, rtol=1e-9):
    """Compare two numeric digests (dicts from `numeric_digest`)."""
    if baseline_digest['rows'] != produced_digest['rows']:
        return False, (f'row count differs: baseline={baseline_digest["rows"]} '
                       f'produced={produced_digest["rows"]}')
    if baseline_digest['cols'] != produced_digest['cols']:
        return False, (f'col count differs: baseline={baseline_digest["cols"]} '
                       f'produced={produced_digest["cols"]}')
    for key in ('col_sum', 'col_min', 'col_max', 'col_mean'):
        if not np.allclose(baseline_digest[key], produced_digest[key],
                           atol=atol, rtol=rtol):
            return False, f'{key} differs'
    return True, 'ok'


# --------------------------------------------------------------------------- #
# Producers — run one example the way run_lume_ace3p would, and return the
# directory it produced its outputs in. Each returns the produced work dir path.
# --------------------------------------------------------------------------- #


def _drive_declarative(data):
    """Build a Workflow from a loaded YAML mapping and drive it through the mode
    layer, forcing dry-run. Used by the sweep / single producers."""
    from lume_ace3p.workflow_graph import Workflow
    from lume_ace3p.modes import run_mode
    data.setdefault('workflow_parameters', {})['dry_run'] = True
    workflow = Workflow.from_config(data)
    run_mode(data.get('mode') or {}, workflow,
             output_spec=data.get('output_parameters'),
             vocs=data.get('vocs_parameters'),
             xopt=data.get('xopt_parameters'),
             sweep=data.get('sweep_parameters'))


def _produce_declarative(example_dir, yaml_name, meta):
    """Dry-run a declarative `parameter_sweep` / `single` example (the s3p /
    omega3p / geant4 / track3p chains) through the real module/mode path."""
    from lume_ace3p.inputs import load_yaml
    tmp = _stage_example(example_dir)
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        _drive_declarative(load_yaml(yaml_name))
    finally:
        os.chdir(cwd)
    return tmp


# The frozen Xopt baselines were captured with objectives auto-named
# ``S(param)_<freq>`` (e.g. ``S(0,0)_12000000000.0``); the synthetic workflow
# maps each such name back to the (s_parameter, frequency) it extracts. The
# example YAMLs may use friendlier objective names, so the baseline producers
# build the synthetic workflow + VOCS directly against the frozen column names.
def _synth_spec(objectives):
    """Parse ``{name: (sparam, freq)}`` from objective names shaped
    ``S(m,n)_<freq>``."""
    spec = {}
    for name in objectives:
        sparam, _, freq = name.rpartition('_')
        spec[name] = (sparam, float(freq))
    return spec


def _produce_xopt_scalar(example_dir, yaml_name, meta):
    """Drive a `scalar_optimize` baseline through the generic mode with the
    synthetic workflow and a fixed seed (no solver env needed). The synthetic
    config (objectives / variables / xopt) is carried on the registry entry."""
    from lume_ace3p import modes
    synth = meta['synth']
    tmp = _stage_example(example_dir)
    cwd = os.getcwd()
    wf = SyntheticWorkflow(_synth_spec(synth['objectives']))
    vocs = {'variables': synth['variables'],
            'objectives': {n: 'MINIMIZE' for n in synth['objectives']}}
    seed_all()
    os.chdir(tmp)
    try:
        modes.scalar_optimize(wf, vocs, synth['xopt'], log_file='sim_output.txt')
    finally:
        os.chdir(cwd)
    return tmp


def _produce_xopt_gp_sweep(example_dir, yaml_name, meta):
    """Drive a `gp_parameter_sweep` baseline through the generic mode with the
    synthetic workflow and a fixed seed."""
    from lume_ace3p import modes
    synth = meta['synth']
    tmp = _stage_example(example_dir)
    cwd = os.getcwd()
    wf = SyntheticWorkflow(_synth_spec(synth['objectives']))
    vocs = {'variables': synth['variables'],
            'objectives': {n: 'explore' for n in synth['objectives']}}
    seed_all()
    os.chdir(tmp)
    try:
        modes.gp_parameter_sweep(wf, synth['sweep'], vocs, synth['xopt'],
                                 log_file='sim_output.txt',
                                 sweep_file='sweep_output.txt')
    finally:
        os.chdir(cwd)
    return tmp


PRODUCERS = {
    'sweep': _produce_declarative,
    'single': _produce_declarative,
    'particle_weight': _produce_declarative,
    'xopt_scalar': _produce_xopt_scalar,
    'xopt_gp_sweep': _produce_xopt_gp_sweep,
}


# --------------------------------------------------------------------------- #
# The example registry — single source of truth.
#
# Each entry:
#   kind        : producer key in PRODUCERS
#   yaml        : YAML filename inside examples/<name>/
#   files       : {fixture_name: (glob_relative_to_workdir, 'table')}. glob picks
#                 ONE file (first sorted match) so per-run temp paths don't leak.
#   digests     : {fixture_name: glob} — large numeric arrays frozen as digests
#   synth       : (xopt kinds only) the synthetic-workflow config used to
#                 reproduce the frozen Xopt trajectory without a solver env —
#                 objective names (shaped ``S(m,n)_<freq>``), VOCS variables, the
#                 xopt block, and (gp sweep) the sweep grid.
#   checkable   : human note on what is numerically checkable vs reachability
#
# DRY_RUN.txt markers are intentionally NOT frozen: the module layer has each
# module append its own dry-run block, so an assembled chain yields a combined
# multi-block marker that differs by design from the old single-block text.
# Per the plan's numeric-equivalence contract, equivalence is checked on the
# extracted tables + digests, not on marker bytes.
# --------------------------------------------------------------------------- #

EXAMPLES = {
    's3p_sweep': {
        'kind': 'sweep',
        'yaml': 's3p_sweep.yaml',
        'files': {
            's3p_sweep_output.txt': ('s3p_sweep_output.txt', 'table'),
        },
        'digests': {},
        'checkable': ('NUMERIC: swept input grid (cornercut x rcorner2) and '
                      'Frequency column. Solver outputs absent (dry-run), so '
                      'the S-parameter values are reachability-only.'),
    },
    's3p_sweep_no_s3p_file': {
        'kind': 'sweep',
        'yaml': 's3p_sweep_no_s3p_file.yaml',
        'files': {
            's3p_sweep_output.txt': ('s3p_sweep_output.txt', 'table'),
        },
        'digests': {},
        'checkable': ('NUMERIC: swept input grid + Frequency column, with the '
                      'ACE3P settings supplied inline. Reachability for the '
                      '(absent) solver step.'),
    },
    't3p_sweep': {
        'kind': 'sweep',
        'yaml': 't3p_sweep.yaml',
        'files': {
            't3p_sweep_output.txt': ('t3p_sweep_output.txt', 'table'),
        },
        'digests': {},
        'checkable': ('NUMERIC: swept input grid (cell_radius x iris_radius) '
                      "and the 's' wake-coordinate column. T3P is a "
                      'time-domain solver, so the table goes long-format over '
                      "'s' the way an S3P table goes over Frequency; the "
                      'wakefield outputs (loss_factor, W) are NaN under dry-run '
                      '-> reachability-only.'),
    },
    'omega3p_sweep': {
        'kind': 'sweep',
        'yaml': 'omega3p_sweep.yaml',
        'files': {
            'omega3p_sweep_output.txt': ('omega3p_sweep_output.txt', 'table'),
        },
        'digests': {},
        'checkable': ('NUMERIC: swept input grid (cav_radius x ellipticity). '
                      'Output columns (R/Q, Mode_freq, E_max, loc_*) are NaN '
                      'without acdtool -> reachability-only.'),
    },
    'omega3p_ace3p_param_sweep': {
        'kind': 'sweep',
        'yaml': 'omega3p_ace3p_param_sweep.yaml',
        'files': {
            'omega3p_sweep_output.txt': ('omega3p_sweep_output.txt', 'table'),
        },
        'digests': {},
        'checkable': ('NUMERIC: the swept cubit grid PLUS the ACE3P Sigma list '
                      '[5.8e7, 1.04e7] which is treated as a third sweep axis '
                      '(so 4x4x2 = 32 runs, workdir names carry the Sigma '
                      'suffix). Solver outputs reachability-only.'),
    },
    'track3p_particle_weight': {
        'kind': 'single',
        'yaml': 'track3p_particle_weight.yaml',
        'files': {},
        'digests': {
            'weighted_particles.digest.json':
                'lume-ace3p_track3p_workdir/track3p_particles_weighted.txt',
        },
        'checkable': ('NUMERIC (real compute): the field-emission ParticleWeight '
                      'and all track columns of the filtered/binned output. '
                      'Frozen as a per-column numeric digest.'),
    },
    'geant4_track3p_beta': {
        'kind': 'sweep',
        'yaml': 'geant4_track3p_beta.yaml',
        'files': {
            'beta_sweep_output': ('geant4_beta_sweep_output', 'table'),
        },
        'digests': {
            'particles_beta40.digest.json':
                'lume-ace3p_geant4_workdir_40.0/particles.data',
            'particles_beta60.digest.json':
                'lume-ace3p_geant4_workdir_60.0/particles.data',
        },
        'checkable': ('NUMERIC (real compute): the Geant4 source file '
                      '(particles.data) generated by the Particles pre-step for '
                      'each beta, frozen as per-column digests; the swept beta '
                      'grid. The Geant4 solver itself is dry-run.'),
    },
    's3p_optimization': {
        'kind': 'xopt_scalar',
        'yaml': 's3p_optimization.yaml',
        'files': {
            'sim_output.txt': ('sim_output.txt', 'table'),
        },
        'digests': {},
        'synth': {
            'objectives': ['S(0,0)_12000000000.0'],
            'variables': {'cornercut': [14, 17], 'rcorner1': [0.5, 2.5]},
            'xopt': {'generator': 'NelderMeadGenerator', 'num_random': 0,
                     'num_step': 25},
        },
        'checkable': ('NUMERIC (synthetic workflow, seeded): full NelderMead '
                      'trajectory (cornercut, rcorner1, objective). '
                      'Seed-reproducible and cluster-independent.'),
    },
}


# Examples that are intentionally NOT frozen as numeric fixtures, with the
# reason. Recorded so the baseline is honest about coverage gaps.
NOT_FROZEN = {
    's3p_mf_optimization': (
        'MultiFidelity cost-budget path divides by wall-clock xopt_runtime and '
        'loops on alotted_time, so the trajectory length and values are '
        'timing-dependent: reachability-only, not numerically checkable. The '
        'example YAML is on the current workflow:/mode: schema (nested '
        'input_parameters).'),
    'UCB_Example': (
        'Non-runnable legacy reference now kept under examples/incomplete/ (see '
        'its README): missing load.jou/load.s3p geometry, legacy schema, and it '
        'declares three objectives while xopt 3.0.0\'s UpperConfidenceBound '
        'generator rejects multi-objective VOCS. Not frozen as a baseline.'),
    's3p_bayesian_sweep': (
        'DE-REGISTERED 2026-08. Was a numeric baseline (10x10 GP posterior-mean '
        'sweep + exploration trajectory) but it drives a real botorch '
        'BayesianExploration fit, so it took minutes-to-hours and was therefore '
        'never actually run — it gated nothing. The generic gp_parameter_sweep '
        'plumbing (tensor grid, VOCS build, shared table writer) is covered by '
        'the fast tests; what is no longer checked is that botorch\'s GP '
        'posterior-mean numerics still match the frozen fixture, which is an '
        'upstream concern, not ours.'),
    'MOBO_ExpectedHypervolume_Example': (
        'DE-REGISTERED 2026-08. Botorch MOBO/EHVI fit: slow (minutes+) AND a '
        'known nondeterministic flake, so it was both unrunnable in practice and '
        'unreliable when run. The shipped YAML is itself a non-runnable legacy '
        'reference under examples/incomplete/.'),
}


def example_root(name, meta):
    """Directory holding the example's YAML — most live in examples/<name>/.
    A `stage_dir` override (e.g. the non-runnable MOBO reference under
    examples/incomplete/) points elsewhere."""
    return os.path.join(EXAMPLES_DIR, meta.get('stage_dir', name))


def stage_dir_for(name, meta):
    """The example_dir argument the producers expect (relative to examples/).
    Defaults to the example name; `stage_dir` overrides it (e.g. 'incomplete')."""
    return meta.get('stage_dir', name)


def resolve_one(workdir, pattern):
    """Return the single file matching `pattern` (glob) under `workdir`, or
    raise if zero / ambiguous in a way that would make the fixture unstable."""
    matches = sorted(glob.glob(os.path.join(workdir, pattern)))
    if not matches:
        raise FileNotFoundError(
            f'expected output {pattern!r} not produced under {workdir}')
    return matches[0]


def produce(name, meta):
    """Run the example and return the workdir it produced outputs in."""
    producer = PRODUCERS[meta['kind']]
    return producer(stage_dir_for(name, meta), meta['yaml'], meta)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def dump_json(path, obj):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write('\n')
