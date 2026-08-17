# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Read coverage-relevant settings out of a project's config files.

Pure introspection helpers shared by ``detect_coverage.py``: each
function answers one question about a consumer's checkout and none
of them decide policy or write output. The decision logic and the
``$GITHUB_ENV`` contract live in ``detect_coverage.py``.

Stdlib only (tomllib + configparser + re), with a 'tomli' fallback
for Python <3.11 (the surrounding action.yaml step ``uv pip
install``s tomli into the venv when the requested
``inputs.python_version`` is older than 3.11).

Importing this module exits the process with status 2 when no TOML
parser is available, since every caller needs one.
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
# TOML table lookup
# --------------------------------------------------------------------------


def _toml_table(data: object, *path: str) -> dict[str, object]:
    """Return the nested TOML table at *path*, or an empty table.

    Walks one key at a time and verifies that each intermediate value
    really is a table before descending. A consumer whose config puts
    a scalar where a table belongs - ``[tool] coverage = "on"``, say -
    therefore reads as 'not configured' rather than raising
    ``AttributeError`` and failing the whole action run.
    """
    current = data
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    if not isinstance(current, dict):
        return {}
    return current


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
        addopts = _toml_table(data, "tool", "pytest", "ini_options").get("addopts")
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


# Patterns that, when present in [tool.coverage.run].omit, cause
# coverage.py to refuse to instrument the project's own code under
# the action's default (non-editable) install layout. The package
# physically lives at '.venv/lib/pythonX.Y/site-packages/<pkg>/...'
# under setup-uv's workspace venv, and any omit glob matching that
# path silently masks all collection. We match by case-insensitive
# substring rather than parsing the glob: any literal mention of
# 'venv' or 'site-packages' inside a pattern is sufficient evidence
# the consumer's omit list and the action's install layout are at
# odds. The substring approach also catches non-leading variants
# ('.venv*', '*venv', '*/site-packages*') that a stricter
# component-level match would miss.
_OMIT_INSTALL_LOCATION_SUBSTRINGS: tuple[str, ...] = (
    "venv",
    "site-packages",
)


def _omit_pattern_excludes_install(pattern: str) -> bool:
    """Return True iff a single omit glob would mask a non-editable install."""
    lowered = pattern.lower()
    return any(sub in lowered for sub in _OMIT_INSTALL_LOCATION_SUBSTRINGS)


def problematic_omit_patterns(config_path: Path | None) -> list[str]:
    """Return omit globs that exclude the non-editable install location.

    Recognises:

        - TOML:   [tool.coverage.run].omit                    (pyproject.toml)
        - INI:    [coverage:run] omit                         (setup.cfg, tox.ini)
        - INI:    [run] omit                                  (.coveragerc)

    Returns the offending patterns in declaration order so the
    action can name them explicitly in the warning it emits.
    Returns an empty list if no coverage config is in play, the
    file is unparsable, or every omit pattern is benign.

    This is a conservative heuristic (substring match against
    'venv' / 'site-packages'); it intentionally errs toward
    surfacing the warning rather than silently passing a
    misconfiguration.
    """
    if config_path is None or not config_path.is_file():
        return []

    suffix = config_path.suffix.lower()
    name = config_path.name.lower()

    def _filter(values: list[str]) -> list[str]:
        return [v for v in values if _omit_pattern_excludes_install(v)]

    if suffix == ".toml":
        try:
            with config_path.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError:
            return []
        omit = _toml_table(data, "tool", "coverage", "run").get("omit")
        if isinstance(omit, list):
            return _filter([item for item in omit if isinstance(item, str)])
        if isinstance(omit, str):
            return _filter([omit])
        return []

    # INI form: setup.cfg / tox.ini / .coveragerc.
    cfg = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=("#", ";"),
    )
    try:
        cfg.read(config_path)
    except configparser.Error:
        return []
    candidate_sections = ("coverage:run", "run")
    if name == ".coveragerc":
        candidate_sections = ("run", "coverage:run")
    for section in candidate_sections:
        if cfg.has_option(section, "omit"):
            raw = cfg.get(section, "omit")
            # INI multi-line values are concatenated with newlines
            # by configparser, but consumers also occasionally
            # paste a TOML-style single-line list directly into
            # an INI section ('omit = ["a", "b"]'). Split on
            # both separators and strip the punctuation noise so
            # either spelling decomposes into one entry per
            # pattern; commas are deliberately removed from the
            # strip set since we already handled them as a split
            # delimiter and dropping them again is a no-op.
            entries: list[str] = []
            for chunk in re.split(r"[\n,]", raw):
                cleaned = chunk.translate(str.maketrans("", "", "[]\"'")).strip()
                if cleaned:
                    entries.append(cleaned)
            return _filter(entries)
    return []


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
        run = _toml_table(data, "tool", "coverage", "run")
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
    name = _toml_table(data, "project").get("name")
    if not isinstance(name, str) or not name.strip():
        return ""
    return re.sub(r"[-_.]+", "_", name.strip().lower())
