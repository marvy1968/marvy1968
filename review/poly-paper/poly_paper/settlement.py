"""Settle a review fill without relabeling the game it was authorized for.

Settlement may change result, payout, pnl, settle_px, settled_ts, and settle_src.
It does not change pick, slug, participants, token, sport, or event. A payload for
a different event is an audit reject and does not update the row. Rows that are
already settled are left untouched. Legacy rows with no stored participants are
not graded here.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .canonical import canonical_json, sha256_obj
from .identity import _team_matches
from .provider import parse_team
from .storage import IDENTITY_COLUMNS, connect, locked, purpose, transaction

CT = ZoneInfo("America/Chicago")


def settle(db_path, fill_id, settlement):
    with locked(db_path) as real:
        con = connect(real)
        try:
            return _settle(con, fill_id, settlement)
        finally:
            con.close()


def _settle(con, fill_id, settlement):
    row = con._raw.execute("select * from fills where id=?", (fill_id,)).fetchone()
    if row is None:
        return {"accepted": False, "action": "fail_closed", "reasons": ["fill_missing"]}
    if row["status"] != "open":
        return {"accepted": False, "action": "fail_closed", "reasons": ["already_settled"]}
    if not row["participants_json"] or not row["event_slug"]:
        return {"accepted": False, "action": "fail_closed", "reasons": ["legacy_unscoped"]}
    reasons = _identity_agree(row, settlement)
    before = {column: row[column] for column in IDENTITY_COLUMNS}
    if reasons:
        _audit(con, row, "settlement_reject", reasons, settlement)
        return {"accepted": False, "action": "fail_closed", "reasons": reasons}
    px = settlement.get("long_settlement_px")
    if isinstance(px, bool) or not isinstance(px, (int, float)) or not 0 <= float(px) <= 1:
        _audit(con, row, "settlement_reject", ["settlement_px_invalid"], settlement)
        return {"accepted": False, "action": "fail_closed", "reasons": ["settlement_px_invalid"]}
    win_px = float(px) if row["side"] == "long" else 1 - float(px)
    win_px = round(win_px, 4)
    payout = round(row["contracts"] * win_px, 4)
    pnl = round(payout - row["cost"], 4)
    result = "win" if win_px >= 0.99 else "loss" if win_px <= 0.01 else "push"
    now = datetime.now(CT).isoformat()
    with transaction(con):
        with purpose("settle"):
            con._raw.execute(
                """update fills set status='settled', result=?, payout=?, pnl=?, settle_px=?,
                   settled_ts=?, settle_src=? where id=? and status='open'""",
                (result, payout, pnl, win_px, now, settlement.get("source") or "review", fill_id),
            )
        after = con._raw.execute("select * from fills where id=?", (fill_id,)).fetchone()
        if any(after[column] != before[column] for column in IDENTITY_COLUMNS):
            raise RuntimeError("settlement changed identity columns")
        _insert_settlement_audit(con, after, "settlement_accept", ["identity_agreed"], settlement)
    return {"accepted": True, "action": "settlement_accept", "reasons": ["identity_agreed"], "pnl": pnl, "result": result}


def _identity_agree(row, settlement):
    if not isinstance(settlement, dict):
        return ["settlement_malformed"]
    reasons = []
    if settlement.get("event_slug") != row["event_slug"]:
        reasons.append("settlement_event_disagrees")
    if row["event_id"] and settlement.get("event_id") != row["event_id"]:
        reasons.append("settlement_event_disagrees")
    if row["token_id"] and settlement.get("token_id") != row["token_id"]:
        reasons.append("settlement_token_disagrees")
    stored = json_participants(row["participants_json"])
    supplied = settlement.get("participants")
    if not isinstance(supplied, list) or len(supplied) < 2:
        reasons.append("settlement_participants_missing")
    else:
        teams = [parse_team(item) if isinstance(item, dict) else None for item in supplied]
        if any(team is None for team in teams):
            reasons.append("settlement_participants_missing")
        else:
            for side in stored:
                label = side.get("entry")
                if not any(_team_matches(label, team) for team in teams):
                    reasons.append("settlement_participants_disagree")
                    break
    ordered = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)
    return ordered


def json_participants(text):
    import json
    return json.loads(text)


def _audit(con, row, action, reasons, settlement):
    with transaction(con):
        _insert_settlement_audit(con, row, action, reasons, settlement)


def _insert_settlement_audit(con, row, action, reasons, settlement):
    from .authorize import source_identity
    version, tree = source_identity()
    payload = {"fill_id": row["id"], "settlement": settlement, "reasons": reasons}
    with purpose("audit"):
        con._raw.execute(
            """insert into fill_audit(
                ts, action, reasons_json, fill_id, writer, caller, input_json, input_sha256,
                snapshot_json, snapshot_sha256, sport, event_id, event_slug, event_date,
                participants_json, outcome_json, token_id, token_status, requested_side,
                policy_id, policy_sha256, effective_policy_json, decision_json,
                source_version, source_tree_sha256
            ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(CT).isoformat(),
                action,
                canonical_json(reasons),
                row["id"],
                "settlement",
                "settlement",
                canonical_json(payload),
                sha256_obj(payload),
                None,
                None,
                row["sport_canon"],
                row["event_id"],
                row["event_slug"],
                row["event_date"],
                row["participants_json"],
                row["outcome_json"],
                row["token_id"],
                row["token_status"],
                row["side"],
                None,
                row["policy_sha256"],
                None,
                canonical_json({"reasons": reasons}),
                version,
                tree,
            ),
        )
