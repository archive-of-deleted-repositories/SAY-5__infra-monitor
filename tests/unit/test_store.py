"""Storage layer: schema creation, host upsert, time-range query."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from infra_monitor.store import MetricSample, Storage


def test_in_memory_creates_schema() -> None:
    s = Storage.in_memory()
    assert s.list_hosts() == []


def test_upsert_host_is_idempotent(storage: Storage) -> None:
    a = storage.upsert_host(name="h1", kind="linux")
    b = storage.upsert_host(name="h1", kind="linux")
    assert a.id == b.id
    assert len(storage.list_hosts()) == 1


def test_metrics_range_query(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    rows = [
        MetricSample(
            host_id=host.id,
            metric_name="cpu_percent",
            value=float(i),
            recorded_at=base + timedelta(seconds=i),
        )
        for i in range(20)
    ]
    storage.insert_samples(rows)

    since = base + timedelta(seconds=10)
    got = storage.metrics(host_id=host.id, metric="cpu_percent", since=since)
    assert [int(r.value) for r in got] == list(range(10, 20))


def test_recent_alerts_returns_empty_initially(storage: Storage) -> None:
    assert storage.recent_alerts() == []
    assert storage.recent_remediations() == []


def test_prune_retention(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")
    base = datetime.now(UTC)
    rows = [
        MetricSample(
            host_id=host.id,
            metric_name="cpu_percent",
            value=1.0,
            recorded_at=base - timedelta(days=40),
        ),
        MetricSample(
            host_id=host.id,
            metric_name="cpu_percent",
            value=2.0,
            recorded_at=base - timedelta(days=10),
        ),
    ]
    storage.insert_samples(rows)
    pruned = storage.prune_default_retention(days=30)
    assert pruned == 1
    remaining = storage.metrics(
        host_id=host.id,
        metric="cpu_percent",
        since=base - timedelta(days=365),
    )
    assert len(remaining) == 1
