<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# 🐍 Test Python Project

Tests a Python project and generates coverage reports.

There are two ways for tests to run:

- Using pytest and pytest-cov to run tests and generate coverage reports
- Using tox when provided with a suitable configuration file

## python-test-action

## Usage Example

The example below demonstrates an implementation as a matrix job:

<!-- markdownlint-disable MD046 -->

```yaml
  python-tests:
    name: "Python Test"
    runs-on: "ubuntu-24.04"
    needs:
      - python-build
    # Matrix job
    strategy:
      fail-fast: false
      matrix: ${{ fromJson(needs.python-build.outputs.matrix_json) }}
    permissions:
      contents: read
    steps:
      - name: "Test Python project"
        uses: lfreleng-actions/python-test-action@main
        with:
          python_version: ${{ matrix.python-version }}
          report_artefact: true
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

Note: build your project before invoking tests (not shown above)

<!-- markdownlint-enable MD046 -->

## Inputs

<!-- markdownlint-disable MD013 -->

| Variable Name   | Required | Default       | Description                                                               |
| --------------- | -------- | ------------- | ------------------------------------------------------------------------- |
| python_version  | True     |               | Python version used to run tests                                          |
| editable        | False    | False         | Install Python package in editable mode                                   |
| permit_fail     | False    | False         | Continue even when one or more tests fail                                 |
| report_artefact | False    | True          | Uploads test/coverage report bundle as artefact                           |
| artefact_name   | False    |               | Custom name for uploaded artefact                                         |
| path_prefix     | False    | .             | Directory location containing Python project code                         |
| tests_path      | False    | auto-detect   | Relative path to the folder containing tests (detects: `test` or `tests`) |
| tox_tests       | False    | False         | Uses tox to perform Python tests (requires tox.ini)                       |
| tox_envs        | False    | "lint tests"  | Space separated list of tox environment names to run                      |
| github_token    | False    |               | GitHub token for API access during tests                                  |
| pytest_args     | False    |               | Custom pytest arguments (e.g., -n0, -v, --tb=short)                       |

<!-- markdownlint-enable MD013 -->

Set artefact_name to avoid artefact naming conflicts when matrix jobs
produce identical filenames, which can cause workflow failures when
attempting to upload them to a workflow run.

## Editable Installation Mode

By default, the action installs the Python package in **standard (non-editable)**
mode. This is the recommended approach for CI/CD testing as it:

- Tests the package as users will actually install it
- Avoids rebuild issues with complex build systems (e.g., meson-python)
- Is faster and more reliable in CI environments

Set `editable: true` when you specifically need editable installs for
development workflows or when testing local code changes.

### Example with Editable Mode

```yaml
- name: "Test Python project with editable install"
  uses: lfreleng-actions/python-test-action@main
  with:
    python_version: "3.12"
    editable: true
```

## Coverage Reports

The embedded pytest behaviour will create HTML coverage reports as ZIP file
bundles. Set REPORT_ARTEFACT true to also upload them to GitHub as artefacts.

## GitHub Token Support

The action accepts an optional `github_token` input parameter that makes the
GitHub token available to your tests as the `GITHUB_TOKEN` environment variable.
This is useful for tests that need to interact with GitHub APIs or access
private repositories without encountering rate limits.

### Example with GitHub Token

```yaml
- name: "Test Python project with GitHub API access"
  uses: lfreleng-actions/python-test-action@main
  with:
    python_version: "3.12"
    github_token: ${{ secrets.GITHUB_TOKEN }}
    report_artefact: true
```

## Custom Pytest Arguments

The action supports passing custom arguments to pytest through the
`pytest_args` input. This is useful for controlling test execution behavior,
such as disabling parallel execution or adjusting output verbosity.

**Security Note:** The action validates and sanitizes all pytest arguments to
prevent command injection. You must use safe characters and pytest-compatible flags.

### Example with Custom Pytest Arguments

```yaml
- name: "Test Python project with serial execution"
  uses: lfreleng-actions/python-test-action@main
  with:
    python_version: "3.10"
    pytest_args: "-n0"  # Disable parallel execution
    report_artefact: true
```

```yaml
- name: "Test Python project with custom arguments"
  uses: lfreleng-actions/python-test-action@main
  with:
    python_version: "3.10"
    pytest_args: "-v -x --tb=short"  # Three arguments combined
    report_artefact: true
```

### Common pytest_args Examples

- `-n0` - Disable parallel execution (run tests serially)
- `-v` - Verbose output
- `-vv` - Extra verbose output
- `--tb=short` - Shorter traceback format
- `-k test_name` - Run tests matching the expression
- `-x` - Stop after first failure
- `--maxfail=2` - Stop after 2 failures

**Note:** The action accepts pytest arguments that start with `-` or `--`,
or valid pytest expressions/paths. The action validates arguments to ensure they
contain safe characters (alphanumeric, space, `-`, `=`, `,`, `.`, `/`, `:`, `_`).
Use space-separated strings to pass arguments (e.g., `"-n0 -v"`).
