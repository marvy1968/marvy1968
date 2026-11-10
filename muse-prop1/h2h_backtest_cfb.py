#!/usr/bin/env python3
"""H2H college football backtest — Arm A (short test, then full arms).

Frozen spec: h2h-backtest-cfb-spec-2026-11-10.md (written BEFORE this run).
Log-only backtest. No bets, no odds, no keys. ESPN site API only.

Short test (2026-11-10): accumulate Oct 18 – Nov 8, 2026 and evaluate only
Nov 7-8 (the last weekend, ~50 games). That validates the whole pipeline —
fetch, parse, accumulate, vote, pick, report — on ~3 prior games per team
before the full arms run (Arm A full season, Arm B postseason, Arm C the
2025-26 validation season).

Usage:
  python3 h2h_backtest_cfb.py [--start YYYYMMDD] [--end YYYYMMDD]
      [--eval-start YYYYMMDD] [--out PREFIX]
Defaults: start 20261018, end 20261108, eval-start 20261107, out h2h_backtest_cfb_short.
"""
import argparse
import csv
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / "h2h_cfb_cache"
CACHE.mkdir(exist_ok=True)

SITE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
SLEEP = 0.15  # seconds between API calls

# (metric key, higher_is_better). Frozen list from the spec.
METRICS = [
    ("points", True),
    ("total_yards", True),
    ("pass_yards", True),
    ("rush_yards", True),
    ("first_downs", True),
    ("third_down", True),
    ("yards_per_pass", True),
    ("yards_per_rush", True),
    ("turnovers", False),    # lower wins
    ("takeaways", True),
    ("points_allowed", False),  # lower wins
    ("yards_allowed", False),   # lower wins
    ("penalty_yards", False),   # lower wins
    ("possession", True),
]
HIGHER = {k: h for k, h in METRICS}


def get_json(url, params=None, cache_key=None, retries=3):
    """GET JSON, with a disk cache for immutable resources (summaries)."""
    if cache_key:
        p = CACHE / f"{cache_key}.json"
        if p.exists():
            return json.loads(p.read_text())
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            data = r.json()
            if cache_key:
                (CACHE / f"{cache_key}.json").write_text(json.dumps(data))
            time.sleep(SLEEP)
            return data
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.0 + attempt)
    raise RuntimeError("unreachable")


def _parse_display_number(s):
    """Parse a displayValue like '370' or '5-30' -> (first, second)."""
    if s is None:
        return None, None
    parts = str(s).split("-")
    try:
        a = float(parts[0].replace(",", ""))
    except ValueError:
        return None, None
    b = None
    if len(parts) > 1:
        try:
            b = float(parts[1].replace(",", ""))
        except ValueError:
            b = None
    return a, b


def parse_game(event):
    """Extract one completed game: date, teams, scores, home/away, neutral,
    and the 14 metrics per team (from the summary boxscore). Returns None
    if the game is not usable (not completed, missing stats)."""
    comp = event.get("competitions", [{}])[0]
    status = comp.get("status", {}).get("type", {})
    if not status.get("completed"):
        return None
    game_id = event["id"]
    game_date = event["date"][:10]  # YYYY-MM-DD
    competitors = comp.get("competitors", [])
    if len(competitors) != 2:
        return None
    teams = {}
    for c in competitors:
        tid = str(c["team"]["id"])
        teams[c["homeAway"]] = {
            "id": tid,
            "name": c["team"].get("displayName", tid),
            "score": int(c.get("score", 0)),
        }
    if "home" not in teams or "away" not in teams:
        return None
    neutral = bool(comp.get("neutralSite", False))
    # Fetch the summary for team stats.
    try:
        summ = get_json(f"{SITE}/summary", params={"event": game_id},
                        cache_key=f"cfb_summary_{game_id}")
    except Exception as e:
        print(f"  summary fetch failed for {game_id}: {e}", file=sys.stderr)
        return None
    box = summ.get("boxscore", {})
    raw = {}
    for t in box.get("teams", []):
        tid = str(t["team"]["id"])
        stats = {s["name"]: s for s in t.get("statistics", [])}
        raw[tid] = stats
    if len(raw) != 2:
        return None
    home_id, away_id = teams["home"]["id"], teams["away"]["id"]
    if home_id not in raw or away_id not in raw:
        return None

    def metrics_for(tid, opp_id):
        s = raw[tid]
        opp = raw[opp_id]
        total_yards, _ = _parse_display_number(
            s.get("totalYards", {}).get("displayValue"))
        opp_yards, _ = _parse_display_number(
            opp.get("totalYards", {}).get("displayValue"))
        turnovers, _ = _parse_display_number(
            s.get("turnovers", {}).get("displayValue"))
        opp_turnovers, _ = _parse_display_number(
            opp.get("turnovers", {}).get("displayValue"))
        _, pen_yards = _parse_display_number(
            s.get("totalPenaltiesYards", {}).get("displayValue"))
        m = {
            "points": float(teams["home"]["score"] if tid == home_id
                            else teams["away"]["score"]),
            "total_yards": total_yards,
            "pass_yards": s.get("netPassingYards", {}).get("value"),
            "rush_yards": s.get("rushingYards", {}).get("value"),
            "first_downs": s.get("firstDowns", {}).get("value"),
            "third_down": s.get("thirdDownEff", {}).get("value"),
            "yards_per_pass": s.get("yardsPerPass", {}).get("value"),
            "yards_per_rush": s.get("yardsPerRushAttempt", {}).get("value"),
            "turnovers": turnovers,
            "takeaways": opp_turnovers,
            "points_allowed": float(teams["away"]["score"] if tid == home_id
                                    else teams["home"]["score"]),
            "yards_allowed": opp_yards,
            "penalty_yards": pen_yards,
            "possession": s.get("possessionTime", {}).get("value"),
        }
        # All 14 must be present and numeric.
        for k, v in m.items():
            if v is None:
                return None
            try:
                m[k] = float(v)
            except (TypeError, ValueError):
                return None
        return m

    home_m = metrics_for(home_id, away_id)
    away_m = metrics_for(away_id, home_id)
    if home_m is None or away_m is None:
        return None
    return {
        "id": game_id,
        "date": game_date,
        "home": {"id": home_id, "name": teams["home"]["name"],
                 "score": teams["home"]["score"], "metrics": home_m},
        "away": {"id": away_id, "name": teams["away"]["name"],
                 "score": teams["away"]["score"], "metrics": away_m},
        "neutral": neutral,
    }


def fetch_games(start, end):
    """Fetch all completed FBS games in [start, end] (dates inclusive)."""
    games = []
    d = start
    while d <= end:
        datestr = d.strftime("%Y%m%d")
        try:
            data = get_json(f"{SITE}/scoreboard",
                            params={"dates": datestr, "groups": 80})
        except Exception as e:
            print(f"  scoreboard fetch failed for {datestr}: {e}", file=sys.stderr)
            d += timedelta(days=1)
            continue
        for event in data.get("events", []):
            g = parse_game(event)
            if g is not None:
                games.append(g)
        d += timedelta(days=1)
    games.sort(key=lambda g: (g["date"], g["id"]))
    return games


def run(start, end, eval_start):
    games = fetch_games(start, end)
    print(f"fetched {len(games)} completed games with full stats "
          f"({start} to {end})")
    # Accumulate per-team averages in date order; evaluate only games on or
    # after eval_start, using averages from strictly earlier dates.
    sums = {}   # team_id -> {metric: total}
    counts = {}  # team_id -> games
    evaluated = []
    skipped_no_data = 0
    for g in games:
        gdate = date.fromisoformat(g["date"])
        # Averages available BEFORE this game (strictly earlier dates).
        # We process games in date order, so sums/counts hold only earlier
        # games — but games on the SAME date must not see each other. Track
        # the date boundary: snapshot averages at each new date.
        if gdate != getattr(run, "_last_date", None):
            run._last_date = gdate
            run._snapshot = {tid: {k: sums[tid][k] / counts[tid]
                                   for k in sums[tid]}
                             for tid in sums}
            run._counts = dict(counts)
        snap = run._snapshot
        # Accumulate this game AFTER computing its averages (for later games).
        for side in ("home", "away"):
            tid = g[side]["id"]
            sums.setdefault(tid, {k: 0.0 for k, _ in METRICS})
            counts[tid] = counts.get(tid, 0) + 1
            for k, _ in METRICS:
                sums[tid][k] += g[side]["metrics"][k]
        if gdate < eval_start:
            continue
        hid, aid = g["home"]["id"], g["away"]["id"]
        if hid not in snap or aid not in snap:
            skipped_no_data += 1
            continue
        ha, aa = snap[hid], snap[aid]
        h_votes = a_votes = 0
        metric_wins = {}
        for k, higher in METRICS:
            if ha[k] == aa[k]:
                metric_wins[k] = None
                continue
            home_wins = (ha[k] > aa[k]) if higher else (ha[k] < aa[k])
            metric_wins[k] = "home" if home_wins else "away"
            if home_wins:
                h_votes += 1
            else:
                a_votes += 1
        if not g["neutral"]:
            h_votes += 1  # home field
        home_won = g["home"]["score"] > g["away"]["score"]
        if h_votes == a_votes:
            pick = None
        else:
            pick = "home" if h_votes > a_votes else "away"
        evaluated.append({
            "id": g["id"], "date": g["date"],
            "home": g["home"]["name"], "away": g["away"]["name"],
            "home_score": g["home"]["score"], "away_score": g["away"]["score"],
            "neutral": g["neutral"],
            "h_votes": h_votes, "a_votes": a_votes,
            "pick": pick,
            "pick_won": (pick == "home" and home_won) or
                        (pick == "away" and not home_won) if pick else None,
            "home_won": home_won,
            "metric_wins": metric_wins,
        })
    picks = [e for e in evaluated if e["pick"]]
    wins = [e for e in picks if e["pick_won"]]
    home_wins = [e for e in evaluated if e["home_won"]]
    # Per-metric win rates (decisive games only).
    per_metric = {}
    for k, _ in METRICS:
        dec = [e for e in evaluated if e["metric_wins"][k]]
        won = [e for e in dec
               if (e["metric_wins"][k] == "home" and e["home_won"]) or
               (e["metric_wins"][k] == "away" and not e["home_won"])]
        per_metric[k] = {
            "decisive": len(dec), "meet_won": len(won),
            "rate": (len(won) / len(dec)) if dec else None,
        }
    result = {
        "spec": "h2h-backtest-cfb-spec-2026-11-10.md",
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "eval_start": eval_start.isoformat()},
        "games_fetched": len(games),
        "games_evaluated": len(evaluated),
        "skipped_no_data": skipped_no_data,
        "picks_made": len(picks),
        "wins": len(wins),
        "losses": len(picks) - len(wins),
        "win_rate": (len(wins) / len(picks)) if picks else None,
        "baseline_home_win_rate": (len(home_wins) / len(evaluated)) if evaluated else None,
        "per_metric": per_metric,
    }
    return result, evaluated


def main():
    ap = argparse.ArgumentParser(description="H2H college football backtest (short test).")
    ap.add_argument("--start", default="20261018")
    ap.add_argument("--end", default="20261108")
    ap.add_argument("--eval-start", default="20261107")
    ap.add_argument("--out", default="h2h_backtest_cfb_short")
    args = ap.parse_args()
    start = date.fromisoformat(args.start[:4] + "-" + args.start[4:6] + "-" + args.start[6:8])
    end = date.fromisoformat(args.end[:4] + "-" + args.end[4:6] + "-" + args.end[6:8])
    eval_start = date.fromisoformat(args.eval_start[:4] + "-" + args.eval_start[4:6] + "-" + args.eval_start[6:8])
    result, evaluated = run(start, end, eval_start)
    (HERE / f"{args.out}_results.json").write_text(json.dumps(result, indent=1))
    with open(HERE / f"{args.out}_games.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "date", "home", "away", "home_score", "away_score",
                    "neutral", "h_votes", "a_votes", "pick", "pick_won"])
        for e in evaluated:
            w.writerow([e["id"], e["date"], e["home"], e["away"],
                        e["home_score"], e["away_score"], e["neutral"],
                        e["h_votes"], e["a_votes"], e["pick"], e["pick_won"]])
    print()
    print(f"games fetched: {result['games_fetched']}")
    print(f"games evaluated: {result['games_evaluated']} "
          f"(skipped no data: {result['skipped_no_data']})")
    print(f"picks: {result['picks_made']}, wins: {result['wins']}, "
          f"losses: {result['losses']}")
    if result["win_rate"] is not None:
        print(f"H2H win rate: {100*result['win_rate']:.1f}%")
    if result["baseline_home_win_rate"] is not None:
        print(f"home-team baseline: {100*result['baseline_home_win_rate']:.1f}%")
    print("per-metric win rates (decisive games):")
    for k, v in result["per_metric"].items():
        r = f"{100*v['rate']:.1f}%" if v["rate"] is not None else "n/a"
        print(f"  {k:16s} {v['decisive']:4d} decisive, {r}")


if __name__ == "__main__":
    main()
