#!/usr/bin/env bash
# Marv watchdog (marv-health.timer, every 30 min): Telegram alert only when something is wrong, and once
# more when it's fixed. Checks: always-on services running, the daily run happened in the last 26 hours
# and didn't fail, disk under 90%, Odds API credits not nearly gone.
set -uo pipefail
DIR=/opt/marv-bot
STATE=$DIR/state
mkdir -p "$STATE"
problems=()

for svc in marv-live marv-edges marv-bridge; do
  systemctl is-active --quiet "$svc" || problems+=("$svc is not running (systemd restarts it; check: journalctl -u $svc -n 50)")
done

if [ "$(systemctl show -p Result --value marv-bot.service)" = "exit-code" ]; then
  problems+=("the daily run failed last time (journalctl -u marv-bot -n 80)")
fi
last=$(systemctl show -p ExecMainExitTimestampMonotonic --value marv-bot.service)
now=$(awk '{print int($1 * 1000000)}' /proc/uptime)
up_hours=$(( now / 3600000000 ))
if [ "${last:-0}" -eq 0 ] && [ "$up_hours" -ge 26 ]; then
  problems+=("the daily run hasn't finished since the VM started ${up_hours}h ago")
elif [ "${last:-0}" -gt 0 ] && [ $(( (now - last) / 3600000000 )) -ge 26 ]; then
  problems+=("no daily run in the last 26 hours")
fi

disk=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[ "${disk:-0}" -ge 90 ] && problems+=("disk is ${disk}% full")

credits=$(journalctl -u marv-edges -u marv-bot --since "-1 day" --no-pager 2>/dev/null | grep -oE "[0-9]+ credits left" | tail -1 | grep -oE "^[0-9]+")
[ -n "${credits:-}" ] && [ "$credits" -lt 2000 ] && problems+=("Odds API credits are low: $credits left")

flag=$STATE/.health_alert
msg=$(mktemp)
if [ ${#problems[@]} -gt 0 ]; then
  text=$(printf '⚠️ Marv health check\n'; printf -- '- %s\n' "${problems[@]}")
  if [ ! -f "$flag" ] || [ "$(cat "$flag")" != "$text" ]; then  # alert once per distinct problem set
    printf '%s\n' "$text" > "$msg"
    chmod 644 "$msg"
    runuser -u marvbot -- "$DIR/.venv/bin/python" -m marv notify --file "$msg" >/dev/null 2>&1 \
      && printf '%s' "$text" > "$flag"
  fi
elif [ -f "$flag" ]; then
  printf '✅ Marv health check: everything is running again.\n' > "$msg"
  chmod 644 "$msg"
  runuser -u marvbot -- "$DIR/.venv/bin/python" -m marv notify --file "$msg" >/dev/null 2>&1 && rm -f "$flag"
fi
rm -f "$msg"
