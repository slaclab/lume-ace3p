import os, glob, shutil

if 'NERSC_HOST' in os.environ:
    PLATFORM = os.environ['NERSC_HOST']
elif 'HOSTNAME' in os.environ:
    PLATFORM = os.environ['HOSTNAME']
else
    PLATFORM = os.uname()[1]
    
if PLATFORM.startswith('sdf'):
    os.environ['ACE3P_PATH'] = '/sdf/group/rfar/ace3p/bin/'
    os.environ['CUBIT_PATH'] = '/sdf/group/rfar/software/'
    os.environ['MPI_CALLER'] = 'mpirun'
elif PLATFORM.startswith('perlmutter'):
    os.environ['ACE3P_PATH'] = '/global/cfs/cdirs/ace3p/perlmutter/CPU/'
    os.environ['CUBIT_PATH'] = '/global/cfs/cdirs/ace3p/tools'
    os.environ['MPI_CALLER'] = 'srun'
else
    ace3p_dir = shutil.which('omega3p')
    if ace3p_dir is not None:   #Check if ace3p modules are defined in environment
        ace3p_dir = ace3p_dir.strip('omega3p')
        os.environ['ACE3P_PATH'] = ace3p_dir
    else: 
        #Try checking if ace3p is installed in the local home directory
        ace3p_dir = glob.glob('**/ace3p/bin',root_dir=os.path.expanduser('~'),recursive=True)
        assert len(ace3p_dir)>0, 'The ace3p installation was not found.'
        os.environ['ACE3P_PATH'] = ace3p_dir[0]
    cubit_dir = shutil.which('cubit')   #Check if cubit is defined in environment
    if cubit_dir is not None:
        cubit_dir = cubit_dir.strip('cubit')
        os.environ['CUBIT_PATH'] = cubit_dir
    else:
        #Try checking if cubit is installed in the local home directory
        cubit_dir = glob.glob('**/Cubit*',root_dir=os.path.expanduser('~'),recursive=True)
        assert len(cubit_dir)>0, 'The cubit installation was not found.'
        os.environ['CUBIT_PATH'] = cubit_dir[0]
    os.environ['MPI_CALLER'] = shutil.which('mpirun')  #Try checking if local system has mpirun installed
    assert os.environ['MPI_CALLER'] is not None, 'MPI installation not found.'