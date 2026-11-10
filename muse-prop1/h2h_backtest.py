#!/usr/bin/env python3
"""H2H basketball backtest — three arms + champion reference.

Frozen spec: h2h-backtest-spec-2026-11-08.md (written BEFORE this run).
Log-only backtest. No bets, no odds, no keys. ESPN site API + core API only.

Arm A: NCAAB 2026-27, completed games Nov 2-8, 2026 (groups=50 scoreboard).
Arm B: NCAAB 2025-26 postseason, NCAA Tournament only (neutral + both seeds
       1-16), regular-season averages from core API types/2.
Arm C: NBA 2026-27, completed games Oct 20-Nov 8, 2026 (default scoreboard).

Champion reference: each team's pre-game average vs the mean of the last 3
champions' season averages per metric; +1 per metric met/beaten in the good
direction. Champion votes are added to the H2H total AND reported separately,
plus the pick/win rate with and without the champion factor.
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / "h2h_cache"
CACHE.mkdir(exist_ok=True)
RESULTS = HERE / "h2h_backtest_results.json"
GAMES_CSV = HERE / "h2h_backtest_games.csv"

NCAAB_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
NBA_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
NCAAB_CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"
NBA_CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"

SLEEP = 0.15  # seconds between API calls

# (metric key, higher_is_better). Frozen list from the spec.
METRICS = [
    ("points", True),
    ("rebounds", True),
    ("off_reb", True),
    ("def_reb", True),
    ("assists", True),
    ("steals", True),
    ("blocks", True),
    ("turnovers", False),   # lower wins
    ("fouls", False),       # lower wins
    ("threes", True),
    ("fg_pct", True),
    ("tp_pct", True),
    ("ft_pct", True),
    ("ast_to", True),
]
HIGHER = {k: h for k, h in METRICS}

# Champions, identified from ESPN scoreboard data (championship-game winners /
# last Finals-game winners). (league, season, team_id, name)
NCAAB_CHAMPS = {
    2023: ("41", "UConn Huskies"),
    2024: ("41", "UConn Huskies"),
    2025: ("57", "Florida Gators"),
    2026: ("130", "Michigan Wolverines"),
}
NBA_CHAMPS = {
    2024: ("2", "Boston Celtics"),
    2025: ("25", "Oklahoma City Thunder"),
    2026: ("18", "New York Knicks"),
}

# Arm champion sets: which champion years form the benchmark.
CHAMP_YEARS = {
    "A": ("ncaab", [2024, 2025, 2026]),
    "B": ("ncaab", [2023, 2024, 2025]),
    "C": ("nba", [2024, 2025, 2026]),
}


def get_json(url, params=None, cache_key=None, retries=3):
    """GET JSON, with a disk cache for immutable resources (summaries, stats)."""
    if cache_key:
        p = CACHE / (cache_key + ".json")
        if p.exists():
            return json.loads(p.read_text())
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=25)
            r.raise_for_status()
            data = r.json()
            if cache_key:
                p.write_text(json.dumps(data))
            time.sleep(SLEEP)
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"GET failed {url} {params}: {last}")


def fnum(x):
    """Parse a displayValue like '35', '21-60' (returns None), '--' -> None."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s in ("", "--", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_made_attempt(s):
    """'21-60' -> (21.0, 60.0); '4-17' -> (4.0, 17.0)."""
    if s is None:
        return None, None
    parts = str(s).strip().split("-")
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


# ------------------------------------------------------------------ scoreboard
def fetch_scoreboard(site, ds, groups=None):
    params = {"dates": ds}
    if groups:
        params["groups"] = groups
    data = get_json(site + "/scoreboard", params)
    games = []
    for e in data.get("events", []) or []:
        comp = (e.get("competitions") or [{}])[0]
        status = comp.get("status", {}) or {}
        if not ((status.get("type") or {}).get("completed")):
            continue
        competitors = comp.get("competitors", []) or []
        if len(competitors) != 2:
            continue
        home = away = None
        for c in competitors:
            team = c.get("team") or {}
            entry = {
                "id": str(team.get("id")),
                "name": team.get("displayName", ""),
                "score": int(c.get("score") or 0),
                "seed": (c.get("curatedRank") or {}).get("current"),
                "homeAway": c.get("homeAway"),
            }
            if c.get("homeAway") == "home":
                home = entry
            else:
                away = entry
        if home is None or away is None:
            continue
        games.append({
            "event_id": str(e.get("id")),
            "date": ds,
            "home": home,
            "away": away,
            "neutral": bool(comp.get("neutralSite")),
            "season_type": (e.get("season") or {}).get("type"),
        })
    return games


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ------------------------------------------------------------- game summaries
def fetch_game_stats(site, event_id):
    """Per-team per-game stats from the ESPN summary boxscore. Returns
    {team_id: {metric: value}} or None if the boxscore is unusable."""
    data = get_json(site + "/summary", {"event": event_id},
                    cache_key=f"summary_{event_id}")
    box = data.get("boxscore", {}) or {}
    # scores from the header competitions
    scores = {}
    for comp in (data.get("header", {}) or {}).get("competitions", []) or []:
        for c in comp.get("competitors", []) or []:
            team = c.get("team") or {}
            scores[str(team.get("id"))] = int(c.get("score") or 0)
    out = {}
    for t in box.get("teams", []) or []:
        team = t.get("team") or {}
        tid = str(team.get("id"))
        stats = {}
        for s in t.get("statistics", []) or []:
            name = s.get("name", "")
            dv = s.get("displayValue")
            if name == "totalRebounds":
                stats["rebounds"] = fnum(dv)
            elif name == "offensiveRebounds":
                stats["off_reb"] = fnum(dv)
            elif name == "defensiveRebounds":
                stats["def_reb"] = fnum(dv)
            elif name == "assists":
                stats["assists"] = fnum(dv)
            elif name == "steals":
                stats["steals"] = fnum(dv)
            elif name == "blocks":
                stats["blocks"] = fnum(dv)
            elif name == "turnovers":
                stats["turnovers"] = fnum(dv)
            elif name == "fouls":
                stats["fouls"] = fnum(dv)
            elif name == "threePointFieldGoalsMade-threePointFieldGoalsAttempted":
                made, _ = parse_made_attempt(dv)
                stats["threes"] = made
            elif name == "fieldGoalPct":
                stats["fg_pct"] = fnum(dv)
            elif name == "threePointFieldGoalPct":
                stats["tp_pct"] = fnum(dv)
            elif name == "freeThrowPct":
                stats["ft_pct"] = fnum(dv)
        if tid in scores:
            stats["points"] = float(scores[tid])
        # ast/to ratio is computed from averages, not per game
        out[tid] = stats
    return out


# ------------------------------------------------------------ core API stats
def fetch_season_averages(core, season, team_id):
    """Season averages (types/2 = regular season) from the core API.
    Returns {metric: per-game average} or None."""
    url = (f"{core}/seasons/{season}/types/2/teams/{team_id}/statistics")
    data = get_json(url, {"lang": "en", "region": "us"},
                    cache_key=f"core_{core.split('/')[-1]}_{season}_{team_id}")
    vals = {}
    cats = (data.get("splits", {}) or {}).get("categories", []) or []
    for cat in cats:
        for st in cat.get("stats", []) or []:
            vals[st.get("name")] = st.get("value")
    gp = vals.get("gamesPlayed") or 0
    if not gp:
        return None

    def avg(avg_name, total_name):
        if vals.get(avg_name) is not None:
            return float(vals[avg_name])
        if vals.get(total_name) is not None:
            return float(vals[total_name]) / gp
        return None

    out = {
        "points": avg("avgPoints", "points"),
        "rebounds": avg("avgRebounds", "rebounds"),
        "off_reb": avg("avgOffensiveRebounds", "offensiveRebounds"),
        "def_reb": avg("avgDefensiveRebounds", "defensiveRebounds"),
        "assists": avg("avgAssists", "assists"),
        "steals": avg("avgSteals", "steals"),
        "blocks": avg("avgBlocks", "blocks"),
        "turnovers": avg("avgTurnovers", "turnovers"),
        "fouls": avg("avgFouls", "fouls"),
        "threes": avg("avgThreePointFieldGoalsMade", "threePointFieldGoalsMade"),
        "fg_pct": vals.get("fieldGoalPct"),
        "tp_pct": vals.get("threePointFieldGoalPct"),
        "ft_pct": vals.get("freeThrowPct"),
        "ast_to": vals.get("assistTurnoverRatio"),
    }
    out = {k: (float(v) if v is not None else None) for k, v in out.items()}
    if out.get("ast_to") is None and out.get("assists") is not None and out.get("turnovers"):
        out["ast_to"] = out["assists"] / max(out["turnovers"], 0.1)
    if any(v is None for v in out.values()):
        return None
    return out


# ------------------------------------------------------------------- voting
def vote_metrics(avg_a, avg_b):
    """H2H metric votes. Returns (votes_a, votes_b, per_metric dict)."""
    va = vb = 0
    per = {}
    for key, higher in METRICS:
        a, b = avg_a[key], avg_b[key]
        if a == b:
            per[key] = 0
            continue
        if higher:
            w = 1 if a > b else -1
        else:
            w = 1 if a < b else -1
        per[key] = w
        if w == 1:
            va += 1
        else:
            vb += 1
    return va, vb, per


def vote_champion(avg, benchmark):
    """+1 per metric where the team's average meets/beats the champion
    benchmark in the good direction. Returns (votes, per-metric dict)."""
    v = 0
    per = {}
    for key, higher in METRICS:
        a, bench = avg[key], benchmark[key]
        ok = (a >= bench) if higher else (a <= bench)
        per[key] = 1 if ok else 0
        v += 1 if ok else 0
    return v, per


def averages_from_games(game_stats_list):
    """Mean of per-game stats over a list of {metric: value} dicts.
    Returns the 14-metric average dict or None if the list is empty or any
    metric is missing from every game."""
    if not game_stats_list:
        return None
    out = {}
    for key, _ in METRICS:
        if key == "ast_to":
            continue
        vals = [g[key] for g in game_stats_list if g.get(key) is not None]
        if not vals:
            return None
        out[key] = sum(vals) / len(vals)
    out["ast_to"] = out["assists"] / max(out["turnovers"], 0.1)
    return out


# ------------------------------------------------------------------- arms
def run_arm_season(arm, site, start, end, groups=None):
    """Arms A (NCAAB) and C (NBA): running pre-game averages from summaries."""
    print(f"\n=== Arm {arm}: fetching scoreboards {start} to {end} ===")
    games = []
    for d in daterange(start, end):
        games.extend(fetch_scoreboard(site, d.strftime("%Y%m%d"), groups))
    games.sort(key=lambda g: (g["date"], g["event_id"]))
    print(f"Arm {arm}: {len(games)} completed games in sample")
    # per-team list of (date, game_stats), in date order
    history = {}
    rows = []
    skipped_no_data = 0
    skipped_box = 0
    for i, g in enumerate(games):
        if i % 50 == 0:
            print(f"  Arm {arm}: game {i+1}/{len(games)} ({g['date']})")
        stats = fetch_game_stats(site, g["event_id"])
        if not stats:
            skipped_box += 1
            continue
        # pre-game averages: strictly earlier dates only
        avgs = {}
        ok = True
        for side in ("home", "away"):
            tid = g[side]["id"]
            prior = [gs for (d, gs) in history.get(tid, []) if d < g["date"]]
            a = averages_from_games(prior)
            if a is None:
                ok = False
                break
            avgs[side] = a
        if not ok:
            skipped_no_data += 1
        else:
            rows.append((g, avgs))
        # record this game's stats into history (after evaluation)
        for side in ("home", "away"):
            tid = g[side]["id"]
            gs = stats.get(tid)
            if gs and all(gs.get(k) is not None for k, _ in METRICS if k != "ast_to") \
                    and gs.get("points") is not None:
                history.setdefault(tid, []).append((g["date"], gs))
            elif gs is None:
                skipped_box += 0  # team block missing; already counted below
        # note: games whose boxscore is incomplete for a team are still
        # evaluated from prior data but do not extend that team's history
    print(f"Arm {arm}: {len(rows)} games with pre-game averages for both teams, "
          f"{skipped_no_data} skipped (no prior data), {skipped_box} boxscore issues")
    return rows


def run_arm_tournament():
    """Arm B: 2026 NCAA Tournament, regular-season averages from core API."""
    print("\n=== Arm B: fetching tournament scoreboards ===")
    tdates = ["20260317", "20260318", "20260319", "20260320", "20260321", "20260322",
              "20260326", "20260327", "20260328", "20260329", "20260404", "20260406"]
    games = []
    for ds in tdates:
        for g in fetch_scoreboard(NCAAB_SITE, ds, "50"):
            ranks = [g["home"].get("seed"), g["away"].get("seed")]
            if g["neutral"] and all(isinstance(r, int) and 1 <= r <= 16 for r in ranks):
                games.append(g)
    games.sort(key=lambda g: (g["date"], g["event_id"]))
    print(f"Arm B: {len(games)} NCAA tournament games (filter expects 67)")
    team_ids = sorted({g[s]["id"] for g in games for s in ("home", "away")})
    print(f"Arm B: {len(team_ids)} teams; fetching regular-season averages...")
    avgs = {}
    for i, tid in enumerate(team_ids):
        a = fetch_season_averages(NCAAB_CORE, 2026, tid)
        if a is None:
            print(f"  WARNING: no season averages for team {tid}")
        else:
            avgs[tid] = a
        if i % 20 == 0:
            print(f"  Arm B: team {i+1}/{len(team_ids)}")
    rows = []
    skipped = 0
    for g in games:
        ha, aa = avgs.get(g["home"]["id"]), avgs.get(g["away"]["id"])
        if ha is None or aa is None:
            skipped += 1
            continue
        rows.append((g, {"home": ha, "away": aa}))
    print(f"Arm B: {len(rows)} games with averages, {skipped} skipped")
    return rows


def champion_benchmarks():
    """Benchmark per metric = mean of the last 3 champions' season averages."""
    out = {}
    for arm, (league, years) in CHAMP_YEARS.items():
        core = NCAAB_CORE if league == "ncaab" else NBA_CORE
        champs = NCAAB_CHAMPS if league == "ncaab" else NBA_CHAMPS
        per_champ = []
        names = []
        for y in years:
            tid, name = champs[y]
            a = fetch_season_averages(core, y, tid)
            if a is None:
                raise RuntimeError(f"no season averages for champion {name} ({league} {y})")
            per_champ.append(a)
            names.append(f"{name} {y}")
        bench = {}
        for key, _ in METRICS:
            bench[key] = sum(c[key] for c in per_champ) / len(per_champ)
        out[arm] = {"benchmark": bench, "champions": names,
                    "champion_avgs": per_champ}
        print(f"Arm {arm} champions: {', '.join(names)}")
    return out


def evaluate(rows, arm, benchmark, home_applies):
    """Evaluate one arm's rows. Returns (stats dict, game rows list)."""
    picks = wins = 0
    picks_no_champ = wins_no_champ = 0
    tied_total = 0
    baseline_wins = 0
    baseline_n = 0
    metric_wins = {k: [0, 0] for k, _ in METRICS}  # [metric-winner won, decisive]
    champ_votes_total = {"home": 0, "away": 0}
    h2h_votes_total = {"home": 0, "away": 0}
    home_points = 0
    out_rows = []
    for g, avgs in rows:
        ha, aa = avgs["home"], avgs["away"]
        hv_h, hv_a, per = vote_metrics(ha, aa)
        cv_h, _ = vote_champion(ha, benchmark)
        cv_a, _ = vote_champion(aa, benchmark)
        home_pt_h = 1 if (home_applies and not g["neutral"]) else 0
        total_h = hv_h + cv_h + home_pt_h
        total_a = hv_a + cv_a
        total_h_nc = hv_h + home_pt_h
        total_a_nc = hv_a
        winner = "home" if g["home"]["score"] > g["away"]["score"] else "away"
        # baseline
        if arm == "B":
            sh, sa = g["home"].get("seed"), g["away"].get("seed")
            if sh is not None and sa is not None and sh != sa:
                base_pick = "home" if sh < sa else "away"
                baseline_n += 1
                if base_pick == winner:
                    baseline_wins += 1
        else:
            baseline_n += 1
            if winner == "home":
                baseline_wins += 1
        # picks
        pick = None
        if total_h != total_a:
            pick = "home" if total_h > total_a else "away"
            picks += 1
            if pick == winner:
                wins += 1
        else:
            tied_total += 1
        pick_nc = None
        if total_h_nc != total_a_nc:
            pick_nc = "home" if total_h_nc > total_a_nc else "away"
            picks_no_champ += 1
            if pick_nc == winner:
                wins_no_champ += 1
        for key, w in per.items():
            if w != 0:
                metric_wins[key][1] += 1
                metric_winner = "home" if w == 1 else "away"
                if metric_winner == winner:
                    metric_wins[key][0] += 1
        champ_votes_total["home"] += cv_h
        champ_votes_total["away"] += cv_a
        h2h_votes_total["home"] += hv_h
        h2h_votes_total["away"] += hv_a
        home_points += home_pt_h
        out_rows.append({
            "arm": arm, "date": g["date"], "event_id": g["event_id"],
            "home": g["home"]["name"], "away": g["away"]["name"],
            "home_score": g["home"]["score"], "away_score": g["away"]["score"],
            "home_seed": g["home"].get("seed"), "away_seed": g["away"].get("seed"),
            "neutral": g["neutral"], "winner": winner,
            "h2h_home": hv_h, "h2h_away": hv_a,
            "champ_home": cv_h, "champ_away": cv_a,
            "home_point": home_pt_h,
            "total_home": total_h, "total_away": total_a,
            "total_home_no_champ": total_h_nc, "total_away_no_champ": total_a_nc,
            "pick": pick, "pick_no_champ": pick_nc,
            "pick_won": (pick == winner) if pick else None,
            "pick_no_champ_won": (pick_nc == winner) if pick_nc else None,
            "metric_votes": per,
        })
    n = len(rows)
    stats = {
        "games_evaluated": n,
        "picks_made": picks,
        "wins": wins,
        "losses": picks - wins,
        "win_rate": round(wins / picks, 4) if picks else None,
        "picks_no_champ": picks_no_champ,
        "wins_no_champ": wins_no_champ,
        "win_rate_no_champ": round(wins_no_champ / picks_no_champ, 4) if picks_no_champ else None,
        "tied_totals": tied_total,
        "baseline_n": baseline_n,
        "baseline_wins": baseline_wins,
        "baseline_rate": round(baseline_wins / baseline_n, 4) if baseline_n else None,
        "per_metric": {k: {"decisive": v[1], "metric_winner_won": v[0],
                           "rate": round(v[0] / v[1], 4) if v[1] else None}
                       for k, v in metric_wins.items()},
        "champ_votes_total": champ_votes_total,
        "champ_votes_avg_per_game": {s: round(champ_votes_total[s] / n, 2) if n else None
                                     for s in ("home", "away")},
        "h2h_votes_total": h2h_votes_total,
        "home_points_total": home_points,
    }
    return stats, out_rows


def main():
    print("H2H backtest — frozen spec h2h-backtest-spec-2026-11-08.md")
    benches = champion_benchmarks()

    rows_a = run_arm_season("A", NCAAB_SITE, date(2026, 11, 2), date(2026, 11, 8), "50")
    rows_b = run_arm_tournament()
    rows_c = run_arm_season("C", NBA_SITE, date(2026, 10, 20), date(2026, 11, 8), None)

    all_rows = []
    result = {
        "spec": "h2h-backtest-spec-2026-11-08.md",
        "generated": date.today().isoformat(),
        "metrics": [k for k, _ in METRICS],
        "champion_benchmarks": {arm: {"champions": b["champions"],
                                      "benchmark": {k: round(v, 3) for k, v in b["benchmark"].items()}}
                                for arm, b in benches.items()},
        "arms": {},
    }
    for arm, rows, home_applies in (("A", rows_a, True), ("B", rows_b, False), ("C", rows_c, True)):
        stats, grows = evaluate(rows, arm, benches[arm]["benchmark"], home_applies)
        result["arms"][arm] = stats
        all_rows.extend(grows)
        print(f"\nArm {arm}: evaluated={stats['games_evaluated']} picks={stats['picks_made']} "
              f"wins={stats['wins']} win_rate={stats['win_rate']} "
              f"(no-champ {stats['win_rate_no_champ']}) baseline={stats['baseline_rate']}")

    RESULTS.write_text(json.dumps(result, indent=1))
    import csv
    with open(GAMES_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["arm", "date", "event_id", "home", "away", "home_score", "away_score",
                    "home_seed", "away_seed", "neutral", "winner",
                    "h2h_home", "h2h_away", "champ_home", "champ_away", "home_point",
                    "total_home", "total_away", "total_home_no_champ", "total_away_no_champ",
                    "pick", "pick_no_champ", "pick_won", "pick_no_champ_won"])
        for r in all_rows:
            w.writerow([r["arm"], r["date"], r["event_id"], r["home"], r["away"],
                        r["home_score"], r["away_score"], r["home_seed"], r["away_seed"],
                        r["neutral"], r["winner"], r["h2h_home"], r["h2h_away"],
                        r["champ_home"], r["champ_away"], r["home_point"],
                        r["total_home"], r["total_away"],
                        r["total_home_no_champ"], r["total_away_no_champ"],
                        r["pick"], r["pick_no_champ"], r["pick_won"], r["pick_no_champ_won"]])
    print(f"\nresults -> {RESULTS.name}, games -> {GAMES_CSV.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
