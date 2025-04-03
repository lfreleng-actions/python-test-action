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
```

Note: build your project before invoking tests (not shown above)

<!-- markdownlint-enable MD046 -->

## Inputs

<!-- markdownlint-disable MD013 -->

| Variable Name   | Required | Default      | Description                                          |
| --------------- | -------- | ------------ | ---------------------------------------------------- |
| PYTHON_VERSION  | True     |              | Python version used to run tests                     |
| PERMIT_FAIL     | False    | False        | Continue even when one or more tests fail            |
| REPORT_ARTEFACT | False    | True         | Uploads test/coverage report bundle as artefact      |
| PATH_PREFIX     | False    |              | Directory location containing Python project code    |
| TESTS_PATH      | False    | test/tests   | Path relative to the project folder containing tests |
| TOX_TESTS       | False    | False        | Uses tox to perform Python tests (requires tox.ini)  |
| TOX_ENVS        | False    | "lint tests" | Space separated list of tox environment names to run |

<!-- markdownlint-enable MD013 -->

## Coverage Reports

The embedded pytest behaviour will create HTML coverage reports as ZIP file
bundles. Set REPORT_ARTEFACT true to also upload them to GitHub as artefacts.
