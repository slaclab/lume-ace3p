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
