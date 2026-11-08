"""NCAAF live track-only rules (Marvin approved 2026-11-04; source /opt/marv-feed/reports/worst_roi_20261104.md #1, #2).
Track-only = paper row still written + graded but tagged watchlist:track_only (out of the paper ledger, no paper
stake) and the Telegram send is skipped (alert row stays sent=0). Same pattern as nfl_live_rec_track.py.

1) AJ_NCAAF_LIVE_VALUE_TRACK: strategy 'value', league ncaaf, in-game (alert details in_game=true, or paper bet
   ts after commence) -> track-only.         Off: AJ_NCAAF_LIVE_VALUE_TRACK=off | data/AJ_NCAAF_LIVE_VALUE_TRACK_OFF
2) AJ_CFB_TD_TRACK: league ncaaf, market player_anytime_td (any strategy/path), placed after kickoff, AND
   (D/ST player  OR  placed > 30 min after kickoff) -> track-only.
   Off: AJ_CFB_TD_TRACK=off | data/AJ_CFB_TD_TRACK_OFF   (minutes override: AJ_CFB_TD_TRACK_MIN=30)
File switches are checked on every call (no restart needed).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
REASON_VALUE = "ncaaf_live_value_track"
REASON_TD = "cfb_td_track"
_OFFS = ("off", "0", "false", "no")


def _on(name: str) -> bool:
    if os.environ.get(name, "").strip().lower() in _OFFS:
        return False
    return not (_ROOT / "data" / f"{name}_OFF").exists()


def _ts(x):
    if not x:
        return None
    try:
        t = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _det(d):
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:  # noqa: BLE001
            return {}
    return d if isinstance(d, dict) else {}


def _mins(ts, commence):
    a, b = _ts(ts), _ts(commence)
    return None if a is None or b is None else (a - b).total_seconds() / 60.0


def _cfb(league) -> bool:
    return str(league or "").lower() in ("ncaaf", "cfb")


def _td_limit() -> float:
    try:
        return float(os.environ.get("AJ_CFB_TD_TRACK_MIN", "30"))
    except ValueError:
        return 30.0


def classify(league, strategy, market, in_game, mins, player) -> str | None:
    """Reason when the bet/alert is track-only (switches respected), else None.
    mins = minutes after kickoff (None if unknown)."""
    if not _cfb(league):
        return None
    live = bool(in_game) or (mins is not None and mins > 0)
    if _on("AJ_NCAAF_LIVE_VALUE_TRACK") and str(strategy or "") == "value" and live:
        return REASON_VALUE
    if _on("AJ_CFB_TD_TRACK") and str(market or "") == "player_anytime_td" and live:
        if "D/ST" in str(player or "") or (mins is not None and mins > _td_limit()):
            return REASON_TD
    return None


def track_paper(db, row) -> str | None:
    """Paper hook: reason -> tag the paper row watchlist:track_only."""
    row = row or {}
    if not _cfb(row.get("league")):
        return None
    det = _det(row.get("details")) or _det(row.get("details_json"))
    ig = bool(row.get("in_game") or det.get("in_game"))
    if not ig and db is not None and row.get("alert_id") is not None:
        try:
            r = db.conn.execute("SELECT details_json FROM alerts WHERE id = ?", (int(row["alert_id"]),)).fetchone()
            ig = bool(r is not None and _det(r[0]).get("in_game"))
        except Exception:  # noqa: BLE001
            pass
    ts = row.get("ts") or datetime.now(timezone.utc).isoformat()
    return classify(row.get("league"), row.get("strategy"), row.get("market"), ig,
                    _mins(ts, row.get("commence")), row.get("player"))


def blocked_alert(db, alert_id) -> str | None:
    """Notifier hook: reason when any alert id is track-only (do not send), else None."""
    if db is None or alert_id is None:
        return None
    ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
    for a in ids:
        try:
            r = db.conn.execute("SELECT league, strategy, market, details_json, ts, message FROM alerts WHERE id = ?",
                                (int(a),)).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if r is None or not _cfb(r[0]):
            continue
        det = _det(r[3])
        player, commence = det.get("player"), det.get("commence")
        try:
            p = db.conn.execute("SELECT player, commence FROM paper_bets WHERE alert_id = ? ORDER BY id LIMIT 1",
                                (int(a),)).fetchone()
            if p is not None:
                player, commence = player or p[0], commence or p[1]
        except Exception:  # noqa: BLE001
            pass
        if not player and "D/ST" in str(r[5] or ""):
            player = "D/ST"
        why = classify(r[0], r[1], r[2], det.get("in_game"), _mins(r[4], commence), player)
        if why:
            return why
    return None
