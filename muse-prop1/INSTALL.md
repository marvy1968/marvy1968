# Muse Prop1 — Install (for Grok / any installer)

## 1. What this is
Muse Prop1 is a log-only in-game player-prop engine for Bovada.
NFL + Top 30 NCAAF + NCAAB + NCAAW. It sends recommendations only.
It NEVER places a bet, never logs in to Bovada, never auto-bets.

## 2. Prerequisites
- Python 3.12+
- A PropLine API key (free 1,000/day at prop-line.com — do NOT paste it in chat, put it in `.env`)
- No other keys needed for log-only mode. Telegram keys only if/when alerts turn on.

## 3. Install
```bash
git clone <repo> muse-prop1        # or copy this folder
cd muse-prop1
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: PROPLINE_API_KEY=...   (chmod 600 .env)
```

## 4. Check (do this before any live run)
```bash
python3 test_engine.py        # expect 9/9
python3 test_muse_prop1.py    # expect 4/4, no network
python3 runner.py --once --sport nfl --dry-run
# expect: 2 demo picks logged, second run: 0 picks (dedupe)
# check state/picks.jsonl — no real money, no Telegram, no network
rm -f state/picks.jsonl state/dedupe.json state/first_lines.json state/skips.jsonl
```

## 5. Verify the live feeds (one real response per sport — REQUIRED)
Parsers were wired to PropLine docs + ESPN docs + fixtures, NOT to live data.
Before trusting a live pick, run these and confirm the parsers return rows:
```bash
# PropLine: does basketball_ncaaw exist?
curl "https://api.prop-line.com/v1/sports?apiKey=$PROPLINE_API_KEY"
# Expect football_nfl, football_ncaaf, basketball_ncaab. If basketball_ncaaw is
# missing / 404, NCAAW stays log-only / disabled — do NOT guess a key.
# ESPN: one live scoreboard per sport, check parse_scoreboard() returns live games
# with elapsed_min > 0 and parse_summary() returns players with stats.
```
If a live shape differs from the fixture, fix the parser, add a fixture for
that shape, re-run all tests, and do NOT go live until tests pass again.

## 6. Run log-only (first live run)
```bash
python3 runner.py --once --sport nfl
# mode is log_only in config.yaml — picks go to state/picks.jsonl only, no Telegram
# Check state/skips.jsonl for why lines were rejected
```
Schedule with cron (one cycle per minute, runner has a lock file so overlaps exit):
```
* * * * * cd /path/to/muse-prop1 && .venv/bin/python runner.py --once >> logs/runner.log 2>&1
```
Do NOT run a loop + cron at the same time.

## 7. What NOT to do
- Do NOT flip `mode` to `alert` until a sport passes DESIGN §7 (replay + 2-week shadow).
- Do NOT place bets from the picks, and do NOT add auto-betting code.
- Do NOT lower the edge/EV gates (7pp / 5%) to get more picks.
- Do NOT commit `.env`, `state/`, or `__pycache__` to git.
- Do NOT paste any API key, Telegram token, or Bovada credential in chat.

## 8. Files
- `DESIGN.md` — full design + pre-registered test
- `engine.py`, `bovada.py`, `live_feed.py`, `priors.py`, `runner.py`, `backtest.py`, `notifier.py`
- `config.yaml`, `top30_ncaaf.yaml` (refresh weekly from AP/ESPN rankings)
- `fixtures/` — mocked PropLine/ESPN NFL data for dry-run
- `GROK_NOTE.md` — the check list for Grok, in forwardable form
