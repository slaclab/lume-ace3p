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
# run_xopt succeed for every objective frequency the shipped configs use.
SYNTH_FREQS = np.array([
    9.424e9, 9.674e9, 11.324e9, 11.424e9, 11.524e9, 12.0e9, 12.424e9,
])


class SyntheticS3PWorkflow:
    """Stand-in for `S3PWorkflow` in `run_xopt`: returns a fixed frequency grid
    and a smooth, input-dependent S-parameter response. Deterministic given the
    input dict, so the optimizer sees signal but never touches Cubit/S3P.

    Mirrors `FakeS3PWorkflow` in `tests/test_run_xopt_compat.py` but with a
    wider frequency grid covering every example's objective frequency."""

    def __init__(self, workflow_dict, input_dict):
        self.input_dict = input_dict

    def run(self):
        x = float(sum(float(v) for v in self.input_dict.values()))
        base = 0.01 * (1.0 + np.cos(SYNTH_FREQS / 1e9 - x))
        return {
            'IndexMap': {},
            'Frequency': SYNTH_FREQS,
            'S(0,0)': base,
            'S(1,1)': base * 0.9,
        }


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
    Only regular files are copied (no nested example subdirs exist today)."""
    src = os.path.join(EXAMPLES_DIR, example_dir)
    tmp = tempfile.mkdtemp(prefix='baseline_')
    for name in os.listdir(src):
        path = os.path.join(src, name)
        if os.path.isfile(path):
            shutil.copy(path, tmp)
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


def _produce_sweep(example_dir, yaml_name):
    """Dry-run a `parameter_sweep` example (omega3p / s3p / geant4)."""
    from lume_ace3p.inputs import load_yaml, build_inputs
    from lume_ace3p.workflow import (S3PWorkflow, Omega3PWorkflow,
                                     Geant4Workflow)
    tmp = _stage_example(example_dir)
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        data = load_yaml(yaml_name)
        wd = data['workflow_parameters']
        wd['dry_run'] = True  # force dry-run regardless of local environment
        inputs = build_inputs(data)
        outd = data.get('output_parameters')
        module = wd['module'].lower()
        if module == 'omega3p':
            Omega3PWorkflow(wd, inputs, outd).run_sweep()
        elif module == 's3p':
            S3PWorkflow(wd, inputs).run_sweep()
        elif module == 'geant4':
            Geant4Workflow(wd, inputs, outd,
                           particle_params=data.get('particle_parameters')
                           ).run_sweep()
        else:
            raise ValueError(f'unhandled sweep module {module!r}')
    finally:
        os.chdir(cwd)
    return tmp


def _produce_particle_weight(example_dir, yaml_name):
    """Run the real `Particles` field-emission weighting (pure Python, no
    solver env needed)."""
    from lume_ace3p.inputs import load_yaml
    from lume_ace3p.particles import Particles
    tmp = _stage_example(example_dir)
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        data = load_yaml(yaml_name)
        wd = data['workflow_parameters']
        Particles(wd.get('particle_input'), data.get('particle_parameters'),
                  output_file=wd.get('particle_output'),
                  workdir=os.getcwd()).run()
    finally:
        os.chdir(cwd)
    return tmp


def _produce_xopt_scalar(example_dir, yaml_name):
    """Drive a `scalar_optimize` example through run_xopt with the synthetic
    solver and a fixed seed."""
    import lume_ace3p.run_xopt as rx
    from lume_ace3p.inputs import load_yaml
    tmp = _stage_example(example_dir)
    cwd = os.getcwd()
    yaml_path = os.path.join(EXAMPLES_DIR, example_dir, yaml_name)
    saved = rx.S3PWorkflow
    rx.S3PWorkflow = SyntheticS3PWorkflow
    seed_all()
    os.chdir(tmp)
    try:
        data = load_yaml(yaml_path)
        voc = data['vocs_parameters']
        voc.setdefault('constraints', {})
        voc.setdefault('observables', [])
        voc.setdefault('constants', {})
        rx.run_xopt(data['workflow_parameters'], voc, data['xopt_parameters'])
    finally:
        os.chdir(cwd)
        rx.S3PWorkflow = saved
    return tmp


def _produce_xopt_gp_sweep(example_dir, yaml_name):
    """Drive a `gp_parameter_sweep` example through run_lf_sweep with the
    synthetic solver and a fixed seed."""
    import lume_ace3p.run_xopt as rx
    from lume_ace3p.inputs import load_yaml
    tmp = _stage_example(example_dir)
    cwd = os.getcwd()
    yaml_path = os.path.join(EXAMPLES_DIR, example_dir, yaml_name)
    saved = rx.S3PWorkflow
    rx.S3PWorkflow = SyntheticS3PWorkflow
    seed_all()
    os.chdir(tmp)
    try:
        data = load_yaml(yaml_path)
        voc = data['vocs_parameters']
        voc.setdefault('constraints', {})
        voc.setdefault('observables', [])
        voc.setdefault('constants', {})
        rx.run_lf_sweep(data['workflow_parameters'], data['sweep_parameters'],
                        voc, data['xopt_parameters'])
    finally:
        os.chdir(cwd)
        rx.S3PWorkflow = saved
    return tmp


PRODUCERS = {
    'sweep': _produce_sweep,
    'particle_weight': _produce_particle_weight,
    'xopt_scalar': _produce_xopt_scalar,
    'xopt_gp_sweep': _produce_xopt_gp_sweep,
}


# --------------------------------------------------------------------------- #
# The example registry — single source of truth.
#
# Each entry:
#   kind        : producer key in PRODUCERS
#   yaml        : YAML filename inside examples/<name>/
#   files       : {fixture_name: (glob_relative_to_workdir, compare_kind)}
#                 compare_kind in {'table', 'marker'}. glob picks ONE file
#                 (first sorted match) so per-run temp paths don't leak in.
#   digests     : {fixture_name: glob} — large numeric arrays frozen as digests
#   checkable   : human note on what is numerically checkable vs reachability
# --------------------------------------------------------------------------- #

EXAMPLES = {
    's3p_sweep': {
        'kind': 'sweep',
        'yaml': 's3p_sweep.yaml',
        'files': {
            's3p_sweep_output.txt': ('s3p_sweep_output.txt', 'table'),
            'dry_run_marker.txt': ('lume-ace3p_s3p_workdir_12.0_4.0/DRY_RUN.txt',
                                    'marker'),
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
            'dry_run_marker.txt': ('lume-ace3p_s3p_workdir_12.0_4.0/DRY_RUN.txt',
                                    'marker'),
        },
        'digests': {},
        'checkable': ('NUMERIC: swept input grid + Frequency column; the marker '
                      'also records the parsed ACE3P Section leaves. '
                      'Reachability for the (absent) solver step.'),
    },
    'omega3p_sweep': {
        'kind': 'sweep',
        'yaml': 'omega3p_sweep.yaml',
        'files': {
            'omega3p_sweep_output.txt': ('omega3p_sweep_output.txt', 'table'),
            'dry_run_marker.txt': (
                'lume-ace3p_omega3p_workdir_90.0_0.5/DRY_RUN.txt', 'marker'),
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
            'dry_run_marker.txt': (
                'lume-ace3p_omega3p_workdir_90.0_0.5_10400000.0/DRY_RUN.txt',
                'marker'),
        },
        'digests': {},
        'checkable': ('NUMERIC: the swept cubit grid PLUS the ACE3P Sigma list '
                      '[5.8e7, 1.04e7] which is treated as a third sweep axis '
                      '(so 4x4x2 = 32 runs, workdir names carry the Sigma '
                      'suffix). The marker records the parsed ACE3P Section '
                      '(SurfaceMaterial/Sigma) leaves. Solver outputs '
                      'reachability-only.'),
    },
    'track3p_particle_weight': {
        'kind': 'particle_weight',
        'yaml': 'track3p_particle_weight.yaml',
        'files': {},
        'digests': {
            'weighted_particles.digest.json': 'track3p_particles_weighted.txt',
        },
        'checkable': ('NUMERIC (real compute): the field-emission ParticleWeight '
                      'and all track columns of the filtered/binned output. '
                      'Frozen as a per-column numeric digest.'),
    },
    'geant4_track3p_beta': {
        'kind': 'sweep',
        'yaml': 'geant4_track3p_beta.yaml',
        'files': {
            'dry_run_marker.txt': (
                'lume-ace3p_geant4_workdir_40.0/DRY_RUN.txt', 'marker'),
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
                      'grid. The Geant4 solver itself is dry-run (reachability: '
                      'marker records input/particle/geometry/output files).'),
    },
    's3p_optimization': {
        'kind': 'xopt_scalar',
        'yaml': 's3p_optimization.yaml',
        'files': {
            'sim_output.txt': ('sim_output.txt', 'table'),
        },
        'digests': {},
        'checkable': ('NUMERIC (synthetic solver, seeded): full NelderMead '
                      'trajectory (cornercut, rcorner1, objective). '
                      'Seed-reproducible and cluster-independent.'),
    },
    's3p_bayesian_sweep': {
        'kind': 'xopt_gp_sweep',
        'yaml': 's3p_bayesian_sweep.yaml',
        'files': {
            'sweep_output.txt': ('sweep_output.txt', 'table'),
            'sim_output.txt': ('sim_output.txt', 'table'),
        },
        'digests': {},
        'checkable': ('NUMERIC (synthetic solver, seeded): the 10x10 GP '
                      'posterior-mean sweep table and the exploration '
                      'trajectory. Seed-reproducible.'),
    },
    'MOBO_ExpectedHypervolume_Example': {
        'kind': 'xopt_scalar',
        'yaml': 'MOBO_ExpectedHypervolume_Example.yaml',
        'root_yaml': True,
        'files': {
            'sim_output.txt': ('sim_output.txt', 'table'),
        },
        'digests': {},
        'checkable': ('NUMERIC (synthetic solver, seeded): the MOBO/EHVI '
                      'trajectory (R1, L1, r10, three S(0,0) objectives). '
                      'Seed-reproducible.'),
    },
}


# Examples that are intentionally NOT frozen as numeric fixtures, with the
# reason. Recorded so the baseline is honest about coverage gaps.
NOT_FROZEN = {
    's3p_mf_optimization': (
        'MultiFidelity cost-budget path divides by wall-clock xopt_runtime and '
        'loops on alotted_time, so the trajectory length and values are '
        'timing-dependent: reachability-only, not numerically checkable. Its '
        'generator construction/stepping is smoke-tested in '
        'tests/test_run_xopt_compat.py::test_multifidelity.'),
    'UCB_Example': (
        'The shipped UCB_Example.yaml declares three objectives, but xopt '
        "3.0.0's UpperConfidenceBoundGenerator rejects multi-objective VOCS "
        '(VOCSError: "this generator does not support multi-objective '
        'optimization"). The shipped config is not runnable as-is under the '
        'pinned xopt; frozen as a known-error, not a numeric baseline.'),
}


def example_root(name, meta):
    """Directory holding the example's YAML — most live in examples/<name>/,
    the two MOBO/UCB examples sit at the examples/ root."""
    if meta.get('root_yaml'):
        return EXAMPLES_DIR
    return os.path.join(EXAMPLES_DIR, name)


def stage_dir_for(name, meta):
    """The example_dir argument the producers expect (relative to examples/).
    Root-level YAMLs stage from examples/ itself ('.')."""
    return '.' if meta.get('root_yaml') else name


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
    return producer(stage_dir_for(name, meta), meta['yaml'])


def load_json(path):
    with open(path) as f:
        return json.load(f)


def dump_json(path, obj):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write('\n')
