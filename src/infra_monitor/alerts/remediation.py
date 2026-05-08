"""Remediation runner.

Spawns a Bash script via :mod:`subprocess` with a hard timeout, captures
stdout/stderr (truncated to 4KB each — anything more is debug noise), and
persists a :class:`RemediationAction` row. Cooldown is enforced at the call
site (we won't run the same script for the same rule within
``cooldown_seconds``); the engine state machine already guarantees this for
the common case but the runner double-checks because remediations are
expensive and idempotency mistakes burn real services.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from infra_monitor.store import RemediationAction, Storage

log = logging.getLogger(__name__)

_EXCERPT_LIMIT = 4096


def _now() -> datetime:
    return datetime.now(UTC)


def _excerpt(s: str) -> str:
    if len(s) <= _EXCERPT_LIMIT:
        return s
    return s[: _EXCERPT_LIMIT - 32] + f"\n[truncated, {len(s)} bytes total]"


def cooldown_active(
    storage: Storage,
    *,
    rule_id: int,
    host_id: int,
    script_path: str,
    cooldown_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Return True if a same (rule, host, script) ran within the cooldown window."""
    cutoff = (now or _now()) - timedelta(seconds=cooldown_seconds)
    with storage.session() as s:
        stmt = (
            select(RemediationAction)
            .where(RemediationAction.rule_id == rule_id)
            .where(RemediationAction.host_id == host_id)
            .where(RemediationAction.script_path == script_path)
            .where(RemediationAction.fired_at >= cutoff)
            .limit(1)
        )
        return s.execute(stmt).first() is not None


def run(
    storage: Storage,
    *,
    rule_id: int,
    host_id: int,
    script_path: str,
    args: list[str],
    timeout_seconds: int = 60,
    cooldown_seconds: int = 300,
    runner: Any = subprocess.run,
) -> RemediationAction:
    """Execute the remediation script and persist an audit row.

    ``runner`` is injectable so tests can swap a fake subprocess in. The
    return value is the persisted :class:`RemediationAction` (post-commit)
    so callers can log the row id.
    """
    if cooldown_active(
        storage,
        rule_id=rule_id,
        host_id=host_id,
        script_path=script_path,
        cooldown_seconds=cooldown_seconds,
    ):
        action = RemediationAction(
            rule_id=rule_id,
            host_id=host_id,
            script_path=script_path,
            args_json=list(args),
            exit_code=None,
            stdout_excerpt="",
            stderr_excerpt="cooldown active; skipped",
            duration_ms=0,
        )
        with storage.session() as s:
            s.add(action)
            s.flush()
        return action

    cmd = [str(Path(script_path))] + list(args)
    log.info("running remediation: %s", " ".join(shlex.quote(c) for c in cmd))

    started = time.monotonic()
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    try:
        completed = runner(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = int(completed.returncode)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        stdout = (
            (exc.stdout or b"").decode("utf-8", "replace")
            if isinstance(exc.stdout, (bytes, bytearray))
            else (exc.stdout or "")
        )
        stderr = f"timeout after {timeout_seconds}s"
    except FileNotFoundError as exc:
        exit_code = None
        stderr = f"script not found: {exc}"

    duration_ms = int((time.monotonic() - started) * 1000)

    action = RemediationAction(
        rule_id=rule_id,
        host_id=host_id,
        script_path=script_path,
        args_json=list(args),
        exit_code=exit_code,
        stdout_excerpt=_excerpt(stdout),
        stderr_excerpt=_excerpt(stderr),
        duration_ms=duration_ms,
    )
    with storage.session() as s:
        s.add(action)
        s.flush()
    return action
