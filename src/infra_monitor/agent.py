"""Agent loop: pull from a collector, persist samples, feed the rule engine,
hand fired alerts to the dispatcher. Runs as ``infra-monitor agent`` or once
via :func:`tick` from tests / the simulator.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from infra_monitor.store import MetricSample, Storage

if TYPE_CHECKING:
    from infra_monitor.alerts.threshold import RuleEngine
    from infra_monitor.collectors._base import Collector, Sample
    from infra_monitor.dispatcher import Dispatcher

log = logging.getLogger(__name__)


@dataclass
class Agent:
    storage: Storage
    collectors: list[Collector]
    engine: RuleEngine
    dispatcher: Dispatcher

    def tick(self) -> list[Sample]:
        all_samples: list[Sample] = []
        for c in self.collectors:
            try:
                samples = c.collect()
            except Exception as exc:  # noqa: BLE001
                log.exception("collector %s raised: %s", c.name, exc)
                continue
            all_samples.extend(samples)
            self._persist(samples)
            self._evaluate(samples)
        return all_samples

    def run_forever(self, interval_seconds: float) -> None:  # pragma: no cover - loop
        log.info("agent started; interval=%.1fs", interval_seconds)
        while True:
            self.tick()
            time.sleep(interval_seconds)

    # ------------------------------------------------------------------

    def _persist(self, samples: list[Sample]) -> None:
        if not samples:
            return
        rows: list[MetricSample] = []
        for sample in samples:
            host = self.storage.upsert_host(name=sample.host_name, kind=sample.host_kind)
            rows.append(
                MetricSample(
                    host_id=host.id,
                    metric_name=sample.metric,
                    value=sample.value,
                    recorded_at=sample.recorded_at,
                    tags=sample.tags or None,
                )
            )
        self.storage.insert_samples(rows)

    def _evaluate(self, samples: list[Sample]) -> None:
        for sample in samples:
            fired = self.engine.feed(
                host_key=sample.host_name,
                metric=sample.metric,
                value=sample.value,
                now=sample.recorded_at,
            )
            for ev in fired:
                self.dispatcher.dispatch(ev)
