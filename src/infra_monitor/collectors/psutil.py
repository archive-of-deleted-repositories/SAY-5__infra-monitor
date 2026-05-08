"""Local Linux collector. Uses :mod:`psutil` (not ``/proc`` reads directly)
because psutil already handles the Linux/Mac/Windows differences and the
``/proc`` field-shifts between kernel versions. The output shape is what
matters for the rest of the pipeline.

Metrics emitted (per call):

* ``cpu_percent``   — system-wide CPU utilisation
* ``memory_percent`` — RAM usage
* ``disk_percent``  — root partition fill, ``mountpoint`` tag
* ``network_bytes_sent`` / ``network_bytes_recv`` — cumulative counters
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from types import ModuleType

from infra_monitor.collectors._base import Sample


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class PsutilCollector:
    """Sample local system metrics. ``psutil_module`` injectable for tests."""

    name: str = "psutil"
    host_name: str | None = None
    psutil_module: ModuleType | None = None
    mountpoint: str = "/"

    def __post_init__(self) -> None:
        if self.host_name is None:
            self.host_name = socket.gethostname()
        if self.psutil_module is None:  # pragma: no cover - exercised in real run
            import psutil  # noqa: WPS433

            self.psutil_module = psutil

    def collect(self) -> list[Sample]:
        ps = self.psutil_module
        assert ps is not None  # for mypy; set in __post_init__
        ts = _now()
        samples: list[Sample] = []

        # CPU. ``interval=None`` returns the value since the previous call,
        # which is what we want for an interval-based agent loop.
        try:
            cpu = float(ps.cpu_percent(interval=None))
            samples.append(self._sample("cpu_percent", cpu, ts))
        except Exception:  # noqa: BLE001 - psutil can raise OSError on weird envs
            pass

        try:
            mem = float(ps.virtual_memory().percent)
            samples.append(self._sample("memory_percent", mem, ts))
        except Exception:  # noqa: BLE001
            pass

        try:
            disk = float(ps.disk_usage(self.mountpoint).percent)
            samples.append(
                self._sample(
                    "disk_percent",
                    disk,
                    ts,
                    tags={"mountpoint": self.mountpoint},
                )
            )
        except Exception:  # noqa: BLE001 - mountpoint may not exist
            pass

        try:
            net = ps.net_io_counters()
            samples.append(self._sample("network_bytes_sent", float(net.bytes_sent), ts))
            samples.append(self._sample("network_bytes_recv", float(net.bytes_recv), ts))
        except Exception:  # noqa: BLE001
            pass

        return samples

    def _sample(
        self,
        metric: str,
        value: float,
        ts: datetime,
        *,
        tags: dict[str, str] | None = None,
    ) -> Sample:
        return Sample(
            host_name=self.host_name or "localhost",
            host_kind="linux",
            metric=metric,
            value=value,
            recorded_at=ts,
            tags=dict(tags or {}),
        )
