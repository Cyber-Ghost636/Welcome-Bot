#!/usr/bin/env bash
set -euo pipefail

WELCOME_SERVICE="welcome-bot"
STS_SERVICE="sts-solo-bot"
CANONICAL_LINK="$HOME/welcome-bot"
LOG_LINES="${1:-20}"

print_header() {
  local title="$1"
  printf "\n==== %s ====\n" "$title"
}

service_status() {
  local svc="$1"
  if systemctl list-unit-files --type=service | awk '{print $1}' | grep -qx "${svc}.service"; then
    local active enabled
    active="$(systemctl is-active "$svc" 2>/dev/null || true)"
    enabled="$(systemctl is-enabled "$svc" 2>/dev/null || true)"
    printf "%s: active=%s enabled=%s\n" "$svc" "$active" "$enabled"
  else
    printf "%s: unit-not-found\n" "$svc"
  fi
}

print_recent_logs() {
  local svc="$1"
  if systemctl list-unit-files --type=service | awk '{print $1}' | grep -qx "${svc}.service"; then
    sudo journalctl -u "$svc" -n "$LOG_LINES" --no-pager || true
  else
    echo "Skipping logs: ${svc}.service not found"
  fi
}

print_header "Service States"
service_status "$WELCOME_SERVICE"
service_status "$STS_SERVICE"

print_header "Canonical Symlink"
if [[ -L "$CANONICAL_LINK" ]]; then
  echo "Link: $CANONICAL_LINK"
  echo "Target: $(readlink -f "$CANONICAL_LINK")"
elif [[ -e "$CANONICAL_LINK" ]]; then
  echo "Path exists but is not a symlink: $CANONICAL_LINK"
  echo "Resolved path: $(readlink -f "$CANONICAL_LINK")"
else
  echo "Missing path: $CANONICAL_LINK"
fi

print_header "Recent Logs (${WELCOME_SERVICE})"
print_recent_logs "$WELCOME_SERVICE"

print_header "Recent Logs (${STS_SERVICE})"
print_recent_logs "$STS_SERVICE"

print_header "Done"
echo "Tip: pass a number to change log lines, e.g. ./scripts/health_check_services.sh 50"
