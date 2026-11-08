"""Kalshi DEMO execution of March_edge SENT alerts (2026-10-16; fake money, demo host only).
Used by runner.py (pregame alerts) and live_runner.py (in-game alerts <= 3 min old) when kalshi_demo.yaml source: march_edge."""
import json, os
from datetime import datetime
import autogate as ag
import me_alerts as MA
import prop_feed as PF
import calib_size as CS   # 2026-11-01 calibration-tier sizing (off: CALIB_SIZING=off | data/CALIB_SIZING_OFF)
import re
from zoneinfo import ZoneInfo
from mapper import _map_total, _norm, _alts, SERIES, TOTAL_SERIES
from kalshi_demo import _normalize_market
_kfee = lambda a: (0.07 * a * (100 - a) / 100) if isinstance(a, (int, float)) and 0 < a < 100 else 0.0  # 2026-10-30 (Marvin): Kalshi taker fee in cents/contract, subtracted from edge before gate
SERIES.setdefault("wncaab", "KXNCAAWBGAME")   # 2026-10-25: women's CBB game ML (track-only via KALSHI_CBB_TRACK; no WCBB total/spread series)

LIVE_MIN_C, LIVE_MAX_C = 10, 90      # live: never buy near-dead prices (<10c or >90c)

def _team_code(e):
    t = _norm(e.get("pick"))
    for side in ("home", "away"):
        n = _norm(e.get(side))
        if n and (t == n or n.endswith(t) or t.endswith(n)):
            return e.get(side + "_abbr")
    return None

_mk_cache = {}
def _open(c, **kw):
    k = tuple(sorted(kw.items()))
    if k not in _mk_cache:
        _mk_cache[k] = c.markets(status="open", limit=1000, **kw).get("markets", [])
    return _mk_cache[k]

def _nm(x):
    return _norm((x or "").replace("St.", "State")).strip()

def resolve_event(e, c):
    """EXACT Kalshi game event for the alert: suffix after the date == AWAY+HOME Kalshi codes, both verified
    (code = ESPN abbr alias, or the market's yes_sub_title == the ESPN team location/short name), same ET game day.
    Returns (event_ticker, {side: (code, market or None)}) or (None, reason)."""
    series = SERIES.get(e.get("sport"))
    if not series:
        return None, f"no Kalshi game series for {e.get('sport')}"
    try:
        st = datetime.fromisoformat(str(e.get("start")).replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date()   # Kalshi event date = ET game day
    except Exception:
        st = None
    names = {s: {_nm(e.get(s + "_loc")), _nm(e.get(s + "_short")), _nm(e.get(s))} - {""} for s in ("home", "away")}
    evs = {}
    for m in _open(c, series_ticker=series):
        evs.setdefault(m.get("event_ticker") or "", []).append(m)
    for et, ms in evs.items():
        mt = re.match(r"^[A-Z0-9]+-(\d{2}[A-Z]{3}\d{2})(?:\d{4})?([A-Z0-9]+)$", et)
        if not mt:
            continue
        if st:
            try:
                d = datetime.strptime(mt.group(1).title(), "%y%b%d").date()
                if d != st: continue      # exact game day (MLB/NHL series repeat the same pair)
            except ValueError:
                continue
        suf = mt.group(2); found = {}
        for m in ms:
            code = m["ticker"].split("-")[-1].upper(); sub = _nm(m.get("yes_sub_title"))
            for s in ("home", "away"):
                if code in _alts(e.get(s + "_abbr")) or (sub and sub in names[s]):
                    found[s] = (code, m)
        if not found:
            continue
        hc = found.get("home", (None,))[0]; ac = found.get("away", (None,))[0]
        if hc and not ac and suf.endswith(hc):
            x = suf[:-len(hc)]; ac = x if x in _alts(e.get("away_abbr")) else None
        if ac and not hc and suf.startswith(ac):
            x = suf[len(ac):]; hc = x if x in _alts(e.get("home_abbr")) else None
        if hc and ac and suf in (ac + hc, hc + ac):
            return et, {"home": found.get("home", (hc, None)), "away": found.get("away", (ac, None))}
    return None, "no exact Kalshi game (both team codes must match the event)"

def map_h2h_me(e, c):
    et, info = resolve_event(e, c)
    if not et:
        return None, info
    t = _norm(e.get("pick")); mine = None
    for s in ("home", "away"):
        n = _norm(e.get(s))
        if n and (t == n or n.endswith(t) or t.endswith(n)):
            mine = s
    if not mine:
        return None, "picked team not in game"
    other = "away" if mine == "home" else "home"
    m = info[mine][1]
    if m:      # YES on our team
        return {"ticker": m["ticker"], "side": "yes", "yes_ask": m.get("yes_ask"), "event_ticker": et, "yes_sub_title": m.get("yes_sub_title")}, None
    m = info[other][1]   # demo often lists one side only: NO on the opponent (2-way game market)
    return {"ticker": m["ticker"], "side": "no", "yes_ask": PF.side_ask(m, "no"), "event_ticker": et, "yes_sub_title": m.get("yes_sub_title")}, None

def map_total_me(e, c):
    pick = str(e.get("pick") or ""); sp = e.get("sport")
    over = pick.lower().startswith("over")
    if sp not in TOTAL_SERIES:
        return None, f"no Kalshi total series for {sp}"
    L = float(pick.split()[-1])
    et, info = resolve_event(e, c)
    if not et:
        m = _map_total(dict(e, allow_under=True), c)     # MLB path (codes from names)
        return (m, None) if m else (None, info)
    ev = TOTAL_SERIES[sp] + "-" + et.split("-", 1)[1]
    mk = c.markets(event_ticker=ev, status="open", limit=200).get("markets", [])
    side = "yes" if over else "no"
    ok = [m for m in mk if m.get("floor_strike") is not None and PF.side_ask(m, side)
          and ((over and 0 <= L - float(m["floor_strike"]) <= 1.5) or (not over and 0 <= float(m["floor_strike"]) - L <= 1.5))]
    if not ok:
        return None, "no Kalshi total strike within 1.5 (never worse)"
    m = max(ok, key=lambda m: float(m["floor_strike"])) if over else min(ok, key=lambda m: float(m["floor_strike"]))
    return {"ticker": m["ticker"], "side": side, "yes_ask": PF.side_ask(m, side), "event_ticker": ev, "strike": m["floor_strike"]}, None

SPREAD_SERIES = {"nfl": "KXNFLSPREAD", "cfb": "KXNCAAFSPREAD", "ncaaf": "KXNCAAFSPREAD", "nba": "KXNBASPREAD",
                 "ncaab": "KXNCAAMBSPREAD"}   # 2026-10-25: + CBB (track-only, KALSHI_CBB_TRACK)
# 2026-10-25 (Marvin): TRACK-ONLY soccer (MLS / EPL / UCL) + CBB. Off: KALSHI_SOCCER_TRACK / KALSHI_CBB_TRACK = off | data/<NAME>_OFF
SOCCER = {"usa.1", "mls", "eng.1", "epl", "uefa.champions", "ucl"}
WCBB_ALIASES = {"womens-college-basketball", "ncaaw", "wcbb", "wncaab"}
CBB_ALIASES = {"mens-college-basketball", "ncaam", "ncaamb", "cbb", "ncaab"}
EXTRA_TOTAL = {"usa.1": "KXMLSTOTAL", "mls": "KXMLSTOTAL", "eng.1": "KXEPLTOTAL", "epl": "KXEPLTOTAL",
               "uefa.champions": "KXUCLTOTAL", "ucl": "KXUCLTOTAL", "ncaab": "KXNCAAMBTOTAL"}
# 2026-10-30 after-reset job (Marvin "cover every sport each venue lists"): La Liga / Bundesliga / Serie A / Ligue 1
# (March_edge soccer leagues esp.1 / ger.1 / ita.1 / fra.1) are NEW on Kalshi -> game ML + totals FORCED TRACK-ONLY
# (KALSHI_TRADE_ALL does not lift it) until the Nov 7 by-sport review. Off: KALSHI_NEW_SOCCER_TRACK=off |
# data/KALSHI_NEW_SOCCER_TRACK_OFF (alerts go back to "no Kalshi market").
NEW_SOCCER = {"esp.1": "KXLALIGA", "ger.1": "KXBUNDESLIGA", "ita.1": "KXSERIEA", "fra.1": "KXLIGUE1"}
for _lg, _px in NEW_SOCCER.items():
    SERIES.setdefault(_lg, _px + "GAME"); EXTRA_TOTAL.setdefault(_lg, _px + "TOTAL"); SOCCER.add(_lg)
# 2026-10-30 after-reset job (Marvin: best-ROI traded types get priority + larger stakes): stake x1.25 for the two
# best-earning Kalshi traded types (THIN: cfb:live:ml:dog 4-1 +$822 +313% n=5; nfl:pre:ml:dog +47% n=6, 100-bet
# backtest +12%). Off: KALSHI_ROI_BOOST=off | data/KALSHI_ROI_BOOST_OFF
ROI_BOOST = {("cfb", True, "dog"): 1.25, ("nfl", False, "dog"): 1.25}
def track_lane(e):
    """'soccer' / 'cbb' when this entry belongs to a track-only lane that is switched on, else None."""
    sp = e.get("sport")
    if sp in NEW_SOCCER: return "soccer" if _sw("KALSHI_NEW_SOCCER_TRACK") else None
    if sp in SOCCER and _sw("KALSHI_SOCCER_TRACK"): return "soccer"
    if sp in ("ncaab", "wncaab") and _sw("KALSHI_CBB_TRACK"): return "cbb" if sp == "ncaab" else "wcbb"
    return None
def map_total_extra(e, c):
    """Soccer/CBB totals: Over L -> YES strike S <= L; Under L -> NO strike S >= L; |S-L| <= 0.75 soccer / 1.5 CBB (never worse)."""
    pick = str(e.get("pick") or ""); over = pick.lower().startswith("over")
    try: L = float(pick.split()[-1])
    except Exception: return None, "total line unparsed"
    et, info = resolve_event(e, c)
    if not et: return None, info
    tol = 0.75 if e.get("sport") in SOCCER else 1.5
    ev = EXTRA_TOTAL[e["sport"]] + "-" + et.split("-", 1)[1]; side = "yes" if over else "no"
    mk = c.markets(event_ticker=ev, status="open", limit=200).get("markets", [])
    ok = [m for m in mk if m.get("floor_strike") is not None and PF.side_ask(m, side)
          and ((over and 0 <= L - float(m["floor_strike"]) <= tol) or (not over and 0 <= float(m["floor_strike"]) - L <= tol))]
    if not ok: return None, f"no Kalshi total strike within {tol} (never worse)"
    m = max(ok, key=lambda m: float(m["floor_strike"])) if over else min(ok, key=lambda m: float(m["floor_strike"]))
    return {"ticker": m["ticker"], "side": side, "yes_ask": PF.side_ask(m, side), "event_ticker": ev, "strike": m["floor_strike"], "track_only": True}, None
def map_spread_me(e, c):
    """2026-10-25 (Marvin): TRACK-ONLY spread mapping (never ordered). 'Team -X' -> YES 'Team wins by over S', S <= X, X-S <= 1;
    'Team +X' -> NO on 'Opponent wins by over S', S >= X, S-X <= 1 (never worse). Off: KALSHI_SPREAD_TRACK=off | data/KALSHI_SPREAD_TRACK_OFF"""
    ser = SPREAD_SERIES.get(e.get("sport"))
    if not ser: return None, f"no Kalshi spread series for {e.get('sport')}"
    try: X = float(str(e.get("pick")).split()[-1])
    except Exception: return None, "spread line unparsed"
    et, info = resolve_event(e, c)
    if not et: return None, info
    t = _norm(" ".join(str(e.get("pick")).split()[:-1])); mine = None
    for sd in ("home", "away"):
        n = _norm(e.get(sd))
        if n and (t == n or n.endswith(t) or t.endswith(n)): mine = sd
    if not mine: return None, "picked team not in game"
    fav = X < 0; team = mine if fav else ("away" if mine == "home" else "home"); code = info[team][0]
    ev = ser + "-" + et.split("-", 1)[1]; side = "yes" if fav else "no"
    mk = [m for m in c.markets(event_ticker=ev, status="open", limit=200).get("markets", [])
          if m.get("floor_strike") is not None and str(m.get("ticker", "")).rsplit("-", 1)[-1].startswith(str(code)) and PF.side_ask(m, side)]
    A = abs(X)
    ok = [m for m in mk if (fav and 0 <= A - float(m["floor_strike"]) <= 1.0) or (not fav and 0 <= float(m["floor_strike"]) - A <= 1.0)]
    if not ok: return None, "no Kalshi spread strike within 1 (never worse)"
    m = max(ok, key=lambda m: float(m["floor_strike"])) if fav else min(ok, key=lambda m: float(m["floor_strike"]))
    return {"ticker": m["ticker"], "side": side, "yes_ask": PF.side_ask(m, side), "event_ticker": ev, "strike": m["floor_strike"], "track_only": True}, None

def map_alert(e, c):
    if e.get("sport") in WCBB_ALIASES: e["sport"] = "wncaab"
    if e.get("sport") in CBB_ALIASES: e["sport"] = "ncaab"          # March_edge CBB league key -> Kalshi/ESPN key
    tl = track_lane(e)
    if e.get("sport") in NEW_SOCCER and not tl:
        return None, f"{e.get('sport')}: new Kalshi league off (KALSHI_NEW_SOCCER_TRACK)"
    if tl and e["market"] == "total" and e.get("sport") in EXTRA_TOTAL:
        return map_total_extra(e, c)
    if tl and e["market"] in ("h2h", "spread"):
        m, why = (map_h2h_me(e, c) if e["market"] == "h2h" else map_spread_me(e, c))
        if m: m["track_only"] = True
        return m, why
    if e["market"] == "prop":
        aa, ha = e.get("away_abbr"), e.get("home_abbr")
        try:
            et, info = resolve_event(e, c)
            if et: aa, ha = info["away"][0], info["home"][0]      # Kalshi's own codes for this exact game
        except Exception:
            pass
        m, why = PF.map_prop({"league": e["league"], "market": e["prop_market"], "player": e["player"], "side": e["side"],
                              "line": e["line"], "p": e["p"], "away_abbr": aa, "home_abbr": ha}, c)
        return m, why
    if e["market"] == "h2h":
        return map_h2h_me(e, c)
    if e["market"] == "total":
        return map_total_me(e, c)
    if e["market"] == "spread" and _sw("KALSHI_SPREAD_TRACK"):
        return map_spread_me(e, c)
    return None, f"{e['market']} not offered on Kalshi mapper"

def _sw(name):
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    return os.environ.get(name, "on").lower() not in ("off", "0", "false", "no") and not os.path.exists(os.path.join(d, name + "_OFF"))

def _not_listed(today, a, e, why):
    """2026-10-25: count alerts with no Kalshi market (per day / league:market:pre|live) in data/not_listed.json."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "not_listed.json")
    try: d = json.load(open(p))
    except Exception: d = {}
    k = f"{a.get('league')}:{e.get('market')}:{'live' if e.get('live') else 'pre'}"; day = d.setdefault(today, {}); ids = day.setdefault(k, [])
    if str(a.get("id")) not in ids: ids.append(str(a.get("id")))
    try:
        tmp = p + ".tmp"; json.dump(d, open(tmp, "w"), indent=1); os.replace(tmp, p)
    except Exception: pass

def run(c, cfg, gcfg, st, kill, rows, skip, live, DRY, log, placed0=0):
    import type_gate as TG   # 2026-10-23 TYPE-GATE: only profitable types order; others track-only
    TRACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "track_only.jsonl")
    stats = {}; today = datetime.now(ag.CT).date().isoformat()
    done_alerts = {r.get("me_alert_id") for r in rows if r.get("me_alert_id") and r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    done_games = {r.get("me_game_key") for r in rows if r.get("me_game_key") and r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    placed = placed0; out = []
    import side_guard as SG
    sides = SG.held_sides(rows, SG.all_tickers(rows, skip))   # 2026-10-24: + every past demo order (open or settled); KALSHI_DEDUPE_ALL=off     # 2026-10-19: one position per game+side across ML/spread/total
    # 2026-10-23 (Marvin): every live alert is judged on its first live run (13-185s after the alert). Later runs
    # used to re-check it and log "live alert stale" (live runner) / "game final" (pregame runner) every pass,
    # which hid the real first-look reason. Now: pregame runner ignores live-type alerts (live runner owns them);
    # live runner judges an alert once it is past the window only if it never saw it while fresh (logged once).
    seen_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "me_live_seen.json")
    try:
        seen = set(json.load(open(seen_p))) if live else set()
    except Exception:
        seen = set()
    LIVE_T = ("total_swing", "line_swing", "soccer_grind", "prop_live_unsent")
    for a in MA.load(stats):
        t0 = MA.alert_type(a)
        if not live and (t0.endswith("_live") or t0 in LIVE_T):
            stats["live_type_other_runner"] = stats.get("live_type_other_runner", 0) + 1; continue
        if live:
            if (a.get("age_s") or 1e9) > MA.LIVE_MAX_AGE_S and str(a["id"]) in seen:
                stats["judged_earlier"] = stats.get("judged_earlier", 0) + 1; continue
            seen.add(str(a["id"]))
        e, why = MA.to_entry(a)
        res = {"alert_id": a["id"], "type": e.get("me_type"), "pick": e.get("pick") or a.get("selection"), "league": a.get("league")}
        def skipr(r, **kw):
            res.update(result="skip", reason=r, **kw); out.append(res)
            PF.log_match(dict(res, src="me_live" if live else "me_pregame"))
        if why: skipr(why); continue
        if bool(e.get("live")) != bool(live):
            res.update(result="other_runner"); continue      # pregame alerts -> runner.py, live -> live_runner.py
        ok, why = MA.fresh_enough(e)
        if not ok: skipr(why); continue
        if a["id"] in done_alerts: skipr("alert already traded"); continue
        gk = f"{e['event_id']}:{e['market']}:{e.get('player') or ''}:{e.get('prop_market') or ''}"
        if gk in done_games: skipr("already hold this game+market"); continue
        try:
            m, why = map_alert(e, c)
        except Exception as ex:
            m, why = None, f"map err {str(ex)[:80]}"
        if not m:
            if not DRY and _sw("KALSHI_SHADOW_ALL"): _not_listed(today, a, e, why)
            skipr(why); continue
        side = m.get("side", "yes")
        if m["ticker"] in skip: skipr("ticker held / bought today", ticker=m["ticker"]); continue
        sk = SG.side_key(m["ticker"], e.get("pick"))
        if sk and sk in sides: skipr("double dip: already hold this game+side (hedges only)", ticker=m["ticker"]); continue
        try:   # re-check the ask at order time
            mk = _normalize_market(c._req("GET", f"/markets/{m['ticker']}").get("market", {}))
            ask = PF.side_ask(mk, side); status = mk.get("status")
        except Exception as ex:
            ask, status = m.get("yes_ask"), "unknown"
        pk = m.get("p_kalshi") or e["p"]
        if str(e.get("market") or "").lower() != "prop":   # 2026-10-26 cap 65%, non-props (off: KALSHI_WINCAP=off)
            import wincap as _WC; pk = _WC.cap(pk)
        fair_c = round(100 * pk); edge_c = round(fair_c - (ask or 999) - _kfee(ask), 2)
        stake = PF.kelly_usd(pk, ask or 0, gcfg["bankroll"], gcfg["staking"]["after_kelly_frac"],
                             gcfg["staking"]["after_cap_pct"], cfg["max_stake_usd"], float(e.get("stake") or 1.0))
        _bm = ROI_BOOST.get((e.get("sport"), bool(live), "dog" if (ask or 99) <= 50 else "fav")) if str(e.get("market", "")).lower() in ("h2h", "ml", "moneyline") else None
        if _bm and stake > 0 and _sw("KALSHI_ROI_BOOST"): stake = round(stake * _bm, 2)
        if str(e.get("me_type") or "").startswith("team_trend") and _sw("KALSHI_TT_CAP"): stake = min(stake, 15.0)   # 2026-11-01 Marvin: cap team_trend at prop size (off: KALSHI_TT_CAP=off)
        # 2026-11-01 Marvin: calibration-tier sizing replaces Kelly (hard caps max_stake_usd / KALSHI_TT_CAP stay upper bounds)
        _cap = float(cfg["max_stake_usd"]) * (1.0 if float(e.get("stake") or 1.0) > 0 else 0.0)
        if str(e.get("me_type") or "").startswith("team_trend") and _sw("KALSHI_TT_CAP"): _cap = min(_cap, 15.0)
        _cs, _cal = CS.size("kalshi", e.get("me_type") or "marv", pk, (ask or 0) / 100, cap_usd=_cap, dry=bool(DRY) or not ask,
                            extra={"key": f"me:{a['id']}", "ticker": m["ticker"], "pick": e.get("pick")})
        if _cs is not None: stake = _cs
        reason = None
        if kill: reason = f"kill: {kill}"
        elif status not in ("active", "open", "unknown"): reason = f"market {status}"
        elif not ask or not 0 < ask < 100: reason = "no ask"
        elif live and not (LIVE_MIN_C <= ask <= LIVE_MAX_C): reason = f"live price {ask}c outside {LIVE_MIN_C}-{LIVE_MAX_C}c"
        elif edge_c < cfg["min_edge_cents"]: reason = f"edge {edge_c}c < {cfg['min_edge_cents']}c at Kalshi price"
        elif stake <= 0: reason = (_cal or {}).get("calib_skip") or "kelly stake 0"
        elif placed >= cfg["max_orders_per_run"]: reason = "max orders this run"
        elif not (cfg.get("DEMO_AUTO") and c.has_key): reason = "DEMO_AUTO off / no key"
        elif e["market"] == "prop" and not cfg.get("DEMO_PROPS"): reason = "DEMO_PROPS off"
        tt = TG.classify(None, e.get("me_type"), e.get("market"), e.get("league"))
        track_only = reason is None and not TG.allowed(tt)
        # 2026-10-25 (Marvin "paper track all sports"): skips with a real Kalshi price (edge below min / live price band)
        # are logged track-only as shadow_<kind>:<type>:<pre|live>[:dog|fav] so track_grade.py gives every lane an ROI.
        # No order. Off: KALSHI_SHADOW_ALL=off | data/KALSHI_SHADOW_ALL_OFF
        if reason and ask and 0 < ask < 100 and not DRY and not kill and _sw("KALSHI_SHADOW_ALL") and (
                reason.startswith("edge ") or reason.startswith("live price ")):
            _ml = str(e.get("market", "")).lower() in ("h2h", "ml", "moneyline")
            _lt = f"shadow_{'edge' if reason.startswith('edge') else 'band'}:{tt}:{'live' if live else 'pre'}" + ((":dog" if ask <= 50 else ":fav") if _ml else "")
            TG.track(TRACK, {"ts": datetime.now(ag.CT).isoformat(), "date": today, "key": f"me:{a['id']}", "pick": ("LIVE " if live else "") + str(e.get("pick")),
                             "ticker": m["ticker"], "side": side, "ask": ask, "fair_c": fair_c, "edge_c": edge_c, "stake": 50.0, "mode": "track",
                             "type": _lt, "bet_type": tt, "reason": reason, "me_type": e.get("me_type"), "league": a.get("league"),
                             "contracts": max(1, int(5000 // ask))})
        # 2026-10-25 (Marvin "optimize to max ROI"): PREGAME ML favorites (ask > 50c) track-only (5-3 -$59, thin n=8),
        # logged as <type>_fav; pregame ML dogs + all live alerts unchanged. Off: KALSHI_PRE_FAV_TRACK=off | data/KALSHI_PRE_FAV_TRACK_OFF
        if (reason is None and not track_only and not live and str(e.get("market", "")).lower() in ("h2h", "ml", "moneyline")
                and ask and ask > 50 and _sw("KALSHI_PRE_FAV_TRACK") and (not TG.trade_all() or TG.force_track("pre_ml_fav"))):   # 2026-11-02 force_track (KALSHI_FORCE_TRACK)   # 2026-10-26 KALSHI_TRADE_ALL: favorites trade
            tt = str(tt) + "_fav"; track_only = True
        if reason is None and m.get("track_only"):   # 2026-10-25: spreads are track-only, never ordered
            _tl = track_lane(e); _pk = str(e.get("pick", "")).lower()
            _sd = ("under" if "under" in _pk else "over") if e["market"] == "total" else (("dog" if (ask or 0) <= 50 else "fav") if e["market"] == "h2h" else "spread")
            tt = (f"{_tl}:{e.get('league')}:{e['market']}:{'live' if live else 'pre'}:{_sd}:{e.get('me_type')}" if _tl
                  else f"spread:{e.get('league')}:{'live' if live else 'pre'}"); track_only = not TG.allowed(tt) if TG.trade_all() else True   # 2026-10-26 KALSHI_TRADE_ALL
        if reason is None and e.get("sport") in NEW_SOCCER:   # 2026-10-30: new Kalshi soccer leagues never order (see NEW_SOCCER)
            track_only = True
            if not str(tt).startswith("soccer:"): tt = f"soccer:{e.get('league')}:{e['market']}:{'live' if live else 'pre'}:{e.get('me_type')}"
        if track_only: reason = f"track-only: {tt}"
        go = reason is None and not DRY
        rec = {"ts": datetime.now(ag.CT).isoformat(), "date": today, "key": f"me:{a['id']}", "pick": ("LIVE " if live else "") + str(e.get("pick")),
               "ticker": m["ticker"], "side": side, "ask": ask, "fair_c": fair_c, "edge_c": edge_c, "stake": stake, **(_cal or {}), "kill": kill,
               "me_alert_id": a["id"], "me_type": e["me_type"], "me_game_key": gk, "src": "march_edge", "source": e.get("source") or "sent_alert",
               "prop_player": e.get("player"), "prop_market": e.get("prop_market"), "event_id": e.get("event_id"),
               "kalshi_line": m.get("kalshi_line") or m.get("strike"), "mode": "demo" if go else ("dry" if DRY else "shadow")}
        rec["bet_type"] = tt
        if track_only and not DRY:
            TG.track(TRACK, dict(rec, type=tt, contracts=max(1, int(stake * 100 // ask))))
        if go:
            try:
                rec["order"] = c.place_order(m["ticker"], side, max(1, int(stake * 100 // ask)), ask, meta=rec)   # 2026-10-31 kalfix: partial -> remainder rests 5 min
                placed += 1; skip.add(m["ticker"]); done_alerts.add(a["id"]); done_games.add(gk)
                if sk: sides.add(sk)
            except Exception as ex:
                rec["error"] = str(ex)[:200]
        if not DRY:
            log(rec)
        res.update(result="order" if go else ("would_order" if DRY and reason is None else "skip"),
                   reason=reason, ticker=m["ticker"], side=side, ask=ask, fair_c=fair_c, edge_c=edge_c, stake=stake)
        out.append(res); PF.log_match(dict(res, src="me_live" if live else "me_pregame"))
    if live:
        try:
            tmp = seen_p + ".tmp"; json.dump(sorted(seen, key=lambda x: int(x) if x.isdigit() else 0)[-2000:], open(tmp, "w")); os.replace(tmp, seen_p)
        except Exception:
            pass
    return out, placed, stats

def summarize(out):
    from collections import Counter
    return {"orders": sum(r["result"] == "order" for r in out), "would_order": sum(r["result"] == "would_order" for r in out),
            "skips": dict(Counter(r["reason"] for r in out if r["result"] == "skip"))}
 
 
 
 
 
