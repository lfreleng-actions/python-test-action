#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
#
# Validate the KEY=VALUE lines detect_coverage.py prints, before the
# calling step writes them to $GITHUB_ENV.
#
# This is the second of two independent layers. detect_coverage.py
# already strips control characters from every value it emits, but
# this script is what stands between that output and the environment
# file, so it re-checks here rather than trusting the producer. A
# line outside the allowed key set means either a bug in the script
# or an attempt to smuggle an assignment (BASH_ENV, LD_PRELOAD, ...)
# out of a consumer's pyproject.toml, which is untrusted input
# whenever the action runs against an unreviewed contribution.
#
# Reads the candidate lines on stdin. Exits 0 when every line is a
# recognised assignment free of control characters, 1 otherwise,
# naming the offending line on stderr (shell-quoted, so an embedded
# CR cannot mangle the log). Living in its own file (rather than
# inline in action.yaml) keeps it directly testable; see
# tests/test_detect_coverage.py.

set -euo pipefail

# The exact keys detect_coverage.py may emit. Kept in sync with the
# 'out' tuple in that script's main(), and asserted against it by
# test_validator_accepts_real_script_output.
ALLOWED_KEYS='coverage_source_configured'
ALLOWED_KEYS="${ALLOWED_KEYS}|cov_in_addopts"
ALLOWED_KEYS="${ALLOWED_KEYS}|coverage_inject_cov"
ALLOWED_KEYS="${ALLOWED_KEYS}|coverage_target_configured"
ALLOWED_KEYS="${ALLOWED_KEYS}|coverage_fallback_pkg"
ALLOWED_KEYS="${ALLOWED_KEYS}|coverage_omit_excludes_install"
ALLOWED_KEYS="${ALLOWED_KEYS}|coverage_problematic_omit_patterns"

while IFS= read -r line; do
  [ -n "$line" ] || continue

  # 'read' splits on LF only, so any other control character - most
  # importantly CR - survives inside "$line". The runner treats a lone
  # CR as a record terminator when it parses the environment file, so
  # 'coverage_fallback_pkg=x\rBASH_ENV=/tmp/payload' would satisfy the
  # key check below and still forge an assignment. Reject the whole
  # class here so this layer holds on its own if _env_safe() in
  # detect_coverage.py ever regresses. LC_ALL=C keeps the class
  # byte-wise, so multi-byte UTF-8 in a package name still passes.
  if printf '%s' "$line" | LC_ALL=C grep -q '[[:cntrl:]]'; then
    echo "Error: detect_coverage.py emitted a line containing a" \
      "control character; refusing to write it to the environment ❌" >&2
    printf '  %q\n' "$line" >&2
    exit 1
  fi

  if ! printf '%s' "$line" | grep -Eq "^(${ALLOWED_KEYS})="; then
    echo "Error: detect_coverage.py emitted an unexpected line;" \
      "refusing to write it to the environment ❌" >&2
    printf '  %q\n' "$line" >&2
    exit 1
  fi
done

exit 0
