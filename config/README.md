# Configuration

`monitor.example.yaml` is the shape; copy to `monitor.yaml` and edit.

## Schema

```yaml
hosts:
  - name: <string>            # unique host identifier
    kind: linux | ec2
    region: us-east-1         # ec2 only

rules:
  - name: <string>            # unique rule name
    metric: <string>          # one of: cpu_percent, memory_percent, disk_percent,
                              #         network_bytes_sent, network_bytes_recv
    condition: ">90"          # operator + threshold; supports >, >=, <, <=, ==, !=
    duration_seconds: 60      # condition must hold this long before firing (0 = immediate)
    severity: warning | critical
    actions:
      - kind: webhook
        url: <url>            # supports ${ENV_VAR} substitution
        cooldown_seconds: 300
      - kind: remediation
        script: scripts/<name>.sh
        args: [<arg>, ...]
        cooldown_seconds: 300
```

## Notes

- `duration_seconds` is the load-bearing knob for flap suppression. A 5-second
  CPU spike won't page anyone if `duration_seconds: 60`.
- `cooldown_seconds` prevents the same action from running repeatedly while
  the condition is still breached.
- Webhook secrets are NOT in this file. Set `INFRA_MONITOR_WEBHOOK_SECRET` in
  the environment.
