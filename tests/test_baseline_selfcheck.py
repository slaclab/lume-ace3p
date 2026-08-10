"""Baseline self-check — the fast default correctness gate.

Re-runs each frozen example through the **declarative module/mode path** and
confirms it still reproduces its own `tests/baseline/` fixtures within tolerance.
This is the numeric-equivalence gate the refactor relies on: the fixtures were
frozen from the pre-refactor code (Phase 0.5), and the new code path must still
match them on the numerically-checkable quantities.

Every registered example runs fast (dry-run sweeps + pure-Python particle
weighting + a NelderMead trajectory). The two botorch GP-fitting examples
(MOBO/EHVI and the BayesianExploration GP sweep) were **de-registered** in
2026-08: they took minutes-to-hours, one was a known nondeterministic flake, and
so they were never actually run — see `baseline_utils.NOT_FROZEN` for the full
reasoning and what is no longer covered.

Run:  python -m pytest tests/test_baseline_selfcheck.py
or standalone:  python tests/test_baseline_selfcheck.py

Regenerate fixtures (intentionally, from the current code):
    python tests/freeze_baseline.py
"""

import os

import pytest

import baseline_utils as bu


def _check_example(name, meta):
    """Produce the example fresh and compare every fixture to its baseline.
    Returns a list of failure messages (empty == pass)."""
    dest = os.path.join(bu.BASELINE_DIR, name)
    assert os.path.isdir(dest), (
        f'no baseline fixtures for {name!r}; run tests/freeze_baseline.py')

    workdir = bu.produce(name, meta)
    failures = []

    for fixture_name, (pattern, compare_kind) in meta['files'].items():
        baseline_path = os.path.join(dest, fixture_name)
        try:
            produced_path = bu.resolve_one(workdir, pattern)
        except FileNotFoundError as exc:
            failures.append(f'{fixture_name}: {exc}')
            continue
        if compare_kind == 'table':
            ok, msg = bu.compare_tables(baseline_path, produced_path)
        elif compare_kind == 'marker':
            ok, msg = bu.compare_marker(baseline_path, produced_path)
        else:
            ok, msg = False, f'unknown compare kind {compare_kind!r}'
        if not ok:
            failures.append(f'{fixture_name}: {msg}')

    for fixture_name, pattern in meta['digests'].items():
        baseline_digest = bu.load_json(os.path.join(dest, fixture_name))
        try:
            produced_path = bu.resolve_one(workdir, pattern)
        except FileNotFoundError as exc:
            failures.append(f'{fixture_name}: {exc}')
            continue
        ok, msg = bu.compare_digests(baseline_digest,
                                     bu.numeric_digest(produced_path))
        if not ok:
            failures.append(f'{fixture_name}: {msg}')

    return failures


@pytest.mark.parametrize('name', sorted(bu.EXAMPLES))
def test_baseline_matches(name):
    meta = bu.EXAMPLES[name]
    failures = _check_example(name, meta)
    assert not failures, f'{name} baseline mismatch:\n  ' + '\n  '.join(failures)


def test_all_examples_have_fixtures():
    """Every registered example must have a frozen fixture dir with a
    manifest — catches a registry entry added without re-freezing."""
    missing = []
    for name in bu.EXAMPLES:
        manifest = os.path.join(bu.BASELINE_DIR, name, 'manifest.json')
        if not os.path.isfile(manifest):
            missing.append(name)
    assert not missing, (
        'missing fixtures (run tests/freeze_baseline.py): ' + ', '.join(missing))


if __name__ == '__main__':
    passed = 0
    failed = 0
    for name in sorted(bu.EXAMPLES):
        fails = _check_example(name, bu.EXAMPLES[name])
        if fails:
            failed += 1
            print(f'FAIL  {name}')
            for f in fails:
                print(f'        {f}')
        else:
            passed += 1
            print(f'PASS  {name}')
    print(f'\n{passed}/{passed + failed} examples match baseline')
    raise SystemExit(0 if failed == 0 else 1)
