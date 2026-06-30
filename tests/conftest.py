"""Shared pytest configuration."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark non-integration tests as unit."""
    for item in items:
        if "integration" not in item.keywords:
            item.add_marker(pytest.mark.unit)
