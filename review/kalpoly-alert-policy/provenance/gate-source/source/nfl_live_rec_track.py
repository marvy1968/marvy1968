"""NFL LIVE receptions player props are TRACK-ONLY (Marvin approved 2026-11-04).
Scope: league nfl AND market player_receptions* AND in-game (alert details_json in_game=true).
Paper row is still written + graded but tagged watchlist:track_only (out of the paper ledger / no paper stake),
and the Telegram send is skipped (alert row stays sent=0). Pregame receptions, live pass/rush yds, other sports
unchanged. Off: AJ_NFL_LIVE_REC_TRACK=off | data/AJ_NFL_LIVE_REC_TRACK_OFF (checked every call; no restart).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
REASON = "nfl_live_rec_track"


def enabled() -> bool:
    if os.environ.get("AJ_NFL_LIVE_REC_TRACK", "").strip().lower() in ("off", "0", "false", "no"):
        return False
    return not (_ROOT / "data" / "AJ_NFL_LIVE_REC_TRACK_OFF").exists()


def _in_game(details) -> bool:
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except Exception:  # noqa: BLE001
            return False
    return bool(isinstance(details, dict) and details.get("in_game"))


def match(league, market, details) -> bool:
    return (str(league or "").lower() == "nfl" and str(market or "").startswith("player_receptions")
            and _in_game(details))


def _rows(db, alert_id):
    ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
    for a in ids:
        try:
            r = db.conn.execute("SELECT league, market, details_json FROM alerts WHERE id = ?", (int(a),)).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if r is not None:
            yield r


def blocked_alert(db, alert_id) -> bool:
    """Notifier hook: True -> do not send (switch on and any alert id is a live NFL receptions prop)."""
    if db is None or alert_id is None or not enabled():
        return False
    return any(match(r[0], r[1], r[2]) for r in _rows(db, alert_id))


def track_paper(db, row) -> bool:
    """Paper hook: True -> tag the paper row watchlist:track_only."""
    if not enabled() or str((row or {}).get("league") or "").lower() != "nfl" \
            or not str(row.get("market") or "").startswith("player_receptions"):
        return False
    if row.get("in_game") or _in_game(row.get("details")) or _in_game(row.get("details_json")):
        return True
    return db is not None and row.get("alert_id") is not None and blocked_alert(db, row.get("alert_id"))
