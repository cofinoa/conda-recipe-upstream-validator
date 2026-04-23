"""
conda-recipe-upstream-validator: Validate conda recipe dependencies against upstream package metadata.

This module provides tools to compare dependencies declared in upstream package metadata
(e.g., R's DESCRIPTION file) with those listed in conda recipes (meta.yaml).
"""

__version__ = "0.1.0"
__author__ = "Antonio S. Cofiño"
__author_orcid__ = "0000-0001-7719-979X"
__author_github__ = "@cofinoa"
__license__ = "GPL-3.0-or-later"

from upstream_recipe_validator.checker import check_dependencies

__all__ = ["check_dependencies"]
