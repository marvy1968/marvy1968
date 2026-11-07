"""Append-only decision and delivery audit.

Exposure for an already emitted qualified play is the first allocation row. Later
reevaluations append a new row and do not change the first one. Legacy pick and
track files are read, never rewritten, by the helpers in this module.

Delivery status:
  failed         the send function raised the pre-request missing-token error.
                 No request was started by send_message. A later run may try.
  unknown        the send returned without a provider receipt, or it raised after
                 the call began. This includes today's send_message, which returns
                 None. _post discards the Bot API body, so a message id is not
                 available. A blocked chat also returns without an id. None of
                 those are an acknowledgement.
  acknowledged   only when the send function returns a receipt (a dict with
                 message_id, or a non-empty receipt string). This module never
                 invents one.

Unknown outcomes are not retried here. A second deliver() with the same intent
does not call the sender again. apply_betcard_send also refuses a new attempt
when an unknown or acknowledged intent already exists for that card date.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import fcntl

LEDGER = "decision_audit.jsonl"
_PREWIRE = "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set"


@dataclass
class SendStatus:
    code: str
    intent_id: str
    raised: bool = False


def new_run_id() -> str:
    return "run_" + hashlib.sha256(os.urandom(16)).hexdigest()[:20]


def decision_id(*, ledger: str, sport: str, game_id, market: str, side: str, et_date) -> str:
    day = et_date.isoformat() if hasattr(et_date, "isoformat") else str(et_date)
    raw = "|".join([ledger, str(sport), str(game_id), str(market), str(side).lower(), day])
    return "dec_" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def _path(state) -> Path:
    return Path(state) / LEDGER


def _iter_lines(text: str):
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            yield json.loads(stripped)
        except json.JSONDecodeError:
            continue


def events(state) -> list:
    path = _path(state)
    if not path.exists():
        return []
    return list(_iter_lines(path.read_text()))


def append_event(state, event: dict) -> dict:
    """Append one JSON object. The same idempotency_key returns the existing row."""
    if "idempotency_key" not in event:
        raise ValueError("idempotency_key is required")
    path = _path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(event, sort_keys=True, default=str) + "\n"
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        current = handle.read()
        for old in _iter_lines(current):
            if old.get("idempotency_key") == event["idempotency_key"]:
                return old
        handle.seek(0, os.SEEK_END)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return event


def record_stage(state, *, run_id: str, sport: str, stage: str, rows: list) -> dict:
    body = json.dumps(rows, sort_keys=True, default=str)
    return append_event(state, {
        "kind": "gate_snapshot",
        "run_id": run_id,
        "sport": sport,
        "stage": stage,
        "rows": rows,
        "idempotency_key": "stage:" + hashlib.sha256(f"{run_id}|{sport}|{stage}|{body}".encode()).hexdigest()[:24],
    })


def record_allocation(state, *, run_id: str, sport: str, game_id, market: str, side: str, et_date,
                      stake_fraction: float, game: str = "", home: str = "", away: str = "",
                      line=None, price=None, preserved: bool = False) -> dict:
    """First write is the exposure. A repeat links back to it and does not add stake."""
    day = et_date.isoformat() if hasattr(et_date, "isoformat") else str(et_date)
    ident = decision_id(ledger="qualified", sport=sport, game_id=game_id, market=market, side=side, et_date=day)
    existing = next((row for row in events(state)
                     if row.get("kind") == "allocation" and row.get("decision_id") == ident), None)
    if existing:
        return append_event(state, {
            "kind": "reevaluation",
            "run_id": run_id,
            "decision_id": ident,
            "sport": sport,
            "game_id": game_id,
            "market": market,
            "side": side,
            "et_date": day,
            "preserved_stake_fraction": existing.get("stake_fraction"),
            "proposed_stake_fraction": stake_fraction,
            "exposure": False,
            "idempotency_key": f"reeval:{ident}:{run_id}",
        })
    return append_event(state, {
        "kind": "allocation",
        "ledger": "qualified",
        "run_id": run_id,
        "decision_id": ident,
        "sport": sport,
        "game_id": game_id,
        "market": market,
        "side": side,
        "et_date": day,
        "stake_fraction": stake_fraction,
        "units": round(float(stake_fraction) * 100, 4),
        "game": game,
        "home": home,
        "away": away,
        "line": line,
        "price": price,
        "preserved": preserved,
        "exposure": True,
        "idempotency_key": f"alloc:{ident}",
    })


def _legacy_stake(row: dict, sport: str, stake_of) -> float:
    key = f"{sport}_stake"
    if key in row and row[key] is not None:
        return float(row[key])
    return float(stake_of(row))


def exposure_context(sport: str, pred_ids: set, legacy_rows: list, stake_of, audit_rows: list, et_of):
    """Prior night fractions and in-slate preserved allocations.

    pred_ids are games on the current slate. Their emitted stake is returned in
    `preserved` and is not also added to `prior`. Audit exposure wins over a
    legacy picks row with the same game, market, side, and ET date. Track-only
    rows are not a source of stake. Nothing here is written back.
    """
    preserved = {}
    prior = {}
    seen = set()
    for row in audit_rows:
        if row.get("kind") != "allocation" or row.get("exposure") is False or row.get("sport") != sport:
            continue
        if row.get("ledger") not in (None, "qualified"):
            continue
        night = et_of(row.get("et_date"))
        side = str(row.get("side", "")).lower()
        stake = float(row.get("stake_fraction") or 0.0)
        token = (str(row.get("game_id")), row.get("market"), side, night)
        seen.add(token)
        if str(row.get("game_id")) in {str(item) for item in pred_ids}:
            preserved[(str(row.get("game_id")), row.get("market"), side)] = {
                "stake_fraction": stake,
                "decision_id": row.get("decision_id"),
                "et_date": night,
            }
        else:
            prior[night] = prior.get(night, 0.0) + stake
    for row in legacy_rows:
        if row.get("sport") != sport:
            continue
        night = et_of(row.get("start"))
        side = str(row.get("side", "")).lower()
        token = (str(row.get("game_id")), row.get("market"), side, night)
        if token in seen:
            continue
        stake = _legacy_stake(row, sport, stake_of)
        ident = decision_id(ledger="qualified", sport=sport, game_id=row.get("game_id"),
                            market=row.get("market"), side=side, et_date=night)
        if str(row.get("game_id")) in {str(item) for item in pred_ids}:
            preserved[(str(row.get("game_id")), row.get("market"), side)] = {
                "stake_fraction": stake,
                "decision_id": ident,
                "et_date": night,
                "legacy": True,
            }
        else:
            prior[night] = prior.get(night, 0.0) + stake
        seen.add(token)
    return preserved, prior


def qualified_exposure(sport: str, et_date, audit_rows: list) -> float:
    day = et_date.isoformat() if hasattr(et_date, "isoformat") else str(et_date)
    total = 0.0
    seen = set()
    for row in audit_rows:
        if row.get("kind") != "allocation" or row.get("exposure") is False or row.get("sport") != sport:
            continue
        if str(row.get("et_date")) != day or row.get("decision_id") in seen:
            continue
        seen.add(row.get("decision_id"))
        total += float(row.get("stake_fraction") or 0.0)
    return total


def receipt_from(result):
    """A provider receipt, or None. Never synthesizes one from a successful-looking return."""
    if isinstance(result, dict):
        message_id = result.get("message_id")
        if message_id:
            return {"message_id": message_id}
        token = result.get("receipt")
        if isinstance(token, str) and token.strip():
            return {"receipt": token.strip()}
        return None
    if isinstance(result, str) and result.strip():
        return {"receipt": result.strip()}
    return None


def _intents(rows, channel, day: str):
    for row in rows:
        if row.get("kind") == "send_intent" and row.get("channel") == channel and row.get("et_date") == day:
            yield row


def _delivery_for(rows, intent_id: str):
    found = [row for row in rows if row.get("kind") == "delivery" and row.get("intent_id") == intent_id]
    return found[-1] if found else None


def resend_blocked(state, when, channel: str) -> bool:
    """True when another automatic send could double-deliver.

    A legacy bet-card marker blocks the resend and is still not a receipt.
    `failed` (pre-request) does not block. Missing, unknown, and acknowledged do.
    """
    day = when.isoformat() if isinstance(when, date) else str(when)
    if channel == "betcard":
        path = Path(state) / "betcard_sent.json"
        try:
            sent = json.loads(path.read_text()) if path.exists() else {}
        except ValueError:
            sent = {}
        if isinstance(sent, dict) and day in sent:
            return True
    rows = events(state)
    for intent in _intents(rows, channel, day):
        delivery = _delivery_for(rows, intent.get("intent_id"))
        code = None if delivery is None else delivery.get("status")
        if code is None or code in ("unknown", "acknowledged"):
            return True
    return False


def deliver(state, *, run_id: str, channel: str, when, decision_ids: list, payload: str, send_fn) -> SendStatus:
    """Record intent, call send_fn once, record the outcome. Does not retry."""
    day = when.date().isoformat() if isinstance(when, datetime) else (
        when.isoformat() if isinstance(when, date) else str(when))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    intent_id = "intent_" + hashlib.sha256(f"{channel}|{day}|{digest}|{run_id}".encode()).hexdigest()[:20]
    existing = next((row for row in events(state) if row.get("idempotency_key") == f"intent:{intent_id}"), None)
    if existing:
        delivery = _delivery_for(events(state), intent_id)
        code = delivery.get("status") if delivery else "unknown"
        return SendStatus(code, intent_id, raised=bool(delivery and delivery.get("raised")))
    append_event(state, {
        "kind": "send_intent",
        "intent_id": intent_id,
        "run_id": run_id,
        "channel": channel,
        "et_date": day,
        "decision_ids": list(decision_ids),
        "payload_sha256": digest,
        "exposure": False,
        "idempotency_key": f"intent:{intent_id}",
    })
    raised = False
    try:
        result = send_fn()
    except ValueError as exc:
        code = "failed" if str(exc) == _PREWIRE else "unknown"
        raised = True
        receipt = None
        note = "pre-request token check" if code == "failed" else "exception during send; outcome unknown"
    except Exception:
        result = None
        code = "unknown"
        raised = True
        receipt = None
        note = "exception during send; outcome unknown; not retried"
    else:
        receipt = receipt_from(result)
        if receipt:
            code = "acknowledged"
            note = "provider receipt returned by the send function"
        else:
            code = "unknown"
            note = ("no provider receipt. send_message returns None and discards the Bot API body; "
                    "a blocked chat also returns without a message id. Not an acknowledgement. Not retried.")
    append_event(state, {
        "kind": "delivery",
        "intent_id": intent_id,
        "run_id": run_id,
        "channel": channel,
        "et_date": day,
        "status": code,
        "receipt": receipt,
        "raised": raised,
        "note": note,
        "idempotency_key": f"delivery:{intent_id}",
    })
    return SendStatus(code, intent_id, raised=raised)


def note_log_failure(state, *, channel: str, intent_id: str, detail: str) -> dict:
    return append_event(state, {
        "kind": "log_failure",
        "channel": channel,
        "intent_id": intent_id,
        "detail": detail[:300],
        "idempotency_key": f"logfail:{intent_id}:{detail[:80]}",
    })


def commit_emitted_card(state, sport, preds) -> None:
    """Hook for a feed overlay. The shared package records no stake by itself.

    Called only after a card send that was not a pre-request failure. Dry runs never call it.
    """
    return None


def bet_decision_ids(bets, when) -> list:
    day = when.date() if isinstance(when, datetime) else when
    out = []
    for bet in bets:
        out.append(decision_id(
            ledger="betcard", sport=bet.sport, game_id=bet.game, market=bet.market, side=bet.side, et_date=day,
        ))
    return out


def apply_betcard_send(state, when, bets, text: str, send_fn, log_fn, alert_fn, mark_fn,
                       force: bool = False) -> str:
    """Bet-card send procedure. Does not call send_fn when the outcome is already unknown or locked.

    A clean return with no receipt is `unknown`: the lock is written, the card is logged,
    and nothing is retried. An exception locks as well (the request may have landed) and
    alerts once. The pre-request missing-token error does not lock, so a later run can try,
    and it is not retried inside this call. force=True is the existing BETCARD_RESEND switch:
    the operator asked for another attempt, which is still a single audited send.
    """
    if not force and resend_blocked(state, when.date() if isinstance(when, datetime) else when, "betcard"):
        return "blocked"
    status = deliver(
        state, run_id=new_run_id(), channel="betcard", when=when,
        decision_ids=bet_decision_ids(bets, when), payload=text, send_fn=send_fn,
    )
    if status.raised:
        alert_fn()
    if status.code != "failed":
        mark_fn(status)
    if status.code == "unknown" and not status.raised:
        try:
            log_fn()
        except Exception as exc:
            note_log_failure(state, channel="betcard", intent_id=status.intent_id, detail=f"{type(exc).__name__}: {exc}")
            return "log_failure"
    return status.code
