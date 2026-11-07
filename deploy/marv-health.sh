#!/usr/bin/env bash
# Marv watchdog (marv-health.timer, every 30 min): Telegram alert only when something is wrong, and once
# more when it's fixed. Checks: always-on services running, the daily run happened in the last 26 hours
# and didn't fail, disk under 90%, Odds API credits not nearly gone.
#
# Uptime and the previous-run timestamp are integers. `print` of a large awk number follows OFMT
# (%.6g), which is scientific notation for non-integers and for some awk builds, and Bash then
# rejects the value. A non-integer here exits 1 before any alert. A completed check still exits 0,
# including a check that found a real problem and alerted.
set -uo pipefail
DIR=${MARV_HEALTH_DIR:-/opt/marv-bot}
STATE=$DIR/state
UPTIME_FILE=${MARV_HEALTH_UPTIME_FILE:-/proc/uptime}
mkdir -p "$STATE"

die() { printf 'marv-health: %s\n' "$*" >&2; exit 1; }

normalize_uint() {
  local raw="$2"
  raw=${raw//[[:space:]]/}
  case "$raw" in
    ""|"0"|"n/a"|"[n/a]"|"none"|"[notset]")
      if [ "$3" = yes ]; then printf '%s\n' 0; return 0; fi
      die "$1 is missing" ;;
  esac
  case "$raw" in
    *[!0-9]*) die "$1 is not an integer: $raw" ;;
  esac
  printf '%s\n' "$raw"
}

uptime_us() {
  [ -r "$UPTIME_FILE" ] || die "cannot read uptime"
  local raw now
  raw=$(awk 'NR==1{print $1; exit}' "$UPTIME_FILE") || die "uptime read failed"
  raw=${raw//[[:space:]]/}
  [ -n "$raw" ] || die "uptime is empty"
  case "$raw" in
    *.*.*|*[!0-9.]*|.*|*.) die "uptime is not a number: $raw" ;;
  esac
  now=$(awk 'NR==1{printf "%d\n", int($1 * 1000000); exit}' "$UPTIME_FILE") || die "uptime integer conversion failed"
  now=${now//[[:space:]]/}
  case "$now" in
    ""|*[!0-9]*) die "uptime microseconds are not an integer: ${now:-empty}" ;;
  esac
  printf '%s\n' "$now"
}

now=$(uptime_us) || exit $?
last_raw=$(systemctl show -p ExecMainExitTimestampMonotonic --value marv-bot.service 2>/dev/null || true)
last=$(normalize_uint "ExecMainExitTimestampMonotonic" "$last_raw" yes) || exit $?
up_hours=$(( 10#$now / 3600000000 ))
since_hours=0
if [ "$last" -gt 0 ]; then
  if [ "$now" -ge "$last" ]; then
    since_hours=$(( (10#$now - 10#$last) / 3600000000 ))
  else
    die "last-run timestamp is ahead of uptime"
  fi
fi
if [ -n "${MARV_HEALTH_TRACE:-}" ]; then
  printf '%s %s %s\n' "$now" "$up_hours" "$since_hours" > "$MARV_HEALTH_TRACE"
fi

problems=()

for svc in marv-live marv-edges marv-bridge; do
  systemctl is-active --quiet "$svc" || problems+=("$svc is not running (systemd restarts it; check: journalctl -u $svc -n 50)")
done

if [ "$(systemctl show -p Result --value marv-bot.service)" = "exit-code" ]; then
  problems+=("the daily run failed last time (journalctl -u marv-bot -n 80)")
fi
if [ "$last" -eq 0 ] && [ "$up_hours" -ge 26 ]; then
  problems+=("the daily run hasn't finished since the VM started ${up_hours}h ago")
elif [ "$last" -gt 0 ] && [ "$since_hours" -ge 26 ]; then
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
