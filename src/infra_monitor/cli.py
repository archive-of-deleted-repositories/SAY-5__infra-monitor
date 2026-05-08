"""``infra-monitor`` CLI.

Subcommands
-----------
* ``migrate``    — create the schema (alembic-equivalent: ``Base.metadata.create_all``)
* ``seed``       — insert a sample host + sample rule for demos
* ``agent``      — run the local psutil collector loop
* ``dashboard``  — serve the FastAPI dashboard on :8080
* ``alerts``     — list recent alert events
* ``simulate``   — feed synthetic metrics into the engine to demonstrate the alert flow
"""

from __future__ import annotations

import logging
import math
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import click

from infra_monitor.agent import Agent
from infra_monitor.alerts.threshold import Action, Rule, RuleEngine
from infra_monitor.collectors._base import Sample
from infra_monitor.collectors.psutil import PsutilCollector
from infra_monitor.config_loader import load_rules
from infra_monitor.dispatcher import Dispatcher
from infra_monitor.settings import get_settings
from infra_monitor.store import Storage

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@click.group()
def main() -> None:
    """infra-monitor — system metrics + threshold alerts + remediation."""
    _setup_logging()


@main.command()
def migrate() -> None:
    """Create database tables."""
    cfg = get_settings()
    store = Storage.from_url(cfg.database_url)
    store.create_all()
    click.echo(f"schema applied to {cfg.database_url}")


@main.command()
def seed() -> None:
    """Insert a sample host + sample rule so the dashboard isn't empty."""
    cfg = get_settings()
    store = Storage.from_url(cfg.database_url)
    store.create_all()
    store.upsert_host(name="demo-host", kind="linux")
    click.echo("seeded demo-host")


@main.command()
@click.option("--interval", type=float, default=None, help="seconds between collections")
def agent(interval: float | None) -> None:
    """Run the local psutil collector loop. Ctrl-C to stop."""
    cfg = get_settings()
    store = Storage.from_url(cfg.database_url)
    store.create_all()

    rules = _load_rules_or_empty(cfg.config_path)
    engine = RuleEngine(rules=rules)
    dispatcher = Dispatcher(
        storage=store,
        webhook_secret=cfg.webhook_secret,
        remediation_timeout_seconds=cfg.remediation_timeout_seconds,
    )

    a = Agent(
        storage=store,
        collectors=[PsutilCollector()],
        engine=engine,
        dispatcher=dispatcher,
    )
    a.run_forever(interval_seconds=interval or cfg.agent_interval_seconds)


@main.command()
@click.option("--host", type=str, default="127.0.0.1")
@click.option("--port", type=int, default=8080)
def dashboard(host: str, port: int) -> None:
    """Serve the FastAPI dashboard."""
    import uvicorn

    from infra_monitor.api import create_app

    cfg = get_settings()
    store = Storage.from_url(cfg.database_url)
    store.create_all()
    app = create_app(storage=store, settings=cfg)
    uvicorn.run(app, host=host, port=port, log_level="info")


@main.command(name="alerts")
@click.option("--limit", type=int, default=20)
def alerts_cmd(limit: int) -> None:
    """List recent alert events."""
    cfg = get_settings()
    store = Storage.from_url(cfg.database_url)
    rows = store.recent_alerts(limit=limit)
    if not rows:
        click.echo("(no alerts)")
        return
    for r in rows:
        click.echo(
            f"{r.fired_at.isoformat()} rule={r.rule_id} host={r.host_id} "
            f"value={r.metric_value:.2f} severity={r.severity}"
        )


@main.command()
@click.option("--seconds", type=int, default=120, help="how long to simulate")
@click.option("--rate", type=float, default=1.0, help="samples/second")
def simulate(seconds: int, rate: float) -> None:
    """Feed synthetic CPU samples that breach a 90% rule and watch it fire.

    Useful for screen-recordings and for verifying the dashboard end-to-end
    without a real load generator.
    """
    cfg = get_settings()
    store = Storage.from_url(cfg.database_url)
    store.create_all()
    store.upsert_host(name="sim-host", kind="linux")

    rule = Rule(
        name="sim-cpu-saturation",
        metric="cpu_percent",
        condition=">90",
        duration_seconds=10,
        actions=(
            Action(
                kind="webhook", config={"url": "http://127.0.0.1:9/ignored"}, cooldown_seconds=60
            ),
        ),
    )
    engine = RuleEngine(rules=[rule])
    dispatcher = Dispatcher(storage=store, webhook_secret=cfg.webhook_secret)

    period = 1.0 / max(rate, 0.1)
    start = datetime.now(UTC)
    n = int(seconds * rate)
    for i in range(n):
        ts = start + timedelta(seconds=i / rate)
        # Sine-wave centred at 80, amplitude 25 → spends time above 90.
        cpu = 80.0 + 25.0 * math.sin(i / 8.0)
        sample = Sample(
            host_name="sim-host",
            host_kind="linux",
            metric="cpu_percent",
            value=cpu,
            recorded_at=ts,
        )
        host = store.upsert_host(name=sample.host_name, kind=sample.host_kind)
        from infra_monitor.store import MetricSample

        store.insert_samples(
            [
                MetricSample(
                    host_id=host.id, metric_name=sample.metric, value=sample.value, recorded_at=ts
                )
            ]
        )
        for fired in engine.feed(
            host_key=sample.host_name, metric=sample.metric, value=sample.value, now=ts
        ):
            dispatcher.dispatch(fired)
        if rate <= 100:
            time.sleep(period * 0.0)  # don't actually sleep — let it run as fast as it can
    click.echo(f"simulated {n} samples; fired={len(dispatcher.fired_alerts)}")


def _load_rules_or_empty(path: Path) -> list[Rule]:
    if not path.exists():
        log.warning("config file not found at %s; running with no rules", path)
        return []
    return load_rules(path)


if __name__ == "__main__":  # pragma: no cover
    main()
