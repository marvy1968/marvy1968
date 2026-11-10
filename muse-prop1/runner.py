"""Muse Prop1 — runner. One cycle (--once), log-only by default.
Order matters (fixes old pacer bugs):
1. Fetch stats + odds with as_of timestamps. Drop if stats >90s stale.
2. Project (engine.py, sport adapter) — shrinkage + elapsed gate.
   Rate-only (pace-off rework, pace-off-test-spec-2026-11-09): the live
   projection uses the live RATE at SEASON pace (config use_live_pace=false);
   the pace ratio is 1.0. The projection is then ANCHORED to the current
   posted line (Grok flaw 1) before any edge is computed.
3. Edge vs no-vig — gates in config.yaml.
4. Dedupe (sport, game, player, market, side) — one ever.
5. RE-CHECK PropLine once at send time — drop if line/price moved against you.
6. Log pick + snapshot to state/picks.jsonl BEFORE any alert.
7. Alert only if mode == 'alert' (Telegram). mode == 'log_only' logs only.
Never auto-bets. Never uses final box score at decision time. Never places
an order on any sportsbook — there is no code path that does. No code in
Prop1 fetches from, logs into, or places bets with Bovada or any sportsbook;
odds come from PropLine (Odds API) only.

Prior rule (Grok flaw 2): the prior mean is the FIRST-SEEN PropLine line
frozen BEFORE the game starts. Run `runner.py --freeze-priors` on a cron
before the slate to freeze pregame lines. evaluate_line REJECTS a prior
that was not frozen pregame (skip "prior_not_pregame") unless a
priors.yaml override exists for that player/market.

Quota (Grok flaw 3): every PropLine fetch is reserved against the budgets
in config.yaml (propline_daily_budget / propline_monthly_budget) before the
request is sent. Over budget -> QuotaExceeded, the sport is skipped and the
run logs it. Polling is 300s per sport, only while that sport has live games.

Usage:
  python3 runner.py --once                       # one cycle, all sports in config
  python3 runner.py --once --sport nfl          # NFL only
  python3 runner.py --once --dry-run            # fixtures/, no network, no Telegram
  python3 runner.py --once --mode alert         # overrides config mode (still log-first)
  python3 runner.py --loop --interval 30        # repeat until stopped (use cron instead in prod)
  python3 runner.py --freeze-priors             # freeze pregame lines, then exit
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from engine import (PaceInput, BasketballPaceInput, project_final,
                    project_basketball_final, prob_over_yards, prob_over_td,
                    american_to_implied, remove_vig, evaluate_pick, passes_gates,
                    anchor_projection, SPORTS)
from propline import (fetch_propline_props, extract_propline_lines, find_line,
                      configure_bookmakers, configure_quota)
from quota import QuotaExceeded, QuotaTracker
from live_feed import fetch_live_games, fetch_game_summary, parse_scoreboard, parse_summary
from priors import (load_priors_yaml, get_prior, freeze_first_line,
                    frozen_prior, is_pregame_frozen, parse_commence_time)
from notifier import send_telegram, format_pick

ROOT = Path(__file__).parent
STATE = ROOT / "state"
STATE.mkdir(exist_ok=True)

MARKET_LABEL = {
    "player_pass_yds": "Pass Yds", "player_pass_tds": "Pass TDs",
    "player_rush_yds": "Rush Yds", "player_reception_yds": "Receiving Yds",
    "player_receptions": "Receptions", "player_anytime_td": "Anytime TD",
    "player_points": "Points", "player_rebounds": "Rebounds",
    "player_assists": "Assists", "player_threes": "Threes",
    "player_points_rebounds": "Pts+Reb", "player_points_assists": "Pts+Ast",
    "player_rebounds_assists": "Reb+Ast",
    "player_points_rebounds_assists": "Pts+Reb+Ast",
}

# Which player-stat key maps to each market
MARKET_STAT = {
    "player_pass_yds": "pass_yds", "player_pass_tds": "pass_tds",
    "player_rush_yds": "rush_yds", "player_reception_yds": "rec_yds",
    "player_receptions": "receptions", "player_anytime_td": "rush_tds",
    "player_points": "points", "player_rebounds": "rebounds",
    "player_assists": "assists", "player_threes": "threes",
    "player_points_rebounds": "pr", "player_points_assists": "pa",
    "player_rebounds_assists": "ra", "player_points_rebounds_assists": "pra",
}
# Anytime TD is rush+rec TDs in our parser (pass TDs are the QB's, not the runner's)
def current_stat(player_row: dict, market: str) -> float:
    if market == "player_anytime_td":
        return player_row.get("rush_tds", 0) + player_row.get("rec_tds", 0)
    return player_row.get(MARKET_STAT[market], 0.0)

def load_config(path: Path = ROOT / "config.yaml") -> dict:
    return yaml.safe_load(path.read_text())

def load_top30(path: Path = ROOT / "top30_ncaaf.yaml") -> set[str]:
    """Return the set of ESPN displayNames for the Top-30 NCAAF teams.
    The yaml lists AP poll spellings (teams:) and maps each to ESPN's
    displayName (espn_display_names:). The runner matches against ESPN
    displayNames because that is what the scoreboard returns — matching AP
    spellings against ESPN names silently dropped every game (Grok flaw 4).
    If a team has no mapping entry, its AP spelling is used as-is and the
    caller logs a warning (see load_top30_with_warnings)."""
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return set()
    teams = data.get("teams") or []
    mapping = data.get("espn_display_names") or {}
    return {mapping.get(t, t) for t in teams}

def load_top30_with_warnings(path: Path = ROOT / "top30_ncaaf.yaml") -> tuple[set[str], list[str]]:
    """Like load_top30, plus the list of AP names with no ESPN mapping."""
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return set(), []
    teams = data.get("teams") or []
    mapping = data.get("espn_display_names") or {}
    missing = [t for t in teams if t not in mapping]
    return {mapping.get(t, t) for t in teams}, missing

def dedupe_key(sport, game_id, player, market, side) -> str:
    return f"{sport}|{game_id}|{player}|{market}|{side}"

def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default

def already_picked(key: str, state_dir: Path = STATE) -> bool:
    return key in _load_json(state_dir / "dedupe.json", [])

def mark_picked(key: str, state_dir: Path = STATE):
    p = state_dir / "dedupe.json"
    seen = _load_json(p, [])
    if key not in seen:
        seen.append(key)
        p.write_text(json.dumps(seen))

def log_pick(pick: dict, state_dir: Path = STATE):
    with open(state_dir / "picks.jsonl", "a") as f:
        f.write(json.dumps({**pick, "logged_at": time.time()}) + "\n")

def log_skip(reason: str, ctx: dict, state_dir: Path = STATE):
    with open(state_dir / "skips.jsonl", "a") as f:
        f.write(json.dumps({"reason": reason, "ctx": ctx, "at": time.time()}) + "\n")

def configure_odds(cfg: dict, state_dir: Path = STATE) -> None:
    """Wire config.yaml into the PropLine client: the bookmaker allowlist
    and the quota tracker. Called once per run_once / freeze cycle."""
    configure_bookmakers(cfg.get("odds_bookmakers") or None)
    try:
        configure_quota(QuotaTracker(
            state_dir,
            daily_budget=int(cfg.get("propline_daily_budget", 500)),
            monthly_budget=int(cfg.get("propline_monthly_budget", 1000))))
    except Exception as e:
        print(f"quota tracker unavailable ({e}) — PropLine calls unenforced")
        configure_quota(None)

def evaluate_line(sport: str, line_row: dict, game: dict, cfg: dict,
                  priors_yaml: dict, state_dir: Path = STATE,
                  require_pregame_prior: bool = True) -> dict | None:
    """Evaluate one PropLine line against one game state. Returns a pick dict
    or None (with a skip logged). Pure evaluation — no network, no alert.
    require_pregame_prior=False is for dry-run/fixtures only (the fixture
    line is a pregame snapshot by construction); production always requires
    a pregame-frozen prior."""
    scfg = cfg.get(sport, {}) if sport != "ncaaf" else cfg.get("ncaaf", {})
    if sport == "ncaaf":
        scfg = cfg.get("ncaaf", {})
    else:
        scfg = cfg.get(sport, {})
    total_min = SPORTS[sport]["total_min"]
    min_elapsed = scfg.get("min_elapsed_minutes", SPORTS[sport]["min_elapsed"])
    sd_inf = scfg.get("sd_inflation", SPORTS[sport]["sd_inflation"])
    elapsed = game["elapsed_min"]
    if elapsed < min_elapsed:
        log_skip("too_early", {"sport": sport, "game": game["game_id"], "elapsed": elapsed}, state_dir)
        return None
    if game["remaining_min"] <= cfg.get("no_pick_final_minutes", 2):
        log_skip("too_late", {"sport": sport, "game": game["game_id"]}, state_dir)
        return None
    player = line_row["player"]
    market = line_row["market"]
    prow = game["players"].get(player)
    if prow is None:
        log_skip("no_player_stats", {"sport": sport, "game": game["game_id"], "player": player}, state_dir)
        return None
    cur = current_stat(prow, market)
    # Prior: first-seen line frozen (never the live line). The freeze records
    # frozen_at + commence_time; the gate below verifies it was pregame.
    first = freeze_first_line(state_dir, sport, game["game_id"], player, market,
                              line_row["line"],
                              commence_time=line_row.get("commence_time"))
    has_override = bool(priors_yaml and player in priors_yaml
                        and market in priors_yaml[player])
    if require_pregame_prior and not has_override:
        entry = frozen_prior(state_dir, sport, game["game_id"], player, market)
        if not is_pregame_frozen(entry):
            log_skip("prior_not_pregame",
                     {"sport": sport, "game": game["game_id"], "player": player,
                      "market": market, "frozen_at": (entry or {}).get("frozen_at"),
                      "commence_time": (entry or {}).get("commence_time")},
                     state_dir)
            return None
    prior = get_prior(sport, player, market, first, priors_yaml)
    if prior["mean"] is None:
        return None
    # Rate-only rework (Grok flaw 5): the live projection uses the live rate
    # at SEASON pace. config use_live_pace=false is the live default.
    use_live_pace = bool(cfg.get("use_live_pace", False))
    anchor_w = float(cfg.get("line_anchor_weight", 0.5))
    # Project, then anchor the projection to the CURRENT posted line (Grok
    # flaw 1) before any probability or edge is computed.
    if sport in ("nfl", "ncaaf"):
        plays = game.get("team_plays")
        if plays is None:
            plays = int(elapsed * scfg.get("season_plays_per_min", 1.05))
        # Blowout gate (NCAAF)
        lead = abs(game["home_score"] - game["away_score"])
        if sport == "ncaaf" and elapsed > 30 and lead >= scfg.get("blowout_lead_skip", 28):
            log_skip("blowout", {"sport": sport, "game": game["game_id"], "lead": lead}, state_dir)
            return None
        proj_raw, sd = project_final(PaceInput(
            current=cur, elapsed_min=elapsed, total_min=total_min,
            pregame_mean=prior["mean"], pregame_sd=prior["sd"],
            pregame_per_play=prior["rate"], plays_so_far=plays,
            season_plays_per_min=scfg.get("season_plays_per_min", 1.05),
            sd_inflation=sd_inf, use_live_pace=use_live_pace))
        proj = anchor_projection(proj_raw, line_row["line"], anchor_w)
        pace_note = f"{plays} plays, {proj:.0f} projected (raw {proj_raw:.0f})"
    else:
        if prow.get("fouls", 0) >= 4:
            log_skip("foul_trouble_4", {"sport": sport, "player": player}, state_dir)
            return None
        minutes = prow.get("minutes", 0)
        if minutes <= 0:
            log_skip("no_minutes", {"sport": sport, "player": player}, state_dir)
            return None
        lead = game["home_score"] - game["away_score"]
        # Real live pace: game possessions from the ESPN boxscore team block
        # (live_feed.parse_summary). Only needed when live pace is actually
        # used (use_live_pace=True); the rate-only default (pace ratio 1.0)
        # does not need possessions, so a missing team block is not a skip
        # there. NEVER the elapsed*season-pace estimate — that made the pace
        # ratio exactly 1.0 (a no-op). Fail-closed if the summary had no
        # parseable team totals AND live pace is on.
        poss = (game.get("possessions") or {}).get("game")
        if use_live_pace and poss is None:
            log_skip("no_team_possessions", {"sport": sport, "game": game["game_id"]}, state_dir)
            return None
        proj_raw, sd = project_basketball_final(BasketballPaceInput(
            current=cur, minutes_played=minutes, elapsed_min=elapsed, total_min=total_min,
            pregame_mean=prior["mean"], pregame_sd=prior["sd"],
            pregame_per_min=prior["rate"], pregame_minutes=prior["pregame_minutes"] or 30,
            fouls=int(prow.get("fouls", 0)), lead=lead,
            season_poss_per_min=scfg.get("season_poss_per_min", 1.75),
            poss_so_far=int(round(poss)) if poss is not None else 0,
            sd_inflation=sd_inf, use_live_pace=use_live_pace))
        proj = anchor_projection(proj_raw, line_row["line"], anchor_w)
        pace_note = (f"{minutes:.0f} min played, {proj:.1f} projected "
                     f"(raw {proj_raw:.1f}), {int(prow.get('fouls',0))} fouls, "
                     f"{poss:.0f} poss" if poss is not None else
                     f"{minutes:.0f} min played, {proj:.1f} projected "
                     f"(raw {proj_raw:.1f}), {int(prow.get('fouls',0))} fouls")
    # Probability — low-count markets (TDs / threes) use Poisson(lambda=proj),
    # everything else uses Normal(mean=proj, sd=sd). The projection here is
    # already line-anchored, so the probability is vs the anchored mean.
    if market in ("player_anytime_td", "player_pass_tds", "player_threes"):
        from engine import poisson_pmf
        lam = max(0.0, proj)
        k = int(line_row["line"])  # line 1.5 -> need >= 2; line 0.5 -> need >= 1
        p_over = 1.0 - sum(poisson_pmf(i, lam) for i in range(k + 1))
    else:
        p_over = prob_over_yards(proj, sd, line_row["line"])
    # Evaluate both sides, keep the better one
    candidates = []
    for side, price in (("over", line_row["over_price"]), ("under", line_row["under_price"])):
        model_p = p_over if side == "over" else 1 - p_over
        imp_over = american_to_implied(line_row["over_price"])
        imp_under = american_to_implied(line_row["under_price"])
        fair_over, fair_under = remove_vig(imp_over, imp_under)
        fair = fair_over if side == "over" else fair_under
        ev = evaluate_pick(model_p, price, side)["ev"]
        ok, why = passes_gates(model_p, fair, ev,
                               min_edge_pp=cfg.get("min_edge_pp", 0.07),
                               min_ev=cfg.get("min_ev", 0.05))
        candidates.append({"side": side, "price": price, "model_p": model_p,
                           "fair_p": fair, "ev": ev, "ok": ok, "why": why})
    best = max(candidates, key=lambda c: c["model_p"] - c["fair_p"])
    if not best["ok"]:
        log_skip("no_edge", {"sport": sport, "player": player, "market": market,
                             "model_p": round(best["model_p"], 3), "fair_p": round(best["fair_p"], 3),
                             "ev": round(best["ev"], 3), "why": best["why"]}, state_dir)
        return None
    pick = {
        "engine": "muse-prop1", "sport": sport, "game_id": game["game_id"],
        "home_team": line_row.get("home_team"), "away_team": line_row.get("away_team"),
        "player": player, "market": market, "market_label": MARKET_LABEL.get(market, market),
        "side": best["side"], "line": line_row["line"], "odds": best["price"],
        "odds_source": "propline",
        "bookmakers": line_row.get("bookmakers"),
        "model_p": round(best["model_p"], 4), "fair_p": round(best["fair_p"], 4),
        "edge_pp": round(best["model_p"] - best["fair_p"], 4), "ev": round(best["ev"], 4),
        "projected_final": round(proj, 2), "projected_raw": round(proj_raw, 2),
        "line_anchor_weight": anchor_w, "use_live_pace": use_live_pace,
        "current": cur,
        "elapsed_min": round(elapsed, 1), "pace_note": pace_note,
        "stake_units": cfg.get("flat_units", 0.5),
        "log_id": f"{sport}_{game['game_id']}_{player}_{market}_{best['side']}_{line_row['line']}",
        "odds_as_of": line_row.get("as_of"), "stats_as_of": game.get("as_of"),
    }
    return pick

def freeze_priors(cfg: dict, sports: list[str], state_dir: Path = STATE) -> dict:
    """Pregame freeze (Grok flaw 2): fetch PropLine odds for every configured
    sport and freeze the line for each (sport, game, player, market) whose
    commence_time is still in the future. Never overwrites an existing
    frozen line. Run on a cron BEFORE the slate (morning / pregame).
    Returns {"frozen": n_new, "already": n_existing, "started": n_skipped,
    "errors": [...]}. Each sport costs one PropLine call (quota applies)."""
    configure_odds(cfg, state_dir)
    now = time.time()
    counts = {"frozen": 0, "already": 0, "started": 0, "errors": []}
    for sport in sports:
        sport = "ncaaf" if sport == "ncaaf_top30" else sport
        try:
            odds_payload = fetch_propline_props(sport)
        except QuotaExceeded as e:
            counts["errors"].append(f"{sport}: quota exceeded ({e})")
            print(f"[{sport}] freeze skipped: quota exceeded ({e})")
            continue
        except Exception as e:
            counts["errors"].append(f"{sport}: {e}")
            print(f"[{sport}] freeze failed: {e}")
            continue
        for row in extract_propline_lines(odds_payload, sport):
            commence = parse_commence_time(row.get("commence_time"))
            if commence is None:
                counts["started"] += 1
                continue
            if commence <= now:
                counts["started"] += 1
                continue
            key = f"{sport}|{row['game_id']}|{row['player']}|{row['market']}"
            if frozen_prior(state_dir, sport, row["game_id"], row["player"],
                            row["market"]) is not None:
                counts["already"] += 1
                continue
            freeze_first_line(state_dir, sport, row["game_id"], row["player"],
                              row["market"], row["line"],
                              commence_time=row.get("commence_time"))
            counts["frozen"] += 1
    print(f"freeze-priors done: {counts}")
    return counts

def run_once(cfg: dict, sports: list[str], mode: str, dry_run: bool = False,
             state_dir: Path = STATE) -> list[dict]:
    """One cycle. Returns picks logged (before any alert)."""
    configure_odds(cfg, state_dir)
    priors_yaml = load_priors_yaml(ROOT / "priors.yaml")
    top30, top30_missing = load_top30_with_warnings()
    if top30_missing:
        print(f"[ncaaf] WARNING: {len(top30_missing)} Top-30 teams have no "
              f"ESPN displayName mapping and fall back to AP spelling: "
              f"{', '.join(top30_missing)}")
    picks: list[dict] = []
    picks_this_slate: dict[str, int] = {}
    for sport in sports:
        # "ncaaf_top30" is the config label for Top-30 NCAAF; the engine
        # key is "ncaaf" (the Top-30 filter applies via top30_ncaaf.yaml).
        # Without this, the fetch raises ValueError and the sport is
        # silently skipped — a default run would poll only three sports.
        sport = "ncaaf" if sport == "ncaaf_top30" else sport
        if sport == "ncaaw":
            # Verify key first — if list_sports fails / no ncaaw, skip with a log
            pass
        try:
            if dry_run:
                import json as _json
                odds_payload = _json.loads((ROOT / "fixtures" / f"propline_{sport}.json").read_text())
                odds_payload["as_of"] = time.time()
                sb_payload = _json.loads((ROOT / "fixtures" / f"espn_{sport}_scoreboard.json").read_text())
                sb_payload["as_of"] = time.time()
                games_live = parse_scoreboard(sb_payload, sport)
                lines = extract_propline_lines(odds_payload, sport)
                # Dry-run: use the fixture summary for the one game in the fixture
                summaries = {}
                sum_path = ROOT / "fixtures" / f"espn_{sport}_summary.json"
                if sum_path.exists():
                    summaries[games_live[0]["game_id"]] = parse_summary(
                        _json.loads(sum_path.read_text()), sport) if games_live else None
            else:
                # FREE-TIER GATE: ESPN is free/keyless — check live games
                # FIRST and skip the PropLine odds call entirely when this
                # sport has nothing live. The odds call is one bulk call
                # per sport, so a wasted call is a whole poll's quota.
                sb_payload = fetch_live_games(sport)
                games_live = parse_scoreboard(sb_payload, sport)
                if not games_live:
                    print(f"[{sport}] no live games — skipping PropLine odds")
                    continue
                odds_payload = fetch_propline_props(sport)
                lines = extract_propline_lines(odds_payload, sport)
                summaries = {}
        except QuotaExceeded as e:
            print(f"[{sport}] PropLine quota exceeded — skipping cycle: {e}")
            log_skip("propline_quota", {"sport": sport, "error": str(e)}, state_dir)
            continue
        except Exception as e:
            print(f"[{sport}] fetch failed, skipping cycle: {e}")
            continue
        if not games_live:
            continue
        for gl in games_live:
            # NCAAF Top-30 filter — gl home/away are ESPN displayNames,
            # top30 is the mapped ESPN displayName set (Grok flaw 4).
            if sport == "ncaaf" and top30:
                if gl.get("home") not in top30 and gl.get("away") not in top30:
                    continue
            if picks_this_slate.get(sport, 0) >= cfg.get("max_picks_per_slate_per_sport", 6):
                continue
            try:
                if dry_run:
                    game = summaries.get(gl["game_id"])
                    if game is None:
                        continue
                    game["elapsed_min"] = gl["elapsed_min"]
                    game["remaining_min"] = gl["remaining_min"]
                else:
                    # Stale check: scoreboard as_of vs now
                    if gl.get("as_of") and time.time() - gl["as_of"] > cfg.get("max_stale_stats_seconds", 90):
                        continue
                    summary = fetch_game_summary(sport, gl["game_id"])
                    game = parse_summary(summary, sport)
                    game["elapsed_min"] = gl["elapsed_min"]
                    game["remaining_min"] = gl["remaining_min"]
                    if not game["players"]:
                        log_skip("no_player_stats_game", {"sport": sport, "game": gl["game_id"]}, state_dir)
                        continue
            except Exception as e:
                print(f"[{sport} {gl['game_id']}] summary failed: {e}")
                continue
            for row in lines:
                if str(row["game_id"]) != str(gl["game_id"]):
                    continue
                # Dry-run fixtures are pregame snapshots by construction, so
                # the pregame-prior gate is bypassed there. Production always
                # requires a pregame-frozen prior.
                pick = evaluate_line(sport, row, game, cfg, priors_yaml, state_dir,
                                     require_pregame_prior=not dry_run)
                if not pick:
                    continue
                # Dedupe
                key = dedupe_key(sport, pick["game_id"], pick["player"], pick["market"], pick["side"])
                if already_picked(key, state_dir):
                    continue
                # Re-check PropLine once
                try:
                    if dry_run:
                        recheck = row
                    else:
                        recheck_payload = fetch_propline_props(sport)
                        recheck_lines = extract_propline_lines(recheck_payload, sport)
                        recheck = find_line(recheck_lines, pick["game_id"], pick["player"],
                                            pick["market"], pick["line"])
                    if recheck is None:
                        log_skip("recheck_line_gone", {"pick": pick["log_id"]}, state_dir)
                        continue
                    new_price = recheck["over_price"] if pick["side"] == "over" else recheck["under_price"]
                    # Price moved against us by >5 cents (American odds worse by 5+)
                    if pick["side"] == "over" and new_price < pick["odds"] - 0:
                        # American odds: -110 -> -115 is worse by 5. Compare directly.
                        if pick["odds"] - new_price >= cfg.get("max_price_move_cents_against", 5):
                            log_skip("recheck_price_moved", {"pick": pick["log_id"], "old": pick["odds"], "new": new_price}, state_dir)
                            continue
                    if pick["side"] == "under" and new_price < pick["odds"]:
                        if pick["odds"] - new_price >= cfg.get("max_price_move_cents_against", 5):
                            log_skip("recheck_price_moved", {"pick": pick["log_id"], "old": pick["odds"], "new": new_price}, state_dir)
                            continue
                    pick["odds"] = new_price
                except QuotaExceeded as e:
                    print(f"[{sport}] re-check skipped (quota): {e}")
                    log_skip("recheck_quota", {"pick": pick["log_id"]}, state_dir)
                    continue
                except Exception as e:
                    print(f"[{sport}] re-check failed, dropping pick {pick['log_id']}: {e}")
                    continue
                # Log BEFORE alert
                log_pick(pick, state_dir)
                mark_picked(key, state_dir)
                picks.append(pick)
                picks_this_slate[sport] = picks_this_slate.get(sport, 0) + 1
                if picks_this_slate[sport] >= cfg.get("max_picks_per_game", 3):
                    # per-game cap is checked via picks_this_slate per sport in v1;
                    # a stricter per-game count lives in the log — see DESIGN §4
                    pass
                if mode == "alert":
                    try:
                        send_telegram(format_pick(pick))
                    except Exception as e:
                        print(f"Telegram failed for {pick['log_id']}: {e} (pick is logged)")
    return picks

def _poll_interval_seconds(sport: str, cfg: dict) -> int:
    """Live PropLine poll interval for one sport (seconds).
    Football uses poll_seconds, basketball uses basketball_poll_seconds.
    A sport is only polled while it has live games (see run_once)."""
    if sport in ("ncaab", "ncaaw"):
        return int(cfg.get("basketball_poll_seconds", 300))
    return int(cfg.get("poll_seconds", 300))


def main():
    ap = argparse.ArgumentParser(prog="muse-prop1")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--loop", action="store_true", help="repeat until stopped")
    ap.add_argument("--interval", type=int, default=30,
                    help="loop tick seconds (default 30); per-sport PropLine "
                         "pace comes from config.yaml poll_seconds / "
                         "basketball_poll_seconds")
    ap.add_argument("--sport", default=None, help="nfl | ncaaf | ncaab | ncaaw")
    ap.add_argument("--mode", default=None, help="log_only | alert (overrides config)")
    ap.add_argument("--dry-run", action="store_true", help="use fixtures/, no network, no Telegram")
    ap.add_argument("--freeze-priors", action="store_true",
                    help="freeze pregame PropLine lines for all sports, then exit. "
                         "Run on a cron BEFORE the slate (morning / pregame).")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    mode = args.mode or cfg.get("mode", "log_only")
    if mode not in ("log_only", "alert"):
        print(f"bad mode {mode!r}, use log_only or alert"); return 2
    sports = [args.sport] if args.sport else list(cfg.get("sports", ["nfl"]))
    # Lock file — never overlap runs
    lock = STATE / "runner.lock"
    if lock.exists():
        print("runner.lock exists — another run is active, exiting"); return 0
    lock.write_text(str(time.time()))
    try:
        if args.freeze_priors:
            freeze_priors(cfg, sports)
            return 0
        if args.loop:
            # Per-sport pacing: each sport is polled on its own clock
            # (config poll_seconds / basketball_poll_seconds) and only
            # while it has live games. The tick just wakes the loop to
            # check which sports are due — it is not the poll pace.
            tick = max(5, args.interval)
            next_poll = {s: 0.0 for s in sports}
            while True:
                now = time.time()
                due = [s for s in sports if now >= next_poll[s]]
                if due:
                    picks = run_once(cfg, due, mode, dry_run=args.dry_run)
                    print(f"{time.strftime('%H:%M:%S')} cycle done: {len(picks)} pick(s) logged, mode={mode}")
                    for p in picks:
                        print(f"  {p['sport']} {p['player']} {p['side']} {p['line']} {p['market_label']} @{p['odds']} model {p['model_p']:.0%} EV {p['ev']:.1%}")
                    polled_at = time.time()
                    for s in due:
                        next_poll[s] = polled_at + _poll_interval_seconds(s, cfg)
                time.sleep(tick)
        else:
            picks = run_once(cfg, sports, mode, dry_run=args.dry_run)
            print(f"cycle done: {len(picks)} pick(s) logged, mode={mode}")
            for p in picks:
                print(f"  {p['sport']} {p['player']} {p['side']} {p['line']} {p['market_label']} @{p['odds']} model {p['model_p']:.0%} EV {p['ev']:.1%}")
            if not picks:
                print("no picks passed the gates this cycle (see state/skips.jsonl for reasons)")
            return 0
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass

if __name__ == "__main__":
    raise SystemExit(main())
