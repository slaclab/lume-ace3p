import os, glob, shutil, warnings

if 'NERSC_HOST' in os.environ:
    PLATFORM = os.environ['NERSC_HOST']
elif 'HOSTNAME' in os.environ:
    PLATFORM = os.environ['HOSTNAME']
else:
    PLATFORM = os.uname()[1]

try:
    if PLATFORM.startswith('sdf'):
        os.environ['ACE3P_PATH'] = '/sdf/group/rfar/ace3p/bin/'
        os.environ['CUBIT_PATH'] = '/sdf/group/rfar/software/'
        os.environ['MPI_CALLER'] = 'mpirun'
    elif PLATFORM.startswith('perlmutter'):
        os.environ['ACE3P_PATH'] = '/global/cfs/cdirs/ace3p/perlmutter/CPU/'
        os.environ['CUBIT_PATH'] = '/global/cfs/cdirs/ace3p/perlmutter/CPU/'
        os.environ['MPI_CALLER'] = 'srun'
    else:
        ace3p_dir = shutil.which('omega3p')
        if ace3p_dir is not None:
            ace3p_dir = ace3p_dir.strip('omega3p')
            os.environ['ACE3P_PATH'] = ace3p_dir
        else:
            ace3p_dir = glob.glob('**/ace3p/bin', root_dir=os.path.expanduser('~'), recursive=True)
            if not ace3p_dir:
                raise RuntimeError('The ace3p installation was not found. Set ACE3P_PATH manually.')
            os.environ['ACE3P_PATH'] = ace3p_dir[0]

        cubit_dir = shutil.which('cubit')
        if cubit_dir is not None:
            cubit_dir = cubit_dir.strip('cubit')
            os.environ['CUBIT_PATH'] = cubit_dir
        else:
            cubit_dir = glob.glob('**/Cubit*', root_dir=os.path.expanduser('~'), recursive=True)
            if not cubit_dir:
                raise RuntimeError('The cubit installation was not found. Set CUBIT_PATH manually.')
            os.environ['CUBIT_PATH'] = cubit_dir[0]

        mpi_caller = shutil.which('mpirun')
        if mpi_caller is None:
            raise RuntimeError('MPI installation not found. Set MPI_CALLER manually.')
        os.environ['MPI_CALLER'] = mpi_caller

except RuntimeError as e:
    warnings.warn(
        f"ACE3P environment not fully configured: {e} "
        "Workflows will fail at runtime until the environment is set up correctly."
    )
    for var in ('ACE3P_PATH', 'CUBIT_PATH', 'MPI_CALLER'):
        os.environ.setdefault(var, '')

from .workflow import Omega3PWorkflow, S3PWorkflow
from .cubit import Cubit
from .ace3p import Omega3P, S3P
from .acdtool import Acdtool
from .tools import WriteOmega3PDataTable, WriteS3PDataTable, WriteXoptData
