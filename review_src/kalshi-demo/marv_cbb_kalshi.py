"""2026-10-25 (Marvin): Marv Predict CBB / WCBB board picks (/opt/marv-feed/data/{cbb,wcbb}/board_cbb.json) -> Kalshi DEMO
TRACK-ONLY rows in data/track_only.jsonl (lane marv_<cbb|wcbb>:<ml|spread|total>:<side>), one per game+side, graded by
track_grade.py. NEVER orders. Off: KALSHI_MARV_CBB_TRACK=off | data/KALSHI_MARV_CBB_TRACK_OFF. DRY=1 prints only.
MARV_CBB_BOARDS=path1,path2 overrides the board paths (tests)."""
import json, os, sys
from datetime import datetime
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import autogate as ag, type_gate as TG, me_kalshi as MK
from kalshi_demo import KalshiDemo
_kfee = lambda a: (0.07 * a * (100 - a) / 100) if isinstance(a, (int, float)) and 0 < a < 100 else 0.0  # 2026-10-30 (Marvin): Kalshi taker fee in cents/contract, subtracted from edge before gate
BOARDS = os.environ.get("MARV_CBB_BOARDS", "/opt/marv-feed/data/cbb/board_cbb.json,/opt/marv-feed/data/wcbb/board_cbb.json").split(",")
TRACK = os.path.join(HERE, "data", "track_only.jsonl"); STAKE = 50.0
MK_ = {"h2h": "ml", "spread": "spread", "total": "total"}
_G = {}
def _guard(c):
    """2026-10-26 (Marvin "Let it trade"): load the same guards runner.py uses, once per run."""
    if _G: return _G
    import yaml, side_guard as SG
    cfg = yaml.safe_load(open(os.path.join(HERE, "kalshi_demo.yaml"))); gcfg = ag.load_cfg(os.path.join(HERE, "autogate.yaml"))
    st = ag.load_state(); today = datetime.now(ag.CT).date(); LOG = os.path.join(HERE, "data", "demo_log.jsonl")
    rows = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    settled = [{"pnl": r.get("pnl"), "bet_date": r.get("date")} for r in rows if r.get("mode") == "demo"]
    try: settled += [{"pnl": g.get("pnl"), "bet_date": g.get("date")} for g in map(json.loads, open(os.path.join(HERE, "data", "results.jsonl")))]
    except Exception: pass
    kill = ag.kill_check(settled, gcfg, st, today) or ("manual KILL file" if os.path.exists(os.path.join(HERE, "KILL")) else None)
    skip = {r.get("ticker") for r in rows if r.get("date") == today.isoformat() and r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    try: skip |= {mp.get("ticker") for mp in (c.positions().get("market_positions") or []) if float(mp.get("position_fp") or 0) != 0}
    except Exception: pass
    _G.update(cfg=cfg, kill=kill, skip=skip, SG=SG, sides=SG.held_sides(rows, SG.all_tickers(rows, skip)), placed=0, LOG=LOG)
    return _G

def trade(c, e, m, rec, DRY):
    """Kalshi DEMO order for a Marv CBB/WCBB board pick. Guards: KALSHI_TRADE_ALL + KALSHI_MARV_CBB_TRADE switches, kill
    (autogate $300/day loss stop + KILL file), DEMO_AUTO + key, 65% win cap (wincap), ask re-check, min edge, 10-90c,
    $50/order, max orders/run, ticker held/bought today, one per game+side (side_guard). Off: KALSHI_MARV_CBB_TRADE=off |
    data/KALSHI_MARV_CBB_TRADE_OFF. Returns reason (None = ordered / would order)."""
    if not (MK._sw("KALSHI_MARV_CBB_TRADE") and TG.trade_all()): return "lane off"
    g = _guard(c); cfg = g["cfg"]; side = m.get("side", "yes")
    if g["kill"]: return f"kill: {g['kill']}"
    if not (cfg.get("DEMO_AUTO") and c.has_key): return "DEMO_AUTO off / no key"
    if m["ticker"] in g["skip"]: return "ticker held / bought today"
    sk = g["SG"].side_key(m["ticker"], e.get("pick"))
    if sk and sk in g["sides"]: return "double dip: game+side held"
    try:
        mk = MK._normalize_market(c._req("GET", f"/markets/{m['ticker']}").get("market", {})); ask = MK.PF.side_ask(mk, side); status = mk.get("status")
    except Exception: ask, status = rec["ask"], "unknown"
    if status not in ("active", "open", "unknown"): return f"market {status}"
    if not ask or not 10 <= int(ask) <= 90: return f"ask {ask} outside 10-90c"
    import wincap as WC
    fair_c = round(100 * WC.cap(float(e.get("p") or 0))); edge_c = round(fair_c - int(ask) - _kfee(int(ask)), 2)
    if edge_c < cfg["min_edge_cents"]: return f"edge {edge_c}c < {cfg['min_edge_cents']}c (65% cap)"
    if g["placed"] >= cfg["max_orders_per_run"]: return "max orders this run"
    stake = min(STAKE, float(cfg.get("max_stake_usd", STAKE))); n = max(1, int(stake * 100 // int(ask)))
    if DRY: print("WOULD ORDER", m["ticker"], side, n, "@", ask, "edge", edge_c); return None
    o = dict(rec, ts=datetime.now(ag.CT).isoformat(), key=f"marvcbb:{e.get('key')}", ask=int(ask), fair_c=fair_c, edge_c=edge_c, stake=stake,
             mode="demo", src="marv_cbb", bet_type=rec["type"], me_game_key=f"{e.get('event_id')}:{e.get('market')}::", reason=None)
    try:
        o["order"] = c.place_order(m["ticker"], side, n, int(ask)); g["placed"] += 1; g["skip"].add(m["ticker"])
        if sk: g["sides"].add(sk)
    except Exception as ex: o["error"] = str(ex)[:200]
    with open(g["LOG"], "a") as fh: fh.write(json.dumps(o) + "\n")
    return None if not o.get("error") else "order error"

def main():
    if not MK._sw("KALSHI_MARV_CBB_TRACK"): print("KALSHI_MARV_CBB_TRACK off"); return
    DRY = os.environ.get("DRY") == "1"; c = KalshiDemo(); today = datetime.now(ag.CT).date().isoformat()
    out = {"logged": 0, "not_listed": 0, "no_price": 0, "started": 0}
    for b in BOARDS:
        try: ents = json.load(open(b)).get("entries") or []
        except Exception: continue
        for e in ents:
            try:
                if datetime.fromisoformat(str(e["start"]).replace("Z", "+00:00")) <= datetime.now(ag.CT): out["started"] += 1; continue
            except Exception: pass
            lane0 = "marv_wcbb" if e.get("sport") == "wncaab" else "marv_cbb"
            e2 = dict(e, me_type="marv_predict", sport=e.get("sport"), league=e.get("sport"))
            try: m, why = MK.map_alert(e2, c)
            except Exception as x: m, why = None, f"err {x}"[:60]
            if not m: out["not_listed"] += 1; DRY and print("NOT_LISTED", e.get("pick"), why); continue
            ask = m.get("yes_ask")
            if not ask or not (1 <= int(ask) <= 99): out["no_price"] += 1; continue
            ask = int(ask); mk = MK_.get(e.get("market"), e.get("market"))
            sd = ("dog" if ask <= 50 else "fav") if mk == "ml" else e.get("side")
            rec = {"date": today, "type": f"{lane0}:{mk}:{sd}", "key": e.get("key"), "pick": e.get("pick"), "ticker": m["ticker"],
                   "side": m["side"], "ask": ask, "contracts": max(1, int(STAKE * 100 // ask)), "stake": STAKE, "fair_c": round(100 * float(e.get("p") or 0)),
                   "league": e.get("sport"), "market": e.get("market"), "src": e.get("src"), "event_id": e.get("event_id"), "reason": "track-only: marv predict cbb"}
            try: why_t = trade(c, e, m, rec, DRY)
            except Exception as x: why_t = f"trade err {str(x)[:80]}"
            out["trade:" + str(why_t or "ok").split(" ")[0]] = out.get("trade:" + str(why_t or "ok").split(" ")[0], 0) + 1
            if DRY: print("DRY", rec["type"], rec["pick"], rec["ticker"], rec["side"], ask, "|", why_t or "would order"); out["logged"] += 1; continue
            TG.track(TRACK, rec, dedupe=("type", "key")); out["logged"] += 1
    print("marv_cbb_kalshi:", out)
if __name__ == "__main__": main()
