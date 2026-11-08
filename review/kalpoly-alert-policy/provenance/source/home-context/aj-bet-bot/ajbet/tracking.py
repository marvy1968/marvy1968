"""Round-2 tracking (Marvin 2026-10-02): live-alert log with a ~5-minute Bovada markout, and a Pinnacle
close snapshot ~5-10 minutes before kickoff for CLV on every alert type.

live_alert_log  one row per LIVE alert (sent or paper): price age at alert, ESPN possession/field position,
                Bovada + Pinnacle at alert time, and the Bovada price / Pinnacle no-vig ~5 min later
                (taken from the live refresher's cache: 0 extra credits; 'missed' if no fresh pull came).
close_snapshots one row per pregame paper bet: Pinnacle close (no-vig fair at the bet's line, Pinnacle line)
                and Bovada close, from ONE featured call per sport per kickoff window (budget 'close').
                CLV = close fair p x decimal(bet price) - 1 (same line); line_clv = points gained vs the close.
                For live bets the markout row doubles as the CLV reference (Pinnacle ~5 min later).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .db import today_local
from .markets import name_sim
from .oddsmath import american_to_decimal

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_alert_log (
  alert_id INTEGER PRIMARY KEY, ts REAL, event_id TEXT, league TEXT, strategy TEXT, market TEXT, selection TEXT,
  line REAL, sent INTEGER, price_age_s REAL, situation_json TEXT, score TEXT,
  bovada_line REAL, bovada_price REAL, pin_fair_p REAL, odds_event_id TEXT,
  due_ts REAL, m5_ts REAL, m5_bovada_line REAL, m5_bovada_price REAL, m5_pin_fair_p REAL, m5_status TEXT
);
CREATE TABLE IF NOT EXISTS close_snapshots (
  bet_id INTEGER PRIMARY KEY, alert_id INTEGER, ts REAL, strategy TEXT, market TEXT, selection TEXT,
  bet_line REAL, bet_price REAL, pin_line REAL, pin_price REAL, pin_fair_p REAL,
  bovada_line REAL, bovada_price REAL, clv REAL, line_clv REAL, minutes_before REAL, note TEXT
);
"""


def ensure(db) -> None:
    db.conn.executescript(SCHEMA)
    db.conn.commit()


def _iso(s: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _outcomes(ev: Dict[str, Any], book: str, market: str) -> List[Dict[str, Any]]:
    for b in (ev or {}).get("bookmakers") or []:
        if (b.get("key") or "").lower() == book:
            for m in b.get("markets") or []:
                if m.get("key") == market:
                    return list(m.get("outcomes") or [])
    return []


def _pick(outs: List[Dict[str, Any]], selection: str, aliases=None) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if not outs or not selection:
        return None, outs
    sel = str(selection)
    low = sel.split()[0].lower() if sel.split() else ""
    if low in ("over", "under"):
        hit = [o for o in outs if str(o.get("name", "")).lower() == low]
        return (hit[0] if hit else None), outs
    best, bs = None, 0.0
    for o in outs:
        s = name_sim(sel, str(o.get("name", "")), aliases or {})
        if s > bs:
            best, bs = o, s
    return (best if bs >= 0.6 else None), outs


def quote(ev: Optional[Dict[str, Any]], book: str, market: str, selection: str, point: Optional[float] = None,
          aliases=None) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(line, price, no-vig fair p) for `selection` at `book`. If several lines are listed, prefer `point`.
    Fair p is only computed for two-way markets with both sides at the same line."""
    mk = "totals" if market in ("alternate_totals",) else market
    outs = _outcomes(ev or {}, book, mk)
    if not outs:
        return None, None, None
    if point is not None and mk in ("totals", "spreads"):
        same = [o for o in outs if o.get("point") is not None and
                abs(abs(float(o["point"])) - abs(float(point))) < 1e-9]
        if same:
            outs = [o for o in outs if o.get("point") is None or abs(abs(float(o["point"])) - abs(float(point))) < 1e-9]
    o, allo = _pick(outs, selection, aliases)
    if o is None or o.get("price") is None:
        return None, None, None
    line = float(o["point"]) if o.get("point") is not None else None
    others = [x for x in allo if x is not o and x.get("price") is not None and
              (line is None or x.get("point") is None or abs(abs(float(x["point"])) - abs(line)) < 1e-9)]
    fair = None
    if len(others) == 1:
        a, b = 1 / american_to_decimal(float(o["price"])), 1 / american_to_decimal(float(others[0]["price"]))
        fair = a / (a + b) if a + b > 0 else None
    return line, float(o["price"]), fair


def line_gain(market: str, selection: str, bet_line: Optional[float], close_line: Optional[float]) -> Optional[float]:
    """Points the bet's line beats the close by (positive = better than the close)."""
    if bet_line is None or close_line is None:
        return None
    s = str(selection or "").lower()
    if "totals" in market or s.startswith(("over", "under")):
        return (close_line - bet_line) if s.startswith("over") else (bet_line - close_line)
    if market == "spreads":
        return bet_line - close_line
    return None


# ------------------------------------------------------------------ live alert log + 5-min markout
class LiveLog:
    def __init__(self, db, cfg: Dict[str, Any], leagues: Dict[str, Dict[str, Any]], cache_fn: Callable[[str], Any],
                 clock: Callable[[], float] = time.time, aliases=None):
        self.db, self.cfg, self.leagues, self.cache_fn, self.clock = db, cfg, leagues, cache_fn, clock
        self.aliases = aliases or {}
        c = (cfg.get("tracking") or {}).get("live_markout") or {}
        self.delay = float(c.get("delay_s", 300))
        self.window = float(c.get("max_late_s", 600))
        ensure(db)

    def record(self, alert_id: Optional[int], g, strategy: str, market: str, selection: str, line, sent: bool,
               odds_ts: Optional[float], ev: Optional[Dict[str, Any]], odds_event_id: Optional[str] = None) -> None:
        if not alert_id:
            return
        from .live_guard import situation_brief
        try:
            now = self.clock()
            bl, bp, _ = quote(ev, "bovada", market, selection, line, self.aliases) if ev else (None, None, None)
            _, _, pf = quote(ev, "pinnacle", market, selection, line, self.aliases) if ev else (None, None, None)
            score = f"{g.away.abbr} {g.away.score}-{g.home.score} {g.home.abbr} {g.detail}" if g is not None else ""
            self.db.conn.execute(
                "INSERT OR REPLACE INTO live_alert_log (alert_id, ts, event_id, league, strategy, market, selection, line,"
                " sent, price_age_s, situation_json, score, bovada_line, bovada_price, pin_fair_p, odds_event_id, due_ts,"
                " m5_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (alert_id, now, getattr(g, "event_id", None), getattr(g, "league", None), strategy, market, selection,
                 line, int(bool(sent)), None if odds_ts is None else round(now - float(odds_ts), 1),
                 json.dumps(situation_brief(g)), score, bl, bp, pf, odds_event_id or (ev or {}).get("id"),
                 now + self.delay, "pending"))
            self.db.conn.commit()
        except Exception:  # noqa: BLE001 - logging must never block an alert
            log.exception("live alert log failed")

    def mark_sent(self, alert_id: Optional[int]) -> None:
        if alert_id:
            self.db.conn.execute("UPDATE live_alert_log SET sent=1 WHERE alert_id=?", (alert_id,))
            self.db.conn.commit()

    def process(self) -> Dict[str, int]:
        """Fill due markouts from the live odds cache (no extra Odds API calls)."""
        out = {"filled": 0, "missed": 0}
        now = self.clock()
        rows = self.db.conn.execute(
            "SELECT * FROM live_alert_log WHERE m5_status='pending' AND due_ts<=?", (now,)).fetchall()
        for r in rows:
            r = dict(r)
            ok = (self.leagues.get(r["league"]) or {}).get("odds_key", "")
            hit = self.cache_fn(ok) if ok else None
            ev = None
            if hit and hit[0] >= r["due_ts"] - 60:
                ev = next((e for e in hit[1] or [] if e.get("id") == r["odds_event_id"]), None)
            if ev is not None:
                bl, bp, _ = quote(ev, "bovada", r["market"], r["selection"], r["line"], self.aliases)
                _, _, pf = quote(ev, "pinnacle", r["market"], r["selection"], r["line"], self.aliases)
                self.db.conn.execute("UPDATE live_alert_log SET m5_ts=?, m5_bovada_line=?, m5_bovada_price=?,"
                                     " m5_pin_fair_p=?, m5_status=? WHERE alert_id=?",
                                     (hit[0], bl, bp, pf, "ok" if bp is not None else "bovada_off", r["alert_id"]))
                out["filled"] += 1
                if pf is not None:          # live CLV reference: Pinnacle no-vig ~5 min after the alert
                    for b in self.db.conn.execute("SELECT id, price FROM paper_bets WHERE alert_id=? AND clv IS NULL",
                                                  (r["alert_id"],)).fetchall():
                        self.db.update_clv(b["id"], pf, pf * american_to_decimal(float(b["price"])) - 1)
            elif now > r["due_ts"] + self.window:
                self.db.conn.execute("UPDATE live_alert_log SET m5_status='missed' WHERE alert_id=?", (r["alert_id"],))
                out["missed"] += 1
        if rows:
            self.db.conn.commit()
        return out


# ------------------------------------------------------------------ Pinnacle close snapshot (CLV)
class Closer:
    """~5-10 min before kickoff: one featured odds call per sport covering every open pregame paper bet in the
    window (budget 'close' in odds_api.allocations), then a close row + paper_bets.close_fair_p/clv."""

    def __init__(self, db, cfg: Dict[str, Any], leagues: Dict[str, Dict[str, Any]], odds,
                 clock: Callable[[], float] = time.time, aliases=None):
        self.db, self.cfg, self.leagues, self.odds, self.clock = db, cfg, leagues, odds, clock
        self.aliases = aliases or {}
        c = (cfg.get("tracking") or {}).get("close") or {}
        self.enabled = bool(c.get("enabled", True))
        self.lo, self.hi = float(c.get("min_minutes", 5)) * 60, float(c.get("max_minutes", 10)) * 60
        self.props = bool(c.get("props", True))
        self.max_prop_events = int(c.get("max_prop_events_per_day", 8))
        ensure(db)

    def _pending(self, games: Dict[str, List[Any]]) -> Dict[str, List[Tuple[Dict[str, Any], Any]]]:
        now = self.clock()
        by_eid = {g.event_id: g for gs in games.values() for g in gs}
        out: Dict[str, List[Tuple[Dict[str, Any], Any]]] = {}
        rows = self.db.conn.execute(
            "SELECT p.* FROM paper_bets p LEFT JOIN close_snapshots c ON c.bet_id=p.id "
            "WHERE p.status='open' AND c.bet_id IS NULL").fetchall()
        for r in rows:
            r = dict(r)
            g = by_eid.get(r["event_id"])
            if g is None or g.state != "pre":
                continue
            st = _iso(g.start)
            if st is None or not (self.lo <= st - now <= self.hi):
                continue
            out.setdefault(g.league, []).append((r, g))
        return out

    def run(self, games: Dict[str, List[Any]]) -> Dict[str, Any]:
        from .markets import match_event
        from .oddsapi import BudgetExceeded
        res = {"snapshots": 0, "calls": 0, "skipped": []}
        if not self.enabled:
            return res
        for league, lst in self._pending(games).items():
            ok = (self.leagues.get(league) or {}).get("odds_key")
            if not ok:
                continue
            feat = [x for x in lst if not ((x[0].get("market") or "").startswith(("player_", "batter_", "pitcher_")) or "team_totals" in (x[0].get("market") or ""))]
            props = [x for x in lst if x not in feat]
            events: List[Dict[str, Any]] = []
            if feat:
                try:
                    events = self.odds.live_odds(ok, strategy="close", markets="h2h,spreads,totals") or []
                    res["calls"] += 1
                except BudgetExceeded as e:
                    res["skipped"].append(f"{ok} close: {e}")
                    events = []
                for r, g in feat:
                    ev = match_event(g.home.name, g.away.name, g.start, events, self.aliases) if events else None
                    self._snap(r, g, ev, "featured" if ev else "no close odds")
                    res["snapshots"] += 1
            if props and self.props:
                done = int(self.db.kv_get(f"close_props:{today_local()}")[1] or 0)
                by_ev: Dict[str, List[Tuple[Dict[str, Any], Any]]] = {}
                for r, g in props:
                    by_ev.setdefault(r.get("odds_event_id") or "", []).append((r, g))
                for oeid, items in by_ev.items():
                    if not oeid or done >= self.max_prop_events:
                        for r, g in items:
                            self._snap(r, g, None, "props close skipped (cap/no id)")
                        continue
                    mks = ",".join(sorted({r["market"] for r, _ in items}))
                    try:
                        pev = self.odds.event_odds(ok, oeid, mks, strategy="close")
                        res["calls"] += 1
                        done += 1
                    except BudgetExceeded as e:
                        res["skipped"].append(f"{ok} props close: {e}")
                        pev = None
                    for r, g in items:
                        self._snap(r, g, pev, "props" if pev else "no close odds")
                        res["snapshots"] += 1
                self.db.kv_set(f"close_props:{today_local()}", done)
        return res

    def _snap(self, r: Dict[str, Any], g, ev: Optional[Dict[str, Any]], note: str) -> None:
        mk, sel, line = r.get("market") or "", r.get("selection") or "", r.get("line")
        if r.get("player"):
            sel_q = f"{sel}"           # props: Over/Under; outcome description carries the player
        else:
            sel_q = sel
        pl = pp = pf = bl = bp = None
        if ev is not None:
            if r.get("player"):
                pl, pp, pf = self._prop_quote(ev, "pinnacle", mk, r["player"], sel, line)
                bl, bp, _ = self._prop_quote(ev, "bovada", mk, r["player"], sel, line)
                if pf is None:          # Pinnacle rarely hangs props: consensus no-vig at the same line
                    pf = self._prop_consensus(ev, mk, r["player"], sel, line)
                    note += "; consensus close" if pf is not None else ""
            else:
                pl, pp, pf = quote(ev, "pinnacle", mk, sel_q, line, self.aliases)
                bl, bp, _ = quote(ev, "bovada", mk, sel_q, line, self.aliases)
        same = pl is None or line is None or abs(abs(float(pl)) - abs(float(line))) < 1e-9
        clv = (pf * american_to_decimal(float(r["price"])) - 1) if (pf is not None and same and r.get("price")) else None
        lg = line_gain(mk, sel, line, pl)
        st = _iso(g.start)
        self.db.conn.execute(
            "INSERT OR REPLACE INTO close_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r.get("alert_id"), self.clock(), r.get("strategy"), mk, sel, line, r.get("price"), pl, pp, pf,
             bl, bp, clv, lg, None if st is None else round((st - self.clock()) / 60, 1), note))
        self.db.conn.commit()
        if clv is not None:
            self.db.update_clv(r["id"], pf, clv)

    @staticmethod
    def _prop_outs(ev, book, market, player, sel, line):
        outs = [o for o in _outcomes(ev, book, market)
                if str(o.get("description", "")).lower() == str(player).lower()]
        if line is not None:
            outs = [o for o in outs if o.get("point") is not None and abs(float(o["point"]) - float(line)) < 1e-9]
        return outs

    def _prop_quote(self, ev, book, market, player, sel, line):
        outs = self._prop_outs(ev, book, market, player, sel, line)
        me = [o for o in outs if str(o.get("name", "")).lower() == str(sel).lower()]
        if not me:
            return None, None, None
        o = me[0]
        other = [x for x in outs if x is not o]
        fair = None
        if len(other) == 1:
            a, b = 1 / american_to_decimal(float(o["price"])), 1 / american_to_decimal(float(other[0]["price"]))
            fair = a / (a + b)
        return (None if o.get("point") is None else float(o["point"])), float(o["price"]), fair

    def _prop_consensus(self, ev, market, player, sel, line):
        fs = []
        for b in ev.get("bookmakers") or []:
            _, _, f = self._prop_quote(ev, (b.get("key") or "").lower(), market, player, sel, line)
            if f is not None:
                fs.append(f)
        if len(fs) < 2:
            return None
        fs.sort()
        return fs[len(fs) // 2]
