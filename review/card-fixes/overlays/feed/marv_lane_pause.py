"""Pregame card LANE PAUSE (Marvin approved 2026-11-01, Sunday v3.2 review item 3).
Paused lanes are taken OFF the daily card (/card, /parlay, /betcard, card PNGs, Telegram) but still paper-tracked:
they move to card["track_paused"], never go into parlays, and are logged in daily_picks_log.json as kind
"paused_track" (graded by the normal grader like any row; NOT in the "straight ML/O-U" record, per-sport lines,
or v3.2 on_posted_card). Review: /opt/marv-feed/reports/v32_college_shadow.md (paused-lanes table).
Default lanes: NHL ML, NHL O/U, MLB O/U, CFB O/U (Marv; v3.2 CFB O/U is shadow-only anyway).
SWITCHES (checked on every call, no restart):
  all off  -> env MARV_LANE_PAUSE=off  or  touch /opt/marv-feed/MARV_LANE_PAUSE_OFF
  per lane -> env MARV_LANE_PAUSE_LANES="nhl:ml,nhl:total,mlb:total,cfb:total" (edit list)
              or touch /opt/marv-feed/MARV_LANE_PAUSE_OFF_<sport>_<market>  e.g. MARV_LANE_PAUSE_OFF_nhl_ml
Applied from marv_rules.apply() AFTER marv_type_gate (so it wraps it)."""
import os
from pathlib import Path

FEED = Path("/opt/marv-feed")
OFF_FILE = FEED / "MARV_LANE_PAUSE_OFF"
DEFAULT = "nhl:ml,nhl:total,mlb:total,cfb:total"
KIND = "paused_track"
_OFFS = ("off", "0", "false", "no")
_done = {}


def on():
    return os.environ.get("MARV_LANE_PAUSE", "on").strip().lower() not in _OFFS and not OFF_FILE.exists()


def lanes(_on=on):
    if not _on():
        return set()
    out = set()
    for t in os.environ.get("MARV_LANE_PAUSE_LANES", DEFAULT).split(","):
        t = t.strip().lower()
        if ":" in t:
            sp, mk = t.split(":", 1)
            if not (FEED / f"MARV_LANE_PAUSE_OFF_{sp}_{mk}").exists():
                out.add((sp, mk))
    return out


def _pair(x):
    if isinstance(x, dict):
        if x.get("master_extreme"):
            return None
        return (str(x.get("sport", "")).lower(), str(x.get("market", "")).lower())
    if x is None or getattr(x, "master_extreme", False):
        return None
    return (str(getattr(x, "sport", "") or "").lower(), str(getattr(x, "market", "") or "").lower())


def paused(x, L=None, _lanes=lanes):
    pair = _pair(x)
    if not pair or pair == ("", ""):
        return False
    return pair in (_lanes() if L is None else L)


def _rid(l):
    return f"{l['game']}:{l['market']}:{l['side']}:{l['line']}"


def split_card(card):
    """Move paused-lane legs out of ml/ou/spread (+ track_ml_fav) into track_paused; idempotent.
    Switch turned off -> legs come back at view time."""
    if not isinstance(card, dict) or "ml" not in card:
        return card
    L = lanes()
    card = dict(card)
    tp = list(card.get("track_paused") or [])
    back = [dict(x) for x in tp if not paused(x, L)]
    for x in back:
        x.pop("lane_paused", None)
        (card.setdefault("ou" if x.get("market") == "total" else ("spread" if x.get("market") == "spread" else "ml"), [])).append(x)
    tp = [x for x in tp if paused(x, L)]
    have = {(x.get("sport"), x.get("game"), x.get("market"), x.get("side")) for x in tp}
    for k in ("ml", "ou", "spread", "track_ml_fav"):
        lst = card.get(k) or []
        for x in lst:
            if paused(x, L) and (x.get("sport"), x.get("game"), x.get("market"), x.get("side")) not in have:
                tp.append(dict(x, lane_paused=True, tracking_only=True, track_gate="lane_pause"))
                have.add((x.get("sport"), x.get("game"), x.get("market"), x.get("side")))
        if k in card:
            card[k] = [x for x in lst if not paused(x, L)]
    card["track_paused"] = tp
    if isinstance(card.get("parlays"), dict):
        card["parlays"] = {s: [p for p in (ps or []) if not any(paused(l, L) for l in p.get("legs") or [])]
                           for s, ps in card["parlays"].items()}
        card["parlays"] = {s: ps for s, ps in card["parlays"].items() if ps}
    if "sports_with_legs" in card:
        live = {x.get("sport") for k in ("ml", "ou", "spread") for x in card.get(k) or []} | set(card.get("parlays") or {})
        card["sports_with_legs"] = [s for s in card["sports_with_legs"] if s in live]
    return card


def apply(D=None):
    if _done.get("on"):
        return
    if D is None:
        from marv import dailypicks as D
    o_build, o_pool, o_jload, o_log = D.build, D._pool_ok, D._jload, D.log_card

    def build(legs, uf, sports_checked, now):
        return split_card(o_build(legs, uf, sports_checked, now))

    def _pool_ok(leg):
        if paused(leg):
            return False
        return o_pool(leg)

    def _jload(path, default):
        r = o_jload(path, default)
        try:
            if str(path).endswith(str(D.CARD)) and isinstance(r, dict):
                r = split_card(r)
        except Exception:  # noqa: BLE001
            pass
        return r

    def log_card(state, card):
        n = o_log(state, card)  # paused legs are not in ml/ou/spread -> never logged as "straight"
        tp = (card or {}).get("track_paused") or []
        if not tp:
            return n
        try:
            path = D._dir(Path(state)) / D.LOG
            book = o_jload(path, [])
            have = {(r["date"], r.get("kind"), r.get("sport"), r["id"]) for r in book}
            for x in tp:
                rid = _rid(x)
                if (card["date"], KIND, x["sport"], rid) in have:
                    continue
                book.append({"date": card["date"], "kind": KIND, "sport": x["sport"], "id": rid, "price": x["price"],
                             "units": 0.5, "track_only": True, "track_gate": "lane_pause",
                             "track_note": "lane paused 2026-11-01 (Marvin): off card, paper-tracked",
                             "legs": [{k: x.get(k) for k in ("sport", "game", "home", "away", "start", "market", "side",
                                                            "line", "price", "tier", "league", "skey")} | {"result": None}],
                             "result": None, "profit": None})
                have.add((card["date"], KIND, x["sport"], rid))
                n += 1
            D._jsave(path, book)
        except Exception as e:  # noqa: BLE001
            print("marv_lane_pause: paper log failed", e)
        return n

    D.build, D._pool_ok, D._jload, D.log_card = build, _pool_ok, _jload, log_card
    _done["on"] = True


# Names below stay callable if marv.lane_pause is not on this bot tree (a hard checkout
# of an older bot). When the shared module is present it is the only predicate.
_on_fallback, _lanes_fallback, _paused_fallback, _split_fallback = on, lanes, paused, split_card
try:
    from marv import lane_pause as _shared
    if getattr(_shared, "CONTRACT", None) != 1:
        raise ImportError("lane_pause contract")
    on, lanes, paused, split_card = _shared.on, _shared.lanes, _shared.paused, _shared.split_card
except ImportError:
    _shared = None


def apply_betcard():
    """Filter model, tag, gap, and select when this bot tree does not already.

    The shared betcard functions set _lane_pause and are the direct path. This wrap
    covers an older betcard.py that a bot-tree checkout can restore. Tracking rows
    go through the shared buffer when that module is present, and through betcard.log
    otherwise. Daily-card apply() above is unchanged.
    """
    from marv import betcard as B
    if all(getattr(fn, "_lane_pause", False) for fn in (B.candidates, B.tag_candidates, B.gap_candidates, B.select)):
        return
    c0, t0, g0, s0, log0 = B.candidates, B.tag_candidates, B.gap_candidates, B.select, B.log
    local = []

    def _filter(rows, source):
        if _shared is not None:
            return _shared.partition(list(rows), source)
        keep = []
        for row in rows:
            if _paused_fallback(row):
                local.append(row)
            else:
                keep.append(row)
        return keep

    def candidates(sport, preds, projections=None):
        return _filter(c0(sport, preds, projections), "model")

    def tag_candidates(sport, slate, tags):
        return _filter(t0(sport, slate, tags), "tag")

    def gap_candidates(gaps):
        return _filter(g0(gaps), "gap")

    def select(cands, *a, **k):
        return s0(_filter(cands, "select"), *a, **k)

    def log(state, bets, when):
        result = log0(state, bets, when)
        day = when.date().isoformat() if hasattr(when, "date") else str(when)
        if _shared is not None:
            _shared.persist_tracking(state, day)
        return result

    for fn in (candidates, tag_candidates, gap_candidates, select):
        fn._lane_pause = True
    B.candidates, B.tag_candidates, B.gap_candidates, B.select, B.log = (
        candidates, tag_candidates, gap_candidates, select, log)
