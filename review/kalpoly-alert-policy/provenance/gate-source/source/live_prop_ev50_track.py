"""LIVE player props with stated EV > 50% are TRACK-ONLY (Marvin approved 2026-11-04).
Scope: market player_* (any league/strategy) AND in-game (alert details_json in_game=true, or bet ts after
commence) AND stated EV (alerts.edge; paper row ev if no alert) > AJ_LIVE_PROP_EV_MAX (default 0.5).
Paper row is still written + graded but tagged watchlist:track_only (out of the paper ledger / no paper stake),
and the Telegram send is skipped (alert row stays sent=0). Pregame props and non-prop alerts unchanged.
Off: AJ_LIVE_PROP_EV50_TRACK=off | data/AJ_LIVE_PROP_EV50_TRACK_OFF (checked every call; no restart).
Same pattern as nfl_live_rec_track.py / ncaaf_live_track.py.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
REASON = "live_prop_ev50_track"


def enabled() -> bool:
    if os.environ.get("AJ_LIVE_PROP_EV50_TRACK", "").strip().lower() in ("off", "0", "false", "no"):
        return False
    return not (_ROOT / "data" / "AJ_LIVE_PROP_EV50_TRACK_OFF").exists()


def ev_max() -> float:
    try:
        return float(os.environ.get("AJ_LIVE_PROP_EV_MAX", "0.5"))
    except ValueError:
        return 0.5


def _det(d):
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:  # noqa: BLE001
            return {}
    return d if isinstance(d, dict) else {}


def _ts(x):
    if not x:
        return None
    try:
        t = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _after(ts, commence) -> bool:
    a, b = _ts(ts), _ts(commence)
    return a is not None and b is not None and a > b


def _prop(market) -> bool:
    return str(market or "").startswith("player_")


def classify(market, in_game, ev) -> bool:
    if not enabled() or not _prop(market) or not in_game:
        return False
    try:
        return ev is not None and float(ev) > ev_max()
    except (TypeError, ValueError):
        return False


def track_paper(db, row) -> bool:
    """Paper hook: True -> tag the paper row watchlist:track_only."""
    row = row or {}
    if not enabled() or not _prop(row.get("market")):
        return False
    ig = bool(row.get("in_game") or _det(row.get("details")).get("in_game"))
    ev = row.get("ev")
    if db is not None and row.get("alert_id") is not None:
        try:
            r = db.conn.execute("SELECT details_json, edge FROM alerts WHERE id = ?", (int(row["alert_id"]),)).fetchone()
            if r is not None:
                ig = ig or bool(_det(r[0]).get("in_game"))
                if r[1] is not None:
                    ev = r[1]
        except Exception:  # noqa: BLE001
            pass
    ig = ig or _after(row.get("ts") or datetime.now(timezone.utc).isoformat(), row.get("commence"))
    return classify(row.get("market"), ig, ev)


def blocked_alert(db, alert_id) -> bool:
    """Notifier hook: True when any alert id is a live prop with stated EV above the max (do not send)."""
    if db is None or alert_id is None or not enabled():
        return False
    ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
    for a in ids:
        try:
            r = db.conn.execute("SELECT market, details_json, edge, ts FROM alerts WHERE id = ?", (int(a),)).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if r is None or not _prop(r[0]):
            continue
        det = _det(r[1])
        ig = bool(det.get("in_game")) or _after(r[3], det.get("commence"))
        if classify(r[0], ig, r[2]):
            return True
    return False
