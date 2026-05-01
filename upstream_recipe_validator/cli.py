"""
Command-line interface for conda-recipe-upstream-validator.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from upstream_recipe_validator.checker import check_dependencies


RECIPE_OVERRIDE_FILENAME = "upstream_recipe_validator.yaml"


def _resolve_mapping_override_yaml(
    meta_path: Path, explicit_override: Optional[str]
) -> Optional[str]:
    """
    Resolve override YAML path.

    Priority:
      1) Explicit --mapping-override-yaml argument
      2) Recipe-local special file next to meta.yaml
    """
    if explicit_override:
        return explicit_override

    candidate = meta_path.parent / RECIPE_OVERRIDE_FILENAME
    if candidate.exists():
        return str(candidate)

    return None


def main():
    """Run as script, expecting paths from command line or defaults."""
    parser = argparse.ArgumentParser(
        description="Validate conda recipe dependencies against upstream package metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --description DESCRIPTION --meta-yaml recipe/meta.yaml
    %(prog)s --mapping-override-yaml custom_r_map.yaml
  %(prog)s --strict --exit-code 2
        """,
    )
    parser.add_argument(
        "--description",
        default="DESCRIPTION",
        help="Path to upstream metadata file (default: DESCRIPTION)",
    )
    parser.add_argument(
        "--meta-yaml",
        default="recipe/meta.yaml",
        help="Path to conda recipe meta.yaml (default: recipe/meta.yaml)",
    )
    parser.add_argument(
        "--mapping-override-yaml",
        default=None,
        help=(
            "Optional YAML with mapping/exclusion overrides merged on top of "
            "the built-in package mapping (partial override, not full replacement)"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat Suggests/missing metadata as errors (default: warnings only)",
    )
    parser.add_argument(
        "--exit-code",
        type=int,
        default=1,
        help="Exit code on errors (default: 1)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    args = parser.parse_args()

    desc_path = Path(args.description)
    meta_path = Path(args.meta_yaml)

    if not desc_path.exists():
        print(f"ERROR: {desc_path} not found", file=sys.stderr)
        sys.exit(2)

    if not meta_path.exists():
        print(f"ERROR: {meta_path} not found", file=sys.stderr)
        sys.exit(2)

    mapping_override_yaml = _resolve_mapping_override_yaml(
        meta_path, args.mapping_override_yaml
    )

    if mapping_override_yaml:
        print(
            f"⚠️  NOTE: Recipe-specific mapping override in use ({mapping_override_yaml}). "
            "Validation may be incomplete or skipping packages that differ from the generic policy.",
            file=sys.stderr,
        )

    errors, warnings = check_dependencies(
        str(desc_path),
        str(meta_path),
        strict=args.strict,
        mapping_override_yaml=mapping_override_yaml,
    )

    # Print results
    if errors:
        print("🔴 ERRORS (dependency mismatch):")
        for err in errors:
            print(f"  - {err}")

    if warnings:
        print("🟡 WARNINGS:")
        for warn in warnings:
            print(f"  - {warn}")

    if not errors and not warnings:
        print("✅ All dependencies match between upstream and conda recipe")

    # Exit with error if there are errors
    if errors:
        sys.exit(args.exit_code)

    sys.exit(0)


if __name__ == "__main__":
    main()
