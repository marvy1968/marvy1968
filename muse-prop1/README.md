# Muse Prop1 — Bovada in-game player props

Design: `DESIGN.md` v1.1 — read that first. Install: `INSTALL.md`. Grok check: `GROK_NOTE.md`.

## Scope (v1.1)
NFL + Top 30 NCAAF + NCAAB + NCAAW. Each sport tested separately — a pass in NFL does not unlock the others. Testing order: NFL, NCAAF, NCAAB, NCAAW last.

## Quick start (log-only replay, no money, no network)
Snapshot replay is the comparable path. It does not poll PropLine or the Odds API.
`LIVE_PROPS_SEND` must be explicitly `off`. Unset is refused.
```bash
cd muse-prop1
pip install -r requirements.txt
export LIVE_PROPS_SEND=off
python3 test_engine.py
python3 test_muse_prop1.py
python3 test_calibration.py
python3 test_replay.py
python3 runner.py --dry-run --state-dir /tmp/muse-prop1-replay
```
See `COLLECTOR.md` for the shared collector, the 100-credit cap, and the compare commands.

## Status
- Replay reads `marv.prop_snapshot.v1` and writes `marv.prop_shadow.v1`. The hash matches Claude Prop 1 at `0155dbca`.
- Game identity is normalized sport + away + home + UTC scheduled start. Provider ids are aliases, not the key.
- A pregame number is used only when the snapshot anchor kind is `quoted_pregame`. `inferred` stays inferred. The kind `pregame` is refused.
- Production rate is that player's anchor or a player file entry. The old generic yards-per-play table is not used.
- Pace is observed team plays or possessions from the ESPN boxscore. Missing pace abstains.
- Half-point totals are not shifted by an extra 0.5. Integer lines still use +0.5.
- At most 3 recommendations per cohort, remembered across snapshots in the orchestrator index.
- Probabilities are `uncalibrated`. No held-out fit is shipped. A probability cap is not a calibration.
- Mode stays `log_only`. Telegram stays off unless `LIVE_PROPS_SEND` is explicitly on, `mode` is `alert`, and `telegram_enabled` is true. This CLI refuses every case except log-only replay.

## What it will NOT do
- Place bets on Bovada. No auto-betting, no Bovada login, no code path that bets.
- Guarantee winners. Target is calibrated edge > vig, repeatedly, on unseen games.
- Use stats or prices from after bet time. Replay enforces as-of <= bet_time - 30s.

## What Grok still needs to check (see GROK_NOTE.md)
1. One real PropLine + one real ESPN response per sport — parsers were wired to docs + fixtures, not live data.
2. NCAAW PropLine key (`basketball_ncaaw`) — unverified.
3. Install + dry-run + one live log-only cycle on the target machine.
