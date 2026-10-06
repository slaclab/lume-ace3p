"""Suite-wide fixtures.

The thread pin below is the outcome of plans/pytest_slowdown_experiment_plan.md.

The suite is almost entirely small-matrix work, so threaded BLAS buys it nothing
and the synchronisation overhead is pure loss. OpenBLAS and the ``libgomp``
bundled with scikit-learn do respect the job's cpuset (measured: 8 threads each
in an 8-CPU sbatch, not ``nproc``), but two independent pools of 8 spinning over
8 cores still cost ~40% of the suite's CPU time, and on a contended node tipped
into a 4x wall blowup -- on sdfmilan265, 25:13 wall / 1:14:59 CPU became
7:25 / 8:02 pinned on the same node. Pinned runs land at 5:34-6:21 wall and a
tight 6:56-8:02 CPU, against 12:14-12:25 CPU for the *fast* unpinned runs.
"""

import pytest
from threadpoolctl import threadpool_limits


@pytest.fixture(scope="session", autouse=True)
def _single_threaded_blas():
    """Hold OpenBLAS/OpenMP to one thread each for the whole session.

    ``threadpool_limits`` is deliberate rather than exporting ``OMP_NUM_THREADS``:
    it rewrites the limits of the libraries already loaded in *this* process and
    is not inherited by children. Tests that launch ACE3P solvers rely on that —
    e.g. the ``track3p_geant4_chain`` example exports ``OMP_NUM_THREADS=8`` to
    match its ``cores: 8``, and the pin must not leak in and throttle it.
    """
    with threadpool_limits(limits=1):
        yield
