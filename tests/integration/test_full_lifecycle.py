"""End-to-end: synthetic CPU stream → ARMING → FIRING → webhook to local mock
receiver → remediation script runs → audit row recorded.

Gated by ``RUN_INTEGRATION=1`` so a sandboxed unit run doesn't pay for it.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from infra_monitor.agent import Agent
from infra_monitor.alerts.threshold import Action, Rule, RuleEngine
from infra_monitor.collectors._base import Sample
from infra_monitor.dispatcher import Dispatcher
from infra_monitor.store import Storage

pytestmark = pytest.mark.integration


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Receiver(BaseHTTPRequestHandler):
    received: list[tuple[dict[str, str], bytes]] = []

    def log_message(self, *args: object, **kwargs: object) -> None:  # silence stderr
        pass

    def do_POST(self) -> None:  # noqa: N802 - http handler API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).received.append((dict(self.headers), body))
        self.send_response(200)
        self.end_headers()


class _StaticCollector:
    name = "static"

    def __init__(self, samples: list[Sample]) -> None:
        self._samples = samples

    def collect(self) -> list[Sample]:
        out, self._samples = self._samples, []
        return out


def _start_receiver(port: int) -> tuple[HTTPServer, threading.Thread]:
    _Receiver.received = []
    server = HTTPServer(("127.0.0.1", port), _Receiver)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1")
def test_full_lifecycle(tmp_path: Path) -> None:
    """A 60s synthetic stream above 90% CPU triggers webhook + remediation.

    Asserts:

    * webhook receiver got exactly one POST
    * payload signature header matches HMAC of body
    * RemediationAction row exists with exit_code 0 and stdout from the script
    * AlertEvent row exists with metric_value > 90
    """
    port = _free_port()
    server, _thread = _start_receiver(port)
    try:
        storage = Storage.in_memory()

        # Use the bundled disk_cleanup.sh as a benign remediation.
        # It does no destructive work in CI ($DAYS=999 so nothing matches).
        repo_root = Path(__file__).resolve().parents[2]
        script = repo_root / "scripts" / "disk_cleanup.sh"
        assert script.exists(), script
        os.chmod(script, 0o755)

        rule = Rule(
            name="cpu-saturation",
            metric="cpu_percent",
            condition=">90",
            duration_seconds=10,
            actions=(
                Action(
                    kind="webhook",
                    config={"url": f"http://127.0.0.1:{port}/hook"},
                    cooldown_seconds=300,
                ),
                Action(
                    kind="remediation",
                    config={"script": str(script), "args": ["999"]},
                    cooldown_seconds=300,
                ),
            ),
            severity="critical",
        )
        engine = RuleEngine(rules=[rule])
        dispatcher = Dispatcher(storage=storage, webhook_secret="integ-secret")

        # Build 60 samples spaced 1s apart, all at 95% — fires at +10s.
        base = datetime.now(UTC) - timedelta(seconds=60)
        samples = [
            Sample(
                host_name="i-test",
                host_kind="ec2",
                metric="cpu_percent",
                value=95.0,
                recorded_at=base + timedelta(seconds=i),
            )
            for i in range(60)
        ]

        agent = Agent(
            storage=storage,
            collectors=[_StaticCollector(samples)],
            engine=engine,
            dispatcher=dispatcher,
        )
        agent.tick()

        # Allow the receiver thread to deliver any pending request.
        for _ in range(50):
            if _Receiver.received:
                break
            import time

            time.sleep(0.05)

        # 1. webhook delivered exactly once
        assert len(_Receiver.received) == 1, _Receiver.received
        headers, body = _Receiver.received[0]
        sig = headers.get("X-Inframonitor-Signature") or headers.get("X-InfraMonitor-Signature")
        assert sig and sig.startswith("sha256=")

        # signature must validate against our secret
        import hashlib
        import hmac

        expected = hmac.new(b"integ-secret", body, hashlib.sha256).hexdigest()
        assert sig == f"sha256={expected}"

        payload = json.loads(body)
        assert payload["host"] == "i-test"
        assert payload["metric"] == "cpu_percent"
        assert payload["severity"] == "critical"
        assert payload["value"] == 95.0

        # 2. AlertEvent persisted
        alerts = storage.recent_alerts()
        assert len(alerts) == 1
        assert alerts[0].metric_value == 95.0

        # 3. RemediationAction persisted with exit_code 0
        rems = storage.recent_remediations()
        assert len(rems) == 1
        rem = rems[0]
        assert rem.exit_code == 0
        assert rem.script_path == str(script)
        assert rem.args_json == ["999"]
        # Script always echoes "pruning rotated logs" — verify capture.
        assert "pruning rotated logs" in rem.stdout_excerpt
    finally:
        server.shutdown()
        server.server_close()
