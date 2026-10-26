#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-$HOME/oanda_master_alliance}"
UNIT_SOURCE="$BOT_DIR/deployment/systemd"
UNIT_DESTINATION="/etc/systemd/system"

units=(
  "oanda-master-ai.service"
  "oanda-master-ai-watchdog.service"
  "oanda-master-ai-health.service"
  "oanda-master-ai-health.timer"
  "oanda-master-ai-recovery.service"
  "oanda-master-ai-maintenance.service"
  "oanda-master-ai-maintenance.timer"
  "oanda-master-ai-backup.service"
  "oanda-master-ai-backup.timer"
  "oanda-master-ai-report.service"
  "oanda-master-ai-report.timer"
  "oanda-master-ai.target"
)

if ! command -v systemctl >/dev/null 2>&1; then
    echo "ERROR: systemctl is not available."
    exit 1
fi

echo
echo "Installing OANDA Master AI systemd units..."

for unit in "${units[@]}"; do
    if [[ ! -f "$UNIT_SOURCE/$unit" ]]; then
        echo "ERROR: Missing unit:"
        echo "  $UNIT_SOURCE/$unit"
        exit 1
    fi

    sudo install \
      -m 0644 \
      "$UNIT_SOURCE/$unit" \
      "$UNIT_DESTINATION/$unit"
done

sudo systemctl daemon-reload

echo
echo "Installed units:"
printf '  %s\n' "${units[@]}"

echo
echo "Services were not enabled automatically."
echo "Services were not started automatically."
echo
echo "Safe timer to enable later:"
echo "  sudo systemctl enable --now oanda-master-ai-health.timer"
echo
echo "Main research daemon:"
echo "  sudo systemctl start oanda-master-ai.service"
echo
echo "Automatic execution and order submission remain disabled."
