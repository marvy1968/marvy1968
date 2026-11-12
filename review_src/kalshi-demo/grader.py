"""Kalshi DEMO grader (fake money; DEMO API only - KalshiDemo refuses non-demo hosts). Never prints secrets.
Orders = /portfolio/fills grouped by order_id (joined to data/demo_log.jsonl for pick/date).
Settlement = /portfolio/settlements (fallback GET /markets/{ticker} result). Writes data/results_ledger.csv,
data/results.jsonl (read by runner.py daily-loss stop) and data/summary.json."""
import csv, json, os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from kalshi_demo import KalshiDemo
HERE = os.path.dirname(os.path.abspath(__file__)); CT = ZoneInfo("America/Chicago"); DATA = os.path.join(HERE, "data")
LOG = os.path.join(DATA, "demo_log.jsonl")
def pages(c, path, key):
    out, cur = [], None
    for _ in range(50):
        p = {"limit": 200}
        if cur: p["cursor"] = cur
        d = c._req("GET", path, auth=True, params=p); out += d.get(key) or []; cur = d.get("cursor")
        if not cur: break
    return out
def ct(iso): return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(CT)
def load_jsonl(path):
    """Missing demo_log / resting / calib files are empty. The grader does not require them to exist."""
    try:
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]
    except OSError:
        return []
_META = ("date", "key", "pick", "lane", "bet_type", "me_type", "me_alert_id", "prop_player", "me_game_key")
def index_logs(logrows):
    bylog = {}
    for r in logrows or []:
        if r.get("mode") != "demo" or not r.get("order"):
            continue
        oid = (r.get("order") or {}).get("order_id")
        if oid:
            bylog[oid] = r
    return bylog
def index_parents(resting_rows, bylog):
    """Child order_id -> parent order_id, from resting_orders and from a parent log's order.rest."""
    parent_of = {}
    for row in resting_rows or []:
        child, parent = row.get("order_id"), row.get("parent_order_id")
        if child and parent:
            parent_of.setdefault(child, parent)
    for oid, lg in (bylog or {}).items():
        rest = ((lg.get("order") or {}).get("rest") or {})
        if rest.get("order_id"):
            parent_of.setdefault(rest["order_id"], oid)
    return parent_of
def _copy_meta(src, inherited_from=None):
    out = {k: src.get(k) for k in _META if src.get(k) is not None}
    if inherited_from:
        out["inherited_from"] = inherited_from
    return out
def settlement_me_type(meta):
    """Bucket a fill. bet_type wins so a Marv total child lands in master_total:nba, not the old 'marv' fallback."""
    if not meta:
        return None
    if meta.get("bet_type"):
        return meta["bet_type"]
    if meta.get("me_type"):
        return meta["me_type"]
    key = str(meta.get("key") or "")
    parts = key.split(":")
    mk = {"h2h": "ml", "ml": "ml", "moneyline": "ml", "total": "total", "spread": "spread", "ats": "spread"}
    if key.startswith("master:") and len(parts) >= 3:
        return "master_%s:%s" % (mk.get(parts[1], parts[1]), parts[2])
    if meta.get("prop_player"):
        return "legacy_prop"
    if meta.get("key") or meta.get("pick"):
        return "marv"
    return None
def _same_second(a, b):
    try:
        ta = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        tb = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return int(ta.timestamp()) == int(tb.timestamp())
def match_calib(ticker, filled_ts, calib_rows):
    """Same ticker and the same UTC second. A later second is not a match (no nearest-neighbor)."""
    for row in calib_rows or []:
        if row.get("ticker") == ticker and _same_second(filled_ts, row.get("ts")):
            return row
    return None
def resolve_meta(order, bylog, parent_of, calib_rows):
    oid = order.get("order_id")
    if oid in bylog:
        return _copy_meta(bylog[oid]), "demo_log"
    parent = (parent_of or {}).get(oid)
    if parent and parent in bylog:
        return _copy_meta(bylog[parent], parent), "parent"
    hit = match_calib(order.get("ticker"), order.get("filled_ts"), calib_rows)
    if hit and hit.get("calib_lane"):
        return {"me_type": hit["calib_lane"], "bet_type": hit["calib_lane"], "lane": hit["calib_lane"]}, "calib_decisions"
    return {}, "missing"
def join_meta(orders, logrows, resting_rows, calib_rows):
    """Attach lane / bet_type / key / pick onto fill rows. Pure: does not read or write ledgers."""
    bylog = index_logs(logrows)
    parent_of = index_parents(resting_rows, bylog)
    out = []
    for order in orders:
        meta, how = resolve_meta(order, bylog, parent_of, calib_rows)
        row = dict(order)
        filled = order.get("filled_ts")
        row.update(date=meta.get("date") or (ct(filled).date().isoformat() if filled else None),
                   key=meta.get("key"), pick=meta.get("pick"), lane=meta.get("lane"), bet_type=meta.get("bet_type"),
                   me_type=settlement_me_type(meta), me_alert_id=meta.get("me_alert_id"), meta_src=how)
        out.append(row)
    return out
def rec(rows):
    w = sum(r["result"] == "win" for r in rows); l = sum(r["result"] == "loss" for r in rows); p = sum(r["result"] == "push" for r in rows)
    risk = sum(r["cost"] for r in rows); pnl = sum(r["pnl"] for r in rows)
    return {"W": w, "L": l, "P": p, "record": f"{w}-{l}-{p}", "risked": round(risk, 2), "pnl": round(pnl, 2),
            "roi_pct": round(100 * pnl / risk, 2) if risk else None}
def main():
    c = KalshiDemo(); assert c.has_key, "no demo key"
    logrows = load_jsonl(LOG)   # missing demo_log is an empty join, not a crash
    demo = [r for r in logrows if r.get("mode") == "demo"]
    bylog = index_logs(logrows)
    parent_of = index_parents(load_jsonl(os.path.join(DATA, "resting_orders.jsonl")), bylog)
    calib_rows = load_jsonl(os.path.join(DATA, "calib_decisions.jsonl"))
    failed = [r for r in demo if r.get("error")]
    fills = [f for f in pages(c, "/portfolio/fills", "fills") if f.get("action") == "buy" or f.get("side") == "no"]  # 2026-10-15: NO buys fill as action=sell, side=no
    sett = {s["ticker"]: s for s in pages(c, "/portfolio/settlements", "settlements")}
    orders = {}
    for f in fills:
        o = orders.setdefault(f["order_id"], {"order_id": f["order_id"], "ticker": f["ticker"], "side": f.get("side"),
                                              "contracts": 0.0, "notional": 0.0, "fee": 0.0, "filled_ts": f["created_time"]})
        n = float(f["count_fp"]); px = float(f["yes_price_dollars"] if f.get("side") == "yes" else f["no_price_dollars"])
        o["contracts"] += n; o["notional"] += n * px; o["fee"] += float(f.get("fee_cost") or 0)
        o["filled_ts"] = min(o["filled_ts"], f["created_time"])
    mcache, rows, now = {}, [], datetime.now(CT)
    for o in sorted(orders.values(), key=lambda x: x["filled_ts"]):
        meta, _how = resolve_meta(o, bylog, parent_of, calib_rows)
        o.update(date=meta.get("date") or ct(o["filled_ts"]).date().isoformat(), key=meta.get("key"), pick=meta.get("pick"),
                 lane=meta.get("lane"), bet_type=meta.get("bet_type"),
                 me_type=settlement_me_type(meta), me_alert_id=meta.get("me_alert_id"),
                 price=round(o["notional"] / o["contracts"], 4) if o["contracts"] else None,
                 cost=round(o["notional"] + o["fee"], 4), fee=round(o["fee"], 4), notional=round(o["notional"], 4))
        res_side_px = src = sts = None; s = sett.get(o["ticker"])
        if s:
            v = float(s.get("value") if s.get("value") is not None else (100 if s.get("market_result") == "yes" else 0)) / 100
            res_side_px = v if o["side"] == "yes" else 1 - v; src = "settlements"; sts = s.get("settled_time")
        else:
            if o["ticker"] not in mcache:
                try: mcache[o["ticker"]] = c._req("GET", f"/markets/{o['ticker']}").get("market") or {}
                except Exception: mcache[o["ticker"]] = {}
            m = mcache[o["ticker"]]; r = m.get("result")
            if m.get("status") in ("settled", "finalized") and r in ("yes", "no"):
                res_side_px = 1.0 if r == o["side"] else 0.0; src = "market_result"; sts = m.get("settlement_ts") or m.get("close_time")
        if res_side_px is None:
            o.update(status="open", result=None, payout=None, pnl=None, settle_px=None, settle_src=None, settled_ts=None)
        else:
            pay = round(o["contracts"] * res_side_px, 4)
            o.update(status="settled", settle_px=round(res_side_px, 4), payout=pay, pnl=round(pay - o["cost"], 4), settle_src=src,
                     settled_ts=ct(sts).isoformat() if sts else now.isoformat(),
                     result="win" if res_side_px >= 0.99 else "loss" if res_side_px <= 0.01 else "push")
        rows.append(o)
    keys = ["order_id", "date", "key", "pick", "ticker", "side", "contracts", "price", "notional", "fee", "cost", "filled_ts",
            "status", "result", "settle_px", "payout", "pnl", "settle_src", "settled_ts", "me_type", "me_alert_id", "lane", "bet_type"]
    def put(name, writer):
        tmp = os.path.join(DATA, name + ".tmp")
        with open(tmp, "w", newline="") as f: writer(f)
        os.replace(tmp, os.path.join(DATA, name))
    put("results_ledger.csv", lambda f: (lambda w: (w.writeheader(), w.writerows(rows)))(csv.DictWriter(f, keys, extrasaction="ignore")))
    st = [r for r in rows if r["status"] == "settled"]; op = [r for r in rows if r["status"] == "open"]
    put("results.jsonl", lambda f: [f.write(json.dumps({k: r[k] for k in keys}) + "\n") for r in st])
    bal = c.balance(); today = now.date().isoformat(); yday = (now.date() - timedelta(days=1)).isoformat()
    bys = {}; byb = {}
    for r in st: bys.setdefault(r["settled_ts"][:10], []).append(r); byb.setdefault(r["date"], []).append(r)
    s = {"generated": now.isoformat(), "demo_only": True, "source": "Kalshi DEMO API (demo-api.kalshi.co)",
         "cash": round(bal.get("balance", 0) / 100, 2), "portfolio_value": round(bal.get("portfolio_value", 0) / 100, 2),
         "account_total": round((bal.get("balance", 0) + bal.get("portfolio_value", 0)) / 100, 2),
         "all_time": rec(st), "today_ct": {"date": today, **rec(bys.get(today, []))}, "yesterday_ct": {"date": yday, **rec(bys.get(yday, []))},
         "daily_by_settle_date": {d: rec(v) for d, v in sorted(bys.items())},
         "daily_by_bet_date": {d: rec(v) for d, v in sorted(byb.items())},
         "by_alert_type": {t: rec([r for r in st if (r.get("me_type") or "untagged") == t]) for t in sorted({r.get("me_type") or "untagged" for r in st})},
         "open_by_alert_type": {t: sum(1 for r in op if (r.get("me_type") or "untagged") == t) for t in sorted({r.get("me_type") or "untagged" for r in op})},
         "orders_filled": len(rows), "orders_failed": len(failed),
         "failed_by_date": {d: sum(1 for r in failed if r.get("date") == d) for d in sorted({r.get("date") for r in failed})},
         "open": {"count": len(op), "cost": round(sum(r["cost"] for r in op), 2),
                  "positions": [{k: r[k] for k in ("order_id", "date", "pick", "ticker", "side", "contracts", "price", "cost")} for r in op]}}
    put("summary.json", lambda f: json.dump(s, f, indent=1))
    for r in st: print("settled", r["date"], r["pick"], r["ticker"], r["result"], round(r["pnl"], 2))
    print(f"all-time {s['all_time']} | cash {s['cash']} portfolio {s['portfolio_value']} | open {s['open']['count']} (${s['open']['cost']}) | failed {len(failed)}")
if __name__ == "__main__": main()
