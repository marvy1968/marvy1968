# Marv Predict Max: handoff notes for Claude Code on the VM

You are running on the owner's Google Compute Engine VM. The bot lives in `/opt/marv-bot` (installed by
`deploy/install.sh`, runs as user `marvbot`). Read README.md and ANALYSIS.md before changing behaviour.

## What it is
Multi-sport betting-prediction bot that sends Telegram cards. Active sports (`DEFAULT_SPORTS` in
`marv/sports.py`): NFL, college football top 30 (`cfb`), EuroLeague, WNBA, NCAA men's (`ncaab`) and women's
(`ncaaw`) basketball. NBA/MLB/NHL/soccer exist but are off (NBA off until later in its season).

Pipeline (`marv/main.py run`): load schedule + odds (`marv/sources.py`) → stats experts project each game
(`marv/stats/`: every team offense/defense stat, strength-of-schedule adjusted for college/EuroLeague;
ridge "stat formula" + random forest + power ratings, consensus) → shared Monte Carlo (`marv/sims/`) →
veto stack (expert agreement, backtested confidence floors in `marv/stats/registry.py`, key injuries
`marv/data/injuries.py`, stale stats) → Telegram card + pick ledger + SQLite audit + PDF chart.

## Services (systemd)
- `marv-bot.timer` 10:00 ET daily, `marv-bot-late.timer` 17:30 ET (+11:45 Sun): `python -m marv run`
- `marv-live.service`: in-game updates after every quarter/half (`python -m marv live`)
- `marv-bridge.service`: local fair-price API on 127.0.0.1:8787 for the owner's odds-alert bot

## Backtested results (walk-forward, held-out seasons) - keep claims honest
| Sport | ML floor | Held-out ML accuracy |
|---|---|---|
| NFL | 78% + eliminations (market<70%, starters out, wind) | 86.7-93% on 30-76 picks, ~break-even ROI |
| NCAA men's | 80% + experts agree + roster veto | 90.8% (3,907 picks, 2024-26 D1) |
| NCAA women's | 76% | 90.8% (7,076 picks, 2023-25 D1) |
| WNBA | 85% | ~83-88% |
| NBA (off) | 86% | 90.5%; O/U 52.8% |
High accuracy = heavy favorites; ROI after vig is ~break-even where measured. Never present targets
(e.g. "90%+ guaranteed", the 100% figures in the owner's PDFs) as results.

## Open work, in order
1. Install/verify: `sudo bash deploy/install.sh`; fill `/opt/marv-bot/.env` (TELEGRAM_BOT_TOKEN,
   TELEGRAM_CHAT_ID, CFBD_API_KEY, optional ODDS_API_KEY); `python -m marv test-telegram`;
   `python -m marv run --sport all --force --dry-run` and fix anything that fails against live
   ESPN / CFBD / EuroLeague / MLB APIs (these were unreachable from the build sandbox, only unit-tested).
2. Backtests that need the VM's network:
   `python -m marv stats-backtest --sport cfb --seasons 2016-2025`,
   `--sport euroleague --seasons 2018-2025`, and `--sport ncaab|ncaaw --seasons 2019-2025` (now with
   ESPN historical lines → O/U accuracy and ROI). Update `marv/stats/registry.py` RULES and
   `marv/backtest_results.json` from the reports in `state/reports/`.
3. EuroLeague Women: `python -m marv probe-ewl`; adapt `marv/data/fiba.py` if the FIBA LiveStats format
   differs; add a schedule source and no-odds cards before enabling `euroleague_women`.
4. Owner's odds-alert bot ("March_edge"): find it with `sudo bash deploy/find_odds_bot.sh`; integrate via
   the bridge (`marv/bridge.py`): block alerts Marv disagrees with, fix "edge vs open" (ignores score),
   duplicate alerts and stake sizing.

5. NFL player props (`marv/props/`): `python -m marv props-backtest --seasons 2023-2025 --real` grades the
   model against real past Bovado/DraftKings/FanDuel lines (~40 credits per game, capped by --max-credits;
   responses cached in state/cache/props_hist). Set PROPS_SHRINK / PROPS_MIN_EDGE from its results.
   Live: `python -m marv props --dry-run`; the daily NFL run sends props automatically when ODDS_API_KEY is set.
   NCAAB props: same commands with `--sport ncaab` (`--real` only fetches games with a top-50 team).

6. Edge board + Telegram queries (`marv/board.py`, `marv/querybot.py`, `marv-edges.service`): verify
   `python -m marv edges` against live Odds API data and `/board` in Telegram. RECOMMENDED status comes
   only from `marv/backtest_edges.json` (closing-price backtests); update it if new backtests qualify.
7. Quarter-by-quarter model (`marv/ingame/`): `python -m marv ingame train --sport nfl` (and cfb, ncaab,
   ncaaw, wnba). The live ESPN summary parsers (`ingame/plays.py: espn_football, espn_basketball`) were
   written from ESPN's documented JSON without network access: check them against a real
   `/summary?event=` response during the first live game and fix field names if needed.

8. Line movement + O/U trend tags (paper only): every card shows "line move: open → now"; `state/lines.json`
   keeps open and last-before-kickoff lines for a year. College cards tag OVER-FADE / OVER-FADE+MOVE /
   OVER-FADE+INFLATED / UNDER-FADE and NFL cards TOTAL-INFLATED (`marv/ou_tags.py`), logged to
   `state/ou_tags_log.json` and graded by `python -m marv situational`. Check the tags appear on the next
   cfb/nfl dry run (`--sport cfb --force --dry-run`); never turn them into picks without 100+ graded games.

## Rules
- Run `python -m unittest discover -s tests` before restarting services.
- Keep PAPER_MODE on unless the owner explicitly turns it off; picks are graded in `state/picks.json`.
- Don't print or commit secrets from `.env`. Git remote: github.com/marvy1968/marvy1968, branch
  `claude/analysis-ak180w`.
