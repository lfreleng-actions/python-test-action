# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Trivial fixture package used to exercise coverage collection."""


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Return the product of two integers."""
    return a * b
