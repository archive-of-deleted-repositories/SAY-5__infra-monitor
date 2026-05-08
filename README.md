# infra-monitor

Python tool that collects system metrics from Linux hosts (via a local
`psutil` agent) and AWS EC2 instances (via CloudWatch `GetMetricData`),
persists time-series to SQLite, displays trend charts on a FastAPI dashboard,
and triggers threshold alerts that dispatch HMAC-signed webhooks and run
configurable Bash remediation scripts. Every script invocation lands in an
audit log.

[![CI](https://github.com/SAY-5/infra-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/SAY-5/infra-monitor/actions/workflows/ci.yml)

## What this studies

- **Collector Protocol pattern** — two implementations (`psutil` for Linux,
  `cloudwatch` for AWS) emit the same `Sample` shape, so the alert engine and
  dashboard are source-agnostic. New cloud (GCP Monitoring, Azure Monitor) is
  one file.
- **Arming-vs-firing state machine** — `OK → ARMING → FIRING → COOLDOWN → OK`.
  The `duration_seconds` knob is what stops a flappy metric from paging
  someone forty times. Tested with `hypothesis` — random metric streams
  cannot produce more than one fire per cooldown window.
- **HMAC-signed webhooks** — `X-InfraMonitor-Signature: sha256=<hex>` on a
  body serialised with `sort_keys=True` so receivers can verify
  reproducibly. There is a pinned reference vector test in
  `tests/unit/test_webhook.py` so a refactor that changes the body
  serialisation format will fail loudly.
- **Audit-log discipline for remediation** — every script invocation persists
  `(rule_id, host_id, fired_at, script_path, args, exit_code,
  stdout_excerpt, stderr_excerpt, duration_ms)`. A remediation script
  failing silently is the worst-case outcome of an alert pipeline; this
  closes that hole.
- **Cooldown gate** — the runner double-checks that the same `(rule, host,
  script)` did not run within `cooldown_seconds` before spawning. Belt and
  braces against state machine bugs.

## How this differs from `SAY-5/health-monitor`

`health-monitor` watches HTTP endpoints and restarts a service after three
consecutive failures. Different problem. This watches **system metrics**
(CPU, memory, disk, network) from local Linux hosts and AWS, gives you a web
dashboard with trend charts, and runs threshold alert workflows with arming,
cooldown, and a remediation audit log. Different scale, different failure
modes, different correctness questions.

## Module layout

| Module                              | Responsibility                                            |
| ----------------------------------- | --------------------------------------------------------- |
| `infra_monitor.collectors._base`    | `Collector` Protocol + `Sample` dataclass                 |
| `infra_monitor.collectors.psutil`   | Local Linux collector                                     |
| `infra_monitor.collectors.cloudwatch` | AWS CloudWatch `GetMetricData`                          |
| `infra_monitor.alerts.threshold`    | `OK → ARMING → FIRING → COOLDOWN` state machine           |
| `infra_monitor.alerts.webhook`      | HMAC-signed POST with retry                               |
| `infra_monitor.alerts.remediation`  | Subprocess runner + audit log                             |
| `infra_monitor.store`               | SQLAlchemy 2 models + Storage facade                      |
| `infra_monitor.agent`               | Collector → store → engine → dispatcher loop              |
| `infra_monitor.dispatcher`          | Hands fired alerts to webhook + remediation actions       |
| `infra_monitor.api`                 | FastAPI dashboard + JSON API                              |
| `infra_monitor.cli`                 | `infra-monitor` Click entry point                         |
| `infra_monitor.config_loader`       | YAML rule loader with `${ENV}` expansion                  |

## Quickstart

```bash
make dev
make migrate seed
make test lint typecheck
# in one terminal:
make agent
# in another:
make dashboard
# open http://127.0.0.1:8080
```

Or with Docker:

```bash
docker compose up --build
```

To watch the alert flow without a real load generator:

```bash
make simulate
infra-monitor alerts
```

## Architecture

```
                  ┌──────────────────┐
                  │  collectors      │
                  │  ─ psutil        │   produces Sample(host, metric,
                  │  ─ cloudwatch    │              value, ts, tags)
                  └────────┬─────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │  agent loop      │
                  └────┬─────────┬───┘
                       │         │
              persist  │         │  feed
                       ▼         ▼
              ┌────────────┐ ┌──────────────────┐
              │  storage   │ │  RuleEngine      │
              │  (SQLite)  │ │  state machine   │
              └─────┬──────┘ └────────┬─────────┘
                    │                 │ FiredAlert
                    │                 ▼
                    │        ┌──────────────────┐
                    │        │  dispatcher      │
                    │        └─┬─────────────┬──┘
                    │          │             │
                    │   webhook│       remediation
                    │   (HMAC) │       (subprocess)
                    │          ▼             ▼
                    │   ┌───────────┐ ┌──────────────┐
                    │   │ receiver  │ │ Bash script  │
                    │   └───────────┘ └──────────────┘
                    │                       │
                    │                       ▼
                    │              audit row → storage
                    ▼
          ┌──────────────────┐
          │  FastAPI         │
          │  dashboard       │
          └──────────────────┘
```

## Dashboard screenshot

`docs/screenshot.png` is the placeholder location. To capture one yourself:

```bash
make simulate              # generates ~120 samples
make dashboard             # http://127.0.0.1:8080
# open the page, click "sim-host" in the sidebar
# use the system screenshot tool, save as docs/screenshot.png
```

## What this is *not*

- **No agent-side encryption.** Metrics are assumed to traverse a trusted
  local network. Production deploys put a TLS-terminating proxy in front of
  the dashboard.
- **No authentication on the dashboard.** Single-tenant assumption; if you
  expose this on a network you don't fully control, gate it with a reverse
  proxy that does auth.
- **No metric aggregation tier.** Samples roll off after `metric_retention_days`
  (default 30) on a `prune_default_retention()` call. Production setups hand
  this off to Prometheus + downsampling or InfluxDB CQs.
- **Not Kubernetes-native.** No DaemonSet, no service-mesh integration. See
  `SAY-5/kube-deploy` if that is the deployment target.
- **No alert deduplication across distributed monitors.** A single instance
  is the assumption; if you run two agents pointed at the same DB they will
  both arm/fire independently.
- **No incident management.** Webhook payload integrates with PagerDuty /
  Slack via their generic webhook receivers; managing the incident lifecycle
  (ack, escalate, postmortem) is upstream of this tool.
- **No anomaly detection.** Static thresholds only. Rolling-window
  baselines and z-score detection are deliberate non-goals at this scale.

## License

MIT. See `LICENSE`.
