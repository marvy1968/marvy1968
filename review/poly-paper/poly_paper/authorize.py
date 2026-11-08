"""Single PAPER simulated-fill authorization boundary.

Every accepted fill and every rejected attempt that reaches this function is
written in one transaction: the fill row (accept only) and the audit row commit
together, or neither does. Direct SQL on the guarded connection cannot insert.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .canonical import canonical_json, sha256_obj, sha256_text
from .fee import economics_match
from .policy import classify, decide_fill
from .storage import connect, locked, purpose, transaction

CT = ZoneInfo("America/Chicago")
SOURCE_NAME = "review-poly-paper-20261108"


class _Injected(RuntimeError):
    pass


def source_identity():
    root = Path(__file__).resolve().parent
    parts = []
    for path in sorted(root.iterdir()):
        if path.suffix not in {".py", ".json"} or path.name.startswith("."):
            continue
        parts.append(path.name + ":" + sha256_text(path.read_text(encoding="utf-8")))
    return SOURCE_NAME, sha256_text("\n".join(parts))


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def authorize_and_commit(db_path, request, *, policy_dir, switch_dir, env=None):
    env = {} if env is None else dict(env)
    version, tree = source_identity()
    try:
        decision = _decide(request, policy_dir=policy_dir, switch_dir=switch_dir, env=env)
    except _Injected:
        decision = {
            "action": "fail_closed",
            "reasons": ["transaction_failed"],
            "identity": None,
            "policy_action": "allow",
            "input_record": {"failpoint": "transaction"},
            "snapshot": None,
            "effective": None,
            "gate": None,
        }
    with locked(db_path) as real:
        con = connect(real)
        try:
            return _persist(con, decision, version, tree, request)
        finally:
            con.close()


def _decide(request, *, policy_dir, switch_dir, env):
    reasons = []
    if not isinstance(request, dict):
        return _pack("fail_closed", ["request_malformed"], None, "fail_closed", {}, None, None, None)
    entry = request.get("entry")
    if not isinstance(entry, dict):
        return _pack("fail_closed", ["request_malformed"], None, "fail_closed", {}, None, None, None)
    entry = dict(entry)
    if "live" in request:
        entry["live"] = request.get("live")
    writer = request.get("writer")
    if request.get("writer_conflict") or writer not in ("runner", "pm_fill"):
        reasons.append("request_malformed")
    claimed_side = request.get("claimed_side")
    if claimed_side not in ("long", "short"):
        reasons.append("request_malformed")
    accounting = request.get("accounting_date")
    if not isinstance(accounting, str) or len(accounting) != 10:
        reasons.append("request_malformed")
    try:
        snapshot_json = canonical_json(request.get("snapshot"))
        snapshot = json.loads(snapshot_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _pack("fail_closed", ["request_malformed"], None, "fail_closed", {"entry": entry}, None, None, None)
    input_record = {
        "writer": writer,
        "caller": request.get("caller"),
        "entry": entry,
        "claimed_slug": request.get("claimed_slug"),
        "claimed_side": claimed_side,
        "claimed_token": request.get("claimed_token"),
        "price": request.get("price"),
        "contracts": request.get("contracts"),
        "fee": request.get("fee"),
        "cost": request.get("cost"),
        "fair": request.get("fair"),
        "edge": request.get("edge"),
        "accounting_date": accounting,
        "lane": request.get("lane"),
        "lane_switch": request.get("lane_switch"),
        "live_order": bool(request.get("live_order")),
    }
    if reasons:
        return _pack("fail_closed", reasons, None, "fail_closed", input_record, snapshot, None, None)
    supplied = request.get("type_tag")
    actual = classify(entry.get("key"), entry.get("me_type"), entry.get("market"), entry.get("sport"))
    if supplied is not None and supplied != actual:
        return _pack("fail_closed", ["type_tag_disagrees"], None, "fail_closed", input_record, snapshot, None, None)
    decided = decide_fill(
        entry=entry,
        snapshot=snapshot,
        claimed_slug=request.get("claimed_slug"),
        claimed_side=claimed_side,
        claimed_token=request.get("claimed_token"),
        price=request.get("price"),
        writer=writer,
        lane_switch=request.get("lane_switch"),
        policy_dir=policy_dir,
        switch_dir=switch_dir,
        env=env,
        live_order=bool(request.get("live_order")),
    )
    action = decided["action"]
    all_reasons = list(decided["reasons"])
    identity = decided["identity"]
    if action == "accept" and not economics_match(request.get("contracts"), request.get("price"), request.get("fee"), request.get("cost")):
        action = "fail_closed"
        all_reasons = ["fee_or_contract_mismatch"]
    return _pack(action, all_reasons, identity, decided["policy_action"], input_record, snapshot, decided["effective"], decided["gate"])


def _pack(action, reasons, identity, policy_action, input_record, snapshot, effective, gate):
    return {
        "action": action,
        "reasons": reasons,
        "identity": identity,
        "policy_action": policy_action,
        "input_record": input_record,
        "snapshot": snapshot,
        "effective": effective,
        "gate": gate,
    }


def _persist(con, decision, version, tree, request):
    failpoint = request.get("failpoint") if isinstance(request, dict) else None
    if decision["action"] != "accept":
        audit_id = _write_audit(con, decision, version, tree, fill_id=None)
        return _public(False, decision, None, audit_id)
    try:
        with transaction(con):
            accounting = decision["input_record"]["accounting_date"]
            key = decision["input_record"]["entry"].get("key")
            existing = con._raw.execute("select id from fills where date=? and key=?", (accounting, key)).fetchone()
            if existing:
                decision = dict(decision, action="track_reject", reasons=["duplicate_key"])
                audit_id = _insert_audit(con, decision, version, tree, None)
                return _public(False, decision, None, audit_id)
            fill_id = _insert_fill(con, decision, version, tree)
            if failpoint in ("after_fill_insert", "before_commit"):
                raise _Injected(failpoint)
            audit_id = _insert_audit(con, decision, version, tree, fill_id)
        return _public(True, decision, fill_id, audit_id)
    except _Injected:
        failed = dict(decision, action="fail_closed", reasons=["transaction_failed"])
        audit_id = _write_audit(con, failed, version, tree, None)
        return _public(False, failed, None, audit_id)


def _write_audit(con, decision, version, tree, fill_id):
    with transaction(con):
        return _insert_audit(con, decision, version, tree, fill_id)


def _insert_fill(con, decision, version, tree):
    record = decision["input_record"]
    entry = record["entry"]
    ident = decision["identity"]
    now = datetime.now(CT).isoformat()
    with purpose("fill"):
        cur = con._raw.execute(
            """insert into fills(
                ts, date, key, pick, slug, side, contracts, price, fee, cost, fair, edge, status,
                me_type, me_alert_id, lane, sport_canon, event_id, event_slug, event_date,
                participants_json, outcome_json, token_id, token_status, input_sha256, snapshot_sha256,
                policy_sha256, source_version, source_tree_sha256
            ) values (?,?,?,?,?,?,?,?,?,?,?,?, 'open', ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now,
                record["accounting_date"],
                entry.get("key"),
                entry.get("pick"),
                ident.outcome["market_slug"],
                ident.side,
                record["contracts"],
                record["price"],
                record["fee"],
                record["cost"],
                _num(record.get("fair")),
                _num(record.get("edge")),
                entry.get("me_type") or "marv",
                entry.get("me_alert_id"),
                record.get("lane"),
                ident.sport,
                ident.event_id,
                ident.event_slug,
                ident.event_date,
                canonical_json(ident.participants),
                canonical_json(ident.outcome),
                ident.token_id,
                ident.token_status,
                sha256_obj(record),
                sha256_obj(decision["snapshot"]),
                _policy_sha(decision),
                version,
                tree,
            ),
        )
    return cur.lastrowid


def _insert_audit(con, decision, version, tree, fill_id):
    ident = decision.get("identity")
    record = decision.get("input_record") or {}
    now = datetime.now(CT).isoformat()
    effective = decision.get("effective")
    body = {
        "action": decision["action"],
        "reasons": decision["reasons"],
        "policy_action": decision.get("policy_action"),
        "notes": [] if ident is None else ident.notes,
        "type_gate_sha256": None if decision.get("gate") is None else sha256_obj(decision["gate"]),
        "failpoint": record.get("failpoint") if isinstance(record, dict) else None,
    }
    with purpose("audit"):
        cur = con._raw.execute(
            """insert into fill_audit(
                ts, action, reasons_json, fill_id, writer, caller, input_json, input_sha256,
                snapshot_json, snapshot_sha256, sport, event_id, event_slug, event_date,
                participants_json, outcome_json, token_id, token_status, requested_side,
                policy_id, policy_sha256, effective_policy_json, decision_json,
                source_version, source_tree_sha256
            ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now,
                decision["action"],
                canonical_json(decision["reasons"]),
                fill_id,
                record.get("writer") if isinstance(record, dict) else None,
                record.get("caller") if isinstance(record, dict) else None,
                canonical_json(record),
                sha256_obj(record),
                None if decision.get("snapshot") is None else canonical_json(decision["snapshot"]),
                None if decision.get("snapshot") is None else sha256_obj(decision["snapshot"]),
                None if ident is None else ident.sport,
                None if ident is None else ident.event_id,
                None if ident is None else ident.event_slug,
                None if ident is None else ident.event_date,
                None if ident is None or ident.participants is None else canonical_json(ident.participants),
                None if ident is None or ident.outcome is None else canonical_json(ident.outcome),
                None if ident is None else ident.token_id,
                None if ident is None else ident.token_status,
                record.get("claimed_side") if isinstance(record, dict) else None,
                None if not effective else effective.get("policy_id"),
                _policy_sha(decision),
                None if effective is None else canonical_json(effective),
                canonical_json(body),
                version,
                tree,
            ),
        )
    return cur.lastrowid


def _policy_sha(decision):
    if not decision.get("effective"):
        return None
    return sha256_obj(decision["effective"])


def _public(accepted, decision, fill_id, audit_id):
    return {
        "accepted": accepted,
        "action": decision["action"],
        "reasons": list(decision["reasons"]),
        "fill_id": fill_id,
        "audit_id": audit_id,
        "quarantined_library": True,
    }
