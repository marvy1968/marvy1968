#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="$HOME/oanda_master_alliance"
SOURCE_DIR="$BOT_DIR/deployment/systemd"
TARGET_DIR="/etc/systemd/system"

UNITS=(
  oanda-master-ai-shadow-campaign.service
  oanda-master-ai-shadow-campaign-health.service
  oanda-master-ai-shadow-campaign-health.timer
  oanda-master-ai-shadow-campaign-snapshot.service
  oanda-master-ai-shadow-campaign-snapshot.timer
  oanda-master-ai-shadow-campaign-alert.service
  oanda-master-ai-shadow-campaign-alert.timer
)

TIMERS=(
  oanda-master-ai-shadow-campaign-health.timer
  oanda-master-ai-shadow-campaign-snapshot.timer
  oanda-master-ai-shadow-campaign-alert.timer
)

RUNNERS=(
  run_segment4b4.py
  run_segment4b4_health.py
  run_segment4b4_snapshot.py
  run_segment4b4_checkpoint_alerts.py
  run_segment4b4_systemd_inspection.py
)

echo
echo "OANDA Master AI — systemd deployment preflight"
echo

for runner in "${RUNNERS[@]}"; do
    if [[ ! -f "$BOT_DIR/$runner" ]]; then
        echo "ERROR: Missing runner: $BOT_DIR/$runner"
        exit 1
    fi
done

for unit in "${UNITS[@]}"; do
    if [[ ! -f "$SOURCE_DIR/$unit" ]]; then
        echo "ERROR: Missing unit source: $SOURCE_DIR/$unit"
        exit 1
    fi
done

echo "Preflight passed."
echo "Installing seven systemd unit files..."

for unit in "${UNITS[@]}"; do
    sudo install -m 0644 \
      "$SOURCE_DIR/$unit" \
      "$TARGET_DIR/$unit"
done

sudo systemctl daemon-reload

# The campaign engine is deliberately not enabled or started.
sudo systemctl disable \
  oanda-master-ai-shadow-campaign.service \
  >/dev/null 2>&1 || true

# Only monitoring and reporting timers are activated.
for timer in "${TIMERS[@]}"; do
    sudo systemctl enable --now "$timer"
done

echo
echo "Installed monitoring timers:"
sudo systemctl list-timers \
  --all \
  --no-pager \
  'oanda-master-ai-shadow-campaign-*'

echo
echo "Campaign service remains disabled:"
sudo systemctl is-enabled \
  oanda-master-ai-shadow-campaign.service \
  2>/dev/null || true

echo
echo "Safety:"
echo "  Automatic promotion: disabled"
echo "  Automatic execution: disabled"
echo "  Practice-order submission: disabled"
echo "  Live trading: blocked"
echo "  Orders submitted: 0"
