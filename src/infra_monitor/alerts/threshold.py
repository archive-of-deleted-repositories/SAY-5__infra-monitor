"""Threshold rule engine — the load-bearing piece of the alert pipeline.

States::

    OK        ──(condition becomes true)──▶ ARMING
    ARMING    ──(condition stays true ≥duration)──▶ FIRING
    ARMING    ──(condition becomes false)─▶ OK
    FIRING    ──(actions dispatched)──────▶ COOLDOWN
    COOLDOWN  ──(cooldown elapsed)────────▶ OK
    COOLDOWN  ──(condition still true)────▶ COOLDOWN  (no re-fire)

The point: a flappy metric does not produce a flood of alerts. The rule must
hold the breach for ``duration_seconds`` before firing, and after firing it
sits in ``COOLDOWN`` for the longest cooldown of any of its actions.

This module is pure (no I/O, no DB). The agent loop pumps it with
``(timestamp, value)`` tuples; whatever fires comes out as a list of
:class:`FiredAlert` records to dispatch.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class State(StrEnum):
    OK = "OK"
    ARMING = "ARMING"
    FIRING = "FIRING"
    COOLDOWN = "COOLDOWN"


_OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}

_CONDITION_RE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")


def parse_condition(expr: str) -> tuple[Callable[[float, float], bool], float]:
    """Parse ``"> 90"`` → (operator.gt, 90.0). Raises :class:`ValueError` on bad input."""
    m = _CONDITION_RE.match(expr)
    if not m:
        raise ValueError(f"invalid condition: {expr!r}")
    op_token, threshold_token = m.group(1), m.group(2)
    return _OPS[op_token], float(threshold_token)


# ---------------------------------------------------------------------------
# Declarative rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    kind: str  # "webhook" | "remediation"
    config: dict[str, Any]
    cooldown_seconds: int = 300


@dataclass(frozen=True)
class Rule:
    name: str
    metric: str
    condition: str  # ">90"
    duration_seconds: int = 0
    actions: tuple[Action, ...] = ()
    severity: str = "warning"

    def matches(self, value: float) -> bool:
        op, threshold = parse_condition(self.condition)
        return op(value, threshold)

    @property
    def cooldown(self) -> int:
        return max((a.cooldown_seconds for a in self.actions), default=300)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@dataclass
class RuleState:
    """Per-(rule, host) state. Tracked separately because one rule can apply to
    many hosts and they must arm/fire independently.
    """

    rule: Rule
    host_key: str
    state: State = State.OK
    armed_at: datetime | None = None
    fired_at: datetime | None = None

    def observe(
        self,
        value: float,
        now: datetime,
    ) -> FiredAlert | None:
        """Feed in one observation; return a fired alert iff the rule transitioned
        from ARMING → FIRING on this call.
        """

        breached = self.rule.matches(value)
        cooldown = timedelta(seconds=self.rule.cooldown)
        duration = timedelta(seconds=self.rule.duration_seconds)

        if self.state is State.OK:
            if breached:
                self.state = State.ARMING
                self.armed_at = now
                # Zero-duration rule fires on first hit.
                if duration <= timedelta(0):
                    return self._fire(value, now)
            return None

        if self.state is State.ARMING:
            if not breached:
                self.state = State.OK
                self.armed_at = None
                return None
            assert self.armed_at is not None
            if (now - self.armed_at) >= duration:
                return self._fire(value, now)
            return None

        if self.state is State.FIRING:
            # Should immediately move to COOLDOWN; defensive transition.
            self.state = State.COOLDOWN
            return None

        if self.state is State.COOLDOWN:
            assert self.fired_at is not None
            if (now - self.fired_at) >= cooldown:
                # Cooldown expired. Re-evaluate.
                self.state = State.OK
                self.armed_at = None
                self.fired_at = None
                return self.observe(value, now)
            return None

        raise RuntimeError(f"unreachable state {self.state}")

    def _fire(self, value: float, now: datetime) -> FiredAlert:
        self.state = State.COOLDOWN
        self.fired_at = now
        self.armed_at = None
        return FiredAlert(
            rule=self.rule,
            host_key=self.host_key,
            value=value,
            fired_at=now,
        )


@dataclass(frozen=True)
class FiredAlert:
    rule: Rule
    host_key: str
    value: float
    fired_at: datetime


# ---------------------------------------------------------------------------
# Engine — owns the per-(rule, host) RuleState map
# ---------------------------------------------------------------------------


@dataclass
class RuleEngine:
    rules: list[Rule]
    states: dict[tuple[str, str], RuleState] = field(default_factory=dict)

    def feed(
        self,
        *,
        host_key: str,
        metric: str,
        value: float,
        now: datetime,
    ) -> list[FiredAlert]:
        fired: list[FiredAlert] = []
        for rule in self.rules:
            if rule.metric != metric:
                continue
            key = (rule.name, host_key)
            state = self.states.get(key)
            if state is None:
                state = RuleState(rule=rule, host_key=host_key)
                self.states[key] = state
            ev = state.observe(value, now)
            if ev is not None:
                fired.append(ev)
        return fired
