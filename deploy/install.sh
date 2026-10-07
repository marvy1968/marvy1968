#!/usr/bin/env bash
# Install Marv Predict Max on a Debian/Ubuntu Google Compute Engine VM.
# Usage: sudo bash deploy/install.sh   (from an unpacked copy), or
#        curl -fsSL https://raw.githubusercontent.com/marvy1968/marvy1968/claude/analysis-ak180w/deploy/install.sh | sudo bash
#
# No-SSH install (works from a phone): in the Cloud Console, edit the VM and add metadata
#   startup-script      = the curl line above (without sudo)
#   telegram-bot-token  = your BotFather token      (optional, filled into .env)
#   cfbd-api-key        = your CFBD key             (optional)
#   odds-api-key        = your Odds API key         (optional)
#   telegram-chat-id    = your chat id              (optional; found automatically if you messaged the bot)
# then Reset the VM. You get a Telegram "connected" message when it's done.
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

# Settings from Google Compute Engine metadata (lets you install without SSH).
meta() {
  curl -sf -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" 2>/dev/null || true
}
set_env() {  # set_env KEY VALUE: replace or add KEY=VALUE in .env
  if grep -q "^$1=" "$DIR/.env"; then
    sed -i "s|^$1=.*|$1=$2|" "$DIR/.env"
  else
    echo "$1=$2" >> "$DIR/.env"
  fi
}
for pair in TELEGRAM_BOT_TOKEN:telegram-bot-token TELEGRAM_CHAT_ID:telegram-chat-id \
            CFBD_API_KEY:cfbd-api-key ODDS_API_KEY:odds-api-key; do
  value="$(meta "${pair#*:}")"
  if [ -n "$value" ]; then
    set_env "${pair%%:*}" "$value"
  fi
done

chmod 600 "$DIR/.env"
chown -R marvbot:marvbot "$DIR"

as_bot() { runuser -u marvbot -- bash -c "cd $DIR && .venv/bin/python -m marv $*"; }
if grep -q "^TELEGRAM_BOT_TOKEN=." "$DIR/.env"; then
  if ! grep -q "^TELEGRAM_CHAT_ID=." "$DIR/.env"; then
    chat="$( (as_bot get-chat-id 2>/dev/null || true) | awk -F'\t' '/^-?[0-9]+\t/ {print $1; exit}')"
    if [ -n "$chat" ]; then
      set_env TELEGRAM_CHAT_ID "$chat"
      echo "Found Telegram chat id automatically."
    else
      echo ">>> Send your bot any message in Telegram, then run this installer again (or Reset the VM)."
    fi
  fi
  if grep -q "^TELEGRAM_CHAT_ID=." "$DIR/.env" && [ ! -f "$DIR/state/.connected" ]; then
    as_bot test-telegram && touch "$DIR/state/.connected" && chown marvbot:marvbot "$DIR/state/.connected"
  fi
fi

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
