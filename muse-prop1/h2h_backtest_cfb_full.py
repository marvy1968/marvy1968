#!/usr/bin/env python3
"""Full CFB H2H backtest — Arms A, B, C + 3-part champion comparison.

Frozen spec: h2h-backtest-cfb-spec-2026-11-10.md (written BEFORE this run).
Log-only backtest. No bets, no odds, no keys. ESPN site API + core API
(rankings only, for the Arm B AP baseline).

Imports the fetch/parse helpers from h2h_backtest_cfb.py (same 14 metrics,
same summary parsing, same disk cache h2h_cfb_cache/ — summaries are reused,
not re-fetched).

Arms:
  A: 2026-27 regular season, Aug 29 - Nov 8 2026 (sequential averages).
  C: 2025-26 regular season, Aug 23 - Nov 29 2025 (sequential averages).
  B: 2025-26 postseason (46 games, seasontype=3 scoreboard), averages =
     each team's FULL 2025-26 regular season (same games as Arm C).
     No home point. Baseline = higher AP-ranked team (final regular-season
     AP Top 25 = Week 16, dated 2025-12-07, core API).

Champions (benchmarks): Michigan 2023, Ohio State 2024, Indiana 2025.
Season averages from their per-game summaries over their regular-season
Saturday windows (Aug 26-Nov 25 2023, Aug 24-Nov 30 2024, Aug 23-Nov 29
2025) — the same window convention as Arm C (conference championship
games excluded). Benchmark = mean of the 3 champions' season averages.
Per the spec's explicit Arm B instruction, all 3 champions form the
benchmark for every arm including Arm B. FLAG: Indiana was not decided
champion until the Jan 20, 2026 championship game, so for Arm B's
December/early-January games the benchmark technically includes a champion
not yet decided — the spec's own parenthetical justifying this is wrong on
the date, but its instruction (2023+2024+2025 for Arm B) is explicit, so
it is followed here and flagged, not silently changed.

Usage:
  python3 h2h_backtest_cfb_full.py [--skip-fetch] [--champions-only]
  --skip-fetch: reuse saved games JSONs (h2h_backtest_cfb_arm*_games.json)
  instead of fetching. --champions-only: only (re)compute champion
  benchmarks and Parts 1-3 from saved evaluated records.
"""
import argparse
import csv
import json
import math
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Reuse the frozen helpers from the short-test script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from h2h_backtest_cfb import (CACHE, METRICS, SITE, fetch_games, get_json,
                              parse_game)

HERE = Path(__file__).resolve().parent
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"

# Regular-season windows (Saturday windows; conference championships excluded).
ARM_A = (date(2026, 8, 29), date(2026, 11, 8))
ARM_C = (date(2025, 8, 23), date(2025, 11, 29))
CHAMP_WINDOWS = {
    "Michigan 2023": ("Michigan Wolverines", date(2023, 8, 26), date(2023, 11, 25)),
    "Ohio State 2024": ("Ohio State Buckeyes", date(2024, 8, 24), date(2024, 11, 30)),
    "Indiana 2025": ("Indiana Hoosiers", date(2025, 8, 23), date(2025, 11, 29)),
}


def _avg(sums, counts, tid):
    return {k: sums[tid][k] / counts[tid] for k, _ in METRICS}


def evaluate_sequential(games, use_home_point=True):
    """Walk games in date order; pre-game averages = strictly earlier dates.
    Returns (evaluated, games_fetched, skipped_no_data). Each evaluated
    record carries the pre-game averages (needed for the champion parts)."""
    sums, counts = {}, {}
    evaluated, skipped = [], 0
    last_date, snapshot = None, {}
    for g in games:
        gdate = date.fromisoformat(g["date"])
        if gdate != last_date:
            last_date = gdate
            snapshot = {tid: _avg(sums, counts, tid) for tid in sums}
        # Accumulate this game for later dates.
        for side in ("home", "away"):
            tid = g[side]["id"]
            sums.setdefault(tid, {k: 0.0 for k, _ in METRICS})
            counts[tid] = counts.get(tid, 0) + 1
            for k, _ in METRICS:
                sums[tid][k] += g[side]["metrics"][k]
        hid, aid = g["home"]["id"], g["away"]["id"]
        if hid not in snapshot or aid not in snapshot:
            skipped += 1
            continue
        evaluated.append(_evaluate_one(g, snapshot[hid], snapshot[aid],
                                       use_home_point))
    return evaluated, len(games), skipped


def _evaluate_one(g, ha, aa, use_home_point):
    """H2H vote/pick for one game given both teams' pre-game averages."""
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
    if use_home_point and not g["neutral"]:
        h_votes += 1  # home field
    home_won = g["home"]["score"] > g["away"]["score"]
    if h_votes == a_votes:
        pick = None
    else:
        pick = "home" if h_votes > a_votes else "away"
    return {
        "id": g["id"], "date": g["date"],
        "home": g["home"]["name"], "away": g["away"]["name"],
        "home_id": g["home"]["id"], "away_id": g["away"]["id"],
        "home_score": g["home"]["score"], "away_score": g["away"]["score"],
        "neutral": g["neutral"],
        "home_avg": ha, "away_avg": aa,
        "h_votes": h_votes, "a_votes": a_votes,
        "pick": pick,
        "pick_won": ((pick == "home" and home_won) or
                     (pick == "away" and not home_won)) if pick else None,
        "home_won": home_won,
        "metric_wins": metric_wins,
    }


def season_averages(games):
    """Full-window per-team averages (no date discipline — the whole list)."""
    sums, counts = {}, {}
    for g in games:
        for side in ("home", "away"):
            tid = g[side]["id"]
            sums.setdefault(tid, {k: 0.0 for k, _ in METRICS})
            counts[tid] = counts.get(tid, 0) + 1
            for k, _ in METRICS:
                sums[tid][k] += g[side]["metrics"][k]
    return {tid: _avg(sums, counts, tid) for tid in sums}, counts


def fetch_postseason():
    """The 2025-26 postseason: seasontype=3 scoreboard returns the whole
    slate (46 events: bowls + 11 CFP games) in one call."""
    data = get_json(f"{SITE}/scoreboard",
                    params={"dates": "20260101", "groups": 80, "seasontype": 3})
    seen, games = set(), []
    for event in data.get("events", []):
        if event["id"] in seen:
            continue
        seen.add(event["id"])
        g = parse_game(event)
        if g is not None:
            games.append(g)
    games.sort(key=lambda g: (g["date"], g["id"]))
    return games


def fetch_team_games(display_name, start, end):
    """All games involving one team in [start, end] (scoreboards by date,
    filter by displayName, parse only that team's games)."""
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
            comp = event.get("competitions", [{}])[0]
            names = [c.get("team", {}).get("displayName")
                     for c in comp.get("competitors", [])]
            if display_name not in names:
                continue
            g = parse_game(event)
            if g is not None:
                games.append(g)
        d += timedelta(days=1)
    games.sort(key=lambda g: (g["date"], g["id"]))
    return games


def fetch_ap_top25_2025():
    """Final regular-season AP Top 25 (Week 16, dated 2025-12-07), core API.
    Returns {team_id: rank} and {team_id: displayName}. Cached to disk."""
    cache_path = HERE / "h2h_cfb_cache" / "ap_top25_2025_w16.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    url = (f"{CORE}/seasons/2025/types/2/weeks/16/rankings/1")
    d = get_json(url)
    assert d.get("date", "").startswith("2025-12-07"), f"unexpected poll date {d.get('date')}"
    ranks, names = {}, {}
    for t in d.get("ranks", []):
        ref = t.get("team", {}).get("$ref", "")
        m = re.search(r"/teams/(\d+)", ref)
        if not m:
            continue
        tid = m.group(1)
        ranks[tid] = int(t["current"])
        # Resolve the team $ref for its displayName.
        team = get_json(ref, cache_key=f"cfb_team_{tid}")
        names[tid] = team.get("displayName", tid)
    out = {"date": d.get("date"), "headline": d.get("headline"),
           "ranks": ranks, "names": names}
    cache_path.write_text(json.dumps(out, indent=1))
    return out


# ---------- champion parts ----------

def champion_votes(avg, benchmark):
    """+1 per metric where avg meets/beats the benchmark in the good
    direction (>= higher-is-better, <= lower-is-better; tie counts)."""
    votes, meets = 0, {}
    for k, higher in METRICS:
        ok = (avg[k] >= benchmark[k]) if higher else (avg[k] <= benchmark[k])
        meets[k] = bool(ok)
        if ok:
            votes += 1
    return votes, meets


def power_score(avg, benchmark):
    """Mean of the 14 normalised differences from the benchmark."""
    total = 0.0
    for k, higher in METRICS:
        b = benchmark[k]
        if b == 0:
            continue
        total += ((avg[k] - b) / b) if higher else ((b - avg[k]) / b)
    return total / len(METRICS)


def _pick_stats(records, pick_key="pick", won_key="pick_won"):
    picks = [r for r in records if r[pick_key]]
    wins = [r for r in picks if r[won_key]]
    tied = len(records) - len(picks)
    return {"picks": len(picks), "wins": len(wins),
            "losses": len(picks) - len(wins),
            "win_rate": (len(wins) / len(picks)) if picks else None,
            "tied": tied}


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) *
                    sum((y - my) ** 2 for y in ys))
    return (num / den) if den else None


def analyse_arm(evaluated, benchmark, use_home_point, ap_ranks=None):
    """Parts 1-3 for one arm. Returns a dict; also enriches each record
    with champion votes / power scores (in place) for pooled analysis."""
    for r in evaluated:
        hc, h_meets = champion_votes(r["home_avg"], benchmark)
        ac, a_meets = champion_votes(r["away_avg"], benchmark)
        r["h_champ_votes"] = hc
        r["a_champ_votes"] = ac
        r["champ_meets"] = {"home": h_meets, "away": a_meets}
        r["h_power"] = power_score(r["home_avg"], benchmark)
        r["a_power"] = power_score(r["away_avg"], benchmark)
        # Part 1 standalone pick (champion votes only, no home point).
        if hc == ac:
            r["champ_pick"] = None
        else:
            r["champ_pick"] = "home" if hc > ac else "away"
        r["champ_pick_won"] = (
            (r["champ_pick"] == "home" and r["home_won"]) or
            (r["champ_pick"] == "away" and not r["home_won"])
        ) if r["champ_pick"] else None
        # Part 2 WITH champion = H2H + champion + home point (if used).
        hw = r["h_votes"] + hc
        aw = r["a_votes"] + ac
        if hw == aw:
            r["with_pick"] = None
        else:
            r["with_pick"] = "home" if hw > aw else "away"
        r["with_pick_won"] = (
            (r["with_pick"] == "home" and r["home_won"]) or
            (r["with_pick"] == "away" and not r["home_won"])
        ) if r["with_pick"] else None
        # Part 3 power-rank pick (tie within 0.001 = no pick).
        if abs(r["h_power"] - r["a_power"]) <= 0.001:
            r["power_pick"] = None
        else:
            r["power_pick"] = "home" if r["h_power"] > r["a_power"] else "away"
        r["power_pick_won"] = (
            (r["power_pick"] == "home" and r["home_won"]) or
            (r["power_pick"] == "away" and not r["home_won"])
        ) if r["power_pick"] else None
        r["power_margin"] = abs(r["h_power"] - r["a_power"])

    # H2H-only (Part 2 WITHOUT) — must reproduce the arm totals exactly.
    without = _pick_stats(evaluated, "pick", "pick_won")
    with_champ = _pick_stats(evaluated, "with_pick", "with_pick_won")
    standalone = _pick_stats(evaluated, "champ_pick", "champ_pick_won")
    power = _pick_stats(evaluated, "power_pick", "power_pick_won")

    # Baseline.
    if ap_ranks is not None:
        ranked = [r for r in evaluated
                  if r["home_id"] in ap_ranks and r["away_id"] in ap_ranks]
        unranked = [r for r in evaluated if r not in ranked]
        base_wins = sum(
            1 for r in ranked
            if (r["home_won"] and ap_ranks[r["home_id"]] < ap_ranks[r["away_id"]]) or
            (not r["home_won"] and ap_ranks[r["away_id"]] < ap_ranks[r["home_id"]]))
        baseline = {"n": len(ranked), "wins": base_wins,
                    "rate": (base_wins / len(ranked)) if ranked else None,
                    "unranked_games": len(unranked),
                    "kind": "higher AP-ranked team"}
    else:
        home_wins = sum(1 for r in evaluated if r["home_won"])
        baseline = {"n": len(evaluated), "wins": home_wins,
                    "rate": (home_wins / len(evaluated)) if evaluated else None,
                    "kind": "home team"}

    # Per-metric champion win rates (decisive = exactly one team met it).
    champ_metric = {}
    for k, _ in METRICS:
        dec = [r for r in evaluated
               if r["champ_meets"]["home"][k] != r["champ_meets"]["away"][k]]
        won = [r for r in dec
               if (r["champ_meets"]["home"][k] and r["home_won"]) or
               (r["champ_meets"]["away"][k] and not r["home_won"])]
        champ_metric[k] = {"decisive": len(dec), "meet_won": len(won),
                           "rate": (len(won) / len(dec)) if dec else None}

    # Overlap: H2H pick vs champion-standalone pick.
    both = [r for r in evaluated if r["pick"] and r["champ_pick"]]
    agree = [r for r in both if r["pick"] == r["champ_pick"]]
    disagree = [r for r in both if r["pick"] != r["champ_pick"]]
    overlap = {
        "both_picked": len(both), "agree": len(agree),
        "agreement_rate": (len(agree) / len(both)) if both else None,
        "disagree": len(disagree),
        "disagree_champ_won": sum(1 for r in disagree if r["champ_pick_won"]),
        "disagree_h2h_won": sum(1 for r in disagree if r["pick_won"]),
        "corr_champ_diff_vs_h2h_diff": _pearson(
            [r["h_champ_votes"] - r["a_champ_votes"] for r in evaluated],
            [r["h_votes"] - r["a_votes"] for r in evaluated]),
    }

    # Power-score distribution + margin quartiles.
    all_scores = []
    for r in evaluated:
        all_scores.append(r["h_power"])
        all_scores.append(r["a_power"])
    all_scores.sort()
    n = len(all_scores)
    dist = {
        "n_teams": n,
        "above_zero": sum(1 for s in all_scores if s > 0),
        "above_zero_rate": (sum(1 for s in all_scores if s > 0) / n) if n else None,
        "mean": (sum(all_scores) / n) if n else None,
        "median": all_scores[n // 2] if n else None,
        "min": all_scores[0] if n else None,
        "max": all_scores[-1] if n else None,
    }
    power_picks = sorted([r for r in evaluated if r["power_pick"]],
                         key=lambda r: r["power_margin"])
    quartiles = []
    if power_picks:
        q = len(power_picks) // 4
        for i in range(4):
            grp = power_picks[i * q:(i + 1) * q if i < 3 else len(power_picks)]
            if not grp:
                continue
            w = sum(1 for r in grp if r["power_pick_won"])
            quartiles.append({
                "quartile": i + 1,
                "margin_min": grp[0]["power_margin"],
                "margin_max": grp[-1]["power_margin"],
                "picks": len(grp), "wins": w,
                "win_rate": w / len(grp),
            })
    return {
        "games_evaluated": len(evaluated),
        "with_champion": with_champ,
        "without_champion": without,
        "champion_standalone": standalone,
        "power_rank": power,
        "baseline": baseline,
        "champ_metric_win_rates": champ_metric,
        "champ_votes_total": {
            "home": sum(r["h_champ_votes"] for r in evaluated),
            "away": sum(r["a_champ_votes"] for r in evaluated)},
        "champ_votes_avg_per_game": {
            "home": (sum(r["h_champ_votes"] for r in evaluated) / len(evaluated)) if evaluated else None,
            "away": (sum(r["a_champ_votes"] for r in evaluated) / len(evaluated)) if evaluated else None},
        "h2h_votes_total": {
            "home": sum(r["h_votes"] for r in evaluated),
            "away": sum(r["a_votes"] for r in evaluated)},
        "home_points_total": (sum(1 for r in evaluated if not r["neutral"])
                              if use_home_point else 0),
        "overlap": overlap,
        "power_score_distribution": dist,
        "margin_quartiles": quartiles,
    }


def per_metric_h2h(evaluated):
    out = {}
    for k, _ in METRICS:
        dec = [r for r in evaluated if r["metric_wins"][k]]
        won = [r for r in dec
               if (r["metric_wins"][k] == "home" and r["home_won"]) or
               (r["metric_wins"][k] == "away" and not r["home_won"])]
        out[k] = {"decisive": len(dec), "meet_won": len(won),
                  "rate": (len(won) / len(dec)) if dec else None}
    return out


def arm_summary(name, evaluated, games_fetched, skipped, baseline_kind):
    picks = [r for r in evaluated if r["pick"]]
    wins = [r for r in picks if r["pick_won"]]
    if baseline_kind == "ap":
        raise ValueError("use ap summary separately")
    home_wins = sum(1 for r in evaluated if r["home_won"])
    return {
        "arm": name,
        "games_fetched": games_fetched,
        "games_evaluated": len(evaluated),
        "skipped_no_data": skipped,
        "picks_made": len(picks),
        "wins": len(wins),
        "losses": len(picks) - len(wins),
        "win_rate": (len(wins) / len(picks)) if picks else None,
        "baseline_home_win_rate": (home_wins / len(evaluated)) if evaluated else None,
        "per_metric": per_metric_h2h(evaluated),
    }


def save_games_json(path, games):
    Path(path).write_text(json.dumps(games, indent=1))


def save_eval_csv(path, evaluated):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "date", "home", "away", "home_score", "away_score",
                    "neutral", "h_votes", "a_votes", "pick", "pick_won",
                    "h_champ_votes", "a_champ_votes", "champ_pick",
                    "champ_pick_won", "with_pick", "with_pick_won",
                    "h_power", "a_power", "power_pick", "power_pick_won"])
        for r in evaluated:
            w.writerow([r["id"], r["date"], r["home"], r["away"],
                        r["home_score"], r["away_score"], r["neutral"],
                        r["h_votes"], r["a_votes"], r["pick"], r["pick_won"],
                        r.get("h_champ_votes"), r.get("a_champ_votes"),
                        r.get("champ_pick"), r.get("champ_pick_won"),
                        r.get("with_pick"), r.get("with_pick_won"),
                        round(r.get("h_power", 0), 4), round(r.get("a_power", 0), 4),
                        r.get("power_pick"), r.get("power_pick_won")])


def main():
    ap = argparse.ArgumentParser(description="Full CFB H2H backtest: Arms A/B/C + champion Parts 1-3.")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="reuse saved arm*_games.json instead of fetching")
    args = ap.parse_args()

    # ---------- fetch arms ----------
    if args.skip_fetch:
        games_a = json.loads((HERE / "h2h_backtest_cfb_armA_games.json").read_text())
        games_c = json.loads((HERE / "h2h_backtest_cfb_armC_games.json").read_text())
        games_b = json.loads((HERE / "h2h_backtest_cfb_armB_games.json").read_text())
        print("loaded saved games JSONs (no fetch)")
    else:
        print("=== Arm A: 2026-27 regular season (Aug 29 - Nov 8, 2026) ===", flush=True)
        games_a = fetch_games(*ARM_A)
        print(f"  fetched {len(games_a)} games", flush=True)
        save_games_json(HERE / "h2h_backtest_cfb_armA_games.json", games_a)
        print("=== Arm C: 2025-26 regular season (Aug 23 - Nov 29, 2025) ===", flush=True)
        games_c = fetch_games(*ARM_C)
        print(f"  fetched {len(games_c)} games", flush=True)
        save_games_json(HERE / "h2h_backtest_cfb_armC_games.json", games_c)
        print("=== Arm B: 2025-26 postseason (seasontype=3) ===", flush=True)
        games_b = fetch_postseason()
        print(f"  fetched {len(games_b)} games", flush=True)
        save_games_json(HERE / "h2h_backtest_cfb_armB_games.json", games_b)

    # ---------- evaluate H2H ----------
    print("=== evaluating H2H ===", flush=True)
    eval_a, fetched_a, skip_a = evaluate_sequential(games_a, use_home_point=True)
    eval_c, fetched_c, skip_c = evaluate_sequential(games_c, use_home_point=True)
    avgs_c, counts_c = season_averages(games_c)
    eval_b, fetched_b, skip_b = [], len(games_b), 0
    for g in games_b:
        hid, aid = g["home"]["id"], g["away"]["id"]
        if hid not in avgs_c or aid not in avgs_c:
            skip_b += 1
            continue
        eval_b.append(_evaluate_one(g, avgs_c[hid], avgs_c[aid],
                                    use_home_point=False))
    print(f"  Arm A: {len(eval_a)} evaluated, {skip_a} skipped no data", flush=True)
    print(f"  Arm C: {len(eval_c)} evaluated, {skip_c} skipped no data", flush=True)
    print(f"  Arm B: {len(eval_b)} evaluated, {skip_b} skipped no data", flush=True)

    # ---------- champions ----------
    print("=== champion season averages ===", flush=True)
    champ_avgs = {}
    for label, (dname, start, end) in CHAMP_WINDOWS.items():
        if label == "Indiana 2025":
            # Indiana's 2025 regular-season games are already in Arm C's fetch.
            team_games = [g for g in games_c
                          if g["home"]["name"] == dname or g["away"]["name"] == dname]
            print(f"  {label}: {len(team_games)} games (from Arm C fetch)", flush=True)
        else:
            team_games = fetch_team_games(dname, start, end)
            print(f"  {label}: {len(team_games)} games", flush=True)
        avgs, counts = season_averages(team_games)
        # Find the champion's own team id from its games.
        tid = None
        for g in team_games:
            for side in ("home", "away"):
                if g[side]["name"] == dname:
                    tid = g[side]["id"]
        champ_avgs[label] = {"team_id": tid, "games": len(team_games),
                             "averages": avgs[tid]}
    benchmark = {k: sum(c["averages"][k] for c in champ_avgs.values()) / len(champ_avgs)
                 for k, _ in METRICS}
    print("  benchmark (mean of 3 champions):", flush=True)
    for k, _ in METRICS:
        print(f"    {k:16s} {benchmark[k]:.3f}", flush=True)

    # ---------- AP Top 25 (Arm B baseline) ----------
    print("=== AP Top 25, Week 16 2025 ===", flush=True)
    ap_poll = fetch_ap_top25_2025()
    print(f"  {ap_poll['headline']} ({ap_poll['date']}), "
          f"{len(ap_poll['ranks'])} teams", flush=True)
    ap_ranks = {tid: int(r) for tid, r in ap_poll["ranks"].items()}

    # ---------- champion parts per arm ----------
    print("=== champion Parts 1-3 ===", flush=True)
    part_a = analyse_arm(eval_a, benchmark, use_home_point=True)
    part_c = analyse_arm(eval_c, benchmark, use_home_point=True)
    part_b = analyse_arm(eval_b, benchmark, use_home_point=False,
                         ap_ranks=ap_ranks)

    # Self-check: WITHOUT-champion recomputation must equal the H2H arm totals.
    def self_check(eval_records, arm_result):
        mism = 0
        for r in eval_records:
            if r["pick"] is None:
                continue
            if r["pick"] != ("home" if r["h_votes"] > r["a_votes"] else "away"):
                mism += 1
        return mism
    sc = {"armA": self_check(eval_a, None), "armB": self_check(eval_b, None),
          "armC": self_check(eval_c, None)}

    # Pooled Part 1 standalone + Part 3 power rank.
    pooled_eval = eval_a + eval_b + eval_c
    pooled_standalone = _pick_stats(pooled_eval, "champ_pick", "champ_pick_won")
    pooled_power = _pick_stats(pooled_eval, "power_pick", "power_pick_won")
    pooled_home = sum(1 for r in pooled_eval if r["home_won"])
    both = [r for r in pooled_eval if r["pick"] and r["champ_pick"]]
    agree = [r for r in both if r["pick"] == r["champ_pick"]]
    disagree = [r for r in both if r["pick"] != r["champ_pick"]]
    pooled_overlap = {
        "both_picked": len(both), "agree": len(agree),
        "agreement_rate": (len(agree) / len(both)) if both else None,
        "disagree": len(disagree),
        "disagree_champ_won": sum(1 for r in disagree if r["champ_pick_won"]),
        "disagree_h2h_won": sum(1 for r in disagree if r["pick_won"]),
        "corr_champ_diff_vs_h2h_diff": _pearson(
            [r["h_champ_votes"] - r["a_champ_votes"] for r in pooled_eval],
            [r["h_votes"] - r["a_votes"] for r in pooled_eval]),
    }
    pooled_scores = sorted([r["h_power"] for r in pooled_eval] +
                           [r["a_power"] for r in pooled_eval])
    n = len(pooled_scores)
    pooled_power_picks = sorted([r for r in pooled_eval if r["power_pick"]],
                                key=lambda r: r["power_margin"])
    pooled_quartiles = []
    if pooled_power_picks:
        q = len(pooled_power_picks) // 4
        for i in range(4):
            grp = pooled_power_picks[i * q:(i + 1) * q if i < 3 else len(pooled_power_picks)]
            if not grp:
                continue
            w = sum(1 for r in grp if r["power_pick_won"])
            pooled_quartiles.append({
                "quartile": i + 1, "margin_min": grp[0]["power_margin"],
                "margin_max": grp[-1]["power_margin"],
                "picks": len(grp), "wins": w, "win_rate": w / len(grp)})

    # ---------- arm summaries ----------
    summary_a = arm_summary("A", eval_a, fetched_a, skip_a, "home")
    summary_c = arm_summary("C", eval_c, fetched_c, skip_c, "home")
    # Arm B summary (AP baseline).
    picks_b = [r for r in eval_b if r["pick"]]
    wins_b = [r for r in picks_b if r["pick_won"]]
    ranked_b = [r for r in eval_b if r["home_id"] in ap_ranks and r["away_id"] in ap_ranks]
    base_wins_b = sum(1 for r in ranked_b
                      if (r["home_won"] and ap_ranks[r["home_id"]] < ap_ranks[r["away_id"]]) or
                      (not r["home_won"] and ap_ranks[r["away_id"]] < ap_ranks[r["home_id"]]))
    summary_b = {
        "arm": "B",
        "games_fetched": fetched_b,
        "games_evaluated": len(eval_b),
        "skipped_no_data": skip_b,
        "picks_made": len(picks_b),
        "wins": len(wins_b),
        "losses": len(picks_b) - len(wins_b),
        "win_rate": (len(wins_b) / len(picks_b)) if picks_b else None,
        "baseline_ap_ranked": {"n": len(ranked_b), "wins": base_wins_b,
                               "rate": (base_wins_b / len(ranked_b)) if ranked_b else None},
        "baseline_unranked_games": len(eval_b) - len(ranked_b),
        "per_metric": per_metric_h2h(eval_b),
    }

    # ---------- write outputs ----------
    (HERE / "h2h_backtest_cfb_armA_results.json").write_text(
        json.dumps({"spec": "h2h-backtest-cfb-spec-2026-11-10.md", **summary_a}, indent=1))
    (HERE / "h2h_backtest_cfb_armB_results.json").write_text(
        json.dumps({"spec": "h2h-backtest-cfb-spec-2026-11-10.md", **summary_b}, indent=1))
    (HERE / "h2h_backtest_cfb_armC_results.json").write_text(
        json.dumps({"spec": "h2h-backtest-cfb-spec-2026-11-10.md", **summary_c}, indent=1))
    champ_out = {
        "spec": "h2h-backtest-cfb-spec-2026-11-10.md",
        "metrics": [k for k, _ in METRICS],
        "champion_benchmarks": {
            "armA": {"champions": list(CHAMP_WINDOWS.keys()), "benchmark": benchmark},
            "armB": {"champions": list(CHAMP_WINDOWS.keys()), "benchmark": benchmark,
                     "note": "Per the spec's explicit Arm B instruction all 3 champions form the benchmark. FLAG: Indiana was not decided champion until the Jan 20, 2026 championship game, so Dec/early-Jan Arm B games benchmark against a champion not yet decided — the spec's parenthetical justifying this is wrong on the date; the instruction itself was followed, not silently changed."},
            "armC": {"champions": list(CHAMP_WINDOWS.keys()), "benchmark": benchmark},
        },
        "champion_season_averages": {k: {"team_id": v["team_id"], "games": v["games"],
                                         "averages": v["averages"]}
                                     for k, v in champ_avgs.items()},
        "self_check_without_champion_mismatches": sc,
        "part1": {"armA": part_a, "armB": part_b, "armC": part_c,
                  "pooled_standalone": pooled_standalone,
                  "pooled_overlap": pooled_overlap},
        "part3": {"armA": part_a, "armB": part_b, "armC": part_c,
                  "pooled_power_rank": pooled_power,
                  "pooled_power_score_distribution": {
                      "n_teams": n,
                      "above_zero": sum(1 for s in pooled_scores if s > 0),
                      "above_zero_rate": (sum(1 for s in pooled_scores if s > 0) / n) if n else None,
                      "mean": (sum(pooled_scores) / n) if n else None,
                      "median": pooled_scores[n // 2] if n else None,
                      "min": pooled_scores[0] if n else None,
                      "max": pooled_scores[-1] if n else None},
                  "pooled_margin_quartiles": pooled_quartiles},
    }
    (HERE / "h2h_backtest_cfb_champion_results.json").write_text(
        json.dumps(champ_out, indent=1))
    save_eval_csv(HERE / "h2h_backtest_cfb_armA_games_eval.csv", eval_a)
    save_eval_csv(HERE / "h2h_backtest_cfb_armB_games_eval.csv", eval_b)
    save_eval_csv(HERE / "h2h_backtest_cfb_armC_games_eval.csv", eval_c)

    # ---------- console summary ----------
    def fmt(r):
        return f"{100*r:.1f}%" if r is not None else "n/a"
    for name, s in [("A", summary_a), ("B", summary_b), ("C", summary_c)]:
        print(f"\nArm {name}: fetched {s['games_fetched']}, evaluated {s['games_evaluated']}, "
              f"skipped {s['skipped_no_data']}", flush=True)
        print(f"  picks {s['picks_made']}, wins {s['wins']}, "
              f"H2H {fmt(s['win_rate'])}", flush=True)
        if name == "B":
            b = s["baseline_ap_ranked"]
            print(f"  baseline (higher AP-ranked): {fmt(b['rate'])} on {b['n']} ranked games "
                  f"({s['baseline_unranked_games']} unranked, no baseline pick)", flush=True)
        else:
            print(f"  baseline (home): {fmt(s['baseline_home_win_rate'])}", flush=True)
    for name, p in [("A", part_a), ("B", part_b), ("C", part_c)]:
        print(f"\nChampion Arm {name}: standalone {fmt(p['champion_standalone']['win_rate'])} "
              f"({p['champion_standalone']['picks']} picks) | "
              f"with {fmt(p['with_champion']['win_rate'])} vs without {fmt(p['without_champion']['win_rate'])} | "
              f"power {fmt(p['power_rank']['win_rate'])} ({p['power_rank']['picks']} picks)", flush=True)
    print(f"\nPooled standalone: {fmt(pooled_standalone['win_rate'])} "
          f"({pooled_standalone['picks']} picks)", flush=True)
    print(f"Pooled power rank: {fmt(pooled_power['win_rate'])} "
          f"({pooled_power['picks']} picks)", flush=True)
    print(f"Self-check mismatches: {sc}", flush=True)


if __name__ == "__main__":
    main()
