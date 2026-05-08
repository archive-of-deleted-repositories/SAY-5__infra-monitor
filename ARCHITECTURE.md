# Architecture

## Collector Protocol

A `Collector` produces a list of `Sample` records on each `collect()` call.
Two implementations live in `src/infra_monitor/collectors/`:

| Impl                  | Source                                | Hosts produced                        |
| --------------------- | ------------------------------------- | ------------------------------------- |
| `PsutilCollector`     | `psutil.cpu_percent` etc.             | `host_kind="linux"`, `host_name=$HOSTNAME` |
| `CloudWatchCollector` | `boto3.cloudwatch.get_metric_data`    | `host_kind="ec2"`, `host_name=<instance-id>` |

The `Sample` shape is `(host_name, host_kind, metric, value, recorded_at,
tags)`. Everything downstream — the agent loop, the rule engine, the
dispatcher, the dashboard — consumes only `Sample`s. Adding a third
collector (GCP Monitoring, Azure Monitor) is one new file.

The shared boundary is `Collector(Protocol)` in
`infra_monitor.collectors._base`. We keep it a runtime-checkable Protocol
so that test doubles don't need to inherit a base class.

## State machine

Per-`(rule, host)` instance, encoded in `RuleState.observe(value, now)`:

```
                       breached
              ┌──────────────────────────┐
              │                          ▼
        ┌────────┐  duration met   ┌──────────┐
        │   OK   │ ───────────────▶│  FIRING  │
        └────────┘                 └────┬─────┘
              ▲                         │ dispatch actions
   not breached│                        ▼
              │                    ┌──────────┐
              │  cooldown elapsed  │ COOLDOWN │
              └────────────────────┤          │
                                   └──────────┘
                       not breached, but
                       still inside cooldown
                          → stays COOLDOWN

         (intermediate ARMING state held while
         condition is true but duration not yet met)
```

Properties tested with `hypothesis`:

- starting from `OK` and feeding observations that never breach, the
  state stays `OK`
- between any two `FIRING` transitions for the same `(rule, host)`, at
  least `cooldown` time passes
- if the condition holds for `>= duration`, the rule fires exactly once
  before entering `COOLDOWN`

## HMAC contract

Webhook delivery is in `infra_monitor.alerts.webhook`. The body is JSON
with `sort_keys=True, separators=(",",":")` so the signature is
reproducible:

```json
{"fired_at":"2026-05-07T21:30:00Z","host":"sim-host","metric":"cpu_percent","rule":"sim-cpu","severity":"warning","value":97.5}
```

Header: `X-InfraMonitor-Signature: sha256=<hex>`.

Receiver validates by computing
`hmac.new(secret, raw_body, hashlib.sha256).hexdigest()` and comparing.
The test `tests/unit/test_webhook.py::test_signature_reference_vector`
pins a reference body + secret + signature so a refactor that changes the
serialiser fails loudly.

## Remediation audit log

`RemediationAction` row per script invocation:

| column            | meaning                                           |
| ----------------- | ------------------------------------------------- |
| `rule_id`         | the rule that triggered this run                  |
| `host_id`         | the host the rule fired on                        |
| `fired_at`        | when the script was started (UTC)                 |
| `script_path`     | absolute or relative path                         |
| `args_json`       | the args list                                     |
| `exit_code`       | int, or NULL on timeout / FileNotFoundError       |
| `stdout_excerpt`  | up to 4096 bytes; truncated marker if longer      |
| `stderr_excerpt`  | up to 4096 bytes; truncated marker if longer      |
| `duration_ms`     | wall-clock                                        |

The runner enforces a hard `timeout_seconds` (default 60) and a
`cooldown_seconds` gate at the call site — same `(rule, host, script)`
within the window writes an audit row with `stderr="cooldown active;
skipped"` and `exit_code=NULL` instead of running the script.

## What's deliberately not here

- No agent-side encryption (single-trusted-network assumption)
- No dashboard auth (single-tenant)
- No aggregation tier — raw samples only, prune by retention days
- No K8s native (see `SAY-5/kube-deploy`)
- No cross-instance alert dedup
- No incident management (webhook is the integration point)
- No anomaly detection beyond static thresholds
