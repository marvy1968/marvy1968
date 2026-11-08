"""Marv bet-card delivery. Weekend order matches main.py. Weekdays do not claim a send."""
from __future__ import annotations

import json
from pathlib import Path

from .clock import ct_date, is_weekend


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError:
        return {}


def deliver_card(state: Path, *, now, bets: list, transport) -> dict:
    state.mkdir(parents=True, exist_ok=True)
    day = ct_date(now).isoformat()
    sent_path = state / "betcard_sent.json"
    hold_path = state / "betcard_weekday_hold.json"
    log_path = state / "betcard_log.json"
    sent = _load(sent_path)
    hold = _load(hold_path)
    if is_weekend(now):
        # Source marks the card, then posts, then logs. That order stays.
        if day in sent:
            return {"posted": False, "claimed_sent": True, "logged": False, "status": "already_sent"}
        sent[day] = {"sent_at": now.isoformat(), "games": len(bets)}
        sent_path.write_text(json.dumps(sent))
        transport(bets)
        book = _load(log_path)
        book[day] = bets
        log_path.write_text(json.dumps(book))
        return {"posted": True, "claimed_sent": True, "logged": True, "status": "weekend_posted"}
    if day in hold:
        return {"posted": False, "claimed_sent": False, "logged": False, "status": "already_held"}
    hold[day] = {"held_at": now.isoformat(), "games": len(bets), "delivered": False}
    hold_path.write_text(json.dumps(hold))
    book = _load(log_path)
    book[day] = bets
    log_path.write_text(json.dumps(book))
    return {"posted": False, "claimed_sent": day in _load(sent_path), "logged": True, "status": "weekday_suppressed"}
