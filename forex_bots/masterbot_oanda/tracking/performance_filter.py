#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TRACK_FILE = Path("storage/tracker/alerts.jsonl")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def load_rows() -> list[dict]:
    if not TRACK_FILE.exists():
        return []
    rows = []
    for line in TRACK_FILE.read_text(encoding="utf-8").splitlines():
        try:
            if line.strip():
                rows.append(json.loads(line))
        except:
            pass
    return rows


def _bucket_stats(rows, field):
    out = {}
    for r in rows:
        result = r.get("result")
        if result not in {"win", "loss", "push"}:
            continue

        key = str(r.get(field, "unknown"))

        if key not in out:
            out[key] = {"bets": 0, "wins": 0, "losses": 0, "units": 0.0}

        out[key]["bets"] += 1
        out[key]["units"] += _safe_float(r.get("profit_units", 0))

        if result == "win":
            out[key]["wins"] += 1
        elif result == "loss":
            out[key]["losses"] += 1

    for k, v in out.items():
        wl = v["wins"] + v["losses"]
        v["hit_rate"] = (v["wins"] / wl) if wl > 0 else 0.0

    return out


def build_snapshot():
    rows = load_rows()
    settled = [r for r in rows if r.get("result") in {"win", "loss", "push"}]

    return {
        "by_market": _bucket_stats(settled, "market"),
        "by_tier": _bucket_stats(settled, "tier"),
        "by_sport": _bucket_stats(settled, "sport_key"),
    }


def _score(stats):
    if not stats:
        return 0.0

    bets = stats["bets"]
    hit = stats["hit_rate"]
    units = stats["units"]

    # 🔒 REQUIRE SAMPLE SIZE
    if bets < 3:
        return 0.0
    if bets < 5:
        scale = 0.5
    else:
        scale = 1.0

    score = 0.0

    # 📊 HIT RATE (reduced weight)
    if hit >= 0.65:
        score += min(2.5, (hit - 0.5) * 10)
    elif hit <= 0.45:
        score -= min(4.0, (0.5 - hit) * 14)  # stronger penalty

    # 💰 UNITS (reduced boost, stronger penalty)
    if units > 0:
        score += min(1.5, units * 0.4)
    elif units < 0:
        score -= min(3.0, abs(units) * 0.6)

    return round(score * scale, 2)


def adjust_alert(alert, snap):
    market = alert.get("market")
    tier = alert.get("tier")
    sport = alert.get("sport_key")

    m = _score(snap["by_market"].get(market))
    t = _score(snap["by_tier"].get(tier))
    s = _score(snap["by_sport"].get(sport))

    # ⚖️ REDUCE SPORT INFLUENCE
    total_adj = m + t + (s * 0.6)

    # 🔒 HARD CAP
    total_adj = max(-6.0, min(3.0, total_adj))

    base_conf = _safe_float(alert.get("confidence", 0))
    new_conf = max(0.0, min(99.0, base_conf + total_adj))

    out = dict(alert)
    out["confidence"] = round(new_conf, 2)
    out["performance_adjustment"] = round(total_adj, 2)

    # 🧠 RETIER (more conservative)
    if new_conf >= 88:
        out["tier"] = "A"
    elif new_conf >= 80:
        out["tier"] = "B"
    elif new_conf >= 72:
        out["tier"] = "C"
    else:
        out["tier"] = "D"

    return out


def filter_alerts(alerts, min_conf=60):
    snap = build_snapshot()
    out = []
    for a in alerts:
        val_edge = abs(float(a.get('total_edge_delta') or a.get('edge') or 0))
        if val_edge >= 8.0:
            print(f'!!! MONSTER BYPASS: Edge {val_edge} for {a.get("selection")} !!!')
            out.append(a)
            continue

        adj = adjust_alert(a, snap)
        if adj.get('performance_adjustment', 0) <= -4:
            continue
        if adj.get('confidence', 0) < min_conf:
            continue
        out.append(adj)

    out.sort(key=lambda x: (float(x.get('confidence', 0) or 0), float(x.get('edge', 0) or 0)), reverse=True)
    return out

if __name__ == "__main__":
    print(json.dumps(build_snapshot(), indent=2))
