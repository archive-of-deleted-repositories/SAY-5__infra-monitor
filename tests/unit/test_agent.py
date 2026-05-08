"""Agent loop end-to-end at unit level: collector → store → engine → dispatcher."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from infra_monitor.agent import Agent
from infra_monitor.alerts.threshold import Action, Rule, RuleEngine
from infra_monitor.collectors._base import Sample
from infra_monitor.dispatcher import Dispatcher

if TYPE_CHECKING:
    from infra_monitor.store import Storage


class _StaticCollector:
    name = "static"

    def __init__(self, samples: list[Sample]) -> None:
        self._samples = samples

    def collect(self) -> list[Sample]:
        return list(self._samples)


def test_tick_persists_and_fires(storage: Storage) -> None:
    rule = Rule(
        name="cpu",
        metric="cpu_percent",
        condition=">90",
        duration_seconds=0,
        actions=(Action(kind="webhook", config={"url": "http://x"}, cooldown_seconds=60),),
    )
    engine = RuleEngine(rules=[rule])
    dispatcher = Dispatcher(storage=storage, webhook_secret="x")

    sample = Sample(
        host_name="h1",
        host_kind="linux",
        metric="cpu_percent",
        value=99.0,
        recorded_at=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )
    a = Agent(
        storage=storage,
        collectors=[_StaticCollector([sample])],
        engine=engine,
        dispatcher=dispatcher,
    )

    # patch out the actual webhook send so we don't need the network
    dispatcher.dispatch = MagicMock(wraps=dispatcher.dispatch)  # type: ignore[method-assign]

    out = a.tick()
    assert len(out) == 1
    assert dispatcher.dispatch.called

    hosts = storage.list_hosts()
    assert len(hosts) == 1
    assert hosts[0].name == "h1"


def test_tick_continues_after_collector_failure(storage: Storage) -> None:
    class _Boom:
        name = "boom"

        def collect(self) -> list[Sample]:
            raise RuntimeError("oops")

    sample = Sample(
        host_name="h1",
        host_kind="linux",
        metric="cpu_percent",
        value=10.0,
        recorded_at=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )

    engine = RuleEngine(rules=[])
    dispatcher = Dispatcher(storage=storage, webhook_secret="x")
    a = Agent(
        storage=storage,
        collectors=[_Boom(), _StaticCollector([sample])],
        engine=engine,
        dispatcher=dispatcher,
    )
    out = a.tick()
    assert len(out) == 1
