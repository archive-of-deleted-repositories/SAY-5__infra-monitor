"""Load alert rules from YAML. Env-var substitution: ``${FOO}`` is replaced at
load time so secrets stay in ``$INFRA_MONITOR_*`` and never live in a
checked-in file.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

import yaml

from infra_monitor.alerts.threshold import Action, Rule

if TYPE_CHECKING:
    from pathlib import Path

_ENV_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load_rules(path: Path) -> list[Rule]:
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        return []
    raw = _expand(raw)
    rules_raw = raw.get("rules", [])
    rules: list[Rule] = []
    for entry in rules_raw:
        actions: list[Action] = []
        for a in entry.get("actions", []):
            kind = a["kind"]
            cooldown = int(a.get("cooldown_seconds", 300))
            actions.append(Action(kind=kind, config=a, cooldown_seconds=cooldown))
        rules.append(
            Rule(
                name=entry["name"],
                metric=entry["metric"],
                condition=entry["condition"],
                duration_seconds=int(entry.get("duration_seconds", 0)),
                actions=tuple(actions),
                severity=entry.get("severity", "warning"),
            )
        )
    return rules


def load_hosts(path: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        return []
    return list(_expand(raw.get("hosts", [])))
