"""Export the VM's paper / demo underdog bets into the dog_lanes.py input format (read-only on every store).

Sources (schemas as printed by `dog_lanes.py inspect` on 2026-11-15):
  Polymarket paper  /opt/polymarket-paper/data/pm_paper.db, table fills: key 'h2h:<sport>:<event>:<home|away>' (the
                    side of the named `pick`), side long (bought the pick) / short (bought against it), price and fair
                    (model probability) are both for the side BOUGHT, contracts, result win/loss, lane, bovada_price.
  Marv CLV          /opt/marv-bot/state/marv_clv.db, table clv: rows src='polymarket', key 'pm:<fill id>' give the
                    game's start time and the closing fair probability of `pick` (close_fair_p).
  Kalshi demo       /opt/kalshi-demo/data/demo_log.jsonl: only rows with mode 'demo', a filled order and a result field
                    are exported (shadow rows have no order and no grade).

A bet is a DOG when the price paid for its side is below --dog-max (default 0.5).
Lane = the fill's `lane` column when set, otherwise '<sport>:ml dog' (the fill came from the moneyline feed).
Columns not saved on the fills (spread, open line, sharp books) are left out; dog_lanes.py skips those breakdowns.
Bovada's price at bet time is exported as sharp_p and labelled in the report notes: it is NOT a sharp book.
fav_price is approximated as 1 - price + 0.01 (the other side's ask on a 1c-wide book).

usage: python tools/dog_lanes/export_vm.py OUT.csv [--dog-max 0.5]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PM_DB = "/opt/polymarket-paper/data/pm_paper.db"
CLV_DB = "/opt/marv-bot/state/marv_clv.db"
KALSHI_LOG = "/opt/kalshi-demo/data/demo_log.jsonl"
FIELDS = ["lane", "venue", "sport", "game", "side", "start", "placed", "price", "model_p", "market_p", "stake",
          "is_home", "sharp_p", "close_p", "fav_price", "result", "source_id"]


def american_to_prob(a):
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    if 0 < a < 1:
        return a
    if a >= 100:
        return 100 / (a + 100)
    if a <= -100:
        return -a / (-a + 100)
    return None


def ro(path: str):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def clv_rows(path: str) -> dict:
    try:
        c = ro(path)
        return {k: (start, fair) for k, start, fair in
                c.execute("SELECT key, start, close_fair_p FROM clv WHERE src='polymarket'")}
    except Exception:
        return {}


def polymarket(pm_db: str, clv: dict, dog_max: float) -> list[dict]:
    out = []
    c = ro(pm_db)
    c.row_factory = sqlite3.Row
    for r in c.execute("SELECT * FROM fills"):
        r = dict(r)
        res = {"win": 1, "loss": 0}.get(str(r.get("result") or "").lower())
        price = r.get("price")
        if res is None or price is None or price >= dog_max:
            continue
        parts = str(r["key"]).split(":")
        if len(parts) < 4 or parts[0] != "h2h":
            continue
        sport, event, pick_side = parts[1], parts[2], parts[3]
        long = str(r.get("side") or "long").lower() == "long"
        is_home = int((pick_side == "home") == long)
        start, close_pick = clv.get(f"pm:{r['id']}", (None, None))
        close_p = None if close_pick is None else (close_pick if long else 1 - close_pick)
        bov = american_to_prob(r.get("bovada_price"))
        if bov is not None and not long:
            bov = 1 - bov
        out.append({"lane": r.get("lane") or f"{sport}:ml dog", "venue": "polymarket", "sport": sport,
                    "game": f"{sport}:{event}", "side": ("" if long else "against ") + str(r.get("pick")),
                    "start": start or r.get("date"), "placed": r.get("ts"), "price": price, "model_p": r.get("fair"),
                    "market_p": price, "stake": r.get("cost"), "is_home": is_home, "sharp_p": bov, "close_p": close_p,
                    "fav_price": round(1 - price + 0.01, 4), "result": res, "source_id": f"pm:{r['id']}"})
    return out


def kalshi(path: str, dog_max: float) -> tuple[list[dict], dict]:
    out, seen = [], {"rows": 0, "demo_filled": 0, "graded": 0}
    try:
        lines = open(path)
    except FileNotFoundError:
        return out, seen
    for line in lines:
        seen["rows"] += 1
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        o = r.get("order") or {}
        if r.get("mode") != "demo" or not o or float(o.get("fill_count") or 0) <= 0:
            continue
        seen["demo_filled"] += 1
        res = r.get("result") or o.get("result")
        res = {"win": 1, "yes": 1, "loss": 0, "no": 0}.get(str(res).lower())
        fp = float(o.get("average_fill_price") or 0)
        price = fp / 100 if fp > 1 else fp
        if res is None or not 0 < price < dog_max:
            continue
        seen["graded"] += 1
        parts = str(r.get("key", "")).split(":")
        sport = parts[1] if len(parts) > 1 else "?"
        out.append({"lane": r.get("lane") or f"{sport}:ml dog", "venue": "kalshi", "sport": sport,
                    "game": f"{sport}:{parts[2] if len(parts) > 2 else r.get('ticker')}", "side": r.get("pick"),
                    "start": r.get("start") or r.get("date"), "placed": r.get("ts"), "price": price,
                    "model_p": (r.get("fair_c") or 0) / 100 or None, "market_p": price,
                    "stake": price * float(o.get("fill_count") or 1),
                    "is_home": int(parts[3] == "home") if len(parts) > 3 else None,
                    "fav_price": round(1 - price + 0.01, 4), "result": res, "source_id": f"kalshi:{o.get('order_id')}"})
    return out, seen


def main(out_path: str, dog_max: float = 0.5, pm_db: str = PM_DB, clv_db: str = CLV_DB, kalshi_log: str = KALSHI_LOG):
    import csv
    pm = polymarket(pm_db, clv_rows(clv_db), dog_max)
    ks, kseen = kalshi(kalshi_log, dog_max)
    rows = pm + ks
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    lanes = {}
    for r in rows:
        lanes[r["lane"]] = lanes.get(r["lane"], 0) + 1
    print(f"exported {len(rows)} graded dog bets to {out_path}: Polymarket {len(pm)}, Kalshi {len(ks)} "
          f"(Kalshi log: {kseen['rows']} rows, {kseen['demo_filled']} demo fills, {kseen['graded']} graded dogs)")
    print("lanes:", ", ".join(f"{k} {v}" for k, v in sorted(lanes.items(), key=lambda kv: -kv[1])))
    print("note: sharp_p = Bovada's price at bet time (not a sharp book); fav_price = 1 - price + 0.01 (approximate)")
    return rows


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    dm = float(args[args.index("--dog-max") + 1]) if "--dog-max" in args else 0.5
    main(args[0], dm)
