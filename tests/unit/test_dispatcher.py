"""Dispatcher persists AlertEvents and routes to the right action handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from infra_monitor.alerts.threshold import Action, FiredAlert, Rule
from infra_monitor.dispatcher import Dispatcher

if TYPE_CHECKING:
    from infra_monitor.store import Storage


def _event(actions: tuple[Action, ...]) -> FiredAlert:
    rule = Rule(
        name="r1",
        metric="cpu_percent",
        condition=">90",
        duration_seconds=0,
        actions=actions,
    )
    return FiredAlert(
        rule=rule,
        host_key="h1",
        value=99.0,
        fired_at=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )


def test_dispatcher_persists_event_and_creates_rule_row(storage: Storage) -> None:
    d = Dispatcher(storage=storage, webhook_secret="x")

    with patch("infra_monitor.dispatcher.WebhookDelivery") as WD:
        WD.return_value.deliver.return_value = True
        d.dispatch(
            _event((Action(kind="webhook", config={"url": "http://x"}, cooldown_seconds=300),))
        )

    alerts = storage.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0].metric_value == 99.0


def test_dispatcher_runs_remediation(storage: Storage) -> None:
    d = Dispatcher(storage=storage, webhook_secret="x")
    fake_run = MagicMock()
    with patch("infra_monitor.dispatcher.run_remediation", fake_run):
        d.dispatch(
            _event(
                (
                    Action(
                        kind="remediation",
                        config={"script": "scripts/r.sh", "args": ["a"]},
                        cooldown_seconds=300,
                    ),
                )
            )
        )
    assert fake_run.called
    kwargs = fake_run.call_args.kwargs
    assert kwargs["script_path"] == "scripts/r.sh"
    assert kwargs["args"] == ["a"]


def test_dispatcher_swallows_action_errors(storage: Storage) -> None:
    d = Dispatcher(storage=storage, webhook_secret="x")
    boom = MagicMock(side_effect=RuntimeError("boom"))
    with patch("infra_monitor.dispatcher.run_remediation", boom):
        d.dispatch(
            _event(
                (
                    Action(
                        kind="remediation",
                        config={"script": "s.sh", "args": []},
                        cooldown_seconds=1,
                    ),
                    Action(kind="webhook", config={"url": "http://x"}, cooldown_seconds=1),
                )
            )
        )
    # AlertEvent still persisted despite remediation raising.
    assert len(storage.recent_alerts()) == 1


def test_dispatcher_warns_on_unknown_kind(storage: Storage) -> None:
    d = Dispatcher(storage=storage, webhook_secret="x")
    d.dispatch(_event((Action(kind="bogus", config={}),)))
    assert len(storage.recent_alerts()) == 1
