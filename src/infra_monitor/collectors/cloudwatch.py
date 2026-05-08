"""AWS CloudWatch collector. Uses :func:`boto3.client('cloudwatch').get_metric_data`
because it batches up to 500 series in one call (cheaper than ``get_metric_statistics``
per metric). In CI we point boto3 at ``moto`` — no real AWS account needed.

The same ``Sample`` shape as ``PsutilCollector`` so the alert engine and
dashboard treat both collectors identically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3

from infra_monitor.collectors._base import Sample

# The metrics we pull. Map our internal name to (CloudWatch namespace, metric, stat).
DEFAULT_METRICS: dict[str, tuple[str, str, str]] = {
    "cpu_percent": ("AWS/EC2", "CPUUtilization", "Average"),
    "network_bytes_sent": ("AWS/EC2", "NetworkOut", "Sum"),
    "network_bytes_recv": ("AWS/EC2", "NetworkIn", "Sum"),
}


@dataclass
class CloudWatchCollector:
    instance_ids: list[str]
    region: str = "us-east-1"
    name: str = "cloudwatch"
    period_seconds: int = 60
    lookback_seconds: int = 300
    metrics: dict[str, tuple[str, str, str]] = field(default_factory=lambda: dict(DEFAULT_METRICS))
    client: Any = None  # boto3 cloudwatch client; injectable for tests

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = boto3.client("cloudwatch", region_name=self.region)
        assert self.client is not None  # for mypy

    def collect(self) -> list[Sample]:
        if not self.instance_ids:
            return []

        end = datetime.now(UTC)
        start = end - timedelta(seconds=self.lookback_seconds)

        queries: list[dict[str, Any]] = []
        # Build one MetricDataQuery per (instance, internal-metric).
        for i, instance_id in enumerate(self.instance_ids):
            for j, (internal, (ns, cw_metric, stat)) in enumerate(self.metrics.items()):
                queries.append(
                    {
                        "Id": f"q{i}_{j}",
                        "Label": f"{instance_id}|{internal}",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": ns,
                                "MetricName": cw_metric,
                                "Dimensions": [
                                    {"Name": "InstanceId", "Value": instance_id},
                                ],
                            },
                            "Period": self.period_seconds,
                            "Stat": stat,
                        },
                        "ReturnData": True,
                    }
                )

        try:
            resp = self.client.get_metric_data(
                MetricDataQueries=queries,
                StartTime=start,
                EndTime=end,
                ScanBy="TimestampDescending",
            )
        except Exception:  # noqa: BLE001 - upstream/network glitch shouldn't kill agent
            return []

        out: list[Sample] = []
        for series in resp.get("MetricDataResults", []):
            label = series.get("Label", "")
            if "|" not in label:
                continue
            instance_id, internal = label.split("|", 1)
            timestamps = series.get("Timestamps", [])
            values = series.get("Values", [])
            for ts, value in zip(timestamps, values, strict=False):
                out.append(
                    Sample(
                        host_name=instance_id,
                        host_kind="ec2",
                        metric=internal,
                        value=float(value),
                        recorded_at=ts if isinstance(ts, datetime) else datetime.now(UTC),
                        tags={"region": self.region},
                    )
                )

        return out
