#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEAM_STATS_CACHE_PATH = os.path.join(BASE_DIR, "data_engine", "team_stats_cache.json")
BET_SNAPSHOT_LOG = os.path.join(BASE_DIR, "bet_snapshots.jsonl")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def american_to_decimal(odds: int) -> float:
    if odds > 0:
        return 1.0 + (odds / 100.0)
    return 1.0 + (100.0 / abs(odds))


def american_to_b(odds: int) -> float:
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)


def implied_prob_from_american(odds: int) -> float:
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def calculate_edge(model_prob: float, implied_prob: float) -> float:
    return model_prob - implied_prob


def calculate_ev(model_prob: float, odds: int) -> float:
    dec = american_to_decimal(odds)
    return (model_prob * (dec - 1.0)) - (1.0 - model_prob)


def full_kelly_fraction(p: float, odds: int) -> float:
    b = american_to_b(odds)
    q = 1.0 - p
    f = ((b * p) - q) / b
    return max(0.0, f)


def quarter_kelly_fraction(p: float, odds: int) -> float:
    return full_kelly_fraction(p, odds) * 0.25


def calculate_volatility_scalar(minutes_left: int, score_diff: int) -> float:
    if minutes_left <= 2 and abs(score_diff) <= 6:
        return 0.50
    if minutes_left <= 4 and abs(score_diff) <= 10:
        return 0.70
    if minutes_left <= 8 and abs(score_diff) <= 12:
        return 0.85
    return 1.00


@dataclass
class InefficiencyResult:
    gap: float
    direction: str
    is_significant: bool

    def to_alert_string(self) -> str:
        if not self.is_significant:
            return "No significant market inefficiency."
        return f"{self.direction} gap {self.gap:+.4f}"


LEAGUE_AVG_PROFILE: dict[str, float] = {
    "off_3pa_rate": 0.38,
    "off_3p_pct": 0.34,
    "off_2p_pct": 0.51,
    "off_ft_rate": 0.20,
    "off_ft_pct": 0.71,
    "off_tov_rate": 0.17,
    "off_orb_rate": 0.30,
    "def_3pa_rate_allowed": 0.38,
    "def_3p_pct_allowed": 0.34,
    "def_2p_pct_allowed": 0.51,
    "def_ft_rate_allowed": 0.20,
    "def_tov_forced_rate": 0.17,
    "def_drb_rate": 0.70,
}

TEAM_ALIASES = {
    "unc": "north carolina",
    "tar heels": "north carolina",
    "uconn": "connecticut",
    "ole miss": "mississippi",
    "msu": "michigan state",
    "usu": "utah state",
    "lsu": "louisiana state",
    "pitt": "pittsburgh",
}


def normalize_team_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return TEAM_ALIASES.get(name, name)


def build_sample_team_stats_cache() -> dict[str, dict[str, float]]:
    return {
        "duke": {
            "off_3pa_rate": 0.42,
            "off_3p_pct": 0.38,
            "off_2p_pct": 0.55,
            "off_ft_rate": 0.18,
            "off_ft_pct": 0.75,
            "off_tov_rate": 0.16,
            "off_orb_rate": 0.31,
            "def_3pa_rate_allowed": 0.34,
            "def_3p_pct_allowed": 0.30,
            "def_2p_pct_allowed": 0.47,
            "def_ft_rate_allowed": 0.18,
            "def_tov_forced_rate": 0.18,
            "def_drb_rate": 0.72,
        },
        "auburn": {
            "off_3pa_rate": 0.36,
            "off_3p_pct": 0.34,
            "off_2p_pct": 0.53,
            "off_ft_rate": 0.22,
            "off_ft_pct": 0.72,
            "off_tov_rate": 0.17,
            "off_orb_rate": 0.33,
            "def_3pa_rate_allowed": 0.34,
            "def_3p_pct_allowed": 0.30,
            "def_2p_pct_allowed": 0.47,
            "def_ft_rate_allowed": 0.19,
            "def_tov_forced_rate": 0.18,
            "def_drb_rate": 0.71,
        },
        "north carolina": {
            "off_3pa_rate": 0.38,
            "off_3p_pct": 0.35,
            "off_2p_pct": 0.54,
            "off_ft_rate": 0.24,
            "off_ft_pct": 0.74,
            "off_tov_rate": 0.15,
            "off_orb_rate": 0.34,
            "def_3pa_rate_allowed": 0.36,
            "def_3p_pct_allowed": 0.32,
            "def_2p_pct_allowed": 0.49,
            "def_ft_rate_allowed": 0.21,
            "def_tov_forced_rate": 0.16,
            "def_drb_rate": 0.69,
        },
        "connecticut": {
            "off_3pa_rate": 0.41,
            "off_3p_pct": 0.36,
            "off_2p_pct": 0.56,
            "off_ft_rate": 0.19,
            "off_ft_pct": 0.76,
            "off_tov_rate": 0.15,
            "off_orb_rate": 0.35,
            "def_3pa_rate_allowed": 0.35,
            "def_3p_pct_allowed": 0.31,
            "def_2p_pct_allowed": 0.46,
            "def_ft_rate_allowed": 0.18,
            "def_tov_forced_rate": 0.17,
            "def_drb_rate": 0.73,
        },
    }


def ensure_team_stats_cache() -> None:
    os.makedirs(os.path.dirname(TEAM_STATS_CACHE_PATH), exist_ok=True)
    if os.path.exists(TEAM_STATS_CACHE_PATH):
        return
    with open(TEAM_STATS_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(build_sample_team_stats_cache(), f, indent=2, sort_keys=True)


def load_team_stats_cache() -> dict[str, Any]:
    ensure_team_stats_cache()
    try:
        with open(TEAM_STATS_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_team_stat_profile(team_name: str) -> dict[str, float]:
    cache = load_team_stats_cache()
    norm = normalize_team_name(team_name)

    if norm in cache:
        return {**LEAGUE_AVG_PROFILE, **cache[norm]}

    for raw_key, profile in cache.items():
        if normalize_team_name(raw_key) == norm:
            return {**LEAGUE_AVG_PROFILE, **profile}

    return LEAGUE_AVG_PROFILE.copy()


def blend_stat(off_stat: float, def_stat: float) -> float:
    return (off_stat + def_stat) / 2.0


def build_offense_weights_from_stats(
    off_3pa_rate: float,
    off_3p_pct: float,
    off_2p_pct: float,
    off_ft_rate: float,
    off_ft_pct: float,
    off_tov_rate: float,
    is_second_chance: bool = False,
) -> dict[str, float]:
    if is_second_chance:
        off_2p_pct += 0.06
        off_3p_pct += 0.02
        off_ft_rate += 0.03
        off_tov_rate -= 0.03

    off_3pa_rate = _clamp(off_3pa_rate, 0.20, 0.60)
    off_3p_pct = _clamp(off_3p_pct, 0.22, 0.50)
    off_2p_pct = _clamp(off_2p_pct, 0.35, 0.72)
    off_ft_rate = _clamp(off_ft_rate, 0.05, 0.45)
    off_ft_pct = _clamp(off_ft_pct, 0.50, 0.90)
    off_tov_rate = _clamp(off_tov_rate, 0.05, 0.30)

    w_tov = off_tov_rate
    shot_attempt_rate = 1.0 - w_tov

    w3 = shot_attempt_rate * off_3pa_rate * off_3p_pct
    w2 = shot_attempt_rate * (
        ((1.0 - off_3pa_rate) * off_2p_pct)
        + (off_ft_rate * (off_ft_pct ** 2))
    )
    w1 = shot_attempt_rate * (
        off_ft_rate * (2.0 * off_ft_pct * (1.0 - off_ft_pct))
    )
    w_miss = max(0.0, 1.0 - (w_tov + w1 + w2 + w3))

    total = w_tov + w_miss + w1 + w2 + w3
    if total <= 0:
        return {
            "w_tov": 0.17,
            "w_miss": 0.37,
            "w1": 0.08,
            "w2": 0.25,
            "w3": 0.13,
        }

    return {
        "w_tov": w_tov / total,
        "w_miss": w_miss / total,
        "w1": w1 / total,
        "w2": w2 / total,
        "w3": w3 / total,
    }


def possession_expected_value(weights: dict[str, float]) -> float:
    return (
        (1.0 * weights["w1"])
        + (2.0 * weights["w2"])
        + (3.0 * weights["w3"])
    )


def estimate_auto_pace(
    mode: str,
    home_score: int,
    away_score: int,
    minutes_left: int,
    seconds_left: int,
    baseline_ppm: float,
) -> tuple[float, float]:
    total_scored = home_score + away_score
    remaining_minutes = minutes_left + (seconds_left / 60.0)
    segment_length = 20.0 if mode in {"1h", "2h"} else 40.0
    elapsed = max(0.5, segment_length - remaining_minutes)
    pts_per_min = total_scored / elapsed

    recent_ppm = _clamp(pts_per_min / 1.0, 1.8, 4.2)
    baseline_ppm = _clamp(baseline_ppm, 1.8, 4.2)
    return recent_ppm, baseline_ppm


def apply_score_differential_pace_model(
    mode: str,
    score_diff: int,
    recent_ppm: float,
    baseline_ppm: float,
    minutes_left: int,
) -> tuple[float, str]:
    blended = (0.60 * recent_ppm) + (0.40 * baseline_ppm)
    note = "neutral pace blend"

    if mode == "2h":
        if abs(score_diff) <= 6 and minutes_left <= 12:
            blended *= 1.06
            note = "close 2H pace-up applied"
        elif abs(score_diff) >= 12 and minutes_left <= 12:
            blended *= 0.94
            note = "wide-lead 2H pace-down applied"
    elif mode == "full":
        if abs(score_diff) <= 5 and minutes_left <= 8:
            blended *= 1.04
            note = "close-game pace-up applied"
        elif abs(score_diff) >= 15 and minutes_left <= 8:
            blended *= 0.93
            note = "blowout pace-down applied"

    return _clamp(blended, 1.8, 4.4), note


def get_late_game_variance_scalars(
    remaining_minutes: float,
    score_diff: float,
) -> tuple[float, float]:
    poss_time_sd = 5.0
    scoring_share_sd = 0.08

    if remaining_minutes <= 4.0 and abs(score_diff) <= 12:
        poss_time_sd += 1.5
        scoring_share_sd += 0.03

    if remaining_minutes <= 2.0 and abs(score_diff) <= 8:
        poss_time_sd += 2.0
        scoring_share_sd += 0.04

    if remaining_minutes <= 1.0 and abs(score_diff) <= 6:
        poss_time_sd += 2.0
        scoring_share_sd += 0.05

    return poss_time_sd, scoring_share_sd


def detect_market_inefficiency(
    over_prob: float,
    under_prob: float,
    over_odds: int,
    under_odds: int,
    projected_total: float,
    market_total: float,
    threshold: float = 0.03,
) -> InefficiencyResult:
    over_implied = implied_prob_from_american(over_odds)
    under_implied = implied_prob_from_american(under_odds)

    over_gap = over_prob - over_implied
    under_gap = under_prob - under_implied

    if over_gap >= threshold and over_gap >= under_gap:
        return InefficiencyResult(over_gap, "OVER", True)

    if under_gap >= threshold and under_gap > over_gap:
        return InefficiencyResult(under_gap, "UNDER", True)

    return InefficiencyResult(0.0, "NONE", False)


def build_top_bets(
    home_team: str,
    away_team: str,
    market_total: float,
    home_spread_line: float,
    away_spread_line: float,
    over_prob: float,
    under_prob: float,
    home_win_prob: float,
    away_win_prob: float,
    home_cover_prob: float,
    away_cover_prob: float,
    over_odds: int,
    under_odds: int,
    home_ml_odds: int,
    away_ml_odds: int,
    home_spread_odds: int,
    away_spread_odds: int,
    home_score: int,
    away_score: int,
    minutes_left: int,
    min_edge: float = 0.03,
    max_stake: float = 0.05,
) -> list[dict[str, Any]]:
    bets: list[dict[str, Any]] = []
    score_diff = abs(home_score - away_score)
    vol_scalar = calculate_volatility_scalar(minutes_left, score_diff)

    market_candidates = [
        {"label": f"OVER {market_total}", "market": "total", "side": "over", "line": market_total, "prob": over_prob, "odds": over_odds},
        {"label": f"UNDER {market_total}", "market": "total", "side": "under", "line": market_total, "prob": under_prob, "odds": under_odds},
        {"label": f"{home_team} ML", "market": "moneyline", "side": "home", "team": home_team, "prob": home_win_prob, "odds": home_ml_odds},
        {"label": f"{away_team} ML", "market": "moneyline", "side": "away", "team": away_team, "prob": away_win_prob, "odds": away_ml_odds},
        {"label": f"{home_team} {home_spread_line}", "market": "spread", "side": "home", "team": home_team, "line": home_spread_line, "prob": home_cover_prob, "odds": home_spread_odds},
        {"label": f"{away_team} {away_spread_line}", "market": "spread", "side": "away", "team": away_team, "line": away_spread_line, "prob": away_cover_prob, "odds": away_spread_odds},
    ]

    for c in market_candidates:
        implied_prob = implied_prob_from_american(c["odds"])
        edge = calculate_edge(c["prob"], implied_prob)
        ev = calculate_ev(c["prob"], c["odds"])

        if edge < min_edge:
            continue

        base_kelly = quarter_kelly_fraction(c["prob"], c["odds"])
        stake = min(max_stake, base_kelly * vol_scalar)

        bets.append({
            "label": c["label"],
            "market": c["market"],
            "side": c["side"],
            "line": c.get("line"),
            "team": c.get("team"),
            "odds": c["odds"],
            "model_prob": round(c["prob"], 4),
            "implied_prob": round(implied_prob, 4),
            "edge_display": round(edge, 4),
            "ev": round(ev, 4),
            "stake": round(stake, 4),
        })

    bets.sort(key=lambda x: (x["edge_display"], x["stake"]), reverse=True)
    return bets[:5]


def log_bet_snapshot(result: dict[str, Any], logfile: str = BET_SNAPSHOT_LOG) -> None:
    with open(logfile, "a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")


def print_ranked_bets(top_bets: list[dict[str, Any]]) -> None:
    print("\n===== EDGE BOARD =====\n")
    if not top_bets:
        print("No bets passed threshold.\n")
        print("======================\n")
        return

    for idx, bet in enumerate(top_bets, start=1):
        print(
            f"{idx}. {bet['label']} | "
            f"Prob: {bet['model_prob']:.4f} | "
            f"Imp: {bet['implied_prob']:.4f} | "
            f"Edge: {bet['edge_display']:.4f} | "
            f"Stake: {bet['stake']:.4f}"
        )
    print("\n======================\n")


def build_enriched_payload(payload: dict[str, Any]) -> dict[str, Any]:
    home_team = str(payload["home_team"])
    away_team = str(payload["away_team"])

    home_stats = get_team_stat_profile(home_team)
    away_stats = get_team_stat_profile(away_team)

    payload = dict(payload)
    payload.update({
        "home_off_3pa_rate": home_stats["off_3pa_rate"],
        "home_off_3p_pct": home_stats["off_3p_pct"],
        "home_off_2p_pct": home_stats["off_2p_pct"],
        "home_off_ft_rate": home_stats["off_ft_rate"],
        "home_off_ft_pct": home_stats["off_ft_pct"],
        "home_off_tov_rate": home_stats["off_tov_rate"],
        "home_off_orb_rate": home_stats["off_orb_rate"],
        "home_def_3pa_rate_allowed": home_stats["def_3pa_rate_allowed"],
        "home_def_3p_pct_allowed": home_stats["def_3p_pct_allowed"],
        "home_def_2p_pct_allowed": home_stats["def_2p_pct_allowed"],
        "home_def_ft_rate_allowed": home_stats["def_ft_rate_allowed"],
        "home_def_tov_forced_rate": home_stats["def_tov_forced_rate"],
        "home_def_drb_rate": home_stats["def_drb_rate"],

        "away_off_3pa_rate": away_stats["off_3pa_rate"],
        "away_off_3p_pct": away_stats["off_3p_pct"],
        "away_off_2p_pct": away_stats["off_2p_pct"],
        "away_off_ft_rate": away_stats["off_ft_rate"],
        "away_off_ft_pct": away_stats["off_ft_pct"],
        "away_off_tov_rate": away_stats["off_tov_rate"],
        "away_off_orb_rate": away_stats["off_orb_rate"],
        "away_def_3pa_rate_allowed": away_stats["def_3pa_rate_allowed"],
        "away_def_3p_pct_allowed": away_stats["def_3p_pct_allowed"],
        "away_def_2p_pct_allowed": away_stats["def_2p_pct_allowed"],
        "away_def_ft_rate_allowed": away_stats["def_ft_rate_allowed"],
        "away_def_tov_forced_rate": away_stats["def_tov_forced_rate"],
        "away_def_drb_rate": away_stats["def_drb_rate"],
    })
    return payload


def normal_shot_time(catch_up_mode: bool, foul_game_mode: bool, poss_time_sd: float) -> int:
    poss_seconds_mean = 18
    poss_seconds_floor = 4

    if catch_up_mode:
        poss_seconds_mean = 12
        poss_seconds_floor = 3

    if foul_game_mode:
        poss_seconds_mean = 7
        poss_seconds_floor = 2

    return max(poss_seconds_floor, int(random.gauss(poss_seconds_mean, poss_time_sd)))


def turnover_time(catch_up_mode: bool, foul_game_mode: bool) -> int:
    if foul_game_mode:
        return min(8, max(2, int(random.gauss(4, 1.5))))
    if catch_up_mode:
        return min(10, max(3, int(random.gauss(6, 2))))
    return min(14, max(3, int(random.gauss(8, 3))))


def second_chance_time() -> int:
    return min(12, max(2, int(random.gauss(7, 2))))


def evaluate_live_game(raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = build_enriched_payload(raw_payload)

    home_team = str(payload["home_team"])
    away_team = str(payload["away_team"])
    home_score = int(payload["home_score"])
    away_score = int(payload["away_score"])
    minutes_left = int(payload["minutes_left"])
    seconds_left = int(payload["seconds_left"])
    mode = str(payload["mode"]).lower()

    seed_input = f"{home_team}-{away_team}-{home_score}-{away_score}-{minutes_left}-{seconds_left}"
    random.seed(hash(seed_input) % (2**32))

    market_total = float(payload["market_total"])
    over_odds = int(payload.get("over_odds", -110))
    under_odds = int(payload.get("under_odds", -110))
    home_ml_odds = int(payload.get("home_ml_odds", -110))
    away_ml_odds = int(payload.get("away_ml_odds", -110))
    home_spread_line = float(payload.get("home_spread_line", 0.0))
    away_spread_line = float(payload.get("away_spread_line", 0.0))
    home_spread_odds = int(payload.get("home_spread_odds", -110))
    away_spread_odds = int(payload.get("away_spread_odds", -110))

    baseline_ppp = float(payload.get("baseline_points_per_possession", 1.02))
    recent_ppp = float(payload.get("recent_points_per_possession", 0.88))
    recent_ppm = float(payload.get("recent_possessions_per_min", 3.1))
    baseline_ppm = float(payload.get("baseline_possessions_per_min", 3.0))

    home_off_3pa_rate = float(payload.get("home_off_3pa_rate", 0.38))
    home_off_3p_pct = float(payload.get("home_off_3p_pct", 0.34))
    home_off_2p_pct = float(payload.get("home_off_2p_pct", 0.51))
    home_off_ft_rate = float(payload.get("home_off_ft_rate", 0.20))
    home_off_ft_pct = float(payload.get("home_off_ft_pct", 0.71))
    home_off_tov_rate = float(payload.get("home_off_tov_rate", 0.17))
    home_off_orb_rate = float(payload.get("home_off_orb_rate", 0.30))

    away_off_3pa_rate = float(payload.get("away_off_3pa_rate", 0.38))
    away_off_3p_pct = float(payload.get("away_off_3p_pct", 0.34))
    away_off_2p_pct = float(payload.get("away_off_2p_pct", 0.51))
    away_off_ft_rate = float(payload.get("away_off_ft_rate", 0.20))
    away_off_ft_pct = float(payload.get("away_off_ft_pct", 0.71))
    away_off_tov_rate = float(payload.get("away_off_tov_rate", 0.17))
    away_off_orb_rate = float(payload.get("away_off_orb_rate", 0.30))

    home_def_3pa_rate_allowed = float(payload.get("home_def_3pa_rate_allowed", 0.38))
    home_def_3p_pct_allowed = float(payload.get("home_def_3p_pct_allowed", 0.34))
    home_def_2p_pct_allowed = float(payload.get("home_def_2p_pct_allowed", 0.51))
    home_def_ft_rate_allowed = float(payload.get("home_def_ft_rate_allowed", 0.20))
    home_def_tov_forced_rate = float(payload.get("home_def_tov_forced_rate", 0.17))
    home_def_drb_rate = float(payload.get("home_def_drb_rate", 0.70))

    away_def_3pa_rate_allowed = float(payload.get("away_def_3pa_rate_allowed", 0.38))
    away_def_3p_pct_allowed = float(payload.get("away_def_3p_pct_allowed", 0.34))
    away_def_2p_pct_allowed = float(payload.get("away_def_2p_pct_allowed", 0.51))
    away_def_ft_rate_allowed = float(payload.get("away_def_ft_rate_allowed", 0.20))
    away_def_tov_forced_rate = float(payload.get("away_def_tov_forced_rate", 0.17))
    away_def_drb_rate = float(payload.get("away_def_drb_rate", 0.70))

    score_diff = home_score - away_score

    if recent_ppm > 5.0 or baseline_ppm > 5.0 or recent_ppm < 1.0 or baseline_ppm < 1.0:
        recent_ppm, baseline_ppm = estimate_auto_pace(
            mode=mode,
            home_score=home_score,
            away_score=away_score,
            minutes_left=minutes_left,
            seconds_left=seconds_left,
            baseline_ppm=3.0,
        )

    blended_ppm, pace_note = apply_score_differential_pace_model(
        mode=mode,
        score_diff=score_diff,
        recent_ppm=recent_ppm,
        baseline_ppm=baseline_ppm,
        minutes_left=minutes_left,
    )

    blended_ppp = _clamp((0.50 * recent_ppp) + (0.50 * baseline_ppp), 0.82, 1.24)

    blended_home_3pa_rate = blend_stat(home_off_3pa_rate, away_def_3pa_rate_allowed)
    blended_home_3p_pct = blend_stat(home_off_3p_pct, away_def_3p_pct_allowed)
    blended_home_2p_pct = blend_stat(home_off_2p_pct, away_def_2p_pct_allowed)
    blended_home_ft_rate = blend_stat(home_off_ft_rate, away_def_ft_rate_allowed)
    blended_home_tov_rate = blend_stat(home_off_tov_rate, away_def_tov_forced_rate)
    blended_home_ft_pct = home_off_ft_pct
    blended_home_orb_rate = blend_stat(home_off_orb_rate, 1.0 - away_def_drb_rate)

    blended_away_3pa_rate = blend_stat(away_off_3pa_rate, home_def_3pa_rate_allowed)
    blended_away_3p_pct = blend_stat(away_off_3p_pct, home_def_3p_pct_allowed)
    blended_away_2p_pct = blend_stat(away_off_2p_pct, home_def_2p_pct_allowed)
    blended_away_ft_rate = blend_stat(away_off_ft_rate, home_def_ft_rate_allowed)
    blended_away_tov_rate = blend_stat(away_off_tov_rate, home_def_tov_forced_rate)
    blended_away_ft_pct = away_off_ft_pct
    blended_away_orb_rate = blend_stat(away_off_orb_rate, 1.0 - home_def_drb_rate)

    home_weights = build_offense_weights_from_stats(
        blended_home_3pa_rate,
        blended_home_3p_pct,
        blended_home_2p_pct,
        blended_home_ft_rate,
        blended_home_ft_pct,
        blended_home_tov_rate,
        is_second_chance=False,
    )
    home_second_weights = build_offense_weights_from_stats(
        blended_home_3pa_rate,
        blended_home_3p_pct,
        blended_home_2p_pct,
        blended_home_ft_rate,
        blended_home_ft_pct,
        blended_home_tov_rate,
        is_second_chance=True,
    )

    away_weights = build_offense_weights_from_stats(
        blended_away_3pa_rate,
        blended_away_3p_pct,
        blended_away_2p_pct,
        blended_away_ft_rate,
        blended_away_ft_pct,
        blended_away_tov_rate,
        is_second_chance=False,
    )
    away_second_weights = build_offense_weights_from_stats(
        blended_away_3pa_rate,
        blended_away_3p_pct,
        blended_away_2p_pct,
        blended_away_ft_rate,
        blended_away_ft_pct,
        blended_away_tov_rate,
        is_second_chance=True,
    )

    num_sims = 10000
    sum_home_final = 0.0
    sum_away_final = 0.0
    sum_total_final = 0.0
    home_wins = 0
    home_covers = 0
    overs = 0

    for _ in range(num_sims):
        sim_home_score = float(home_score)
        sim_away_score = float(away_score)
        sim_remaining_seconds = (minutes_left * 60) + seconds_left
        home_has_ball = True

        max_possessions = max(1, int((blended_ppm * (sim_remaining_seconds / 60.0)) * 2.8))
        possessions_used = 0
        smooth_weight = 0.70

        while sim_remaining_seconds > 0 and possessions_used < max_possessions:
            if home_has_ball:
                offense_team = "home"
                current_offense_weights = home_weights
                current_second_chance_weights = home_second_weights
                current_orb_rate = _clamp(blended_home_orb_rate, 0.15, 0.45)
            else:
                offense_team = "away"
                current_offense_weights = away_weights
                current_second_chance_weights = away_second_weights
                current_orb_rate = _clamp(blended_away_orb_rate, 0.15, 0.45)

            possession_alive = True
            is_second_chance = False
            extra_chances_used = 0
            max_extra_chances = 2

            while possession_alive and sim_remaining_seconds > 0:
                sim_score_diff = sim_home_score - sim_away_score
                remaining_minutes = sim_remaining_seconds / 60.0
                offense_trailing = (
                    (offense_team == "home" and sim_home_score < sim_away_score)
                    or (offense_team == "away" and sim_away_score < sim_home_score)
                )
                deficit = abs(sim_home_score - sim_away_score)

                catch_up_mode = offense_trailing and remaining_minutes <= 4.0 and 4 <= deficit <= 12
                foul_game_mode = offense_trailing and remaining_minutes <= 1.5 and 3 <= deficit <= 8

                poss_time_sd, _ = get_late_game_variance_scalars(
                    remaining_minutes=remaining_minutes,
                    score_diff=sim_score_diff,
                )

                active_weights = current_second_chance_weights if is_second_chance else current_offense_weights

                if catch_up_mode and offense_trailing:
                    tmp = dict(active_weights)
                    tmp["w3"] += 0.05
                    tmp["w2"] = max(0.0, tmp["w2"] - 0.035)
                    tmp["w_miss"] = max(0.0, tmp["w_miss"] - 0.015)
                    total = sum(tmp.values())
                    active_weights = {k: v / total for k, v in tmp.items()}

                expected_value = possession_expected_value(active_weights)

                outcome = random.choices(
                    ["tov", "miss", "1", "2", "3"],
                    weights=[
                        active_weights["w_tov"],
                        active_weights["w_miss"],
                        active_weights["w1"],
                        active_weights["w2"],
                        active_weights["w3"],
                    ],
                    k=1,
                )[0]

                if outcome == "tov":
                    sim_remaining_seconds = max(
                        0,
                        sim_remaining_seconds - turnover_time(catch_up_mode, foul_game_mode)
                    )
                    random_points = 0.0
                    smoothed_points = (smooth_weight * random_points) + ((1.0 - smooth_weight) * expected_value)
                    if offense_team == "home":
                        sim_home_score += smoothed_points
                    else:
                        sim_away_score += smoothed_points
                    possession_alive = False

                elif outcome in {"1", "2", "3"}:
                    shot_time = second_chance_time() if is_second_chance else normal_shot_time(
                        catch_up_mode, foul_game_mode, poss_time_sd
                    )
                    sim_remaining_seconds = max(0, sim_remaining_seconds - shot_time)

                    random_points = float(int(outcome))
                    smoothed_points = (smooth_weight * random_points) + ((1.0 - smooth_weight) * expected_value)

                    if offense_team == "home":
                        sim_home_score += smoothed_points
                    else:
                        sim_away_score += smoothed_points

                    possession_alive = False

                else:  # miss
                    shot_time = second_chance_time() if is_second_chance else normal_shot_time(
                        catch_up_mode, foul_game_mode, poss_time_sd
                    )
                    sim_remaining_seconds = max(0, sim_remaining_seconds - shot_time)

                    random_points = 0.0
                    smoothed_points = (smooth_weight * random_points) + ((1.0 - smooth_weight) * expected_value)
                    if offense_team == "home":
                        sim_home_score += smoothed_points
                    else:
                        sim_away_score += smoothed_points

                    got_oreb = random.random() < current_orb_rate
                    if got_oreb and extra_chances_used < max_extra_chances and sim_remaining_seconds > 0:
                        extra_chances_used += 1
                        is_second_chance = True
                    else:
                        possession_alive = False

            home_has_ball = not home_has_ball
            possessions_used += 1

        sim_total = sim_home_score + sim_away_score
        sum_home_final += sim_home_score
        sum_away_final += sim_away_score
        sum_total_final += sim_total

        if sim_home_score > sim_away_score:
            home_wins += 1
        if (sim_home_score - sim_away_score) + home_spread_line > 0:
            home_covers += 1
        if sim_total > market_total:
            overs += 1

    projected_home = sum_home_final / num_sims
    projected_away = sum_away_final / num_sims
    projected_total = sum_total_final / num_sims

    home_win_prob = home_wins / num_sims
    away_win_prob = 1.0 - home_win_prob
    home_cover_prob = home_covers / num_sims
    away_cover_prob = 1.0 - home_cover_prob
    over_prob = overs / num_sims
    under_prob = 1.0 - over_prob

    inefficiency = detect_market_inefficiency(
        over_prob=over_prob,
        under_prob=under_prob,
        over_odds=over_odds,
        under_odds=under_odds,
        projected_total=projected_total,
        market_total=market_total,
    )

    top_bets = build_top_bets(
        home_team=home_team,
        away_team=away_team,
        market_total=market_total,
        home_spread_line=home_spread_line,
        away_spread_line=away_spread_line,
        over_prob=over_prob,
        under_prob=under_prob,
        home_win_prob=home_win_prob,
        away_win_prob=away_win_prob,
        home_cover_prob=home_cover_prob,
        away_cover_prob=away_cover_prob,
        over_odds=over_odds,
        under_odds=under_odds,
        home_ml_odds=home_ml_odds,
        away_ml_odds=away_ml_odds,
        home_spread_odds=home_spread_odds,
        away_spread_odds=away_spread_odds,
        home_score=home_score,
        away_score=away_score,
        minutes_left=minutes_left,
        min_edge=0.03,
        max_stake=0.05,
    )

    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "minutes_left": minutes_left,
        "seconds_left": seconds_left,
        "mode": mode,
        "market_total": market_total,
        "projected_home": round(projected_home, 2),
        "projected_away": round(projected_away, 2),
        "projected_total": round(projected_total, 2),
        "home_win_prob": round(home_win_prob, 4),
        "away_win_prob": round(away_win_prob, 4),
        "home_cover_prob": round(home_cover_prob, 4),
        "away_cover_prob": round(away_cover_prob, 4),
        "over_prob": round(over_prob, 4),
        "under_prob": round(under_prob, 4),
        "blended_ppm": round(blended_ppm, 3),
        "blended_ppp": round(blended_ppp, 3),
        "pace_note": pace_note,
        "smooth_weight": 0.70,
        "home_weights": {k: round(v, 4) for k, v in home_weights.items()},
        "home_second_weights": {k: round(v, 4) for k, v in home_second_weights.items()},
        "away_weights": {k: round(v, 4) for k, v in away_weights.items()},
        "away_second_weights": {k: round(v, 4) for k, v in away_second_weights.items()},
        "blended_home_3pa_rate": round(blended_home_3pa_rate, 4),
        "blended_home_3p_pct": round(blended_home_3p_pct, 4),
        "blended_home_2p_pct": round(blended_home_2p_pct, 4),
        "blended_home_ft_rate": round(blended_home_ft_rate, 4),
        "blended_home_tov_rate": round(blended_home_tov_rate, 4),
        "blended_home_orb_rate": round(blended_home_orb_rate, 4),
        "blended_away_3pa_rate": round(blended_away_3pa_rate, 4),
        "blended_away_3p_pct": round(blended_away_3p_pct, 4),
        "blended_away_2p_pct": round(blended_away_2p_pct, 4),
        "blended_away_ft_rate": round(blended_away_ft_rate, 4),
        "blended_away_tov_rate": round(blended_away_tov_rate, 4),
        "blended_away_orb_rate": round(blended_away_orb_rate, 4),
        "inefficiency": {
            "gap": round(inefficiency.gap, 4),
            "direction": inefficiency.direction,
            "is_significant": inefficiency.is_significant,
            "alert": inefficiency.to_alert_string(),
        },
        "top_bets": top_bets,
    }


if __name__ == "__main__":
    sample_payload = {
        "home_team": "Duke",
        "away_team": "Auburn",
        "home_score": 50,
        "away_score": 45,
        "minutes_left": 4,
        "seconds_left": 0,
        "mode": "2h",
        "market_total": 112.5,
        "over_odds": -110,
        "under_odds": -110,
        "home_ml_odds": -145,
        "away_ml_odds": 125,
        "home_spread_line": -3.5,
        "away_spread_line": 3.5,
        "home_spread_odds": -110,
        "away_spread_odds": -110,
        "baseline_points_per_possession": 1.02,
        "recent_points_per_possession": 0.96,
        "recent_possessions_per_min": 3.1,
        "baseline_possessions_per_min": 3.0,
    }

    result = evaluate_live_game(sample_payload)
    print(json.dumps(result, indent=2))
    print_ranked_bets(result["top_bets"])
    log_bet_snapshot(result)
