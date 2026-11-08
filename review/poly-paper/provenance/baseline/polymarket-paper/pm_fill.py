"""Shared Polymarket PAPER fill for lanes that used to be track-only (2026-10-26 3:37 PM ET, Marvin "Let it trade. Try
everything so we will be ready by launch day"): pm_me_track (March_edge copy), pm_nba_live (NBA live props), marv_cbb.
Paper only (pm_paper.db fills; no real orders). Guards, all shared with runner.py:
  PM_TRADE_ALL on + per-lane switch; KILL file; win chance capped at 60% (PM_WINCAP) -> edge >= min_edge_cents after fee;
  price 10-90c; one bet per game+side and per slug across ALL lanes (runner.side_key); one prop per player per game
  (group prefix); PM_DAILY_CAP ($2,000 new paper cost per CT day); NBA moneyline blocked until the Oct 27 review.
Per-lane off: PM_FILL_<LANE>=off | data/PM_FILL_<LANE>_OFF   (LANE = ME_TRACK, NBA_LIVE, MARV_CBB)."""
import os
from datetime import datetime
import runner as RN, pm_type_gate as TG

HERE = os.path.dirname(os.path.abspath(__file__))


def _CS():
    """2026-11-01: shared calibration-tier sizing module (/opt/kalshi-demo/calib_size.py), loaded by path (no sys.path shadowing)."""
    import importlib.util, sys
    if "calib_size" not in sys.modules:
        sp = importlib.util.spec_from_file_location("calib_size", "/opt/kalshi-demo/calib_size.py")
        mod = importlib.util.module_from_spec(sp); sp.loader.exec_module(mod); sys.modules["calib_size"] = mod
    return sys.modules["calib_size"]


def lane_on(name):
    return RN._sw("PM_FILL_" + name) and TG.trade_all()


def fill(name, rec, lane, me_type="marv", group_prefix=None, dry=False, gate=None):
    """rec: slug, side, pick, price, fair, key, contracts?, me_alert_id?, sport?, market?. Returns (filled, reason)."""
    if not lane_on(name): return False, "lane off"
    if os.path.exists(os.path.join(HERE, "KILL")): return False, "kill"
    sp, mk = str(rec.get("sport") or "").lower(), str(rec.get("market") or "").lower()
    if sp == "nba" and mk in ("h2h", "ml", "moneyline"): return False, "NBA ML held (Oct 27 review)"
    p = float(rec["price"]); fair = float(rec.get("fair") or 0)
    if not 0.10 <= p <= 0.90: return False, "price outside 10-90c"
    # 2026-10-30 (Marvin "mostly live prop bets ... 62% edge"): live prop lanes pass gate={cap,p,min_edge}:
    # fill if win prob (capped) >= p with edge>0 after fee, OR edge >= min_edge. Other lanes unchanged (60% cap, min_edge_cents).
    if gate:
        fair = min(fair, gate["cap"]); edge = fair - p - RN.THETA * p * (1 - p)
        if not ((fair >= gate["p"] and edge > 0) or edge >= gate["min_edge"]): return False, f"gate: p {fair:.2f} edge {edge*100:.1f}c"
    else:
        if RN._sw("PM_WINCAP"): fair = min(fair, 0.60)
        edge = fair - p - RN.THETA * p * (1 - p)
        if edge * 100 < RN.CFG["min_edge_cents"]: return False, f"edge {edge*100:.1f}c < {RN.CFG['min_edge_cents']}c (60% cap)"
    # 2026-11-01 Marvin: calibration-tier sizing (max_stake_usd stays the upper bound). Off: CALIB_SIZING=off | data/CALIB_SIZING_OFF
    dollars = float(RN.CFG["max_stake_usd"])
    try:
        _cs, _cal = _CS().size("pm", lane, fair, p, me_type=me_type, cap_usd=dollars, dry=dry, extra={"key": rec.get("key"), "slug": rec.get("slug"), "pick": rec.get("pick")})
        if _cs is not None:
            if _cs <= 0: return False, _cal.get("calib_skip") or "calib stake 0"
            dollars = _cs
    except Exception as ex:
        print("calib_size error", str(ex)[:120])
    c = int(dollars // (p + RN.THETA * p * (1 - p)))
    if c <= 0: return False, "0 contracts"
    f = RN.fee(c, p); cost = round(c * p + f, 4)
    con = RN.db(); today = datetime.now(RN.CT).date().isoformat()
    try:
        rows = con.execute("select slug, pick, key from fills").fetchall()
        if rec["slug"] in {r[0] for r in rows}: return False, "already hold this market"
        if RN.side_key(rec["slug"], rec.get("pick")) in {RN.side_key(r[0], r[1]) for r in rows}: return False, "double dip: game+side held"
        if group_prefix and any(str(r[2]).startswith(group_prefix) for r in rows): return False, "one prop per player per game"
        if RN._sw("PM_DAILY_CAP"):
            # 2026-10-30 (Marvin): *_prop_live lanes get their own $1,500/day (max_daily_live_props_usd), not counted in the $2,000.
            if str(lane).endswith("_prop_live"):
                spent = con.execute("select coalesce(sum(cost),0) from fills where date=? and lane like '%\\_prop\\_live' escape '\\'", (today,)).fetchone()[0]
                if spent + cost > float(RN.CFG.get("max_daily_live_props_usd", 1500)): return False, "live props daily paper budget reached"
            else:
                spent = con.execute("select coalesce(sum(cost),0) from fills where date=? and coalesce(lane,'') not like '%\\_prop\\_live' escape '\\'", (today,)).fetchone()[0]
                if spent + cost > float(RN.CFG.get("max_daily_cost_usd", 2000)): return False, "daily paper cap reached"
        if dry: return False, f"WOULD FILL {lane} {c}@{p} edge {edge*100:.1f}c"
        cur = con.execute("insert or ignore into fills(ts,date,key,pick,slug,side,contracts,price,fee,cost,fair,edge,status,me_type,me_alert_id,lane)"
                          " values(?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?)", (datetime.now(RN.CT).isoformat(), today, rec["key"], rec.get("pick"),
                          rec["slug"], rec["side"], c, p, f, cost, fair, edge, me_type, rec.get("me_alert_id"), lane))
        con.commit()
        return bool(cur.rowcount), "filled" if cur.rowcount else "dup key"
    finally:
        con.close()
