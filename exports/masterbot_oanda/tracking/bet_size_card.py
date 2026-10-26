#!/usr/bin/env python3
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime

TRACK_FILE = Path("storage/tracker/alerts.jsonl")

MAX_CARD_EXPOSURE_UNITS = 7.5

SPORT_KELLY_HAIRCUT = {
    "basketball_nba": 1.00,
    "basketball_wnba": 0.92,
    "basketball_ncaab": 0.88,
    "basketball_wncaab": 0.85,
}


def load_alerts():
    if not TRACK_FILE.exists():
        return []

    alerts = []
    with TRACK_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                alerts.append(json.loads(line))
            except Exception:
                continue
    return alerts


def filter_active(alerts):
    active = []
    for a in alerts:
        result = str(a.get("result", "")).lower()
        if result in ("", "pending", "open", "live"):
            active.append(a)
    return active


def dedupe_alerts(alerts):
    best = {}

    for idx, a in enumerate(alerts):
        key = (
            str(a.get("phase", "")),
            str(a.get("game", "")),
            str(a.get("market", "")),
            str(a.get("selection", "")),
        )

        prev = best.get(key)
        if prev is None:
            best[key] = (idx, a)
            continue

        prev_idx, prev_a = prev
        prev_ts = str(prev_a.get("ts_utc", ""))
        curr_ts = str(a.get("ts_utc", ""))

        if curr_ts and prev_ts:
            if curr_ts >= prev_ts:
                best[key] = (idx, a)
        else:
            if idx >= prev_idx:
                best[key] = (idx, a)

    return [pair[1] for pair in sorted(best.values(), key=lambda x: x[0])]


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def latest_cycle_only(alerts, window_seconds=180):
    stamped = []
    for a in alerts:
        ts = parse_ts(str(a.get("ts_utc", "")))
        if ts is not None:
            stamped.append((ts, a))

    if not stamped:
        return alerts

    latest_ts = max(ts for ts, _ in stamped)
    out = []

    for ts, a in stamped:
        delta = (latest_ts - ts).total_seconds()
        if delta <= window_seconds:
            out.append(a)

    return out


def best_one_per_game(alerts):
    by_game = defaultdict(list)
    for a in alerts:
        game = str(a.get("game", "UNKNOWN"))
        by_game[game].append(a)

    out = []
    for _, items in by_game.items():
        items.sort(
            key=lambda x: (
                float(x.get("confidence", 0) or 0),
                float(x.get("edge", 0) or 0),
            ),
            reverse=True,
        )
        out.append(items[0])

    out.sort(
        key=lambda x: (
            float(x.get("confidence", 0) or 0),
            float(x.get("edge", 0) or 0),
        ),
        reverse=True,
    )
    return out


def implied_win_prob_from_conf(conf):
    """
    Cooler confidence -> win probability mapping.
    Intentionally conservative so Kelly does not overbet.
    """
    conf = float(conf or 0.0)

    if conf >= 92:
        return 0.575
    if conf >= 89:
        return 0.568
    if conf >= 86:
        return 0.560
    if conf >= 83:
        return 0.553
    if conf >= 80:
        return 0.546
    if conf >= 76:
        return 0.538
    if conf >= 72:
        return 0.530
    return 0.515


def get_alert_odds(alert, default=-110.0):
    for key in ("odds", "market_odds", "price", "american_odds", "book_odds"):
        val = alert.get(key)
        if val is None or val == "":
            continue
        try:
            odds = float(val)
            if odds != 0:
                return odds
        except Exception:
            continue
    return float(default)


def flat_bet_units(alert):
    conf = float(alert.get("confidence", 0) or 0)
    edge = float(alert.get("edge", 0) or 0)
    tier = str(alert.get("tier", "")).upper()
    market = str(alert.get("market", "")).lower()

    units = 0.5

    if conf >= 90:
        units += 0.9
    elif conf >= 87:
        units += 0.7
    elif conf >= 84:
        units += 0.5
    elif conf >= 80:
        units += 0.3
    elif conf >= 76:
        units += 0.15

    if edge >= 15:
        units += 0.4
    elif edge >= 10:
        units += 0.3
    elif edge >= 7:
        units += 0.2
    elif edge >= 5:
        units += 0.1

    if tier == "A":
        units += 0.2
    elif tier == "C":
        units -= 0.15
    elif tier == "D":
        units -= 0.3

    if market == "moneyline":
        units -= 0.1
    elif market == "total":
        units += 0.05

    units = max(0.25, min(2.0, units))
    return round(units, 2)


def odds_to_b(american_odds):
    odds = float(american_odds)
    if odds < 0:
        return 100.0 / abs(odds)
    return odds / 100.0


def implied_prob_from_american(american_odds):
    odds = float(american_odds)
    if odds < 0:
        return abs(odds) / (abs(odds) + 100.0)
    return 100.0 / (odds + 100.0)


def sport_haircut(alert):
    sport = str(alert.get("sport_key", "")).lower()
    return float(SPORT_KELLY_HAIRCUT.get(sport, 0.90))


def kelly_units(alert, bankroll_units=100.0, fraction=0.25):
    """
    Real-odds-aware fractional Kelly with sport haircut.
    """
    p_model = implied_win_prob_from_conf(alert.get("confidence", 0))
    american_odds = get_alert_odds(alert, default=-110.0)
    b = odds_to_b(american_odds)
    q = 1.0 - p_model

    raw_kelly = ((b * p_model) - q) / b

    if raw_kelly <= 0:
        return 0.25, american_odds, implied_prob_from_american(american_odds)

    frac_kelly = raw_kelly * fraction
    units = frac_kelly * bankroll_units

    # sport-specific haircut
    units *= sport_haircut(alert)

    units = max(0.25, min(2.0, units))
    return round(units, 2), american_odds, implied_prob_from_american(american_odds)


def recommended_units(alert):
    flat_units = flat_bet_units(alert)
    kelly_sized, used_odds, market_prob = kelly_units(alert)

    rec = min(flat_units, kelly_sized)

    if str(alert.get("tier", "")).upper() == "C":
        rec = min(rec, 0.5)

    return round(max(0.25, rec), 2), flat_units, kelly_sized, used_odds, round(market_prob, 4)


def apply_card_exposure_cap(rows):
    total_rec = sum(r["recommended_units"] for r in rows)

    if total_rec <= MAX_CARD_EXPOSURE_UNITS or total_rec <= 0:
        for r in rows:
            r["capped_units"] = r["recommended_units"]
        return rows, 1.0, total_rec

    scale = MAX_CARD_EXPOSURE_UNITS / total_rec

    for r in rows:
        capped = round(max(0.25, r["recommended_units"] * scale), 2)
        r["capped_units"] = capped

    capped_total = round(sum(r["capped_units"] for r in rows), 2)
    return rows, round(scale, 4), capped_total


def main():
    alerts = load_alerts()
    active = filter_active(alerts)
    active = dedupe_alerts(active)
    active = latest_cycle_only(active, window_seconds=180)
    active = best_one_per_game(active)

    rows = []
    total_flat = 0.0
    total_kelly = 0.0
    total_rec = 0.0

    for a in active:
        rec_units, flat_units, kelly_sized, used_odds, market_prob = recommended_units(a)

        row = {
            "alert": a,
            "flat_units": flat_units,
            "kelly_units": kelly_sized,
            "recommended_units": rec_units,
            "used_odds": used_odds,
            "market_prob": market_prob,
        }
        rows.append(row)

        total_flat += flat_units
        total_kelly += kelly_sized
        total_rec += rec_units

    rows, card_scale, capped_total = apply_card_exposure_cap(rows)

    print("=== BET SIZE CARD ===")
    print(f"total_plays: {len(rows)}")
    print(f"max_card_exposure_units: {MAX_CARD_EXPOSURE_UNITS}")
    print(f"card_scale: {card_scale}")
    print()

    for r in rows:
        a = r["alert"]
        sport = a.get("sport_key", "")
        game = a.get("game", "UNKNOWN")
        market = a.get("market", "")
        selection = a.get("selection", "")
        edge = round(float(a.get("edge", 0) or 0), 2)
        conf = round(float(a.get("confidence", 0) or 0), 2)
        tier = a.get("tier", "")
        haircut = sport_haircut(a)

        print(f"--- {game} ---")
        print(
            f"[{sport}] {market} -> {selection} | "
            f"EDGE={edge} | CONF={conf} | TIER={tier}"
        )
        print(
            f"odds={r['used_odds']} | market_implied={r['market_prob']} | "
            f"sport_haircut={haircut} | "
            f"flat_units={r['flat_units']} | "
            f"kelly_units={r['kelly_units']} | "
            f"recommended_units={r['recommended_units']} | "
            f"capped_units={r['capped_units']}"
        )
        print()

    print(f"TOTAL FLAT UNITS: {round(total_flat, 2)}")
    print(f"TOTAL KELLY UNITS: {round(total_kelly, 2)}")
    print(f"TOTAL RECOMMENDED UNITS: {round(total_rec, 2)}")
    print(f"TOTAL CAPPED UNITS: {round(capped_total, 2)}")


if __name__ == "__main__":
    main()
