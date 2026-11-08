# Review-only Kalshi DEMO / Polymarket PAPER alert policy

Not activated. No installer, timer, service, stake, bankroll, lane, or order change.

The base is `review/kalpoly-alert-policy-base-20261108` at `b38135da31a760c17b991662910ed52f873840c2`. It is not `claude/analysis-ak180w` and it is not the merged paper-identity head.

Source archive SHA256 `0081aefa3b8ac67cc51dada790e7839afdaf8f54f7858c35146e190e8a40e9b0`. The files under `provenance/source` match that export byte for byte. `provenance/SHA256SUMS` lists them.

## What the exported source does

`scanner.py` and `value_scan.py` call `notifier.send` and call `db.mark_sent` only when that returns true. `alerts.py` `flush_outbox` calls `mark_sent` only after `_send_one` succeeds. `db.mark_sent` is `sent=1`. That flag means the primary Telegram chat accepted the message. This review does not write `sent=1` for a skipped or failed post.

`export_props.py` puts `sent=1` rows, plus the existing NFL `team_trend` exception, into `alerts`. `me_alerts.load` is what `live_runner.py`, `me_kalshi.py`, `runner.py` `me_entries`, and `pm_me_track.py` read. `prop_feed.py` does not read `sent`. Holds, demotions, and pauses return before `send` and stay out of the feed. `h2h_cfb` is `NEVER_FEED`.

Saturday and Sunday America/Chicago keep that post path. Monday through Friday an ordinary per-pick or bet-card post is not attempted. The gate decision is stored in `feed_eligibility` instead. The review export includes that row without setting `sent`. A rejected, held, demoted, or dry-run row is not stored there.

A failed or unknown transport receipt leaves `sent` at 0. Unknown is not treated as delivered. A later retry can set `sent=1` only after `http_200`. The same alert is not posted twice. Double-dip protection sees an eligible row, so a weekday skip does not open a second side that a delivered weekend alert would have blocked.

The weekday 21:00 America/Chicago summary is one rendered record per Central date. It uses settled `payout - cost` for that Central settle time. `cost` already includes the fee, which is the grader definition in both venue graders. Open rows stay open risk, not a mark to market. A settled row with no settle time stays unknown. The summary names Kalshi DEMO and Polymarket PAPER and includes its generation time. Tests only render it.

Urgent crash, stall, and loss notices use a separate table and are eligible on weekdays. They are not alert `sent` flags. A process that is down cannot send its own notice. The existing health script runs every 30 minutes and does not watch these venues. An outside checker would still be required. None is installed here.

`loss_policy.json` is off. The 5 percent day-loss steps and a below-baseline notice are a Monitor proposal, not a user-approved threshold. The Kalshi figure 2500 is not a verified baseline. The configured 10000 is a risk bankroll, not a starting baseline. Both stay `UNKNOWN` until a verified snapshot is supplied. Turning the proposal on in a test does not change stake or a risk stop.

`schedule.json` describes the 21:00 Central weekday slot. `installed` and `cron_active` are false.

The Marv bet card on a weekend still records its sent file before the post, matching `main.py`. On a weekday it logs the card for grading, does not post, and does not write `betcard_sent.json`.

## Activation is blocked

The send gates inside `alerts.py` import `cfb_track`, `nfl_live_rec_track`, `ncaaf_live_track`, `live_prop_ev50_track`, `me_gates`, `nba_props_tg`, `enrich`, `h2h_model`, and `buy_edge`. Those modules are not in this export, so this review does not execute them. `gates_passed` is the caller's statement that those gates already allowed a post. Do not treat that as a live replay of `send()`.

`me_kalshi.py` and the paper runner still apply their own mapping, edge, and stake code after `me_alerts.to_entry`. Those formulas are not modified and are not re-executed here. The parity test is the shared feed: the same successful `to_entry` ids on a delivered Saturday and a suppressed Monday.

`prop_feed.py` reads props from `paper_bets` and the export's pick lists, not from `sent`. It is unchanged. Environment-file overrides were not read. The known paused-track versus bet-card divergence is not reconciled, and paused rows are not added to this feed.

No cron, unit, or installer is changed. A future activation needs a byte-exact backup of the config being edited, a named restore of that backup, and a separate decision to wire the hook. This tree does not do that.

## Test

```bash
python3 -m pytest review/kalpoly-alert-policy/tests -q
```
