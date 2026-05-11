# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Unit tests for ``scripts/detect_coverage.py``.

These tests run the script's public functions against the same
``.fixtures/`` directories the end-to-end ``coverage-fixtures``
matrix exercises, plus a handful of synthetic configs covering
edge cases that would be expensive to encode as full action runs
(empty source lists, INI variants, malformed TOML, ``source_pkgs``,
nested ``omit`` patterns, etc.).

The unit-test job runs orders of magnitude faster than the action
matrix and therefore catches regressions in the detection logic
without having to spin up a full venv for each shape. The matrix
job remains responsible for proving the action wires the script's
decisions into pytest-cov correctly.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# ``detect_coverage`` lives under ``scripts/`` rather than as a
# regular package import. Prepend the directory so the module can
# be imported directly; this matches how the surrounding action
# step invokes it (``python detect_coverage.py``).
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import detect_coverage  # noqa: E402

FIXTURES = REPO_ROOT / ".fixtures"


# --------------------------------------------------------------------------
# has_cov_in_addopts
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        # Fixture matrix: each cell exercises one decision branch.
        ("src-layout-path-source", True),  # addopts has --cov=mypkg
        ("src-layout-pkg-source", False),  # addopts has -v only
        ("addopts-cov-only", True),  # addopts has --cov=mypkg
        ("flat-layout-no-config", False),  # no addopts
        ("omit-excludes-install", True),  # addopts has --cov=mypkg
    ],
)
def test_has_cov_in_addopts_against_fixtures(
    fixture_name: str, *, expected: bool
) -> None:
    """Each fixture has a known addopts shape; the detector must match."""
    assert detect_coverage.has_cov_in_addopts(FIXTURES / fixture_name) is expected


def test_has_cov_in_addopts_distinguishes_cov_report(tmp_path: Path) -> None:
    """``--cov-report`` shares the ``--cov`` prefix but is not a target."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [tool.pytest.ini_options]
            addopts = ["-v", "--cov-report=xml", "--cov-fail-under=80"]
            """
        )
    )
    assert detect_coverage.has_cov_in_addopts(tmp_path) is False


def test_has_cov_in_addopts_finds_bare_cov_in_string_form(tmp_path: Path) -> None:
    """``addopts`` accepts a single string too; both shapes must be searched."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [tool.pytest.ini_options]
            addopts = "-ra -q --cov --cov-report=term"
            """
        )
    )
    assert detect_coverage.has_cov_in_addopts(tmp_path) is True


def test_has_cov_in_addopts_reads_setup_cfg(tmp_path: Path) -> None:
    """Legacy projects that put pytest config in ``setup.cfg`` must work."""
    (tmp_path / "setup.cfg").write_text(
        textwrap.dedent(
            """\
            [tool:pytest]
            addopts = --cov=mypkg --cov-report=term
            """
        )
    )
    assert detect_coverage.has_cov_in_addopts(tmp_path) is True


def test_has_cov_in_addopts_ignores_comments(tmp_path: Path) -> None:
    """A '--cov' mention inside an INI comment must not produce a match."""
    (tmp_path / "tox.ini").write_text(
        textwrap.dedent(
            """\
            [pytest]
            # we used to pass --cov=mypkg here but removed it
            addopts = -v -ra
            """
        )
    )
    assert detect_coverage.has_cov_in_addopts(tmp_path) is False


# --------------------------------------------------------------------------
# has_nonempty_coverage_source
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        ("src-layout-path-source", True),  # source = ["src"]
        ("src-layout-pkg-source", True),  # source = ["mypkg"]
        ("addopts-cov-only", False),  # no [tool.coverage.run]
        ("flat-layout-no-config", False),  # no coverage config at all
        ("omit-excludes-install", True),  # source = ["mypkg"]
    ],
)
def test_has_nonempty_coverage_source_against_fixtures(
    fixture_name: str, *, expected: bool
) -> None:
    """Source detection on each fixture matches the README matrix."""
    config = FIXTURES / fixture_name / "pyproject.toml"
    assert detect_coverage.has_nonempty_coverage_source(config) is expected


def test_has_nonempty_coverage_source_recognises_source_pkgs(
    tmp_path: Path,
) -> None:
    """``source_pkgs`` is coverage.py's import-name-only spelling of source."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [tool.coverage.run]
            source_pkgs = ["mypkg"]
            """
        )
    )
    assert (
        detect_coverage.has_nonempty_coverage_source(tmp_path / "pyproject.toml")
        is True
    )


def test_has_nonempty_coverage_source_treats_empty_list_as_unset(
    tmp_path: Path,
) -> None:
    """An empty list must not count as configuration."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [tool.coverage.run]
            source = []
            """
        )
    )
    assert (
        detect_coverage.has_nonempty_coverage_source(tmp_path / "pyproject.toml")
        is False
    )


def test_has_nonempty_coverage_source_handles_missing_config() -> None:
    """A ``None`` config path must short-circuit to False without raising."""
    assert detect_coverage.has_nonempty_coverage_source(None) is False


def test_has_nonempty_coverage_source_reads_coveragerc(tmp_path: Path) -> None:
    """``.coveragerc`` uses the bare ``[run]`` section name, not ``[coverage:run]``."""
    coveragerc = tmp_path / ".coveragerc"
    coveragerc.write_text(
        textwrap.dedent(
            """\
            [run]
            source = mypkg
            """
        )
    )
    assert detect_coverage.has_nonempty_coverage_source(coveragerc) is True


# --------------------------------------------------------------------------
# problematic_omit_patterns
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_name", "expected_patterns"),
    [
        # The fixtures we ship: only one has a problematic omit.
        ("src-layout-path-source", []),
        ("src-layout-pkg-source", []),
        ("addopts-cov-only", []),
        ("flat-layout-no-config", []),
        ("omit-excludes-install", ["*/.venv/*"]),
    ],
)
def test_problematic_omit_patterns_against_fixtures(
    fixture_name: str, *, expected_patterns: list[str]
) -> None:
    """The omit-excludes-install fixture is the only one that should trip detection."""
    config = FIXTURES / fixture_name / "pyproject.toml"
    assert detect_coverage.problematic_omit_patterns(config) == expected_patterns


@pytest.mark.parametrize(
    "pattern",
    [
        "*/venv/*",
        "*/.venv/*",
        "*/.venv*",
        "*venv*",
        "*/site-packages/*",
        "*site-packages*",
        "*/Site-Packages/*",  # case-insensitive
        ".VENV/lib/*",  # case-insensitive
    ],
)
def test_problematic_omit_patterns_flags_install_location_globs(
    tmp_path: Path, pattern: str
) -> None:
    """Each shape of install-location-matching glob must be flagged."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""\
            [tool.coverage.run]
            omit = [{pattern!r}]
            """
        )
    )
    result = detect_coverage.problematic_omit_patterns(tmp_path / "pyproject.toml")
    assert result == [pattern]


@pytest.mark.parametrize(
    "pattern",
    [
        "*/tests/*",
        "*/test_*",
        "*/__pycache__/*",
        "src/mypkg/_version.py",
        "*/.tox/*",
    ],
)
def test_problematic_omit_patterns_ignores_benign_globs(
    tmp_path: Path, pattern: str
) -> None:
    """Benign omit globs must not trigger a false positive."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""\
            [tool.coverage.run]
            omit = [{pattern!r}]
            """
        )
    )
    assert detect_coverage.problematic_omit_patterns(tmp_path / "pyproject.toml") == []


def test_problematic_omit_patterns_preserves_declaration_order(
    tmp_path: Path,
) -> None:
    """The action emits offending patterns by name; order matters for readability."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [tool.coverage.run]
            omit = [
                "*/tests/*",
                "*/.venv/*",
                "*/__pycache__/*",
                "*/site-packages/*",
                "*/venv/*",
            ]
            """
        )
    )
    assert detect_coverage.problematic_omit_patterns(tmp_path / "pyproject.toml") == [
        "*/.venv/*",
        "*/site-packages/*",
        "*/venv/*",
    ]


def test_problematic_omit_patterns_reads_ini_form(tmp_path: Path) -> None:
    """``.coveragerc`` / ``setup.cfg`` style omit lists must also be parsed."""
    coveragerc = tmp_path / ".coveragerc"
    coveragerc.write_text(
        textwrap.dedent(
            """\
            [run]
            omit =
                */tests/*
                */.venv/*
                src/mypkg/_version.py
            """
        )
    )
    assert detect_coverage.problematic_omit_patterns(coveragerc) == ["*/.venv/*"]


def test_problematic_omit_patterns_splits_ini_single_line_list(
    tmp_path: Path,
) -> None:
    """INI sections occasionally hold a TOML-style single-line list.

    A consumer copy-pasting from ``pyproject.toml`` into
    ``setup.cfg`` can end up with
    ``omit = ["*/.venv/*", "*/tests/*"]`` on one line.
    configparser hands that to us verbatim; we must still split
    on both newlines and commas so each pattern is named
    individually in the warning the action emits.
    """
    setup_cfg = tmp_path / "setup.cfg"
    setup_cfg.write_text(
        textwrap.dedent(
            """\
            [coverage:run]
            omit = ["*/.venv/*", "*/tests/*", "*/site-packages/*"]
            """
        )
    )
    assert detect_coverage.problematic_omit_patterns(setup_cfg) == [
        "*/.venv/*",
        "*/site-packages/*",
    ]


def test_problematic_omit_patterns_handles_missing_config() -> None:
    """A ``None`` config path must short-circuit to ``[]`` without raising."""
    assert detect_coverage.problematic_omit_patterns(None) == []


# --------------------------------------------------------------------------
# project_import_name
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        # All four shipping fixtures use [project].name = "mypkg".
        ("src-layout-path-source", "mypkg"),
        ("src-layout-pkg-source", "mypkg"),
        ("addopts-cov-only", "mypkg"),
        ("flat-layout-no-config", "mypkg"),
        ("omit-excludes-install", "mypkg"),
    ],
)
def test_project_import_name_against_fixtures(
    fixture_name: str, *, expected: str
) -> None:
    assert detect_coverage.project_import_name(FIXTURES / fixture_name) == expected


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("markdown-table-fixer", "markdown_table_fixer"),
        ("Markdown.Table.Fixer", "markdown_table_fixer"),
        ("My_Package", "my_package"),
        ("dependa--merge", "dependa_merge"),
        ("  spaced  ", "spaced"),
    ],
)
def test_project_import_name_normalisation(
    tmp_path: Path, declared: str, expected: str
) -> None:
    """PEP 503-style normalisation: lowercase, collapse '-/_/.' to '_'."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""\
            [project]
            name = "{declared}"
            version = "0.0.0"
            """
        )
    )
    assert detect_coverage.project_import_name(tmp_path) == expected


def test_project_import_name_returns_empty_when_missing(tmp_path: Path) -> None:
    """A missing or nameless pyproject.toml must yield '' rather than raising."""
    assert detect_coverage.project_import_name(tmp_path) == ""
    (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires = []\n")
    assert detect_coverage.project_import_name(tmp_path) == ""


# --------------------------------------------------------------------------
# main() end-to-end output
# --------------------------------------------------------------------------


@pytest.fixture
def collect_output(
    capsys: pytest.CaptureFixture[str],
) -> Iterator["Callable[[Path, Path | None], dict[str, str]]"]:
    """Run ``main()`` against a fixture and return the parsed KEY=VALUE output.

    Reading stdout directly via ``capsys`` keeps the test coupled to
    the surface the action.yaml step actually consumes (``while
    read`` over the script's stdout); a mock around ``print`` would
    drift if that contract changed.
    """

    def _run(project: Path, config: Path | None) -> dict[str, str]:
        argv = ["detect_coverage.py", str(project)]
        if config is not None:
            argv.append(str(config))
        rc = detect_coverage.main(argv)
        assert rc == 0, f"main() returned non-zero exit code: {rc}"
        captured = capsys.readouterr()
        result: dict[str, str] = {}
        for line in captured.out.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                result[key] = value
        return result

    yield _run


def test_main_emits_all_keys_for_pkg_source_fixture(
    collect_output: "Callable[[Path, Path | None], dict[str, str]]",
) -> None:
    """The shipping fixture set is the source of truth for the env-var contract."""
    project = FIXTURES / "src-layout-pkg-source"
    config = project / "pyproject.toml"
    out = collect_output(project, config)
    assert out == {
        "coverage_source_configured": "true",
        "cov_in_addopts": "false",
        "coverage_inject_cov": "true",
        "coverage_target_configured": "true",
        "coverage_fallback_pkg": "",
        "coverage_omit_excludes_install": "false",
        "coverage_problematic_omit_patterns": "",
    }


def test_main_flags_omit_excludes_install_fixture(
    collect_output: "Callable[[Path, Path | None], dict[str, str]]",
) -> None:
    """The new fixture must set both the boolean and the patterns blob."""
    project = FIXTURES / "omit-excludes-install"
    config = project / "pyproject.toml"
    out = collect_output(project, config)
    assert out["coverage_omit_excludes_install"] == "true"
    assert out["coverage_problematic_omit_patterns"] == "*/.venv/*"
    # The addopts '--cov=mypkg' suppresses injection but keeps the
    # other signals at their defaults so we can be sure the omit
    # check is the only thing that fired.
    assert out["cov_in_addopts"] == "true"
    assert out["coverage_inject_cov"] == "false"


def test_main_emits_false_for_omit_signal_in_clean_fixture(
    collect_output: "Callable[[Path, Path | None], dict[str, str]]",
) -> None:
    """A clean fixture must report ``false`` and an empty patterns string."""
    project = FIXTURES / "src-layout-pkg-source"
    config = project / "pyproject.toml"
    out = collect_output(project, config)
    assert out["coverage_omit_excludes_install"] == "false"
    assert out["coverage_problematic_omit_patterns"] == ""


def test_main_usage_error_returns_64(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing args trigger BSD ``EX_USAGE`` (64) so callers can diagnose."""
    assert detect_coverage.main(["detect_coverage.py"]) == 64
    err = capsys.readouterr().err
    assert "Usage" in err
