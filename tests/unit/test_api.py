"""FastAPI dashboard: routes return the right shapes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from infra_monitor.api import create_app
from infra_monitor.store import AlertEvent, MetricSample, RemediationAction, Storage


def _client(storage: Storage) -> TestClient:
    app = create_app(storage=storage)
    return TestClient(app)


def test_healthz(storage: Storage) -> None:
    c = _client(storage)
    r = c.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_index_renders(storage: Storage) -> None:
    c = _client(storage)
    r = c.get("/")
    assert r.status_code == 200
    assert "infra-monitor" in r.text


def test_hosts_endpoint(storage: Storage) -> None:
    storage.upsert_host(name="h1", kind="linux")
    storage.upsert_host(name="i-abc", kind="ec2", cloud_id="i-abc")
    c = _client(storage)
    r = c.get("/api/hosts")
    assert r.status_code == 200
    rows = r.json()
    names = {row["name"] for row in rows}
    assert names == {"h1", "i-abc"}


def test_metrics_endpoint(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")
    base = datetime.now(UTC) - timedelta(minutes=10)
    storage.insert_samples(
        [
            MetricSample(
                host_id=host.id,
                metric_name="cpu_percent",
                value=v,
                recorded_at=base + timedelta(seconds=i),
            )
            for i, v in enumerate([10.0, 20.0, 30.0])
        ]
    )
    c = _client(storage)
    r = c.get(f"/api/metrics?host_id={host.id}&metric=cpu_percent&minutes=60")
    assert r.status_code == 200
    body = r.json()
    assert body["host_id"] == host.id
    assert body["metric"] == "cpu_percent"
    assert [p["v"] for p in body["points"]] == [10.0, 20.0, 30.0]


def test_alerts_endpoint(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")
    with storage.session() as s:
        from infra_monitor.store import AlertRule

        rule = AlertRule(
            name="r", metric="cpu_percent", condition=">90", duration_seconds=0, actions_json=[]
        )
        s.add(rule)
        s.flush()
        s.add(
            AlertEvent(
                rule_id=rule.id,
                host_id=host.id,
                metric_value=99.0,
                severity="warning",
                fired_at=datetime.now(UTC),
            )
        )
    c = _client(storage)
    r = c.get("/api/alerts?limit=10")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["severity"] == "warning"


def test_remediations_endpoint(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")
    with storage.session() as s:
        s.add(
            RemediationAction(
                rule_id=1,
                host_id=host.id,
                script_path="s.sh",
                args_json=["a"],
                exit_code=0,
                stdout_excerpt="ok",
                stderr_excerpt="",
                duration_ms=42,
            )
        )
    c = _client(storage)
    r = c.get("/api/remediations")
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["script"] == "s.sh"
    assert rows[0]["args"] == ["a"]
    assert rows[0]["duration_ms"] == 42
