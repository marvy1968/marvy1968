#!/usr/bin/env python3
import json
from collections import defaultdict
from pathlib import Path

TRACK_FILE = Path("storage/tracker/alerts.jsonl")
RESULTS_FILE = Path("storage/tracker/game_results.json")


def load_alerts():
    if not TRACK_FILE.exists():
        return []
    rows = []
    for line in TRACK_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def save_alerts(rows):
    TRACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TRACK_FILE.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def load_results():
    if not RESULTS_FILE.exists():
        return {}
    try:
        return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_results(data):
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_result(game, home_score, away_score):
    data = load_results()
    data[game] = {"home_score": float(home_score), "away_score": float(away_score)}
    save_results(data)
    print("saved:", game)


def infer_teams(row):
    game = str(row.get("game", ""))
    if " @ " in game:
        away_team, home_team = game.split(" @ ", 1)
        return away_team.strip(), home_team.strip()
    return "", ""


def dedupe_alerts(rows):
    best = {}

    for idx, r in enumerate(rows):
        key = (
            str(r.get("phase", "")),
            str(r.get("game", "")),
            str(r.get("market", "")),
            str(r.get("selection", "")),
        )

        prev = best.get(key)
        if prev is None:
            best[key] = (idx, r)
            continue

        prev_idx, prev_row = prev
        prev_ts = str(prev_row.get("ts_utc", ""))
        curr_ts = str(r.get("ts_utc", ""))

        if curr_ts and prev_ts:
            if curr_ts >= prev_ts:
                best[key] = (idx, r)
        else:
            if idx >= prev_idx:
                best[key] = (idx, r)

    out = [pair[1] for pair in sorted(best.values(), key=lambda x: x[0])]
    return out


def grade():
    alerts = load_alerts()
    alerts = dedupe_alerts(alerts)
    results = load_results()

    for r in alerts:
        game = r.get("game")
        if game not in results:
            r["result"] = "pending"
            r["profit_units"] = 0.0
            continue

        res = results[game]
        home = float(res["home_score"])
        away = float(res["away_score"])

        market = str(r.get("market", "")).lower()
        sel = r.get("selection")
        line = float(r.get("market_value", 0) or 0)

        away_team, home_team = infer_teams(r)
        r["home_team"] = home_team
        r["away_team"] = away_team

        # TOTAL
        if market == "total":
            total = home + away
            if total == line:
                r["result"] = "push"
            elif str(sel).lower() == "over":
                r["result"] = "win" if total > line else "loss"
            else:
                r["result"] = "win" if total < line else "loss"

        # MONEYLINE
        elif market == "moneyline":
            if home == away:
                r["result"] = "push"
            else:
                winner = home_team if home > away else away_team
                r["result"] = "win" if sel == winner else "loss"

        # SPREAD
        elif market == "spread":
            # market_value = home spread convention
            if sel == home_team:
                adjusted_home = home + line
                if adjusted_home == away:
                    r["result"] = "push"
                else:
                    r["result"] = "win" if adjusted_home > away else "loss"
            elif sel == away_team:
                away_line = -line
                adjusted_away = away + away_line
                if adjusted_away == home:
                    r["result"] = "push"
                else:
                    r["result"] = "win" if adjusted_away > home else "loss"
            else:
                r["result"] = "pending"

        else:
            r["result"] = "pending"

        if r["result"] == "win":
            r["profit_units"] = 1.0
        elif r["result"] == "loss":
            r["profit_units"] = -1.0
        else:
            r["profit_units"] = 0.0

    save_alerts(alerts)
    print("graded unique rows:", len(alerts))


def _show_bucket(title, bucket):
    print(title)
    for key, vals in sorted(bucket.items(), key=lambda x: (-x[1]["units"], x[0])):
        wins = vals["wins"]
        losses = vals["losses"]
        pushes = vals["pushes"]
        bets = vals["bets"]
        units = round(vals["units"], 2)
        hit = round((wins / (wins + losses) * 100.0), 2) if (wins + losses) else 0.0
        print(f"  {key}: bets={bets} wins={wins} losses={losses} pushes={pushes} hit%={hit} units={units}")
    print()


def summary():
    rows = load_alerts()
    rows = dedupe_alerts(rows)
    settled = [r for r in rows if r.get("result") in ["win", "loss", "push"]]

    print("UNIQUE ALERTS:", len(rows))
    print("SETTLED:", len(settled))

    wins = sum(1 for r in settled if r["result"] == "win")
    losses = sum(1 for r in settled if r["result"] == "loss")
    pushes = sum(1 for r in settled if r["result"] == "push")
    units = sum(float(r.get("profit_units", 0.0) or 0.0) for r in settled)

    print("WINS:", wins)
    print("LOSSES:", losses)
    print("PUSHES:", pushes)
    print("UNITS:", round(units, 2))
    if wins + losses > 0:
        print("WIN RATE:", round(wins / (wins + losses) * 100, 2), "%")
    print()

    by_market = defaultdict(lambda: {"bets": 0, "wins": 0, "losses": 0, "pushes": 0, "units": 0.0})
    by_tier = defaultdict(lambda: {"bets": 0, "wins": 0, "losses": 0, "pushes": 0, "units": 0.0})
    by_sport = defaultdict(lambda: {"bets": 0, "wins": 0, "losses": 0, "pushes": 0, "units": 0.0})

    for r in settled:
        market = str(r.get("market", "unknown"))
        tier = str(r.get("tier", "unknown"))
        sport = str(r.get("sport_key", "unknown"))
        result = str(r.get("result", "pending"))
        units_val = float(r.get("profit_units", 0.0) or 0.0)

        for bucket, key in ((by_market, market), (by_tier, tier), (by_sport, sport)):
            bucket[key]["bets"] += 1
            bucket[key]["units"] += units_val
            if result == "win":
                bucket[key]["wins"] += 1
            elif result == "loss":
                bucket[key]["losses"] += 1
            elif result == "push":
                bucket[key]["pushes"] += 1

    _show_bucket("BY MARKET", by_market)
    _show_bucket("BY TIER", by_tier)
    _show_bucket("BY SPORT", by_sport)

    print("LAST 10 UNIQUE SETTLED")
    for r in settled[-10:]:
        print(
            f"{r['game']} | {r['market']} | {r['selection']} | "
            f"tier={r.get('tier')} | result={r['result']} | units={r.get('profit_units', 0.0)}"
        )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: set_result | grade | summary")
        raise SystemExit(0)

    cmd = sys.argv[1]

    if cmd == "set_result":
        set_result(sys.argv[2], float(sys.argv[3]), float(sys.argv[4]))
    elif cmd == "grade":
        grade()
    elif cmd == "summary":
        summary()
