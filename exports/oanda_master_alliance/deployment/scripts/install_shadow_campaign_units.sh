#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$HOME/oanda_master_alliance/deployment/systemd"
TARGET_DIR="/etc/systemd/system"

UNITS=(
  "oanda-master-ai-shadow-campaign.service"
  "oanda-master-ai-shadow-campaign-health.service"
  "oanda-master-ai-shadow-campaign-health.timer"
  "oanda-master-ai-shadow-campaign-snapshot.service"
  "oanda-master-ai-shadow-campaign-snapshot.timer"
  "oanda-master-ai-shadow-campaign-alert.service"
  "oanda-master-ai-shadow-campaign-alert.timer"
)

echo
echo "Installing OANDA Master AI shadow-campaign units..."

for unit in "${UNITS[@]}"; do
    sudo install \
      -m 0644 \
      "$SOURCE_DIR/$unit" \
      "$TARGET_DIR/$unit"
done

sudo systemctl daemon-reload

echo
echo "Installed units:"

printf '  %s\n' "${UNITS[@]}"

echo
echo "Units were not enabled automatically."
echo "The shadow campaign was not started automatically."
echo
echo "Enable safe monitoring timers later with:"
echo "  sudo systemctl enable --now \\"
echo "    oanda-master-ai-shadow-campaign-health.timer \\"
echo "    oanda-master-ai-shadow-campaign-snapshot.timer \\"
echo "    oanda-master-ai-shadow-campaign-alert.timer"
echo
echo "Start the research-only campaign manually with:"
echo "  sudo systemctl start oanda-master-ai-shadow-campaign.service"
echo
echo "Automatic execution remains disabled."
echo "Order submission remains disabled."
