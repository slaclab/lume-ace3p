"""Resume in the table modes (Phase 4 of
``plans/evaluation_isolation_resume_plan.md``).

Phase 3 wrote the completion manifest; this is what reads it. Under
``mode: {resume: true}`` each point is driven through the manifest in its own
workdir: a point already finished re-runs only its parsers, a half-finished one
restarts at its first non-complete module, and an unstarted one runs normally —
and the result table is the same table either way, which is the whole claim.

**These tests run real subprocesses through fake binaries.** Everything Phase 4
promises is about work *not* being repeated, so a dry-run chain — which launches
nothing to begin with — cannot show it. ``_fake_ace3p`` writes a shell stand-in
for the MPI caller, Cubit, Omega3P, S3P, T3P and acdtool into a directory and
points ``paths:`` at it, so the real command lines run and leave real files. The
mesh carries the swept geometry value and each fake solver reads it back out, so
the numbers in the table are produced by the chain rather than asserted into it —
which is also what makes "a resumed Cubit's mesh is the one the solver used" an
observation rather than an assumption.

What is pinned:

1. **A truncated sweep, resumed, produces the identical table** — and the points
   it inherited did not re-execute (their module logs still hold one invocation
   each and their mesh files' mtimes have not moved).
2. **A failed step is the only one re-run.** The mesh and the solve are not
   repeated, which is the hours this feature exists to save.
3. **A resumed ``t3p`` + ``transwake`` point still reports the kick factor.** The
   test that matters most: acdtool overwrites T3P's own ``wakefield.out``, so a
   resume that skipped it would report the *longitudinal* wake — defect 7 of
   ``plans/acdtool_rework_plan.md``, reintroduced by this feature.
4. **Long-format (S3P) resume keeps its row count and frequency axis**, because a
   resumed module re-runs its parser (design decision 1) rather than being skipped
   outright — without that a resumed S3P point would not know its own axis.
5. **``resume`` is refused under ``workdir_mode: manual``**, naming ``indexed``.
6. **A changed ``config_hash`` re-runs the point**, and says so.
7. **A recorded-complete module whose output is gone is re-run** (``verify``).
8. **``--status`` reports the counts** of a half-finished campaign, and the
   recorded outputs of a resumed point are cross-checked against the re-extracted
   ones.
"""

import json
import os
import shutil
import sys

import numpy as np
import pandas as pd
import pytest

from lume_ace3p import modes, run_lume_ace3p, state
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.workflow_graph import Workflow


posix_only = pytest.mark.skipif(os.name != 'posix',
                                reason='the fake binaries are shell scripts')


# --------------------------------------------------------------------------- #
# A fake ACE3P environment: real subprocesses, no ACE3P.
# --------------------------------------------------------------------------- #

# Drops the rank/CPU flags every wrapper passes and execs the rest, so the real
# command line reaches the fake binary. This is what makes a solver reachable at
# all: with no MPI caller the wrappers emit a command line starting '-n 1 -c 1'.
_MPI = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in
    -n|-c) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
exec "$@"
"""

# Writes the mesh the journal's own export statement names, holding the journal's
# (swept) 'radius' value. Every fake solver below reads that number back out, so
# the mesh is load-bearing rather than a touched file: a resumed point that did
# NOT rebuild its mesh still produces the right solver output only because the
# mesh from the earlier run is still there and still correct.
_CUBIT = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in *.jou|*.jou_copy) journal="$arg" ;; esac
done
mesh=`grep -E '^[ 	]*export' "$journal" | tail -1 | cut -d'"' -f2`
radius=`grep -o 'radius=[^}]*' "$journal" | head -1 | cut -d= -f2`
echo "$radius" > "$mesh"
echo "cubit: wrote $mesh (radius=$radius)"
"""

# 'omega3p <input> [results_dir]' -- writes the KVC-syntax eigensolve output,
# reporting the mesh's radius as the mode frequency.
_OMEGA3P = """#!/bin/sh
dir=${2:-omega3p_results}
radius=`cat *.gen 2>/dev/null | head -1`
mkdir -p "$dir"
{ echo 'Mode : {'
  echo '  ModeIndex : 0'
  echo "  Frequency : $radius"
  echo '  QualityFactor : 5000.0'
  echo '}'
} > "$dir/omega3p.out"
echo "omega3p: wrote $dir/omega3p.out"
"""

# 's3p <input> [results_dir]' -- three swept frequencies, magnitudes scaled by the
# mesh radius so each point's spectrum is its own. Both tables are written, since
# a missing SParameter.out is a warning S3P.output_parser would emit on every run.
_S3P = """#!/bin/sh
dir=${2:-s3p_results}
radius=`cat *.gen 2>/dev/null | head -1`
mkdir -p "$dir"
{ echo '#Index mapping:'
  echo '#          0 : Port 1, Mode 0, Type: TE (cutoff: 1.0e+09 Hz)'
  echo '#Frequency[Hz]          S(0,0)'
  awk -v r="$radius" 'BEGIN { for (i = 1; i <= 3; i++)
        printf "%.8e  %.8e\\n", i * 1.0e9, i * r }'
} > "$dir/Reflection.out"
{ echo '#Frequency[Hz]          S(0,0)'
  awk -v r="$radius" 'BEGIN { for (i = 1; i <= 3; i++)
        printf "%.8e  ( %.8e,  0.00000000e+00)\\n", i * 1.0e9, i * r }'
} > "$dir/SParameter.out"
echo "s3p: wrote $dir/Reflection.out"
"""

# 't3p <input>' -- a longitudinal wake whose loss factor is the mesh radius. T3P
# takes no results-directory argument (see ACE3P.accepts_results_dir_arg), so the
# directory is its documented default plus the OUTPUT subdirectory.
_T3P = """#!/bin/sh
radius=`cat *.gen 2>/dev/null | head -1`
mkdir -p t3p_results/OUTPUT
{ echo "# Loss factor = $radius V/pC"
  echo '# s[m]  W(s)[V/pC]  I_bunch(s)[C/m]'
  echo "0.00  $radius  1.0"
  echo "0.05  $radius  2.0"
} > t3p_results/OUTPUT/wakefield.out
echo "t3p: wrote t3p_results/OUTPUT/wakefield.out"
"""

# 'acdtool meshconvert <mesh>' is a no-op here; 'acdtool postprocess transwake
# <jobname> ...' does what the real one does and what makes defect 7 possible --
# it OVERWRITES the producing T3P run's wakefield.out, turning the longitudinal
# loss factor into a transverse kick factor (here twice the value, so the two are
# never confusable). FAKE_ACDTOOL_FAIL makes it exit without writing.
_ACDTOOL = """#!/bin/sh
if [ -n "$FAKE_ACDTOOL_FAIL" ]; then
  echo "acdtool: deliberate failure" >&2
  exit 1
fi
case "$1 $2" in
  'postprocess transwake')
    out="$3/OUTPUT/wakefield.out"
    old=`grep -m1 factor "$out" | sed 's/.*= *//; s/ *V\\/pC//'`
    kick=`awk -v v="$old" 'BEGIN { printf "%.8e", 2 * v }'`
    { echo "# Kick factor = $kick V/pC"
      echo '# s[m]  W(s)[V/pC]  I_bunch(s)[C/m]'
      echo "0.00  $kick  1.0"
      echo "0.05  $kick  2.0"
    } > "$out"
    echo "acdtool: rewrote $out (kick=$kick)"
    ;;
  *) echo "acdtool: $*" ;;
esac
"""

_FAKES = {'mpirun': _MPI, 'cubit': _CUBIT, 'omega3p': _OMEGA3P, 's3p': _S3P,
          't3p': _T3P, 'acdtool': _ACDTOOL}

# The swept knob lives on an APREPRO line, which is the form Cubit.set_value
# rewrites, so each point's journal (and therefore its mesh) carries its own value.
_JOURNAL = """## a fake cavity
#{radius=100.0}
create sphere radius {radius}
export genesis "cavity.gen" overwrite
"""

_T3P_INPUT = """ModelInfo : {
  File : cavity.gen
}
Monitor :
{
  Type : WakeField
  Name : wakefield
}
"""

_SOLVER_INPUT = 'ModelInfo : {\n  File : cavity.gen\n}\n'


def _fake_ace3p(root):
    """Write the fake binaries under ``root/bin`` and return the ``paths:`` block
    that points every wrapper at them."""
    bin_dir = root / 'bin'
    bin_dir.mkdir(exist_ok=True)
    for name, body in _FAKES.items():
        script = bin_dir / name
        script.write_text(body)
        script.chmod(0o755)
    return {'ace3p': str(bin_dir) + os.sep, 'cubit': str(bin_dir) + os.sep,
            'mpi': str(bin_dir / 'mpirun')}


def _stage(root):
    """Write the journal and solver input files a fake chain reads, and return
    ``root``. Filenames are relative, so tests ``chdir`` here — which is also what
    keeps the wrappers' input-file copying inside the workdirs."""
    root.mkdir(parents=True, exist_ok=True)
    (root / _JOURNAL_NAME).write_text(_JOURNAL)
    (root / 'cavity.omega3p').write_text(_SOLVER_INPUT)
    (root / 'cavity.s3p').write_text(_SOLVER_INPUT)
    (root / 'cavity.t3p').write_text(_T3P_INPUT)
    return root


_JOURNAL_NAME = 'cavity.jou'


def _workflow(root, radii, entries=None, output_spec=None, **params):
    """A real (non-dry-run) chain over the swept ``radius``, in ``indexed``
    workdirs under ``root``, pointed at the fake binaries."""
    return Workflow(
        entries or [
            {'module': 'cubit', 'journal': _JOURNAL_NAME, 'meshconvert': False},
            {'module': 'omega3p', 'input': 'cavity.omega3p'},
        ],
        workflow_params={'workdir': str(root / 'wd'), 'workdir_mode': 'indexed',
                         'dry_run': False, 'paths': _fake_ace3p(root), **params},
        inputs=WorkflowInputs(cubit={'radius': np.array(radii, dtype=float)}),
        output_spec=(output_spec if output_spec is not None
                     else {'f0': {'module': 'omega3p', 'quantity': 'Frequency',
                                  'at': {'mode': 0}}}))


def _alt_root(staged, name='reference'):
    """A second workdir root, for the uninterrupted run a resumed one is compared
    against. Only the workdirs (and the fake binaries) need to be separate: the
    journal and solver inputs are named relatively, as an ACE3P config names them,
    so both runs read the same staged files from the working directory."""
    root = staged / name
    root.mkdir()
    return root


def _invocations(workdir, module_name):
    """How many external commands a module launched in ``workdir``, counted from
    the per-module log the run itself wrote (``$ <command line>`` per invocation).
    Zero when there is no log, which is what a resumed module leaves."""
    path = os.path.join(workdir, module_name + '.log')
    if not os.path.isfile(path):
        return 0
    with open(path) as file:
        return sum(1 for line in file if line.startswith('$ '))


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A staged input directory that is also the working directory."""
    root = _stage(tmp_path)
    monkeypatch.chdir(root)
    return root


# --------------------------------------------------------------------------- #
# 0. The harness itself is worth one test
# --------------------------------------------------------------------------- #


@posix_only
def test_the_fake_chain_really_runs_and_produces_per_point_numbers(staged):
    """The premise every test below rests on: these are real subprocesses whose
    output reaches the table. Each point's mesh carries its own swept radius and
    the solver reports it back, so the ``f0`` column is produced by the chain."""
    df = modes.parameter_sweep(_workflow(staged, [100.0, 101.0]))

    assert df['f0'].tolist() == [100.0, 101.0]
    for index in (0, 1):
        workdir = str(staged / 'wd') + f'_{index}'
        assert os.path.isfile(os.path.join(workdir, 'cavity.gen'))
        assert os.path.isfile(os.path.join(workdir, 'omega3p_results',
                                           'omega3p.out'))
        assert _invocations(workdir, 'cubit') == 1
        assert _invocations(workdir, 'omega3p') == 1


# --------------------------------------------------------------------------- #
# 1. A truncated sweep, resumed, produces the identical table
# --------------------------------------------------------------------------- #


@posix_only
def test_a_resumed_sweep_matches_the_uninterrupted_one(staged):
    """The headline claim: a sweep cut off partway and re-run with ``resume: true``
    produces the same table as one that ran straight through, and does not repeat
    the points it already had.

    The interruption is simulated the way a wall clock makes one — by running the
    first half of the grid and stopping. The points that survive are identified by
    ``config_hash`` over the *materialized* point, so the truncated run's point 3
    and the full run's point 3 are the same point even though the two sweeps have
    different axis lengths.

    Not re-executing is asserted from the run's own artifacts rather than from a
    flag: the module logs still hold exactly one invocation and the mesh files'
    mtimes have not moved."""
    radii = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]

    # A separate workdir root, run straight through, is the answer to compare
    # against.
    reference = _alt_root(staged)
    uninterrupted = modes.parameter_sweep(_workflow(reference, radii))

    # ...and here, the first four points only: the sweep the wall clock killed.
    modes.parameter_sweep(_workflow(staged, radii[:4]))
    done = [str(staged / 'wd') + f'_{i}' for i in range(4)]
    mtimes = {wd: os.path.getmtime(os.path.join(wd, 'cavity.gen'))
              for wd in done}

    resumed = modes.parameter_sweep(_workflow(staged, radii), resume=True)

    pd.testing.assert_frame_equal(uninterrupted, resumed)
    # Worth comparing only because the columns hold real, per-point numbers.
    assert resumed['f0'].tolist() == radii

    for index, workdir in enumerate(done):
        assert _invocations(workdir, 'cubit') == 1, (
            f'point {index} re-ran Cubit')
        assert _invocations(workdir, 'omega3p') == 1, (
            f'point {index} re-ran the solve')
        assert os.path.getmtime(os.path.join(workdir, 'cavity.gen')) == \
            mtimes[workdir]
        entries = {e['name']: e for e in
                   state.read_state(workdir)['modules']}
        assert entries['omega3p']['resumed'] is True

    # The four that had not run did run, and are not marked resumed.
    for index in range(4, 8):
        workdir = str(staged / 'wd') + f'_{index}'
        assert _invocations(workdir, 'omega3p') == 1
        entries = {e['name']: e for e in state.read_state(workdir)['modules']}
        assert 'resumed' not in entries['omega3p']


@posix_only
def test_a_resumed_point_still_reports_its_own_field_index(staged):
    """Design decision 1, directly: a resumed module re-runs its *parser*, so the
    point knows its own results — it is not skipped with an empty solver behind it.

    Omega3P's field index is its parsed mode list, and a module that had been
    skipped outright would have none."""
    workflow = _workflow(staged, [100.0])
    modes.parameter_sweep(workflow)

    resumed = _workflow(staged, [100.0])
    _outputs, ctx = resumed.evaluate([100.0], workdir=resumed.point_workdir(0),
                                     resume=True)
    label, values = resumed.field_index(ctx)
    assert label == 'ModeID'
    assert list(values) == [0]
    assert resumed.field(ctx)['Frequency'] == pytest.approx([100.0])


# --------------------------------------------------------------------------- #
# 2-3. Only the failed step is re-run, and transwake is re-run
# --------------------------------------------------------------------------- #


_TRANSWAKE_ENTRIES = [
    {'module': 'cubit', 'journal': _JOURNAL_NAME, 'meshconvert': False},
    {'module': 't3p', 'input': 'cavity.t3p'},
    {'module': 'acdtool', 'name': 'transwake',
     'command': 'postprocess transwake', 'args': [0.0, 0.0, 0.0, 0.0125]},
]

_KICK = {'K': {'module': 't3p', 'quantity': 'kick_factor'}}


def _transwake_workflow(root, radii):
    return _workflow(root, radii, entries=_TRANSWAKE_ENTRIES,
                     output_spec=_KICK)


@posix_only
def test_a_fixed_step_is_the_only_one_re_run(staged, monkeypatch):
    """⚠️ The two tests that matter most, together: a chain whose acdtool step
    fails, is fixed, and is resumed re-runs **only** acdtool — and reports the
    transverse kick factor, not T3P's longitudinal loss factor.

    Why both at once: ``postprocess transwake`` writes its result *over*
    ``<jobname>/OUTPUT/wakefield.out``, the file T3P already wrote and parsed. So
    after the failed run that file holds the *loss* factor (100.0 here), and a
    resume that wrongly treated acdtool as done would report exactly that — a
    plausible number that is the wrong quantity. Defect 7 of
    ``plans/acdtool_rework_plan.md``, reintroduced by resume. It reports twice the
    value instead, which only the acdtool step produces.

    The failure is *injected* rather than driven by a nonzero exit status because
    the ACE3P wrappers deliberately do not raise on one (see
    ``lume_ace3p.logs.run_logged``): a fake binary exiting 1 would be recorded
    ``complete``, which is a pre-existing property of the wrappers and not
    something resume changes. What is simulated here is a step that aborts — a
    mangled command line, an OSError launching it, a parser that finds nothing."""
    from lume_ace3p.modules import AcdtoolModule

    real_run = AcdtoolModule.run

    def boom(self, ctx, skip_execution=False):
        raise RuntimeError('acdtool step aborted')

    monkeypatch.setattr(AcdtoolModule, 'run', boom)
    with pytest.raises(RuntimeError, match='aborted'):
        modes.parameter_sweep(_transwake_workflow(staged, [100.0]))

    workdir = str(staged / 'wd') + '_0'
    recorded = {e['name']: e['status']
                for e in state.read_state(workdir)['modules']}
    assert recorded == {'cubit': 'complete', 't3p': 'complete',
                        'transwake': 'failed'}
    wake = os.path.join(workdir, 't3p_results', 'OUTPUT', 'wakefield.out')
    assert 'Loss factor' in open(wake).read()     # acdtool never got to it
    mesh_mtime = os.path.getmtime(os.path.join(workdir, 'cavity.gen'))

    monkeypatch.setattr(AcdtoolModule, 'run', real_run)
    resumed = modes.parameter_sweep(_transwake_workflow(staged, [100.0]),
                                    resume=True)

    # Only acdtool ran: the mesh was not rebuilt and the solve was not repeated.
    assert os.path.getmtime(os.path.join(workdir, 'cavity.gen')) == mesh_mtime
    assert _invocations(workdir, 'cubit') == 1
    assert _invocations(workdir, 't3p') == 1
    assert _invocations(workdir, 'transwake') == 1

    # ...and the figure of merit is the transverse one acdtool computed — twice the
    # loss factor the file held a moment ago, so the two can never be confused.
    assert 'Kick factor' in open(wake).read()
    assert set(resumed['K']) == {200.0}


@posix_only
def test_a_completed_transwake_point_resumes_to_the_same_kick_factor(staged):
    """The other half of decision 3: a point that finished *including* acdtool
    reports the same kick factor when resumed, with nothing re-executed.

    T3P's parser runs again and reads a ``wakefield.out`` that acdtool overwrote
    on the earlier run, so it reads the kick factor — which is why a resumed point
    needs no special case and why the acdtool step's re-parse hook is called on the
    resumed path too."""
    first = modes.parameter_sweep(_transwake_workflow(staged, [100.0, 101.0]))
    # T3P is long-format over the wake coordinate 's', so the per-run scalar
    # repeats down each point's block of rows (two samples per point here).
    assert list(first.columns) == ['radius', 's', 'K']
    assert first['K'].tolist() == [200.0, 200.0, 202.0, 202.0]

    resumed = modes.parameter_sweep(_transwake_workflow(staged, [100.0, 101.0]),
                                    resume=True)
    pd.testing.assert_frame_equal(first, resumed)
    for index in (0, 1):
        workdir = str(staged / 'wd') + f'_{index}'
        assert _invocations(workdir, 't3p') == 1
        assert _invocations(workdir, 'transwake') == 1


@posix_only
def test_a_re_run_solver_drags_its_consumers_with_it(staged):
    """⚠️ The sharpest form of defect 7 through resume, and the reason a module
    that has to re-run makes **every later module** re-run too.

    All three steps are recorded complete, but T3P's results have been deleted, so
    ``verify`` sends the solver back to work — and T3P writes the *longitudinal*
    loss factor over ``wakefield.out``. If acdtool were then judged on its own
    record (complete) it would be skipped, and the point would report 100.0 as a
    kick factor: a plausible number that is the wrong quantity, with nothing said.
    Deciding each module independently is exactly that bug, and it is why the
    decision is one-way — once a module has to run, its consumers' inputs have
    changed and their records no longer describe anything.

    ``verify`` cannot save acdtool here: for a command that overwrites its
    producer's file it answers ``None`` on purpose (design decision 3), so the
    ordering rule is the only thing standing between this chain and the wrong
    answer."""
    first = modes.parameter_sweep(_transwake_workflow(staged, [100.0]))
    assert set(first['K']) == {200.0}             # the transverse kick factor

    workdir = str(staged / 'wd') + '_0'
    shutil.rmtree(os.path.join(workdir, 't3p_results'))

    resumed = modes.parameter_sweep(_transwake_workflow(staged, [100.0]),
                                    resume=True)

    assert _invocations(workdir, 't3p') == 2      # the solve was redone...
    assert _invocations(workdir, 'transwake') == 2  # ...and so was its consumer
    assert set(resumed['K']) == {200.0}, (
        'a resumed point reported T3P\'s longitudinal wake as a kick factor')
    assert 'Kick factor' in open(os.path.join(
        workdir, 't3p_results', 'OUTPUT', 'wakefield.out')).read()
    # Cubit is upstream of the module that had to re-run, so it was still reused.
    assert _invocations(workdir, 'cubit') == 1


# --------------------------------------------------------------------------- #
# 4. Long-format (S3P) resume
# --------------------------------------------------------------------------- #


@posix_only
def test_long_format_resume_keeps_its_rows_and_frequency_axis(staged):
    """An S3P sweep goes long-format — one row per (point, frequency) — so its row
    count comes from a *parsed* result. A resumed point that re-runs its parser has
    that axis; one that had merely been skipped would collapse to a single row (or
    to the dry-run sentinel), and the table would silently change shape."""
    entries = [{'module': 'cubit', 'journal': _JOURNAL_NAME,
                'meshconvert': False},
               {'module': 's3p', 'input': 'cavity.s3p'}]
    spec = {'refl': {'module': 's3p', 'quantity': 'S(0,0)'}}
    radii = [100.0, 101.0, 102.0]

    reference = _alt_root(staged)
    uninterrupted = modes.parameter_sweep(
        _workflow(reference, radii, entries=entries, output_spec=spec))

    modes.parameter_sweep(
        _workflow(staged, radii[:1], entries=entries, output_spec=spec))
    resumed = modes.parameter_sweep(
        _workflow(staged, radii, entries=entries, output_spec=spec),
        resume=True)

    assert list(resumed.columns) == ['radius', 'Frequency', 'refl']
    assert len(resumed) == 9                      # 3 points x 3 frequencies
    pd.testing.assert_frame_equal(uninterrupted, resumed)
    assert resumed['Frequency'].tolist() == [1e9, 2e9, 3e9] * 3
    assert _invocations(str(staged / 'wd') + '_0', 's3p') == 1


# --------------------------------------------------------------------------- #
# 5. resume is refused under workdir_mode: manual
# --------------------------------------------------------------------------- #


def _dry_workflow(root, radii, workdir_mode='manual'):
    """A dry-run chain — no binaries — for the tests that never need one to run."""
    return Workflow(
        [{'module': 'cubit', 'journal': _JOURNAL_NAME, 'meshconvert': False},
         {'module': 'omega3p', 'input': 'cavity.omega3p'}],
        workflow_params={'workdir': str(root / 'wd'),
                         'workdir_mode': workdir_mode, 'dry_run': True},
        inputs=WorkflowInputs(cubit={'radius': np.array(radii, dtype=float)}),
        output_spec={})


@pytest.mark.parametrize('mode', [modes.parameter_sweep, modes.single])
def test_resume_under_manual_workdir_mode_raises(tmp_path, mode):
    """Every point shares one workdir under ``manual``, so its single manifest
    describes whichever point ran last — skipping work on the strength of it would
    skip point 5 because point 4 finished. Refused rather than silently wrong, and
    the error names the mode that fixes it (design decision 5)."""
    workflow = _dry_workflow(tmp_path, [100.0])
    with pytest.raises(ValueError, match='indexed'):
        mode(workflow, resume=True)
    # ...and the same workflow is perfectly runnable without resume.
    assert len(mode(workflow)) == 1


def test_run_mode_passes_resume_through(tmp_path):
    """``mode: {resume: true}`` is how a user asks for this, so the dispatch layer
    has to carry it — pinned by the refusal above firing through ``run_mode``."""
    workflow = _dry_workflow(tmp_path, [100.0])
    with pytest.raises(ValueError, match='indexed'):
        modes.run_mode({'type': 'parameter_sweep', 'resume': True}, workflow)
    assert len(modes.run_mode({'type': 'parameter_sweep'}, workflow)) == 1


# --------------------------------------------------------------------------- #
# 6-7. A changed config, and an output that went missing
# --------------------------------------------------------------------------- #


@posix_only
def test_a_changed_configuration_re_runs_the_point_and_says_so(staged, capsys):
    """A workdir written for a different resolved configuration is not this point's
    work, however much its name matches. ``config_hash`` catches it, the point runs
    from the start, and the reason is printed — the user asked to resume and is
    entitled to know why nothing was reused."""
    modes.parameter_sweep(_workflow(staged, [100.0]))
    workdir = str(staged / 'wd') + '_0'
    assert _invocations(workdir, 'omega3p') == 1

    # A different results directory is a different module config, so a different
    # hash — and a different place for the solver to write.
    changed = _workflow(staged, [100.0], entries=[
        {'module': 'cubit', 'journal': _JOURNAL_NAME, 'meshconvert': False},
        {'module': 'omega3p', 'input': 'cavity.omega3p',
         'results_dir': 'elsewhere'}])
    capsys.readouterr()
    df = modes.parameter_sweep(changed, resume=True)

    printed = capsys.readouterr().out
    assert 'different configuration' in printed and 'config_hash' in printed
    assert _invocations(workdir, 'omega3p') == 2
    assert df['f0'].tolist() == [100.0]
    assert os.path.isfile(os.path.join(workdir, 'elsewhere', 'omega3p.out'))


@posix_only
def test_a_module_whose_output_is_gone_is_re_run(staged, capsys):
    """Design decision 2: the manifest is authoritative for "did it run" and the
    module for "is the output still there". A workdir whose results directory was
    deleted (or copied without it) says complete and verifies false, so it is
    re-run with a warning naming what is missing."""
    modes.parameter_sweep(_workflow(staged, [100.0]))
    workdir = str(staged / 'wd') + '_0'
    shutil.rmtree(os.path.join(workdir, 'omega3p_results'))

    capsys.readouterr()
    df = modes.parameter_sweep(_workflow(staged, [100.0]), resume=True)
    printed = capsys.readouterr().out

    assert "'omega3p' is recorded complete" in printed
    assert 'omega3p_results is missing' in printed
    assert _invocations(workdir, 'omega3p') == 2
    # The mesh was still there, so Cubit was not repeated — only the step whose
    # output was gone, and everything after it.
    assert _invocations(workdir, 'cubit') == 1
    assert df['f0'].tolist() == [100.0]


@posix_only
def test_a_resumed_point_warns_when_it_re_extracts_a_different_value(staged,
                                                                    capsys):
    """The free nondeterminism check: the parsers ran again over the same files, so
    the re-extracted outputs must match the recorded ones. Pinned by editing the
    manifest, which is the only way to make an honest chain disagree with itself —
    and the point of the check is precisely to notice when a real one does."""
    modes.parameter_sweep(_workflow(staged, [100.0]))
    workdir = str(staged / 'wd') + '_0'
    path = os.path.join(workdir, state.STATE_FILE)
    with open(path) as file:
        recorded = json.load(file)
    recorded['outputs']['f0'] = 999.0
    with open(path, 'w') as file:
        json.dump(recorded, file)

    capsys.readouterr()
    df = modes.parameter_sweep(_workflow(staged, [100.0]), resume=True)
    printed = capsys.readouterr().out

    assert "re-extracted ['f0'] differently" in printed
    # The freshly extracted value is the one reported — it is the one the files
    # present now actually support.
    assert df['f0'].tolist() == [100.0]


@posix_only
def test_a_matching_recorded_output_is_not_reported_as_drift(staged, capsys):
    """The companion: an honest resume says nothing at all."""
    modes.parameter_sweep(_workflow(staged, [100.0, 101.0]))
    capsys.readouterr()
    modes.parameter_sweep(_workflow(staged, [100.0, 101.0]), resume=True)

    assert 'differently' not in capsys.readouterr().out


@pytest.mark.parametrize('recorded,value,same', [
    # A recorded value has been through JSON, so an array is a list and a numpy
    # scalar a float.
    (100.0, np.float64(100.0), True),
    ([1.0, 2.0], np.array([1.0, 2.0]), True),
    # NaN must compare EQUAL to NaN: a dry run, an unavailable quantity or a
    # failed extraction legitimately produces one, and every such output would
    # otherwise be reported as drift on every resumed point.
    (float('nan'), float('nan'), True),
    ([1.0, float('nan')], np.array([1.0, float('nan')]), True),
    (100.0, 101.0, False),
    # A shape change is a difference, not a comparison failure.
    ([1.0, 2.0], np.array([1.0, 2.0, 3.0]), False),
    # Non-numeric outputs (a geant4 'peak_index' tuple) fall back to equality.
    ([1, 2, 3], (1, 2, 3), True),
    ('longitudinal', 'transverse', False),
])
def test_recorded_and_re_extracted_outputs_are_compared_numerically(
        recorded, value, same):
    """The comparison behind the drift warning, per clause: JSON round-tripping and
    ``NaN`` must not read as a difference, and a shape change must."""
    from lume_ace3p.workflow_graph import _same_output

    assert _same_output(recorded, value) is same


# --------------------------------------------------------------------------- #
# 8. --status
# --------------------------------------------------------------------------- #


def _tamper(workdir, **changes):
    """Rewrite a manifest in place, for the states a test cannot produce
    honestly (a stale hash, a failure in a module that does not fail)."""
    path = os.path.join(workdir, state.STATE_FILE)
    with open(path) as file:
        recorded = json.load(file)
    recorded.update(changes)
    with open(path, 'w') as file:
        json.dump(recorded, file)


@posix_only
def test_status_reports_the_per_point_counts(staged, capsys):
    """``--status`` on a half-finished campaign: one row per point the config
    implies, its verdict, how much of its chain is recorded, and the module a
    resume would start from. Nothing is executed and no manifest is written.

    All five verdicts appear here, three of them from real runs and two from an
    edited manifest (a stale hash and a failed module are states this chain cannot
    reach on demand)."""
    radii = [100.0, 101.0, 102.0, 103.0, 104.0]
    modes.parameter_sweep(_workflow(staged, radii[:3]))
    workdirs = [str(staged / 'wd') + f'_{i}' for i in range(5)]

    # Point 1: recorded for another configuration. Point 2: its solve failed.
    _tamper(workdirs[1], config_hash='sha256:something-else')
    _tamper(workdirs[2], modules=[
        {'name': 'cubit', 'type': 'cubit', 'status': 'complete'},
        {'name': 'omega3p', 'type': 'omega3p', 'status': 'failed',
         'error': 'RuntimeError: boom'}])
    # Point 3: started, module 0 done, nothing after it.
    os.makedirs(workdirs[3], exist_ok=True)
    _tamper(workdirs[2], **{})                    # no-op, keeps the read honest
    partial = state.new_state(
        config_hash=_workflow(staged, radii).point_config_hash([radii[3]]),
        point={'axes': {'radius': radii[3]}}, workdir=workdirs[3])
    partial['modules'] = [{'name': 'cubit', 'type': 'cubit',
                           'status': 'complete'}]
    state.write_state(workdirs[3], partial)

    capsys.readouterr()
    df = modes.status(_workflow(staged, radii))
    printed = capsys.readouterr().out

    assert df['status'].tolist() == ['complete', 'stale', 'failed', 'partial',
                                    'absent']
    assert df['modules'].tolist() == ['2/2', '0/2', '1/2', '1/2', '0/2']
    assert df['next'].tolist() == ['', 'cubit', 'omega3p', 'omega3p', 'cubit']
    assert df['radius'].tolist() == radii
    assert df['workdir'].tolist() == workdirs

    assert ('1 complete, 1 partial, 1 failed, 1 stale, 1 absent') in printed
    assert '5 point(s)' in printed
    # Reading a status wrote nothing: the untouched point is still unstarted.
    assert state.read_state(workdirs[4]) is None


def test_status_of_an_unstarted_sweep_is_all_absent(tmp_path):
    """The degenerate case a user hits first, and the one that must not raise:
    nothing has run, so every point is ``absent`` and the first module of each is
    what a resume would run."""
    df = modes.status(_dry_workflow(tmp_path, [1.0, 2.0], workdir_mode='auto'))
    assert df['status'].tolist() == ['absent', 'absent']
    assert df['next'].tolist() == ['cubit', 'cubit']
    assert df['workdir'].tolist() == [str(tmp_path / 'wd') + '_1.0',
                                      str(tmp_path / 'wd') + '_2.0']


_STATUS_YAML = """\
workflow_parameters :
  'workdir' : 'wd'
  'workdir_mode' : 'indexed'
  'dry_run' : True

workflow :
  - module : cubit
    journal : 'cavity.jou'
  - module : omega3p
    input : 'cavity.omega3p'

mode :
  type : parameter_sweep
  resume : True

input_parameters :
  cubit :
    'radius' : [100.0, 101.0]
"""


def test_status_through_the_cli(staged, monkeypatch, capsys):
    """``run-lume-ace3p --status <config.yaml>`` as a user types it: the config is
    built, the table is printed, and **nothing is run** — no workdir appears."""
    (staged / 'sweep.yaml').write_text(_STATUS_YAML)
    monkeypatch.setattr(sys, 'argv',
                        ['run-lume-ace3p', '--status', 'sweep.yaml'])
    run_lume_ace3p.main()

    printed = capsys.readouterr().out
    assert '2 point(s)' in printed and '2 absent' in printed
    assert not os.path.exists(str(staged / 'wd_0'))


def test_status_without_a_config_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['run-lume-ace3p', '--status'])
    with pytest.raises(SystemExit) as exit_info:
        run_lume_ace3p.main()
    assert exit_info.value.code == 1
    assert '--status needs a config file' in capsys.readouterr().out


def test_status_covers_the_table_modes_only(tmp_path):
    """An Xopt mode chooses its points as it goes, so there is no fixed set of them
    to have finished part of — said plainly rather than reported as zero points."""
    data = {'workflow': [{'module': 'cubit', 'journal': _JOURNAL_NAME}],
            'mode': {'type': 'scalar_optimize'},
            'workflow_parameters': {'dry_run': True}}
    with pytest.raises(ValueError, match='--status covers the table modes'):
        run_lume_ace3p._report_status(data)


# --------------------------------------------------------------------------- #
# 9. collect_training_data on the shared mechanism
# --------------------------------------------------------------------------- #


_BETAS = [f'beta{i}' for i in range(4)]


class _RecordingWorkflow:
    """The slice of the ``Workflow`` surface ``collect_training_data`` drives,
    recording the keywords each ``evaluate`` was called with.

    Deliberately spelled ``**kwargs``: the point of the test is which keywords the
    collector passes, and the doubles in ``test_surrogate_data.py`` — whose
    ``evaluate`` takes ``(overrides, workdir=None)`` and nothing else — are what
    make "pass ``resume=`` only when it is True" load-bearing rather than
    cosmetic."""

    class _Particles:
        type = 'particles'
        params = {'num_bins': 4, 'beta_inputs': _BETAS,
                  'bin_edges': [0.0, 1.0, 2.0, 3.0, 4.0]}

    def __init__(self):
        self.modules = [self._Particles()]
        self.workdir_mode = 'manual'
        self.baseworkdir = None
        self.dry_run = False
        self.calls = []

    def evaluate(self, overrides, **kwargs):
        self.calls.append(kwargs)
        return {}, None

    def field(self, ctx=None):
        return {'dose': {'indices': np.zeros((2, 3), int),
                         'values': np.array([1.0, 2.0])}}


def _collect_cfg(tmp_path, **overrides):
    cfg = {'type': 'collect_training_data', 'store': str(tmp_path / 'store'),
           'num_samples': 2, 'seed': 0,
           'variables': {name: [40.0, 60.0] for name in _BETAS}}
    cfg.update(overrides)
    return cfg


def test_collect_training_data_passes_resume_through(tmp_path):
    """The collector drives the shared seam, so ``resume: true`` reaches it — which
    is what lets a *partly*-run sample restart at its first non-complete module
    rather than at the mesh.

    With ``resume`` unset the keyword is not passed at all, which is what keeps the
    older test doubles (and any direct driver) working."""
    plain = _RecordingWorkflow()
    modes.collect_training_data(_collect_cfg(tmp_path / 'a'), plain)
    assert [set(call) for call in plain.calls] == [{'workdir'}, {'workdir'}]

    resuming = _RecordingWorkflow()
    modes.collect_training_data(_collect_cfg(tmp_path / 'b', resume=True),
                                resuming)
    assert [call.get('resume') for call in resuming.calls] == [True, True]


def test_collect_training_data_still_honors_a_persisted_field(tmp_path):
    """The recognised legacy state: a sample whose ``field.npz`` is already there
    is skipped outright, exactly as before the manifest existed. Every training
    store collected so far records completion that way, and re-deriving those
    samples would invalidate stores that are perfectly good."""
    cfg = _collect_cfg(tmp_path, resume=True)
    first = _RecordingWorkflow()
    modes.collect_training_data(cfg, first)
    assert len(first.calls) == 2

    again = _RecordingWorkflow()
    modes.collect_training_data(cfg, again)
    assert again.calls == []
    assert os.path.isfile(os.path.join(cfg['store'], 'sample_00000',
                                       'field.npz'))


def test_resolved_workdir_matches_what_evaluate_chooses(tmp_path):
    """``--status`` finds a point's manifest by re-deriving its workdir, so that
    derivation has to be the same one ``evaluate`` uses — under both naming modes
    that give a point its own directory."""
    for workdir_mode, expected in (('auto', str(tmp_path / 'wd') + '_2.0'),
                                   ('indexed', str(tmp_path / 'wd') + '_1')):
        workflow = _dry_workflow(tmp_path, [1.0, 2.0], workdir_mode=workdir_mode)
        _outputs, ctx = modes._evaluate_point(workflow, [2.0], 1)
        assert ctx.workdir == expected
        assert workflow.resolved_workdir([2.0], 1) == expected


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
