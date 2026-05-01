"""
R package mapping configuration loaded from YAML.

The configuration supports:
  - a default mapping rule (prefix + lowercase)
  - explicit package-level mapping overrides
  - package exclusions (dependencies that should not be required in conda)
  - optional override YAML merged on top of the default one
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

import yaml


@dataclass(frozen=True)
class MappingConfig:
  """Resolved mapping configuration used by the checker."""

  default_prefix: str
  default_lowercase: bool
  excluded_from_recipe: FrozenSet[str]
  conda_name_map: Dict[str, str]


_DEFAULT_CONFIG_PATH = Path(__file__).parent / "r_packages.yaml"


def _read_yaml(path: Path) -> Dict[str, Any]:
  data = yaml.safe_load(path.read_text(encoding="utf-8"))
  if data is None:
    return {}
  if not isinstance(data, dict):
    raise ValueError(f"Expected YAML mapping at {path}, got {type(data).__name__}")
  return data


def _merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
  merged = dict(base)

  for key, value in override.items():
    if key in {"conda_name_map", "name_map"}:
      current = dict(merged.get(key, {}))
      current.update(value or {})
      merged[key] = current
      continue

    if key in {"exclude_from_recipe", "base_packages", "recommended_packages"}:
      current = list(merged.get(key, []))
      current.extend(value or [])
      # Keep insertion order but remove duplicates
      merged[key] = list(dict.fromkeys(current))
      continue

    if key == "default_mapping" and isinstance(value, dict):
      current = dict(merged.get("default_mapping", {}))
      current.update(value)
      merged["default_mapping"] = current
      continue

    merged[key] = value

  return merged


def _resolve_config(data: Dict[str, Any]) -> MappingConfig:
  default_mapping = data.get("default_mapping", {})
  default_prefix = str(default_mapping.get("prefix", "r-"))
  default_lowercase = bool(default_mapping.get("lowercase", True))

  # Backward compatible keys: if exclude_from_recipe is not present,
  # default to old base_packages behavior.
  excluded_list = data.get("exclude_from_recipe")
  if excluded_list is None:
    excluded_list = data.get("base_packages", [])

  name_map = dict(data.get("conda_name_map", {}))
  name_map.update(data.get("name_map", {}))

  return MappingConfig(
    default_prefix=default_prefix,
    default_lowercase=default_lowercase,
    excluded_from_recipe=frozenset(excluded_list or []),
    conda_name_map=name_map,
  )


def load_mapping_config(override_yaml_path: Optional[str] = None) -> MappingConfig:
  """
  Load mapping configuration from package default YAML plus optional override.

  Args:
    override_yaml_path: Optional path to a user YAML merged on top of default.

  Returns:
    A resolved MappingConfig object.
  """
  base_data = _read_yaml(_DEFAULT_CONFIG_PATH)

  if override_yaml_path:
    override_data = _read_yaml(Path(override_yaml_path))
    base_data = _merge_config(base_data, override_data)

  return _resolve_config(base_data)
