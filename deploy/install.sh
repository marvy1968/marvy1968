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
  # The folder belongs to the marvbot user; tell git that's expected when root updates it.
  # (-c works even when root has no HOME, e.g. under systemd, where --global silently fails.)
  git config --global --add safe.directory "$DIR" 2>/dev/null || true
  # Private repo: fetch over SSH with the vm-link deploy key when it exists (vm-link.sh creates it).
  KEY=/root/.ssh/marv_link
  if [ -f "$KEY" ]; then
    export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    git -c safe.directory="$DIR" -C "$DIR" remote set-url origin git@github.com:marvy1968/marvy1968.git
  fi
  git -c safe.directory="$DIR" -C "$DIR" fetch origin "$BRANCH"
  git -c safe.directory="$DIR" -C "$DIR" checkout -f -B "$BRANCH" "origin/$BRANCH"
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
# Script O/U tracker runs as moonrider1968 and must write state/script_ou (dedupe ledger). chown -R resets the
# group to marvbot, so keep moonrider1968 in that group and the dir group-writable (2026-10-14 duplicate-alert fix).
if id moonrider1968 >/dev/null 2>&1; then
  usermod -aG marvbot moonrider1968 || true
  for d in script_ou master; do  # both trackers (script-ou, marv-master) run as moonrider1968
    mkdir -p "$DIR/state/$d" && chown marvbot:marvbot "$DIR/state/$d" && chmod 2775 "$DIR/state/$d"
    chmod g+w "$DIR"/state/$d/* 2>/dev/null || true
  done
fi

as_bot() { runuser -u marvbot -- bash -c "cd $DIR && .venv/bin/python -m marv $*"; }
if ! grep -q "^TELEGRAM_BOT_TOKEN=." "$DIR/.env"; then
  echo ">>> No Telegram token yet. Add it with:"
  echo "    sudo sed -i 's|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=YOUR_TOKEN|' $DIR/.env"
  echo "    then message your bot in Telegram and run this installer again."
else
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
    if as_bot test-telegram; then
      touch "$DIR/state/.connected" && chown marvbot:marvbot "$DIR/state/.connected"
      echo "Telegram connected: check your phone."
    else
      echo ">>> Telegram test failed: check the token (BotFather) and that you messaged the bot."
    fi
  fi
fi

# Remove the earlier college-only install if present.
if systemctl list-unit-files marv-cfb-bot.timer >/dev/null 2>&1; then
  systemctl disable --now marv-cfb-bot.timer 2>/dev/null || true
  rm -f /etc/systemd/system/marv-cfb-bot.{service,timer}
fi

cp "$DIR"/deploy/marv-*.service "$DIR"/deploy/marv-bot*.timer "$DIR"/deploy/marv-health.timer "$DIR"/deploy/marv-soccer.timer "$DIR"/deploy/marv-dailypicks.timer "$DIR"/deploy/marv-swing-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now marv-bot.timer marv-bot-late.timer marv-health.timer marv-soccer.timer marv-bridge.service marv-live.service marv-edges.service
# Owner 2026-10-17 ("stop resetting my bet bots"): the betting services restart only when betting code changed since the last install.
# A deploy that only touches the stock / forex bots (alpacabot/, fxbot/, their tests, the vm-link runner, docs) leaves them running.
# FORCE_BET_RESTART=1 restarts them anyway.
PREV="$(cat "$DIR/state/.installed_rev" 2>/dev/null || git -c safe.directory="$DIR" -C "$DIR" rev-parse -q --verify "$BRANCH@{1}" 2>/dev/null || true)"
NOW="$(git -c safe.directory="$DIR" -C "$DIR" rev-parse -q --verify HEAD 2>/dev/null || true)"
BET_CHANGED=1
if [ "${FORCE_BET_RESTART:-0}" != "1" ] && [ -n "$PREV" ] && [ -n "$NOW" ]; then
  if ! git -c safe.directory="$DIR" -C "$DIR" diff --name-only "$PREV" "$NOW" 2>/dev/null \
       | grep -qvE '^(alpacabot/|fxbot/|tests/test_(alpaca|fx|funnel|levels|trend_stairs|botreport)|deploy/vm_link\.py|deploy/marv-(alpaca|fx|swing)|[^/]*\.md$)'; then
    BET_CHANGED=0
  fi
fi
if [ "$BET_CHANGED" = "1" ]; then
  systemctl restart marv-bridge.service marv-live.service marv-edges.service 2>/dev/null || true
else
  echo "betting services left running (no betting code changed since $PREV)"
fi
[ -n "$NOW" ] && echo "$NOW" > "$DIR/state/.installed_rev" || true
# Alpaca PAPER trading loop (paper account only; idles with "WAITING FOR KEYS" until the keys are in .env)
systemctl enable marv-alpaca-paper.service 2>/dev/null && systemctl restart marv-alpaca-paper.service 2>/dev/null || true
# our own FOREX practice runner (OANDA practice host only; forex, separate from the stock bot)
systemctl enable marv-fx-paper.service 2>/dev/null && systemctl restart marv-fx-paper.service 2>/dev/null || true
# daily Sector Top-Down swing runner (paper): scan after the close, opening-auction orders before the open
systemctl enable --now marv-swing-scan.timer marv-swing-open.timer 2>/dev/null || true
systemctl list-timers "marv-bot*" --no-pager
echo "Installed. Test with: cd $DIR && sudo -u marvbot .venv/bin/python -m marv test-telegram"
