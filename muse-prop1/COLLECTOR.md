# Shared snapshot, replay, and comparison

Muse Prop1 on this branch prices a frozen file. It does not poll. Claude Prop 1
replay at `0155dbca20e5143c4d92e7ff2b47726f716e891d` prices the same file.
The schema text in `SNAPSHOT_SCHEMA.md` is that commit's `docs/prop_snapshot_v1.md`.
The sample is `fixtures/prop_snapshot_v1.sample.json`.

Do not point either engine at `/opt/marv-feed/muse_nfl_shadow`. That cron is an
older AJ-selected experiment and a different sample. Do not replace the shared
`/opt/marv-bot` checkout. Alpaca files there are dirty and are not this work.
Leave `marv-live` and any legacy Muse cron alone.

## Isolated layout

Use two detached worktrees and three writable roots. None of them is
`/opt/marv-bot` or a parent of it (`/opt` itself is refused).

```text
/opt/claude-prop1-wt     git checkout --detach 0155dbca20e5143c4d92e7ff2b47726f716e891d
/opt/muse-prop1-wt       this branch
/var/lib/claude-prop1    Claude --state-dir  (replaced shadow.jsonl)
/var/lib/muse-prop1      Muse --state-dir    (replaced shadow.jsonl)
/var/lib/prop1-archive   orchestrator copies, one directory per input_hash
```

The VM this was sized for has 2 CPUs, about 3.8 GB RAM, and 29 GB disk.
Replay is bounded: more than 500 observations in one file is refused, and
comparison refuses a shadow file over 100,000 rows. Do not load a season of
raw odds into memory in this process.

## Gates

| Gate | Replay | Send |
|---|---|---|
| `LIVE_PROPS_SEND=off` (also `0`, `false`, `no`) | allowed | no Telegram |
| unset, blank, `yes`, or any other value | refused | no Telegram |
| `config.yaml` `mode` | must stay `log_only` on this CLI | `alert` is refused here |
| `telegram_enabled` | false | a send also needs this true, and this CLI never takes that path |

There is no order path. Replay does not import a network client.

## Commands

Muse, from `muse-prop1/`:

```bash
export LIVE_PROPS_SEND=off
python3 runner.py --snapshot fixtures/prop_snapshot_v1.sample.json \
  --state-dir /var/lib/muse-prop1
```

Claude, from the detached Claude worktree, not from this branch:

```bash
LIVE_PROPS_SEND=off python -m marv live-props replay \
  --snapshot /opt/muse-prop1-wt/muse-prop1/fixtures/prop_snapshot_v1.sample.json \
  --state-dir /var/lib/claude-prop1
```

Both commands replace `live_props/shadow.jsonl`. They do not append. The next
snapshot deletes the previous rows in that state dir. Copy them first:

```bash
export LIVE_PROPS_SEND=off
python3 orchestrate.py \
  --snapshot /path/to/marv.prop_snapshot.v1.json \
  --archive /var/lib/prop1-archive \
  --muse-state /var/lib/muse-prop1 \
  --claude-shadow /var/lib/claude-prop1/live_props/shadow.jsonl
```

`orchestrate.py` replays Muse, copies Claude's file without modifying it, and
writes `/var/lib/prop1-archive/by-hash/<hash>/{muse,claude}/shadow.jsonl` plus
`report.json`. Re-running the same snapshot replaces only that hash. Other
hashes stay. A cumulative snapshot (several observations in one file) archives
each `input_hash` separately.

Comparison alone, if both shadow files already exist:

```bash
python3 compare_shadow.py \
  --muse /var/lib/prop1-archive/by-hash/<hash>/muse/shadow.jsonl \
  --claude /var/lib/prop1-archive/by-hash/<hash>/claude/shadow.jsonl \
  --settlement /path/to/settlement.json \
  --out /var/lib/prop1-archive/by-hash/<hash>/compare \
  --hypothetical-unit 1.0
```

`--settlement` is optional. Missing results stay `pending`.

## What is compared

Common input means the same `input_hash`. That hash is the canonical JSON of
schema, sport, away, home, team keys, UTC start, observed_at, elapsed_minutes,
score, period_label, the raw boxscore, the raw quoted odds, and the anchors.
Aliases and `observation_id` are not hashed. They are copied onto every shadow
row for joins when display names differ.

| Cohort | Meaning |
|---|---|
| Common forecast | Same hash, same player, market, and line, both `abstained: false` |
| Common recommended | Those rows, both `recommended: true`, same side |
| Engine recommended on common inputs | What each engine picked, including props the other skipped |
| Unmatched hashes | Observations only one log contains |
| Same cohort, different hash | Same game key, a different capture. Not pooled into the common-input rates |
| Alias join | Shared ESPN or Odds id, different cohort key. Reported as coverage, not as a match |

`abstained: false` is a forecast. `recommended: true` is a paper pick. Hit rate,
Brier, reliability bins, and paper ROI are computed on recommended rows.
Forecasts are counted separately so a larger priced set cannot be described as
the pick list.

Paper ROI uses the recorded American `price` and one hypothetical unit
(default 1.0), the same unit for both engines. It is not a fill and not the
`stake_units` field. Rows without a price are excluded from ROI. Push, void,
and pending are not wins or losses.

Props that share an observation share a game. The report does not compute a
significance test. Fewer than 30 common recommended props is marked sparse.

## Settlement file

```json
{
  "results": [
    {
      "cohort_key": "nfl|new york giants|dallas cowboys|2026-11-08T18:00:00Z",
      "player": "CeeDee Lamb",
      "market": "player_reception_yds",
      "status": "final",
      "final": 80
    }
  ]
}
```

`status` is `final`, `pending`, `void`, or `push`. `final` equal to the line
is a push. This tool does not invent a final.

## Muse pricing rules on that file

- `elapsed_minutes` is used as written. It is not recomputed from a clock.
- Team pace is `totalOffensivePlays` (or rushing attempts + pass attempts + sacks) or a basketball possession estimate from the boxscore. If those are absent, the row abstains `missing_real_pace`.
- Anchor kind `quoted_pregame` is the only kind with `quoted_before_start: true`. `model_projection` outranks it, matching Claude. `inferred` is used as a number and is never relabeled. Kind `pregame` is a validation error and writes nothing.
- The player's rate is that anchor divided by league plays, or a `per_play` / `per_min` entry in `priors.yaml`. The old generic 3.8 yards per play is not used. Basketball still needs that player's minutes in `priors.yaml`; 30 minutes is not assumed.
- At most 3 new `recommended` rows per cohort. A recommendation id already stored for another hash is not recommended again.
- `probability_status` is `uncalibrated`. `empirical_calibration` is false.

`nba` and `wnba` hash and join, then Muse abstains `sport_not_modeled`. Muse prices `nfl`, `cfb` (internally the NCAAF adapter), `ncaab`, and `ncaaw`.

## Calibration

`calibration.py` fits reliability bins only when every training game starts
strictly before every held-out game and no game is in both. Pending, void, and
push are not wins. A missing file stays uncalibrated. Synthetic rows can prove
the leakage checks; they are not a calibration of this model. No such settled
log exists in this branch. Do not describe the wider uncertainty interval, or
any probability cap, as an empirical calibration.

Remaining requirement: grade real shared snapshots, split by `scheduled_start`,
and only then is there anything to fit.

## Collector that is not built

There is no shared collector. Building one here would open ESPN and a paid
odds API from this branch, which replay is not allowed to do, and a second
poller would double the Odds API bill. Individual credit caps do not add up
to one shared cap.

Existing game-price caches are not this file. `state/cache/bovada_nfl_hist`
and the other line caches are pregame moneylines, spreads, and totals. They
do not contain a live player-prop `quoted_odds` blob, an ESPN `boxscore`, or
`elapsed_minutes` frozen at the same `observed_at`.

A bounded collector, one process, one odds budget, would write
`marv.prop_snapshot.v1` and nothing else the engines need:

| Field | Source |
|---|---|
| `boxscore` | ESPN summary `boxscore` object, unmodified |
| `quoted_odds` | Odds API event-odds object, unmodified, Bovada included |
| `observed_at` | Clock when those two blobs were frozen, timezone-aware |
| `elapsed_minutes` | Measured once at that clock. Replay will not recompute it |
| `period_label` | Label copied through, such as `end of Q2` |
| `score` | `{home, away}` numbers at that clock |
| `scheduled_start` | Kickoff or tip, timezone-aware |
| `away`, `home` | Display names |
| `aliases` | `espn_event_id`, `odds_api_event_id`, optional `muse_game_id` |
| `anchors` | See below |
| `observation_id` | Optional caller id. Not hashed |

Anchors:

- `quoted_pregame` only when a Bovada player line was stored from a capture whose `observed_at` is at or before `scheduled_start`.
- `model_projection` only when a separate model file already has a number for that player and market. It is not a quote.
- `inferred` only when the collector explicitly marks a non-quote fallback. Engines must not upgrade it.
- The kind `pregame` is invalid. A live line must not be stored under a pregame label.

Until that collector exists, Muse and Claude logs are comparable only where
someone has already produced this snapshot and both replays were run on it.
An AJ-selected shadow sample is not that file.

## Keys

Replay needs no key. A collector would need one odds source, not two:

- Claude live uses `ODDS_API_KEY` (The Odds API). Not required to replay a file.
- Muse's old poller used `PROPLINE_API_KEY`. This CLI does not call PropLine.
- PropLine sport key `basketball_ncaaw` was never confirmed. Do not guess it.
- Telegram tokens are unused while `LIVE_PROPS_SEND=off`.

Do not paste any of those into chat.
