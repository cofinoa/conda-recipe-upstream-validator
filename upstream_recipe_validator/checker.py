"""
Core dependency checking logic for upstream recipe validation.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .r_packages import MappingConfig, load_mapping_config

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

#: Regex that extracts version constraints of the form >=1.0 or >= 1.0.0
_CONSTRAINT_RE = re.compile(r"(>=|<=|==|!=|>(?!=)|<(?!=))\s*([\w.\-]+)")


@dataclass(frozen=True)
class VersionConstraint:
    """A single version constraint: an operator and a version string."""

    operator: str  # one of >=, >, <=, <, ==, !=
    version: str   # raw version string as found in the file

    @property
    def normalized_version(self) -> str:
        """Normalize version for cross-format comparison (R '-' / conda '_' → '.')."""
        return re.sub(r"[-_]", ".", self.version)

    def __str__(self) -> str:
        return f"{self.operator}{self.version}"


@dataclass
class PackageDep:
    """A dependency entry: a package name plus zero or more version constraints."""

    name: str
    constraints: List[VersionConstraint] = field(default_factory=list)

    @property
    def has_constraints(self) -> bool:
        return bool(self.constraints)

    @property
    def constraints_str(self) -> str:
        return ", ".join(str(c) for c in self.constraints)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_constraints(text: str) -> List[VersionConstraint]:
    """Extract all version constraints from a parenthesised string like '>= 1.0, < 2.0'."""
    return [
        VersionConstraint(operator=m.group(1), version=m.group(2))
        for m in _CONSTRAINT_RE.finditer(text)
    ]


def _parse_dep_entry(dep_str: str) -> Optional[PackageDep]:
    """
    Parse a single dependency entry such as ``rJava (>= 0.9-8)`` or just ``abind``.

    Returns None for entries that should be ignored (e.g. ``R`` itself).
    """
    dep_str = dep_str.strip()
    if not dep_str:
        return None

    parts = re.split(r"\s*\(", dep_str, maxsplit=1)
    name = parts[0].strip()

    if not name or name == "R":
        return None

    constraints: List[VersionConstraint] = []
    if len(parts) > 1:
        constraints = _parse_constraints(parts[1].rstrip(")"))

    return PackageDep(name=name, constraints=constraints)


# ---------------------------------------------------------------------------
# Public parsers
# ---------------------------------------------------------------------------

def parse_description_deps(description_path: str) -> Dict[str, Dict[str, PackageDep]]:
    """
    Parse an R DESCRIPTION file and extract Depends, Imports, Suggests.

    Returns:
        dict with keys ``'depends'``, ``'imports'``, ``'suggests'`` whose values
        are ``{package_name: PackageDep}`` dicts (including version constraints).
    """
    result: Dict[str, Dict[str, PackageDep]] = {
        "depends": {}, "imports": {}, "suggests": {}
    }

    content = Path(description_path).read_text(encoding="utf-8")

    for field_name, key in [
        ("Depends", "depends"),
        ("Imports", "imports"),
        ("Suggests", "suggests"),
    ]:
        # DCF format: field starts at column 0; continuation lines are indented.
        # The value may begin on the same line as the field name or on the next.
        pattern = rf"^{field_name}:([ \t]*.*(?:\n[ \t]+.*)*)"
        match = re.search(pattern, content, re.MULTILINE)
        if not match:
            continue

        # Collapse continuation-line whitespace so we can split on commas.
        block = re.sub(r"\n\s+", " ", match.group(1).strip())

        for dep_str in block.split(","):
            dep = _parse_dep_entry(dep_str)
            if dep is not None:
                result[key][dep.name] = dep

    return result


@dataclass
class RecipeDeps:
    """Dependencies parsed from a conda meta.yaml, split by section."""

    host: Dict[str, PackageDep] = field(default_factory=dict)
    run: Dict[str, PackageDep] = field(default_factory=dict)

    def in_host(self, name: str) -> bool:
        return name in self.host

    def in_run(self, name: str) -> bool:
        return name in self.run

    def in_both(self, name: str) -> bool:
        return name in self.host and name in self.run

    def get(self, name: str) -> Optional[PackageDep]:
        """Return the dep entry preferring run (where version pins usually live)."""
        return self.run.get(name) or self.host.get(name)


def _parse_section(content: str, section: str) -> Dict[str, PackageDep]:
    deps: Dict[str, PackageDep] = {}
    pattern = rf"^\s*{section}:\s*\n((?:^\s+- .+$\n?)*)"
    for match in re.finditer(pattern, content, re.MULTILINE):
        for line in match.group(1).splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            entry = line[2:].split("#", maxsplit=1)[0].strip()
            if not entry or entry.startswith("{"):
                continue
            tokens = re.split(r"\s+", entry, maxsplit=1)
            pkg_name = tokens[0].strip()
            if not pkg_name:
                continue
            constraints: List[VersionConstraint] = []
            if len(tokens) > 1:
                constraints = _parse_constraints(tokens[1])
            if pkg_name not in deps or (constraints and not deps[pkg_name].has_constraints):
                deps[pkg_name] = PackageDep(name=pkg_name, constraints=constraints)
    return deps


def parse_meta_yaml_deps(meta_yaml_path: str) -> RecipeDeps:
    """
    Parse a conda ``meta.yaml`` and extract host and run dependencies separately.

    Returns:
        A :class:`RecipeDeps` with ``.host`` and ``.run`` dicts
        ``{conda_package_name: PackageDep}`` (version constraints included).
    """
    # TODO: add LinkingTo support — packages in DESCRIPTION LinkingTo should be
    # required in host only (compile-time headers), not in run.
    content = Path(meta_yaml_path).read_text(encoding="utf-8")
    return RecipeDeps(
        host=_parse_section(content, "host"),
        run=_parse_section(content, "run"),
    )


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

def normalize_r_package_name(r_name: str, config: Optional[MappingConfig] = None) -> str:
    """
    Convert an R package name to its conda-forge equivalent.

    Uses explicit mappings from the YAML config first; falls back to the
    generic rule ``{prefix}{lowercase_name}``.
    """
    if config is None:
        config = load_mapping_config()

    if r_name in config.conda_name_map:
        return config.conda_name_map[r_name]

    normalized = r_name.lower() if config.default_lowercase else r_name
    return f"{config.default_prefix}{normalized}"


# ---------------------------------------------------------------------------
# Version alignment check
# ---------------------------------------------------------------------------

def _version_warnings(
    r_pkg: str,
    upstream: PackageDep,
    conda: PackageDep,
) -> List[str]:
    """
    Compare version constraints for one package and return imprecision warnings.

    Rules:
    - Upstream has constraints, conda has none → WARN (imprecise: any version
      is accepted where upstream requires a minimum or range).
    - Both have constraints that differ after normalization → WARN (mismatch:
      shown so the maintainer can decide which is correct).
    - Upstream has no constraints → nothing to validate.
    """
    if not upstream.has_constraints:
        return []

    if not conda.has_constraints:
        return [
            f"version constraint mismatch for '{r_pkg}': "
            f"DESCRIPTION requires [{upstream.constraints_str}] "
            f"but meta.yaml has no version constraint"
        ]

    upstream_set = {
        (c.operator, c.normalized_version) for c in upstream.constraints
    }
    conda_set = {
        (c.operator, c.normalized_version) for c in conda.constraints
    }

    if upstream_set != conda_set:
        return [
            f"version constraint mismatch for '{r_pkg}': "
            f"DESCRIPTION=[{upstream.constraints_str}], "
            f"meta.yaml=[{conda.constraints_str}]"
        ]

    return []


# ---------------------------------------------------------------------------
# Main checker
# ---------------------------------------------------------------------------

def check_dependencies(
    description_path: str = "DESCRIPTION",
    meta_yaml_path: str = "recipe/meta.yaml",
    strict: bool = False,
    mapping_override_yaml: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """
    Compare R package dependencies with conda recipe.

    Args:
        description_path: Path to DESCRIPTION file.
        meta_yaml_path: Path to conda recipe meta.yaml.
        strict: If True, Suggests are also checked (ERROR if missing).
        mapping_override_yaml: Optional YAML path with mapping/exclusion
            overrides to merge on top of package defaults.

    Returns:
        ``(errors, warnings)`` — both are lists of human-readable strings.
    """
    errors: List[str] = []
    warnings: List[str] = []

    r_deps = parse_description_deps(description_path)
    recipe = parse_meta_yaml_deps(meta_yaml_path)
    mapping_config = load_mapping_config(override_yaml_path=mapping_override_yaml)

    def _check_field(
        field_deps: Dict[str, PackageDep],
        field_label: str,
        missing_is_error: bool,
    ) -> None:
        for r_pkg, upstream_dep in field_deps.items():
            if r_pkg in mapping_config.excluded_from_recipe:
                continue

            pkg_conda = normalize_r_package_name(r_pkg, mapping_config)

            in_host = recipe.in_host(pkg_conda)
            in_run = recipe.in_run(pkg_conda)

            if not in_host and not in_run:
                msg = (
                    f"DESCRIPTION {field_label} '{r_pkg}' not found in meta.yaml "
                    f"(expected '{pkg_conda}')"
                )
                if missing_is_error:
                    errors.append(msg)
                else:
                    warnings.append(msg)
                continue

            if in_host and not in_run:
                warnings.append(
                    f"'{pkg_conda}' is in meta.yaml host but missing from run "
                    f"(DESCRIPTION {field_label} '{r_pkg}')"
                )
            elif in_run and not in_host:
                warnings.append(
                    f"'{pkg_conda}' is in meta.yaml run but missing from host "
                    f"(DESCRIPTION {field_label} '{r_pkg}')"
                )

            # Package is present — check version constraints.
            conda_dep = recipe.get(pkg_conda)
            if conda_dep is not None:
                warnings.extend(_version_warnings(r_pkg, upstream_dep, conda_dep))

    _check_field(r_deps["depends"], "Depends", missing_is_error=True)
    _check_field(r_deps["imports"], "Imports", missing_is_error=True)
    if strict:
        _check_field(r_deps["suggests"], "Suggests", missing_is_error=False)

    # Check for extra R packages in meta.yaml not declared in DESCRIPTION.
    all_r_names_conda: Set[str] = {
        normalize_r_package_name(p, mapping_config)
        for field in ("depends", "imports")
        for p in r_deps[field]
        if p not in mapping_config.excluded_from_recipe
    }
    all_recipe_r_pkgs = set(recipe.host) | set(recipe.run)
    for pkg in all_recipe_r_pkgs:
        if not pkg.startswith("r-"):
            continue
        if pkg not in all_r_names_conda and pkg != "r-base":
            warnings.append(
                f"meta.yaml contains '{pkg}' not declared in DESCRIPTION Depends/Imports"
            )

    return errors, warnings
