#!/usr/bin/env python3
"""2026-10-25 (Marvin "paper track all sports that kal/poly bets"): March_edge SENT alerts -> Polymarket price at alert
time -> TRACK-ONLY rows in data/track_only.jsonl (graded by track_grade.py). NEVER places a fill (no DB writes at all).
Lane = me_track:<type>:<sport>:<pre|live>:<fav|dog|over|under|spread>; one row per lane + game + side.
Unlisted alerts are counted in data/not_listed_me.json. Off: PM_ME_TRACK=off | data/PM_ME_TRACK_OFF. Dry: PM_DRY=1."""
import json, os, sys
from datetime import datetime
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import runner as RN, pm_type_gate as TG
from mapper import map_entry, LEAGUE
# 2026-10-30 after-reset job (Marvin "cover every sport each venue lists"): La Liga / Serie A / Bundesliga (March_edge
# esp.1 / ita.1 / ger.1 -> Polymarket US lal / sea / bun) are NEW on Poly -> TRACK-ONLY (logged + graded, never
# paper-filled, even with PM_TRADE_ALL) until the Nov 7 by-sport review. Ligue 1 is not listed on Polymarket US.
# (Soccer 3-way ML does not map on Poly for any league; totals do.) Off: PM_NEW_SOCCER_TRACK=off | data/PM_NEW_SOCCER_TRACK_OFF
NEW_SOCCER = {"esp.1": "lal", "ita.1": "sea", "ger.1": "bun"}
for _k, _v in NEW_SOCCER.items(): LEAGUE.setdefault(_k, _v)
from pm_client import PMPublic
DATA = os.path.join(HERE, "data"); DRY = os.environ.get("PM_DRY") == "1"
SEEN = os.path.join(DATA, "me_track_seen.json")
def side_tag(e, p):
    m = str(e.get("market", "")).lower(); pk = str(e.get("pick", "")).lower()
    if m in ("h2h", "ml", "moneyline"): return "dog" if p <= 0.50 else "fav"
    if m == "total": return "under" if "under" in pk else "over"
    return m or "other"
def main():
    if not RN._sw("PM_ME_TRACK"): print("PM_ME_TRACK off"); return
    try: seen = set(json.load(open(SEEN)))
    except Exception: seen = set()
    entries, skips = RN.me_entries(); cl = PMPublic(); today = datetime.now(RN.CT).date().isoformat()
    try: nl = json.load(open(os.path.join(DATA, "not_listed_me.json")))
    except Exception: nl = {}
    out = {"logged": 0, "not_listed": 0, "seen": 0, "no_price": 0}
    for e in entries:
        if e.get("sport") in ("mens-college-basketball", "ncaam", "ncaamb", "cbb"): e = dict(e, sport="ncaab")   # 2026-10-25 CBB key
        if e.get("sport") == "ncaab" and not RN._sw("PM_CBB_TRACK"): continue   # off: PM_CBB_TRACK=off | data/PM_CBB_TRACK_OFF
        if e.get("sport") in NEW_SOCCER and not RN._sw("PM_NEW_SOCCER_TRACK"): continue
        aid = str(e.get("me_alert_id"))
        if aid in seen: out["seen"] += 1; continue
        seen.add(aid)
        try: m = map_entry(e, cl)
        except Exception: m = None
        live = "live" if e.get("live") else "pre"
        if not m:
            k = f"{e.get('sport')}:{e.get('market')}:{live}"; day = nl.setdefault(today, {}); ids = day.setdefault(k, [])
            if aid not in ids: ids.append(aid)
            out["not_listed"] += 1; continue
        p = m.get("ask")
        if p is None or not (0 < p < 1) or m.get("state") != "MARKET_STATE_OPEN": out["no_price"] += 1; continue
        c = int(RN.CFG["max_stake_usd"] // (p + RN.THETA * p * (1 - p)))
        if c <= 0: continue
        f = RN.fee(c, p); fair = float(e.get("p") or 0)
        lane = f"me_track:{e.get('me_type')}:{e.get('sport')}:{live}:{side_tag(e, p)}"
        rec = {"date": today, "type": lane, "key": f"{m['slug']}:{e.get('pick')}", "pick": e.get("pick"), "slug": m["slug"], "side": m["side"],
               "price": p, "contracts": c, "fee": f, "cost": round(c * p + f, 4), "fair": fair, "edge": fair - p - RN.THETA * p * (1 - p),
               "sport": e.get("sport"), "market": e.get("market"), "me_alert_id": e.get("me_alert_id"), "alert_age_s": e.get("age_s"), "src": "march_edge"}
        # 2026-10-26 (Marvin "Let it trade"): paper fill via pm_fill (all runner guards). Off: PM_FILL_ME_TRACK=off | data/PM_FILL_ME_TRACK_OFF
        try:
            if e.get("sport") in NEW_SOCCER: raise StopIteration   # new Poly league: track-only, no paper fill
            import pm_fill as PFL
            ok, why = PFL.fill("ME_TRACK", dict(rec, key=f"me:{aid}"), lane, me_type=e.get("me_type") or "march_edge", dry=DRY)
            out["fill:" + why.split(" ")[0]] = out.get("fill:" + why.split(" ")[0], 0) + 1
        except StopIteration:
            why = "track-only new league"; out["track_new_league"] = out.get("track_new_league", 0) + 1
        except Exception as ex:
            why = f"fill error {str(ex)[:80]}"
        if DRY: print("DRY", lane, e.get("pick"), m["slug"], p, "|", why); out["logged"] += 1; continue
        TG.track(os.path.join(DATA, "track_only.jsonl"), rec, dedupe=("type", "key", "slug")); out["logged"] += 1
    if not DRY:
        for pth, obj in ((SEEN, sorted(seen)[-5000:]), (os.path.join(DATA, "not_listed_me.json"), nl)):
            tmp = pth + ".tmp"; json.dump(obj, open(tmp, "w"), indent=1); os.replace(tmp, pth)
    print("me_track:", out, "skipped(stale/other):", len(skips))
def marv_cbb():
    """2026-10-25: Marv Predict CBB/WCBB board picks -> TRACK-ONLY rows (lane marv_<cbb|wcbb>:<ml|spread|total>:<side>), one per
    game+side, never fills. Off: PM_MARV_CBB_TRACK=off | data/PM_MARV_CBB_TRACK_OFF. MARV_CBB_BOARDS overrides paths (tests)."""
    if not RN._sw("PM_MARV_CBB_TRACK"): return
    import mapper as MP
    MP.LEAGUE.setdefault("wncaab", "wcbb")          # runtime only (mapper.py has others' uncommitted edits)
    sp = os.path.join(DATA, "marv_cbb_seen.json")
    try: seen = set(json.load(open(sp)))
    except Exception: seen = set()
    cl = PMPublic(); today = datetime.now(RN.CT).date().isoformat(); out = {"logged": 0, "not_listed": 0, "seen": 0, "no_price": 0}
    for b in os.environ.get("MARV_CBB_BOARDS", "/opt/marv-feed/data/cbb/board_cbb.json,/opt/marv-feed/data/wcbb/board_cbb.json").split(","):
        try: ents = json.load(open(b)).get("entries") or []
        except Exception: continue
        for e in ents:
            k = str(e.get("key"))
            if k in seen: out["seen"] += 1; continue
            try: m = map_entry(dict(e), cl)
            except Exception: m = None
            if not m: out["not_listed"] += 1; continue          # retried next run (not marked seen)
            p = m.get("ask")
            if p is None or not (0.10 <= p <= 0.90) or m.get("state") != "MARKET_STATE_OPEN": out["no_price"] += 1; continue
            c = int(RN.CFG["max_stake_usd"] // (p + RN.THETA * p * (1 - p)))
            if c <= 0: continue
            mk = {"h2h": "ml"}.get(e.get("market"), e.get("market")); sd = ("dog" if p <= 0.5 else "fav") if mk == "ml" else e.get("side")
            f = RN.fee(c, p); fair = float(e.get("p") or 0)
            rec = {"date": today, "type": f"{'marv_wcbb' if e.get('sport') == 'wncaab' else 'marv_cbb'}:{mk}:{sd}", "key": k, "pick": e.get("pick"),
                   "slug": m["slug"], "side": m["side"], "price": p, "contracts": c, "fee": f, "cost": round(c * p + f, 4), "fair": fair,
                   "edge": fair - p - RN.THETA * p * (1 - p), "sport": e.get("sport"), "market": e.get("market"), "src": e.get("src")}
            seen.add(k)
            # 2026-10-26 (Marvin "Let it trade"): paper fill via pm_fill. Off: PM_FILL_MARV_CBB=off | data/PM_FILL_MARV_CBB_OFF
            try:
                import pm_fill as PFL
                ok, why = PFL.fill("MARV_CBB", dict(rec, key=f"marvcbb:{k}"), rec["type"], dry=DRY)
                out["fill:" + why.split(" ")[0]] = out.get("fill:" + why.split(" ")[0], 0) + 1
            except Exception as ex:
                why = f"fill error {str(ex)[:80]}"
            if DRY: print("DRY", rec["type"], rec["pick"], m["slug"], p, "|", why); out["logged"] += 1; continue
            TG.track(os.path.join(DATA, "track_only.jsonl"), rec, dedupe=("type", "key")); out["logged"] += 1
    if not DRY:
        tmp = sp + ".tmp"; json.dump(sorted(seen)[-5000:], open(tmp, "w")); os.replace(tmp, sp)
    print("marv_cbb:", out)
if __name__ == "__main__":
    main()
    try:
        if datetime.now().minute % 15 == 0 or os.environ.get("MARV_CBB_BOARDS"): marv_cbb()   # every 15 min (public API politeness)
    except Exception as x: print("marv_cbb error", str(x)[:120])
