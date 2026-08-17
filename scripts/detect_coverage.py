# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
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

The config-file introspection itself lives in the sibling module
``coverage_config.py``; this file holds only the decision logic and
the output contract. The sibling directory is prepended to
``sys.path`` explicitly below rather than relying on the implicit
script-directory entry, which safe-path mode suppresses.

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

import re
import sys
from pathlib import Path

# Python normally puts the script's own directory first on sys.path,
# but safe-path mode (PYTHONSAFEPATH=1, or 'python -P') suppresses
# that entry, and a PYTHONPATH entry could otherwise shadow the
# sibling helper with an unrelated 'coverage_config'. Both are under
# the consumer's control, since their workflow env reaches this step.
# Prepend the resolved directory explicitly so the module loaded here
# is always the one action.yaml just checked for next to this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from coverage_config import (
    has_cov_in_addopts,
    has_nonempty_coverage_source,
    problematic_omit_patterns,
    project_import_name,
)

# Control characters must never reach a value this script prints. The
# surrounding action.yaml step appends our stdout to $GITHUB_ENV
# verbatim, so a CR or LF inside a value would terminate the intended
# assignment and start one the consumer chose - BASH_ENV, LD_PRELOAD
# or similar. That is reachable from a checked-in pyproject.toml,
# because TOML strings can be multi-line:
#
#     [tool.coverage.run]
#     omit = ["""*/.venv/*
#     BASH_ENV=/tmp/payload"""]
#
# and pyproject.toml is untrusted input whenever this action runs
# against an unreviewed contribution. Both consumer-derived values
# ('coverage_fallback_pkg' from [project].name and
# 'coverage_problematic_omit_patterns' from the omit list) can carry
# one. Substituting a space rather than deleting keeps the warning
# text readable and avoids gluing two tokens into one.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _env_safe(value: str) -> str:
    """Neutralise characters that could forge an extra $GITHUB_ENV line."""
    return _CONTROL_CHARS.sub(" ", value)


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(
            "Usage: detect_coverage.py <project_prefix> [coverage_config_path]",
            file=sys.stderr,
        )
        # 64 is BSD sysexits.h's EX_USAGE - distinct from exit code
        # 2 (raised by coverage_config when no TOML parser is
        # available) so callers can distinguish 'caller bug' from
        # 'environment missing required dependency'.
        return 64

    project = Path(argv[1])
    cov_config_arg = argv[2] if len(argv) == 3 else ""
    cov_config = Path(cov_config_arg) if cov_config_arg else None

    cov_in_addopts = has_cov_in_addopts(project)
    source_configured = has_nonempty_coverage_source(cov_config)
    bad_omit_patterns = problematic_omit_patterns(cov_config)

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

    # Persist the offending patterns as a ';'-joined string so the
    # surrounding shell can splat them back into the warning text
    # without having to re-parse the config. ';' is chosen because
    # coverage.py glob syntax has no special meaning for it, and
    # because it survives '$GITHUB_ENV' single-line writes that
    # newlines would not.
    omit_excludes_install = bool(bad_omit_patterns)
    omit_patterns_blob = ";".join(bad_omit_patterns)

    out = (
        ("coverage_source_configured", "true" if source_configured else "false"),
        ("cov_in_addopts", "true" if cov_in_addopts else "false"),
        ("coverage_inject_cov", "true" if inject_cov else "false"),
        (
            "coverage_target_configured",
            "true" if target_configured else "false",
        ),
        ("coverage_fallback_pkg", fallback_pkg),
        (
            "coverage_omit_excludes_install",
            "true" if omit_excludes_install else "false",
        ),
        ("coverage_problematic_omit_patterns", omit_patterns_blob),
    )
    for key, value in out:
        # Single choke point: every emitted value passes through
        # _env_safe, so a new consumer-derived field cannot bypass it.
        print(f"{key}={_env_safe(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
