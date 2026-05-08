"""Shared fixtures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from infra_monitor.store import Storage


@pytest.fixture()
def storage() -> Storage:
    return Storage.in_memory()


@pytest.fixture()
def now() -> datetime:
    return datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
