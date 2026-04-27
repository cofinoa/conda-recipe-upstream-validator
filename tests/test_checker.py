"""
Unit tests for upstream_recipe_validator.checker module.
"""

import tempfile
from pathlib import Path

from upstream_recipe_validator.checker import (
    check_dependencies,
    normalize_r_package_name,
    parse_description_deps,
    parse_meta_yaml_deps,
)
from upstream_recipe_validator.r_packages import load_mapping_config


def test_normalize_r_package_name():
    """Test R package name normalization."""
    assert normalize_r_package_name("rJava") == "r-rjava"
    assert normalize_r_package_name("RCurl") == "r-rcurl"
    assert normalize_r_package_name("abind") == "r-abind"


def test_parse_description_deps():
    """Test DESCRIPTION file parsing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("""Package: loadeR
Version: 1.0.0
Depends:
    R(>= 3.5.0),
    rJava,
    loadeR.java
Imports:
    utils,
    abind,
    RCurl
Suggests:
    transformeR,
    visualizeR
""")
        f.flush()

        result = parse_description_deps(f.name)

    assert "rJava" in result["depends"]
    assert "loadeR.java" in result["depends"]
    assert "R" not in result["depends"]  # R itself should be ignored

    assert "abind" in result["imports"]
    assert "RCurl" in result["imports"]
    assert "utils" in result["imports"]

    assert "transformeR" in result["suggests"]
    assert "visualizeR" in result["suggests"]

    Path(f.name).unlink()


def test_parse_meta_yaml_deps():
    """Test meta.yaml parsing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""package:
  name: r-loader
  version: 1.0.0

requirements:
  host:
    - r-base
    - r-rcurl
    - r-abind
  run:
    - r-base
    - r-rcurl
    - r-abind
    - r-loader.java
    - r-rjava
""")
        f.flush()

        result = parse_meta_yaml_deps(f.name)

    assert "r-base" in result
    assert "r-rcurl" in result
    assert "r-abind" in result
    assert "r-loader.java" in result
    assert "r-rjava" in result

    Path(f.name).unlink()


def test_check_dependencies_match():
    """Test when dependencies match."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
        desc.write("""Package: loadeR
Depends:
    R(>= 3.5.0),
    rJava
Imports:
    abind
""")
        desc.flush()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
            meta.write("""requirements:
  host:
    - r-base
    - r-rjava
    - r-abind
  run:
    - r-base
    - r-rjava
    - r-abind
""")
            meta.flush()

            errors, warnings = check_dependencies(desc.name, meta.name)

        assert len(errors) == 0
        assert len(warnings) == 0

        Path(desc.name).unlink()
        Path(meta.name).unlink()


def test_check_dependencies_missing_in_conda():
    """Test when DESCRIPTION has a package not in meta.yaml."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
        desc.write("""Package: loadeR
Depends:
    rJava,
    climate4R.UDG
Imports:
    abind
""")
        desc.flush()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
            meta.write("""requirements:
  host:
    - r-rjava
    - r-abind
  run:
    - r-rjava
    - r-abind
""")
            meta.flush()

            errors, warnings = check_dependencies(desc.name, meta.name)

        assert len(errors) == 1
        assert "climate4R.UDG" in errors[0]
        assert len(warnings) == 0

        Path(desc.name).unlink()
        Path(meta.name).unlink()


def test_check_dependencies_extra_in_conda():
    """Test when meta.yaml has a package not in DESCRIPTION."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
        desc.write("""Package: loadeR
Depends:
    rJava
Imports:
    abind
""")
        desc.flush()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
            meta.write("""requirements:
  host:
    - r-rjava
    - r-abind
    - r-extra-package
  run:
    - r-rjava
    - r-abind
    - r-extra-package
""")
            meta.flush()

            errors, warnings = check_dependencies(desc.name, meta.name)

        assert len(errors) == 0
        assert len(warnings) == 1
        assert "r-extra-package" in warnings[0]

        Path(desc.name).unlink()
        Path(meta.name).unlink()


def test_check_dependencies_excluded_package_not_required():
        """Excluded packages in mapping policy should not be required in meta.yaml."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
                desc.write("""Package: sample
Imports:
        utils,
        abind
""")
                desc.flush()

                with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
                        meta.write("""requirements:
    host:
        - r-abind
    run:
        - r-abind
""")
                        meta.flush()

                        errors, warnings = check_dependencies(desc.name, meta.name)

                assert len(errors) == 0
                assert len(warnings) == 0

                Path(desc.name).unlink()
                Path(meta.name).unlink()


def test_check_dependencies_override_yaml_mapping_and_exclusion():
        """Override YAML should update mappings and add exclusions."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
                desc.write("""Package: sample
Imports:
        RCurl,
        pkgToIgnore
""")
                desc.flush()

                with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
                        meta.write("""requirements:
    host:
        - custom-rcurl
    run:
        - custom-rcurl
""")
                        meta.flush()

                        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as override:
                                override.write("""conda_name_map:
    RCurl: custom-rcurl
exclude_from_recipe:
    - pkgToIgnore
""")
                                override.flush()

                                errors, warnings = check_dependencies(
                                        desc.name,
                                        meta.name,
                                        mapping_override_yaml=override.name,
                                )

                        Path(override.name).unlink()

                assert len(errors) == 0
                assert len(warnings) == 0

                Path(desc.name).unlink()
                Path(meta.name).unlink()


def test_load_mapping_config_override_shadows_without_replacing():
        """Override should shadow selected keys and keep remaining generic mappings."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as override:
                override.write("""conda_name_map:
    NewPkg: custom-newpkg
exclude_from_recipe:
    - anotherIgnoredPkg
""")
                override.flush()

                config = load_mapping_config(override_yaml_path=override.name)

        # New mapping from override is added
        assert config.conda_name_map["NewPkg"] == "custom-newpkg"
        # Existing generic mappings from base YAML are preserved
        assert "DBI" in config.conda_name_map
        # Override exclusions are merged with base exclusions
        assert "anotherIgnoredPkg" in config.excluded_from_recipe
        assert "utils" in config.excluded_from_recipe

        Path(override.name).unlink()
