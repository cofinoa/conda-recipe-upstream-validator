"""
Core dependency checking logic for upstream recipe validation.
"""

import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .r_packages import MappingConfig, load_mapping_config


def parse_description_deps(description_path: str) -> Dict[str, Set[str]]:
    """
    Parse R DESCRIPTION file and extract Depends, Imports, Suggests.

    Args:
        description_path: Path to DESCRIPTION file

    Returns:
        dict with keys 'depends', 'imports', 'suggests' containing sets of package names
    """
    result = {"depends": set(), "imports": set(), "suggests": set()}

    content = Path(description_path).read_text(encoding="utf-8")

    for field, key in [("Depends", "depends"), ("Imports", "imports"), ("Suggests", "suggests")]:
        pattern = rf"^{field}:\s*\n((?:[ \t]+.*\n)*)"
        match = re.search(pattern, content, re.MULTILINE)
        if not match:
            continue

        deps_block = match.group(1)
        # Split by comma and clean up
        for dep_str in deps_block.split(","):
            dep_str = dep_str.strip()
            if not dep_str:
                continue
            # Remove version constraints while preserving dots in package names.
            dep_name = re.split(r"\s*\(", dep_str, maxsplit=1)[0].strip()
            if dep_name and dep_name != "R":  # Ignore R itself
                result[key].add(dep_name)

    return result


def parse_meta_yaml_deps(meta_yaml_path: str) -> Set[str]:
    """
    Parse meta.yaml and extract host + run dependencies.

    Args:
        meta_yaml_path: Path to meta.yaml file

    Returns:
        set of package names (in conda format, e.g., "r-rcurl")
    """
    deps = set()

    content = Path(meta_yaml_path).read_text(encoding="utf-8")

    # Find host and run sections
    for section in ["host", "run"]:
        # Simple regex to find the section and its indented content
        pattern = rf"^\s*{section}:\s*\n((?:^\s+- .+$\n?)*)"
        matches = re.finditer(pattern, content, re.MULTILINE)
        for match in matches:
            deps_block = match.group(1)
            for line in deps_block.split("\n"):
                # Extract package name from "- r-rcurl" or "- r-package >=1.0"
                line = line.strip()
                if line.startswith("- "):
                    pkg = line[2:].strip()
                    # Remove comments and version constraints while preserving dots.
                    pkg = pkg.split("#", maxsplit=1)[0].strip()
                    pkg = re.split(r"\s+", pkg, maxsplit=1)[0].strip()
                    if pkg and not pkg.startswith("{"):  # Skip templated vars
                        deps.add(pkg)

    return deps


def normalize_r_package_name(r_name: str, config: MappingConfig | None = None) -> str:
    """
    Convert R package name to conda format (e.g., rJava -> r-rjava).

    Uses explicit mappings first; falls back to the configured generic rule.

    Args:
        r_name: R package name
        config: Optional mapping configuration. If not provided, default YAML
            configuration is loaded.

    Returns:
        Normalized conda package name
    """
    if config is None:
        config = load_mapping_config()

    if r_name in config.conda_name_map:
        return config.conda_name_map[r_name]

    normalized_name = r_name.lower() if config.default_lowercase else r_name
    return f"{config.default_prefix}{normalized_name}"


def check_dependencies(
    description_path: str = "DESCRIPTION",
    meta_yaml_path: str = "recipe/meta.yaml",
    strict: bool = False,
    mapping_override_yaml: str | None = None,
) -> Tuple[List[str], List[str]]:
    """
    Compare R package dependencies with conda recipe.

    Args:
        description_path: Path to DESCRIPTION file
        meta_yaml_path: Path to meta.yaml file
        strict: If True, Suggests are also checked (ERROR if missing)
        mapping_override_yaml: Optional YAML path with mapping/exclusion
            overrides to merge on top of package defaults.

    Returns:
        tuple (errors_list, warnings_list)
    """
    errors = []
    warnings = []

    # Parse both files
    r_deps = parse_description_deps(description_path)
    conda_deps = parse_meta_yaml_deps(meta_yaml_path)
    mapping_config = load_mapping_config(override_yaml_path=mapping_override_yaml)

    # Normalize R package names to conda format
    r_depends_conda = {
        normalize_r_package_name(pkg, mapping_config)
        for pkg in r_deps["depends"]
        if pkg not in mapping_config.excluded_from_recipe
    }
    r_imports_conda = {
        normalize_r_package_name(pkg, mapping_config)
        for pkg in r_deps["imports"]
        if pkg not in mapping_config.excluded_from_recipe
    }

    # Excluded packages are considered satisfied by policy and do not require
    # explicit conda recipe entries.

    # Check Depends (should be in conda host/run)
    for pkg in r_deps["depends"]:
        if pkg in mapping_config.excluded_from_recipe:
            continue
        pkg_conda = normalize_r_package_name(pkg, mapping_config)
        if pkg_conda not in conda_deps:
            errors.append(
                f"DESCRIPTION Depends '{pkg}' not found in meta.yaml (expected '{pkg_conda}')"
            )

    # Check Imports (should be in conda host/run)
    for pkg in r_deps["imports"]:
        if pkg in mapping_config.excluded_from_recipe:
            continue
        pkg_conda = normalize_r_package_name(pkg, mapping_config)
        if pkg_conda not in conda_deps:
            errors.append(
                f"DESCRIPTION Imports '{pkg}' not found in meta.yaml (expected '{pkg_conda}')"
            )

    # Check Suggests if strict mode
    if strict:
        for pkg in r_deps["suggests"]:
            if pkg in mapping_config.excluded_from_recipe:
                continue
            pkg_conda = normalize_r_package_name(pkg, mapping_config)
            if pkg_conda not in conda_deps:
                warnings.append(
                    f"DESCRIPTION Suggests '{pkg}' not found in meta.yaml (expected '{pkg_conda}')"
                )

    # Check for extra packages in meta.yaml that aren't in DESCRIPTION
    all_r_deps_conda = r_depends_conda | r_imports_conda
    for pkg in conda_deps:
        if not pkg.startswith("r-"):
            continue  # Skip non-R packages
        if pkg not in all_r_deps_conda and pkg != "r-base":
            warnings.append(
                f"meta.yaml contains '{pkg}' not declared in DESCRIPTION Depends/Imports"
            )

    return errors, warnings
