"""Collector Protocol — the load-bearing seam between data acquisition and the
rest of the system.

A collector produces a list of ``Sample`` tuples on each ``collect()`` call.
The shape is identical for the local (psutil) and cloud (CloudWatch) impls,
which is what lets the agent loop and the alert engine ignore the source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class Sample:
    host_name: str
    host_kind: str  # "linux" | "ec2"
    metric: str
    value: float
    recorded_at: datetime
    tags: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Collector(Protocol):
    name: str

    def collect(self) -> list[Sample]:  # pragma: no cover - protocol
        ...
