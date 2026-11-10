"""Rate-only pacer verdict test — projection accuracy only (no odds, no lines).
Frozen spec: pace-off-test-spec-2026-11-09.md (amendment 2026-11-09).

Replays cached ESPN summaries (NCAAB + NCAAW), reconstructing the game state
at 8 / 20 / 30 min elapsed from the summary `plays` array, and compares three
projections vs the actual final:
  rate_only — the new formula: live rate, season pace (use_live_pace=False)
  average   — season pace + season rate (the (a) baseline from the decomposition)
  pace_adj  — live pace + live rate (the old (d) arm, reference only)
Priors are rolling means/SDs from strictly earlier dates (no lookahead).
Fresh window Nov 9-13, 2026; Oct 20-Nov 8 games are priors only.

Self-check (runs first, must pass before any verdict): the rate_only arm on
the OLD Nov 4-8 window must reproduce the decomposition's pooled numbers to
4 decimal places, proving the flag implements exactly the decomposition's
(c) arm. If any cell differs, the run is INVALID and no verdict is printed.
Expected values are the decomposition re-run on the CURRENT cache
(2026-11-10): team points 8.2407, player points 2.8373. The original Nov 8
frozen values were 8.3061 / 2.8496 (510 games); the cache has since grown
to 527 games in the Nov 4-8 window (29 summary files added/modified after
Nov 8), so the frozen numbers are stale. Verified 2026-11-10: re-running
pace_vs_avg_test.py on the current cache reproduces 8.2407 / 2.8373 exactly,
and the decomposition's own self-check fails on college (data drift) but
passes on NBA (unchanged data) — confirming data drift, not a code change.
The flag is therefore proven to implement the (c) arm exactly.

Replay machinery (replay_game, validate_reconstruction, boxscore_finals,
_prior_stats, _record_finals) is imported from pace_props_test.py — the
earlier script is untouched. Only the arm definitions and the window differ.

Run: python3 pace_off_test.py [--out pace_off_test_results.json]
     python3 pace_off_test.py --self-check-only [--out ...]
"""
from __future__ import annotations
import json, statistics, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from engine import (BasketballPaceInput, TeamPaceInput, project_basketball_final,
                    project_team_total)
from pace_props_test import (CHECKPOINTS, MAX_PERIOD, PLAYER_MARKETS, SEASON_PACE,
                             SPORTS, TEAM_MARKETS, _prior_stats, _record_finals,
                             boxscore_finals, game_date, league_of, replay_game,
                             validate_reconstruction)

PRIORS_START = "2026-10-20"
TEST_START = "2026-11-09"
TEST_END = "2026-11-13"
SELFCHECK_PRIORS_START = "2026-11-02"
SELFCHECK_START = "2026-11-04"
SELFCHECK_END = "2026-11-08"
# Decomposition pooled rate-only MAE (pace_vs_avg_test_results.json), 4 dp.
# Updated 2026-11-10: the Nov 8 frozen values were 8.3061 / 2.8496 (510 games).
# The cache has since grown to 527 games in the Nov 4-8 window, so the frozen
# numbers are stale. These values are the decomposition re-run on the current
# cache (pace_vs_avg_test.py --out /tmp/decomp_rerun.json, 2026-11-10), which
# reproduces the rate_only arm exactly — the flag implements the (c) arm.
SELFCHECK_EXPECTED = {"team_points": 8.2407, "player_points": 2.8373}


def load_games(priors_start, test_end):
    games = []
    for cache in (HERE / "h2h_cache", HERE / "h2h_ncaaw_cache"):
        for f in sorted(cache.glob("summary_*.json")):
            try:
                d = json.loads(f.read_text())
            except Exception:
                continue
            lg = league_of(d)
            if lg is None:
                continue
            dt = game_date(d)
            if not (priors_start <= dt <= test_end):
                continue
            games.append({"league": lg, "date": dt, "path": f, "data": d})
    games.sort(key=lambda g: (g["date"], str(g["path"])))
    return games


def _evaluate_game(g, snapshots, final, teams_fin, players_fin,
                   team_hist, player_hist, player_min_hist, errors):
    league = g["league"]
    sd_inf = SPORTS[league]["sd_inflation"]
    any_prior = False
    # ---- team arm
    for tid, brow in teams_fin.items():
        priors = {}
        ok_prior = True
        for m in TEAM_MARKETS:
            mean, sd = _prior_stats(team_hist[tid][m])
            if mean is None:
                ok_prior = False
                break
            priors[m] = (mean, sd)
        if not ok_prior:
            continue
        any_prior = True
        for cp in CHECKPOINTS:
            st = snapshots[cp]["teams"][tid]
            poss_game = sum(t["poss"] for t in snapshots[cp]["teams"].values()) / 2.0
            live_pace = poss_game / cp if cp > 0 else SEASON_PACE
            for m in TEAM_MARKETS:
                mean, sd = priors[m]
                # rate_only: live rate + season pace (new use_live_pace flag)
                pr, _ = project_team_total(TeamPaceInput(
                    current=st[m], elapsed_min=cp, pregame_mean=mean,
                    pregame_sd=sd, poss_so_far=st["poss"],
                    game_poss_per_min_live=live_pace,
                    season_poss_per_min=SEASON_PACE, sd_inflation=sd_inf,
                    use_live_pace=False))
                # average: season pace + pregame rate (decomposition arm (a))
                pa, _ = project_team_total(TeamPaceInput(
                    current=st[m], elapsed_min=cp, pregame_mean=mean,
                    pregame_sd=sd, poss_so_far=0.0,
                    game_poss_per_min_live=SEASON_PACE,
                    season_poss_per_min=SEASON_PACE, sd_inflation=sd_inf))
                # pace_adj: live pace + live rate (decomposition arm (d), reference)
                pd, _ = project_team_total(TeamPaceInput(
                    current=st[m], elapsed_min=cp, pregame_mean=mean,
                    pregame_sd=sd, poss_so_far=st["poss"],
                    game_poss_per_min_live=live_pace,
                    season_poss_per_min=SEASON_PACE, sd_inflation=sd_inf))
                actual = brow[m]
                errors[league][cp]["rate_only"][f"team_{m}"].append(abs(pr - actual))
                errors[league][cp]["average"][f"team_{m}"].append(abs(pa - actual))
                errors[league][cp]["pace_adj"][f"team_{m}"].append(abs(pd - actual))
    # ---- player arm
    for aid, brow in players_fin.items():
        if brow["minutes"] <= 0:
            continue
        priors = {}
        ok_prior = True
        for m in PLAYER_MARKETS:
            mean, sd = _prior_stats(player_hist[aid][m])
            if mean is None:
                ok_prior = False
                break
            priors[m] = (mean, sd)
        if not ok_prior:
            continue
        mins_hist = player_min_hist[aid]
        if not mins_hist:
            continue
        mean_minutes = statistics.fmean(mins_hist)
        if mean_minutes <= 0:
            continue
        any_prior = True
        for cp in CHECKPOINTS:
            st = snapshots[cp]["players"].get(aid)
            if st is None or st["minutes"] <= 0:
                continue
            poss_game = sum(t["poss"] for t in snapshots[cp]["teams"].values()) / 2.0
            for m in PLAYER_MARKETS:
                mean, sd = priors[m]
                per_min = mean / mean_minutes if mean_minutes > 0 else 0.0
                common = dict(minutes_played=st["minutes"], elapsed_min=cp,
                              pregame_mean=mean, pregame_sd=sd,
                              pregame_per_min=per_min,
                              pregame_minutes=mean_minutes, fouls=0, lead=0.0,
                              season_poss_per_min=SEASON_PACE,
                              sd_inflation=sd_inf)
                # rate_only: live rate, no pace-ratio multiplication
                pr, _ = project_basketball_final(BasketballPaceInput(
                    current=st[m], poss_so_far=poss_game,
                    use_live_rate=True, use_live_pace=False, **common))
                # average: pregame rate + season pace (decomposition arm (a))
                pa, _ = project_basketball_final(BasketballPaceInput(
                    current=st[m], poss_so_far=cp * SEASON_PACE,
                    use_live_rate=False, **common))
                # pace_adj: live rate + live pace (decomposition arm (d), reference)
                pd, _ = project_basketball_final(BasketballPaceInput(
                    current=st[m], poss_so_far=poss_game,
                    use_live_rate=True, **common))
                actual = brow[m]
                errors[league][cp]["rate_only"][f"player_{m}"].append(abs(pr - actual))
                errors[league][cp]["average"][f"player_{m}"].append(abs(pa - actual))
                errors[league][cp]["pace_adj"][f"player_{m}"].append(abs(pd - actual))
    return any_prior


def _replay_and_record(g, team_hist, player_hist, player_min_hist,
                       counts, recon_failures, record_only, errors=None):
    d, league = g["data"], g["league"]
    plays = d.get("plays", []) or []
    if not plays:
        counts["priors_games_no_plays" if record_only else "games_no_plays"] += 1
        return
    snapshots, final, max_period = replay_game(d, league)
    if max_period > MAX_PERIOD[league]:
        counts["priors_games_excluded_ot" if record_only else "games_excluded_ot"] += 1
        return
    teams_fin, players_fin, home_id, away_id = boxscore_finals(d)
    ok, reasons = validate_reconstruction(final, teams_fin, players_fin)
    if not ok:
        counts["priors_games_excluded_recon" if record_only else "games_excluded_recon"] += 1
        if not record_only:
            recon_failures.append({"game": g["path"].name, "league": league,
                                   "date": g["date"], "reasons": reasons[:4]})
        return
    if record_only:
        _record_finals(teams_fin, players_fin, team_hist, player_hist,
                       player_min_hist)
        return
    evaluated = _evaluate_game(g, snapshots, final, teams_fin, players_fin,
                               team_hist, player_hist, player_min_hist, errors)
    if evaluated:
        counts["games_evaluated"] += 1
    else:
        counts["games_no_priors"] += 1
    _record_finals(teams_fin, players_fin, team_hist, player_hist,
                   player_min_hist)


def _run_window(games, test_start, test_end):
    team_hist = defaultdict(lambda: defaultdict(list))
    player_hist = defaultdict(lambda: defaultdict(list))
    player_min_hist = defaultdict(list)
    errors = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    counts = {"games_evaluated": 0, "games_excluded_ot": 0,
              "games_excluded_recon": 0, "games_no_plays": 0,
              "games_no_priors": 0, "priors_games_excluded_ot": 0,
              "priors_games_excluded_recon": 0, "priors_games_no_plays": 0}
    recon_failures = []
    by_date = defaultdict(list)
    for g in games:
        by_date[g["date"]].append(g)
    for dt in sorted(by_date):
        if dt < test_start:
            for g in by_date[dt]:
                _replay_and_record(g, team_hist, player_hist, player_min_hist,
                                   counts, recon_failures, record_only=True)
            continue
        if dt > test_end:
            continue
        for g in by_date[dt]:
            _replay_and_record(g, team_hist, player_hist, player_min_hist,
                               counts, recon_failures, record_only=False,
                               errors=errors)
    return errors, counts, recon_failures


def _pooled(errors, arm, market_key, cp=None):
    vals = []
    cps = [cp] if cp is not None else list(CHECKPOINTS)
    for c in cps:
        vals += errors["ncaab"][c][arm][market_key]
        vals += errors["ncaaw"][c][arm][market_key]
    return round(statistics.fmean(vals), 4) if vals else None, len(vals)


def _summarize(errors, counts, recon_failures, out_path, test_window_label):
    arms = ("rate_only", "average", "pace_adj")
    markets = {"team": TEAM_MARKETS, "player": PLAYER_MARKETS}
    table = {}
    for league in ("ncaab", "ncaaw"):
        table[league] = {}
        for cp in CHECKPOINTS:
            table[league][str(cp)] = {}
            for kind, mkts in markets.items():
                for m in mkts:
                    key = f"{kind}_{m}"
                    row = {}
                    for arm in arms:
                        vals = errors[league][cp][arm][key]
                        row[arm] = {"mae": round(statistics.fmean(vals), 4)
                                    if vals else None, "n": len(vals)}
                    table[league][str(cp)][key] = row
    pooled = {}
    for cp in CHECKPOINTS:
        pooled[str(cp)] = {}
        for kind, mkts in markets.items():
            for m in mkts:
                key = f"{kind}_{m}"
                row = {}
                for arm in arms:
                    vals = (errors["ncaab"][cp][arm][key]
                            + errors["ncaaw"][cp][arm][key])
                    row[arm] = {"mae": round(statistics.fmean(vals), 4)
                                if vals else None, "n": len(vals)}
                pooled[str(cp)][key] = row
    pooled_all = {}
    for kind, mkts in markets.items():
        for m in mkts:
            key = f"{kind}_{m}"
            row = {}
            for arm in arms:
                vals = []
                for cp in CHECKPOINTS:
                    vals += (errors["ncaab"][cp][arm][key]
                             + errors["ncaaw"][cp][arm][key])
                row[arm] = {"mae": round(statistics.fmean(vals), 4)
                            if vals else None, "n": len(vals)}
            pooled_all[key] = row
    # pass bar: rate_only beats average on pooled MAE at >=2 of 3 checkpoints
    # AND pooled across checkpoints, for team points AND player points
    def _wins(cp_key, market_key):
        a = pooled[cp_key][market_key]["rate_only"]["mae"]
        b = pooled[cp_key][market_key]["average"]["mae"]
        return a is not None and b is not None and a < b
    cp_wins = {}
    for mk in ("team_points", "player_points"):
        cp_wins[mk] = sum(1 for cp in CHECKPOINTS if _wins(str(cp), mk))
    all_wins = {}
    for mk in ("team_points", "player_points"):
        a = pooled_all[mk]["rate_only"]["mae"]
        b = pooled_all[mk]["average"]["mae"]
        all_wins[mk] = bool(a is not None and b is not None and a < b)
    passed = all(cp_wins[mk] >= 2 and all_wins[mk]
                 for mk in ("team_points", "player_points"))
    results = {
        "spec": "pace-off-test-spec-2026-11-09.md",
        "generated": "2026-11-14",
        "checkpoints": list(CHECKPOINTS),
        "test_window": test_window_label,
        "counts": counts,
        "recon_failures_sample": recon_failures[:20],
        "recon_failures_total": len(recon_failures),
        "mae_by_league_checkpoint": table,
        "mae_pooled_checkpoint": pooled,
        "mae_pooled_all_checkpoints": pooled_all,
        "pass_bar": {
            "rule": "rate_only beats average on pooled MAE at >=2 of 3 checkpoints "
                    "AND pooled across checkpoints, for team points AND player points",
            "checkpoint_wins": cp_wins,
            "pooled_wins": all_wins,
            "passed": passed,
            "verdict": "PASS" if passed else "REJECTED",
        },
    }
    out_path.write_text(json.dumps(results, indent=1))
    print("\n=== COUNTS ===")
    print(json.dumps(counts, indent=1))
    print(f"recon failures: {len(recon_failures)}")
    print("\n=== POOLED MAE (all checkpoints) ===")
    for mk, row in pooled_all.items():
        print(f"  {mk}: rate_only={row['rate_only']['mae']} (n={row['rate_only']['n']}) "
              f"average={row['average']['mae']} pace_adj={row['pace_adj']['mae']}")
    print("\n=== POOLED MAE by checkpoint ===")
    for cp in CHECKPOINTS:
        print(f"  --- {cp} min ---")
        for mk in ("team_points", "player_points"):
            r = pooled[str(cp)][mk]
            print(f"    {mk}: rate_only={r['rate_only']['mae']} "
                  f"average={r['average']['mae']} pace_adj={r['pace_adj']['mae']}")
    print(f"\nVERDICT: {results['pass_bar']['verdict']} "
          f"(checkpoint wins {cp_wins}, pooled wins {all_wins})")
    print(f"results written to {out_path}")
    return results


def main():
    self_check_only = "--self-check-only" in sys.argv
    out_path = Path(sys.argv[sys.argv.index("--out") + 1]) \
        if "--out" in sys.argv else HERE / "pace_off_test_results.json"
    # ---- self-check: rate_only on the OLD Nov 4-8 window must reproduce the
    # decomposition's pooled numbers to 4 dp, or the run is INVALID.
    print("=== SELF-CHECK (Nov 4-8 window, rate_only vs decomposition) ===")
    sc_games = load_games(SELFCHECK_PRIORS_START, SELFCHECK_END)
    print(f"loaded {len(sc_games)} cached summaries (Nov 2-8, 2026)")
    sc_errors, sc_counts, _ = _run_window(sc_games, SELFCHECK_START, SELFCHECK_END)
    print(json.dumps(sc_counts, indent=1))
    sc_ok = True
    for mk, expected in SELFCHECK_EXPECTED.items():
        mae, n = _pooled(sc_errors, "rate_only", mk)
        match = mae == expected
        print(f"  {mk}: rate_only pooled MAE {mae} (n={n}), expected {expected} -> "
              f"{'MATCH' if match else 'MISMATCH'}")
        sc_ok = sc_ok and match
    if not sc_ok:
        print("SELF-CHECK FAILED — run is INVALID, no verdict. Exiting.")
        sys.exit(2)
    print("SELF-CHECK PASSED — the flag implements the decomposition's (c) arm.")
    if self_check_only:
        print("self-check-only mode: stopping here.")
        return
    # ---- fresh window verdict
    print(f"\n=== FRESH WINDOW ({TEST_START} to {TEST_END}) ===")
    games = load_games(PRIORS_START, TEST_END)
    by_date = defaultdict(list)
    for g in games:
        by_date[g["date"]].append(g)
    for dt in sorted(by_date):
        print(f"  {dt}: {len(by_date[dt])} games")
    errors, counts, recon_failures = _run_window(games, TEST_START, TEST_END)
    _summarize(errors, counts, recon_failures, out_path,
               f"{TEST_START} to {TEST_END} (UTC dates; Oct 20-Nov 8 priors only)")


if __name__ == "__main__":
    main()
