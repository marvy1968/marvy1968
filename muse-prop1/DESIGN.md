# Muse Prop1 — In-Game Prop Engine — Design v1.1
**For Bovada | NFL + Top 30 NCAAF + NCAAB + NCAAW | Recommendations only — never auto-bets**

Date: 2026-11-06 (v1.1: 4-sport scope; v1.2: full engine written)
Status: FULL ENGINE WRITTEN v1.2, 13/13 tests pass (9 engine + 4 integration), dry-run verified. Log-only / shadow mode only. NOT validated on live data. Do not bet real money on this until it passes the pre-registered test in §7, per sport. Grok check still required: one real PropLine + ESPN response per sport, NCAAW key verification.

## 0. My reading of your prompt — correct me if I'm wrong
- "pods" = **odds** (Bovada live odds)
- "brovado" = **Bovada**
- "pace of the game" = live game pace: football = plays/min, basketball = possessions/min, plus time remaining, game script, fouls/minutes
- "props" = **player props**, full-game
- Scope, confirmed 2026-11-06 19:42 ET: **NFL, Top 30 NCAAF, NCAAB, and women's college basketball (NCAAW)**
  - "Top 30 NCAAF" = games where at least one team is in the Top 30 (AP Top 25 + next 5, refreshed weekly from ESPN rankings — see §10). Not all 134 FBS teams.
  - "Nccab" = **NCAAB** (men's college basketball)
  - NCAAW = women's college basketball, same basketball adapter, thinner props — see §10 warning
- "accurately" = calibrated, not guaranteed. No engine picks winners accurately in the absolute sense. The honest target is: **model probability > Bovada's no-vig implied probability by enough to beat the vig, repeatedly, on unseen games, per sport.** A pass in NFL does NOT pass NCAAF/NCAAB/NCAAW — each sport is tested separately.

## 1. What the engine does, in one loop (every 30 seconds, live games only)
```
Live stats (ESPN)  ─┐
Pregame projection ─┼─> Pace projection (sport adapter) ─> Projected final stat ─> Fair P(Over/Under)
Bovada live line   ─┘                                                              ─> Edge vs no-vig price
                                                                                        │
                                                                                  Gate / filter
                                                                                        │
                                                                                  Telegram alert (if pass)
                                                                                        └─> Log everything, grade after game
```

It does NOT place bets. Bovada has no official API and no auto-bet endpoint in this design. The alert gives you: sport, player, prop, Over/Under, line, Bovada price, model %, fair %, EV %, stake (units), and "check this line now — expires in 90s". You place it manually.

## 2. Data sources — checked 2026-11-06
| Need | Source v1 | Why / cost / catch |
|---|---|---|
| Bovada live player-prop odds | **PropLine API** — `api.prop-line.com/v1/sports/{sport_key}/odds?bookmakers=bovada` | Free tier 1,000 req/day, no card. Bovada listed for all sports + full player props. Refresh 60s, 30s in-progress. This is a third-party aggregator, NOT Bovada official. |
| — NFL | `football_nfl` — `player_pass_yds, player_pass_tds, player_rush_yds, player_reception_yds, player_receptions, player_anytime_td` | Full coverage. |
| — NCAAF | `football_ncaaf` — same football markets | Props concentrated on Power Five + marquee Group of Five games; that is exactly why the Top 30 filter fits. |
| — NCAAB | `basketball_ncaab` — `player_points, player_rebounds, player_assists, player_threes, player_steals, player_blocks, player_turnovers, player_points_rebounds, player_points_assists, player_rebounds_assists, player_points_rebounds_assists, player_double_double` | Bovada posts 90–110+ markets on big NCAAB games. |
| — NCAAW | PropLine sport key to verify on first live call (`/v1/sports` — likely `basketball_ncaaw`, NOT confirmed in docs search) | **Thinnest coverage. Do not assume it exists.** If PropLine returns no NCAAW props, that sport stays game-lines-only / log-only until a source is verified. Bovada posts far fewer women's college props, limits are lower, lines move faster. |
| Fallback odds | The Odds API / odds-api.io | Paid. Only if PropLine Bovada coverage drops. Do not scrape Bovada directly in v1 — fragile, ToS risk, bot-wall. |
| Live in-game stats | **ESPN site API** (`site.api.espn.com`, unofficial, no key) — scoreboard + summary | Free, ~30s lag. Unofficial, can break. Poll every 30s, never faster. Paths: `football/nfl`, `football/college-football`, `basketball/mens-college-basketball`, `basketball/womens-college-basketball`. |
| Pregame projection + history | NFL: nflverse via `nflreadpy`. NCAAF: CollegeFootballData.com (free, key) + cfbfastR. Basketball: SportsDataverse / Bart Torvik (prior, not live) + ESPN season stats | For the pregame mean, per-play / per-minute rates, variance, opponent adjustment. NOT live — use only as the prior. |
| Delivery | Telegram — your existing `MarvsT_bot` | Token already stored locally in `~/.credentials/telegram.env` (chmod 600). Engine reads it from env, never prints it. |

**Keys needed from you (do NOT paste in chat):** PropLine API key only, in `~/workspace/muse-prop1/.env` as `PROPLINE_API_KEY=...`. No Bovada login is needed or used. CollegeFootballData key is optional, only if you want NCAAF priors from there instead of ESPN season stats.

## 3. Pace projection — the core
This is where the old pacer was weak. Pace is not "yards so far ÷ minutes × 60". It is opportunity projection with shrinkage. Football and basketball use different adapters — same edge math, different projection.

### 3.1 Football (NFL + NCAAF) — plays
Inputs per player, per check:
- `current` — stat so far, `elapsed_min`, `remaining_min`
- `pregame_mean`, `pregame_sd`
- `team_plays_so_far`, `team_plays_per_min_live`, `team_plays_per_min_season`
- `game_script` — score diff, possession; pass rate rises when trailing, rush rate rises when leading late
- `usage_live` — targets/carries/routes so far vs expected share; snap share if available

Projected final:
```
live_pace = team_plays_so_far / elapsed_min   (capped 0.7–1.3x season pace)
blended_pace = w * live_pace + (1-w) * season_pace
  where w = elapsed_min / 60, floored 0.10, capped 0.85
projected_remaining_plays = blended_pace * remaining_min
per_play_rate = w * (stat_so_far / plays_so_far, capped 0.5–1.5x pregame) + (1-w) * pregame_per_play_rate
  + game_script adjustment (trailing by 10+ in 2H: pass +12%, rush -15%)
projected_final = current + projected_remaining_plays * per_play_rate
```

NCAAF differences (same code, different config):
- `sd_inflation: 1.35` (NFL 1.15) — tempo variance, blowouts, backups, and QB/RB rotations are much bigger in college
- Blowout gate: lead >28 in 2H, or pregame spread >21 and lead >21 at half = skip. Starters get pulled, pace collapses.
- No picks before 10 min elapsed (college clock stops more, pace is noisier early than NFL's 12-min gate)

**Early-game shrinkage (fixes old bug):** No picks before 12 min elapsed NFL / 10 min NCAAF. Before that, `w` is tiny anyway, so a hot 5 minutes cannot move the projection much. Caps found by test: 80 yds in 5 min projected 371 before caps, 317 after.

### 3.2 Basketball (NCAAB + NCAAW) — possessions + minutes
Football projects plays. Basketball projects **minutes the player will actually play × points/rebounds/assists per minute**.

Inputs per player, per check:
- `current` — stat so far, `minutes_played`, `elapsed_min` (game clock, 40 min total), `fouls`
- `pregame_mean`, `pregame_sd`, `pregame_per_min` (stat per minute played)
- `team_possessions_so_far`, `possessions_per_min_live` vs season
- `usage_live` — shot attempts / minutes so far vs season share

Projected final:
```
projected_minutes_remaining = (minutes_played / elapsed_min) * remaining_min
  — shrunk toward pregame expected minutes (w = elapsed / 40)
  — foul adjustment: 2 fouls in 1H: -25% remaining minutes; 3 fouls: -40%; 4 fouls: skip Over
  — blowout adjustment: lead >20 with <10 min left: -30% (starters sit)
per_min_rate = w * (stat_so_far / minutes_played, capped 0.5–1.5x) + (1-w) * pregame_per_min
  + pace adjustment: blended possessions/min vs season (faster game = more chances)
projected_final = current + projected_minutes_remaining * per_min_rate
```

- No picks before **8 min elapsed** (20% of a 40-min game). Basketball pace stabilises faster than football, but rotation/foul noise is high early.
- `sd_inflation: 1.25` for NCAAB, `1.35` for NCAAW (thinner data, more variance in minutes/usage).
- Markets: points, rebounds, assists, threes, PRA combos. Threes / steals / blocks use Poisson, not Normal (low counts).

### 3.3 From projection to probability (all sports)
- Yards / points / rebounds / assists (roughly normal at these volumes): `Normal(mean=projected_final, sd=sd_live)` where `sd_live = pregame_sd * sqrt(remaining_frac) * sd_inflation`
- TDs / threes / steals: Poisson with mean = projected count. Do NOT use Normal for low-count props.
- `P(Over line) = 1 - CDF(line + 0.5)` (continuity correction), `P(Under) = 1 - P(Over)`.
- Calibrate `sd` per sport first: if the model says 65% and it only hits 55% over 100+ bets, the SD is too small. Calibration is part of the test, not an afterthought — and it is per sport.

## 4. Edge vs Bovada — the only decision that matters (same for all 4 sports)
```
implied_over = 1 / decimal(over_price) ; implied_under likewise
vig = implied_over + implied_under - 1
no_vig_over = implied_over / (1 + vig)
edge_pp = model_P(Over) - no_vig_over
EV = model_P * (decimal_payout - 1) - (1 - model_P)
```

**Send a pick ONLY if ALL pass:**
1. `edge_pp >= 0.07` (7 points) AND `EV >= 0.05` (5%) — same bar for all sports to start; basketball/college may need a higher bar after calibration, never a lower one
2. Elapsed gate: NFL 12 min, NCAAF 10 min, basketball 8 min; and not in final 2 min (football) / final 2 min (basketball)
3. Line stable: same line + price within 10 cents for the last 2 polls (60s). No chasing a move.
4. Football: player not injured / snap share not collapsed (>30% drop = skip). Basketball: foul gate in §3.2, minutes share not collapsed.
5. NCAAF only: at least one team in Top 30 list (`top30_ncaaf.yaml`, refreshed weekly). NCAAB/NCAAW: no Top-30 filter to start — props themselves are the filter — but priority is ranked / Power-conference games.
6. Dedupe: no prior pick for same `(sport, game, player, market, side)` today; max 3 picks per game, max 6 per slate **per sport** (not 6 total across 4 sports — a Saturday college slate is bigger than an NFL Sunday)
7. Line re-check: at send time, re-fetch Bovada once. If line or price moved against you by >5 cents, DROP it and log "stale — dropped".

**Stake:** flat 0.5u, or 0.25 Kelly capped at 1.0u. Never more.

Why 7pp / 5%? Bovada vig on props is typically 4.5–5.5%. A 2–3pp "edge" is noise + vig. The old replay (+6u, Nov 1–2) had 83% of profit from 8 passing bets — concentration, not a stable edge.

## 5. What an alert looks like
```
🏈 LIVE PROP — NFL
Josh Allen — Over 245.5 Pass Yds @ -110 (Bovada)
Model: 64% | Fair (no-vig): 52% | Edge +12pp | EV +9.1%
Pace: 68 plays projected (season 64, live 71) | Allen 142 yds, 28 min left
Stake: 0.5u — check line now, expires ~90s
Log ID: 2026-11-06_BUF_MIA_allen_pass_yds_over_245.5
```
```
🏀 LIVE PROP — NCAAB
Cooper Flagg — Over 18.5 Points @ -115 (Bovada)
Model: 62% | Fair: 53% | Edge +9pp | EV +6.4%
Pace: 71 poss projected | 14 pts, 22 min played, 1 foul, 16 min left
Stake: 0.5u — check line now, expires ~90s
Log ID: 2026-11-06_DUKE_UNC_flagg_points_over_18.5
```
No alert without sport, log ID, model %, and EV. No "lock", no "guaranteed".

## 6. Architecture (files in ~/workspace/muse-prop1/)
| File | Job |
|---|---|
| `engine.py` | Pure math: football pace, basketball pace, Normal/Poisson fair prob, vig removal, edge, gates. No network. Unit-testable. |
| `bovada.py` | PropLine client: Bovada-only, bulk odds per sport (`football_nfl`, `football_ncaaf`, `basketball_ncaab`, `basketball_ncaaw` to verify), exact `(player, market, line)` matching. Returns timestamped snapshots. |
| `live_feed.py` | ESPN poller: scoreboard + summary for all 4 leagues → per-player current stats + clock + score + fouls/minutes (basketball). |
| `notifier.py` | Telegram sender via env token. One message per pick, rate-limited. |
| `runner.py` | 30s loop per sport: fetch → project (sport adapter) → edge → gate → dedupe → re-check → alert → log. Log-only mode = alert suppressed, log only. |
| `backtest.py` | Replay harness: replays timestamped odds + stats snapshots, as-of only. Per-sport reports. |
| `config.yaml` | Thresholds, gates, markets, bankroll, per-sport settings. Change config = new test, not a silent tweak. |
| `top30_ncaaf.yaml` | Top 30 NCAAF teams, refreshed weekly from AP/ESPN rankings. Empty template until first refresh. |
| `state/` | `picks.jsonl` (every pick + snapshot), `grades.jsonl` (after game), `dedupe.json`. Never pushed to git. |
| `.env` (not in repo) | `PROPLINE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

## 7. Pre-registered test — frozen BEFORE any live pick, PER SPORT
This follows your own rule: backtest is a screen only, a variant has to earn it in forward paper (shadow) test. And it fixes the four bugs Claude flagged in the old NFL pacer on Nov 5.

**Bugs to fix, explicitly:**
1. **Lookahead:** old code matched stats/prices up to 15 min AFTER the bet. Fix: every snapshot carries `as_of` timestamp; replay may use only data with `as_of <= bet_time - 30s`. Any use of final box score at decision time = fail the run.
2. **Exact line matching:** old code matched close lines. Fix: match `(player_id, market, line)` exactly. A 245.5 line is not a 249.5 line.
3. **Early-game shrinkage:** old code over-trusted early pace. Fix: §3 shrinkage + elapsed gates (NFL 12, NCAAF 10, basketball 8 min). Ablation: run with/without and report both.
4. **Dedupe:** old code could double-count. Fix: dedupe key `(sport, game_id, player_id, market, side)`. One pick per key per game, ever.

**Frozen test, per sport:**
- Data: replay 4 weeks per sport — timestamped PropLine Bovada snapshots + ESPN snapshots, stored before the run. NCAAF: 4 weeks of Top-30 games only. Basketball: 4 weeks of games with a Bovada prop posted.
- Split: first 2 weeks train (tune SD inflation + thresholds only), last 2 weeks test (frozen). No tuning on test.
- Costs: Bovada vig as quoted, plus 2-cent slippage on every pick.
- Pass bar (ALL, per sport): test ROI > 0% AND yield > 3% AND n >= 50 picks AND win rate within ±8pp of mean model probability (calibration) AND profit not concentrated: top 10 picks < 50% of winning profit. If it fails any, that sport stays log-only — a pass in NFL does not unlock NCAAW.
- Then: 2 weeks forward shadow (log-only, live, no money) — same bar, on fresh games. Only then discuss real stakes, and only flat 0.5u.

**What the Nov 1–2 replay does NOT prove:** 29-21, +6u, ~+12% ROI on ~50 bets, 83% from 8 bets, no live pick graded yet. That is a hypothesis, not validation. The new engine must re-run that period with the lookahead fix and report whether the +6u survives.

## 8. Risks / honest limits
- Bovada live props move in seconds and limit quickly. A 30s poll + re-check will miss some lines and drop picks. That is by design — a dropped pick beats a stale pick.
- ESPN unofficial API can break or lag. If live stats are >90s stale, skip the check, do not project from old data.
- PropLine free tier: 1,000 req/day. At 30s polling, 4 sports × bulk call = 4 calls per check = ~8,000/day if you poll all day — **over the free tier.** Budget it: poll only live games, poll each sport only while it has live games, and stagger (football 30s, basketball 45s). Hobby $9/mo (5,000/day) is the likely need for 4 sports. Do not poll per-game.
- College variance is real: blowouts, backups, foul trouble, and thin women's-college lines will kill more picks than NFL. The blowout/foul gates are mitigations, not cures.
- NCAAW is the highest-risk sport in the set: thinnest props, lowest limits, least data. It goes last in testing order.
- No model "accurately" picks props. If test ROI is ~0 after vig, the correct answer is "no edge — don't bet it", not a lower threshold.

## 9. Decisions I need from you
1. Odds source: **PropLine free** (recommended to start) or do you already have The Odds API / odds-api.io? For 4 sports you will likely need Hobby $9/mo — ok?
2. Delivery: **Telegram** (existing bot) or just here in chat for now?
3. Start in **log-only shadow mode** for the test in §7 — yes? (Recommended: yes, no real bets until a sport passes.)
4. Top 30 NCAAF definition: **AP Top 25 + next 5** (recommended) or CFP Top 25 only, or Top 30 by a different ranking?

## 10. Multi-sport notes (v1.1)
- **Testing order:** NFL first (most data, prior pacer), then NCAAF Top 30, then NCAAB, then NCAAW last. Do not test all four at once and blend the results.
- **Top 30 NCAAF:** `top30_ncaaf.yaml` is a template. Refresh weekly from ESPN `rankings` endpoint / AP poll. A game qualifies if either team is in the list that week. Do not hard-code a ranking from memory — rankings change weekly.
- **NCAAF props** are concentrated on Power Five + marquee G5 games. That matches the Top 30 filter — the filter is not cutting much prop volume, it is cutting noise.
- **NCAAB:** no Top-30 filter, but the engine will naturally only pick games where Bovada posts a prop. Expect most picks from ranked / Power-conference games.
- **NCAAW:** verify PropLine sport key first. If no key / no props, report that plainly and keep NCAAW log-only on game lines only — do not invent a feed.
