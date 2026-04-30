# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
"""Detect existing coverage configuration in a project tree.

This script consolidates the coverage-detection logic the action
needs to decide:

  - whether the consumer's pytest config already supplies '--cov',
    in which case the action MUST NOT inject its own (issue #138);
  - whether the discovered coverage config sets a non-empty
    [tool.coverage.run].source / [coverage:run] source / [run] source,
    which suppresses the missing-target warning;
  - whether to derive a fallback package name from [project].name
    when neither signal is present (so the action can pass
    '--cov=<pkg>' rather than a bare '--cov' that would collect zero
    data for non-editable installs).

It writes KEY=VALUE pairs to stdout for the surrounding shell step
to forward to ``$GITHUB_ENV``. Stdlib only (tomllib + configparser
+ re), with a 'tomli' fallback for Python <3.11 (the surrounding
action.yaml step ``uv pip install``s tomli into the venv when the
requested ``inputs.python_version`` is older than 3.11). The
script runs under the action's setup-uv-managed venv Python -
the same interpreter that runs pytest in the next step - so
detection and test runtime see the same import resolution.

Usage::

    python detect_coverage.py <project_prefix> <coverage_config_path>

The second argument may be empty if no coverage config was
discovered; the script handles that gracefully.

Exit codes:
    0   detection completed; KEY=VALUE pairs printed to stdout.
    2   no TOML parser available (need tomllib >= 3.11 or tomli).
    64  usage error (wrong number of arguments).
"""

from __future__ import annotations

import configparser
import re
import sys
from pathlib import Path

# Prefer the stdlib tomllib (Python 3.11+); fall back to the third-
# party 'tomli' package (the same code that became tomllib) so the
# script works under the action's setup-uv-managed venv when the
# consumer requested a pre-3.11 inputs.python_version. The
# surrounding action.yaml step 'Install project and test/dev
# dependencies [pytest]' detects this case and 'uv pip install's
# tomli into the venv before this script runs.
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        print(
            "Error: detect_coverage.py needs a TOML parser; install "
            "Python 3.11+ (for stdlib tomllib) or 'tomli' ❌",
            file=sys.stderr,
        )
        sys.exit(2)

# --------------------------------------------------------------------------
# --cov token detection
# --------------------------------------------------------------------------

# Match '--cov' as a bare flag or as '--cov=<value>'. The leading
# non-word boundary avoids matching '--cov-report' / '--cov-config' /
# '--cov-fail-under' which all begin with '--cov' but do not
# configure a collection target. The trailing terminator covers all
# the ways '--cov' can end inside a config value:
#
#   '='   for '--cov=mypkg'
#   ' '   for '-v --cov ...' (whitespace separator)
#   '"'   for "'--cov'" / '"--cov"' inside quoted strings
#   "'"   for the same with single quotes
#   $     for '--cov' at end-of-line / end-of-string
_COV_RE = re.compile(r"""(?:^|[^A-Za-z0-9_-])--cov(?:=|\s|['"]|$)""")


def _addopts_text(addopts: object) -> str:
    """Coerce a TOML addopts value to a single string for searching.

    pytest accepts both list-of-strings and string forms; treat both
    uniformly. Any non-string elements are skipped.
    """
    if isinstance(addopts, list):
        return " ".join(item for item in addopts if isinstance(item, str))
    if isinstance(addopts, str):
        return addopts
    return ""


def has_cov_in_addopts(project: Path) -> bool:
    """Return True iff some pytest config supplies '--cov' in addopts.

    Checked locations, in order (first hit wins):

        - pyproject.toml [tool.pytest.ini_options].addopts
        - pytest.ini      [pytest].addopts
        - setup.cfg       [tool:pytest].addopts
        - tox.ini         [pytest].addopts

    Comments in TOML are stripped natively by the parser; INI
    comments (both '#' and ';') are stripped by configparser. So a
    '--cov' mention in a comment never produces a false match.
    """
    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        try:
            with pyproject.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError:
            data = {}
        addopts = (
            data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts")
        )
        if addopts and _COV_RE.search(_addopts_text(addopts)):
            return True

    ini_targets = (
        ("pytest.ini", "pytest"),
        ("setup.cfg", "tool:pytest"),
        ("tox.ini", "pytest"),
    )
    for filename, section in ini_targets:
        path = project / filename
        if not path.is_file():
            continue
        cfg = configparser.ConfigParser(
            interpolation=None,
            inline_comment_prefixes=("#", ";"),
        )
        try:
            cfg.read(path)
        except configparser.Error:
            continue
        if cfg.has_option(section, "addopts"):
            value = cfg.get(section, "addopts")
            if _COV_RE.search(value):
                return True

    return False


# --------------------------------------------------------------------------
# coverage source list detection
# --------------------------------------------------------------------------


def has_nonempty_coverage_source(config_path: Path | None) -> bool:
    """Return True iff the discovered coverage config sets source.

    Recognises:

        - TOML:   [tool.coverage.run].source        (pyproject.toml)
        - INI:    [coverage:run] source             (setup.cfg, tox.ini)
        - INI:    [run] source                      (.coveragerc)

    A source list of '[]' / empty string counts as 'not configured':
    coverage.py treats those identically to no source set, and the
    action's missing-target warning should fire in that case so the
    consumer notices.

    coverage.py also accepts ``source_pkgs`` (a list of importable
    package names rather than paths). Treat that as a configured
    source too: if the consumer specified either, they have signalled
    coverage configuration.
    """
    if config_path is None or not config_path.is_file():
        return False

    suffix = config_path.suffix.lower()
    name = config_path.name.lower()

    if suffix == ".toml":
        try:
            with config_path.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError:
            return False
        run = data.get("tool", {}).get("coverage", {}).get("run", {})
        for key in ("source", "source_pkgs"):
            value = run.get(key)
            if isinstance(value, list) and any(
                isinstance(item, str) and item.strip() for item in value
            ):
                return True
            if isinstance(value, str) and value.strip():
                return True
        return False

    # INI form: setup.cfg / tox.ini / .coveragerc.
    cfg = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=("#", ";"),
    )
    try:
        cfg.read(config_path)
    except configparser.Error:
        return False
    candidate_sections = (
        # coverage.py-style (setup.cfg, tox.ini)
        "coverage:run",
        # .coveragerc-style
        "run",
    )
    # .coveragerc is by convention coverage's own file; if it has a
    # bare [run] section that is the coverage one. setup.cfg / tox.ini
    # use the namespaced [coverage:run] form.
    if name == ".coveragerc":
        candidate_sections = ("run", "coverage:run")
    for section in candidate_sections:
        for key in ("source", "source_pkgs"):
            if cfg.has_option(section, key):
                # configparser collapses INI multi-line values into
                # one string with newlines; any non-whitespace
                # content counts as a non-empty value. Strip
                # brackets, commas, and quotes too so empty-list /
                # empty-string spellings - 'source = []',
                # 'source = ""', 'source = \'\'' - count as 'not
                # configured', matching the TOML branch above and
                # coverage.py's own treatment of an empty source
                # list.
                raw = cfg.get(section, key)
                stripped = raw.translate(str.maketrans("", "", "[],\"'")).strip()
                if stripped:
                    return True
    return False


# --------------------------------------------------------------------------
# [project].name -> import-name normalisation
# --------------------------------------------------------------------------


def project_import_name(project: Path) -> str:
    """Derive the conventional import name from [project].name.

    Returns '' if pyproject.toml is missing, malformed, or does not
    define [project].name.

    Normalisation: lowercase the distribution name, then collapse
    runs of '-' / '_' / '.' to a single '_'. This is a heuristic
    (some distributions use a different import name from their
    distribution name, e.g. 'Pillow' -> 'PIL') but it covers the
    common case and keeps the fallback wholly local to the action
    without needing post-install introspection.
    """
    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        return ""
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError:
        return ""
    name = data.get("project", {}).get("name")
    if not isinstance(name, str) or not name.strip():
        return ""
    return re.sub(r"[-_.]+", "_", name.strip().lower())


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(
            "Usage: detect_coverage.py <project_prefix> [coverage_config_path]",
            file=sys.stderr,
        )
        # 64 is BSD sysexits.h's EX_USAGE - distinct from exit code
        # 2 (the missing-TOML-parser path above) so callers can
        # distinguish 'caller bug' from 'environment missing
        # required dependency'.
        return 64

    project = Path(argv[1])
    cov_config_arg = argv[2] if len(argv) == 3 else ""
    cov_config = Path(cov_config_arg) if cov_config_arg else None

    cov_in_addopts = has_cov_in_addopts(project)
    source_configured = has_nonempty_coverage_source(cov_config)

    # Inject decision: ONLY suppressed by an existing --cov in
    # addopts. The source list does NOT suppress injection because
    # pytest-cov needs --cov on the CLI or in addopts to activate.
    inject_cov = not cov_in_addopts

    # Warn decision: either signal is sufficient evidence the
    # consumer has thought about coverage. Absence of BOTH triggers
    # the missing-target warning.
    target_configured = cov_in_addopts or source_configured

    # Fallback package name only used when injection happens AND
    # source is not configured (otherwise the source list scopes
    # collection and a bare --cov is sufficient).
    fallback_pkg = ""
    if inject_cov and not source_configured:
        fallback_pkg = project_import_name(project)

    out = (
        ("coverage_source_configured", "true" if source_configured else "false"),
        ("cov_in_addopts", "true" if cov_in_addopts else "false"),
        ("coverage_inject_cov", "true" if inject_cov else "false"),
        (
            "coverage_target_configured",
            "true" if target_configured else "false",
        ),
        ("coverage_fallback_pkg", fallback_pkg),
    )
    for key, value in out:
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
