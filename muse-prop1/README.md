# Muse Prop1 — in-game player props (PropLine odds only)

Design: `DESIGN.md` v1.1 — read that first. Install: `INSTALL.md`. Grok check: `GROK_NOTE.md`.

> 2026-11-10 (Grok review): Bovada is REMOVED. No code in Prop1 fetches
> from, logs into, or places bets with Bovada or any other sportsbook.
> Odds come from PropLine (Odds API) only — `propline.py` uses the median
> price across the bookmakers PropLine returns, explicitly excluding
> "bovada". See the changelog in DESIGN.md.

## Scope (v1.1)
NFL + Top 30 NCAAF + NCAAB + NCAAW. Each sport tested separately — a pass in NFL does not unlock the others. Testing order: NFL, NCAAF, NCAAB, NCAAW last.

## Quick start (log-only, no money)
```bash
cd ~/workspace/muse-prop1
pip install -r requirements.txt
python3 test_engine.py          # 21 unit tests, no network, no keys
python3 test_muse_prop1.py      # 18 integration tests, mocked PropLine/ESPN
python3 runner.py --once --sport nfl --dry-run   # fixtures, no network, logs 2 demo picks
# then, live (needs a key — do NOT paste it in chat):
cp .env.example .env
# edit .env: PROPLINE_API_KEY=...
python3 runner.py --once --sport nfl   # log-only, one cycle
```

## Status (v1.2 — full engine written)
- `engine.py` — football + basketball pace, fair prob, edge. 21/21 tests.
- `propline.py` — PropLine client (Odds API). No bookmaker filter is
  requested; extraction uses the median price across the bookmakers
  PropLine returns, explicitly excluding "bovada". Main lines only,
  suspended skipped, exact matching. Quota-enforced (quota.py).
- `quota.py` — PropLine call budgets (daily/monthly), fail-closed.
- `live_feed.py` — ESPN scoreboard + summary parser (football passing/rushing/receiving, basketball MIN/PTS/REB/AST/PF, clock → elapsed).
- `priors.py` — prior mean = first-seen PropLine line, frozen BEFORE the
  game starts (`runner.py --freeze-priors`), in `state/first_lines.json`
  with frozen_at + commence_time. SD tables per market.
- `runner.py` — full cycle: fetch → project (rate-only, line-anchored) →
  edge → dedupe → re-check → log BEFORE alert → Telegram only if
  mode=alert. Lock file, fail-closed.
- `backtest.py` — grading + pass bar, plus replay harness (as-of <= bet_time - 30s).
- Fixtures in `fixtures/` — dry-run logs 2 demo picks (Josh Allen, intentionally hot), second run logs 0 (dedupe works).
- Mode is `log_only` in `config.yaml`. Do not flip to `alert` until DESIGN §7 passes per sport.

## What it will NOT do
- Fetch from, log into, or place bets with Bovada or any sportsbook. No
  auto-betting, no sportsbook login, no code path that bets. Odds come from
  PropLine (Odds API) only.
- Guarantee winners. Target is calibrated edge > vig, repeatedly, on unseen games.
- Use stats or prices from after bet time. Replay enforces as-of <= bet_time - 30s.

## What Grok still needs to check (see GROK_NOTE.md)
1. One real PropLine + one real ESPN response per sport — parsers were wired to docs + fixtures, not live data.
2. NCAAW PropLine key (`basketball_ncaaw`) — unverified.
3. Install + dry-run + one live log-only cycle on the target machine.
