"""Threshold rule engine. The state-machine invariants are the core risk."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from infra_monitor.alerts.threshold import (
    Action,
    Rule,
    RuleEngine,
    RuleState,
    State,
    parse_condition,
)

# ---- condition parsing ----------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "value", "expected"),
    [
        (">90", 91.0, True),
        (">90", 90.0, False),
        (">=90", 90.0, True),
        ("<10", 9.99, True),
        ("<=10", 10.0, True),
        ("==42", 42.0, True),
        ("!=42", 0.0, True),
    ],
)
def test_parse_condition(expr: str, value: float, expected: bool) -> None:
    op, threshold = parse_condition(expr)
    assert op(value, threshold) is expected


@pytest.mark.parametrize("expr", ["", "??90", "> nine"])
def test_parse_condition_rejects_garbage(expr: str) -> None:
    with pytest.raises(ValueError):
        parse_condition(expr)


# ---- state transitions ----------------------------------------------------


def _rule(duration: int = 0, cooldown: int = 60) -> Rule:
    return Rule(
        name="cpu",
        metric="cpu_percent",
        condition=">90",
        duration_seconds=duration,
        actions=(Action(kind="webhook", config={"url": "x"}, cooldown_seconds=cooldown),),
    )


def test_zero_duration_fires_on_first_breach(now: datetime) -> None:
    rs = RuleState(rule=_rule(duration=0), host_key="h")
    fired = rs.observe(95.0, now)
    assert fired is not None
    assert rs.state is State.COOLDOWN


def test_arming_clears_when_value_drops(now: datetime) -> None:
    rs = RuleState(rule=_rule(duration=30), host_key="h")
    assert rs.observe(95.0, now) is None
    assert rs.state is State.ARMING
    cleared = rs.observe(50.0, now + timedelta(seconds=10))
    assert cleared is None
    assert rs.state is State.OK
    assert rs.armed_at is None


def test_arming_fires_after_duration(now: datetime) -> None:
    rs = RuleState(rule=_rule(duration=30), host_key="h")
    assert rs.observe(95.0, now) is None
    assert rs.observe(95.0, now + timedelta(seconds=29)) is None
    assert rs.state is State.ARMING
    fired = rs.observe(95.0, now + timedelta(seconds=30))
    assert fired is not None
    assert rs.state is State.COOLDOWN


def test_cooldown_blocks_refire(now: datetime) -> None:
    rs = RuleState(rule=_rule(duration=0, cooldown=120), host_key="h")
    assert rs.observe(95.0, now) is not None
    # 60s later, still breached, still in cooldown
    assert rs.observe(99.0, now + timedelta(seconds=60)) is None
    assert rs.state is State.COOLDOWN


def test_cooldown_expires_then_can_fire(now: datetime) -> None:
    rs = RuleState(rule=_rule(duration=0, cooldown=60), host_key="h")
    assert rs.observe(95.0, now) is not None
    assert rs.observe(50.0, now + timedelta(seconds=70)) is None
    assert rs.state is State.OK
    fired = rs.observe(95.0, now + timedelta(seconds=80))
    assert fired is not None


def test_engine_only_routes_matching_metric(now: datetime) -> None:
    e = RuleEngine(rules=[_rule(duration=0)])
    out = e.feed(host_key="h", metric="memory_percent", value=99.0, now=now)
    assert out == []


def test_engine_per_host_state_is_independent(now: datetime) -> None:
    e = RuleEngine(rules=[_rule(duration=0)])
    a = e.feed(host_key="h1", metric="cpu_percent", value=99.0, now=now)
    b = e.feed(host_key="h2", metric="cpu_percent", value=99.0, now=now)
    assert len(a) == 1
    assert len(b) == 1


# ---- hypothesis: at-most-one fire per cooldown ----------------------------


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    values=st.lists(
        st.floats(min_value=0.0, max_value=200.0, allow_nan=False), min_size=1, max_size=200
    ),
    duration=st.integers(min_value=0, max_value=30),
    cooldown=st.integers(min_value=10, max_value=200),
)
def test_at_most_one_fire_per_cooldown(values: list[float], duration: int, cooldown: int) -> None:
    rule = Rule(
        name="cpu",
        metric="cpu_percent",
        condition=">90",
        duration_seconds=duration,
        actions=(Action(kind="webhook", config={"url": "x"}, cooldown_seconds=cooldown),),
    )
    rs = RuleState(rule=rule, host_key="h")
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    fires: list[datetime] = []
    for i, v in enumerate(values):
        ev = rs.observe(v, base + timedelta(seconds=i))
        if ev is not None:
            fires.append(ev.fired_at)

    # Adjacent fires must be ≥ cooldown apart.
    for a, b in zip(fires, fires[1:], strict=False):
        assert (b - a).total_seconds() >= cooldown, (a, b, fires)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    values=st.lists(
        st.floats(min_value=0.0, max_value=80.0, allow_nan=False), min_size=1, max_size=200
    ),
)
def test_never_fires_below_threshold(values: list[float]) -> None:
    rule = Rule(name="cpu", metric="cpu_percent", condition=">90", duration_seconds=0)
    rs = RuleState(rule=rule, host_key="h")
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    fires = 0
    for i, v in enumerate(values):
        if rs.observe(v, base + timedelta(seconds=i)) is not None:
            fires += 1
    assert fires == 0
    assert rs.state is State.OK
