#!/usr/bin/env bash
# Keep a Claude Code "remote control" session linked to this VM, so Claude can run and fix Marv here
# while you watch and approve from the Claude app.
#
# Run as your normal user (not root), from an SSH window:
#   curl -fsSL https://raw.githubusercontent.com/marvy1968/marvy1968/claude/analysis-ak180w/deploy/claude-link.sh -o claude-link.sh && bash claude-link.sh
#
# First run: it installs tmux and Claude Code, then opens the link inside a tmux session called "marv".
# Sign in when Claude asks (open the link it shows on your phone). After that:
#   - closing the SSH window keeps the link running
#   - after a VM reboot it restarts on its own (cron @reboot)
#   - to look at it again:   tmux attach -t marv      (detach: press Ctrl+B, then D)
#   - to stop it:            tmux kill-session -t marv
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
  echo "Run this as your normal user (without sudo); it asks for sudo itself when needed." >&2
  exit 1
fi

DIR=/opt/marv-bot
CLAUDE="$HOME/.local/bin/claude"

if ! command -v tmux >/dev/null 2>&1; then
  echo "Installing tmux..."
  sudo apt-get update -y >/dev/null && sudo apt-get install -y tmux >/dev/null
fi

if [ ! -x "$CLAUDE" ] && ! command -v claude >/dev/null 2>&1; then
  echo "Installing Claude Code..."
  curl -fsSL https://claude.ai/install.sh | bash
fi
[ -x "$CLAUDE" ] || CLAUDE="$(command -v claude)"

# Let this user read the bot folder (the bot itself runs as marvbot).
if [ -d "$DIR" ] && ! [ -r "$DIR/CLAUDE.md" ]; then
  sudo setfacl -R -m "u:$USER:rX" "$DIR" 2>/dev/null || sudo chmod -R o+rX "$DIR"
fi
WORK="$DIR"; [ -d "$WORK" ] || WORK="$HOME"

# Restart the link after a reboot.
LINE="@reboot cd $WORK && tmux new-session -d -s marv '$CLAUDE remote-control'"
( crontab -l 2>/dev/null | grep -v "remote-control" ; echo "$LINE" ) | crontab -

if tmux has-session -t marv 2>/dev/null; then
  echo "The link is already running. Opening it (detach with Ctrl+B then D)..."
else
  tmux new-session -d -s marv -c "$WORK" "$CLAUDE remote-control"
  echo "Started the link in tmux session 'marv'. Opening it so you can sign in (detach with Ctrl+B then D)..."
fi
sleep 1
exec tmux attach -t marv
