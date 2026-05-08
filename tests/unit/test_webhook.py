"""Webhook delivery: signature reference vector, retry behaviour."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from infra_monitor.alerts.threshold import Action, FiredAlert, Rule
from infra_monitor.alerts.webhook import (
    SIGNATURE_HEADER,
    WebhookDelivery,
    serialize,
    sign_payload,
)

if TYPE_CHECKING:
    import pytest


def _event() -> FiredAlert:
    rule = Rule(
        name="cpu-saturation",
        metric="cpu_percent",
        condition=">90",
        duration_seconds=60,
        actions=(Action(kind="webhook", config={}, cooldown_seconds=300),),
        severity="critical",
    )
    return FiredAlert(
        rule=rule,
        host_key="i-abc",
        value=97.5,
        fired_at=datetime(2026, 5, 7, 21, 30, 0, tzinfo=UTC),
    )


def test_serialize_is_stable_sorted_compact() -> None:
    body = serialize(_event())
    assert body == (
        b'{"fired_at":"2026-05-07T21:30:00Z","host":"i-abc","metric":"cpu_percent",'
        b'"rule":"cpu-saturation","severity":"critical","value":97.5}'
    )
    # Must be valid JSON.
    decoded = json.loads(body)
    assert decoded["host"] == "i-abc"


def test_signature_reference_vector() -> None:
    """Pinned reference: (body, secret) → signature. If this drifts, receivers
    that hard-coded our HMAC will break — fail loudly."""
    body = serialize(_event())
    secret = "test-secret-do-not-use"
    sig = sign_payload(body, secret)

    expected_hex = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected_hex}"
    # And the actual pinned value (computed once, locked in):
    assert sig == "sha256=c1bf1e013201a222f2d30fdd272f2505c9de38d6e6030a4d317d9a44b656f04a"


def test_deliver_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    d = WebhookDelivery(url="http://r.example.com/hook", secret="s", client=client)
    assert d.deliver(_event()) is True
    headers = captured["headers"]
    assert isinstance(headers, dict)
    sig = headers[SIGNATURE_HEADER.lower()]
    assert isinstance(sig, str)
    assert sig.startswith("sha256=")
    body = captured["body"]
    assert isinstance(body, (bytes, bytearray))
    expected = hmac.new(b"s", bytes(body), hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


def test_deliver_4xx_no_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad payload")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    d = WebhookDelivery(url="http://r/hook", secret="s", client=client, max_attempts=3)
    assert d.deliver(_event()) is False
    assert calls["n"] == 1  # 4xx fails fast, no retry


def test_deliver_5xx_retries_up_to_max_attempts() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="upstream down")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    d = WebhookDelivery(url="http://r/hook", secret="s", client=client, max_attempts=3)
    assert d.deliver(_event()) is False
    assert calls["n"] == 3


def test_deliver_recovers_on_second_attempt() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    d = WebhookDelivery(url="http://r/hook", secret="s", client=client, max_attempts=3)
    assert d.deliver(_event()) is True
    assert calls["n"] == 2
