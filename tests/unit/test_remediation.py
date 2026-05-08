"""Remediation runner: cooldown gate, audit log captures, timeout/error paths."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from infra_monitor.alerts import remediation
from infra_monitor.store import RemediationAction, Storage


def test_run_records_audit_row(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")
    rule_id = 1

    runner = MagicMock()
    runner.return_value = subprocess.CompletedProcess(
        args=["x"], returncode=0, stdout="restarted nginx", stderr=""
    )

    action = remediation.run(
        storage,
        rule_id=rule_id,
        host_id=host.id,
        script_path="scripts/restart_service.sh",
        args=["nginx"],
        runner=runner,
    )

    assert action.exit_code == 0
    assert "restarted nginx" in action.stdout_excerpt
    rows = storage.recent_remediations()
    assert len(rows) == 1
    assert rows[0].args_json == ["nginx"]
    assert rows[0].duration_ms >= 0


def test_run_truncates_long_output(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")
    big = "x" * 10_000
    runner = MagicMock(
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=big, stderr=big),
    )
    action = remediation.run(
        storage,
        rule_id=1,
        host_id=host.id,
        script_path="s.sh",
        args=[],
        runner=runner,
    )
    assert len(action.stdout_excerpt) <= 4096
    assert "[truncated" in action.stdout_excerpt
    assert "[truncated" in action.stderr_excerpt


def test_run_records_timeout(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 60))

    action = remediation.run(
        storage,
        rule_id=1,
        host_id=host.id,
        script_path="s.sh",
        args=[],
        timeout_seconds=1,
        runner=runner,
    )
    assert action.exit_code is None
    assert "timeout" in action.stderr_excerpt.lower()


def test_run_records_missing_script(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")

    def runner(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError("/nope/nope.sh")

    action = remediation.run(
        storage,
        rule_id=1,
        host_id=host.id,
        script_path="/nope/nope.sh",
        args=[],
        runner=runner,
    )
    assert action.exit_code is None
    assert "not found" in action.stderr_excerpt


def test_cooldown_gate_skips_run(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")
    runner = MagicMock(
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    # First run: allowed
    remediation.run(
        storage,
        rule_id=1,
        host_id=host.id,
        script_path="s.sh",
        args=[],
        cooldown_seconds=300,
        runner=runner,
    )
    # Second run inside cooldown: skipped
    second = remediation.run(
        storage,
        rule_id=1,
        host_id=host.id,
        script_path="s.sh",
        args=[],
        cooldown_seconds=300,
        runner=runner,
    )
    assert "cooldown active" in second.stderr_excerpt
    assert runner.call_count == 1


def test_cooldown_active_helper(storage: Storage) -> None:
    host = storage.upsert_host(name="h1", kind="linux")
    base = datetime.now(UTC)
    with storage.session() as s:
        s.add(
            RemediationAction(
                rule_id=1,
                host_id=host.id,
                script_path="s.sh",
                args_json=[],
                exit_code=0,
                fired_at=base - timedelta(seconds=30),
            )
        )

    assert (
        remediation.cooldown_active(
            storage, rule_id=1, host_id=host.id, script_path="s.sh", cooldown_seconds=60
        )
        is True
    )
    assert (
        remediation.cooldown_active(
            storage, rule_id=1, host_id=host.id, script_path="s.sh", cooldown_seconds=10
        )
        is False
    )
