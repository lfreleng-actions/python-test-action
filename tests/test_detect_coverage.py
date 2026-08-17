# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Unit tests for the action's coverage-detection scripts.

Covers ``scripts/coverage_config.py`` (config-file introspection)
and ``scripts/detect_coverage.py`` (decision logic and the
``$GITHUB_ENV`` output contract).

These tests run the scripts' public functions against the same
``.fixtures/`` directories the end-to-end ``coverage-fixtures``
matrix exercises, plus a handful of synthetic configs covering
edge cases that would be expensive to encode as full action runs
(empty source lists, INI variants, malformed TOML, ``source_pkgs``,
nested ``omit`` patterns, etc.).

The unit-test job runs orders of magnitude faster than the action
matrix and therefore catches regressions in the detection logic
without having to spin up a full venv for each shape. The matrix
job remains responsible for proving the action wires the scripts'
decisions into pytest-cov correctly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# ``detect_coverage`` and ``coverage_config`` live under ``scripts/``
# rather than as a regular package import. Prepend the directory so
# the modules can be imported directly; this matches how the
# surrounding action step invokes the script (``python
# detect_coverage.py``), which puts ``scripts/`` first on sys.path.
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import coverage_config
import detect_coverage

FIXTURES = REPO_ROOT / ".fixtures"


# --------------------------------------------------------------------------
# _toml_table
# --------------------------------------------------------------------------


def test_toml_table_returns_nested_table() -> None:
    """A fully populated path yields the innermost table."""
    data = {"tool": {"coverage": {"run": {"source": ["mypkg"]}}}}
    assert coverage_config._toml_table(data, "tool", "coverage", "run") == {
        "source": ["mypkg"]
    }


@pytest.mark.parametrize(
    "data",
    [
        # Missing at the first key.
        {},
        # Missing at an intermediate key.
        {"tool": {}},
        # Scalar where an intermediate table is expected.
        {"tool": {"coverage": "on"}},
        # Scalar at the leaf itself.
        {"tool": {"coverage": {"run": "yes"}}},
    ],
)
def test_toml_table_returns_empty_for_absent_or_scalar(data: dict[str, object]) -> None:
    """Malformed config reads as 'not configured' rather than raising."""
    assert coverage_config._toml_table(data, "tool", "coverage", "run") == {}


def test_detectors_survive_scalar_where_table_expected(tmp_path: Path) -> None:
    """A consumer's scalar-for-table config must not crash the action."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent("""
            [tool]
            pytest = "enabled"
            coverage = "enabled"

            [project]
            name = "mypkg"
            """).lstrip(),
        encoding="utf-8",
    )
    assert coverage_config.has_cov_in_addopts(tmp_path) is False
    assert coverage_config.has_nonempty_coverage_source(pyproject) is False
    assert coverage_config.problematic_omit_patterns(pyproject) == []
    assert coverage_config.project_import_name(tmp_path) == "mypkg"


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
    assert coverage_config.has_cov_in_addopts(FIXTURES / fixture_name) is expected


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
    assert coverage_config.has_cov_in_addopts(tmp_path) is False


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
    assert coverage_config.has_cov_in_addopts(tmp_path) is True


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
    assert coverage_config.has_cov_in_addopts(tmp_path) is True


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
    assert coverage_config.has_cov_in_addopts(tmp_path) is False


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
    assert coverage_config.has_nonempty_coverage_source(config) is expected


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
        coverage_config.has_nonempty_coverage_source(tmp_path / "pyproject.toml")
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
        coverage_config.has_nonempty_coverage_source(tmp_path / "pyproject.toml")
        is False
    )


def test_has_nonempty_coverage_source_handles_missing_config() -> None:
    """A ``None`` config path must short-circuit to False without raising."""
    assert coverage_config.has_nonempty_coverage_source(None) is False


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
    assert coverage_config.has_nonempty_coverage_source(coveragerc) is True


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
    assert coverage_config.problematic_omit_patterns(config) == expected_patterns


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
    result = coverage_config.problematic_omit_patterns(tmp_path / "pyproject.toml")
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
    assert coverage_config.problematic_omit_patterns(tmp_path / "pyproject.toml") == []


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
    assert coverage_config.problematic_omit_patterns(tmp_path / "pyproject.toml") == [
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
    assert coverage_config.problematic_omit_patterns(coveragerc) == ["*/.venv/*"]


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
    assert coverage_config.problematic_omit_patterns(setup_cfg) == [
        "*/.venv/*",
        "*/site-packages/*",
    ]


def test_problematic_omit_patterns_handles_missing_config() -> None:
    """A ``None`` config path must short-circuit to ``[]`` without raising."""
    assert coverage_config.problematic_omit_patterns(None) == []


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
    assert coverage_config.project_import_name(FIXTURES / fixture_name) == expected


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
    assert coverage_config.project_import_name(tmp_path) == expected


def test_project_import_name_returns_empty_when_missing(tmp_path: Path) -> None:
    """A missing or nameless pyproject.toml must yield '' rather than raising."""
    assert coverage_config.project_import_name(tmp_path) == ""
    (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires = []\n")
    assert coverage_config.project_import_name(tmp_path) == ""


# --------------------------------------------------------------------------
# main() end-to-end output
# --------------------------------------------------------------------------


@pytest.fixture
def collect_output(
    capsys: pytest.CaptureFixture[str],
) -> Iterator[Callable[[Path, Path | None], dict[str, str]]]:
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
    collect_output: Callable[[Path, Path | None], dict[str, str]],
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
    collect_output: Callable[[Path, Path | None], dict[str, str]],
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
    collect_output: Callable[[Path, Path | None], dict[str, str]],
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


# --------------------------------------------------------------------------
# sibling-import resolution
# --------------------------------------------------------------------------


def _run_script(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Invoke the script the way action.yaml does, under a given environment."""
    project = FIXTURES / "src-layout-pkg-source"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "detect_coverage.py"),
            str(project),
            str(project / "pyproject.toml"),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_script_runs_under_safe_path_mode() -> None:
    """``PYTHONSAFEPATH=1`` drops the implicit script-directory sys.path entry.

    The consumer's workflow environment reaches this action's steps, so
    safe-path mode is theirs to set. The script prepends its own
    directory explicitly for exactly this reason; without that, the
    ``coverage_config`` import would raise ``ModuleNotFoundError``.
    """
    result = _run_script({**os.environ, "PYTHONSAFEPATH": "1"})
    assert result.returncode == 0, result.stderr
    assert "coverage_source_configured=true" in result.stdout


def test_script_prefers_sibling_over_pythonpath(tmp_path: Path) -> None:
    """A ``PYTHONPATH`` entry must not shadow the shipped helper module.

    Combined with safe-path mode, since that is when the implicit
    script-directory entry disappears and a ``PYTHONPATH`` module of
    the same name would otherwise win the import.
    """
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "coverage_config.py").write_text(
        'raise RuntimeError("decoy coverage_config was imported")\n',
        encoding="utf-8",
    )
    result = _run_script(
        {**os.environ, "PYTHONSAFEPATH": "1", "PYTHONPATH": str(decoy)}
    )
    assert result.returncode == 0, result.stderr
    assert "decoy" not in result.stderr
    assert "coverage_source_configured=true" in result.stdout


# --------------------------------------------------------------------------
# $GITHUB_ENV injection
# --------------------------------------------------------------------------

# The complete set of keys main() may emit. action.yaml appends this
# script's stdout to $GITHUB_ENV, so any line outside this set is a
# forged assignment.
EXPECTED_KEYS = frozenset(
    {
        "coverage_source_configured",
        "cov_in_addopts",
        "coverage_inject_cov",
        "coverage_target_configured",
        "coverage_fallback_pkg",
        "coverage_omit_excludes_install",
        "coverage_problematic_omit_patterns",
    }
)


def _parse_env_output(stdout: str) -> dict[str, str]:
    """Parse main()'s stdout the way the action's shell loop does."""
    parsed: dict[str, str] = {}
    for line in stdout.splitlines():
        if line:
            key, _, value = line.partition("=")
            parsed[key] = value
    return parsed


def test_multiline_omit_pattern_cannot_forge_env_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """TOML strings can span lines; a value must not start a new assignment.

    Without neutralisation, action.yaml would append the embedded
    newline to $GITHUB_ENV verbatim, letting a checked-in pyproject.toml
    set BASH_ENV (or any other variable) for every later step in the
    job. That is reachable input whenever this action runs against an
    unreviewed contribution.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent('''\
            [project]
            name = "mypkg"

            [tool.coverage.run]
            omit = ["""*/.venv/*
            BASH_ENV=/tmp/payload"""]
            '''),
        encoding="utf-8",
    )
    assert (
        detect_coverage.main(["detect_coverage.py", str(tmp_path), str(pyproject)]) == 0
    )
    emitted = _parse_env_output(capsys.readouterr().out)
    assert set(emitted) == EXPECTED_KEYS
    # The pattern is still reported to the user, newline neutralised.
    patterns = emitted["coverage_problematic_omit_patterns"]
    assert "\n" not in patterns
    assert "BASH_ENV=/tmp/payload" in patterns


def test_multiline_project_name_cannot_forge_env_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``[project].name`` is the other consumer-derived value emitted."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent('''\
            [project]
            name = """mypkg
            BASH_ENV=/tmp/payload"""
            '''),
        encoding="utf-8",
    )
    assert detect_coverage.main(["detect_coverage.py", str(tmp_path)]) == 0
    emitted = _parse_env_output(capsys.readouterr().out)
    assert set(emitted) == EXPECTED_KEYS
    assert "\n" not in emitted["coverage_fallback_pkg"]


# --------------------------------------------------------------------------
# scripts/validate_env_lines.sh
# --------------------------------------------------------------------------

VALIDATOR = SCRIPTS_DIR / "validate_env_lines.sh"


def _run_validator(stdin_text: str) -> subprocess.CompletedProcess[str]:
    """Feed candidate env lines to the shell validator, as action.yaml does."""
    return subprocess.run(
        ["bash", str(VALIDATOR)],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
    )


def test_validator_accepts_real_script_output() -> None:
    """The two layers must agree: real output has to survive the allow-list.

    Pairing the real script with the real validator means a new key
    added to ``main()`` without a matching entry in the shell allow-list
    fails here rather than in a consumer's job.
    """
    project = FIXTURES / "omit-excludes-install"
    produced = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "detect_coverage.py"),
            str(project),
            str(project / "pyproject.toml"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert set(_parse_env_output(produced.stdout)) == EXPECTED_KEYS
    result = _run_validator(produced.stdout)
    assert result.returncode == 0, result.stderr


def test_validator_rejects_forged_assignment() -> None:
    """A smuggled newline must not reach $GITHUB_ENV even if layer one fails."""
    result = _run_validator(
        "coverage_problematic_omit_patterns=*/.venv/*\nBASH_ENV=/tmp/payload\n"
    )
    assert result.returncode == 1
    assert "BASH_ENV=/tmp/payload" in result.stderr


@pytest.mark.parametrize(
    "line",
    [
        # Unknown key.
        "PATH=/tmp/payload",
        # Known key as a suffix rather than the whole key.
        "evil_coverage_fallback_pkg=x",
        # Known key without the '=' separator.
        "coverage_fallback_pkg",
    ],
)
def test_validator_rejects_malformed_lines(line: str) -> None:
    """Only exact 'known_key=' prefixes are accepted."""
    assert _run_validator(line + "\n").returncode == 1


def test_validator_ignores_blank_lines() -> None:
    """Trailing newlines in the captured output are not an error."""
    assert _run_validator("\ncov_in_addopts=true\n\n").returncode == 0


@pytest.mark.parametrize(
    ("label", "line"),
    [
        # CR is the dangerous one: 'read' splits on LF only, so this
        # arrives as a single shell line, yet the runner treats a lone
        # CR as a record terminator in the environment file.
        ("carriage return", "coverage_fallback_pkg=x\rBASH_ENV=/tmp/payload"),
        ("vertical tab", "coverage_fallback_pkg=x\vBASH_ENV=/tmp/payload"),
        ("form feed", "coverage_fallback_pkg=x\fBASH_ENV=/tmp/payload"),
        ("escape", "coverage_fallback_pkg=x\x1bBASH_ENV=/tmp/payload"),
        ("delete", "coverage_fallback_pkg=x\x7fBASH_ENV=/tmp/payload"),
    ],
)
def test_validator_rejects_control_characters(label: str, line: str) -> None:
    """Layer two must block control characters without relying on layer one.

    The key-prefix check alone accepts these: the forged assignment is
    hidden mid-line rather than after a newline.
    """
    result = _run_validator(line + "\n")
    assert result.returncode == 1, f"{label} was accepted"
    assert "control character" in result.stderr


def test_validator_accepts_neutralised_payload() -> None:
    """The two layers compose: layer one's output survives layer two.

    ``_env_safe`` turns the newline in a hostile omit pattern into a
    space, which leaves a single well-formed assignment whose value
    merely *contains* the text ``BASH_ENV=...``. That is inert, and
    must not be rejected.
    """
    line = "coverage_problematic_omit_patterns=*/.venv/* BASH_ENV=/tmp/payload\n"
    assert _run_validator(line).returncode == 0


def test_validator_accepts_non_ascii_value() -> None:
    """Byte-wise control-character matching must not reject valid UTF-8."""
    assert _run_validator("coverage_fallback_pkg=pakét\n").returncode == 0
