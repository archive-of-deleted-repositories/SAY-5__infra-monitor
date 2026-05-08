# Sample remediation scripts

Drop your own scripts in this directory and reference them from
`config/monitor.example.yaml` under an `actions: kind: remediation` block.

Contract:

- script must be executable (`chmod +x`)
- exit code `0` = success; non-zero is recorded in the audit log but does not
  re-fire the alert
- stdout/stderr are captured and truncated to 4 KB each
- the runner enforces a hard timeout (`remediation_timeout_seconds`, default 60s)

## Provided

| script                 | purpose                                                |
| ---------------------- | ------------------------------------------------------ |
| `restart_service.sh`   | Restart a systemd unit. Stub on non-systemd hosts.     |
| `disk_cleanup.sh`      | Prune rotated logs + old `/tmp` files; conservative.   |

## Audit log

Every invocation is persisted to the `remediation_actions` table with
`(rule_id, host_id, fired_at, script_path, args, exit_code, stdout_excerpt,
stderr_excerpt, duration_ms)`. Inspect via the dashboard or
`infra-monitor alerts` (planned: a dedicated `remediations` subcommand).
