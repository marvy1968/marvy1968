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
def rec(rows):
    w = sum(r["result"] == "win" for r in rows); l = sum(r["result"] == "loss" for r in rows); p = sum(r["result"] == "push" for r in rows)
    risk = sum(r["cost"] for r in rows); pnl = sum(r["pnl"] for r in rows)
    return {"W": w, "L": l, "P": p, "record": f"{w}-{l}-{p}", "risked": round(risk, 2), "pnl": round(pnl, 2),
            "roi_pct": round(100 * pnl / risk, 2) if risk else None}
def main():
    c = KalshiDemo(); assert c.has_key, "no demo key"
    logrows = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    demo = [r for r in logrows if r.get("mode") == "demo"]
    bylog = {(r.get("order") or {}).get("order_id"): r for r in demo if r.get("order")}
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
        lg = bylog.get(o["order_id"], {})
        o.update(date=lg.get("date") or ct(o["filled_ts"]).date().isoformat(), key=lg.get("key"), pick=lg.get("pick"),
                 me_type=lg.get("me_type") or (("legacy_prop" if lg.get("prop_player") else "marv") if lg else None), me_alert_id=lg.get("me_alert_id"),
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
            "status", "result", "settle_px", "payout", "pnl", "settle_src", "settled_ts", "me_type", "me_alert_id"]
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
