"""Polymarket US PAPER runner: Marv board -> map -> LOCAL simulated fill (best ask + taker fee) -> sqlite.
NO real orders. No production trading code exists here. PREPROD_ENABLED (flag) is off and keys don't exist."""
import json, os, sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pm_client import PMPublic
from mapper import map_entry
import sys; sys.path.insert(0, "/opt/marv-feed"); import pick_feed
HERE = os.path.dirname(os.path.abspath(__file__)); CT = ZoneInfo("America/Chicago")
CFG = json.load(open(os.path.join(HERE, "config.json")))
BOARD = os.environ.get("MARV_BOARD", "/opt/marv-bot/state/board.json")
DB = os.path.join(HERE, "data", "pm_paper.db")
THETA = 0.0695

def side_key(slug, pick):
    """(game, backed side): game from the Polymarket slug (prefix-league-t1-t2-date), side = picked team (line
    stripped) or over/under. Fill `pick` is the backed outcome (e.g. Colts short on aec-...-ten-ind = Colts)."""
    import re as _re
    m = _re.match(r"^[a-z]+-([a-z0-9.]+-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})", str(slug or ""))
    game = m.group(1) if m else str(slug)
    pk = str(pick or "").strip().lower()
    if pk.startswith(("over", "under")):
        return (game, pk.split()[0])
    pk = _re.sub(r"\s+(ml|moneyline)$", "", pk)   # 2026-10-23: 'Hornets ML' == 'Hornets +2.5' side
    return (game, _re.sub(r"\s*[+-]?\d+(\.\d+)?\s*$", "", pk).strip())

def fee(c, p): return round(THETA * c * p * (1 - p), 4)
def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True); c = sqlite3.connect(DB)
    c.execute("""create table if not exists fills(id integer primary key, ts text, date text, key text, pick text,
      slug text, side text, contracts real, price real, fee real, cost real, fair real, edge real, status text,
      pnl real, unique(date,key))""")
    c.execute("create table if not exists runs(ts text, entries int, mapped int, filled int, note text)")
    have = {r[1] for r in c.execute("pragma table_info(fills)")}
    for col, typ in (("me_type", "text"), ("me_alert_id", "integer"), ("lane", "text"), ("bovada_price", "real"),
                     ("bovada_line", "real"), ("bovada_ts", "text")):   # BOVADA-REF 2026-10-23   # 2026-10-16 March_edge alert-type tag
        if col not in have: c.execute(f"alter table fills add column {col} {typ}")
    return c
def me_entries():
    """March_edge SENT alerts -> entries (no props: Polymarket US has no player props)."""
    sys.path.insert(0, "/opt/kalshi-demo"); import me_alerts as MA
    out, skips = [], []
    for a in MA.load({}):
        e, why = MA.to_entry(a)
        if not why and e.get("market") == "prop": why = "prop (Kalshi only)"
        if not why:
            ok, w2 = MA.fresh_enough(e)
            if not ok: why = w2
        if why: skips.append((a["id"], e.get("me_type"), why)); continue
        out.append(e)
    return out, skips
PM_PROPS = os.path.join(HERE, "data", "pm_props.json")
def refresh_pm_props():
    """Kick the hourly prop build (marv-bot venv, niced, detached) from the 15-min timer; no new systemd unit."""
    import subprocess, time as _t
    lock = PM_PROPS + ".lock"
    # 2026-10-24 bug fix: a child spawned here was killed with the oneshot service cgroup when runner exited (~1 min),
    # so pm_props.json stopped updating after 3:29 PM CT Oct 23. Builds now run in pm-props-build.timer (own unit).
    if os.path.exists("/etc/systemd/system/pm-props-build.timer"): return
    try: age = _t.time() - os.path.getmtime(PM_PROPS)
    except OSError: age = 1e9
    if age < 55 * 60 or (os.path.exists(lock) and _t.time() - os.path.getmtime(lock) < 20 * 60): return
    open(lock, "w").write(str(os.getpid()))
    subprocess.Popen(["nice", "-n", "10", "/opt/marv-bot/.venv/bin/python", os.path.join(HERE, "pm_props_build.py")],
                     cwd=HERE, stdout=open(os.path.join(HERE, "data", "pm_props_build.log"), "w"), stderr=subprocess.STDOUT,
                     start_new_session=True)
def pm_prop_entries():
    """Entries built hourly by pm_props_build.py (marv-bot venv). Sanity: projection within 60-167% of the line;
    pre-filter on the listed Yes price (+2c) before hitting the order book (runner still needs +3c at the live ask)."""
    try: d = json.load(open(PM_PROPS))
    except Exception as ex: print("pm_props load err", ex); return []
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(d["built"])).total_seconds() / 60
    if age > 150: print("pm_props stale", round(age), "min"); return []
    out = []
    for e in d.get("entries", []):
        line, proj = float(e["line"]), float(e["proj"])
        if line <= 0 or not (0.60 <= proj / line <= 1.67): continue
        y = e.get("yes_px")
        if y is not None and y == y:
            px = y if e["side"] == "Over" else 1 - y
            if float(e["p"]) - px < 0.03: continue
        out.append(dict(e, _pre=float(e["p"]) - (px if y is not None and y == y else 0.5)))
    out.sort(key=lambda e: -e["_pre"])   # best threshold per player/stat/side first (side guard keeps one)
    print("pm props:", len(d.get("entries", [])), "rows,", len(out), "pass sanity/prefilter", d.get("stats"))
    return out
def map_prop(e, cl):
    """Exact Polymarket prop market from the build (slug + Yes/No side); live order-book price + stale guard."""
    from mapper import _price
    px, state = _price(cl, e["pm_slug"], e["pm_side"] == "long")
    return {"slug": e["pm_slug"], "side": e["pm_side"], "team": e.get("player"), "ask": px, "state": state,
            "market_line": e.get("line")}
def main():
    assert CFG.get("PAPER_ONLY") is True, "PAPER_ONLY must be true"
    assert CFG.get("PREPROD_ENABLED") is False or os.path.exists(os.path.join(HERE, "preprod.env")), "no preprod creds"
    con = db(); cl = PMPublic(); today = datetime.now(CT).date().isoformat(); now = datetime.now(CT).isoformat()
    kill = os.path.exists(os.path.join(HERE, "KILL"))
    SOURCE = os.environ.get("PM_SOURCE") or CFG.get("source", "marv"); DRY = os.environ.get("PM_DRY") == "1"; skips = []
    if SOURCE == "march_edge":
        entries, skips = me_entries()
        lanes = set(CFG.get("marv_lanes") or [])   # NBA-LANE 2026-10-23: Marv-direct lanes alongside March_edge
        if lanes:
            try:
                lane = [dict(e, lane="marv_" + str(e.get("sport"))) for e in pick_feed.entries()
                        if e.get("sport") in lanes and e.get("market") != "prop"]
                print("marv lanes:", sorted(lanes), "entries:", len(lane)); entries += lane
            except Exception as ex:
                print("marv lane err", ex)
    else:
        entries = [e for e in pick_feed.entries() if e.get("market") != "prop"]
        if not DRY:
            try: refresh_pm_props()
            except Exception as ex: print("pm props refresh err", ex)
        entries += pm_prop_entries()   # 2026-10-23 Marvin: Polymarket player props from Marv Predict NFL prop model
    mapped = filled = 0
    # 2026-10-16: at most ONE position per game+market: skip any slug we already hold open (existing fills left open)
    # 2026-10-24 bug fix: open AND settled fills count (same game re-bought day after day: 25 extra fills Oct 13-16).
    # Off: PM_DEDUPE_ALL=off | data/PM_DEDUPE_ALL_OFF (back to open-only).
    _all = os.environ.get("PM_DEDUPE_ALL", "").lower() not in ("off", "0", "false", "no") and not os.path.exists(os.path.join(HERE, "data", "PM_DEDUPE_ALL_OFF"))
    _stq = "" if _all else " where status='open'"
    held = {r[0] for r in con.execute("select slug from fills" + _stq)}
    # 2026-10-19 (Marvin, no double dipping unless hedging): ONE open position per game + backed side across ML /
    # spread / total slugs (slug guard alone let ML + spread on the same team through). Opposite side = hedge, allowed.
    held_sides = {side_key(r[0], r[1]) for r in con.execute("select slug, pick from fills" + _stq)}
    done_alerts = {r[0] for r in con.execute("select me_alert_id from fills where me_alert_id is not null")}
    prop_checks = 0
    for e in entries:
        if not e.get("p"): continue
        if e.get("pm_slug"):   # props are last: stop at the per-run fill cap; at most 150 order-book checks per run
            if filled >= CFG["max_fills_per_run"] or prop_checks >= int(CFG.get("max_prop_checks", 150)): break
            prop_checks += 1
        try: st = datetime.fromisoformat(str(e["start"]).replace("Z", "+00:00")) if e.get("start") else None
        except ValueError: st = None
        if st and st <= datetime.now(timezone.utc) and not e.get("live"): continue           # game started: no fills
        if e.get("me_alert_id") in done_alerts: skips.append((e.get("me_alert_id"), e.get("me_type"), "alert already filled")); continue
        m = map_prop(e, cl) if e.get("pm_slug") else map_entry(e, cl)
        if not m:
            _not_listed(today, e) if not DRY else None
            print("unmapped", e.get("market"), e.get("pick")); skips.append((e.get("me_alert_id"), e.get("me_type"), "no exact Polymarket game/market")); continue
        if m["slug"] in held:
            print("skip held", m["slug"], e.get("pick")); skips.append((e.get("me_alert_id"), e.get("me_type"), "already hold this game+market")); continue
        sk = side_key(m["slug"], e.get("pick"))
        if sk in held_sides:
            print("skip same side", sk); skips.append((e.get("me_alert_id"), e.get("me_type"), "double dip: already hold this game+side (hedges only)")); continue
        mapped += 1; p = m["ask"]; fair = float(e["p"]) + float(m.get("fair_adj") or 0)
        # 2026-10-26 (Marvin "What ever caps are most profitable"; calib_test cap60 best OOS): model win chance capped at 60%
        # before the edge check. Off: PM_WINCAP=off | data/PM_WINCAP_OFF. (pm_nba_live keeps its own cap+shrink.)
        if _sw("PM_WINCAP"): fair = min(fair, 0.60)
        if p is None or not (0 < p < 1) or m.get("state") != "MARKET_STATE_OPEN": print("no ask", m); continue
        import pm_type_gate as TG
        tt = TG.classify(e.get("key") or e.get("pick"), e.get("me_type"), e.get("market"), e.get("sport"))
        _ml = str(e.get("market", "")).lower() in ("h2h", "ml", "moneyline")
        # 2026-10-25 (Marvin "Do what is most profitable"): CFB ML underdogs MAY trade on Poly (9-1 +$1,611 n=10), tagged
        # lane=cfb_dog for review at 30 graded. Off: PM_CFB_DOG_TRADE=off | data/PM_CFB_DOG_TRADE_OFF (card rule again).
        cfb_dog = _ml and tt == "ml:cfb" and p <= 0.50 and not e.get("live") and _sw("PM_CFB_DOG_TRADE")
        # 2026-10-23 Marv card rule (Marvin): no ML underdogs at +100 or longer (Polymarket ask <= 0.50)
        # 2026-10-25 (Marvin "optimize to max ROI"): NFL pregame ML dogs trade too (+$170 n=4, thin), lane=nfl_dog, bypassing
        # the card rule AND the ml:nfl pin_track; NFL favorites stay track-only (pin). Off: PM_NFL_DOG_TRADE=off | data/PM_NFL_DOG_TRADE_OFF
        nfl_dog = _ml and tt == "ml:nfl" and p <= 0.50 and not e.get("live") and _sw("PM_NFL_DOG_TRADE")
        _ta = TG.trade_all() and not TG.nba_hold(tt)   # 2026-10-26 PM_TRADE_ALL: card rule / CFB-fav track lifted (NBA ML held)
        if _ml and p <= 0.50 and not cfb_dog and not nfl_dog and not _ta:
            if not DRY: _shadow(today, "card", tt, e, m, p, fair, e.get("live"))
            skips.append((e.get("me_alert_id"), e.get("me_type"), "ML dog +100 or longer (card rule)")); continue
        if cfb_dog:
            e = dict(e, lane="cfb_dog")
        if nfl_dog:
            e = dict(e, lane="nfl_dog")
        # 2026-10-25 (Marvin): CFB ML favorites (ask > 0.50) track-only on Poly (7-5 -$97 -16% n=12): logged + graded as
        # type ml:cfb_fav, no fill. Off: PM_CFB_FAV_TRACK=off | data/PM_CFB_FAV_TRACK_OFF.
        if _ml and tt == "ml:cfb" and p > 0.50 and _sw("PM_CFB_FAV_TRACK") and not _ta:
            tt = "ml:cfb_fav"
        mult = float(e["stake"]) if e.get("stake") is not None else 1.0
        if e.get("src") is None and e.get("market") != "prop" and 0 < mult < 0.1:
            mult = 1.0   # 2026-10-23 Marvin: board rows carry Kelly fractions (0.01-0.02), not stake multipliers -> full $ base
        if mult <= 0:
            print("skip stake0", e.get("pick")); continue  # UF v2 stake 0 => skip
        # 2026-10-30 after-reset job (Marvin: best-ROI traded types get larger stakes): pregame NFL player props, Poly's best
        # traded lane with a real sample (n=73, +8.4% ROI) -> stake x1.25 ($50 -> $62.50); $2K/day cap unchanged.
        # Off: PM_ROI_BOOST=off | data/PM_ROI_BOOST_OFF
        if e.get("pm_slug") and str(e.get("key", "")).startswith("pmprop:nfl") and not e.get("live") and _sw("PM_ROI_BOOST"): mult *= 1.25
        dollars = CFG["max_stake_usd"] * mult
        # 2026-11-01 Marvin: calibration-tier sizing; old $ (max_stake_usd x mult) stays the upper bound. Off: CALIB_SIZING=off | data/CALIB_SIZING_OFF
        try:
            import importlib.util as _ilu   # load by path: importing pm_fill here would pull /opt/kalshi-demo/runner.py via sys.path
            if "calib_size" not in sys.modules:
                _sp = _ilu.spec_from_file_location("calib_size", "/opt/kalshi-demo/calib_size.py"); _md = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_md); sys.modules["calib_size"] = _md
            _cs, _cal = sys.modules["calib_size"].size("pm", e.get("lane") or "runner:", fair, p, me_type=e.get("me_type") or "marv", cap_usd=dollars, dry=DRY,
                                        extra={"key": e.get("key") or e.get("pick"), "slug": m.get("slug"), "pick": e.get("pick")})
            if _cs is not None: dollars = _cs
        except Exception as ex:
            print("calib_size error", str(ex)[:120])
        c = int(dollars // (p + THETA * p * (1 - p)))
        f = fee(c, p); edge = fair - p - THETA * p * (1 - p)
        band = (not e.get("live")) or 0.10 <= p <= 0.90       # live: no near-dead prices
        go = (not kill and band and edge * 100 >= CFG["min_edge_cents"] and filled < CFG["max_fills_per_run"] and c > 0)
        # 2026-10-26 (PM_TRADE_ALL volume guard): new paper cost per CT day capped at max_daily_cost_usd (default $2,000 =
        # ~18% of the $11.1K bankroll, ~40 fills). Over the cap -> no fill (logged as skip). Off: PM_DAILY_CAP=off | data/PM_DAILY_CAP_OFF
        if go and _sw("PM_DAILY_CAP"):
            _spent = con.execute("select coalesce(sum(cost),0) from fills where date=? and coalesce(lane,'') not like '%\\_prop\\_live' escape '\\'", (today,)).fetchone()[0]  # 2026-10-30: live props have own budget
            if _spent + c * p > float(CFG.get("max_daily_cost_usd", 2000)):
                go = False; skips.append((e.get("me_alert_id"), e.get("me_type"), f"daily paper cap ${CFG.get('max_daily_cost_usd', 2000)} reached"))
        # TYPE-GATE (2026-10-23): tt computed above (pm_type_gate.classify); only profitable types fill, others track-only
        if go and ((not TG.allowed(tt) and not nfl_dog) or tt == "ml:cfb_fav" or TG.force_track(tt, live=bool(e.get("live")))):   # 2026-11-02 force_track (PM_FORCE_TRACK)
            go = False; skips.append((e.get("me_alert_id"), e.get("me_type"), f"track-only: {tt}"))
            if not DRY:
                TG.track(os.path.join(HERE, "data", "track_only.jsonl"), {"date": today, "type": tt, "key": e.get("key") or e.get("pick"), "pick": e.get("pick"),
                         "slug": m["slug"], "side": m["side"], "price": p, "contracts": c, "fee": f, "cost": round(c * p + f, 4), "fair": fair, "edge": edge,
                         "sport": e.get("sport"), "market": e.get("market")}, dedupe=("type", "key", "slug"))
            print("TRACK-ONLY", tt, e.get("pick"), m["slug"], p)
        elif not go:
            if not DRY and not kill and edge * 100 < CFG["min_edge_cents"]: _shadow(today, "edge", tt, e, m, p, fair, e.get("live"))
            skips.append((e.get("me_alert_id"), e.get("me_type"), "kill" if kill else "live price outside 10-90c" if not band
                          else f"edge {round(edge*100,1)}c < {CFG['min_edge_cents']}c at Polymarket price" if edge * 100 < CFG["min_edge_cents"] else "max fills / 0 contracts"))
        elif DRY:
            skips.append((e.get("me_alert_id"), e.get("me_type"), "WOULD FILL (dry)")); go = False
            filled += 1; held.add(m["slug"]); held_sides.add(sk)   # dry mirrors live guards/caps
        print({"pick": e.get("pick"), **m, "fair": fair, "edge_c": round(edge * 100, 2), "stake_mult": mult, "dollars": round(dollars, 2), "contracts": c, "paper_fill": go})
        if go:
            sys.path.insert(0, "/opt/kalshi-demo"); import bovada_px as BPX
            bx = BPX.lookup(e.get("sport"), e.get("market"), e.get("pick"), e.get("home"), e.get("away"))
            cur = con.execute("insert or ignore into fills(ts,date,key,pick,slug,side,contracts,price,fee,cost,fair,edge,status,me_type,me_alert_id)"
                " values(?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?)", (now, today, e.get("key") or e.get("pick"), e.get("pick"),
                m["slug"], m["side"], c, p, f, round(c * p + f, 4), fair, edge, e.get("me_type") or "marv", e.get("me_alert_id")))
            if cur.rowcount:
                con.execute("update fills set lane=?, bovada_price=?, bovada_line=?, bovada_ts=? where id=?",
                            (e.get("lane") or f"runner:{tt}:{'live' if e.get('live') else 'pre'}", bx.get("bovada_price"), bx.get("bovada_line"), bx.get("bovada_ts"), cur.lastrowid))
            filled += cur.rowcount; held.add(m["slug"]); held_sides.add(sk)
    from collections import Counter
    print("skips:", dict(Counter(w for _, _, w in skips)))
    if not DRY: _save_not_listed(today)
    if DRY:
        print(f"DRY done: source={SOURCE} entries={len(entries)} mapped={mapped}"); return
    con.execute("insert into runs values(?,?,?,?,?)", (now, len(entries), mapped, filled, ("KILL " if kill else "") + SOURCE))
    con.commit(); print(f"done: source={SOURCE} entries={len(entries)} mapped={mapped} paper_fills={filled} kill={kill}")
def _shadow(today, kind, tt, e, m, p, fair, live):
    """2026-10-25 (Marvin "paper track all sports"): log a would-be bet that was SKIPPED (card rule / edge below min) to
    track_only.jsonl so track_grade.py settles it and every lane gets an ROI. Flat $ stake, no fill. Off: PM_SHADOW_ALL=off | data/PM_SHADOW_ALL_OFF"""
    if not _sw("PM_SHADOW_ALL") or not m or p is None or not (0 < p < 1): return
    import pm_type_gate as TG
    c = int(CFG["max_stake_usd"] // (p + THETA * p * (1 - p)))
    if c <= 0: return
    f = fee(c, p); ml = str(e.get("market", "")).lower() in ("h2h", "ml", "moneyline")
    lane = f"shadow_{kind}:{tt}:{'live' if live else 'pre'}" + ((":dog" if p <= 0.50 else ":fav") if ml else "")
    TG.track(os.path.join(HERE, "data", "track_only.jsonl"), {"date": today, "type": lane, "key": e.get("key") or e.get("pick"), "pick": e.get("pick"),
             "slug": m["slug"], "side": m["side"], "price": p, "contracts": c, "fee": f, "cost": round(c * p + f, 4), "fair": fair,
             "edge": fair - p - THETA * p * (1 - p), "sport": e.get("sport"), "market": e.get("market"), "shadow": kind}, dedupe=("type", "key", "slug"))
_NOT_LISTED = {}
def _not_listed(today, e):
    k = f"{e.get('sport')}:{e.get('market')}:{'live' if e.get('live') else 'pre'}"; _NOT_LISTED[k] = _NOT_LISTED.get(k, 0) + 1
def _save_not_listed(today):
    if not _sw("PM_SHADOW_ALL") or not _NOT_LISTED: return
    pth = os.path.join(HERE, "data", "not_listed.json")
    try: d = json.load(open(pth))
    except Exception: d = {}
    d[today] = dict(_NOT_LISTED)   # latest run's unmapped counts for today (no exact Polymarket game/market)
    tmp = pth + ".tmp"; json.dump(d, open(tmp, "w"), indent=1); os.replace(tmp, pth)
def _sw(name):
    return os.environ.get(name, "on").lower() not in ("off", "0", "false", "no") and not os.path.exists(os.path.join(HERE, "data", name + "_OFF"))
if __name__ == "__main__": main()
 
 
 
 
