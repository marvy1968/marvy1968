# AJ telegram_bot.py source export — 2026-11-16 (CT)

Purpose: source for the CloudAgent PR fixing the duplicate poll thread in `TelegramBot.ensure_polling()` (ajbet/telegram_bot.py ~lines 455-505).
AJ source exists only on the VM (not in GitHub). Read-only export: existing SSH login (moonrider1968, ~/.ssh/gcp_vm), no GitHub/deploy key, no marv_link,
no VM writes/restarts/code execution. Each file: lstat regular non-symlink, realpath parent check, O_NOFOLLOW open, dev/inode match, <=350 KB, SHA256 verified after transfer.
NOT exported: config.py, config.yaml, env files, DBs, logs, conftest.py (needs config.py + config.yaml; cloud must supply fixture config).

## Files
| original | exported | bytes | original SHA256 | exported SHA256 | status | original mtime (CT) | read (CT) |
|---|---|---|---|---|---|---|---|
| /home/moonrider1968/aj-bet-bot/ajbet/telegram_bot.py | source/telegram_bot.py | 88953 | `085fa66d6318b11b05881393b27f8e55352bfeac3db81ef35898301c8e6e886f` | `085fa66d6318b11b05881393b27f8e55352bfeac3db81ef35898301c8e6e886f` | identical | 2026-11-01T03:14:14.702665-06:00 | 2026-11-16T20:41:41.060087-06:00 |
| /home/moonrider1968/aj-bet-bot/ajbet/valuefinder.py | source/valuefinder.py | 22203 | `841ac5caa4019790077b642895f5f10c0f5bea9fbecddab6689ac4c07ff95980` | `841ac5caa4019790077b642895f5f10c0f5bea9fbecddab6689ac4c07ff95980` | identical | 2026-10-23T19:21:03.370713-05:00 | 2026-11-16T20:41:41.061148-06:00 |
| /home/moonrider1968/aj-bet-bot/ajbet/db.py | source/db.py | 16868 | `1b79b79b8c64b2a07cdd4086af9237b1d53f831f49ccb27c580e2b27bf11d5a4` | `1b79b79b8c64b2a07cdd4086af9237b1d53f831f49ccb27c580e2b27bf11d5a4` | identical | 2026-11-11T14:24:30.975284-06:00 | 2026-11-16T20:41:41.061728-06:00 |
| /home/moonrider1968/aj-bet-bot/ajbet/oddsapi.py | source/oddsapi.py | 12578 | `45e43b187777f656172090841b8214ce917f4b9c10f813180205bfb275be4ddc` | `45e43b187777f656172090841b8214ce917f4b9c10f813180205bfb275be4ddc` | identical | 2026-10-24T09:52:55.505303-05:00 | 2026-11-16T20:41:41.062131-06:00 |
| /home/moonrider1968/aj-bet-bot/ajbet/__init__.py | source/__init__.py | 112 | `d5e2d7daa36ef666eabb14830ac48d3a21cbd840ae49d4025bbed692be8a1f07` | `d5e2d7daa36ef666eabb14830ac48d3a21cbd840ae49d4025bbed692be8a1f07` | identical | 2026-10-01T22:52:54-05:00 | 2026-11-16T20:41:41.062647-06:00 |
| /home/moonrider1968/aj-bet-bot/tests/test_telegram_bot.py | source/test_telegram_bot.py | 15660 | `c2ef2f5d634ce6ca5e9aa4e34a7b48661ae2f9ccc8f9a39f16f76c11d646b623` | `c2ef2f5d634ce6ca5e9aa4e34a7b48661ae2f9ccc8f9a39f16f76c11d646b623` | identical | 2026-10-03T14:28:51-05:00 | 2026-11-16T20:41:41.062858-06:00 |

## Redaction / screening notes
Screened every line for: Telegram bot tokens, `api.telegram.org/bot<literal>`, private-key markers, emails, credential prefixes (sk-, ghp_, github_pat_, AKIA, xox*, AIza),
credential literal assignments (api_key/token/secret/password/chat_id/bearer = "..."), Telegram chat-ID-like numbers (-100..., chat + 6-13 digits), long numerics (9+ digits), phone-like.
- Result: **no redactions; all exported files are byte-identical to the originals.**
- One hit, kept: tests/test_telegram_bot.py line 14 `CHAT = "<6-digit>"` is a synthetic test fixture (6 digits, 2 distinct digits). Its SHA256 matched no numeric value in the AJ env,
  telegram-send.conf or config.yaml (fingerprint comparison on the VM, no values printed). The test token literal is the fixture string "T0KEN".
- telegram_bot.py takes its token from the Notifier at runtime (`self._token = getattr(notifier, "_token", "")`) and scrubs it from logs; oddsapi.py reads keys from env var names in config (dynamic, no values).

## Imports (see static-imports.json)
telegram_bot.py top-level local imports (needed to import the module): friendly, valuefinder, oddsmath, db, markets, oddsapi.
 - this bundle: valuefinder.py, db.py, oddsapi.py, __init__.py; earlier bundles: friendly.py, oddsmath.py, markets.py, mybook.py, models.py (valuefinder/markets deps).
 - With this bundle + earlier six-file and 14-file bundles, `import ajbet.telegram_bot` resolves (stdlib + requests only).
Lazy (inside command handlers, not needed for the poll-thread fix/tests): budget [NOT exported (lazy, inside command handlers)], grader [NOT exported (lazy, inside command handlers)], lane_top [NOT exported (lazy, inside command handlers)], line_swing [NOT exported (lazy, inside command handlers)], live_quality [NOT exported (lazy, inside command handlers)], marv_live [NOT exported (lazy, inside command handlers)], pace_cmd [NOT exported (lazy, inside command handlers)], paper [NOT exported (lazy, inside command handlers)], policy [NOT exported (lazy, inside command handlers)], recalibrate [NOT exported (lazy, inside command handlers)], recap_split [NOT exported (lazy, inside command handlers)], results_fmt [NOT exported (lazy, inside command handlers)], scanner [NOT exported (lazy, inside command handlers)], sweetspot [NOT exported (lazy, inside command handlers)], total_swing [NOT exported (lazy, inside command handlers)], trends [NOT exported (lazy, inside command handlers)], parlay_pause (external /opt/marv-feed, lazy) [NOT exported].
tests/test_telegram_bot.py additionally imports ajbet.alerts (Notifier; imports strategies [earlier bundle]), ajbet.value_scan (106 KB, large dependency chain) and ajbet.espn [earlier bundle];
alerts.py and value_scan.py are NOT exported — for the new ensure_polling tests, stub Notifier (needs `_token`, `_chat`, `send()`) instead.

SHA256SUMS lists the delivered bytes. Evidence for review only; not deployment or runtime-test authorization.
