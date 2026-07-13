"""Sphinx configuration for lume-ace3p documentation."""

import os
import sys
from datetime import datetime

import tomllib

sys.path.insert(0, os.path.abspath("../src"))

# Single source of truth: read the version straight from pyproject.toml. We parse
# the file rather than importing the package because the Read the Docs build only
# installs the Sphinx toolchain (docs/requirements.txt), not lume-ace3p or its
# runtime deps (numpy/pandas/xopt) — and sphinx-autoapi documents the source
# statically, so no package import is needed for the build.
with open(os.path.join(os.path.dirname(__file__), "..", "pyproject.toml"), "rb") as _f:
    _version = tomllib.load(_f)["project"]["version"]

project = "lume-ace3p"
author = "David Bizzozero, Lila Fowler"
copyright = f"{datetime.now().year}, SLAC National Accelerator Laboratory"
release = _version
version = ".".join(_version.split(".")[:2])

extensions = [
    "myst_parser",
    "autoapi.extension",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

source_suffix = {
    ".md": "markdown",
    ".rst": "restructuredtext",
}

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "linkify",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3

autoapi_type = "python"
autoapi_dirs = ["../src/lume_ace3p"]
autoapi_root = "api"
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
    "imported-members",
]
autoapi_keep_files = False
autoapi_add_toctree_entry = True
autoapi_python_class_content = "both"
autoapi_member_order = "groupwise"

napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False

html_theme = "furo"
html_static_path = ["_static"]
html_logo = "_static/SLAC-lab-hires.png"
html_title = "lume-ace3p"
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
}

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
    "xopt": ("https://xopt.xopt.org/", None),
}
intersphinx_disabled_reftypes = ["std:doc"]

nitpicky = False
