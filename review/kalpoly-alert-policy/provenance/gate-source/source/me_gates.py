"""2026-11-02 (Marvin approved) paper/send gates.
1) VALUE_ODDS_CAP: no paper bet for strategy 'value' at American odds longer than +300 (alerts unaffected).
   Off: VALUE_ODDS_CAP=off | data/VALUE_ODDS_CAP_OFF.  Cap override: VALUE_ODDS_CAP_MAX=300
2) CFB_PREGAME_PAPER: no CFB (ncaaf) pregame paper bets on moneyline (h2h), spreads or player props.
   Pregame = commence known and still in the future. In-game CFB unchanged.  Off: CFB_PREGAME_PAPER=off | data/CFB_PREGAME_PAPER_OFF
3) TOTAL_SWING_NOSEND: total_swing alerts are not sent to Telegram (row stays sent=0; paper bet still logged + graded).
   Off: TOTAL_SWING_NOSEND=off | data/TOTAL_SWING_NOSEND_OFF
File switches are checked on every call (no restart needed for them).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _on(name: str) -> bool:
    if os.environ.get(name, "").strip().lower() in ("off", "0", "false", "no"):
        return False
    return not (_ROOT / "data" / f"{name}_OFF").exists()


def _cap() -> float:
    try:
        return float(os.environ.get("VALUE_ODDS_CAP_MAX", "300"))
    except ValueError:
        return 300.0


def _pregame(commence) -> bool:
    if not commence:
        return False
    try:
        t = datetime.fromisoformat(str(commence).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < t
    except Exception:  # noqa: BLE001
        return False


def skip_paper(row) -> str | None:
    """Reason string when this paper bet must not be written, else None."""
    st, mk = str(row.get("strategy") or ""), str(row.get("market") or "")
    if _on("VALUE_ODDS_CAP") and st == "value":
        try:
            if float(row.get("price")) > _cap():
                return "value_odds_cap"
        except (TypeError, ValueError):
            pass
    if (_on("CFB_PREGAME_PAPER") and str(row.get("league") or "").lower() in ("ncaaf", "cfb")
            and (mk in ("h2h", "spreads") or mk.startswith("player_")) and _pregame(row.get("commence"))):
        return "cfb_pregame_paper"
    return None


def nosend_alert(db, alert_id) -> bool:
    """True when any alert id is a total_swing alert and TOTAL_SWING_NOSEND is on."""
    if db is None or alert_id is None or not _on("TOTAL_SWING_NOSEND"):
        return False
    ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
    for a in ids:
        try:
            r = db.conn.execute("SELECT strategy FROM alerts WHERE id = ?", (int(a),)).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if r is not None and r[0] == "total_swing":
            return True
    return False
