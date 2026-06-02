import os, glob, shutil

from .site_defaults import SITE_DEFAULTS, detect_site

_MISSING = ''

def _autodetect_ace3p():
    found = shutil.which('omega3p')
    if found:
        return os.path.dirname(found) + os.sep
    matches = glob.glob('**/ace3p/bin', root_dir=os.path.expanduser('~'), recursive=True)
    if matches:
        return os.path.join(os.path.expanduser('~'), matches[0]) + os.sep
    return _MISSING

def _autodetect_cubit():
    found = shutil.which('cubit')
    if found:
        return os.path.dirname(found) + os.sep
    matches = glob.glob('**/Cubit*', root_dir=os.path.expanduser('~'), recursive=True)
    if matches:
        return os.path.join(os.path.expanduser('~'), matches[0]) + os.sep
    return _MISSING

def _autodetect_mpi():
    return shutil.which('mpirun') or _MISSING

def _autodetect_none():
    return _MISSING

_RESOLVERS = {
    'ace3p':           ('ACE3P_PATH',      _autodetect_ace3p),
    'cubit':           ('CUBIT_PATH',      _autodetect_cubit),
    'mpi':             ('MPI_CALLER',      _autodetect_mpi),
    'geant4_app_path': ('GEANT4_APP_PATH', _autodetect_none),
    'geant4_app_exe':  ('GEANT4_APP_EXE',  _autodetect_none),
}

def resolve_paths(yaml_overrides=None):
    """Resolve tool paths.

    Precedence (highest first): YAML override > environment variable
    > site default (matched by hostname) > autodetect on PATH/$HOME.

    Returns a dict with keys: ace3p, cubit, mpi, geant4_app_path,
    geant4_app_exe. Missing entries are returned as empty strings so
    callers can do truthiness checks (used by dry_run auto-enable).
    """
    overrides = yaml_overrides or {}
    site_paths = SITE_DEFAULTS.get(detect_site(), {})
    resolved = {}
    for key, (env_var, autodetect) in _RESOLVERS.items():
        resolved[key] = (overrides.get(key)
                         or os.environ.get(env_var)
                         or site_paths.get(key)
                         or autodetect())
    return resolved
