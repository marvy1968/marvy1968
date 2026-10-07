#!/usr/bin/env bash
# Install Marv CFB Predict Max on a Debian/Ubuntu Google Compute Engine VM.
# Usage: sudo bash deploy/install.sh   (from an unpacked copy), or
#        curl -fsSL https://raw.githubusercontent.com/marvy1968/marvy1968/claude/analysis-ak180w/deploy/install.sh | sudo bash
set -euo pipefail

REPO="${REPO:-https://github.com/marvy1968/marvy1968.git}"
BRANCH="${BRANCH:-claude/analysis-ak180w}"
DIR=/opt/marv-cfb-bot

apt-get update -y
apt-get install -y git python3 python3-venv

id marvbot >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin marvbot

SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd || true)"
if [ -n "$SRC" ] && [ -d "$SRC/cfb_bot" ] && [ "$SRC" != "$DIR" ]; then
  # Running from an unpacked copy (e.g. marv-cfb-bot.tar.gz): install that copy.
  mkdir -p "$DIR" && cp -r "$SRC"/. "$DIR"/
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
  echo ">>> Edit $DIR/.env and add CFBD_API_KEY, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
fi
chmod 600 "$DIR/.env"
chown -R marvbot:marvbot "$DIR"

cp "$DIR/deploy/marv-cfb-bot.service" "$DIR/deploy/marv-cfb-bot.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now marv-cfb-bot.timer
systemctl list-timers marv-cfb-bot.timer --no-pager
echo "Installed. Test with: cd $DIR && sudo -u marvbot .venv/bin/python -m cfb_bot test-telegram"
