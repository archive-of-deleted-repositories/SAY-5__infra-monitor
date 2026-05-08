"""Dispatch fired alerts to their configured actions (webhook + remediation),
persist the AlertEvent, and feed remediation rows into the audit log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from infra_monitor.alerts.remediation import run as run_remediation
from infra_monitor.alerts.webhook import WebhookDelivery
from infra_monitor.store import AlertEvent, Host, Storage

if TYPE_CHECKING:
    from infra_monitor.alerts.threshold import FiredAlert

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Dispatcher:
    storage: Storage
    webhook_secret: str
    remediation_timeout_seconds: int = 60
    fired_alerts: list[FiredAlert] = field(default_factory=list)

    def dispatch(self, fired: FiredAlert) -> None:
        self.fired_alerts.append(fired)
        rule_id, host_id = self._resolve(fired)

        # Persist the AlertEvent first — even if downstream actions fail
        # (network error, broken script), the alert itself is recorded.
        with self.storage.session() as s:
            s.add(
                AlertEvent(
                    rule_id=rule_id,
                    host_id=host_id,
                    metric_value=fired.value,
                    severity=fired.rule.severity,
                    fired_at=fired.fired_at,
                )
            )

        for action in fired.rule.actions:
            try:
                if action.kind == "webhook":
                    url = action.config.get("url", "")
                    if not url:
                        log.warning("webhook action missing url for rule %s", fired.rule.name)
                        continue
                    WebhookDelivery(url=url, secret=self.webhook_secret).deliver(fired)
                elif action.kind == "remediation":
                    script = action.config.get("script", "")
                    args = list(action.config.get("args", []))
                    if not script:
                        log.warning("remediation missing script for rule %s", fired.rule.name)
                        continue
                    run_remediation(
                        self.storage,
                        rule_id=rule_id,
                        host_id=host_id,
                        script_path=script,
                        args=args,
                        timeout_seconds=self.remediation_timeout_seconds,
                        cooldown_seconds=action.cooldown_seconds,
                    )
                else:
                    log.warning("unknown action kind: %s", action.kind)
            except Exception as exc:  # noqa: BLE001 - one bad action shouldn't drop others
                log.exception("action %s failed for rule %s: %s", action.kind, fired.rule.name, exc)

    # ------------------------------------------------------------------

    def _resolve(self, fired: FiredAlert) -> tuple[int, int]:
        from sqlalchemy import select

        with self.storage.session() as s:
            host = s.execute(select(Host).where(Host.name == fired.host_key)).scalar_one_or_none()
            if host is None:
                host = Host(name=fired.host_key, kind="unknown")
                s.add(host)
                s.flush()
            from infra_monitor.store import AlertRule

            rule_row = s.execute(
                select(AlertRule).where(AlertRule.name == fired.rule.name)
            ).scalar_one_or_none()
            if rule_row is None:
                rule_row = AlertRule(
                    name=fired.rule.name,
                    metric=fired.rule.metric,
                    condition=fired.rule.condition,
                    duration_seconds=fired.rule.duration_seconds,
                    actions_json=[
                        {"kind": a.kind, "config": a.config, "cooldown_seconds": a.cooldown_seconds}
                        for a in fired.rule.actions
                    ],
                    enabled=True,
                )
                s.add(rule_row)
                s.flush()
            return rule_row.id, host.id
