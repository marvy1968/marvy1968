#!/usr/bin/env bash
# Install Marv Predict Max on a Debian/Ubuntu Google Compute Engine VM.
# Usage: sudo bash deploy/install.sh   (from an unpacked copy), or
#        curl -fsSL https://raw.githubusercontent.com/marvy1968/marvy1968/claude/analysis-ak180w/deploy/install.sh | sudo bash
set -euo pipefail

REPO="${REPO:-https://github.com/marvy1968/marvy1968.git}"
BRANCH="${BRANCH:-claude/analysis-ak180w}"
DIR=/opt/marv-bot

apt-get update -y
apt-get install -y git python3 python3-venv tzdata

id marvbot >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin marvbot

SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd || true)"
if [ -n "$SRC" ] && [ -d "$SRC/marv" ] && [ "$SRC" != "$DIR" ]; then
  # Running from an unpacked copy (e.g. marv-bot.tar.gz): install that copy, keeping .env and state.
  mkdir -p "$DIR"
  cp -r "$SRC"/. "$DIR"/
elif [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$BRANCH" && git -C "$DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO" "$DIR"
fi

python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade pip
"$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"

if [ ! -f "$DIR/.env" ]; then
  cp "$DIR/.env.example" "$DIR/.env"
  echo ">>> Edit $DIR/.env and add TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (and CFBD_API_KEY for college football)"
fi
mkdir -p "$DIR/state"
chmod 600 "$DIR/.env"
chown -R marvbot:marvbot "$DIR"

# Remove the earlier college-only install if present.
if systemctl list-unit-files marv-cfb-bot.timer >/dev/null 2>&1; then
  systemctl disable --now marv-cfb-bot.timer 2>/dev/null || true
  rm -f /etc/systemd/system/marv-cfb-bot.{service,timer}
fi

cp "$DIR"/deploy/marv-*.service "$DIR"/deploy/marv-bot*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now marv-bot.timer marv-bot-late.timer marv-bridge.service marv-live.service
systemctl list-timers "marv-bot*" --no-pager
echo "Installed. Test with: cd $DIR && sudo -u marvbot .venv/bin/python -m marv test-telegram"
