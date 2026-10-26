#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-$HOME/oanda_master_alliance}"
SOURCE="$BOT_DIR/deployment/logrotate/oanda-master-ai"
DESTINATION="/etc/logrotate.d/oanda-master-ai"

if [[ ! -f "$SOURCE" ]]; then
    echo "ERROR: Logrotate configuration not found:"
    echo "  $SOURCE"
    exit 1
fi

sudo install \
  -m 0644 \
  "$SOURCE" \
  "$DESTINATION"

sudo logrotate \
  --debug \
  "$DESTINATION"

echo
echo "OANDA Master AI logrotate configuration installed."
