<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2025 The Linux Foundation
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

## The matrix

| Fixture | Layout | `addopts` | `[tool.coverage.run].source` | Why it matters |
|--- |--- |--- |--- |--- |
| [`src-layout-path-source`](./src-layout-path-source/) | src/ | `--cov=mypkg` | `["src"]` | The exact regression case from issue #138. Path-based source + addopts `--cov` + non-editable install used to silently collect 0%. |
| [`src-layout-pkg-source`](./src-layout-pkg-source/) | src/ | `--cov` (bare) | `["mypkg"]` | The recommended shape. Package-name source resolves via import and works for any install layout. |
| [`flat-layout-no-config`](./flat-layout-no-config/) | flat | (none) | (none) | The zero-configuration case. The action must derive the package name from `[project].name` and pass `--cov=<pkg>` as a fallback - a bare `--cov` collects nothing for non-editable installs. |
| [`addopts-cov-only`](./addopts-cov-only/) | src/ | `--cov=mypkg` | (none) | The middle ground: project specifies a package via addopts but does not configure a source list. The action must NOT inject a bare `--cov` (which would override the addopts target). |

## Why these shapes specifically

The detection logic in `action.yaml` decides whether to inject a
`--cov` argument based on two signals: the presence of a non-empty
`[tool.coverage.run].source` and the presence of `--cov=` in any
pytest config file. These four fixtures cover every combination of
those two signals being on or off:

| Fixture | source set | --cov in addopts | inject --cov? |
|--- |:-: |:-: |:-: |
| `src-layout-path-source` | YES | YES | NO |
| `src-layout-pkg-source` | YES | NO | NO |
| `flat-layout-no-config` | NO | NO | YES (`--cov=<pkg>`) |
| `addopts-cov-only` | NO | YES | NO |

A regression that causes the action to inject `--cov` when it should
not (the issue #138 case) will produce 0% coverage in
`src-layout-path-source` and break the CI assertion. A regression
that causes the action to skip the fallback when it should inject
(e.g. breaking the detection regex or losing the package-name
derivation) will produce 0% coverage in `flat-layout-no-config`.

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
