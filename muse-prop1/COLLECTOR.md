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

## Collector

`collector.py` is the only Odds API caller in this experiment. It writes one
`marv.prop_snapshot.v1` file and gives that same path to Muse replay and to
Claude replay. It does not write `recs.json` or anything under `/opt/marv-bot`.
Game-price caches of moneylines and totals are not inputs.

Coverage is NFL and NBA only:

| Sport | Odds API sport key | Markets |
|---|---|---|
| nfl | `americanfootball_nfl` | `player_pass_yds`, `player_rush_yds`, `player_reception_yds`, `player_receptions` |
| nba | `basketball_nba` | `player_points`, `player_rebounds`, `player_assists`, `player_threes` |

ESPN scoreboard and summary use `site.api.espn.com` and no key. Odds calls are
`GET /v4/sports/{sport}/events` (documented free in `marv/props/odds.py`) and
`GET /v4/sports/{sport}/events/{id}/odds?bookmakers=bovada`. Current event odds
cost 1 credit per market per region. This collector uses one region, so each
props call reserves 4 credits before the request. Historical odds are not
called. The events list reserves 0 and still requires `x-requests-last`. A
missing header, auth failure, quota failure, or any difference between the
reserved cost and `x-requests-last` (including an events call reserved at 0)
keeps the reservation, writes the day block, and does not make another call.
A larger header is added to `used` before that block. A smaller header does
not refund.

The cap is 100 credits per UTC day in `{state}/usage.json`, not 100 requests.
`used` never decreases. A held reservation left by a crash fails the next run
closed before any request. Default bounds are 4 games and 4 billable calls
per one-shot. Replay children do not inherit the collector environment. They
receive PATH, locale, and Python runtime names plus `LIVE_PROPS_SEND=off`.

Games pair on the same canonical home, away, and sport when the two listed
starts are within 30 minutes. That window is the maximum. A shared provider
id does not pair different teams. Two candidates in the window, or a listing
more than 30 minutes away, stay unmatched. The cohort clock written on the
snapshot is ESPN's scheduled start. The Odds start, the offset in seconds,
and `providers_agree_on_start` are stored beside it. A non-zero offset is not
described as agreement. A pregame quote is kept only when ESPN status is
`pre` and the capture is before the earlier of the two starts.

Billable props calls are chosen only after those free lists. Live games that
already have a pre-start quote come first, alternating NFL and NBA. A new
pregame quote is bought once, and only when the game starts within 60 minutes.
A game further out is logged `outside_prestart_window` and is not billed.
The same quarter is not billed again; the next quarter is a new observation.
A run with nothing eligible spends no props credits.

`quoted_pregame` is stored only when `observed_at` is strictly before
`scheduled_start`. The later live response stays in `quoted_odds` and is not
copied into anchors. This collector does not read a projection file and does
not write `model_projection` or `inferred`. Without a stored pregame quote,
both engines abstain `no_anchor` instead of using a generic rate. If the raw
box has no team plays or possessions, Muse abstains `missing_real_pace`.
`elapsed_minutes` is measured once from the ESPN clock at capture.

`mode` must be `log_only`, `telegram_enabled` false, and `live_props_send`
the string `off`. The process env `LIVE_PROPS_SEND` must also be off.
`ODDS_API_KEY` is read from the environment and is not printed. The Claude
subprocess does not receive that key, a PropLine key, or a Telegram token.
`claude_code_dir` and `claude_python` are required before any credit is spent.
Paths containing `install.sh` or `vm_link` are refused. NCAAW and PropLine are
not called.

```bash
export LIVE_PROPS_SEND=off
python3 collector.py --once \
  --state-dir /var/lib/prop1-collector \
  --archive /var/lib/prop1-archive
python3 collector.py --daily-summary \
  --state-dir /var/lib/prop1-collector \
  --archive /var/lib/prop1-archive \
  --date 2026-11-08
python3 collector.py --grade \
  --state-dir /var/lib/prop1-collector \
  --archive /var/lib/prop1-archive \
  --snapshot /var/lib/prop1-archive/snapshots/<file>.json \
  --muse-shadow /var/lib/prop1-archive/by-hash/<digest>/muse/shadow.jsonl \
  --claude-shadow /var/lib/prop1-archive/by-hash/<digest>/claude/shadow.jsonl
python3 collector.py --regrade \
  --state-dir /var/lib/prop1-collector \
  --archive /var/lib/prop1-archive \
  --snapshot /var/lib/prop1-archive/snapshots/<file>.json \
  --final /var/lib/prop1-archive/finals/<hash>.json \
  --muse-shadow /var/lib/prop1-archive/by-hash/<digest>/muse/shadow.jsonl \
  --claude-shadow /var/lib/prop1-archive/by-hash/<digest>/claude/shadow.jsonl
```

`--once` holds `collector.lock` and does not rewrite an archived snapshot.
`--grade` calls ESPN only and saves the raw final box. `--regrade` and
`--daily-summary` do not open a socket. Settlements are `final`, `pending`,
or `void`. Comparison uses recorded prices and a hypothetical 1.0 unit. No
calibration is fit. The daily summary at
`/var/lib/prop1-archive/daily/<date>/summary.json` has `live_ready: false`.

A run that never captured a pre-start quote still archives the live box and
the live quote, and both engines abstain. That is not a ready betting run.

## Keys

`ODDS_API_KEY` is the only secret the collector reads, and only from the
environment. Replay of an existing file does not need it. PropLine and
`basketball_ncaaw` are not used. Telegram tokens are not used while
`LIVE_PROPS_SEND=off`. Do not paste keys into chat.
