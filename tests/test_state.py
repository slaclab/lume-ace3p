"""The per-evaluation completion manifest (Phase 3 of
``plans/evaluation_isolation_resume_plan.md``).

Phase 3 adds a record and reads nothing back: every evaluation writes
``lume_ace3p_state.json`` into its workdir, updated after each module, and Phase 4
is what resumes from it. So what these tests pin is that the record is *true* and
that it is written *incrementally* — a manifest that only appears on success would
describe exactly the runs that do not need it.

1. **A completed run records its chain and its results.** The ``modules`` list is
   in DAG order and the ``outputs`` are the ones ``evaluate`` returned.
2. **A failed run records how far it got.** The failing module is ``failed`` with
   its error, and the modules after it are *absent* — which is what makes the
   file partial, and partial is the feature.
3. **``config_hash`` covers the resolved configuration and nothing else.** It
   moves for a module config, an input value or an ``output_parameters`` change,
   and does not move for ``paths``, ``dry_run``, or a YAML comment — the last
   three being the things that must not invalidate a half-finished campaign.
4. **``verify`` answers "is the output still there".** ``False`` for a results
   directory that was deleted, ``None`` where a module genuinely cannot tell.
5. **The acdtool mutating case is ``None``, permanently.** ``postprocess
   transwake`` overwrites ``wakefield.out``, so that file's presence is not
   evidence the step ran; treating it as evidence is defect 7 of
   ``plans/acdtool_rework_plan.md`` reintroduced by the resume feature.
6. **Manifests are not baseline artifacts** — excluded explicitly, since they
   carry timestamps and absolute paths.
"""

import json
import os

import numpy as np
import pytest

import baseline_utils as bu
from lume_ace3p import state
from lume_ace3p.inputs import WorkflowInputs, load_yaml
from lume_ace3p.modules import (
    AcdtoolModule, CubitModule, Geant4Module, MeshSourceModule, Omega3PModule,
    ParticlesModule, RunContext, S3PModule, T3PModule, TRACK3P_PARTICLES,
)
from lume_ace3p.workflow_graph import Workflow


TRACK3P_SAMPLE = os.path.join(bu.EXAMPLES_DIR, 'assets',
                              'sample_track3p_particles.txt')


def _write(path, text=''):
    """Create ``path`` (and its parents) holding ``text``."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as file:
        file.write(text)
    return path


def _particles_workflow(root, betas, **params):
    """A ``track3p_source -> particles`` chain over β, rooted at ``root``.

    Declared with ``particles`` **first** so the DAG has something to reorder: the
    manifest's module order is only worth asserting if the YAML order differs from
    it. The field-emission weighting is pure Python, so every point produces real
    numbers with no ACE3P binary — which is what makes the recorded ``outputs``
    worth comparing."""
    particles = {'module': 'particles', 'impact_order': 1, 'impact_face_id': 6,
                 'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
                 'beta_input': 'beta', 'output_format': 'geant4',
                 'output': 'particles.data'}
    particles.update(params.pop('particles', {}))
    output_spec = params.pop('output_spec', None) or {
        'weight': {'module': 'particles', 'quantity': 'total_weight'},
        'count': {'module': 'particles', 'quantity': 'count'}}
    return Workflow(
        [particles, {'module': 'track3p_source', 'file': TRACK3P_SAMPLE}],
        workflow_params={'workdir': str(root / 'wd'), 'workdir_mode': 'indexed',
                         'dry_run': True, **params},
        inputs=WorkflowInputs(particles={'beta': np.array(betas)}),
        output_spec=output_spec)


# --------------------------------------------------------------------------- #
# 1. A completed run records its chain and its results
# --------------------------------------------------------------------------- #


def test_a_completed_run_records_the_dag_order_and_the_outputs(tmp_path):
    """The manifest of a finished evaluation: schema, point, chain in DAG order,
    and outputs equal to what ``evaluate`` returned.

    The YAML lists ``particles`` before ``track3p_source``; the manifest records
    the order the chain actually *ran* in, which is the DAG's."""
    wf = _particles_workflow(tmp_path, [40.0])
    outputs, ctx = wf.evaluate([40.0])

    recorded = state.read_state(ctx.workdir)
    assert recorded is not None
    assert recorded['schema'] == state.SCHEMA
    assert recorded['point'] == {'axes': {'beta': 40.0}}
    assert recorded['workdir'] == os.path.abspath(ctx.workdir)
    assert [entry['name'] for entry in recorded['modules']] == [
        'track3p_source', 'particles']
    assert [m.name for m in ctx.modules] == ['track3p_source', 'particles']
    assert {entry['status'] for entry in recorded['modules']} == {'complete'}

    assert set(recorded['outputs']) == set(outputs)
    assert recorded['outputs']['weight'] == pytest.approx(outputs['weight'])
    assert recorded['outputs']['count'] == outputs['count']


def test_a_completed_run_records_each_module_s_artifacts(tmp_path):
    """Each entry carries what that module produced, workdir-relative — so the
    manifest describes the directory it sits in rather than the machine that
    wrote it."""
    wf = _particles_workflow(tmp_path, [40.0])
    _outputs, ctx = wf.evaluate([40.0])
    recorded = state.read_state(ctx.workdir)

    entries = {entry['name']: entry for entry in recorded['modules']}
    assert entries['track3p_source']['artifacts'] == {
        'track3p_particles': 'sample_track3p_particles.txt'}
    assert entries['particles']['artifacts'] == {
        'particle_source': 'particles.data'}
    assert not any(os.path.isabs(path) for entry in recorded['modules']
                   for path in entry.get('artifacts', {}).values())


def test_a_solver_records_the_results_directory_it_resolved(tmp_path):
    """``job_name`` is the results directory the solver resolved — the value
    acdtool's positional commands are handed, and what a later reader needs to
    find the output again. Recorded even under dry-run, where it comes from the
    declared override or the documented default."""
    (tmp_path / 'x.jou').write_text('## journal\nexport genesis "m.gen"\n')
    (tmp_path / 'x.omega3p').write_text('ModelInfo : {}\n')
    wf = Workflow(
        [{'module': 'cubit', 'journal': str(tmp_path / 'x.jou')},
         {'module': 'omega3p', 'input': str(tmp_path / 'x.omega3p'),
          'results_dir': 'run17'}],
        workflow_params={'workdir': str(tmp_path / 'wd'), 'dry_run': True},
        inputs=WorkflowInputs(), output_spec={})
    _outputs, ctx = wf.evaluate()

    entries = {e['name']: e for e in state.read_state(ctx.workdir)['modules']}
    assert entries['omega3p']['job_name'] == 'run17'
    assert 'job_name' not in entries['cubit']


def test_a_sweep_writes_one_manifest_per_point(tmp_path):
    """Every point gets its own manifest under its own workdir, each naming its
    own axis values — the per-point identity a resume keys on."""
    from lume_ace3p import modes

    wf = _particles_workflow(tmp_path, [40.0, 50.0, 60.0])
    modes.parameter_sweep(wf)

    for index, beta in enumerate([40.0, 50.0, 60.0]):
        recorded = state.read_state(wf.point_workdir(index))
        assert recorded['point'] == {'axes': {'beta': beta}}
        assert {e['status'] for e in recorded['modules']} == {'complete'}


def test_write_state_leaves_no_temporary_and_reports_a_foreign_schema(tmp_path):
    """The manifest is replaced atomically (an interrupted write must not leave
    the half-written file where a resume would read it), and a manifest of an
    unknown schema reads as *no state* — which means "run this point" rather than
    a misread."""
    path = state.write_state(str(tmp_path), state.new_state(config_hash='x'))
    assert os.path.basename(path) == state.STATE_FILE
    assert sorted(os.listdir(tmp_path)) == [state.STATE_FILE]
    assert state.read_state(str(tmp_path))['config_hash'] == 'x'

    with open(path) as file:
        raw = json.load(file)
    raw['schema'] = state.SCHEMA + 1
    with open(path, 'w') as file:
        json.dump(raw, file)
    assert state.read_state(str(tmp_path)) is None

    with open(path, 'w') as file:
        file.write('{not json')
    assert state.read_state(str(tmp_path)) is None
    assert state.read_state(str(tmp_path / 'nowhere')) is None


# --------------------------------------------------------------------------- #
# 2. A failed run records how far it got
# --------------------------------------------------------------------------- #


def test_a_failing_middle_module_is_recorded_and_the_later_ones_are_absent(
        tmp_path, monkeypatch):
    """The partial manifest, which is the whole reason it is written after each
    module rather than once at the end: the module that raised is ``failed`` with
    its message, the one before it is ``complete``, and the one after it never
    ran and says nothing.

    A module absent from the list is deliberately distinct from a ``failed`` one —
    Phase 4 resumes from the first non-complete module, and "never attempted" and
    "attempted and broke" are both non-complete for that purpose but only one of
    them is worth reporting to a user."""
    def boom(self, ctx):
        raise RuntimeError('the weighting fell over')

    monkeypatch.setattr(ParticlesModule, 'run', boom)
    wf = Workflow(
        [{'module': 'track3p_source', 'file': TRACK3P_SAMPLE},
         {'module': 'particles', 'beta': [40.0], 'num_bins': 1},
         {'module': 'geant4', 'geant4_input': 'never_read.geant4'}],
        workflow_params={'workdir': str(tmp_path / 'wd'), 'dry_run': True},
        inputs=WorkflowInputs(), output_spec={})

    with pytest.raises(RuntimeError, match='fell over'):
        wf.evaluate()

    recorded = state.read_state(str(tmp_path / 'wd'))
    statuses = [(e['name'], e['status']) for e in recorded['modules']]
    assert statuses == [('track3p_source', 'complete'), ('particles', 'failed')]
    assert recorded['modules'][1]['error'] == (
        'RuntimeError: the weighting fell over')
    # Nothing was extracted, so nothing is claimed.
    assert recorded['outputs'] == {}


def test_a_manifest_exists_before_the_first_module_runs(tmp_path, monkeypatch):
    """A point killed inside module 0 still leaves its identity and config hash
    behind, which is what tells a resume the workdir belongs to *this* study."""
    def boom(self, ctx):
        raise RuntimeError('died in the first step')

    monkeypatch.setattr(MeshSourceModule, 'run', boom)
    wf = Workflow(
        [{'module': 'mesh', 'file': 'nowhere.gen'}],
        workflow_params={'workdir': str(tmp_path / 'wd'), 'dry_run': True},
        inputs=WorkflowInputs(cubit={'radius': 3.0}), output_spec={})
    with pytest.raises(RuntimeError):
        wf.evaluate()

    recorded = state.read_state(str(tmp_path / 'wd'))
    assert recorded['config_hash'].startswith('sha256:')
    assert recorded['point'] == {'axes': {'radius': 3.0}}
    assert [e['status'] for e in recorded['modules']] == ['failed']


# --------------------------------------------------------------------------- #
# 3. config_hash covers the resolved configuration and nothing else
# --------------------------------------------------------------------------- #


def _run_hash(root, betas=(40.0,), **params):
    """The ``config_hash`` a real evaluation records under ``root``."""
    wf = _particles_workflow(root, list(betas), **params)
    _outputs, ctx = wf.evaluate([betas[0]])
    return state.read_state(ctx.workdir)['config_hash']


def test_config_hash_is_stable_across_two_identical_runs(tmp_path):
    """The same configuration run twice, in two different directories, hashes the
    same — the property every clause below is a difference against."""
    first = _run_hash(tmp_path / 'a')
    assert first.startswith('sha256:')
    assert first == _run_hash(tmp_path / 'b')


def test_config_hash_changes_when_a_module_config_changes(tmp_path):
    """The work function is a module config value, so it changes the answer and
    must therefore change the hash."""
    assert (_run_hash(tmp_path / 'a')
            != _run_hash(tmp_path / 'b',
                         particles={'work_function': 4.6}))


def test_config_hash_changes_when_an_input_value_changes(tmp_path):
    """The hash is over the *materialized* point, so two grid points of one sweep
    hash differently — which is what stops a resumed point from being matched
    against a neighbour's workdir."""
    assert _run_hash(tmp_path / 'a', betas=(40.0,)) != _run_hash(tmp_path / 'b',
                                                                betas=(50.0,))


def test_config_hash_changes_when_an_output_parameter_changes(tmp_path):
    """A point whose recorded outputs no longer answer the question being asked
    has to be re-extracted, so the output spec is in the hash."""
    assert (_run_hash(tmp_path / 'a')
            != _run_hash(tmp_path / 'b',
                         output_spec={'weight': {'module': 'particles',
                                                 'quantity': 'total_weight'}}))


def test_config_hash_ignores_paths(tmp_path):
    """Site-specific and deliberately excluded: the same workdir must resume on a
    different machine, where every executable path differs."""
    assert (_run_hash(tmp_path / 'a', paths={'ace3p': '/opt/ace3p/'})
            == _run_hash(tmp_path / 'b', paths={'ace3p': '/scratch/build/'}))


def test_config_hash_ignores_dry_run(tmp_path):
    """``dry_run`` says how to run, not what the answer is. (This chain runs for
    real either way — the weighting is pure Python — so both hashes come from a
    genuinely completed evaluation.)"""
    assert (_run_hash(tmp_path / 'a', dry_run=True)
            == _run_hash(tmp_path / 'b', dry_run=False))


_YAML = """\
workflow :
  - module : cubit
    journal : 'pillbox.jou'
  - module : omega3p
    input : 'pillbox.omega3p'

input_parameters :
  cubit :
    cav_radius : 100.0

output_parameters :
  'f0' : {module: omega3p, quantity: Frequency, at: {mode: 0}}
"""

_YAML_COMMENTED = """\
# A cavity eigensolve.
workflow :
  - module : cubit          # the mesher
    journal : 'pillbox.jou'
  - module : omega3p
    input : 'pillbox.omega3p'

input_parameters :
  cubit :
    # swept in the sibling config
    cav_radius : 100.0

output_parameters :
  'f0' : {module: omega3p, quantity: Frequency, at: {mode: 0}}   # the mode
"""


def test_config_hash_ignores_yaml_comments(tmp_path):
    """The hash is taken over parsed values, so documenting a config does not
    invalidate a campaign that is already half-run."""
    def hash_of(text, name):
        path = tmp_path / name
        path.write_text(text)
        wf = Workflow.from_config(load_yaml(str(path)))
        return state.config_hash(wf.entries, wf.inputs, wf.output_spec)

    assert (hash_of(_YAML, 'plain.yaml')
            == hash_of(_YAML_COMMENTED, 'commented.yaml'))


def test_config_hash_covers_an_ace3p_leaf_and_its_order(tmp_path):
    """ACE3P sections are order-significant, and the hash keeps that: an entry
    order that the input file's semantics depend on is not a free reordering the
    way a mapping's keys are."""
    from lume_ace3p.ace3p import Section

    def inputs_with(*pairs):
        section = Section()
        for name, value in pairs:
            section.append(name, value)
        return WorkflowInputs(ace3p=section)

    entries = [{'module': 'omega3p', 'input': 'x.omega3p'}]
    first = state.config_hash(entries, inputs_with(('A', '1'), ('B', '2')), {})
    swapped = state.config_hash(entries, inputs_with(('B', '2'), ('A', '1')), {})
    changed = state.config_hash(entries, inputs_with(('A', '9'), ('B', '2')), {})
    assert first != swapped
    assert first != changed


def test_config_hash_ignores_the_key_order_of_a_module_entry():
    """A module entry is a mapping, so its key order carries nothing — only the
    ``workflow:`` list's own order does."""
    a = state.config_hash([{'module': 's3p', 'input': 'x.s3p', 'tasks': 4}],
                          WorkflowInputs(), {})
    b = state.config_hash([{'tasks': 4, 'input': 'x.s3p', 'module': 's3p'}],
                          WorkflowInputs(), {})
    reordered = state.config_hash(
        [{'module': 'cubit', 'journal': 'x.jou'},
         {'module': 's3p', 'input': 'x.s3p'}], WorkflowInputs(), {})
    swapped = state.config_hash(
        [{'module': 's3p', 'input': 'x.s3p'},
         {'module': 'cubit', 'journal': 'x.jou'}], WorkflowInputs(), {})
    assert a == b
    assert reordered != swapped


# --------------------------------------------------------------------------- #
# 4. verify answers "is the output still there"
# --------------------------------------------------------------------------- #


def _ctx(tmp_path, **kwargs):
    return RunContext(str(tmp_path), **kwargs)


def test_omega3p_verify_fails_for_a_deleted_results_directory(tmp_path):
    """The case design decision 2 exists for: the manifest says the solve
    completed, but its results were deleted (or the workdir was copied without
    them), so the module says the output is gone and Phase 4 re-runs it."""
    module = Omega3PModule({'input': str(tmp_path / 'x.omega3p')})
    (tmp_path / 'x.omega3p').write_text('ModelInfo : {}\n')
    ctx = _ctx(tmp_path)

    assert module.verify(ctx) is False
    results = _write(str(tmp_path / 'omega3p_results' / 'omega3p.out'), 'Mode : {}\n')
    assert module.verify(ctx) is True
    os.remove(results)
    assert module.verify(ctx) is False


def test_s3p_verify_checks_the_magnitudes_file(tmp_path):
    """``Reflection.out`` and not ``SParameter.out``: older ACE3P builds write no
    complex S-parameters and ``S3P.output_parser`` only warns about that, so a run
    missing it still produced results."""
    module = S3PModule({'input': str(tmp_path / 'x.s3p')})
    (tmp_path / 'x.s3p').write_text('ModelInfo : {}\n')
    ctx = _ctx(tmp_path)

    assert module.verify(ctx) is False
    _write(str(tmp_path / 's3p_results' / 'Reflection.out'), '#Frequency[Hz]\n')
    assert module.verify(ctx) is True


_T3P_INPUT = """\
Monitor :
{
  Type : WakeField
  Name : wakefield
}
Monitor :
{
  Type : Power
  Name : inputPower
}
"""


def test_t3p_verify_requires_every_declared_monitor_s_output(tmp_path):
    """T3P's check reuses the monitor table directly: each declared ``Monitor``
    writes a file named after its own ``Name``, so a results directory missing one
    of them fails — a partially-deleted run is not a complete one."""
    path = tmp_path / 'x.t3p'
    path.write_text(_T3P_INPUT)
    module = T3PModule({'input': str(path)})
    ctx = _ctx(tmp_path)
    output = tmp_path / 't3p_results' / 'OUTPUT'

    assert module.verify(ctx) is False
    _write(str(output / 'wakefield.out'), '# Loss factor = -1.0 V/pC\n')
    assert module.verify(ctx) is False        # inputPower.out still missing
    _write(str(output / 'inputPower.out'), '0.0 0.0\n')
    assert module.verify(ctx) is True


def test_t3p_verify_is_unknown_when_no_monitor_is_declared(tmp_path):
    """A run declaring no readable monitor has nothing whose absence would mean
    anything, so the answer is "cannot tell" rather than "gone"."""
    path = tmp_path / 'bare.t3p'
    path.write_text('ModelInfo : {}\n')
    assert T3PModule({'input': str(path)}).verify(_ctx(tmp_path)) is None


def test_a_solver_verify_honors_results_dir_and_a_jobname_leaf(tmp_path):
    """Where a solver's results live is resolved the same way the wrapper resolves
    it — module ``results_dir:`` first, then a ``JobName`` leaf in the input file —
    so verification looks in the directory the run actually wrote to."""
    (tmp_path / 'named.omega3p').write_text('JobName : from_input\n')
    from_input = Omega3PModule({'input': str(tmp_path / 'named.omega3p')})
    override = Omega3PModule({'input': str(tmp_path / 'named.omega3p'),
                              'results_dir': 'run17'})
    ctx = _ctx(tmp_path)

    _write(str(tmp_path / 'from_input' / 'omega3p.out'), 'Mode : {}\n')
    assert from_input.verify(ctx) is True
    assert override.verify(ctx) is False
    _write(str(tmp_path / 'run17' / 'omega3p.out'), 'Mode : {}\n')
    assert override.verify(ctx) is True


def test_cubit_verify_names_the_mesh_from_the_journal(tmp_path):
    """The mesh is named by the journal's ``export`` statement, read from the
    journal rather than from ``ctx.artifacts`` — so the question can be answered
    *before* the step would be re-run, which is when a resume asks it."""
    journal = tmp_path / 'cavity.jou'
    journal.write_text('## journal\ncreate brick x 1\n'
                       'export genesis "cavity.gen" overwrite\n')
    module = CubitModule({'journal': str(journal)})
    ctx = _ctx(tmp_path)

    assert module.verify(ctx) is False
    (tmp_path / 'cavity.gen').write_text('mesh')
    assert module.verify(ctx) is True


def test_verify_is_unknown_under_dry_run(tmp_path):
    """A dry-run chain skips the binaries by design and records a nominal mesh
    path nothing wrote, so the absence of an output is not evidence of anything.
    ``particles`` is the exception: it always runs, so it is always checkable."""
    journal = tmp_path / 'cavity.jou'
    journal.write_text('export genesis "cavity.gen" overwrite\n')
    dry = _ctx(tmp_path, dry_run=True)

    assert CubitModule({'journal': str(journal)}).verify(dry) is None
    assert Omega3PModule({'input': str(tmp_path / 'x.omega3p')}).verify(dry) is None
    assert T3PModule({'input': str(tmp_path / 'x.t3p')}).verify(dry) is None
    assert AcdtoolModule({'input': 'x.rfpost'}).verify(dry) is None
    assert Geant4Module({'geant4_input': 'x.geant4'}).verify(dry) is None


def test_particles_verify_checks_its_output_file(tmp_path):
    """Checked under dry-run too, because this step always runs: the Geant4 binary
    is the only thing a dry run skips, so the particle source it consumes is always
    produced."""
    module = ParticlesModule({'beta': [40.0], 'num_bins': 1,
                              'output': 'particles.data'})
    dry = _ctx(tmp_path, dry_run=True)
    assert module.verify(dry) is False
    (tmp_path / 'particles.data').write_text('0 0 0 0 0 1 0 0 1 6\n')
    assert module.verify(dry) is True


def test_particles_verify_derives_the_default_output_name(tmp_path):
    """With no explicit ``output:`` the filename is the one
    :class:`~lume_ace3p.particles.Particles` derives from the Track3P dump, so it
    is answerable only once the upstream source module has recorded that
    artifact."""
    module = ParticlesModule({'beta': [40.0], 'num_bins': 1})
    assert module.verify(_ctx(tmp_path)) is None

    ctx = _ctx(tmp_path)
    ctx.artifacts[TRACK3P_PARTICLES] = str(tmp_path / 'dump.txt')
    assert module.verify(ctx) is False
    (tmp_path / 'dump_modified.txt').write_text('')
    assert module.verify(ctx) is True


def test_geant4_verify_is_unknown_until_its_output_names_are_known(tmp_path):
    """The scoring filenames normally live in the Geant4 input file, which this
    module reads only when it builds its wrapper — so before it has run, only an
    explicit override can name them."""
    unknown = Geant4Module({'geant4_input': 'x.geant4'})
    assert unknown.verify(_ctx(tmp_path)) is None

    named = Geant4Module({'geant4_input': 'x.geant4',
                          'geant4_dose_output': 'dose.txt'})
    assert named.verify(_ctx(tmp_path)) is False
    (tmp_path / 'dose.txt').write_text('0 0 0 1.0\n')
    assert named.verify(_ctx(tmp_path)) is True


def test_a_source_module_verifies_true(tmp_path):
    """Staging is idempotent, so there is nothing for a resume to check: the
    module runs again either way, which is what re-records its artifact."""
    assert MeshSourceModule({'file': 'nowhere.gen'}).verify(_ctx(tmp_path)) is True


# --------------------------------------------------------------------------- #
# 5. The acdtool mutating case is None, permanently
# --------------------------------------------------------------------------- #


def test_a_mutating_acdtool_command_never_verifies_from_its_output_file(tmp_path):
    """⚠️ Defect 7 of ``plans/acdtool_rework_plan.md``, guarded against being
    reintroduced by the resume feature.

    ``postprocess transwake`` writes its result *over*
    ``<jobname>/OUTPUT/wakefield.out`` — the file ``T3PModule`` already wrote and
    parsed. So that file is present whether or not acdtool ever ran, and reading
    its presence as "complete" would skip the transwake step and report T3P's
    **longitudinal** wake as a kick factor: a wrong-but-plausible number, silently.

    ``verify`` must therefore answer ``None`` (cannot tell) even with the file
    sitting right there. Only the manifest's record that acdtool *ran*
    distinguishes the two states. Do not turn this into a presence check."""
    module = AcdtoolModule({'command': 'postprocess transwake',
                            'args': [0.0, 0.0, 0.0, 0.0125]})
    ctx = _ctx(tmp_path)
    _write(str(tmp_path / 't3p_results' / 'OUTPUT' / 'wakefield.out'),
           '# Loss factor = -3.885e-01 V/pC\n')

    assert module.spec.mutates is not None       # the premise of the test
    assert module.verify(ctx) is None


def test_a_non_mutating_acdtool_command_verifies_from_its_output_file(tmp_path):
    """``postprocess rf`` writes its own ``rfpost.out``, which nothing else
    produces — so for it, and only for it, presence *is* evidence."""
    module = AcdtoolModule({'input': 'x.rfpost'})
    ctx = _ctx(tmp_path)

    assert module.spec.mutates is None
    assert module.verify(ctx) is False
    (tmp_path / 'rfpost.out').write_text('[RoverQ]\n')
    assert module.verify(ctx) is True


def test_an_acdtool_command_that_writes_nothing_readable_is_unknown(tmp_path):
    """``volmontomode`` converts field dumps for ParaView and writes no file this
    wrapper knows about, so there is nothing to check."""
    module = AcdtoolModule({'command': 'postprocess volmontomode'})
    assert module.verify(_ctx(tmp_path)) is None


# --------------------------------------------------------------------------- #
# 6. Manifests are not baseline artifacts
# --------------------------------------------------------------------------- #


def test_the_manifest_is_excluded_from_baseline_resolution(tmp_path):
    """Excluded **explicitly**, not by no glob happening to match it: the manifest
    carries timestamps and the absolute workdir it was written in, so it can never
    be stable run-to-run, and it records how a run went rather than what it
    computed."""
    state.write_state(str(tmp_path), state.new_state(config_hash='x'))
    (tmp_path / 'sweep_output.txt').write_text('a\tb\n1\t2\n')

    assert state.STATE_FILE in bu.BASELINE_EXCLUDED
    assert bu.resolve_one(str(tmp_path), '*') == str(tmp_path / 'sweep_output.txt')
    # Even asked for by name.
    with pytest.raises(FileNotFoundError):
        bu.resolve_one(str(tmp_path), state.STATE_FILE)


def test_no_frozen_baseline_pattern_names_the_manifest():
    """No registry entry compares one today either, so the exclusion is a guard
    rather than a repair."""
    named = [(name, fixture, pattern)
             for name, meta in bu.EXAMPLES.items()
             for fixture, (pattern, _kind) in meta['files'].items()
             if os.path.basename(pattern) == state.STATE_FILE]
    assert not named


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
