"""Sphinx configuration for lume-ace3p documentation."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath("../src"))

project = "lume-ace3p"
author = "David Bizzozero, Lila Fowler"
copyright = f"{datetime.now().year}, SLAC National Accelerator Laboratory"
release = "0.1.0"

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
