# Pacer v2

Log-only decisions for live player props. The tool reads candidate JSONL files and writes a decision JSONL plus reports. It does not place orders, size stakes, or send alerts.

Dependencies are the Python 3 standard library, numpy, scipy, and scikit-learn. Every input and output path is a CLI flag, so the same commands run on a VM where the files live somewhere else.

Turn the whole tool off by creating a file named `OFF` or by setting an environment variable. Either one makes every command exit 0 before it reads or writes:

- `pacer_v2/OFF` next to this package, or `./pacer_v2/OFF` from the working directory
- `PACER_V2_OFF` set to any value, including empty

Remove the file and unset the variable to turn it back on.

```bash
python -m unittest discover -s pacer_v2/tests -v
```

## What a v2 decision does

The v1 probability on the row is `p_model_raw` (the probability that the quoted side hits). v2 adjusts it, blends it with the market, calibrates it, then applies gates. `action` is `bet` or `skip`. A `bet` is a log line, not an order.

1. **Market blend.** `p_blend = w * p_pacer + (1 - w) * market_mid`. `market_mid` is `(bid + ask) / 2` when both are present, otherwise `price`. `w` is fit on training rows by minimizing Brier score (`scipy.optimize.minimize_scalar`, clipped to `[0, 1]`). Below 30 graded rows, `w` stays `0.5`.
2. **Edge gate.** Bet only when calibrated `p - price >= 0.15`. `price` is the probability price of the side (or American odds, converted). The comparison uses the price you pay, not the mid.
3. **Overs fix.** Overs also need a price inside a learned band and a calibrated hit probability at or above a learned floor. Unders do not. The band and the floor are a grid search on training overs that already clear the 0.15 edge and the timing filter, maximizing ROI among cells with at least 30 bets. Until that cell exists, the prior is price in `[0.40, 0.62]` and hit probability `>= 0.58`. That prior is not a result.
4. **Minutes.** Remaining regulation minutes are scaled by a blowout factor, a foul factor, and a back-to-back factor (`0.92` when the flag is set). The change in expected production shifts `P(over)` in logit space by `delta / scale`.
5. **Opponent defense and pace.** Optional team-stats JSON. Both multipliers default to `1.0` when the file, the team, or the stat is missing. Defense below 1 suppresses the stat; pace above 1 raises it.
6. **Timing.** Skip when game elapsed is under 6 minutes, or regulation (or overtime) remaining is under 3 minutes. Elapsed of exactly 6 and remaining of exactly 3 are allowed. A missing clock does not skip the row; the decision records `game_clock_unknown`.
7. **Weekly isotonic recalibration.** `recalib` refits an isotonic map from blended `p` to the graded outcome on rows in the last `--days` (default 28) before the newest timestamp in the file. The blend weight and the overs rule are left alone. Fewer than 30 graded rows, or a one-sided window when a real map is already saved, leaves the previous map in place. The map is stored in the model JSON (`X`/`y` thresholds; applied with `numpy.interp`).

`p_pacer` is `p_model_raw` after the minutes, foul, back-to-back, defense, and pace shift. The number that faces the edge gate is that blend passed through the isotonic map.

## Minutes rules

Basketball defaults to 48 minutes and 12-minute periods (NBA / WNBA). `league` of `ncaab`, `ncaaw`, `cbb`, `wbb`, `wncaab`, or `ncaa`, or an explicit `regulation_minutes` of 40, uses 40 minutes and 20-minute halves. Football uses 60 minutes and 15-minute quarters. A period number past regulation, or a period label of `ot`, is overtime (5 minutes basketball, 10 football).

Late means 75% of regulation, or any overtime.

| Situation | Remaining-minutes factor |
|---|---|
| Basketball, late, \|margin\| >= 25 | 0.40 |
| Basketball, late, \|margin\| >= 18 | 0.65 |
| Football, late, \|margin\| >= 28 | 0.35 |
| Football, late, \|margin\| >= 17 | 0.50 |
| `role=backup` | 1.0 (blowout cut off) |
| Fouls at the foul-out limit | 0.15 |
| One foul under the limit, before 90% of the game | 0.50 |
| Two fouls under the limit, before half | 0.80 |
| Back-to-back flag | 0.92 |

The foul-out limit is 6, or 5 for a 40-minute game or a college league. `foul_limit` on the row overrides it. Football foul counts are ignored.

`margin` is the player's own team margin (positive means leading). `season_rate` at or below a per-stat cap is per minute; above the cap it is treated as per game and divided by 32 (basketball) or 60 (football). `rate_unit` of `per_min` or `per_game` skips that guess.

### Gaps

These are not modeled, and the decision's `gaps` list names which fallbacks fired:

- No substitution log, stagger, or coach tendency. Remaining minutes are the regulation clock times the factors above.
- Foul trouble is a threshold, not a free-throw or lineup model.
- Back-to-back applies only when the row sets `b2b` / `back_to_back` / `is_b2b`, or the team-stats file lists `b2b_teams` / `b2b_players`. Nothing fetches a schedule.
- A home-perspective margin that was not flipped to the player's team is invisible here.
- Overtime is a flat period length, not a possession model.
- Football minutes are game-clock minutes. There is no snap model. `role=backup` turns the blowout cut off and does not add garbage-time snaps.
- A college game with no `league` and no `regulation_minutes` is scored as 48 minutes.
- Defense and pace stay 1.0 when the lookup misses.
- A missing period and clock does not borrow a basketball player's `minutes_played`, and it does not skip the row.

## Input rows

JSONL, one object per line. Missing fields are empty, not errors. A bad line is skipped and reported on stderr. Extra fields are ignored.

| Field | Meaning |
|---|---|
| `ts` | ISO-8601 or epoch seconds/ms |
| `game_id`, `player`, `stat`, `side`, `line` | Identity. `side` is over/under. |
| `stat` | `pts` `reb` `ast` `pra` `3pm`, or `py` `ry` `recy` `rec` `td`. Aliases such as `player_pass_yds` are accepted. |
| `cur` | Stat so far |
| `minutes_played`, `period`, `clock` | Clock is `m:ss`, `h:mm:ss`, seconds if the number is >= 20, or minutes if it is under 20 |
| `fouls`, `margin`, `season_rate` | See minutes rules |
| `p_model_raw` | v1 pacer probability for the side |
| `bid`, `ask`, `price` | Probabilities in `(0, 1]`, or American odds (`<= -100` or `>= 100`) |
| `p_old` | Optional probability from the previous model. If absent, reports use `0.5 * p_model_raw + 0.5 * market_mid` |
| `result` / `outcome` / `won` | Graded win (`true`/`won`) or loss. `push` is excluded from ROI |
| `pnl` | Copied onto the decision for audit. ROI does not use it |
| `b2b`, `opponent`, `team`, `league`, `role` | Optional context |

The dedupe key is `game_id|player|stat|side|line|ts`. Scoring the same file again replaces those keys in `--out` and leaves every other key in place. Output is sorted by timestamp then key, so a second run with the same inputs and model is byte-identical.

## Team-stats file

Optional. `--team-stats` points at one JSON object:

```json
{
  "teams": {
    "BOS": {"pace": 1.04, "def": {"pts": 0.95, "reb": 1.01, "ast": 0.98, "pra": 0.96, "3pm": 0.93}}
  },
  "games": {
    "401234": {"pace": 1.08, "opp_def": {"pts": 0.97}}
  },
  "b2b_teams": ["LAL"],
  "b2b_players": ["A Player"]
}
```

Game `pace` wins over the mean of the two teams' pace factors. Game `opp_def` wins over the opponent's `def` block. A single number is used for every stat.

## Model file

JSON written by `backtest --model-out` and updated by `recalib`. Fields that matter: `w`, `w_fitted`, `overs` (`price_lo`, `price_hi`, `min_hit_p`, `fitted`), `isotonic` (`x`, `y`, `identity`), `edge_min`. With no file, score and report use `w = 0.5`, the overs prior above, and an identity isotonic map.

## Metrics

Reports compare three rules on graded, non-push rows:

- **old** — bet when `p_old - price >= 0.15`
- **v1** — bet when `p_model_raw - price >= 0.15` (no timing, overs, minutes, or blend)
- **v2** — bet when `action` is `bet`

A contract costs `price` and pays 1 if the side wins. `roi` is total profit over total cost. `roi_slip_2c` buys the same contract at `min(0.99, price + 0.02)`. `n_ge_30` is true when that system's bet count is at least 30. `backtest` also prints `v2_n>=30=yes|no` for the held-out games.

`backtest` orders games by their first timestamp, puts the first 60% of games in train and the rest in test, and never places one `game_id` in both. Weight, overs rule, and isotonic map are fit on train only, then frozen for the test report.

## Commands

```bash
python -m pacer_v2 score \
  --in /path/to/live.jsonl \
  --out /path/to/decisions.jsonl \
  --model /path/to/model.json \
  --team-stats /path/to/team_stats.json

python -m pacer_v2 report \
  --in /path/to/decisions.jsonl \
  --out /path/to/report.json

python -m pacer_v2 backtest \
  --in /path/to/graded.jsonl \
  --model-out /path/to/model.json \
  --out /path/to/backtest.json

python -m pacer_v2 recalib \
  --in /path/to/graded.jsonl \
  --model /path/to/model.json \
  --days 28
```

`report` accepts either a decision log or raw candidates. Raw candidates are scored with `--model` first. A decision log is used as written.

## Cron

Paths below match a VM checkout at `/opt/marv-bot`. Change them to wherever the JSONL actually lives. The process is log-only; the OFF file is the kill switch if a run should stay quiet.

```cron
# Every 10 minutes: fold new candidate rows into the decision log.
*/10 * * * * cd /opt/marv-bot && python -m pacer_v2 score --in /opt/marv-bot/state/pacer/live.jsonl --out /opt/marv-bot/state/pacer_v2/decisions.jsonl --model /opt/marv-bot/state/pacer_v2/model.json --team-stats /opt/marv-bot/state/pacer_v2/team_stats.json >> /opt/marv-bot/state/pacer_v2/score.log 2>&1

# Weekly isotonic refit. 13:15 UTC is 08:15 Eastern Standard Time (09:15 during daylight time).
15 13 * * 1 cd /opt/marv-bot && python -m pacer_v2 recalib --in /opt/marv-feed/reports/pacer_v2/graded_nfl.jsonl --model /opt/marv-bot/state/pacer_v2/model.json --days 28 --team-stats /opt/marv-bot/state/pacer_v2/team_stats.json >> /opt/marv-bot/state/pacer_v2/recalib.log 2>&1
```

Fit the first model with `backtest` (or leave the defaults in place) before the timer depends on it. Nothing in this folder is a proven betting record. The report prints the counts in the file it was given.
