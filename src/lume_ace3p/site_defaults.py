import os

SITE_DEFAULTS = {
    'sdf': {
        'ace3p': '/sdf/group/rfar/ace3p/bin/',
        'cubit': '/sdf/group/rfar/software/',
        'mpi':   'srun',
    },
    'perlmutter': {
        'ace3p': '/global/cfs/cdirs/ace3p/perlmutter/CPU/',
        'cubit': '/global/cfs/cdirs/ace3p/perlmutter/CPU/',
        'mpi':   'srun',
    },
}

def detect_site():
    host = (os.environ.get('NERSC_HOST')
            or os.environ.get('HOSTNAME')
            or os.uname()[1])
    for prefix in SITE_DEFAULTS:
        if host.startswith(prefix):
            return prefix
    return None
