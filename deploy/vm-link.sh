#!/usr/bin/env bash
# Let Claude run Marv jobs on this VM through GitHub (Claude can reach GitHub but not your VM).
#
# Run once, in an SSH window:
#   curl -fsSL https://raw.githubusercontent.com/marvy1968/marvy1968/claude/analysis-ak180w/deploy/vm-link.sh -o vm-link.sh && sudo bash vm-link.sh
#
# What it sets up:
#   - updates the bot (deploy/install.sh)
#   - an SSH "deploy key" that can push to github.com/marvy1968/marvy1968 only (you add it on GitHub once)
#   - a `vm-link` branch with jobs/ and results/ folders
#   - marv-vmlink.timer: every minute, runs new jobs Claude pushed and pushes the output back
# Only allowlisted commands run (tests, update, status, logs, show, `marv ...` without --send/--watch, run
# only with --dry-run), never through a shell, and .env secret values are blanked out of every result.
# NOTE: the repository is public, so results are public too. Make it private on GitHub if you'd rather.
#
# Stop it any time:   sudo systemctl disable --now marv-vmlink.timer
# Remove the access:  delete the "marv-vm-link" deploy key on GitHub (Settings -> Deploy keys)
set -euo pipefail
[ "$(id -u)" = "0" ] || { echo "Run with sudo: sudo bash vm-link.sh" >&2; exit 1; }

REPO_SSH="git@github.com:marvy1968/marvy1968.git"
BOT_BRANCH="claude/analysis-ak180w"
LINK=/opt/marv-link
KEY=/root/.ssh/marv_link
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

echo "== 1/4 Updating the bot =="
curl -fsSL "https://raw.githubusercontent.com/marvy1968/marvy1968/$BOT_BRANCH/deploy/install.sh" | bash >/tmp/marv-install.log 2>&1 \
  || { echo "install.sh failed, see /tmp/marv-install.log"; exit 1; }
[ -f /opt/marv-bot/deploy/vm_link.py ] || { echo "vm_link.py missing after update"; exit 1; }

echo "== 2/4 GitHub deploy key =="
mkdir -p /root/.ssh && chmod 700 /root/.ssh
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "marv-vm-link" -f "$KEY"
until git ls-remote "$REPO_SSH" >/dev/null 2>&1 && git ls-remote "$REPO_SSH" | grep -q .; do
  cat <<EOF

Add this key on GitHub (one time):
  1. Open https://github.com/marvy1968/marvy1968/settings/keys/new
  2. Title: marv-vm-link
  3. Key (copy the whole line):

$(cat "$KEY.pub")

  4. Tick "Allow write access", then "Add key".
EOF
  read -r -p "Press Enter once it's added... " _ </dev/tty
done
echo "Deploy key works."

echo "== 3/4 vm-link branch =="
if [ ! -d "$LINK/.git" ]; then
  if git ls-remote --exit-code --heads "$REPO_SSH" vm-link >/dev/null 2>&1; then
    git clone -q --branch vm-link --single-branch "$REPO_SSH" "$LINK"
  else
    mkdir -p "$LINK/jobs" "$LINK/results"
    git -C "$LINK" init -q
    git -C "$LINK" checkout -q -b vm-link
    git -C "$LINK" remote add origin "$REPO_SSH"
    cat > "$LINK/README.md" <<'EOF'
# vm-link

Jobs for the Marv VM. Add `jobs/<name>.job` with one command line; the VM writes `results/<name>.txt`.
Commands: `tests`, `update`, `status`, `logs <unit> [lines]`, `show <path under state/>`,
`marv <command> [args]` (run needs --dry-run; --send/--watch refused). See deploy/vm_link.py on the bot branch.
EOF
    touch "$LINK/jobs/.keep" "$LINK/results/.keep"
    git -C "$LINK" add -A
    git -C "$LINK" -c user.name=marv-vm -c user.email=marv-vm@localhost commit -q -m "vm-link: start"
    git -C "$LINK" push -q -u origin vm-link
  fi
fi

echo "== 4/4 Service =="
cat > /etc/systemd/system/marv-vmlink.service <<EOF
[Unit]
Description=Marv VM link: run jobs from the vm-link branch
After=network-online.target

[Service]
Type=oneshot
Environment=GIT_SSH_COMMAND=$GIT_SSH_COMMAND
ExecStart=/usr/bin/python3 /opt/marv-bot/deploy/vm_link.py
EOF
cat > /etc/systemd/system/marv-vmlink.timer <<'EOF'
[Unit]
Description=Check the vm-link branch for Marv jobs every minute

[Timer]
OnBootSec=1min
OnUnitInactiveSec=1min

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now marv-vmlink.timer
mkdir -p "$LINK/jobs"
STAMP="$(date +%Y%m%d-%H%M%S)"
echo "status" > "$LINK/jobs/$STAMP-hello.job"
git -C "$LINK" add -A && git -C "$LINK" -c user.name=marv-vm -c user.email=marv-vm@localhost commit -q -m "vm-link: hello job" \
  && git -C "$LINK" push -q origin vm-link || true
systemctl start marv-vmlink.service || true
echo
echo "Done. The VM now checks the vm-link branch every minute."
echo "Tell Claude: \"the VM link is set up\". Last result:"
ls -t "$LINK/results" | head -3
