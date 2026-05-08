"""Collector implementations sharing a single Protocol so the rest of the
pipeline (alerts, dashboard, storage) is collector-agnostic.
"""

from infra_monitor.collectors._base import Collector, Sample

__all__ = ["Collector", "Sample"]
