"""One builder for every external-tool launch line (Phase 1 of
``plans/parallel_sweep_plan.md``).

The solver, acdtool, meshconvert, Geant4 and the Cubit mesher each used to build
their own shell line, none of them able to share an allocation: Slurm gives a
step the node's whole memory by default, so two concurrent steps serialize. A
**slice** — the share of the allocation one sweep worker owns — is what lets
them pack, and :func:`step_command` is the one place that turns a slice into
step flags.

With no slice (``workers: 1``, or no MPI caller) every line is byte-identical to
what its wrapper built before, so a serial run's command lines, and every
baseline, are unchanged. The four historical spellings are kept as ``form``\\ s
for exactly that reason; under a slice they converge on one.
"""

from typing import NamedTuple


# The serial spellings, one per historical call site.
SOLVER = 'solver'            # '<mpi> -n T -c C <opts> <argv>'; Geant4 uses it too
ACDTOOL = 'acdtool'          # same, but flags and opts dropped when absent
MESHCONVERT = 'meshconvert'  # '<mpi> --nodes=1 --ntasks=1 <argv>'
BARE = 'bare'                # '<argv>': the Cubit mesher, not under MPI at all


class Slice(NamedTuple):
    """The share of the allocation one worker owns for its whole chain.

    ``nodes`` is the node count every step is pinned to (``-N``), ``cpus`` the
    CPUs per node the slice holds, and ``mem_flag`` the memory flag each step
    carries (``'--mem-per-cpu=4096'``, or ``'--mem=0'`` when the job sets no
    memory limit).
    """
    nodes: int
    cpus: int
    mem_flag: str


def _join(*parts):
    return ' '.join(str(p) for p in parts if p not in (None, ''))


def step_command(mpi_caller, tasks, cores, opts, argv, slice=None,
                 form=SOLVER):
    """The shell line that launches one external tool as one step.

    ``argv`` is the tool and its arguments, already joined. Without a slice the
    line is ``form``'s historical spelling. With one, every step becomes
    ``<mpi> -N <nodes> -n T -c C --exact <mem flag> <opts> <argv>``; a serial
    step (``tasks`` 1 or None — Cubit, meshconvert, serial acdtool, Geant4's
    single rank) runs as ``-N 1 -n 1 -c <slice cpus>`` so it holds the slice's
    whole CPU and memory share while it runs (decision 2 of the plan). A slice
    with no MPI caller has nothing to launch it, so it falls back to the
    serial spelling.
    """
    opts = opts or ''
    if slice is None or not mpi_caller:
        if form == BARE:
            return argv
        if form == MESHCONVERT:
            return mpi_caller + ' --nodes=1 --ntasks=1 ' + argv
        if form == ACDTOOL:
            flags = ([mpi_caller, '-n', tasks or 1, '-c', cores or 1]
                     if mpi_caller else [])
            return _join(*flags, opts, argv)
        return (mpi_caller + ' -n ' + str(tasks) + ' -c ' + str(cores) + ' '
                + opts + ' ' + argv)
    if tasks in (None, 1):
        nodes, tasks, cores = 1, 1, slice.cpus
    else:
        nodes = slice.nodes
    return _join(mpi_caller, '-N', nodes, '-n', tasks, '-c', cores or 1,
                 '--exact', slice.mem_flag, opts, argv)
