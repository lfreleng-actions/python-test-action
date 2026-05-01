<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# Coverage configuration regression fixtures

These fixtures exercise the matrix of `pyproject.toml` shapes the action
must handle without silently reporting 0% coverage. Each fixture is a
self-contained Python project: a single-module package, two trivial
tests, and a `pyproject.toml` configured to mirror a real-world pattern
seen in the wild.

The CI job [`coverage-fixtures` in `.github/workflows/testing.yaml`][ci]
runs the action against every fixture with `editable: false` (the
action's default) and asserts:

1. All tests pass.
2. The generated `coverage.xml` reports a non-zero `line-rate`.

Both assertions are needed because the regression tracked in
[issue #138][issue138] manifested as "tests pass, but coverage is 0%" -
silent and easy to miss without a coverage-value check.

[ci]: ../.github/workflows/testing.yaml
[issue138]: https://github.com/lfreleng-actions/python-test-action/issues/138

## Why a top-level `.fixtures/` directory?

Fixtures live at the repository root rather than under `tests/`
because the action's auto-generated fallback `.coveragerc` includes
`*/tests/*` in its `omit` list (a sensible default for real consumers
who want to exclude their own test code from coverage). Putting our
fixture projects under `tests/fixtures/` would cause that pattern to
match the fixtures' own package code (whose absolute path would
contain `/tests/`), defeating the very assertions this matrix is
designed to provide. The leading dot keeps the directory out of
common file-discovery heuristics for any future packaging of the
action repository.

## The decision matrix

The detection logic in `action.yaml` answers two independent
questions about every consumer project:

- **`cov_in_addopts`** - does any pytest config file
  (`pyproject.toml` `addopts`, `pytest.ini`, `setup.cfg`, `tox.ini`)
  contain `--cov` (bare or with `=<pkg>`)?
- **`coverage_source_configured`** - does the discovered coverage
  config set a non-empty `[tool.coverage.run].source` /
  `[coverage:run] source` / `[run] source`?

Each signal drives a different decision:

| Signal                       | Decision it drives                                                  |
|---                           |---                                                                  |
| `cov_in_addopts`             | Whether the action injects `--cov` itself                           |
| `coverage_source_configured` | Whether the action emits the "no coverage target configured" warning AND whether the action derives a fallback package name |

When `cov_in_addopts=false` (the action will inject `--cov`), the
form of the injection depends on `coverage_source_configured`:

- **`source_configured=true`**: inject a bare `--cov`. The
  configured source list scopes collection; the bare `--cov` only
  needs to activate pytest-cov.
- **`source_configured=false`**: derive the package name from
  `[project].name` and inject `--cov=<pkg>`. Without an explicit
  target, pytest-cov needs to know which package to track, since
  coverage.py's default rule excludes site-packages.

## The fixtures

| Fixture                                                  | Layout | `addopts` | `[tool.coverage.run].source` | Action's `--cov` decision |
|---                                                       |---     |---        |---                           |---                        |
| [`src-layout-path-source`](./src-layout-path-source/)    | src/   | `--cov=mypkg` | `["src"]`                | NO injection (addopts wins)         |
| [`src-layout-pkg-source`](./src-layout-pkg-source/)      | src/   | (none)        | `["mypkg"]`              | inject bare `--cov` (source scopes) |
| [`flat-layout-no-config`](./flat-layout-no-config/)      | flat   | (none)        | (none)                   | inject `--cov=mypkg` (derived)      |
| [`addopts-cov-only`](./addopts-cov-only/)                | src/   | `--cov=mypkg` | (none)                   | NO injection (addopts wins)         |

### Why each fixture matters

- **`src-layout-path-source`** is the exact regression case from
  issue #138. Path-based source + addopts `--cov` + non-editable
  install used to silently collect 0% because the action injected
  an unconditional bare `--cov` on top of the addopts entry. With
  the fix, `cov_in_addopts=true` suppresses injection, so addopts
  wins and pytest-cov collects against `mypkg` from `site-packages`.

- **`src-layout-pkg-source`** is the recommended shape:
  package-name source, no `--cov` in addopts. The action sees
  source configured (so no warning) but no `--cov` in addopts (so
  it must inject one). It injects a bare `--cov` to activate
  pytest-cov; `[tool.coverage.run].source = ["mypkg"]` then scopes
  collection. This is the cell that broke before fix round 2: with
  the original "either signal suppresses injection" logic,
  source-set was enough to suppress `--cov` and pytest-cov silently
  did nothing.

- **`flat-layout-no-config`** is the zero-configuration case: no
  source list, no `--cov` anywhere. The action emits a warning to
  both console and `$GITHUB_STEP_SUMMARY` recommending explicit
  configuration, then derives the package name from
  `[project].name` and injects `--cov=mypkg` so collection runs. A
  bare `--cov` here would collect zero data for non-editable
  installs (coverage.py's default rule excludes `site-packages`),
  so the explicit `--cov=<pkg>` is required.

- **`addopts-cov-only`** is the middle case: `--cov=mypkg` in
  addopts, no source list. The detection regex must spot
  `--cov=mypkg` and skip injection (otherwise the same regression
  as `src-layout-path-source` reappears via a different signal).
  The regex deliberately excludes `--cov-report` / `--cov-config` /
  `--cov-fail-under` to avoid false positives.

## Running fixtures locally

Each fixture is a normal Python project. To reproduce what CI does
against, say, `src-layout-path-source`:

```sh
cd .fixtures/src-layout-path-source
uv venv .venv
. .venv/bin/activate
uv pip install '.[dev]'           # non-editable, matches action default
uv pip install pytest pytest-cov 'coverage[toml]'
python -m pytest \
  --cov-report=term-missing \
  --cov-report=xml:/tmp/coverage.xml \
  --cov-config=./pyproject.toml \
  tests
```

Without the action's fix, simulating its broken v1.1.0 behaviour by
adding a bare `--cov` to that command line causes
`src-layout-path-source` to report 0% coverage. With the fix, the
action detects the existing `--cov=mypkg` in addopts and skips the
bare `--cov`, producing the expected non-zero coverage.
