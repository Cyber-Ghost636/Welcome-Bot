#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="sts-solo-bot"
SOURCE_UNIT="/home/wh33ls/welcome-bot/scripts/${SERVICE_NAME}.service"
TARGET_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ ! -f "${SOURCE_UNIT}" ]]; then
  echo "Unit template not found: ${SOURCE_UNIT}" >&2
  exit 1
fi

echo "Installing ${SERVICE_NAME}.service to /etc/systemd/system"
sudo cp "${SOURCE_UNIT}" "${TARGET_UNIT}"

# Ensure systemd picks up the new unit.
sudo systemctl daemon-reload

# Enable at boot and start now.
sudo systemctl enable --now "${SERVICE_NAME}"

# Show a quick status summary.
sudo systemctl --no-pager --full status "${SERVICE_NAME}" | head -n 30

echo "Done. Tail logs with: sudo journalctl -u ${SERVICE_NAME} -f"
