#!/usr/bin/env bash
# Sample remediation: prune /var/log/*.log.gz older than $1 days (default 14)
# and /tmp files older than 7 days. Conservative defaults — read this before
# pointing it at production.
set -euo pipefail

DAYS="${1:-14}"

echo "pruning rotated logs older than ${DAYS} days"
find /var/log -type f -name "*.log.gz" -mtime "+${DAYS}" -print -delete 2>/dev/null || true

echo "pruning /tmp files older than 7 days"
find /tmp -mindepth 1 -mtime +7 -print -delete 2>/dev/null || true

df -h / 2>/dev/null || true
