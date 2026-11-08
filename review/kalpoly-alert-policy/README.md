# Review-only Kalshi DEMO / Polymarket PAPER alert policy

Not activated. No installer, timer, service, stake, bankroll, lane, or order change.

The base is `review/kalpoly-alert-policy-base-20261108` at `b38135da31a760c17b991662910ed52f873840c2`. It is not `claude/analysis-ak180w` and it is not the merged paper-identity head.

Source archive SHA256 `0081aefa3b8ac67cc51dada790e7839afdaf8f54f7858c35146e190e8a40e9b0`. The files under `provenance/source` match that export byte for byte. `provenance/SHA256SUMS` lists them.

## What the exported source does

`scanner.py` and `value_scan.py` call `notifier.send`, then `db.mark_sent`, only when `send` returns true. `flush_outbox` calls `mark_sent` only after `_send_one` succeeds. `sent=1` in that source means the primary chat accepted the message. This review does not call `send()` and does not write `sent=1`. An HTTP 200 observed without `send()` stays `missing_dependency` and leaves `sent` at 0.

`export_props.py` feeds `sent=1` rows plus the existing NFL `team_trend` exception. `me_alerts.load` is what `live_runner.py`, `me_kalshi.py`, paper `me_entries`, and `pm_me_track.py` read. `prop_feed.py` does not read `sent`. The exported `send()` swallows an import error in `cfb_track`, `nfl_live_rec_track`, `ncaaf_live_track`, `live_prop_ev50_track`, and `me_gates` and continues. That is not a recorded rejection. This review does not copy that swallow into eligibility.

Hold, demote, veto, dry-run, and double-dip are rejections visible in the exported control flow: they return before the post. Missing modules are a different status. Neither is feed-eligible. The eligibility table cannot store `gate_proof = 'executed'` or `suppressed_weekday`. A weekday Telegram skip therefore does not add a trade. The export id list stays the production `sent=1` plus team-trend query. That is not live selection parity.

`main.py` writes `betcard_sent.json` and then calls `send_message`. The file is not a Telegram receipt. Weekend tests keep that order and set `delivery_proven` false. Weekdays do not post the card and do not write that file. The evening record names only Kalshi DEMO and Polymarket PAPER. It is not an AJ message dump.

The 21:00 America/Chicago summary is one rendered body per Central weekday date. Publishing it records a receipt separately. A crash after the post starts, and before a receipt is stored, leaves `unknown` and does not claim delivery. A restart does not post again in that state. Two processes can still both reach Telegram. `exactly_once_telegram` is false even after one in-process HTTP 200. Open rows stay cost at risk. A settle time that is missing stays unknown.

Urgent notices are a separate table. They do not set `sent` and they do not enter the trade feed. A dead process cannot send one. `external.py` is a blocked proposal. `marv-health.sh` remains a 30-minute check and is not started.

`loss_policy.json` is off. Five percent steps and a below-baseline notice are a Monitor proposal. Unknown day-open or baseline produces `day_opening_unknown` or `baseline_unknown`, not a red alert. A red notice is only a transition from a verified open at or above a verified baseline to a settled value below it. 2500 is not a verified Kalshi baseline. 10000 is a risk bankroll, not a starting baseline.

`card_rules.py` is already in the VM archive at `source/marv-feed/card_rules.py` and is called from `me_alerts.py` line 197 for non-prop entries. This review does not run it.

## Files still required

`EXPORT_REQUEST.json` lists the absolute paths. Each line was checked against the hashed provenance. Do not send configs, env, credentials, databases, or logs.

## Activation is blocked

No cron, unit, or installer is changed. These tests do not replay `send()` and do not show that Monday and Saturday would-bet sets match.

## Test

```bash
python3 -m pytest review/kalpoly-alert-policy/tests -q
```

9 passed. The tests check source line text, the closed eligibility table, Central Friday through Monday and DST, receipt failure, and the two-venue summary. They are not live selection parity.
