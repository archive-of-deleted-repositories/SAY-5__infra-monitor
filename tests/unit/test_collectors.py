"""Collector boundary cases."""

from __future__ import annotations

import datetime as _dt
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from infra_monitor.collectors._base import Collector, Sample
from infra_monitor.collectors.cloudwatch import CloudWatchCollector
from infra_monitor.collectors.psutil import PsutilCollector


def _fake_psutil(
    *,
    cpu: float = 50.0,
    mem: float = 60.0,
    disk: float = 70.0,
    net_sent: float = 1000.0,
    net_recv: float = 2000.0,
    fail_disk: bool = False,
) -> types.SimpleNamespace:
    def cpu_percent(interval: float | None = None) -> float:
        return cpu

    def virtual_memory() -> types.SimpleNamespace:
        return types.SimpleNamespace(percent=mem)

    def disk_usage(_path: str) -> types.SimpleNamespace:
        if fail_disk:
            raise OSError("no such mountpoint")
        return types.SimpleNamespace(percent=disk)

    def net_io_counters() -> types.SimpleNamespace:
        return types.SimpleNamespace(bytes_sent=net_sent, bytes_recv=net_recv)

    return types.SimpleNamespace(
        cpu_percent=cpu_percent,
        virtual_memory=virtual_memory,
        disk_usage=disk_usage,
        net_io_counters=net_io_counters,
    )


def test_psutil_collector_emits_all_metrics() -> None:
    c = PsutilCollector(host_name="t1", psutil_module=_fake_psutil())
    samples = c.collect()
    assert isinstance(c, Collector)
    metrics = sorted(s.metric for s in samples)
    assert metrics == [
        "cpu_percent",
        "disk_percent",
        "memory_percent",
        "network_bytes_recv",
        "network_bytes_sent",
    ]
    for s in samples:
        assert s.host_name == "t1"
        assert s.host_kind == "linux"
        assert isinstance(s.recorded_at, datetime)


def test_psutil_collector_missing_field_does_not_crash() -> None:
    c = PsutilCollector(host_name="t1", psutil_module=_fake_psutil(fail_disk=True))
    samples = c.collect()
    metrics = {s.metric for s in samples}
    assert "disk_percent" not in metrics
    assert "cpu_percent" in metrics


def test_psutil_collector_disk_tag_records_mountpoint() -> None:
    c = PsutilCollector(host_name="t1", psutil_module=_fake_psutil(), mountpoint="/")
    samples = c.collect()
    disk = next(s for s in samples if s.metric == "disk_percent")
    assert disk.tags == {"mountpoint": "/"}


def test_cloudwatch_empty_instance_list_returns_empty() -> None:
    c = CloudWatchCollector(instance_ids=[], client=MagicMock())
    assert c.collect() == []


def test_cloudwatch_parses_label_back_to_instance_and_metric() -> None:
    fake_ts = datetime(2026, 5, 7, 12, 0, 0, tzinfo=_dt.UTC)
    client = MagicMock()
    client.get_metric_data.return_value = {
        "MetricDataResults": [
            {
                "Label": "i-abc|cpu_percent",
                "Timestamps": [fake_ts],
                "Values": [73.5],
            },
        ],
    }
    c = CloudWatchCollector(instance_ids=["i-abc"], region="us-east-1", client=client)
    samples = c.collect()
    assert len(samples) == 1
    s = samples[0]
    assert s.host_name == "i-abc"
    assert s.host_kind == "ec2"
    assert s.metric == "cpu_percent"
    assert s.value == pytest.approx(73.5)
    assert s.tags == {"region": "us-east-1"}


def test_cloudwatch_api_failure_returns_empty() -> None:
    client = MagicMock()
    client.get_metric_data.side_effect = RuntimeError("boom")
    c = CloudWatchCollector(instance_ids=["i-xxx"], client=client)
    assert c.collect() == []


def test_sample_is_immutable() -> None:
    s = Sample(
        host_name="h",
        host_kind="linux",
        metric="cpu_percent",
        value=10.0,
        recorded_at=datetime.now(_dt.UTC),
    )
    with pytest.raises(
        (AttributeError, _dt.datetime.__class__.__bases__[0]) if False else AttributeError
    ):
        s.value = 99.0  # type: ignore[misc]
