# Prop snapshot v1

Side-by-side input for Claude Prop 1 and Muse. Replay prices this file and writes shadow rows. It does not call ESPN, The Odds API, Telegram, or a broker. It does not grade. Grading a shared cohort belongs in one grader, not a second copy here.

The live collector is unchanged. `marv-live` still polls and still records the daily Odds API cap in `state/live_props/credits.json`. Replay does not read or write that file.

## Startup

`--state-dir` is required. `STATE_DIR` from the environment is ignored, so a live `.env` cannot redirect the output. The directory must not be `/opt/marv-bot` or anything inside it, and it must not be a parent of that tree (`/opt` is refused).

```bash
LIVE_PROPS_SEND=off python -m marv live-props replay \
  --snapshot fixtures/prop_snapshot_v1.sample.json \
  --state-dir /tmp/claude-prop-replay
```

Unset `LIVE_PROPS_SEND` defaults to on and replay refuses before it writes. `0`, `false`, and `no` are also explicit off tokens. Anything else is refused.

Output, replaced on each run (not appended):

`/tmp/claude-prop-replay/live_props/shadow.jsonl`

One JSON object per line. Keys are sorted. Floats from the pricer are rounded to 4 decimal places, the same rounding the live shadow log uses. That rounding is output formatting. The pricer itself is `scan_game` with these inputs.

## Input `marv.prop_snapshot.v1`

```json
{
  "schema": "marv.prop_snapshot.v1",
  "observations": [ { } ]
}
```

Each observation:

| Field | Required | Meaning |
|---|---|---|
| `sport` | yes | `nfl`, `cfb`, `nba`, `wnba`, `ncaab`, `ncaaw` |
| `away`, `home` | yes | Display names used for the cohort and for the existing team match inside the box |
| `scheduled_start` | yes | Kickoff or tip, timezone-aware ISO-8601 |
| `observed_at` | yes | When this scoreboard and these quotes were frozen. Freshness uses this clock, not the wall clock |
| `elapsed_minutes` | yes | Minutes already played. Replay does not recompute this from the clock |
| `period_label` | yes | Label copied into the shadow row, for example `end of Q2` |
| `score` | yes | `{"home": number, "away": number}` |
| `boxscore` | yes | ESPN summary `boxscore` object, the same object `parse_box` reads |
| `quoted_odds` | yes | Odds API event-odds object (`bookmakers` array). Only `key: "bovada"` is priced |
| `anchors` | yes | List, possibly empty. See kinds below |
| `aliases` | no | String-to-string source ids. Recommended keys: `espn_event_id`, `odds_api_event_id`, `muse_game_id` |
| `observation_id` | no | Caller id, copied through. Not part of the hash |

### Anchors

```json
{"player": "CeeDee Lamb", "market": "player_reception_yds", "value": 70.0, "kind": "model_projection"}
```

`kind` is one of:

- `quoted_pregame` — a real book line observed before `scheduled_start`. This is the only kind that sets `quoted_before_start` true.
- `model_projection` — a model number, not a market quote. It overrides `quoted_pregame` for the same player and market, which is the live rule.
- `inferred` — anything that was not a real pregame quote and not a model projection. It is used as the anchor number when nothing better exists. It is never stored under the kind `pregame`.

The kind `pregame` by itself is a validation error. An anchor without a quote does not invent a line.

### Cohort key

```text
sport|away_key|home_key|scheduled_start_utc
```

`away_key` / `home_key`: Unicode NFKD, drop accents, lower case, `&` to `and`, then keep letters and digits as space-separated tokens. `Dallas Cowboys` and `dallas  cowboys` share a key. This is not the fuzzy matcher the pricer uses on box-score team strings. Join on this key and on `aliases` when display names differ.

`scheduled_start` in the key and in the hash is UTC `YYYY-MM-DDTHH:MM:SSZ` with the seconds field and no microseconds.

### Input hash

`input_hash` is `sha256:` plus the hex SHA-256 of the UTF-8 bytes of this object, serialized with sorted keys, separators `,` and `:`, and `ensure_ascii` false (Python `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`).

Hashed fields: `schema`, `sport`, `away`, `home`, `away_key`, `home_key`, `scheduled_start`, `observed_at`, `elapsed_minutes`, `score` (`away`, `home`), `period_label`, `boxscore`, `quoted_odds`, `anchors`.

Not hashed: `aliases`, `observation_id`. `elapsed_minutes`, `score.home`, `score.away`, and each anchor `value` are passed through `float` before hashing, so `30` and `30.0` match. Numbers inside `boxscore` and `quoted_odds` are not coerced (`30` and `30.0` stay different there). The anchor list is sorted by player, market, kind.

Both implementations must parse the file as JSON and hash that object. Do not hash the raw file bytes.

## Output `marv.prop_shadow.v1`

Every line has:

| Field | Meaning |
|---|---|
| `schema` | `marv.prop_shadow.v1` |
| `mode` | `snapshot_replay` |
| `model`, `model_version` | `claude-prop1` |
| `cohort_key`, `game_id` | The cohort key above. `game_id` is that key, not an ESPN id |
| `observation_id` | Copied, or `""` |
| `input_hash` | Hash of this observation's inputs. Same on every row from that observation |
| `sport`, `away`, `home`, `scheduled_start`, `observed_at` | Normalized clock; names as supplied, stripped |
| `aliases` | Copied object |
| `period` | `period_label` |
| `abstained` | `false` when `scan_game` priced the quote. `true` when it did not |
| `abstain_reason` | `""`, or one of `incomplete_quote`, `stale_quote`, `market_not_modeled`, `player_not_in_box`, `no_anchor`, `not_priced` |
| `player`, `market`, `line` | The quote |
| `anchor_kind` | Winning kind, or JSON `null` when there was no anchor |
| `quoted_before_start` | `true` only when `anchor_kind` is `quoted_pregame` |

A priced row also contains the `scan_game` fields (`side`, `price`, `p`, `edge`, `ev`, `proj`, `so_far`, `anchor`, `recommended`, `reason`, and the rest). `recommended` true is a paper flag in the file. Replay does not send it.

`stale_quote` uses the same `LIVE_PROP_MAX_AGE` default (180 seconds) as the live engine, measured from `observed_at` to the quote's `last_update`.

## What this schema does not do

- It does not accept a homemade box-score table. The frozen ESPN `boxscore` is the input so the pricer sees the bytes it already parses. A second box format would be a new parser.
- It does not recompute `elapsed_minutes` from a clock.
- It does not price a book other than Bovada.
- It does not grade results. Muse's grader should consume `cohort_key`, `aliases`, `input_hash`, and `model_version` from these rows.
- It does not change `Runner._budget_ok`. Live polling still stops at the daily credit cap.
