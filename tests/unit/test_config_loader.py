"""YAML rule loader."""

from __future__ import annotations

from typing import TYPE_CHECKING

from infra_monitor.config_loader import load_hosts, load_rules

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_load_rules(tmp_path: Path) -> None:
    cfg = tmp_path / "monitor.yaml"
    cfg.write_text(
        """
rules:
  - name: cpu
    metric: cpu_percent
    condition: ">90"
    duration_seconds: 60
    severity: critical
    actions:
      - kind: webhook
        url: http://example/hook
        cooldown_seconds: 300
      - kind: remediation
        script: scripts/restart_service.sh
        args: [nginx]
        cooldown_seconds: 600
""",
    )
    rules = load_rules(cfg)
    assert len(rules) == 1
    rule = rules[0]
    assert rule.name == "cpu"
    assert rule.severity == "critical"
    assert rule.duration_seconds == 60
    assert len(rule.actions) == 2
    assert rule.actions[0].kind == "webhook"
    assert rule.actions[1].config["script"] == "scripts/restart_service.sh"
    assert rule.actions[1].cooldown_seconds == 600


def test_load_rules_env_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_HOOK", "https://hooks.example/abc123")
    cfg = tmp_path / "monitor.yaml"
    cfg.write_text(
        """
rules:
  - name: r
    metric: cpu_percent
    condition: ">90"
    actions:
      - kind: webhook
        url: ${MY_HOOK}
""",
    )
    rules = load_rules(cfg)
    assert rules[0].actions[0].config["url"] == "https://hooks.example/abc123"


def test_load_hosts(tmp_path: Path) -> None:
    cfg = tmp_path / "m.yaml"
    cfg.write_text(
        """
hosts:
  - name: localhost
    kind: linux
  - name: i-abc
    kind: ec2
    region: us-east-1
""",
    )
    hosts = load_hosts(cfg)
    assert len(hosts) == 2
    assert hosts[0]["kind"] == "linux"
    assert hosts[1]["region"] == "us-east-1"


def test_load_rules_empty_file(tmp_path: Path) -> None:
    cfg = tmp_path / "m.yaml"
    cfg.write_text("")
    assert load_rules(cfg) == []
    assert load_hosts(cfg) == []
