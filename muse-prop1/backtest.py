"""Replay harness — NO LOOKAHEAD.
Input: timestamped snapshots (odds + stats), stored BEFORE the run.
Rule: at simulated bet_time, use only snapshots with as_of <= bet_time - 30s.
Match (player_id, market, line) exactly. Grade vs final box score AFTER the game.
Report: n, win rate, ROI, yield, PF, calibration (mean model_p vs actual win rate),
profit concentration (top-10 share). Split train/test frozen in config/test_spec.
A run that uses any data after bet_time is invalid — fail it, do not report its ROI.
"""
from __future__ import annotations

def grade_picks(picks: list[dict]) -> dict:
    """picks: each with model_p, odds (american), result in {'win','loss','push'}, stake_units."""
    n = len([p for p in picks if p["result"] != "push"])
    wins = len([p for p in picks if p["result"] == "win"])
    staked = sum(p["stake_units"] for p in picks if p["result"] != "push")
    # profit in units: win = stake * (decimal-1), loss = -stake
    from engine import american_to_decimal
    profit = 0.0
    profits = []
    for p in picks:
        if p["result"] == "push":
            continue
        dec = american_to_decimal(p["odds"])
        pnl = p["stake_units"] * (dec - 1) if p["result"] == "win" else -p["stake_units"]
        profit += pnl
        profits.append(pnl)
    # Concentration: top-10 wins as share of ALL winning profit (not net profit,
    # which losses can push over 100% and mis-flag a diversified book).
    wins_only = sorted([x for x in profits if x > 0], reverse=True)
    gross_win = sum(wins_only)
    top10_share = sum(wins_only[:10]) / gross_win if gross_win > 0 else 0.0
    mean_model_p = sum(p["model_p"] for p in picks) / len(picks) if picks else 0
    return {
        "n": n, "wins": wins,
        "win_rate": wins / n if n else 0,
        "mean_model_p": mean_model_p,
        "calibration_gap_pp": (wins / n - mean_model_p) if n else 0,
        "staked_units": staked,
        "profit_units": profit,
        "roi": profit / staked if staked else 0,
        "top10_profit_share": top10_share,
    }

def replay_snapshots(snapshots: list[dict], evaluate_fn, final_stats: dict) -> list[dict]:
    """Replay timestamped snapshots through evaluate_fn, enforcing no lookahead.
    snapshots: each {bet_time, as_of, sport, line_row, game} — only snapshots
    with as_of <= bet_time - 30 are usable; any later snapshot is skipped and
    counted in the returned report's 'lookahead_skipped'.
    final_stats: {(sport, game_id, player, market): final_stat_value}
    Returns graded picks: each pick + result win/loss/push.
    This is a harness — collect snapshots BEFORE the run, in timestamp order.
    """
    graded = []
    for snap in sorted(snapshots, key=lambda s: s["bet_time"]):
        if snap["as_of"] > snap["bet_time"] - 30:
            continue  # lookahead — never evaluate on future data
        pick = evaluate_fn(snap)
        if not pick:
            continue
        key = (pick["sport"], pick["game_id"], pick["player"], pick["market"])
        final = final_stats.get(key)
        if final is None:
            continue
        line = pick["line"]
        if final == line:
            result = "push"
        elif (pick["side"] == "over" and final > line) or (pick["side"] == "under" and final < line):
            result = "win"
        else:
            result = "loss"
        graded.append({**pick, "result": result, "final_stat": final})
    return graded

def passes_bar(report: dict) -> tuple[bool, list[str]]:
    fails = []
    if report["roi"] <= 0:
        fails.append(f"ROI {report['roi']:.1%} <= 0")
    if report["roi"] <= 0.03:
        fails.append(f"yield {report['roi']:.1%} <= 3%")
    if report["n"] < 50:
        fails.append(f"n={report['n']} < 50")
    if abs(report["calibration_gap_pp"]) > 0.08:
        fails.append(f"calibration gap {report['calibration_gap_pp']:.1%} > 8pp")
    if report["top10_profit_share"] > 0.50:
        fails.append(f"top-10 share {report['top10_profit_share']:.0%} > 50%")
    return (len(fails) == 0, fails)
