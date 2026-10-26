#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

try:
    from kelly_engine import adaptive_kelly, calculate_bet_size
except Exception:
    def adaptive_kelly(signal: dict, bankroll: float = 10000.0) -> float:
        try:
            return round(min(0.10, float(signal.get("kelly_quarter", 0.02))), 4)
        except Exception:
            return 0.02

    def calculate_bet_size(signal: dict, bankroll: float) -> float:
        return round(bankroll * adaptive_kelly(signal, bankroll), 2)


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return default
            if s.endswith("%"):
                return float(s[:-1]) / 100.0
        return float(v)
    except Exception:
        return default


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(x, high))


def _american_implied_prob(odds: Any) -> float:
    o = _to_float(odds, 0.0)
    if o == 0:
        return 0.0
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


def _american_to_decimal(odds: Any) -> float:
    o = _to_float(odds, -110.0)
    if o < 0:
        return 1.0 + (100.0 / abs(o))
    return 1.0 + (o / 100.0)


def _raw_kelly(probability: float, decimal_odds: float) -> float:
    b = max(1e-9, decimal_odds - 1.0)
    p = _clamp(probability, 0.0, 1.0)
    q = 1.0 - p
    raw = ((b * p) - q) / b
    return max(0.0, raw)


def _ev(probability: float, decimal_odds: float) -> float:
    if decimal_odds <= 1.01:
        decimal_odds = 1.9091
    b = max(0.0, decimal_odds - 1.0)
    p = _clamp(probability, 0.0, 1.0)
    q = 1.0 - p
    return (p * b) - q


def _strength_from_conf(conf: float) -> str:
    if conf >= 0.80:
        return "A"
    if conf >= 0.68:
        return "B"
    if conf >= 0.58:
        return "C"
    return "D"


def _score_signal(edge_val: float, prob: float, ev: float) -> float:
    return (
        edge_val * 100.0 * 0.45 +
        prob * 100.0 * 0.35 +
        max(0.0, ev) * 100.0 * 0.20
    )


def _game_context(edge: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    home_team = str(payload.get("home_team", "Home"))
    away_team = str(payload.get("away_team", "Away"))
    home_score = int(_to_float(payload.get("home_score", 0), 0))
    away_score = int(_to_float(payload.get("away_score", 0), 0))
    minutes_left = int(_to_float(payload.get("minutes_left", 0), 0))
    seconds_left = int(_to_float(payload.get("seconds_left", 0), 0))
    mode = str(payload.get("mode", "2h")).lower()

    home_ml_prob = _clamp(_to_float(edge.get("home_ml_prob", 0.0)), 0.0, 0.9999)
    away_ml_prob = _clamp(_to_float(edge.get("away_ml_prob", 0.0)), 0.0, 0.9999)

    if home_score > away_score:
        leader = home_team
        leader_prob = home_ml_prob
        score_diff = home_score - away_score
    elif away_score > home_score:
        leader = away_team
        leader_prob = away_ml_prob
        score_diff = away_score - home_score
    else:
        leader = "tied"
        leader_prob = max(home_ml_prob, away_ml_prob)
        score_diff = 0

    return {
        "game": f"{away_team} @ {home_team}",
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "minutes_left": minutes_left,
        "seconds_left": seconds_left,
        "mode": mode,
        "leader": leader,
        "leader_win_prob": round(leader_prob, 4),
        "score_diff": score_diff,
    }


def _line_better(market: str, side: str, new_line: Any, old_line: Any) -> bool:
    if market == "moneyline":
        return False
    try:
        n = float(new_line)
        o = float(old_line)
    except Exception:
        return False

    s = str(side).lower()

    if market == "spread":
        if n >= 0 and o >= 0:
            return n > o
        if n <= 0 and o <= 0:
            return n > o
        return n > o

    if market == "total":
        if "over" in s:
            return n < o
        if "under" in s:
            return n > o

    return False


def _candidate_signals(edge: Dict[str, Any], payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    ctx = _game_context(edge, payload)
    home_team = ctx["home_team"]
    away_team = ctx["away_team"]
    game = ctx["game"]

    candidates: List[Dict[str, Any]] = []
    suspicious = bool(edge.get("suspicious_any", False))
    pace = str(edge.get("calibration", {}).get("pace", payload.get("pace", "normal")))
    pressure = str(payload.get("pressure", "normal"))
    foul_window = bool(payload.get("foul_window", False))
    comeback_window = bool(payload.get("comeback_window", False))

    # spread
    home_cover_prob = _clamp(_to_float(edge.get("home_cover_prob", 0.0)), 0.0, 0.9999)
    away_cover_prob = _clamp(_to_float(edge.get("away_cover_prob", 0.0)), 0.0, 0.9999)
    home_spread_edge = max(0.0, _to_float(edge.get("home_spread_edge", 0.0)))
    away_spread_edge = max(0.0, _to_float(edge.get("away_spread_edge", 0.0)))

    if home_spread_edge > 0:
        odds = _to_float(payload.get("home_spread_odds", -110), -110)
        candidates.append({
            **ctx,
            "market": "spread",
            "selection": f"{home_team} {payload.get('home_spread_line')}",
            "side": home_team,
            "line": payload.get("home_spread_line"),
            "odds": odds,
            "cover_prob": home_cover_prob,
            "confidence": home_cover_prob,
            "edge": home_spread_edge,
            "suspicious": suspicious,
            "pace": pace,
            "pressure": pressure,
            "foul_window": foul_window,
            "comeback_window": comeback_window,
        })

    if away_spread_edge > 0:
        odds = _to_float(payload.get("away_spread_odds", -110), -110)
        candidates.append({
            **ctx,
            "market": "spread",
            "selection": f"{away_team} {payload.get('away_spread_line')}",
            "side": away_team,
            "line": payload.get("away_spread_line"),
            "odds": odds,
            "cover_prob": away_cover_prob,
            "confidence": away_cover_prob,
            "edge": away_spread_edge,
            "suspicious": suspicious,
            "pace": pace,
            "pressure": pressure,
            "foul_window": foul_window,
            "comeback_window": comeback_window,
        })

    # ml
    home_ml_prob = _clamp(_to_float(edge.get("home_ml_prob", 0.0)), 0.0, 0.9999)
    away_ml_prob = _clamp(_to_float(edge.get("away_ml_prob", 0.0)), 0.0, 0.9999)
    home_ml_edge = max(0.0, _to_float(edge.get("home_ml_edge", 0.0)))
    away_ml_edge = max(0.0, _to_float(edge.get("away_ml_edge", 0.0)))

    if home_ml_edge > 0:
        odds = _to_float(payload.get("home_ml_odds", -110), -110)
        candidates.append({
            **ctx,
            "market": "moneyline",
            "selection": home_team,
            "side": home_team,
            "line": None,
            "odds": odds,
            "win_prob": home_ml_prob,
            "confidence": home_ml_prob,
            "edge": home_ml_edge,
            "suspicious": suspicious,
            "pace": pace,
            "pressure": pressure,
            "foul_window": foul_window,
            "comeback_window": comeback_window,
        })

    if away_ml_edge > 0:
        odds = _to_float(payload.get("away_ml_odds", -110), -110)
        candidates.append({
            **ctx,
            "market": "moneyline",
            "selection": away_team,
            "side": away_team,
            "line": None,
            "odds": odds,
            "win_prob": away_ml_prob,
            "confidence": away_ml_prob,
            "edge": away_ml_edge,
            "suspicious": suspicious,
            "pace": pace,
            "pressure": pressure,
            "foul_window": foul_window,
            "comeback_window": comeback_window,
        })

    # total
    over_prob = _clamp(_to_float(edge.get("over_prob", 0.0)), 0.0, 0.9999)
    under_prob = _clamp(_to_float(edge.get("under_prob", 0.0)), 0.0, 0.9999)
    over_edge = max(0.0, _to_float(edge.get("over_edge", 0.0)))
    under_edge = max(0.0, _to_float(edge.get("under_edge", 0.0)))

    if over_edge > 0:
        odds = _to_float(payload.get("over_odds", -110), -110)
        candidates.append({
            **ctx,
            "market": "total",
            "selection": f"over {payload.get('market_total')}",
            "side": "over",
            "line": payload.get("market_total"),
            "odds": odds,
            "total_prob": over_prob,
            "confidence": over_prob,
            "edge": over_edge,
            "suspicious": suspicious,
            "pace": pace,
            "pressure": pressure,
            "foul_window": foul_window,
            "comeback_window": comeback_window,
        })

    if under_edge > 0:
        odds = _to_float(payload.get("under_odds", -110), -110)
        candidates.append({
            **ctx,
            "market": "total",
            "selection": f"under {payload.get('market_total')}",
            "side": "under",
            "line": payload.get("market_total"),
            "odds": odds,
            "total_prob": under_prob,
            "confidence": under_prob,
            "edge": under_edge,
            "suspicious": suspicious,
            "pace": pace,
            "pressure": pressure,
            "foul_window": foul_window,
            "comeback_window": comeback_window,
        })

    return candidates


def _dedupe_best(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[tuple, Dict[str, Any]] = {}

    for c in candidates:
        market = str(c.get("market", "")).lower()
        side = str(c.get("side", "")).lower()
        key = (c.get("game"), market, side)

        bot_prob = _clamp(_to_float(c.get("confidence", 0.0)), 0.0, 0.9999)
        decimal_odds = _american_to_decimal(c.get("odds", -110))
        ev = _ev(bot_prob, decimal_odds)
        score = _score_signal(_to_float(c.get("edge", 0.0)), bot_prob, ev)

        c["_score"] = score
        c["_ev"] = ev

        prev = best.get(key)
        if prev is None:
            best[key] = c
            continue

        if _line_better(market, side, c.get("line"), prev.get("line")):
            best[key] = c
            continue

        if c["_score"] > prev.get("_score", -999):
            best[key] = c

    return list(best.values())


def _enrich_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    market = str(signal.get("market", "")).lower()

    if market == "spread":
        bot_prob = _clamp(_to_float(signal.get("cover_prob", signal.get("confidence", 0.0))), 0.0, 0.9999)
    elif market == "moneyline":
        bot_prob = _clamp(_to_float(signal.get("win_prob", signal.get("confidence", 0.0))), 0.0, 0.9999)
    elif market == "total":
        bot_prob = _clamp(_to_float(signal.get("total_prob", signal.get("confidence", 0.0))), 0.0, 0.9999)
    else:
        bot_prob = _clamp(_to_float(signal.get("confidence", 0.0)), 0.0, 0.9999)

    odds = _to_float(signal.get("odds", -110), -110)
    book_prob = _american_implied_prob(odds)
    win_edge = bot_prob - book_prob
    decimal_odds = _american_to_decimal(odds)
    ev = _ev(bot_prob, decimal_odds)

    raw_kelly = _raw_kelly(bot_prob, decimal_odds)
    signal["kelly_quarter"] = round(min(0.10, raw_kelly * 0.25), 4)
    signal["bot_win_pct"] = round(bot_prob * 100.0, 2)
    signal["book_win_pct"] = round(book_prob * 100.0, 2)
    signal["win_pct_edge"] = round(win_edge * 100.0, 2)
    signal["ev_percent"] = round(ev * 100.0, 2)
    signal["strength"] = _strength_from_conf(bot_prob)
    signal["score"] = round(_score_signal(_to_float(signal.get("edge", 0.0)), bot_prob, ev), 3)

    signal["kelly_adaptive"] = adaptive_kelly(signal)
    signal["bet_size"] = calculate_bet_size(signal, bankroll=10000)

    signal["summary"] = (
        f"{signal['game']} | {signal['market'].upper()} | {signal['selection']} | "
        f"bot={signal['bot_win_pct']}% | book={signal['book_win_pct']}% | "
        f"edge={signal['win_pct_edge']}% | ev={signal['ev_percent']}%"
    )
    signal["timestamp"] = datetime.utcnow().isoformat()
    return signal


def process_bet_intelligence(edge: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    candidates = _candidate_signals(edge, payload)
    deduped = _dedupe_best(candidates)

    ranked_signals: List[Dict[str, Any]] = []
    for sig in deduped:
        ranked_signals.append(_enrich_signal(sig))

    ranked_signals.sort(
        key=lambda x: (_to_float(x.get("score", 0.0)), _to_float(x.get("ev_percent", 0.0)), _to_float(x.get("confidence", 0.0))),
        reverse=True
    )

    return {
        "ranked_signals": ranked_signals,
        "alerts_to_send": list(ranked_signals),
        "policy_summary": {
            "candidate_signals_raw": len(candidates),
            "candidate_signals_deduped": len(deduped),
            "duplicates_pruned": max(0, len(candidates) - len(deduped)),
            "suspicious_any": bool(edge.get("suspicious_any", False)),
            "totals_suspicious": bool(edge.get("calibration", {}).get("suspicious_total", False)),
        },
    }


if __name__ == "__main__":
    sample_edge = {
        "home_cover_prob": 0.72,
        "away_cover_prob": 0.28,
        "over_prob": 0.41,
        "under_prob": 0.59,
        "home_ml_prob": 0.76,
        "away_ml_prob": 0.24,
        "home_spread_edge": 0.08,
        "away_spread_edge": 0.0,
        "over_edge": 0.0,
        "under_edge": 0.03,
        "home_ml_edge": 0.06,
        "away_ml_edge": 0.0,
        "suspicious_any": False,
        "calibration": {"suspicious_total": False},
    }

    sample_payload = {
        "sport_key": "basketball_ncaab",
        "home_team": "Michigan State Spartans",
        "away_team": "Louisville Cardinals",
        "home_score": 45,
        "away_score": 42,
        "minutes_left": 3,
        "seconds_left": 10,
        "mode": "1h",
        "market_total": 145.5,
        "home_ml_odds": -160,
        "away_ml_odds": 140,
        "home_spread_line": -4.5,
        "away_spread_line": 4.5,
        "home_spread_odds": -110,
        "away_spread_odds": -110,
        "over_odds": -110,
        "under_odds": -110,
    }

    out = process_bet_intelligence(sample_edge, sample_payload)
    print("BET INTELLIGENCE FINAL TEST")
    print(out["policy_summary"])
    for s in out["ranked_signals"]:
        print(s["summary"], "| adaptive_kelly=", s["kelly_adaptive"], "| bet_size=", s["bet_size"])
