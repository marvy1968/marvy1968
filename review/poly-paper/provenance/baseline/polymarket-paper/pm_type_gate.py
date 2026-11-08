"""Bet-type gate (2026-10-23, Marvin: "only trade the most profitable bets").
A bet type (sport x market, or the March_edge alert type) places orders only if it is on the TRADE allowlist in
type_gate.json (types that were profitable: settled P&L > 0 and ROI > 0). Every other type -- losers, and types
with no settled bets yet (new sports, new markets) -- is TRACK-ONLY: still evaluated with all normal guards,
logged as a would-have-bet with price/stake, graded by track_grade.py, but no order/fill is placed.
OFF SWITCH (instant revert to trading every type): create the file TYPE_GATE_OFF in this repo dir,
or set env TYPE_GATE=off, or set "enabled": false in type_gate.json."""
import json, os, re
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_P = os.path.join(HERE, "type_gate.json")
OFF_P = os.path.join(HERE, "TYPE_GATE_OFF")
CT = ZoneInfo("America/Chicago")
_MK = {"h2h": "ml", "ml": "ml", "moneyline": "ml", "total": "total", "spread": "spread", "ats": "spread",
       "team_total": "team_total", "tt": "team_total", "prop": "prop"}
_SPORTS = {"nfl", "cfb", "nba", "nhl", "mlb", "wnba", "soccer", "ncaab", "ncaaw", "cbb", "euroleague", "mls", "epl"}


def cfg():
    try:
        return json.load(open(CFG_P))
    except Exception:
        return {"enabled": False}   # missing/broken config never blocks trading


def active():
    return (os.environ.get("TYPE_GATE", "").lower() not in ("off", "0", "false")
            and not os.path.exists(OFF_P) and bool(cfg().get("enabled", False)))


def classify(key=None, me_type=None, market=None, sport=None):
    """Type tag matching the settled-ledger breakdown: March_edge alert type (value_live, team_trend, ...) or
    '<market>:<sport>' for Marv picks (ml:cfb, ml:nfl, master_total:nfl, prop:nfl, total:mlb, ml:nba ...)."""
    if me_type and me_type not in ("marv",):
        return str(me_type)
    k = str(key or "").lower(); p = k.split(":")
    sp = str(sport or "").lower() or None
    if k.startswith(("pmprop:", "prop:")) or str(market or "").lower() == "prop":
        return f"prop:{sp or 'nfl'}"
    if k.startswith("live:"):
        return "legacy_prop"
    if k.startswith("master:") and len(p) >= 3:
        return f"master_{_MK.get(p[1], p[1])}:{p[2]}"
    if p and p[0] in _MK and len(p) >= 2:
        return f"{_MK[p[0]]}:{p[1]}"
    if p and p[0] in _SPORTS and len(p) >= 3 and p[2] in _MK:
        return f"{_MK[p[2]]}:{p[0]}"
    mk = _MK.get(str(market or "").lower(), str(market or "other").lower())
    return f"{mk}:{sp or 'unknown'}"


def never(t):
    """2026-10-24 (Marvin): types that never fill even with the gate off (h2h_cfb = CFB H2H model test)."""
    return t in set(cfg().get("never_trade", ["h2h_cfb"]))


TRADE_ALL_OFF_P = os.path.join(HERE, "data", "PM_TRADE_ALL_OFF")


def trade_all():
    """2026-10-26 3:33 PM ET Marvin ("Let it trade. Try everything so we will be ready by launch day"): every lane
    paper-fills (allowlist, never_trade, pin_track ml:nfl, CFB favorites, ML-dog card rule all lifted) except NBA ML
    (held for the Oct 27 NBA review). Off (previous track-only set): PM_TRADE_ALL=off | data/PM_TRADE_ALL_OFF."""
    return os.environ.get("PM_TRADE_ALL", "on").lower() not in ("off", "0", "false", "no") and not os.path.exists(TRADE_ALL_OFF_P)


def nba_hold(t):
    """NBA ML lanes stay track-only until the Oct 27 NBA review."""
    s = str(t or "").lower()
    return s.startswith("ml:nba")


FORCE_TRACK_OFF_P = os.path.join(HERE, "data", "PM_FORCE_TRACK_OFF")


def force_track(t, live=None):
    """2026-11-02 Marvin: forced track-only lanes (type_gate.json force_track, e.g. prop:nfl:pre = NFL pregame props,
    -4.1% over 203 bets). Beats PM_TRADE_ALL; still logged to track_only.jsonl + graded. Entries match '<type>' or
    '<type>:pre|live'. Off: env PM_FORCE_TRACK=off | data/PM_FORCE_TRACK_OFF."""
    if os.environ.get("PM_FORCE_TRACK", "on").lower() in ("off", "0", "false", "no") or os.path.exists(FORCE_TRACK_OFF_P):
        return False
    L = set(cfg().get("force_track", []))
    return t in L or (live is not None and f"{t}:{'live' if live else 'pre'}" in L)


def allowed(t):
    """True = place the order. Gate off => everything allowed (pre-gate behaviour), except never_trade types."""
    if trade_all() and not nba_hold(t):
        return True
    if never(t):
        return False
    if pinned_track(t):
        return False
    if not active():
        return True
    return t in set(cfg().get("trade", []))


def track(path, rec, dedupe=("type", "key", "ticker")):
    """Append a would-have-bet record (once per type/key/market) for track_grade.py."""
    rec = dict(rec, ts=rec.get("ts") or datetime.now(CT).isoformat(), mode="track")
    sig = tuple(str(rec.get(k)) for k in dedupe)
    try:
        for ln in open(path):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if tuple(str(r.get(k)) for k in dedupe) == sig and r.get("date") == rec.get("date"):
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return True


PIN_TRACK_OFF_P = os.path.join(HERE, "PIN_TRACK_OFF")


def pinned_track(t):
    """2026-10-24 Marvin: manual track-only pins (type_gate.json pin_track, e.g. ml:nfl): never paper-filled, still
    tracked, even if the allowlist or gate changes. Off: touch PIN_TRACK_OFF or env PIN_TRACK=off."""
    if os.environ.get("PIN_TRACK", "").lower() in ("off", "0", "false") or os.path.exists(PIN_TRACK_OFF_P):
        return False
    return t in set(cfg().get("pin_track", []))
