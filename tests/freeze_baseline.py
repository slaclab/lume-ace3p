"""Generate the Phase-0.5 golden baseline fixtures under tests/baseline/.

Run once (from the repo root, on the *current* pre-refactor code) to freeze the
current behavior of every example:

    python tests/freeze_baseline.py

Re-running overwrites the fixtures. `test_baseline_selfcheck.py` then re-runs
the current code and confirms it still matches these fixtures — that guards
against a flaky/nondeterministic capture being used as a reference in later
phases. See docs/workflow_module_refactor_plan.md, Phase 0.5.
"""

import os
import shutil

import baseline_utils as bu


def freeze_one(name, meta):
    dest = os.path.join(bu.BASELINE_DIR, name)
    os.makedirs(dest, exist_ok=True)
    workdir = bu.produce(name, meta)

    frozen = []
    # Verbatim files (tables / markers).
    for fixture_name, (pattern, _kind) in meta['files'].items():
        src = bu.resolve_one(workdir, pattern)
        shutil.copy(src, os.path.join(dest, fixture_name))
        frozen.append(fixture_name)

    # Large numeric arrays -> compact numeric digest JSON.
    for fixture_name, pattern in meta['digests'].items():
        src = bu.resolve_one(workdir, pattern)
        bu.dump_json(os.path.join(dest, fixture_name), bu.numeric_digest(src))
        frozen.append(fixture_name)

    # Per-example metadata so the self-check knows what to compare and readers
    # know what is numerically checkable.
    manifest = {
        'kind': meta['kind'],
        'yaml': meta['yaml'],
        'files': {k: {'pattern': v[0], 'compare': v[1]}
                  for k, v in meta['files'].items()},
        'digests': {k: {'pattern': v} for k, v in meta['digests'].items()},
        'checkable': meta['checkable'],
    }
    bu.dump_json(os.path.join(dest, 'manifest.json'), manifest)
    print(f'  froze {name}: {", ".join(frozen)}')


def main():
    os.makedirs(bu.BASELINE_DIR, exist_ok=True)
    print(f'Freezing baseline fixtures into {bu.BASELINE_DIR}')
    for name, meta in bu.EXAMPLES.items():
        freeze_one(name, meta)
    # Record the intentionally-unfrozen examples alongside the fixtures.
    bu.dump_json(os.path.join(bu.BASELINE_DIR, 'not_frozen.json'), bu.NOT_FROZEN)
    print('Done. Also wrote not_frozen.json (coverage gaps, with reasons).')


if __name__ == '__main__':
    main()
