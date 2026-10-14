#!/usr/bin/env bash
# Find the odds-alert bot on this VM and package it for review with secrets removed.
#   sudo bash find_odds_bot.sh            -> lists where bots are and how they run
#   sudo bash find_odds_bot.sh /path/dir  -> writes ~/odds-bot-redacted.tar.gz (no keys/tokens)
set -uo pipefail

if [ "${1:-}" = "--reports" ]; then
  # Results only: tail of each log, head of markdown/text reports, head+tail of csv results. Secrets are scrubbed on the way out.
  DIR="$(realpath "${2:?folder}")"
  redact() { sed -E \
    -e 's/[0-9]{8,10}:[A-Za-z0-9_-]{30,}/REDACTED_TELEGRAM_TOKEN/g' \
    -e 's/\b(PK|AK|CK)[A-Z0-9]{16,}\b/REDACTED_BROKER_KEY/g' \
    -e 's/((API|api|APCA|apca|ALPACA|alpaca)[-_A-Za-z]*(KEY|key|SECRET|secret|TOKEN|token)[-_A-Za-z]*["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"']?)[A-Za-z0-9\/+_-]{8,}/\1REDACTED/g' \
    -e 's/\b[A-Za-z0-9\/+]{40}\b/REDACTED40/g'; }
  find "$DIR" -type f \( -name '*.log' -o -name '*.md' -o -name '*.txt' -o -name '*.csv' -o -name '*report*.json' -o -name '*result*.json' \) \
       -not -path '*/venv/*' -not -path '*/.venv/*' -not -path '*/node_modules/*' -not -path '*/archive/*' -not -name 'requirements*.txt' \
       -size +0 -mtime -400 2>/dev/null | sort | head -80 | while read -r f; do
    echo; echo "===== $f ($(wc -l < "$f") lines, $(stat -c %y "$f" | cut -c1-10))"
    case "$f" in
      *.log) tail -n 45 "$f" | cut -c1-220 | redact ;;
      *.csv) head -n 4 "$f" | cut -c1-220 | redact; echo "..."; tail -n 3 "$f" | cut -c1-220 | redact ;;
      *) head -n 70 "$f" | cut -c1-220 | redact ;;
    esac
  done
  exit 0
fi

if [ "${1:-}" = "--trading" ]; then
  echo "== Python/JS/shell files mentioning Alpaca or other brokers (paths only, no code)"
  grep -rlIis --include='*.py' --include='*.js' --include='*.ts' --include='*.sh' --include='*.ipynb' \
       -e 'alpaca' -e 'APCA-API' -e 'APCA_API' -e 'alpaca_trade_api' -e 'alpaca-py' -e 'ib_insync' \
       /home /opt /root /srv 2>/dev/null \
    | grep -v -e '/opt/marv-bot' -e '/opt/marv-link' -e 'site-packages' -e '/venv/' -e '/.venv/' -e '/node_modules/' | head -40
  echo
  echo "== Folders (file count) containing them"
  grep -rlIis --include='*.py' -e 'alpaca' -e 'APCA' /home /opt /root /srv 2>/dev/null \
    | grep -v -e '/opt/marv-bot' -e '/opt/marv-link' -e 'site-packages' -e '/venv/' -e '/.venv/' | xargs -r -n1 dirname | sort | uniq -c | sort -rn | head -10
  echo
  echo "== systemd services / cron / processes that look like trading bots"
  systemctl list-units --type=service --all --no-pager 2>/dev/null | grep -iE 'alpaca|trade|trader|scalp|swing|algo|stock' | grep -v marv
  (crontab -l 2>/dev/null; for u in $(cut -d: -f1 /etc/passwd); do crontab -u "$u" -l 2>/dev/null; done) | grep -v '^#' | grep -iE 'alpaca|trade|scalp|swing|algo' | sort -u
  ps -eo pid,user,args | grep -iE '[a]lpaca|[t]rader|[s]calp' | grep -v marv
  echo
  echo "Next: read the folder with the read-odds-bot job (secrets are stripped)."
  exit 0
fi

if [ $# -eq 0 ]; then
  echo "== Python projects that talk to Telegram (excluding Marv and virtualenvs)"
  grep -rlIs --include='*.py' -e 'api.telegram.org' -e 'telegram' -e 'TELEGRAM' \
       /home /opt /root /srv /var/www 2>/dev/null \
    | grep -v -e '/opt/marv-bot' -e '/venv/' -e '/.venv/' -e 'site-packages' -e '/env/' \
    | xargs -r -n1 dirname | sort | uniq -c | sort -rn | head -20
  echo
  echo "== Of those, files mentioning odds/lines/sportsbooks"
  grep -rlIs --include='*.py' -i -e 'odds' -e 'bovada' -e 'draftkings' -e 'fanduel' -e 'the-odds-api' \
       /home /opt /root /srv 2>/dev/null \
    | grep -v -e '/opt/marv-bot' -e 'site-packages' -e '/venv/' -e '/.venv/' | head -20
  echo
  echo "== systemd services that look like bots"
  systemctl list-units --type=service --all --no-pager 2>/dev/null | grep -iE 'bot|edge|odds|bet|march' | grep -v marv-bot
  for u in $(systemctl list-units --type=service --all --no-pager 2>/dev/null | grep -ioE '[a-z0-9_.@-]*(edge|odds|bet|march)[a-z0-9_.@-]*\.service'); do
    echo "-- $u"; systemctl cat "$u" 2>/dev/null | grep -E 'ExecStart|WorkingDirectory'
  done
  echo
  echo "== cron jobs"
  (crontab -l 2>/dev/null; for u in $(cut -d: -f1 /etc/passwd); do crontab -u "$u" -l 2>/dev/null; done) | grep -v '^#' | grep -iE 'py|bot' | sort -u
  echo
  echo "== running python processes"
  ps -eo pid,user,args | grep -i '[p]ython' | grep -v marv
  echo
  echo "Next: sudo bash $0 /path/to/the/odds/bot/folder"
  exit 0
fi

SRC="$(realpath "$1")"
OUT="$HOME/odds-bot-redacted.tar.gz"
WORK="$(mktemp -d)"
mkdir -p "$WORK/odds-bot"
# Copy code only: no env files, keys, databases, virtualenvs or caches.
tar -C "$SRC" -cf - \
    --exclude='.env' --exclude='.env.*' --exclude='*.pem' --exclude='*.key' --exclude='*.sqlite*' --exclude='*.db' \
    --exclude='venv' --exclude='.venv' --exclude='env' --exclude='__pycache__' --exclude='.git' --exclude='node_modules' \
    --exclude='*.log' . | tar -C "$WORK/odds-bot" -xf -

# Redact anything that looks like a secret inside the copied text files.
find "$WORK/odds-bot" -type f -size -2M | while read -r f; do
  grep -Iq . "$f" 2>/dev/null || continue
  sed -i -E \
    -e 's/[0-9]{8,10}:[A-Za-z0-9_-]{30,}/REDACTED_TELEGRAM_TOKEN/g' \
    -e 's/(["'"'"'])(sk|pk|rk)_[A-Za-z0-9]{16,}(["'"'"'])/\1REDACTED\3/g' \
    -e 's/((api|API)[_-]?(key|KEY)|token|TOKEN|secret|SECRET|password|PASSWORD|chat_id|CHAT_ID)([A-Za-z_]*)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{6,}["'"'"']/\1\4 = "REDACTED"/g' \
    -e 's/(apiKey=)[A-Za-z0-9]{16,}/\1REDACTED/g' \
    -e 's/[0-9a-f]{32}-[0-9a-f]{32}/REDACTED_OANDA_TOKEN/g' \
    -e 's/[0-9]{3}-[0-9]{3}-[0-9]{6,9}-[0-9]{3}/REDACTED_OANDA_ACCOUNT/g' \
    -e 's/(Bearer[[:space:]]+)[A-Za-z0-9._-]{20,}/\1REDACTED/g' \
    -e 's/\b(PK|AK|CK)[A-Z0-9]{16,}\b/REDACTED_BROKER_KEY/g' \
    -e 's/((API|api|APCA|apca)[-_A-Za-z]*(KEY|key|SECRET|secret|TOKEN|token)[-_A-Za-z]*["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'])[^"'"'"']{6,}(["'"'"'])/\1REDACTED\4/g' \
    -e 's/(["'"'"'])[A-Za-z0-9\/+]{40}(["'"'"'])/\1REDACTED40\2/g' \
    "$f"
done
tar -C "$WORK" -czf "$OUT" odds-bot
rm -rf "$WORK"
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))."
echo "Secrets were stripped, but open it and double-check before uploading."
echo "Download it from the SSH window: gear menu -> Download file -> $OUT"
