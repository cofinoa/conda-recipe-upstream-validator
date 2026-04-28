"""
Unit tests for upstream_recipe_validator.checker module.
"""

import tempfile
from pathlib import Path

from upstream_recipe_validator.checker import (
    PackageDep,
    RecipeDeps,
    VersionConstraint,
    _parse_constraints,
    _version_warnings,
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
    """Test DESCRIPTION file parsing returns package names and constraints."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("""Package: loadeR
Version: 1.0.0
Depends:
    R(>= 3.5.0),
    rJava (>= 0.9-8),
    loadeR.java
Imports:
    utils,
    abind,
    RCurl (>= 1.95)
Suggests:
    transformeR,
    visualizeR
""")
        f.flush()

        result = parse_description_deps(f.name)

    assert "rJava" in result["depends"]
    assert "loadeR.java" in result["depends"]
    assert "R" not in result["depends"]  # R itself should be ignored

    # Version constraints are preserved
    rjava_dep = result["depends"]["rJava"]
    assert rjava_dep.has_constraints
    assert rjava_dep.constraints[0].operator == ">="
    assert rjava_dep.constraints[0].normalized_version == "0.9.8"

    assert "abind" in result["imports"]
    assert "RCurl" in result["imports"]
    assert "utils" in result["imports"]

    rcurl_dep = result["imports"]["RCurl"]
    assert rcurl_dep.has_constraints
    assert rcurl_dep.constraints[0].version == "1.95"

    assert not result["imports"]["abind"].has_constraints

    assert "transformeR" in result["suggests"]
    assert "visualizeR" in result["suggests"]

    Path(f.name).unlink()


def test_parse_meta_yaml_deps():
    """Test meta.yaml parsing returns package names and constraints."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""package:
  name: r-loader
  version: 1.0.0

requirements:
  host:
    - r-base
    - r-rcurl >=1.95
    - r-abind
  run:
    - r-base
    - r-rcurl >=1.95
    - r-abind
    - r-loader.java
    - r-rjava >=0.9_8
""")
        f.flush()

        result = parse_meta_yaml_deps(f.name)

    assert "r-base" in result.host
    assert "r-rcurl" in result.host
    assert "r-abind" in result.host

    # r-loader.java and r-rjava are only in run in this fixture
    assert "r-loader.java" in result.run
    assert "r-rjava" in result.run

    assert result.run["r-rjava"].has_constraints
    assert result.run["r-rjava"].constraints[0].operator == ">="
    assert result.run["r-rjava"].constraints[0].normalized_version == "0.9.8"

    assert result.host["r-rcurl"].has_constraints
    assert result.host["r-rcurl"].constraints[0].version == "1.95"

    assert not result.host["r-abind"].has_constraints

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


# ---------------------------------------------------------------------------
# Version constraint tests
# ---------------------------------------------------------------------------

def test_parse_constraints_basic():
    """_parse_constraints extracts operator and version correctly."""
    cs = _parse_constraints(">= 0.9-8")
    assert len(cs) == 1
    assert cs[0].operator == ">="
    assert cs[0].version == "0.9-8"
    assert cs[0].normalized_version == "0.9.8"


def test_parse_constraints_multiple():
    """Multiple constraints in one parenthesised block are all captured."""
    cs = _parse_constraints(">= 1.0, < 2.0")
    assert len(cs) == 2
    operators = {c.operator for c in cs}
    assert operators == {">=", "<"}


def test_version_warnings_no_upstream_constraint():
    """No upstream constraint → no warning even if conda also has none."""
    upstream = PackageDep("abind")
    conda = PackageDep("r-abind")
    assert _version_warnings("abind", upstream, conda) == []


def test_version_warnings_upstream_constraint_conda_none():
    """Upstream has constraint but conda has none → warning."""
    upstream = PackageDep("rJava", [VersionConstraint(">=", "0.9-8")])
    conda = PackageDep("r-rjava")
    ws = _version_warnings("rJava", upstream, conda)
    assert len(ws) == 1
    assert "no version constraint" in ws[0]
    assert ">=" in ws[0]


def test_version_warnings_matching_constraints():
    """Equivalent constraints (after normalization) → no warning."""
    upstream = PackageDep("rJava", [VersionConstraint(">=", "0.9-8")])
    conda = PackageDep("r-rjava", [VersionConstraint(">=", "0.9_8")])
    assert _version_warnings("rJava", upstream, conda) == []


def test_version_warnings_differing_constraints():
    """Different constraints → warning showing both."""
    upstream = PackageDep("rJava", [VersionConstraint(">=", "0.9-8")])
    conda = PackageDep("r-rjava", [VersionConstraint(">=", "0.5")])
    ws = _version_warnings("rJava", upstream, conda)
    assert len(ws) == 1
    assert "0.9-8" in ws[0]
    assert "0.5" in ws[0]


def test_check_dependencies_version_constraint_missing_in_conda():
    """check_dependencies warns when upstream has constraint but conda does not."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
        desc.write("""Package: sample
Imports:
    rJava (>= 0.9-8)
""")
        desc.flush()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
            meta.write("""requirements:
  host:
    - r-rjava
  run:
    - r-rjava
""")
            meta.flush()

            errors, warnings = check_dependencies(desc.name, meta.name)

        assert len(errors) == 0
        assert len(warnings) == 1
        assert "no version constraint" in warnings[0]

        Path(desc.name).unlink()
        Path(meta.name).unlink()


def test_check_dependencies_matching_version_constraint():
    """check_dependencies produces no warning when constraints match (R vs conda notation)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
        desc.write("""Package: sample
Imports:
    rJava (>= 0.9-8)
""")
        desc.flush()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
            meta.write("""requirements:
  host:
    - r-rjava >=0.9_8
  run:
    - r-rjava >=0.9_8
""")
            meta.flush()

            errors, warnings = check_dependencies(desc.name, meta.name)

        assert len(errors) == 0
        assert len(warnings) == 0

        Path(desc.name).unlink()
        Path(meta.name).unlink()


def test_check_dependencies_differing_version_constraint():
    """check_dependencies warns when version constraints differ between sides."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
        desc.write("""Package: sample
Imports:
    rJava (>= 0.9-8)
""")
        desc.flush()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
            meta.write("""requirements:
  host:
    - r-rjava >=0.5
  run:
    - r-rjava >=0.5
""")
            meta.flush()

            errors, warnings = check_dependencies(desc.name, meta.name)

        assert len(errors) == 0
        assert len(warnings) == 1
        assert "0.9-8" in warnings[0]
        assert "0.5" in warnings[0]

        Path(desc.name).unlink()
        Path(meta.name).unlink()


def test_check_dependencies_host_only_warns():
    """Package present in host but absent from run should produce a warning."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
        desc.write("""Package: sample
Imports:
    abind
""")
        desc.flush()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
            meta.write("""requirements:
  host:
    - r-abind
  run: []
""")
            meta.flush()

            errors, warnings = check_dependencies(desc.name, meta.name)

        assert len(errors) == 0
        assert any("host" in w and "run" in w for w in warnings)

        Path(desc.name).unlink()
        Path(meta.name).unlink()


def test_check_dependencies_run_only_warns():
    """Package present in run but absent from host should produce a warning."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as desc:
        desc.write("""Package: sample
Imports:
    abind
""")
        desc.flush()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as meta:
            meta.write("""requirements:
  host: []
  run:
    - r-abind
""")
            meta.flush()

            errors, warnings = check_dependencies(desc.name, meta.name)

        assert len(errors) == 0
        assert any("run" in w and "host" in w for w in warnings)

        Path(desc.name).unlink()
        Path(meta.name).unlink()
