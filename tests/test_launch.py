"""The one step builder (Phase 1 of ``plans/parallel_sweep_plan.md``).

Two things are pinned here. First, the **serial** launch lines — solver,
parallel acdtool, serial acdtool, meshconvert, the Cubit mesher, Geant4 — exactly
as each wrapper built them before :func:`lume_ace3p.launch.step_command` existed,
quirks included (the double space an empty ``opts`` leaves, meshconvert's
``--nodes=1 --ntasks=1``, the bare mesher). Those strings are what keep
``workers: 1`` byte-identical, so they are fixtures, not a description of a
preferred spelling. Second, the same lines under a :class:`Slice`: every step
gains ``-N … --exact <mem flag>``, and a serial step holds the whole slice.
"""

import os
import shutil
import subprocess

import pytest

from lume_ace3p.acdtool import Acdtool
from lume_ace3p.ace3p import Omega3P
from lume_ace3p.cubit import Cubit
from lume_ace3p.geant4 import Geant4

HERE = os.path.dirname(os.path.abspath(__file__))
RFPOST = os.path.join(HERE, 'fixtures', 'acdtool', 'rfpost_inputs',
                      'pillbox-rtop+coax.rfpost')

_JOURNAL = '#{radius=100.0}\ncreate sphere radius {radius}\nexport genesis "cavity.gen" overwrite\n'
_GEANT4 = 'nthreads = 8\nparticles = beam.txt\n'


@pytest.fixture
def commands(monkeypatch):
    """Every shell line a wrapper hands to ``subprocess.run``, none executed.
    With no log file :func:`lume_ace3p.logs.run_logged` is exactly that call."""
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(subprocess, 'run', fake_run)
    return seen


@pytest.fixture
def src(tmp_path, monkeypatch):
    """Inputs in ``tmp_path/src`` (the working directory), workdir separate, so
    no wrapper renames its input to ``*_copy``."""
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'cavity.omega3p').write_text('ModelInfo: {\n  File: ./cavity.ncdf\n}\n')
    (src / 'cavity.jou').write_text(_JOURNAL)
    (src / 'in.geant4').write_text(_GEANT4)
    shutil.copy(RFPOST, src)
    monkeypatch.chdir(src)
    return src


def _wd(src):
    return str(src.parent / 'wd')


def _solver(src, **kwargs):
    return Omega3P('cavity.omega3p', workdir=_wd(src), ace3p_path='/ace3p/',
                   **kwargs)


def _rf(src, **kwargs):
    acd = Acdtool(os.path.basename(RFPOST), workdir=_wd(src), ace3p_path='/ace3p/',
                  acdtool_tasks=4, acdtool_cores=2, **kwargs)
    open(os.path.join(_wd(src), 'rfpost.out'), 'w').close()
    return acd


def _transwake(src, **kwargs):
    return Acdtool(None, workdir=_wd(src), ace3p_path='/ace3p/',
                   acdtool_command='postprocess transwake',
                   acdtool_cores=256, **kwargs)


def _cubit(src, **kwargs):
    return Cubit('cavity.jou', workdir=_wd(src), ace3p_path='/ace3p/',
                 cubit_path='/cubit/', **kwargs)


def _geant4(src, **kwargs):
    return Geant4('in.geant4', workdir=_wd(src), geant4_threads=8,
                  geant4_app_path='/app', geant4_app_exe='dose', **kwargs)


# --------------------------------------------------------------------------- #
# Serial: today's strings, byte for byte.
# --------------------------------------------------------------------------- #

def test_serial_solver_line(src):
    assert _solver(src, ace3p_tasks=16, ace3p_cores=8, mpi_caller='srun',
                   ace3p_opts='--cpu-bind=cores').solver_command() == (
        'srun -n 16 -c 8 --cpu-bind=cores /ace3p/omega3p cavity.omega3p')
    # An empty opts leaves its double space; no MPI caller leaves a leading one.
    assert _solver(src, mpi_caller='srun').solver_command() == (
        'srun -n 1 -c 1  /ace3p/omega3p cavity.omega3p')
    assert _solver(src, mpi_caller='').solver_command() == (
        ' -n 1 -c 1  /ace3p/omega3p cavity.omega3p')


def test_serial_parallel_acdtool_line(src, commands):
    _rf(src, mpi_caller='srun', acdtool_opts='--cpu-bind=cores').run()
    assert commands[-1] == ('srun -n 4 -c 2 --cpu-bind=cores /ace3p/acdtool '
                            'postprocess rf pillbox-rtop+coax.rfpost')
    _rf(src, mpi_caller='').run()
    assert commands[-1] == '/ace3p/acdtool postprocess rf pillbox-rtop+coax.rfpost'


def test_serial_serial_acdtool_line(src, commands):
    _transwake(src, mpi_caller='srun').run(args=[0.0, 0.0, 0.0, 0.0125])
    assert commands[-1] == ('srun -n 1 -c 256 /ace3p/acdtool postprocess '
                            'transwake t3p_results 0.0 0.0 0.0 0.0125')


def test_serial_cubit_and_meshconvert_lines(src, commands):
    _cubit(src, mpi_caller='srun').run()
    assert commands == [
        '/cubit/cubit -nographics -nojournal -noecho cavity.jou',
        'srun --nodes=1 --ntasks=1 /ace3p/acdtool meshconvert cavity.gen',
    ]


def test_serial_geant4_line(src, commands):
    _geant4(src, mpi_caller='srun').run()
    assert commands[-1] == 'srun -n 1 -c 8  /app/dose in.geant4'


# --------------------------------------------------------------------------- #
# Under a slice.
# --------------------------------------------------------------------------- #

from lume_ace3p.launch import Slice, step_command  # noqa: E402

SLICE = Slice(1, 30, '--mem-per-cpu=4096')


def test_sliced_solver_line(src):
    assert _solver(src, ace3p_tasks=15, ace3p_cores=2, mpi_caller='srun',
                   ace3p_opts='--cpu-bind=cores',
                   slice=SLICE).solver_command() == (
        'srun -N 1 -n 15 -c 2 --exact --mem-per-cpu=4096 --cpu-bind=cores '
        '/ace3p/omega3p cavity.omega3p')


def test_sliced_acdtool_lines(src, commands):
    _rf(src, mpi_caller='srun', slice=SLICE).run()
    assert commands[-1] == ('srun -N 1 -n 4 -c 2 --exact --mem-per-cpu=4096 '
                            '/ace3p/acdtool postprocess rf '
                            'pillbox-rtop+coax.rfpost')
    # A serial command holds the slice, whatever cores it was configured with.
    _transwake(src, mpi_caller='srun', slice=SLICE).run(args=[0.0, 0.0, 0.0, 0.0125])
    assert commands[-1] == ('srun -N 1 -n 1 -c 30 --exact --mem-per-cpu=4096 '
                            '/ace3p/acdtool postprocess transwake t3p_results '
                            '0.0 0.0 0.0 0.0125')


def test_sliced_cubit_runs_under_the_mpi_caller(src, commands):
    _cubit(src, mpi_caller='srun', slice=SLICE).run()
    assert commands == [
        'srun -N 1 -n 1 -c 30 --exact --mem-per-cpu=4096 '
        '/cubit/cubit -nographics -nojournal -noecho cavity.jou',
        'srun -N 1 -n 1 -c 30 --exact --mem-per-cpu=4096 '
        '/ace3p/acdtool meshconvert cavity.gen',
    ]


def test_sliced_geant4_line(src, commands):
    _geant4(src, mpi_caller='srun', slice=SLICE).run()
    assert commands[-1] == ('srun -N 1 -n 1 -c 30 --exact --mem-per-cpu=4096 '
                            '/app/dose in.geant4')


def test_a_multi_node_slice_pins_parallel_steps_to_its_nodes():
    """``-N`` is the slice's node count for a parallel step and 1 for a serial
    one, so a two-node worker's solver spans its two nodes and its mesher one."""
    wide = Slice(2, 120, '--mem=0')
    assert step_command('srun', 240, 1, '', 'omega3p x', wide) == (
        'srun -N 2 -n 240 -c 1 --exact --mem=0 omega3p x')
    assert step_command('srun', 1, 1, '', 'cubit x', wide) == (
        'srun -N 1 -n 1 -c 120 --exact --mem=0 cubit x')


def test_a_slice_without_an_mpi_caller_keeps_the_serial_line():
    """Nothing can launch a step flag without a launcher, so no caller means the
    historical spelling even when a slice is set."""
    assert step_command('', 4, 2, '', 'omega3p x', SLICE) == ' -n 4 -c 2  omega3p x'


def test_ctx_slice_reaches_every_wrapper(tmp_path):
    """``RunContext.slice`` defaults to None (a hand-built context is serial) and
    is what the modules pass to their wrappers."""
    from lume_ace3p.modules import RunContext
    assert RunContext(str(tmp_path)).slice is None
    assert RunContext(str(tmp_path), slice=SLICE).slice is SLICE


# --------------------------------------------------------------------------- #
# Phase 2: the allocation and the worker plan.
# --------------------------------------------------------------------------- #

from lume_ace3p.launch import (  # noqa: E402
    Allocation, allocation_from_env, footprint, plan_summary, plan_workers,
)
from lume_ace3p.modules import (  # noqa: E402
    AcdtoolModule, CubitModule, Geant4Module, Omega3PModule,
)


def _chain(tasks, cores):
    """Cubit → Omega3P (tasks × cores) → serial-shaped acdtool rf."""
    return [CubitModule({'journal': 'cavity.jou'}),
            Omega3PModule({'input': 'cavity.omega3p', 'tasks': tasks,
                           'cores': cores}),
            AcdtoolModule({'input': 'x.rfpost', 'tasks': 4, 'cores': 1})]


def _env(cpus, mem='491520', **extra):
    env = {'SLURM_JOB_ID': '1', 'SLURM_JOB_CPUS_PER_NODE': cpus, **extra}
    if mem is not None:
        env['SLURM_MEM_PER_NODE'] = mem
    return env


@pytest.mark.parametrize('cpus, expect', [
    ('120(x2)', (2, 120)),
    ('120', (1, 120)),
    ('64(x2),64', (3, 64)),
])
def test_allocation_reads_the_compressed_cpu_list(cpus, expect):
    alloc = allocation_from_env(_env(cpus))
    assert (alloc.nodes, alloc.cpn) == expect
    assert alloc.mem_per_node == 491520


def test_allocation_memory_per_cpu_and_none():
    assert allocation_from_env(
        _env('120', mem=None, SLURM_MEM_PER_CPU='4000')).mem_per_node == 480000
    assert allocation_from_env(_env('256(x4)', mem=None)).mem_per_node is None


def test_no_slurm_is_no_allocation():
    assert allocation_from_env({}) is None


def test_a_heterogeneous_allocation_is_refused():
    with pytest.raises(ValueError, match='equal CPU counts'):
        allocation_from_env(_env('120,64'))


def test_footprint_is_the_largest_step():
    assert footprint(_chain(15, 4)) == 60
    assert footprint([CubitModule({'journal': 'j'})]) == 1
    assert footprint([Geant4Module({'geant4_input': 'g', 'geant4_threads': 32})]) == 32
    # A serial acdtool command is one rank whatever 'tasks' says.
    serial = AcdtoolModule({'command': 'postprocess transwake', 'tasks': 16,
                            'cores': 8})
    assert serial.launch_size() == (1, 8)


TWO = Allocation(2, 120, 491520)


@pytest.mark.parametrize('tasks, cores, alloc, requested, workers, slice', [
    # 4 half-node workers on 2 nodes: 2 per node.
    (15, 4, TWO, 4, 4, Slice(1, 60, '--mem-per-cpu=4096')),
    (15, 4, TWO, 2, 2, Slice(1, 60, '--mem-per-cpu=4096')),
    (15, 4, TWO, 'auto', 4, Slice(1, 60, '--mem-per-cpu=4096')),
    # 2 two-node workers on 4 nodes.
    (240, 1, Allocation(4, 120, 491520), 2, 2, Slice(2, 120, '--mem-per-cpu=4096')),
    (240, 1, Allocation(4, 120, 491520), 'auto', 2,
     Slice(2, 120, '--mem-per-cpu=4096')),
    # No memory limit (Perlmutter): --mem=0.
    (16, 8, Allocation(1, 256, None), 2, 2, Slice(1, 128, '--mem=0')),
])
def test_counts_that_tile_the_allocation(tasks, cores, alloc, requested,
                                         workers, slice):
    assert plan_workers(_chain(tasks, cores), requested, alloc) == (workers, slice)


@pytest.mark.parametrize('tasks, cores, alloc, requested, listed', [
    (15, 4, TWO, 3, 'Counts that fit: 1, 2, 4.'),
    (15, 4, TWO, 6, 'Counts that fit: 1, 2, 4.'),
    # Two two-node workers on three nodes: nodes are not a multiple of 2.
    (240, 1, Allocation(3, 120, 491520), 2, 'Counts that fit: 1.'),
    # Fewer workers than the nodes hold would idle nodes (§4, Phase 2).
    (240, 1, Allocation(6, 120, 491520), 2, 'Counts that fit: 1, 3.'),
])
def test_counts_that_do_not_tile_are_refused(tasks, cores, alloc, requested,
                                             listed):
    with pytest.raises(ValueError, match='does not tile') as info:
        plan_workers(_chain(tasks, cores), requested, alloc)
    assert str(info.value).endswith(listed)


def test_the_listed_counts_for_a_sixty_cpu_footprint_on_two_wide_nodes():
    """The plan's own example: a 60-CPU footprint on two nodes that each hold
    four lists 2, 4, 6, 8."""
    with pytest.raises(ValueError, match='Counts that fit: 1, 2, 4, 6, 8.$'):
        plan_workers(_chain(15, 4), 3, Allocation(2, 240, None))


def test_auto_with_nothing_that_fits_is_serial():
    assert plan_workers(_chain(120, 1), 'auto', Allocation(1, 120, 491520)) == (1, None)


def test_one_worker_is_always_the_serial_run():
    assert plan_workers(_chain(15, 4), 1, TWO) == (1, None)
    assert plan_workers(_chain(15, 4), 1, None) == (1, None)


def test_no_slurm_runs_the_requested_count_without_a_slice():
    assert plan_workers(_chain(15, 4), 3, None) == (3, None)
    assert plan_workers(_chain(15, 4), 'auto', None) == (1, None)


def test_a_multi_node_step_must_spread_over_the_slice():
    """Check 3: a worker's nodes are fixed by its largest step, so a smaller
    parallel step must still place at least one rank per node, and a single
    rank cannot be wider than a node."""
    chain = _chain(240, 1)
    chain[2] = AcdtoolModule({'input': 'x.rfpost', 'tasks': 1, 'cores': 1})
    assert plan_workers(chain, 2, Allocation(4, 120, None))[0] == 2
    chain[2] = AcdtoolModule({'input': 'x.rfpost', 'tasks': 3, 'cores': 80})
    with pytest.raises(ValueError, match="'acdtool'.*does not spread"):
        plan_workers(chain, 2, Allocation(4, 120, None))
    wide = [Geant4Module({'geant4_input': 'g', 'geant4_threads': 200})]
    with pytest.raises(ValueError, match='cannot span nodes'):
        plan_workers(wide, 2, Allocation(4, 120, None))


def test_inside_slurm_the_caller_must_be_srun():
    with pytest.raises(ValueError, match='needs srun'):
        plan_workers(_chain(15, 4), 2, TWO, mpi_caller='mpirun')
    assert plan_workers(_chain(15, 4), 2, TWO, mpi_caller='/usr/bin/srun')[0] == 2


def test_the_slice_summary_line():
    workers, slice = plan_workers(_chain(30, 2), 4, TWO)
    assert plan_summary(workers, slice, TWO) == (
        '4 workers × (60 CPUs, 240 GB) on 2 nodes')
    nomem = Allocation(1, 256, None)
    workers, slice = plan_workers(_chain(16, 8), 2, nomem)
    assert 'memory is not isolated' in plan_summary(workers, slice, nomem)
    assert plan_summary(1, None, TWO) is None
    assert 'not inside a Slurm job' in plan_summary(3, None, None)


# --------------------------------------------------------------------------- #
# The mode key and the CLI flag.
# --------------------------------------------------------------------------- #

from lume_ace3p import modes  # noqa: E402


@pytest.mark.parametrize('value, expect', [
    (None, 1), (1, 1), (4, 4), ('4', 4), ('auto', 'auto'), ('AUTO', 'auto')])
def test_workers_of_accepts(value, expect):
    cfg = {} if value is None else {'workers': value}
    assert modes.workers_of(cfg) == expect


@pytest.mark.parametrize('value', [0, -2, 'many', 2.5, True])
def test_workers_of_refuses(value):
    with pytest.raises(ValueError, match="positive integer or 'auto'"):
        modes.workers_of({'workers': value})


class _Flow:
    """Just enough of a Workflow for :func:`modes.worker_plan`."""
    def __init__(self, modules, mpi='srun'):
        self.modules = modules
        self.paths = {'mpi': mpi}


def test_worker_plan_reads_the_environment_and_prints_the_slice(capsys):
    env = _env('120(x2)')
    assert modes.worker_plan({'workers': 4}, _Flow(_chain(15, 4)), env) == (
        4, Slice(1, 60, '--mem-per-cpu=4096'))
    assert capsys.readouterr().out == ' - 4 workers × (60 CPUs, 240 GB) on 2 nodes\n'
    with pytest.raises(ValueError, match='does not tile'):
        modes.worker_plan({'workers': 3}, _Flow(_chain(15, 4)), env)


def test_worker_plan_for_one_worker_reads_nothing():
    """The serial default never looks at the workflow or the environment, so
    every existing config and test double runs as before."""
    assert modes.worker_plan({}, None, {'SLURM_JOB_ID': 'x'}) == (1, None)


def test_run_mode_refuses_a_bad_count_before_any_point(monkeypatch):
    monkeypatch.setattr(modes, 'parameter_sweep',
                        lambda *a, **k: pytest.fail('a point ran'))
    monkeypatch.setattr(modes.os, 'environ', _env('120(x2)'))
    with pytest.raises(ValueError, match='does not tile'):
        modes.run_mode({'type': 'parameter_sweep', 'workers': 3},
                       _Flow(_chain(15, 4)))


def test_workers_on_a_sequential_mode_is_refused():
    with pytest.raises(ValueError, match='does not take workers'):
        modes.run_mode({'type': 'scalar_optimize', 'workers': 2}, None)


def test_cli_workers_overrides_the_mode_block(tmp_path, monkeypatch):
    from lume_ace3p import run_lume_ace3p
    config = tmp_path / 'c.yaml'
    config.write_text('workflow:\n  - module: cubit\n    journal: j.jou\n'
                      'mode:\n  type: parameter_sweep\n  workers: 2\n')
    seen = {}
    monkeypatch.setattr(run_lume_ace3p, '_run_declarative',
                        lambda data, config_dir=None: seen.update(data['mode']))
    monkeypatch.setattr('sys.argv',
                        ['run-lume-ace3p', '--workers', 'auto', str(config)])
    run_lume_ace3p.main()
    assert seen == {'type': 'parameter_sweep', 'workers': 'auto'}
