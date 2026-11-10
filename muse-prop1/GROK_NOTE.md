# Note for Grok — check and install Muse Prop1

Marvin-anthony asked Muse to write this engine, and asked you (Grok) to check it and install it. This note is forwardable as-is. Muse cannot reach you directly — there is no Grok connector here — so Marvin-anthony is forwarding this file / repo to you.

## What Muse Prop1 is
- In-game player-prop engine. Odds from PropLine (Odds API) only — NO
  Bovada, no sportsbook. Recommendations only. **Never auto-bets, never
  logs in to any sportsbook.**
- Sports: NFL + Top 30 NCAAF + NCAAB + NCAAW (women's college basketball). Each sport is tested separately.
- How it works: PropLine API for live odds (median price across the
  bookmakers PropLine returns, explicitly excluding "bovada") + ESPN site
  API for live stats → pace projection (football: plays/min, basketball:
  possessions + projected minutes), rate-only (live rate, season pace) →
  fair probability vs no-vig price → send only if edge ≥7pp AND EV ≥5%,
  line stable, re-checked at send time, deduped. The projection is anchored
  to the current posted line, and the prior is frozen BEFORE the game starts
  (`runner.py --freeze-priors`, run on a cron before the slate).
- Mode is `log_only` in `config.yaml`. Picks go to `state/picks.jsonl` only. No Telegram until a sport passes the test in DESIGN.md §7.

## Where the code is
- This folder: `muse-prop1/` — `DESIGN.md`, `README.md`, `INSTALL.md`, `engine.py`, `propline.py`, `quota.py`, `live_feed.py`, `priors.py`, `runner.py`, `backtest.py`, `notifier.py`, `config.yaml`, `top30_ncaaf.yaml`, `fixtures/`, `requirements.txt`, `.env.example`
- If it is on GitHub, pull the `muse/muse-prop1` branch (ask Marvin-anthony for the link — Muse has not pushed it yet unless he approved that step).

## What to CHECK (before you install / run live)
1. **Tests first:** `pip install -r requirements.txt`, then `python3 test_engine.py` (expect 21/21) and `python3 test_muse_prop1.py` (expect 18/18, no network). If any fail, stop and report the failure — do not go live.
2. **Dry-run:** `python3 runner.py --once --sport nfl --dry-run` — expect 2 demo picks logged, second run 0 picks (dedupe). No network, no Telegram, no money. Then clear `state/` (`rm -f state/picks.jsonl state/dedupe.json state/first_lines.json state/skips.jsonl`).
3. **Live-shape verification — REQUIRED, this is the main risk:** the parsers were wired to PropLine docs + ESPN docs + fixtures, NOT to live data.
   - PropLine: `GET /v1/sports?apiKey=...` — confirm `football_nfl`, `football_ncaaf`, `basketball_ncaab` exist. **Verify `basketball_ncaaw` — Muse could NOT confirm that key in the docs. If it is missing / 404, NCAAW stays disabled/log-only. Do not guess a key.**
   - PropLine bulk odds for one live NFL game: confirm `extract_propline_lines()` returns rows with player, market, line, over/under prices. If the shape differs, fix the parser, add a fixture for that shape, re-run all tests.
   - ESPN scoreboard + summary for one live game per sport: confirm `parse_scoreboard()` returns live games with elapsed_min > 0, and `parse_summary()` returns players with stats (football: pass_yds/rush_yds/rec_yds; basketball: minutes/points/rebounds/assists/fouls). If a sport returns no parseable players, that sport must skip games (fail-closed) — do not project from missing stats.
4. **Secrets:** `.env` must contain ONLY `PROPLINE_API_KEY=...` (from prop-line.com, free tier). No sportsbook login, no Telegram token needed for log-only. Never paste keys in chat, never commit `.env` or `state/`.
5. **Quota:** PropLine free is 1,000 req/MONTH (per Grok's review — the old
   "1,000/day" claim was wrong). Polling is 300s per sport, only while that
   sport has live games, and every fetch is reserved against the budgets in
   config.yaml (500/day, 1,000/month) — over budget, calls stop. Report the
   quota you expect before scheduling. Full-season live operation needs the
   Hobby tier or a slower poll.
6. **Top 30 NCAAF:** `top30_ncaaf.yaml` lists the AP Top 30 and maps each
   to ESPN's displayName (`espn_display_names`), verified against the live
   ESPN teams endpoint. Refresh it weekly from AP / ESPN rankings before
   NCAAF runs. A game qualifies only if either team is in the list.

## What to INSTALL
Follow `INSTALL.md` §3–§6 exactly:
- venv + `pip install -r requirements.txt`, `cp .env.example .env` (chmod 600), set `PROPLINE_API_KEY`
- Run the checks in §4–§5 above
- First live run: `python3 runner.py --once --sport nfl` (log-only, one cycle)
- Cron, one cycle per minute, only after the live-shape checks pass. Runner has a lock file — do not run loop + cron together.

## What NOT to do
- Do NOT flip `mode` to `alert` (Telegram) until a sport passes DESIGN.md §7: replay ROI >0, yield >3%, n≥50, calibration within 8pp, top-10 profit share ≤50%, then 2 weeks forward shadow.
- Do NOT place bets from the picks, and do NOT add auto-betting / sportsbook login code.
- Do NOT lower the edge/EV gates (7pp / 5%) to get more picks.
- Do NOT change exits, gates, or config mid-test — a config change is a new test.
- Do NOT run Muse Prop1 on the same machine/account as any live trading bot in a way that shares state — this engine is separate, log-only, and has no broker code at all.

## Report back to Marvin-anthony
- Tests: pass/fail counts
- Dry-run: picks logged / dedupe result
- Live-shape check per sport: pass/fail, and NCAAW key: exists or not
- Install: where it is installed, cron schedule (if any), mode (must still be log_only)
- Any parser fixes you made, with the new fixture + test run

## Known limits (Muse's own words, don't gloss over them)
- Prior mean = first-seen PropLine line, frozen BEFORE the game starts
  (`runner.py --freeze-priors`). That is a market prior, not a
  nflverse/Torvik model. It is honest, but it is not a full projection system.
  The projection is anchored to the current posted line and uses the live
  rate at season pace (rate-only rework).
- The Nov 1–2 replay (+6u, 29-21) does NOT prove an edge — 83% of profit was 8 bets, and the old code had a lookahead bug. This engine must re-run that period with the lookahead fix before anyone trusts it.
- NCAAW is the thinnest, highest-risk sport in the set. It goes last.
- If test ROI is ~0 after vig, the correct answer is "no edge — don't bet it", not a lower threshold.
