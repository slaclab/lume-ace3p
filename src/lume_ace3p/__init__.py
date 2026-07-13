from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("lume-ace3p")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+unknown"

from .cubit import Cubit
from .ace3p import Omega3P, S3P
from .acdtool import Acdtool
from .geant4 import Geant4
from .particles import Particles
from .paths import resolve_paths
from .workflow_graph import Workflow
from .results import write_table, save_field, load_field
