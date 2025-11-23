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

| Variable Name   | Required | Default      | Description                                          |
| --------------- | -------- | ------------ | ---------------------------------------------------- |
| python_version  | True     |              | Python version used to run tests                     |
| artefact_name   | False    |              | Custom name for artefacts (defaults to project name). Useful when building for different platforms/architectures to avoid artefact name conflicts |
| permit_fail     | False    | False        | Continue even when one or more tests fail            |
| report_artefact | False    | True         | Uploads test/coverage report bundle as artefact      |
| path_prefix     | False    |              | Directory location containing Python project code    |
| tests_path      | False    | test/tests   | Path relative to the project folder containing tests |
| tox_tests       | False    | False        | Uses tox to perform Python tests (requires tox.ini)  |
| tox_envs        | False    | "lint tests" | Space separated list of tox environment names to run |
| github_token    | False    |              | GitHub token for API access during tests             |

<!-- markdownlint-enable MD013 -->

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
