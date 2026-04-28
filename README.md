# conda-recipe-upstream-validator

Validate conda recipe dependencies against upstream package metadata.

## Summary

`conda-recipe-upstream-validator` is a Python command-line tool and library that
compares dependency declarations in upstream package metadata (currently R's
`DESCRIPTION` file) with those listed in a conda recipe (`meta.yaml`). It is
intended for use by conda-forge recipe maintainers and package authors who want
to catch missing, extra, or mismatched dependencies when updating conda recipes
to new upstream versions.

## Statement of need

Conda recipes for R packages (and other language ecosystems) must manually mirror
the dependency information already declared in upstream metadata files. When an
upstream package releases a new version, new dependencies may be introduced or
existing ones removed, but these changes are often not reflected in the conda
recipe. This creates a gap that can lead to runtime failures, broken environments,
or silent omissions in released conda packages.

Existing tools such as `conda-smithy` do not currently perform this cross-check
automatically (see [conda-smithy#2311](https://github.com/conda-forge/conda-smithy/issues/2311)).
`conda-recipe-upstream-validator` fills this gap by providing an explicit,
automated validation step that can be integrated into recipe build scripts or
CI pipelines.

## Software design

The validator is structured around three concerns:

- **Parsing**: Extract dependency declarations from upstream metadata
  (`DESCRIPTION`) and from the conda recipe (`meta.yaml`), including version
  constraints.
- **Mapping**: Translate upstream package names to conda package names using a
  two-layer YAML configuration system — a generic base mapping bundled with the
  package, and an optional recipe-specific partial override.
- **Validation**: Compare the two sets of dependencies, reporting errors
  (missing or extra packages) and warnings (version constraint mismatches,
  packages absent from `host` or `run` sections, imprecise constraints, or
  recipe-specific override in use).

Version constraints are normalised before comparison (R uses `-`, conda uses `_`;
both are mapped to `.`). The host and run sections of `meta.yaml` are checked
independently: for pure R packages, a dependency should appear in both sections,
and the tool warns when this symmetry is broken.

The tool is designed to be non-breaking by default: warnings do not cause a
non-zero exit code unless `--strict` is used.

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
--meta-yaml PATH            Path to conda recipe meta.yaml (default: recipe/meta.yaml)
--mapping-override-yaml     Optional override YAML (partial merge over generic mapping)
--strict                    Treat Suggests/missing metadata as errors (default: warnings)
--exit-code N               Exit code on errors (default: 1)
--help                      Show help message
```

## Generic and recipe-specific YAML mapping

The validator uses two YAML layers:

1. **Generic base mapping** (always loaded):
   [`upstream_recipe_validator/r_packages.yaml`](upstream_recipe_validator/r_packages.yaml)
2. **Recipe-specific override** (optional, auto-detected):
   `recipe/upstream_recipe_validator.yaml`
3. **Explicit override** (optional):
   `--mapping-override-yaml /path/to/override.yaml`

Precedence (highest to lowest):
- Explicit `--mapping-override-yaml`
- Recipe-local `recipe/upstream_recipe_validator.yaml`
- Generic `upstream_recipe_validator/r_packages.yaml`

The recipe-specific YAML is a **partial override**, not a full replacement.
It is merged on top of the generic mapping:

- `conda_name_map`: override values shadow same keys; new keys are added.
- `exclude_from_recipe`: merged (union, de-duplicated).
- `default_mapping`: only provided fields are updated.

When a recipe-specific override is active, the tool emits a warning that
validation may be incomplete or skipping packages that differ from the generic
policy.

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

Other upstream formats (Python `setup.py`, Node `package.json`, etc.) can be
added in future versions.

### Conda recipes
- `meta.yaml` (host and run sections)

## Testing

The test suite uses [pytest](https://pytest.org). To run all tests:

```bash
pip install -e ".[dev]"
pytest -v
```

Tests cover name normalisation, constraint parsing, version comparison,
dependency matching, mapping overrides, and host/run section symmetry checks.

## Citation

If you use this software in academic work, please cite it using the information
provided in [CITATION.cff](CITATION.cff).

Preferred citation:

> Antonio S. Cofiño (2026). *conda-recipe-upstream-validator: Validate conda
> recipe dependencies against upstream package metadata*. Version 0.1.0.
> https://github.com/cofinoa/conda-recipe-upstream-validator

## Authorship and AI-assisted development

This project is authored and maintained by Antonio S. Cofiño.

Some parts of the source code and documentation may have been drafted with the
assistance of large language model based tools. These tools were used as
development assistants under human direction, supervision, review, and
validation.

The ideas, requirements, software design, scientific context, integration
decisions, and final acceptance of the code remain the responsibility of the
human author and maintainer.

AI-generated suggestions are not treated as independent authorship contributions
and are not considered authors or copyright holders of this software.

See [AI_USAGE.md](AI_USAGE.md) for a more detailed disclosure.

## Scholarly contribution

This software should be understood as a scholarly research output. Its
contribution is not limited to the source code itself, but includes the design
decisions, validation procedures, documentation, interoperability with existing
scientific tools, and its role in supporting reproducible scientific workflows.

Research software of this kind is increasingly recognised as a primary research
artefact in the computational sciences. Its scholarly impact is appropriately
evaluated using software-specific evidence: persistent identifiers for archived
releases, citations to specific versions, adoption and reuse in scientific
workflows, integration with community infrastructure (such as conda-forge),
quality of documentation, and long-term maintenance commitment.

The [CITATION.cff](CITATION.cff) file provides structured citation metadata
following the Citation File Format standard, which is supported by major
software archives and citation tools. Archived releases with persistent
identifiers will be made available through Zenodo or an equivalent repository
as the software matures.

## License

This software is distributed under the BSD 3-Clause License. See [LICENSE](LICENSE) for details.
