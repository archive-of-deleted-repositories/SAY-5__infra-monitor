"""HMAC-SHA256 signed webhook delivery.

The contract:

* Body is JSON with stable key order (``sort_keys=True``) so the signature is
  reproducible at the receiver. The receiver should re-serialize what it
  receives and compare HMACs. Practically every Slack-style receiver does it
  this way; the test in ``tests/unit/test_webhook.py`` pins the exact value
  we ship.
* The signature header is ``X-InfraMonitor-Signature: sha256=<hex>``.
* 4xx is fail-fast (no retry — the receiver is rejecting the payload, not the
  network); 5xx and timeouts retry with exponential backoff up to 3 attempts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from datetime import datetime

    from infra_monitor.alerts.threshold import FiredAlert

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-InfraMonitor-Signature"


def sign_payload(body: bytes, secret: str) -> str:
    """Return the ``sha256=<hex>`` value for the given body + secret."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def serialize(event: FiredAlert) -> bytes:
    payload: dict[str, Any] = {
        "rule": event.rule.name,
        "metric": event.rule.metric,
        "host": event.host_key,
        "value": event.value,
        "severity": event.rule.severity,
        "fired_at": _isoformat(event.fired_at),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _isoformat(dt: datetime) -> str:
    # RFC3339-ish, drops microseconds for stable signatures
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class WebhookError(Exception):
    pass


class _Retryable(Exception):
    """Marker exception for things tenacity should retry."""


@dataclass
class WebhookDelivery:
    url: str
    secret: str
    timeout_seconds: float = 5.0
    max_attempts: int = 3
    client: httpx.Client | None = None

    def deliver(self, event: FiredAlert) -> bool:
        body = serialize(event)
        signature = sign_payload(body, self.secret)
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: signature,
        }

        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout_seconds)

        try:
            return self._post_with_retry(client, body, headers)
        finally:
            if owns_client:
                client.close()

    def _post_with_retry(
        self,
        client: httpx.Client,
        body: bytes,
        headers: dict[str, str],
    ) -> bool:
        # Build the retry decorator dynamically so callers can configure attempts.
        @retry(
            reraise=True,
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
            retry=retry_if_exception_type(_Retryable),
        )
        def _attempt() -> httpx.Response:
            try:
                resp = client.post(self.url, content=body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise _Retryable(str(exc)) from exc
            if 500 <= resp.status_code < 600:
                raise _Retryable(f"server error {resp.status_code}")
            return resp

        try:
            resp = _attempt()
        except _Retryable as exc:
            log.warning("webhook delivery failed after retries: %s", exc)
            return False
        except RetryError as exc:  # pragma: no cover - tenacity path
            log.warning("webhook delivery exhausted retries: %s", exc)
            return False

        if 400 <= resp.status_code < 500:
            log.warning("webhook receiver rejected payload: %s", resp.status_code)
            return False
        return True
