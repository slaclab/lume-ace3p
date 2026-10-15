"""Per-module hashes decide what a resume reuses (Phase 3 of
``plans/failure_mitigation_plan.md``).

Each manifest module entry records a ``hash`` over the module's entry (less its
launch keys), the input buckets it reads, the contents of its input files and
its upstream modules' hashes. A resume reuses a module only when that hash still
matches, so a change re-runs exactly the modules that depend on it — and a
Cubit-only sweep's meshes are reused by the full workflow that follows it.

The chain tests run real subprocesses through the fake binaries of
``test_resume.py``.
"""

import json
import os

import numpy as np

from lume_ace3p import modes, state
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.modules import CubitModule, build_module
from lume_ace3p.workflow_graph import Workflow

from test_resume import (_JOURNAL_NAME, _invocations, _workflow, posix_only,
                         staged)  # noqa: F401  (fixture)


_CUBIT = {'module': 'cubit', 'journal': _JOURNAL_NAME, 'meshconvert': False}
_OMEGA3P = {'module': 'omega3p', 'input': 'cavity.omega3p'}


def _workdir(root, index=0):
    return str(root / 'wd') + f'_{index}'


def _entries(workdir):
    return {e['name']: e for e in state.read_state(workdir)['modules']}


def _counts(workdir):
    return (_invocations(workdir, 'cubit'), _invocations(workdir, 'omega3p'))


# --------------------------------------------------------------------------- #
# Through the chain
# --------------------------------------------------------------------------- #


@posix_only
def test_every_module_entry_records_its_hash(staged):
    modes.parameter_sweep(_workflow(staged, [100.0]))
    entries = _entries(_workdir(staged))
    assert all(e['hash'].startswith('sha256:') for e in entries.values())
    assert entries['cubit']['hash'] != entries['omega3p']['hash']


@posix_only
def test_a_cubit_only_sweep_is_reused_by_the_full_workflow(staged):
    """§1.4: mesh first, inspect, then solve. The Cubit entry, its inputs and its
    journal are the same in both workflows, so the full run reuses every mesh."""
    radii = [100.0, 101.0]
    modes.parameter_sweep(_workflow(staged, radii, entries=[_CUBIT],
                                    output_spec={}))
    for index in (0, 1):
        assert _counts(_workdir(staged, index)) == (1, 0)

    df = modes.parameter_sweep(_workflow(staged, radii), resume=True)

    assert df['f0'].tolist() == radii
    for index in (0, 1):
        assert _counts(_workdir(staged, index)) == (1, 1)
        assert _entries(_workdir(staged, index))['cubit'].get('resumed') is True


@posix_only
def test_a_launch_key_change_re_runs_nothing(staged):
    """``tasks`` / ``cores`` / ``opts`` size the run, not its answer."""
    modes.parameter_sweep(_workflow(staged, [100.0]))
    resized = _workflow(staged, [100.0], entries=[
        _CUBIT, {**_OMEGA3P, 'tasks': 4, 'cores': 2, 'opts': '--verbose'}])
    df = modes.parameter_sweep(resized, resume=True)

    assert _counts(_workdir(staged)) == (1, 1)
    assert df['f0'].tolist() == [100.0]


@posix_only
def test_a_solver_input_edit_re_runs_only_the_solver(staged, capsys):
    """The YAML is untouched; the input deck's contents changed. Before module
    hashes this was invisible to resume."""
    modes.parameter_sweep(_workflow(staged, [100.0]))
    with open(staged / 'cavity.omega3p', 'a') as file:
        file.write('# edited\n')

    capsys.readouterr()
    modes.parameter_sweep(_workflow(staged, [100.0]), resume=True)

    assert _counts(_workdir(staged)) == (1, 2)
    assert "'omega3p' re-runs" in capsys.readouterr().out


@posix_only
def test_a_journal_edit_re_runs_cubit_and_everything_after_it(staged):
    modes.parameter_sweep(_workflow(staged, [100.0]))
    with open(staged / _JOURNAL_NAME, 'a') as file:
        file.write('## edited\n')

    modes.parameter_sweep(_workflow(staged, [100.0]), resume=True)

    assert _counts(_workdir(staged)) == (2, 2)


@posix_only
def test_a_touched_input_file_is_not_a_change(staged):
    """Contents, not mtimes: a ``touch`` or a copy that keeps the bytes reuses."""
    modes.parameter_sweep(_workflow(staged, [100.0]))
    os.utime(staged / _JOURNAL_NAME, (1.0e9, 1.0e9))
    os.utime(staged / 'cavity.omega3p', (1.0e9, 1.0e9))

    modes.parameter_sweep(_workflow(staged, [100.0]), resume=True)

    assert _counts(_workdir(staged)) == (1, 1)


@posix_only
def test_files_entries_are_hashed_by_path_not_contents(staged):
    """A solver's ``files:`` are data (an SEY table, a field map) and can be
    large, so only the list itself is in the hash: editing one in place is
    reused, while naming a different file re-runs the solver."""
    (staged / 'sey.dat').write_text('1.0 2.0\n')
    (staged / 'sey2.dat').write_text('1.0 2.0\n')
    with_files = lambda name: _workflow(staged, [100.0], entries=[
        _CUBIT, {**_OMEGA3P, 'files': [name]}])
    modes.parameter_sweep(with_files('sey.dat'))

    (staged / 'sey.dat').write_text('3.0 4.0\n')
    modes.parameter_sweep(with_files('sey.dat'), resume=True)
    assert _counts(_workdir(staged)) == (1, 1)

    modes.parameter_sweep(with_files('sey2.dat'), resume=True)
    assert _counts(_workdir(staged)) == (1, 2)


@posix_only
def test_an_output_parameters_change_launches_nothing(staged):
    modes.parameter_sweep(_workflow(staged, [100.0]))
    df = modes.parameter_sweep(_workflow(
        staged, [100.0], output_spec={
            'Q': {'module': 'omega3p', 'quantity': 'QualityFactor',
                  'at': {'mode': 0}}}), resume=True)

    assert _counts(_workdir(staged)) == (1, 1)
    assert df['Q'].tolist() == [5000.0]


@posix_only
def test_a_pre_hash_manifest_resumes_by_config_hash(staged, capsys):
    """A manifest written by 0.7.x has no module hashes. With the same
    configuration it resumes whole, as it always did; with any change it re-runs
    from the start — even a launch-key change, which the old rule could not
    tell apart."""
    def strip_hashes():
        path = os.path.join(_workdir(staged), state.STATE_FILE)
        with open(path) as file:
            recorded = json.load(file)
        for entry in recorded['modules']:
            entry.pop('hash')
        with open(path, 'w') as file:
            json.dump(recorded, file)

    modes.parameter_sweep(_workflow(staged, [100.0]))
    strip_hashes()
    modes.parameter_sweep(_workflow(staged, [100.0]), resume=True)
    assert _counts(_workdir(staged)) == (1, 1)
    # ...and that resume wrote a manifest with hashes.
    assert all('hash' in e for e in _entries(_workdir(staged)).values())

    strip_hashes()
    capsys.readouterr()
    modes.parameter_sweep(_workflow(staged, [100.0], entries=[
        _CUBIT, {**_OMEGA3P, 'tasks': 4}]), resume=True)
    assert 'different configuration' in capsys.readouterr().out
    assert _counts(_workdir(staged)) == (2, 2)


@posix_only
def test_status_names_the_first_module_whose_hash_moved(staged):
    radii = [100.0, 101.0]
    modes.parameter_sweep(_workflow(staged, radii))
    with open(staged / 'cavity.omega3p', 'a') as file:
        file.write('# edited\n')

    df = modes.status(_workflow(staged, radii))
    assert df['status'].tolist() == ['partial', 'partial']
    assert df['modules'].tolist() == ['1/2', '1/2']
    assert df['next'].tolist() == ['omega3p', 'omega3p']

    with open(staged / _JOURNAL_NAME, 'a') as file:
        file.write('## edited\n')
    df = modes.status(_workflow(staged, radii))
    assert df['status'].tolist() == ['stale', 'stale']
    assert df['next'].tolist() == ['cubit', 'cubit']


# --------------------------------------------------------------------------- #
# The hash itself, without running anything
# --------------------------------------------------------------------------- #


def _hashes(entries, **buckets):
    wf = Workflow(entries, workflow_params={'dry_run': True},
                  inputs=WorkflowInputs(**buckets), output_spec={})
    return wf.point_module_hashes()


def test_a_bucket_change_moves_only_the_modules_that_read_it(tmp_path,
                                                            monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'cavity.jou').write_text('export genesis "c.gen"\n')
    sample = os.path.join(os.path.dirname(__file__), '..', 'examples', 'assets',
                          'sample_track3p_particles.txt')
    entries = [{'module': 'cubit', 'journal': 'cavity.jou'},
               {'module': 'track3p_source', 'file': sample},
               {'module': 'field_emission', 'beta_input': 'beta',
                'num_bins': 1},
               {'module': 'geant4', 'geant4_input': 'run.geant4'}]
    base = _hashes(entries, cubit={'r': 1.0}, particles={'beta': 40.0})
    beta = _hashes(entries, cubit={'r': 1.0}, particles={'beta': 50.0})
    radius = _hashes(entries, cubit={'r': 2.0}, particles={'beta': 40.0})

    assert beta['cubit'] == base['cubit']
    assert beta['track3p_source'] == base['track3p_source']
    assert beta['field_emission'] != base['field_emission']
    assert beta['geant4'] != base['geant4']
    # 'cubit' is read by field_emission too (legacy beta), so a radius change
    # moves it; the source module reads no bucket.
    assert radius['cubit'] != base['cubit']
    assert radius['track3p_source'] == base['track3p_source']


def test_an_upstream_hash_moves_every_consumer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'cavity.jou').write_text('export genesis "c.gen"\n')
    (tmp_path / 'cavity.omega3p').write_text('ModelInfo : { File : c.gen }\n')
    entries = [{'module': 'cubit', 'journal': 'cavity.jou'},
               {'module': 'omega3p', 'input': 'cavity.omega3p'},
               {'module': 'acdtool'}]
    before = _hashes(entries)
    (tmp_path / 'cavity.jou').write_text('export genesis "c.gen"\n# edit\n')
    after = _hashes(entries)
    assert all(before[name] != after[name] for name in before)


def test_launch_keys_are_out_of_the_hash_and_other_keys_in():
    inputs = WorkflowInputs()

    def module_hash(module_type, config):
        return build_module(module_type, config=config).module_hash(inputs, {})

    assert (module_hash('omega3p', {'input': 'x', 'ace3p_tasks': 4})
            == module_hash('omega3p', {'input': 'x', 'tasks': 8, 'cores': 2})
            == module_hash('omega3p', {'input': 'x'}))
    assert (module_hash('geant4', {'geant4_input': 'x', 'geant4_threads': 8})
            == module_hash('geant4', {'geant4_input': 'x'}))
    assert (module_hash('acdtool', {'tasks': 2, 'opts': '-v'})
            == module_hash('acdtool', {}))
    assert (module_hash('omega3p', {'input': 'x', 'results_dir': 'r'})
            != module_hash('omega3p', {'input': 'x'}))


def test_a_scoped_block_moves_only_its_module():
    a = CubitModule({'journal': 'cavity.jou'}, name='a')
    b = CubitModule({'journal': 'cavity.jou'}, name='b')
    plain = WorkflowInputs(cubit={'r': 1.0})
    scoped = WorkflowInputs(cubit={'r': 1.0}, scoped={'a': {'cubit': {'r': 2.0}}})
    assert a.module_hash(plain, {}) != a.module_hash(scoped, {})
    assert b.module_hash(plain, {}) == b.module_hash(scoped, {})


def test_file_digest_is_cached_and_follows_contents(tmp_path):
    path = tmp_path / 'deck.txt'
    path.write_text('one')
    first = state.file_digest(str(path))
    assert first == state.file_digest(str(path))
    path.write_text('two')
    os.utime(path, ns=(os.stat(path).st_atime_ns,
                       os.stat(path).st_mtime_ns + 10**9))
    assert state.file_digest(str(path)) != first
    assert state.file_digest(str(tmp_path / 'missing')) is None


def test_point_status_falls_back_to_config_hash_without_module_hashes():
    recorded = {'config_hash': 'h', 'modules': [
        {'name': 'cubit', 'status': 'complete'}]}
    assert state.point_status(recorded, 'h', ['cubit'], {'cubit': 'x'}) == (
        'complete', 1, None)
    assert state.point_status(recorded, 'other', ['cubit'], {'cubit': 'x'}) == (
        'stale', 0, 'cubit')


def test_point_status_with_module_hashes_ignores_config_hash():
    recorded = {'config_hash': 'old', 'modules': [
        {'name': 'cubit', 'status': 'complete', 'hash': 'c'},
        {'name': 'omega3p', 'status': 'complete', 'hash': 'o'}]}
    names = ['cubit', 'omega3p']
    assert state.point_status(recorded, 'new', names,
                              {'cubit': 'c', 'omega3p': 'o'}) == (
        'complete', 2, None)
    assert state.point_status(recorded, 'new', names,
                              {'cubit': 'c', 'omega3p': 'o2'}) == (
        'partial', 1, 'omega3p')
    assert state.point_status(recorded, 'new', names,
                              {'cubit': 'c2', 'omega3p': 'o2'}) == (
        'stale', 0, 'cubit')


def test_point_status_of_an_extraction_error_under_a_new_spec_is_partial():
    """An extraction that failed fails again under the same configuration; an
    edited ``output_parameters`` re-extracts without launching anything."""
    recorded = {'config_hash': 'h', 'error': 'ValueError: no results',
                'modules': [{'name': 'cubit', 'status': 'complete',
                             'hash': 'c'}]}
    assert state.point_status(recorded, 'h', ['cubit'], {'cubit': 'c'}) == (
        'failed', 1, None)
    assert state.point_status(recorded, 'h2', ['cubit'], {'cubit': 'c'}) == (
        'partial', 1, None)


def test_hashing_reads_no_input_point_beyond_its_buckets():
    """The hash is over the materialized point: a swept value is in it."""
    entries = [{'module': 'cubit', 'journal': 'nowhere.jou'}]
    wf = Workflow(entries, workflow_params={'dry_run': True},
                  inputs=WorkflowInputs(cubit={'r': np.array([1.0, 2.0])}),
                  output_spec={})
    assert (wf.point_module_hashes([1.0])['cubit']
            != wf.point_module_hashes([2.0])['cubit'])
