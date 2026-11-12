"""Kalshi DEMO NBA LIVE player-props lane, pacer-deciding (2026-10-31, Marvin approved; fake money, demo host only).
Mirrors /opt/polymarket-paper/pm_nba_live.py: live ESPN box -> kp_pacer (calibrated) win chance; pacer can't price ->
old model (Poly's per-minute rate x remaining minutes tail, shrunk 50% to Kalshi mid). Rows tagged prob_src pacer|old.
Gate: capped p (0.65) >= 62% and edge > 0 after Kalshi fee 0.07*p*(1-p), OR edge >= 5c. Price band 10-90c.
No double dipping: one bet per game+player+stat (any threshold/side), shared key with alert-driven bets (me_game_key),
tickers held/bought today skipped. Partial fills -> remainder rests 5 min (kalshi_demo.place_order).
OFF: lane  -> env KALSHI_NBA_PACER_LANE=off | data/KALSHI_NBA_PACER_LANE_OFF
     pacer -> PACER_KP_NBA_OFF (env or file, kp_pacer convention; lane then uses old model only)
--dry: prints would-place, orders nothing, writes nothing."""
import json, os, re, sys, importlib.util
import calib_size as CS   # 2026-11-01 calibration-tier sizing
from datetime import datetime
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
DATA = os.path.join(HERE, "data"); LOG = os.path.join(DATA, "demo_log.jsonl"); RATES = os.path.join(DATA, "nba_rates_cache.json")
CAP, P_GATE, MIN_EDGE, SHRINK, LO, HI = 0.65, 0.62, 0.05, 0.50, 10, 90
SER = {"PTS": "KXNBAPTS", "REB": "KXNBAREB", "AST": "KXNBAAST", "3PM": "KXNBA3PT", "STL": "KXNBASTL", "BLK": "KXNBABLK", "PRA": "KXNBAPRA"}
MK = {"PTS": "player_points", "REB": "player_rebounds", "AST": "player_assists", "3PM": "player_threes", "STL": "player_steals",
      "BLK": "player_blocks", "PRA": "player_points_rebounds_assists"}
DRY = "--dry" in sys.argv
fee = lambda q: 0.07 * q * (1 - q)
def gate_ok(p, edge): return edge >= MIN_EDGE or (min(p, CAP) >= P_GATE and edge > 0)

# 2026-11-05 kpfix (bug fix only, no strategy change): transient SSL/connection errors (ESPN handshake timeouts) crashed
# runs, and slow calls ran past the unit's TimeoutStartSec=110 (SIGTERM). Retry idempotent GETs with backoff, use
# shorter per-request timeouts, and keep a run deadline so the run always finishes (and logs) inside the unit timeout.
import time, ssl, socket, urllib.error, http.client
T0 = time.monotonic(); BUDGET_S = float(os.environ.get("NBA_PACER_BUDGET_S", "85"))
def left(): return BUDGET_S - (time.monotonic() - T0)
def _transient(ex):
    if isinstance(ex, urllib.error.HTTPError): return ex.code in (429, 500, 502, 503, 504)
    if isinstance(ex, (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError, ConnectionError, http.client.HTTPException)): return True
    try:
        import requests
        if isinstance(ex, (requests.ConnectionError, requests.Timeout)): return True
        if isinstance(ex, requests.HTTPError) and ex.response is not None: return ex.response.status_code in (429, 500, 502, 503, 504)
    except ImportError: pass
    return False
def retry(fn, tries=3, what="req"):
    for i in range(tries):
        try: return fn()
        except Exception as ex:
            wait = 1.0 * 2 ** i
            if i == tries - 1 or not _transient(ex) or left() < wait + 10: raise
            print(f"retry {what} {i + 1}/{tries - 1} after {type(ex).__name__}: {str(ex)[:100]}", flush=True); time.sleep(wait)

def _poly():
    sp = importlib.util.spec_from_file_location("pm_nba_live_ro", "/opt/polymarket-paper/pm_nba_live.py")
    m = importlib.util.module_from_spec(sp); sys.path.insert(0, "/opt/polymarket-paper"); sp.loader.exec_module(m)
    import urllib.request as _ur, json as _j
    def _get(url):   # same as pm_nba_live.get (bad-JSON ValueError kept) but 8 s timeout + retry on transient errors
        def once():
            with _ur.urlopen(_ur.Request(url, headers=m.UA), timeout=8) as r: body = r.read()
            try: return _j.loads(body)
            except ValueError: raise ValueError(f"bad JSON from {url.split('?')[0][:90]} ({len(body)} bytes: {body[:60]!r})")
        return retry(once, what="espn")
    m.get = _get; return m

def _harden(c):
    """Kalshi DEMO session: GETs get (5 s connect, 10 s read) timeouts + retry; non-GET (orders) unchanged, never retried."""
    orig = c.s.request
    def req(method, url, **kw):
        if method.upper() != "GET": return orig(method, url, **kw)
        kw["timeout"] = (5, 10)
        def once():
            r = orig(method, url, **kw)
            if r.status_code in (429, 500, 502, 503, 504): r.raise_for_status()
            return r
        return retry(once, what="kalshi")
    c.s.request = req; return c

NBA_ALT = {"SA": "SAS", "GS": "GSW", "NO": "NOP", "NY": "NYK", "UTAH": "UTA", "WSH": "WAS"}
def _same_game(et, codes):
    """Kalshi event KXNBAPTS-26OCT31OKCHOU == today's ET date + both ESPN team codes (NBA aliases), either order."""
    from zoneinfo import ZoneInfo
    from datetime import timedelta
    et = (et or "").upper(); mt = re.match(r"^[A-Z0-9]+-(\d{2}[A-Z]{3}\d{2})([A-Z]+)$", et)
    if not mt: return False
    day = (datetime.now(ZoneInfo("America/New_York")) - timedelta(hours=5)).strftime("%y%b%d").upper()
    a, b = [NBA_ALT.get(x.upper(), x.upper()) for x in codes]
    return mt.group(1) == day and mt.group(2) in (a + b, b + a)

def lane_off():
    return os.environ.get("KALSHI_NBA_PACER_LANE", "on").lower() in ("off", "0", "false", "no") or os.path.exists(os.path.join(DATA, "KALSHI_NBA_PACER_LANE_OFF"))

def fav_track_on():
    """2026-11-03 Marvin: NBA live favorites (ask > 50c) track-only. Off: KALSHI_NBA_LIVE_FAV_TRACK=off | data/KALSHI_NBA_LIVE_FAV_TRACK_OFF"""
    return os.environ.get("KALSHI_NBA_LIVE_FAV_TRACK", "on").lower() not in ("off", "0", "false", "no") and not os.path.exists(os.path.join(DATA, "KALSHI_NBA_LIVE_FAV_TRACK_OFF"))

def compose_order_reason(*, kill, stake, calib_skip, placed, max_orders, demo_auto, has_key, ask, edge,
                         fav_on, min10_on, force_tracked, autopause_paused):
    """Track-only / skip reasons for one pacer candidate.
    force_track is unchanged and is applied before lane autopause, so an
    already force-tracked lane keeps the force_track reason. Autopause only
    adds a track-only shadow when the lane is allowlisted and latched; the
    default allowlist is empty, so autopause_paused stays false unless opted in."""
    reason = None
    if kill:
        reason = f"kill: {kill}"
    elif stake <= 0:
        reason = calib_skip or "kelly stake 0"
    elif placed >= max_orders:
        reason = "max orders this run"
    elif not (demo_auto and has_key):
        reason = "DEMO_AUTO off / no key"
    fav_track = reason is None and ask > 50 and fav_on
    if fav_track:
        reason = "track-only: nba_prop_live_pacer_fav"
    min10 = reason is None and edge < 0.10 and min10_on
    if min10:
        reason = "track-only: nba_prop_live_pacer_lt10"
    ftrack = reason is None and force_tracked
    if ftrack:
        reason = "track-only: nba_prop_live_pacer (force_track)"
    ap_track = reason is None and autopause_paused
    if ap_track:
        reason = "track-only: nba_prop_live_pacer (lane autopause)"
    return {"reason": reason, "fav_track": fav_track, "min10": min10, "ftrack": ftrack, "ap_track": ap_track}

def main():
    if lane_off(): print("kalshi nba pacer lane OFF"); return
    if os.path.exists(os.path.join(HERE, "KILL")): print("KILL file"); return
    import yaml, autogate as ag, prop_feed as PF
    from mapper import event_exact
    from kalshi_demo import KalshiDemo, _normalize_market
    sys.path.insert(0, "/opt/marv-feed/reports/prop_pacer"); import kp_pacer as KP
    cfg = yaml.safe_load(open(os.path.join(HERE, "kalshi_demo.yaml"))); gcfg = ag.load_cfg(os.path.join(HERE, "autogate.yaml"))
    assert gcfg.get("AUTO_ENABLED") is False
    PM = _poly(); games = PM.boxes()
    if not games: print("no NBA games in progress"); return
    c = _harden(KalshiDemo()); today = datetime.now(ag.CT).date().isoformat()
    if not DRY:
        try: c.sweep_resting()   # partial-fill remainders past 5 min (live_runner also sweeps)
        except Exception as ex: print("rest sweep err", ex)
    rows = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    settled = [{"pnl": r.get("pnl"), "bet_date": r.get("date")} for r in rows if r.get("mode") == "demo"]
    try: settled += [{"pnl": x.get("pnl"), "bet_date": x.get("date")} for x in map(json.loads, open(os.path.join(DATA, "results.jsonl")))]
    except Exception: pass
    kill = ag.kill_check(settled, gcfg, ag.load_state(), datetime.now(ag.CT).date())
    ok_rows = [r for r in rows if r.get("mode") == "demo" and r.get("order") and not r.get("error")]
    done_games = {r.get("me_game_key") for r in ok_rows if r.get("me_game_key")}
    skip = {r.get("ticker") for r in ok_rows if r.get("date") == today}
    if c.has_key:
        for mp in (c.positions().get("market_positions") or []):
            if float(mp.get("position_fp") or 0) != 0: skip.add(mp.get("ticker"))
    cache = json.load(open(RATES)) if os.path.exists(RATES) else {}
    st = {"games": len(games), "markets": 0, "priced": {"pacer": 0, "old": 0}, "pass_gate": 0, "placed": 0, "would": 0, "skip": {}}
    def sk(k): st["skip"][k] = st["skip"].get(k, 0) + 1
    cands = []
    for stt, ser in SER.items():
        try: ms = c.markets(series_ticker=ser, status="open", limit=1000).get("markets", [])
        except Exception as ex: sk(f"series err {ser}"); continue
        for m in ms:
            if left() < 15: sk("run budget: market scan stopped"); break
            name, _, thr = (m.get("yes_sub_title") or "").partition(":"); mt = re.search(r"(\d+)\+", thr)
            if not mt: continue
            T = int(mt.group(1)); pn = PM.norm(name)
            g = next((x for x in games if pn in x["players"]), None)
            if not g: continue
            p = g["players"][pn]; codes = sorted(g["teams"])
            if not _same_game(m.get("event_ticker"), codes): sk("event mismatch (other game/day)"); continue
            st["markets"] += 1
            if p["cur"][stt] >= T: sk("already hit"); continue
            ya, yb = m.get("yes_ask"), m.get("yes_bid")
            if not ya or not yb: sk("no quote"); continue
            src, why = "old", "pacer off (PACER_KP_NBA_OFF)"; po = None
            if not KP.off("nba"):
                try:
                    o = KP.nba(g["id"], p["name"], stt, T, timeout=max(3, min(25, left() - 12)))
                    if "p_over" in o: po, src, why = float(o["p_over"]), "pacer", None
                    else: why = o.get("err", "?")
                except Exception as ex: why = f"{type(ex).__name__}: {str(ex)[:80]}"
            if po is None:
                r = PM.rates(p["id"], cache)
                if not r: sk("old: no rates"); continue
                mu = r[stt] * PM.proj(p, g, r); pm = PM.tail(mu, T - p["cur"][stt], PM.DISP[stt])
                po = (1 - SHRINK) * pm + SHRINK * ((ya + yb) / 200)
            st["priced"][src] += 1
            for side, ps in (("yes", po), ("no", 1 - po)):
                ask = PF.side_ask(m, side)
                if not ask or not 0 < ask < 100: continue
                q = ask / 100; edge = min(ps, CAP) - q - fee(q)
                if not gate_ok(ps, edge): continue
                st["pass_gate"] += 1
                cands.append((edge, dict(m=m, g=g, p=p, stt=stt, T=T, side=side, ps=ps, src=src, why=why, ask=ask, edge=edge)))
    cands.sort(key=lambda x: -x[0]); placed = 0
    for _, x in cands:
        m, g, p, stt = x["m"], x["g"], x["p"], x["stt"]
        gk = f"{g['id']}:prop:{p['name']}:{MK[stt]}"
        if left() < -15: sk("run budget: order loop stopped"); break
        if gk in done_games: sk("dup game+player+stat"); continue
        if m["ticker"] in skip: sk("ticker held"); continue
        try: mk = _normalize_market(c._req("GET", f"/markets/{m['ticker']}").get("market", {}))
        except Exception: sk("refetch err"); continue
        ask = PF.side_ask(mk, x["side"])
        if mk.get("status") not in ("active", "open") or not ask: sk("not open"); continue
        q = ask / 100; edge = min(x["ps"], CAP) - q - fee(q)
        if not gate_ok(x["ps"], edge): sk("edge gone at refetch"); continue
        if not LO <= ask <= HI: sk("price outside 10-90c"); continue
        pk = min(x["ps"], CAP)
        stake = PF.kelly_usd(pk, ask, gcfg["bankroll"], gcfg["staking"]["after_kelly_frac"], gcfg["staking"]["after_cap_pct"], cfg["max_stake_usd"], 1.0)
        _cs, _cal = CS.size("kalshi", "nba_prop_live_pacer", pk, ask / 100, cap_usd=cfg["max_stake_usd"], dry=DRY, extra={"ticker": m["ticker"]})
        if _cs is not None: stake = _cs   # 2026-11-01 calibration-tier sizing (off: CALIB_SIZING=off | data/CALIB_SIZING_OFF)
        # 2026-11-03 Marvin: NBA live favorites (ask > 50c; lane_roi "nba:live:ml:fav" 19-11 -4.4%) TRACK-ONLY: no order,
        # logged to data/track_only.jsonl as nba_prop_live_pacer_fav + graded. Off: KALSHI_NBA_LIVE_FAV_TRACK=off | data/KALSHI_NBA_LIVE_FAV_TRACK_OFF
        # 2026-11-03 8:30 PM ET Marvin: NBA live orders need edge >= 10c; below -> track-only (nba_prop_live_pacer_lt10, graded).
        # Off: KALSHI_NBA_LIVE_MIN10=off | data/KALSHI_NBA_LIVE_MIN10_OFF
        min10_on = os.environ.get("KALSHI_NBA_LIVE_MIN10", "on").lower() not in ("off", "0", "false", "no") \
            and not os.path.exists(os.path.join(DATA, "KALSHI_NBA_LIVE_MIN10_OFF"))
        # 2026-11-09 Marvin: whole NBA pacer lane TRACK-ONLY via type_gate.json force_track "nba_prop_live_pacer" (graded in track_only.jsonl).
        # Off: remove it from force_track | env KALSHI_FORCE_TRACK=off | data/KALSHI_FORCE_TRACK_OFF
        # This check is unchanged. Lane autopause (below) does not edit force_track and only runs when the lane is allowlisted.
        try:
            import type_gate as _TG
            force_tracked = _TG.force_track("nba_prop_live_pacer", live=True)
        except Exception as ex:
            print("force_track check error -> track-only", str(ex)[:120]); force_tracked = True
        # Opt-in overconfident autopause. Default allowlist empty, so this stays false.
        # Intended candidate: kalshi:nba_prop_live_pacer. pm:nba_prop_live is not allowlisted and is not consulted here.
        autopause_paused = False
        if CS.autopause_allowlisted("kalshi", "nba_prop_live_pacer"):
            try:
                autopause_paused = bool(CS.autopause_status("kalshi", "nba_prop_live_pacer").get("paused"))
            except Exception as ex:
                print("autopause check error -> track-only", str(ex)[:120]); autopause_paused = True
        dec = compose_order_reason(kill=kill, stake=stake, calib_skip=(_cal or {}).get("calib_skip"), placed=placed,
                                   max_orders=cfg["max_orders_per_run"], demo_auto=bool(cfg.get("DEMO_AUTO")), has_key=bool(c.has_key),
                                   ask=ask, edge=edge, fav_on=fav_track_on(), min10_on=min10_on, force_tracked=force_tracked,
                                   autopause_paused=autopause_paused)
        reason, fav_track, min10, ftrack, ap_track = dec["reason"], dec["fav_track"], dec["min10"], dec["ftrack"], dec["ap_track"]
        go = reason is None and not DRY
        n = max(1, int(stake * 100 // ask))
        rec = {"ts": datetime.now(ag.CT).isoformat(), "date": today, "key": f"nbapacer:{g['id']}:{PM.norm(p['name'])}:{stt}:{x['T']}:{x['side']}",
               "pick": f"LIVE {p['name']} {'' if x['side'] == 'yes' else 'NOT '}{x['T']}+ {stt}", "ticker": m["ticker"], "side": x["side"],
               "ask": ask, "fair_c": round(100 * pk), "edge_c": round(100 * edge, 2), "stake": stake, **(_cal or {}), "kill": kill, "me_game_key": gk,
               "src": "nba_pacer_lane", "lane": "kalshi_nba_pacer", "bet_type": "nba_prop_live_pacer", "me_type": "nba_prop_live_pacer",
               "prob_src": x["src"], "why_old": x["why"], "p_side": round(x["ps"], 4), "prop_player": p["name"], "prop_market": MK[stt],
               "event_id": g["id"], "kalshi_line": x["T"] - 0.5, "cur": p["cur"][stt], "period": g["period"], "clock": g["clock"],
               "game": g["name"], "intended_count": n, "mode": "demo" if go else ("dry" if DRY else "shadow"), "reason": reason}
        if go:
            try:
                rec["order"] = c.place_order(m["ticker"], x["side"], n, ask, meta=rec); placed += 1
                rec["filled_count"] = rec["order"].get("filled_ioc"); skip.add(m["ticker"]); done_games.add(gk)
            except Exception as ex: rec["error"] = str(ex)[:200]
        elif DRY and reason is None:
            st["would"] += 1; done_games.add(gk)
        try: KP.log({"bot": "kalshi", "ex": "kalshi", "sport": "nba", "key": rec["key"], "pick": rec["pick"], "used": x["src"], "p_side": rec["p_side"], "why_old": x["why"], "dry": DRY})
        except Exception: pass
        if (fav_track or min10 or ftrack or ap_track) and not DRY:
            try:
                import type_gate as TG
                TG.track(os.path.join(DATA, "track_only.jsonl"), dict(rec, type="nba_prop_live_pacer_fav" if fav_track else ("nba_prop_live_pacer_lt10" if min10 else ("nba_prop_live_pacer" if ftrack else "nba_prop_live_pacer_autopause")), contracts=n, mode="track"))
            except Exception as ex: print("fav track error", str(ex)[:120])
        if not DRY:
            with open(LOG, "a") as f: f.write(json.dumps(rec) + "\n")
        print(("DRY would-place " if DRY and reason is None else "") + json.dumps({k: rec.get(k) for k in ("pick", "ticker", "side", "ask", "fair_c", "edge_c", "stake", "prob_src", "mode", "reason", "intended_count", "filled_count")}))
    st["placed"] = placed
    if not DRY:
        try: json.dump(cache, open(RATES + ".tmp", "w")); os.replace(RATES + ".tmp", RATES)
        except Exception: pass
    print(f"{datetime.now():%H:%M:%S} kalshi_nba_pacer {'DRY ' if DRY else ''}{json.dumps(st)}")

if __name__ == "__main__":
    main()
