# conda-recipe-upstream-validator

Validate conda recipe dependencies against upstream package metadata.

This tool compares dependencies declared in upstream package metadata (e.g., R's `DESCRIPTION` file) with those listed in conda recipes (`meta.yaml`). It helps catch mismatches that can lead to missing or incorrect dependencies at runtime.

## Motivation

When conda recipes are updated to new upstream versions, the upstream `DESCRIPTION` (or equivalent metadata) may include new or removed dependencies, but these changes are often not reflected in `meta.yaml`. This tool helps prevent inconsistencies.

See [conda-smithy#2311](https://github.com/conda-forge/conda-smithy/issues/2311) for more context.

## Installation

```bash
pip install conda-recipe-upstream-validator
```

Or from source:

```bash
git clone https://github.com/cofinoa/conda-recipe-upstream-validator.git
cd conda-recipe-upstream-validator
pip install -e .
```

## Usage

### As a CLI tool

```bash
conda-recipe-upstream-validator \
  --description /path/to/DESCRIPTION \
  --meta-yaml recipe/meta.yaml
```

### In conda `build.sh`

```bash
conda-recipe-upstream-validator \
  --description "$SRC_DIR/DESCRIPTION" \
  --meta-yaml recipe/meta.yaml || exit 1
```

### In `meta.yaml` build section

```yaml
build:
  number: 0

requirements:
  build:
    - python
    - pip
    - conda-recipe-upstream-validator

script: |
  conda-recipe-upstream-validator --description $SRC_DIR/DESCRIPTION --meta-yaml recipe/meta.yaml
  python -m pip install .
```

### As a Python library

```python
from upstream_recipe_validator.checker import check_dependencies

errors, warnings = check_dependencies(
    description_path="DESCRIPTION",
    meta_yaml_path="recipe/meta.yaml",
    strict=False
)

for err in errors:
    print(f"ERROR: {err}")

for warn in warnings:
    print(f"WARNING: {warn}")
```

## Command-line options

```
--description PATH          Path to upstream metadata file (default: DESCRIPTION)
--meta-yaml PATH           Path to conda recipe meta.yaml (default: recipe/meta.yaml)
--mapping-override-yaml    Optional override YAML (partial merge over generic mapping)
--strict                   Treat Suggests/missing metadata as errors (default: warnings)
--exit-code N              Exit code on errors (default: 1)
--help                     Show help message
```

## Generic and recipe-specific YAML mapping

The validator uses two YAML layers:

1. Generic base mapping (always loaded):
   [upstream_recipe_validator/r_packages.yaml](upstream_recipe_validator/r_packages.yaml)
2. Recipe-specific override (optional):
  `recipe/upstream_recipe_validator.yaml` (auto-detected).
3. Explicit override (optional):
  `--mapping-override-yaml /path/to/override.yaml`.

Precedence (highest to lowest):
- Explicit `--mapping-override-yaml`
- Recipe-local `recipe/upstream_recipe_validator.yaml`
- Generic `upstream_recipe_validator/r_packages.yaml`

Important: the recipe-specific YAML is a partial override, not a full replacement.
It is merged on top of the generic one:

- `conda_name_map`: override values shadow same keys in base map, new keys are added.
- `exclude_from_recipe`: merged (union-like append + de-dup).
- `default_mapping`: only provided fields are updated.

Example recipe-specific YAML:

```yaml
default_mapping:
  prefix: r-
  lowercase: true

conda_name_map:
  RCurl: r-rcurl-custom
  MySpecialPkg: custom-myspecialpkg

exclude_from_recipe:
  - somePkgHandledElsewhere
```

## Output

### Success
```
✅ All dependencies match between upstream and conda recipe
```

### With warnings
```
🟡 WARNINGS:
  - meta.yaml contains 'r-extra' not declared in DESCRIPTION Depends/Imports
```

### With errors
```
🔴 ERRORS (dependency mismatch):
  - DESCRIPTION Depends 'package' not found in meta.yaml (expected 'r-package')
  - DESCRIPTION Imports 'abind' not found in meta.yaml (expected 'r-abind')
```

## Currently supported formats

### Upstream metadata
- **R packages**: `DESCRIPTION` file (Depends, Imports, Suggests fields)
- Others can be added (Python `setup.py`, Node `package.json`, etc.)

### Conda recipes
- `meta.yaml` (host and run sections)

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) for details.

Any modifications or derivative works must also be distributed under the same license,
ensuring that improvements flow back to the community.

## Authors

- **Antonio S. Cofiño** ([ORCID](https://orcid.org/0000-0001-7719-979X), [@cofinoa](https://github.com/cofinoa))

## Contributing

Contributions are welcome! Please ensure that:

1. You follow the existing code style
2. You add tests for new functionality
3. Your changes are licensed under GPL-3.0-or-later
4. You provide clear commit messages

## Issues

Report issues, bugs, or feature requests on [GitHub Issues](https://github.com/cofinoa/conda-recipe-upstream-validator/issues).
