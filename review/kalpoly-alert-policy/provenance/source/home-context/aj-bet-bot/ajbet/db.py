"""SQLite persistence: pregame snapshots, alerts (dedupe), paper bets, API usage."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import os as _os
from pathlib import Path as _Path


def _track_only():
    v = _os.environ.get("AJ_TRACK_ONLY", "line_swing,total_swing").strip()
    if v.lower() in ("off", "0", "false", "") or (_Path(__file__).resolve().parent.parent / "data" / "TRACK_ONLY_OFF").exists():
        return set()
    return {x.strip() for x in v.split(",") if x.strip()}

LOCAL_TZ = ZoneInfo("America/Chicago")   # day boundaries for daily caps (Marvin is in CT)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pregame (
  event_id TEXT PRIMARY KEY, league TEXT, captured_at TEXT, lines_json TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, dedupe_key TEXT UNIQUE,
  event_id TEXT, league TEXT, strategy TEXT, market TEXT, selection TEXT, line REAL,
  price REAL, book TEXT, fair_p REAL, market_p REAL, edge REAL, value INTEGER,
  trigger TEXT, message TEXT, sent INTEGER DEFAULT 0, details_json TEXT
);
CREATE TABLE IF NOT EXISTS paper_bets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER, ts TEXT, bet_date TEXT,
  event_id TEXT, league TEXT, strategy TEXT, market TEXT, selection TEXT, side TEXT,
  line REAL, price REAL, book TEXT, stake REAL, to_win REAL,
  status TEXT DEFAULT 'open', pnl REAL DEFAULT 0, settled_at TEXT, final_score TEXT
);
CREATE TABLE IF NOT EXISTS api_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, endpoint TEXT, sport TEXT,
  status INTEGER, cost REAL, remaining REAL, used REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS sport_calls (sport TEXT PRIMARY KEY, last_ts TEXT);
CREATE TABLE IF NOT EXISTS value_runs (day TEXT, sport TEXT, win TEXT, ts TEXT, PRIMARY KEY (day, sport, win));
CREATE TABLE IF NOT EXISTS value_props (day TEXT, event_id TEXT, ts TEXT, PRIMARY KEY (day, event_id));
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, ts TEXT, v TEXT);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, chat_id TEXT, text TEXT, html INTEGER,
  alert_ids TEXT, attempts INTEGER DEFAULT 0, status TEXT DEFAULT 'pending', done_ts TEXT
);
"""

# Columns added after v0.1 (applied with ALTER TABLE on existing DBs)
MIGRATIONS = {
    "api_usage": {"strategy": "TEXT DEFAULT 'live'", "key_label": "TEXT DEFAULT 'free'",
                  "ts_utc": "TEXT", "day_local": "TEXT"},
    "paper_bets": {"player": "TEXT", "ev": "REAL", "fair_p": "REAL", "model_p": "REAL",
                   "kelly_stake": "REAL", "close_fair_p": "REAL", "close_ts": "TEXT", "clv": "REAL",
                   "odds_event_id": "TEXT", "commence": "TEXT", "tag": "TEXT", "sport": "TEXT",
                   "edge_open": "REAL", "open_ref": "TEXT"},     # edge vs the opening line (Marvin 2026-10-02)
}


def now() -> datetime:
    return datetime.now().astimezone()


def today_local() -> str:
    return now().astimezone(LOCAL_TZ).date().isoformat()


def local_day(iso: Optional[str]) -> str:
    """CT calendar day of an ISO timestamp (offset-aware; naive = box-local). '' if missing/unparsable."""
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)[:10]
    if t.tzinfo is None:
        t = t.astimezone()
    return t.astimezone(LOCAL_TZ).date().isoformat()


WEEKEND_DAYS = (5, 6)   # Oct 11 2026 (Marvin): Saturday + Sunday (CT) are the big days -> weekend budgets/poll limits


def is_weekend_local(day: Optional[str] = None) -> bool:
    """True when the CT calendar day (ISO date; default today CT) is a Saturday or Sunday."""
    d = date.fromisoformat(day) if day else now().astimezone(LOCAL_TZ).date()
    return d.weekday() in WEEKEND_DAYS


def alloc_daily(a: Optional[Dict[str, Any]], day: Optional[str] = None) -> Optional[float]:
    """Daily allocation for a CT day: `weekend_daily` on Sat/Sun (CT) when set, else `daily`."""
    a = a or {}
    try:   # 2026-10-24 (Marvin): Saturday = CFB priority -> per-strategy saturday_daily (ajbet/sat_priority.py)
        from . import sat_priority as _sp
        v = _sp.saturday_alloc(a, day)
        if v is not None:
            return v
    except Exception:  # noqa: BLE001
        pass
    if a.get("weekend_daily") is not None and is_weekend_local(day):
        return a["weekend_daily"]
    return a.get("daily")


def month_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class DB:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for c, typ in cols.items():
                if c not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} {typ}")
        self.conn.commit()

    # ---- pregame snapshots
    def save_pregame(self, event_id: str, league: str, lines: Dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pregame VALUES (?,?,?,?)",
            (event_id, league, now().isoformat(), json.dumps(lines)))
        self.conn.commit()

    def get_pregame(self, event_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT lines_json FROM pregame WHERE event_id=?", (event_id,)).fetchone()
        return json.loads(r[0]) if r else None

    # ---- alerts / dedupe
    def has_alert(self, dedupe_key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM alerts WHERE dedupe_key=?", (dedupe_key,)).fetchone() is not None

    def insert_alert(self, row: Dict[str, Any]) -> Optional[int]:
        """Returns new id, or None if the dedupe key already exists."""
        cols = ["ts", "dedupe_key", "event_id", "league", "strategy", "market", "selection", "line",
                "price", "book", "fair_p", "market_p", "edge", "value", "trigger", "message", "details_json"]
        row = dict(row)
        row.setdefault("ts", now().isoformat())
        try:
            cur = self.conn.execute(
                f"INSERT INTO alerts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [row.get(c) for c in cols])
        except sqlite3.IntegrityError:
            return None
        self.conn.commit()
        return cur.lastrowid

    def mark_sent(self, alert_id: int) -> None:
        self.conn.execute("UPDATE alerts SET sent=1 WHERE id=?", (alert_id,))
        self.conn.commit()

    # ---- Telegram outbox (failed sends, retried next loop / after restart)
    def outbox_add(self, chat_id: str, text: str, html: bool, alert_ids: List[int]) -> int:
        cur = self.conn.execute("INSERT INTO outbox (ts,chat_id,text,html,alert_ids) VALUES (?,?,?,?,?)",
                                (now().isoformat(), str(chat_id), text, int(html), json.dumps(list(alert_ids))))
        self.conn.commit()
        return cur.lastrowid

    def outbox_pending(self, max_attempts: int = 30) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM outbox WHERE status='pending' AND attempts < ? ORDER BY id",
                                 (max_attempts,)).fetchall()

    def outbox_attempt(self, oid: int) -> None:
        self.conn.execute("UPDATE outbox SET attempts=attempts+1 WHERE id=?", (oid,))
        self.conn.commit()

    def outbox_done(self, oid: int, status: str) -> None:
        self.conn.execute("UPDATE outbox SET status=?, done_ts=? WHERE id=?", (status, now().isoformat(), oid))
        self.conn.commit()

    # ---- paper bets
    def insert_paper_bet(self, row: Dict[str, Any]) -> int:
        cols = ["alert_id", "ts", "bet_date", "event_id", "league", "strategy", "market", "selection",
                "side", "line", "price", "book", "stake", "to_win", "player", "ev", "fair_p", "model_p",
                "kelly_stake", "odds_event_id", "commence", "tag", "sport", "edge_open", "open_ref"]
        row = dict(row)
        ts = now()
        row.setdefault("ts", ts.isoformat())
        row.setdefault("bet_date", ts.astimezone(LOCAL_TZ).date().isoformat())   # CT day (Marvin), not box-local
        # 2026-10-23 Marvin: swing strategies are TRACK-ONLY (still logged + graded, out of the paper ledger).
        # Off: env AJ_TRACK_ONLY=off  or  touch data/TRACK_ONLY_OFF.  Set list: AJ_TRACK_ONLY=line_swing,total_swing
        if row.get("strategy") in _track_only() and not str(row.get("tag") or "").startswith("watchlist"):
            row["tag"] = "watchlist:track_only" + (f"|{row['tag']}" if row.get("tag") else "")
        try:  # 2026-11-04 Marvin: NFL LIVE receptions props track-only (ajbet/nfl_live_rec_track.py); AJ_NFL_LIVE_REC_TRACK=off
            from . import nfl_live_rec_track as _nlr
            if _nlr.track_paper(self, row) and not str(row.get("tag") or "").startswith("watchlist"):
                row["tag"] = "watchlist:track_only" + (f"|{row['tag']}" if row.get("tag") else "")
        except Exception:  # noqa: BLE001
            pass
        try:  # 2026-11-04 Marvin: NCAAF live value + CFB live TD (D/ST, >30min) track-only (ajbet/ncaaf_live_track.py)
            from . import ncaaf_live_track as _nlt   # off: AJ_NCAAF_LIVE_VALUE_TRACK=off / AJ_CFB_TD_TRACK=off | data/<NAME>_OFF
            if _nlt.track_paper(self, row) and not str(row.get("tag") or "").startswith("watchlist"):
                row["tag"] = "watchlist:track_only" + (f"|{row['tag']}" if row.get("tag") else "")
        except Exception:  # noqa: BLE001
            pass
        try:  # 2026-11-04 Marvin: LIVE player props with stated EV > 0.5 track-only (ajbet/live_prop_ev50_track.py)
            from . import live_prop_ev50_track as _lpe   # off: AJ_LIVE_PROP_EV50_TRACK=off | data/AJ_LIVE_PROP_EV50_TRACK_OFF
            if _lpe.track_paper(self, row) and not str(row.get("tag") or "").startswith("watchlist"):
                row["tag"] = "watchlist:track_only" + (f"|{row['tag']}" if row.get("tag") else "")
        except Exception:  # noqa: BLE001
            pass
        try:  # 2026-11-02 Marvin: value odds cap +300 / no CFB pregame ML-spread-prop paper (ajbet/me_gates.py)
            from . import me_gates as _mg
            _why = _mg.skip_paper(row)
        except Exception:  # noqa: BLE001
            _why = None
        if _why:
            return 0
        cur = self.conn.execute(
            f"INSERT INTO paper_bets ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [row.get(c) for c in cols])
        self.conn.commit()
        return cur.lastrowid

    def open_bets(self) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM paper_bets WHERE status='open'").fetchall()

    def settle_bet(self, bet_id: int, status: str, pnl: float, final_score: str) -> None:
        self.conn.execute(
            "UPDATE paper_bets SET status=?, pnl=?, settled_at=?, final_score=? WHERE id=?",
            (status, pnl, now().isoformat(), final_score, bet_id))
        self.conn.commit()

    def all_bets(self) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM paper_bets ORDER BY ts").fetchall()

    # ---- API usage / budget
    def log_usage(self, endpoint: str, sport: str, status: int, cost: Optional[float],
                  remaining: Optional[float], used: Optional[float], note: str = "",
                  strategy: str = "live", key_label: str = "free") -> None:
        t = now()
        self.conn.execute(
            "INSERT INTO api_usage (ts,endpoint,sport,status,cost,remaining,used,note,strategy,key_label,"
            "ts_utc,day_local) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (t.isoformat(), endpoint, sport, status, cost, remaining, used, note, strategy, key_label,
             t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), t.astimezone(LOCAL_TZ).date().isoformat()))
        self.conn.commit()

    def last_usage(self, key_label: Optional[str] = None) -> Optional[sqlite3.Row]:
        q, a = "SELECT * FROM api_usage WHERE remaining IS NOT NULL", []
        if key_label:
            q += " AND COALESCE(key_label,'free')=?"
            a.append(key_label)
        return self.conn.execute(q + " ORDER BY id DESC LIMIT 1", a).fetchone()

    # Daily = CT calendar day; monthly = UTC month (The Odds API resets the 1st at 00:00 UTC)
    _DAY = "COALESCE(day_local, substr(ts,1,10))"
    _MONTH = "COALESCE(substr(ts_utc,1,7), substr(ts,1,7))"

    def credits_used_on(self, day: str, strategy: Optional[str] = None, key_label: Optional[str] = None) -> float:
        q, a = f"SELECT COALESCE(SUM(cost),0) FROM api_usage WHERE {self._DAY}=?", [day]
        if strategy:
            q += " AND COALESCE(strategy,'live')=?"
            a.append(strategy)
        if key_label:
            q += " AND COALESCE(key_label,'free')=?"
            a.append(key_label)
        return float(self.conn.execute(q, a).fetchone()[0] or 0)

    def credits_used_in_month(self, month: str, strategy: Optional[str] = None, key_label: Optional[str] = None) -> float:
        q, a = f"SELECT COALESCE(SUM(cost),0) FROM api_usage WHERE {self._MONTH}=?", [month]
        if strategy:
            q += " AND COALESCE(strategy,'live')=?"
            a.append(strategy)
        if key_label:
            q += " AND COALESCE(key_label,'free')=?"
            a.append(key_label)
        return float(self.conn.execute(q, a).fetchone()[0] or 0)

    # ---- value-finder bookkeeping
    def window_done(self, day: str, sport: str, win: str) -> bool:
        return self.conn.execute("SELECT 1 FROM value_runs WHERE day=? AND sport=? AND win=?",
                                 (day, sport, win)).fetchone() is not None

    def mark_window(self, day: str, sport: str, win: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO value_runs VALUES (?,?,?,?)", (day, sport, win, now().isoformat()))
        self.conn.commit()

    def props_fetched(self, day: str) -> List[str]:
        return [r[0] for r in self.conn.execute("SELECT event_id FROM value_props WHERE day=?", (day,))]

    def mark_props(self, day: str, event_id: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO value_props VALUES (?,?,?)", (day, event_id, now().isoformat()))
        self.conn.commit()

    def kv_get(self, k: str):
        r = self.conn.execute("SELECT ts, v FROM kv WHERE k=?", (k,)).fetchone()
        return (r[0], json.loads(r[1])) if r else (None, None)

    def kv_set(self, k: str, v) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (k, now().isoformat(), json.dumps(v)))
        self.conn.commit()

    def update_clv(self, bet_id: int, close_fair_p: float, clv: float) -> None:
        self.conn.execute("UPDATE paper_bets SET close_fair_p=?, clv=?, close_ts=? WHERE id=?",
                          (close_fair_p, clv, now().isoformat(), bet_id))
        self.conn.commit()

    def sport_last_call(self, sport: str) -> Optional[str]:
        r = self.conn.execute("SELECT last_ts FROM sport_calls WHERE sport=?", (sport,)).fetchone()
        return r[0] if r else None

    def set_sport_last_call(self, sport: str, ts: Optional[str] = None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO sport_calls VALUES (?,?)", (sport, ts or now().isoformat()))
        self.conn.commit()
