"""Kalshi DEMO runner: board + Marv ML preds -> map -> autogate (gates/stake/kill) -> shadow log or DEMO order.
Production is impossible: client refuses non-demo hosts; Bovada never auto."""
import json, os, sys
from datetime import datetime, timezone
import yaml
import autogate as ag
from kalshi_demo import KalshiDemo
from mapper import map_entry
import prop_feed as PF
import calib_size as CS   # 2026-11-01 calibration-tier sizing
_kfee = lambda a: (0.07 * a * (100 - a) / 100) if isinstance(a, (int, float)) and 0 < a < 100 else 0.0  # 2026-10-30 (Marvin): Kalshi taker fee in cents/contract, subtracted from edge before gate

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.environ.get("MARV_BOARD", "/opt/marv-bot/state/board.json")
PREDS = os.environ.get("MARV_PREDS", "/opt/marv-bot/state/predictions.json")
LOG = os.path.join(HERE, "data", "demo_log.jsonl")

def log(rec):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")

def _ml_from_predictions():
    """Build moneyline board rows from Marv predictions for games that have not started."""
    if not os.path.exists(PREDS):
        return []
    try:
        preds = json.load(open(PREDS))
    except Exception:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for key, v in (preds.items() if isinstance(preds, dict) else []):
        if not isinstance(v, dict):
            continue
        sport = v.get("sport")
        if sport not in ("nfl", "cfb", "wnba", "nba", "mlb", "nhl"):
            continue
        hw = v.get("home_win")
        if hw is None:
            continue
        try:
            start = datetime.fromisoformat(str(v.get("start", "")).replace("Z", "+00:00"))
        except Exception:
            continue
        if start <= now:
            continue
        home, away = v.get("home") or "", v.get("away") or ""
        if not home or not away:
            continue
        # Emit both sides; edge filter decides which (if any) is a buy.
        gid = v.get("game_id") or key
        out.append({"key": f"h2h:{key}:home", "sport": sport, "market": "h2h", "pick": home, "home": home, "away": away,
                    "p": float(hw), "game": gid, "start": v.get("start"), "src": "predictions"})
        out.append({"key": f"h2h:{key}:away", "sport": sport, "market": "h2h", "pick": away, "home": home, "away": away,
                    "p": float(1.0 - float(hw)), "game": gid, "start": v.get("start"), "src": "predictions"})
    return out

try:
    sys.path.insert(0, "/opt/marv-feed"); from pick_feed import ml_from_predictions as _ml_from_predictions
except Exception as _ex:
    print("shared pick_feed unavailable, using local:", _ex)

def main():
    cfg = yaml.safe_load(open(os.path.join(HERE, "kalshi_demo.yaml")))
    gcfg = ag.load_cfg(os.path.join(HERE, "autogate.yaml"))
    assert gcfg.get("AUTO_ENABLED") is False, "AUTO_ENABLED must stay false (production lock)"
    st = ag.load_state(); today = datetime.now(ag.CT).date()
    c = KalshiDemo()
    if not c.has_key:
        print("NO DEMO KEY - see SIGNUP.md. Shadow-mapping only.")
    rows = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    settled = [{"pnl": r.get("pnl"), "bet_date": r.get("date")} for r in rows if r.get("mode") == "demo"]
    try:  # graded P&L from grader.py (data/results.jsonl) feeds the daily/weekly loss stop
        settled += [{"pnl": g.get("pnl"), "bet_date": g.get("date")} for g in map(json.loads, open(os.path.join(HERE, "data", "results.jsonl")))]
    except Exception as _ex:
        print("results.jsonl unavailable:", _ex)
    kill = ag.kill_check(settled, gcfg, st, today)
    if os.path.exists(os.path.join(HERE, "KILL")):
        kill = "manual KILL file"
    SOURCE = cfg.get("source", "marv")   # 2026-10-16: march_edge = trade March_edge SENT alerts; marv = legacy Marv/Master board
    print("source:", SOURCE)
    entries = []
    if SOURCE == "marv" and os.path.exists(BOARD):
        try:
            entries.extend(json.load(open(BOARD)).get("entries", []))
        except Exception as ex:
            print("board load err", ex)
    if SOURCE == "marv":
        entries.extend(_ml_from_predictions())
    # NBA-LANE 2026-10-23 (Marvin): Marv-direct lanes (e.g. nba) alongside March_edge; same edge/stake/kill guards below.
    MARV_LANES = set(cfg.get("marv_lanes") or [])
    if SOURCE == "march_edge" and MARV_LANES:
        try:
            import pick_feed as _PF
            import re as _re   # NBA-LANE2: board picks end in " ML"; Kalshi team match wants the bare team
            lane = [dict(e, lane="marv_" + str(e.get("sport")), pick=_re.sub(r"\s+ML$", "", str(e.get("pick") or ""))) for e in _PF.entries()
                    if e.get("sport") in MARV_LANES and e.get("market") != "prop"]
            print("marv lanes:", sorted(MARV_LANES), "entries:", len(lane)); entries.extend(lane)
        except Exception as ex:
            print("marv lane err", ex)
    import bovada_px as BPX
    # skip tickers already bought today (demo log) or still held
    bought_today = {r.get("ticker") for r in rows if r.get("date") == today.isoformat() and r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    held = set()
    if c.has_key:
        try:
            for mp in (c.positions().get("market_positions") or []):
                if float(mp.get("position_fp") or 0) != 0:
                    held.add(mp.get("ticker"))
        except Exception as ex:
            print("positions err", ex)
    skip = bought_today | held
    import side_guard as SG
    sides = SG.held_sides(rows, SG.all_tickers(rows, skip))   # 2026-10-24: + every past demo order (open or settled); KALSHI_DEDUPE_ALL=off   # 2026-10-19: one position per game+side (hedges allowed)
    placed = 0
    for e in entries:
        if not e.get("p"):
            continue
        try:  # NBA-LANE stale guard: never fill a pregame row once the game has started
            if e.get("start") and datetime.fromisoformat(str(e["start"]).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                continue
        except ValueError:
            pass
        try:
            m = map_entry(e, c)
        except Exception as ex:
            m = None
            print("map err", ex)
        if not m:
            continue
        if m.get("ticker") in skip:
            continue
        sk = SG.side_key(m.get("ticker"), e.get("pick"))
        if sk and sk in sides:
            print("skip same game+side (hedges only)", sk); continue
        ask = m.get("yes_ask") or 0
        import wincap as _WC   # 2026-10-26 cap 65% (off: KALSHI_WINCAP=off | data/KALSHI_WINCAP_OFF)
        fair_c = round(100 * _WC.cap(e["p"])); edge_c = round(fair_c - ask - _kfee(ask), 2)
        mult = float(e["stake"]) if e.get("stake") is not None else 1.0
        if mult <= 0:
            continue  # UF v2 stake 0 => skip
        stake = min(cfg["max_stake_usd"], gcfg["bankroll"] * gcfg["staking"]["ramp_flat_pct"] / 100) * mult
        gate_ok = ("game" in st.get("eligible_since", {})) or cfg.get("demo_bypass_gates", False)
        go = (cfg["DEMO_AUTO"] and c.has_key and not kill and gate_ok and 0 < ask < 100
              and edge_c >= cfg["min_edge_cents"] and placed < cfg["max_orders_per_run"])
        rec = {"ts": datetime.now(ag.CT).isoformat(), "date": today.isoformat(), "key": e.get("key"),
               "pick": e.get("pick"), "ticker": m["ticker"], "ask": ask, "fair_c": fair_c, "edge_c": edge_c,
               "stake": stake, "stake_mult": mult, "kill": kill, "mode": "demo" if go else "shadow"}
        if e.get("lane"): rec["lane"] = e["lane"]
        import type_gate as TG   # 2026-10-23 TYPE-GATE: only profitable types order; others track-only
        tt = TG.classify(e.get("key"), e.get("me_type"), e.get("market"), e.get("sport")); rec["bet_type"] = tt
        if go and not TG.allowed(tt):
            go = False; rec["mode"] = "shadow"; rec["reason"] = f"track-only: {tt}"
            if os.environ.get("DEMO_DRY") != "1":
                TG.track(os.path.join(HERE, "data", "track_only.jsonl"), dict(rec, type=tt, side="yes", contracts=max(1, int(stake * 100 // ask))))
        if go and os.environ.get("DEMO_DRY") == "1":
            go = False; rec["mode"] = "dry"; print("DRY would order", rec.get("pick"), rec.get("ticker"))
        if go:  # BOVADA-REF: Bovada price at fill time (CLV comparison)
            rec.update(BPX.lookup(e.get("sport"), e.get("market"), e.get("pick"), e.get("home"), e.get("away")))
            cnt = max(1, int(stake * 100 // ask))
            try:
                rec["order"] = c.place_order(m["ticker"], "yes", cnt, ask)
                placed += 1; sides.add(sk) if sk else None
            except Exception as ex:
                rec["error"] = str(ex)
        log(rec); print(rec)
    # ---- PROPS (2026-10-15: every March_edge prop with EV >= 5%, pregame; Marv game read may veto only if it opposes) ----
    DRY = os.environ.get("DEMO_DRY") == "1"
    if SOURCE == "march_edge":
        import me_kalshi as MK
        out, placed, mst = MK.run(c, cfg, gcfg, st, kill, rows, skip, live=False, DRY=DRY, log=log, placed0=placed)
        print("march_edge pregame:", mst, MK.summarize(out))
        print(f"done: placed={placed} DEMO_AUTO={cfg['DEMO_AUTO']} key={c.has_key} kill={kill} source=march_edge")
        return
    pst = {}; cands = []
    done_keys = {(r.get("prop_player"), r.get("prop_market"), r.get("event_id")) for r in rows
                 if r.get("date") == today.isoformat() and r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    for pk in PF.picks(pst):
        base = {"src": "pregame", "player": pk["player"], "market": pk["market"], "side": pk["side"], "line": pk["line"],
                "ev": pk["ev"], "p": pk["p"], "event_id": pk.get("event_id")}
        try:
            m, why = PF.map_prop(pk, c)
        except Exception as ex:
            m, why = None, f"map err {ex}"
        if not m:
            PF.log_match(dict(base, result="skip", reason=why)); continue
        g = PF.marv_game_read(pk, m)
        if g["verdict"] == "opposes":
            PF.log_match(dict(base, result="skip", reason="marv game read opposes", ticker=m["ticker"], why=g.get("why"))); continue
        cands.append((m["edge_c"] + 100 * pk["ev"] + (pk.get("prio") or 0), pk, m, base, g))
    cands.sort(key=lambda x: -x[0])
    pfill = 0
    for comb, pk, m, base, g in cands:
        dk = (pk["player"], pk["market"], pk.get("event_id"))
        if m["ticker"] in skip or dk in done_keys:
            PF.log_match(dict(base, result="skip", reason="dup player/stat/game or held", ticker=m["ticker"])); continue
        ask = m["yes_ask"]; fair_c = round(100 * m["p_kalshi"]); edge_c = round(m["edge_c"] - _kfee(ask), 2)
        stake = PF.kelly_usd(m["p_kalshi"], ask, gcfg["bankroll"], gcfg["staking"]["after_kelly_frac"],
                             gcfg["staking"]["after_cap_pct"], cfg["max_stake_usd"], float(pk.get("stake_mult") or 1.0))
        _cs, _cal = CS.size("kalshi", "legacy_prop", m["p_kalshi"], ask / 100, cap_usd=cfg["max_stake_usd"], dry=bool(DRY), extra={"ticker": m["ticker"]})
        if _cs is not None: stake = _cs   # 2026-11-01 calibration-tier sizing (off: CALIB_SIZING=off | data/CALIB_SIZING_OFF)
        gate_ok = ("game" in st.get("eligible_since", {})) or cfg.get("demo_bypass_gates", False)
        go = (cfg["DEMO_AUTO"] and cfg.get("DEMO_PROPS", False) and c.has_key and not kill and gate_ok and not DRY
              and 0 < ask < 100 and edge_c >= cfg["min_edge_cents"] and stake > 0 and placed < cfg["max_orders_per_run"])
        rec = {"ts": datetime.now(ag.CT).isoformat(), "date": today.isoformat(), "key": f"prop:{pk['player']}:{pk['market']}:{pk['line']}",
               "pick": f"{pk['player']} {pk['market']} {'u' if pk['side'] == 'under' else 'o'}{pk['line']}", "ticker": m["ticker"], "ask": ask, "fair_c": fair_c,
               "edge_c": edge_c, "comb": round(comb, 1), "kill": kill, "stake": stake, **(_cal or {}), "kalshi_line": m["kalshi_line"], "side": m.get("side", "yes"),
               "prop_player": pk["player"], "prop_market": pk["market"], "event_id": pk.get("event_id"), "marv": g.get("verdict"),
               "mode": "demo" if go else ("dry" if DRY else "shadow")}
        if go:
            try:
                rec["order"] = c.place_order(m["ticker"], m.get("side", "yes"), max(1, int(stake * 100 // ask)), ask, meta=rec)
                placed += 1; pfill += 1; skip.add(m["ticker"]); done_keys.add(dk)
            except Exception as ex:
                rec["error"] = str(ex)
        why = None if go else ("kill: " + str(kill) if kill else "edge < min at Kalshi price" if edge_c < cfg["min_edge_cents"]
                               else "dry run" if DRY else "max orders / stake 0 / disabled")
        PF.log_match(dict(base, result="order" if go else "skip", reason=why, ticker=m["ticker"], ask=ask,
                          kalshi_line=m["kalshi_line"], p_kalshi=m["p_kalshi"], edge_c=edge_c, stake=stake))
        if not DRY:
            log(rec)
        print(rec)
    print(f"props: {pst} marv_good+mapped={len(cands)} prop_orders={pfill}")
    print(f"done: placed={placed} DEMO_AUTO={cfg['DEMO_AUTO']} key={c.has_key} kill={kill} entries={len(entries)}")

if __name__ == "__main__":
    main()
 
