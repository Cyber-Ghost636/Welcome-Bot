#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="welcome-bot"
TARGET_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
RUN_USER="$(id -un)"
CANONICAL_REPO="/home/${RUN_USER}/welcome-bot"
PYTHON_BIN="${CANONICAL_REPO}/.venv/bin/python"

# systemd ExecStart parsing is strict; use a symlink path without spaces.
ln -sfn "${REPO_ROOT}" "${CANONICAL_REPO}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  echo "Expected virtual environment at: ${CANONICAL_REPO}/.venv" >&2
  exit 1
fi

echo "Installing ${SERVICE_NAME}.service to /etc/systemd/system"
sudo tee "${TARGET_UNIT}" >/dev/null <<EOF
[Unit]
Description=Destiny 2 Welcome Bot Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${CANONICAL_REPO}
ExecStart=${PYTHON_BIN} ${CANONICAL_REPO}/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${SERVICE_NAME}" | head -n 30

echo "Done. Tail logs with: sudo journalctl -u ${SERVICE_NAME} -f"