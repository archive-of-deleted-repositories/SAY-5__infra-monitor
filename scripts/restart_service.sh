#!/usr/bin/env bash
# Sample remediation: restart a systemd unit.
# Wired up by the alert engine when a "remediation" action fires.
# Stub on non-systemd hosts (Mac/CI) so it's safe to run in tests.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: restart_service.sh <unit>" >&2
  exit 2
fi

UNIT="$1"

if command -v systemctl >/dev/null 2>&1; then
  echo "restarting ${UNIT} via systemctl"
  systemctl restart "${UNIT}"
  systemctl is-active "${UNIT}"
else
  echo "systemctl not present; would-have-restarted ${UNIT}"
fi
