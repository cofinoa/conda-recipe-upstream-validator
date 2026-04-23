"""
Command-line interface for conda-recipe-upstream-validator.
"""

import argparse
import sys
from pathlib import Path

from upstream_recipe_validator.checker import check_dependencies


def main():
    """Run as script, expecting paths from command line or defaults."""
    parser = argparse.ArgumentParser(
        description="Validate conda recipe dependencies against upstream package metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --description DESCRIPTION --meta-yaml recipe/meta.yaml
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

    errors, warnings = check_dependencies(
        str(desc_path), str(meta_path), strict=args.strict
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
