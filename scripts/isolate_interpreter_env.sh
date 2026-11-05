# shellcheck shell=bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Sourced by action.yaml steps before they start uv or Python.
#
# A composite action inherits its caller job's environment, so an
# earlier step can set variables that make uv or Python select, or
# redirect to, an interpreter the caller chose. Remove them from the
# sourcing step's shell; the action names its own interpreter by
# absolute path instead. Index, proxy and authentication settings
# (UV_INDEX*, UV_DEFAULT_INDEX, UV_KEYRING_PROVIDER, HTTPS_PROXY, ...)
# are left alone. The unset lasts for the sourcing step only.

for _isolate_name in $(compgen -e); do
  case "$_isolate_name" in
    UV_PYTHON* | UV_SYSTEM_PYTHON | UV_MANAGED_PYTHON | \
      UV_NO_MANAGED_PYTHON | UV_PROJECT_ENVIRONMENT | UV_INTERNAL__* | \
      VIRTUAL_ENV* | CONDA_* | PYTHON* | __PYVENV_LAUNCHER__)
      unset "$_isolate_name"
      ;;
  esac
done
unset _isolate_name
