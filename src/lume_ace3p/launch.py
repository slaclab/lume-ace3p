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

import os
import re
from typing import NamedTuple, Optional


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


# --------------------------------------------------------------------------- #
# Sizing (Phase 2): how many workers an allocation holds, and each one's slice.
# --------------------------------------------------------------------------- #

class Allocation(NamedTuple):
    """The Slurm job the run is inside: ``nodes`` nodes of ``cpn`` CPUs each,
    and ``mem_per_node`` MB per node, or ``None`` when the job set no memory
    limit (Perlmutter's exclusive nodes)."""
    nodes: int
    cpn: int
    mem_per_node: Optional[int]


_CPUS_PER_NODE = re.compile(r'^(\d+)(?:\(x(\d+)\))?$')


def allocation_from_env(environ):
    """The :class:`Allocation` ``environ`` describes, or ``None`` when it is not
    a Slurm job (no ``SLURM_JOB_ID``).

    ``SLURM_JOB_CPUS_PER_NODE`` is Slurm's compressed per-node list
    (``120(x2)``, ``64(x3),128``). More than one distinct count is refused: the
    slice model sizes every worker alike, and has no answer for a mixed job."""
    if not environ.get('SLURM_JOB_ID'):
        return None
    spec = environ.get('SLURM_JOB_CPUS_PER_NODE', '')
    counts = {}
    for group in filter(None, spec.split(',')):
        match = _CPUS_PER_NODE.match(group.strip())
        if not match:
            raise ValueError(
                f'cannot read SLURM_JOB_CPUS_PER_NODE={spec!r}.')
        cpus, repeat = int(match.group(1)), int(match.group(2) or 1)
        counts[cpus] = counts.get(cpus, 0) + repeat
    if not counts:
        raise ValueError('SLURM_JOB_ID is set but SLURM_JOB_CPUS_PER_NODE is '
                         'not, so the allocation cannot be sized.')
    if len(counts) > 1:
        raise ValueError(
            f'workers needs nodes with equal CPU counts, and this job has '
            f'{spec} (SLURM_JOB_CPUS_PER_NODE). Request one node type, or set '
            f'workers: 1.')
    (cpn, nodes), = counts.items()
    if environ.get('SLURM_MEM_PER_NODE'):
        mem = int(environ['SLURM_MEM_PER_NODE'])
    elif environ.get('SLURM_MEM_PER_CPU'):
        mem = int(environ['SLURM_MEM_PER_CPU']) * cpn
    else:
        mem = None
    return Allocation(nodes, cpn, mem)


def _launch_sizes(modules):
    """``(name, tasks, cores)`` for every module that launches an external tool."""
    sizes = []
    for module in modules:
        size = module.launch_size()
        if size is not None:
            sizes.append((module.name,) + tuple(size))
    return sizes


def footprint(modules):
    """The largest step in the chain, in CPUs: max of ``tasks × cores``. It
    sizes the slice, since a worker's steps run one at a time inside it."""
    return max([tasks * cores for _, tasks, cores in _launch_sizes(modules)],
               default=1)


def _fitting_counts(foot, alloc):
    """Every worker count above 1 that tiles ``alloc`` for a ``foot``-CPU
    footprint (check 4 of the plan), ascending."""
    if foot <= alloc.cpn:
        per_node = alloc.cpn // foot
        return [alloc.nodes * k for k in range(1, per_node + 1)
                if alloc.nodes * k > 1]
    per_worker = -(-foot // alloc.cpn)
    if alloc.nodes % per_worker:
        return []
    count = alloc.nodes // per_worker
    return [count] if count > 1 else []


def _slice_for(foot, alloc):
    if foot <= alloc.cpn:
        nodes, cpus = 1, foot
    else:
        nodes, cpus = -(-foot // alloc.cpn), alloc.cpn
    if alloc.mem_per_node is None:
        return Slice(nodes, cpus, '--mem=0')
    return Slice(nodes, cpus,
                 '--mem-per-cpu=' + str(alloc.mem_per_node // alloc.cpn))


def _check_steps_fit(modules, slice, alloc):
    """Check 3: every step can be placed on the slice's nodes. Below a node this
    holds by construction of the footprint; across nodes a parallel step's ranks
    are spread by ``-N``, so each node's share must fit, and a single-rank step
    cannot span nodes at all."""
    for name, tasks, cores in _launch_sizes(modules):
        if tasks == 1:
            if cores > alloc.cpn:
                raise ValueError(
                    f"module '{name}' is a single-rank step with {cores} CPUs, "
                    f"more than one node's {alloc.cpn}; a rank cannot span "
                    f"nodes.")
            continue
        if slice.nodes == 1:
            continue
        per_node = -(-tasks // slice.nodes)
        if tasks < slice.nodes or per_node * cores > alloc.cpn:
            raise ValueError(
                f"module '{name}' ({tasks} tasks × {cores} cores) does not "
                f"spread over a worker's {slice.nodes} nodes of {alloc.cpn} "
                f"CPUs: it needs at least {slice.nodes} tasks and at most "
                f"{alloc.cpn // cores * slice.nodes}.")


def plan_workers(modules, requested, allocation, mpi_caller='srun'):
    """``(workers, slice)`` for running ``modules`` as ``requested`` concurrent
    workers (an int or ``'auto'``) inside ``allocation``, or a ``ValueError``
    saying which counts would fit.

    One worker is the serial run: ``(1, None)`` whatever the allocation, so its
    command lines are the historical ones. Outside Slurm (``allocation`` None)
    the requested count runs with no slice — the test suite and a laptop. Under
    Slurm the count must tile the allocation (decision 4 of
    ``plans/parallel_sweep_plan.md``): a count that does not is refused rather
    than left for Slurm to serialize silently. ``'auto'`` takes the largest
    count that does, or 1 when none does."""
    auto = requested == 'auto'
    if not auto and requested == 1:
        return 1, None
    if allocation is None:
        return (1 if auto else requested), None
    launcher = str(mpi_caller or '').split()
    if not launcher or os.path.basename(launcher[0]) != 'srun':
        raise ValueError(
            f'workers > 1 inside a Slurm job needs srun as the MPI caller (it '
            f'is {mpi_caller!r}): the slice flags are srun step options.')
    foot = footprint(modules)
    counts = _fitting_counts(foot, allocation)
    if auto:
        if not counts:
            return 1, None
        requested = counts[-1]
    elif requested not in counts:
        where = (f'{allocation.nodes} node' + ('s' if allocation.nodes > 1 else '')
                 + f' of {allocation.cpn} CPUs')
        fits = ', '.join(str(c) for c in [1] + counts)
        raise ValueError(
            f'workers: {requested} does not tile this allocation ({where}) for '
            f'a {foot}-CPU footprint (the largest step\'s tasks × cores). '
            f'Counts that fit: {fits}.')
    slice = _slice_for(foot, allocation)
    _check_steps_fit(modules, slice, allocation)
    # Check 6, implied by the tiling above; kept because the failure it guards
    # against is a silent queue, not an error.
    assert requested * slice.nodes * slice.cpus <= allocation.nodes * allocation.cpn
    return requested, slice


def plan_summary(workers, slice, allocation):
    """The line a run prints about its worker plan, or ``None`` for a serial
    run. Says what each worker holds, so a user choosing ``workers`` against a
    mesher's or solver's memory need can see the share."""
    if workers == 1:
        return None
    if slice is None:
        return (f'{workers} workers, not inside a Slurm job: steps launch '
                f'without slice flags.')
    share = (f'{slice.cpus} CPUs' if slice.nodes == 1
             else f'{slice.nodes} nodes × {slice.cpus} CPUs')
    nodes = f'{allocation.nodes} node' + ('s' if allocation.nodes > 1 else '')
    if slice.mem_flag == '--mem=0':
        return (f'{workers} workers × ({share}) on {nodes}; the job sets no '
                f'memory limit, so memory is not isolated between workers.')
    gb = allocation.mem_per_node // allocation.cpn * slice.cpus * slice.nodes / 1024
    return f'{workers} workers × ({share}, {gb:g} GB) on {nodes}'
