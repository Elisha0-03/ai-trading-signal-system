#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/ai-trading-signal-system"
APP_USER="ubuntu"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi
if ! id "${APP_USER}" >/dev/null 2>&1; then
  echo "Expected service user '${APP_USER}' was not found. Edit APP_USER in this script first." >&2
  exit 1
fi

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}"
# Do not copy local credentials, virtual environments, or generated runtime data.
tar -C "${SOURCE_DIR}" \
  --exclude='./.env' --exclude='./.venv' --exclude='./venv' \
  --exclude='./logs/*' --exclude='./screenshots/*' \
  -cf - . | tar -C "${APP_DIR}" -xf -
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
if [[ ! -f "${APP_DIR}/.env" ]]; then
  install -o "${APP_USER}" -g "${APP_USER}" -m 0600 "${APP_DIR}/.env.example" "${APP_DIR}/.env"
fi
install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}/logs" "${APP_DIR}/screenshots"
sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
install -m 0644 "${SOURCE_DIR}/deployment/trading_signals.service" /etc/systemd/system/trading_signals.service
systemctl daemon-reload

echo "Prepared ${APP_DIR}. Install Chrome, edit ${APP_DIR}/.env, then enable with:"
echo "  sudo systemctl enable --now trading_signals"
echo "The service was not enabled or started by this script."
