"""Kalshi DEMO live-prop loop (fake money only). Lightweight; exits immediately when no fresh live props.
Rules: fresh <3 min, >=2 stars, exact-or-better line, re-check ask at order time, >=3c edge, $50 max,
<=5 orders/run, no duplicate tickers (held or bought today), KILL file, Marv game read must not oppose."""
import json, os
from datetime import datetime
import prop_feed as PF
import calib_size as CS   # 2026-11-01 calibration-tier sizing
_kfee = lambda a: (0.07 * a * (100 - a) / 100) if isinstance(a, (int, float)) and 0 < a < 100 else 0.0  # 2026-10-30 (Marvin): Kalshi taker fee in cents/contract, subtracted from edge before gate
HERE = os.path.dirname(os.path.abspath(__file__)); LOG = os.path.join(HERE, "data", "demo_log.jsonl")

LIVE_MIN_C, LIVE_MAX_C = 10, 90   # 2026-10-16: no live buys under 10c or over 90c (Kupp Over bought at 7c)

def main_march_edge(cfg):
    import autogate as ag, me_kalshi as MK
    from kalshi_demo import KalshiDemo
    import me_alerts as MA
    if not [a for a in MA.load({}, since_hours=1) if a.get("age_s", 1e9) <= MA.LIVE_MAX_AGE_S]:
        print("live(me): no fresh sent alerts"); return
    if os.path.exists(os.path.join(HERE, "KILL")):
        print("live: KILL file"); return
    gcfg = ag.load_cfg(os.path.join(HERE, "autogate.yaml")); assert gcfg.get("AUTO_ENABLED") is False
    if not (cfg.get("DEMO_AUTO") and cfg.get("DEMO_LIVE_PROPS")):
        print("live: disabled in yaml"); return
    c = KalshiDemo(); today = datetime.now(ag.CT).date().isoformat()
    rows = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    settled = [{"pnl": r.get("pnl"), "bet_date": r.get("date")} for r in rows if r.get("mode") == "demo"]
    try:
        settled += [{"pnl": x.get("pnl"), "bet_date": x.get("date")} for x in map(json.loads, open(os.path.join(HERE, "data", "results.jsonl")))]
    except Exception:
        pass
    kill = ag.kill_check(settled, gcfg, ag.load_state(), datetime.now(ag.CT).date())
    skip = {r.get("ticker") for r in rows if r.get("date") == today and r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    for mp in (c.positions().get("market_positions") or []):
        if float(mp.get("position_fp") or 0) != 0: skip.add(mp.get("ticker"))
    def log(rec):
        with open(LOG, "a") as f: f.write(json.dumps(rec) + "\n")
    try:   # 2026-10-31 kalfix: cancel partial-fill remainders past their 5-min window
        sw = c.sweep_resting()
        if sw: print("rest sweep:", sw)
    except Exception as ex:
        print("rest sweep err", ex)
    out, placed, mst = MK.run(c, cfg, gcfg, {}, kill, rows, skip, live=True, DRY=os.environ.get("DEMO_DRY") == "1", log=log)
    print("live(me) done:", mst, MK.summarize(out))

def main():
    import yaml
    cfg0 = yaml.safe_load(open(os.path.join(HERE, "kalshi_demo.yaml")))
    if cfg0.get("source", "marv") == "march_edge":
        return main_march_edge(cfg0)
    st = {}; lp = PF.picks_live(st)
    if not lp:
        print("live: none", st); return
    if os.path.exists(os.path.join(HERE, "KILL")):
        print("live: KILL file"); return
    import yaml, autogate as ag
    from kalshi_demo import KalshiDemo
    cfg = yaml.safe_load(open(os.path.join(HERE, "kalshi_demo.yaml")))
    gcfg = ag.load_cfg(os.path.join(HERE, "autogate.yaml"))
    assert gcfg.get("AUTO_ENABLED") is False
    if not (cfg.get("DEMO_AUTO") and cfg.get("DEMO_PROPS") and cfg.get("DEMO_LIVE_PROPS")):
        print("live: disabled in yaml"); return
    c = KalshiDemo(); today = datetime.now(ag.CT).date().isoformat()
    rows = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    settled = [{"pnl": r.get("pnl"), "bet_date": r.get("date")} for r in rows if r.get("mode") == "demo"]
    try:  # 2026-10-15: graded P&L (grader -> results.jsonl) feeds the daily-loss stop, same as runner.py
        settled += [{"pnl": x.get("pnl"), "bet_date": x.get("date")} for x in map(json.loads, open(os.path.join(HERE, "data", "results.jsonl")))]
    except Exception:
        pass
    kill = ag.kill_check(settled,
                         gcfg, ag.load_state(), datetime.now(ag.CT).date())
    if kill:
        print("live: kill", kill); return
    skip = {r.get("ticker") for r in rows if r.get("date") == today and r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    for mp in (c.positions().get("market_positions") or []):
        if float(mp.get("position_fp") or 0) != 0: skip.add(mp.get("ticker"))
    done = {(r.get("prop_player"), r.get("prop_market"), r.get("event_id")) for r in rows
            if r.get("date") == today and r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    cands = []
    for pk in lp:
        base = {"src": "live", "player": pk["player"], "market": pk["market"], "side": pk["side"], "line": pk["line"],
                "ev": pk["ev"], "p": pk["p"], "event_id": pk.get("event_id")}
        m, why = PF.map_prop(pk, c)
        if not m:
            PF.log_match(dict(base, result="skip", reason=why)); print("live unmapped", pk["player"], pk["market"], pk["side"], pk["line"], why); continue
        g = PF.marv_game_read(pk, m)
        if g["verdict"] == "opposes":
            PF.log_match(dict(base, result="skip", reason="marv opposes", ticker=m["ticker"])); continue
        cands.append((m["edge_c"] + 100 * pk["ev"] + (pk.get("prio") or 0), pk, m, g))
    cands.sort(key=lambda x: -x[0]); placed = 0
    for comb, pk, m, g in cands:
        dk = (pk["player"], pk["market"], pk.get("event_id"))
        if m["ticker"] in skip or dk in done or placed >= cfg["max_orders_per_run"]:
            PF.log_match({"src": "live", "player": pk["player"], "market": pk["market"], "line": pk["line"], "result": "skip",
                          "reason": "dup player/stat/game, held, or max orders", "ticker": m["ticker"]}); continue
        cur = PF._n  # noqa
        mk = c._req("GET", f"/markets/{m['ticker']}").get("market", {})
        from kalshi_demo import _normalize_market
        ask = PF.side_ask(_normalize_market(mk), m.get("side", "yes"))  # re-check live ask (YES or NO)
        fair_c = round(100 * m["p_kalshi"]); edge_c = round(fair_c - (ask or 999) - _kfee(ask), 2)
        stake = PF.kelly_usd(m["p_kalshi"], ask or 0, gcfg["bankroll"], gcfg["staking"]["after_kelly_frac"],
                             gcfg["staking"]["after_cap_pct"], cfg["max_stake_usd"], float(pk.get("stake_mult") or 1.0))
        _cs, _cal = CS.size("kalshi", "legacy_prop", m["p_kalshi"], (ask or 0) / 100, cap_usd=cfg["max_stake_usd"], dry=not ask, extra={"ticker": m["ticker"]})
        if _cs is not None: stake = _cs   # 2026-11-01 calibration-tier sizing (off: CALIB_SIZING=off | data/CALIB_SIZING_OFF)
        go = (mk.get("status") in ("active", "open") and ask and LIVE_MIN_C <= ask <= LIVE_MAX_C
              and edge_c >= cfg["min_edge_cents"] and stake > 0)
        rec = {"ts": datetime.now(ag.CT).isoformat(), "date": today, "key": f"live:{pk['player']}:{pk['market']}:{pk['line']}",
               "pick": f"LIVE {pk['player']} {pk['market']} {'u' if pk['side'] == 'under' else 'o'}{pk['line']}", "side": m.get("side", "yes"), "ticker": m["ticker"], "ask": ask, "fair_c": fair_c,
               "edge_c": edge_c, "age_s": pk["age_s"], "marv": g["verdict"], "stake": stake, **(_cal or {}), "kalshi_line": m["kalshi_line"],
               "prop_player": pk["player"], "prop_market": pk["market"], "event_id": pk.get("event_id"), "mode": "demo" if go else "shadow"}
        if go:
            try:
                rec["order"] = c.place_order(m["ticker"], m.get("side", "yes"), max(1, int(stake * 100 // ask)), ask, meta=rec); placed += 1; skip.add(m["ticker"])
            except Exception as ex:
                rec["error"] = str(ex)
        with open(LOG, "a") as f: f.write(json.dumps(rec) + "\n")
        PF.log_match({"src": "live", "player": pk["player"], "market": pk["market"], "line": pk["line"], "ticker": m["ticker"],
                      "result": "order" if go else "skip", "reason": None if go else "edge/stake/market status at re-check",
                      "ask": ask, "edge_c": edge_c, "stake": stake})
        print(rec)
    print(f"live done: {st} cands={len(cands)} placed={placed}")

if __name__ == "__main__":
    main()
