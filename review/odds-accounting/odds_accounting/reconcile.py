"""Compare per-call header costs with account-wide counter snapshots.

Per-consumer credits are the sum of valid x-requests-last values only.
A change in x-requests-used is a global snapshot delta. It is not assigned
to the consumer whose response happened to carry the later snapshot, and a
decrease is not reported as a refund. External traffic, out-of-order
responses, and the provider's UTC month reset all stay unknown.
"""

from __future__ import annotations

from pathlib import Path

from .ledger import read_state


def reconcile(path: str | Path, *, day_utc: str | None = None) -> dict:
    state = read_state(Path(path))
    if not state["ok"]:
        return {
            "ok": False,
            "reason": state["reason"],
            "account_linkage": "unverified",
            "refunds_reported": 0,
            "request_count_is_not_credit_count": True,
            "local_shadow_budget_is_not_global_quota": True,
            "by_account_label": {},
        }
    attempts = state["attempts"]
    samples = state["samples"]
    if day_utc is not None:
        attempts = [row for row in attempts if row["day_utc"] == day_utc]
        samples = [row for row in samples if str(row["observed_at_utc"])[:10] == day_utc]
    labels = {(row.get("account_label") or "") for row in attempts}
    labels.update((row.get("account_label") or "") for row in samples)
    grouped = {}
    for label in sorted(labels):
        name = label or "unspecified"
        grouped[name] = _reconcile_label(
            [row for row in attempts if (row.get("account_label") or "") == label],
            [row for row in samples if (row.get("account_label") or "") == label],
        )
    return {
        "ok": True,
        "reason": None,
        "account_linkage": "unverified",
        "refunds_reported": 0,
        "request_count_is_not_credit_count": True,
        "local_shadow_budget_is_not_global_quota": True,
        "by_account_label": grouped,
    }


def _reconcile_label(attempts: list[dict], samples: list[dict]) -> dict:
    physical = [row for row in attempts if row["is_physical"] == 1]
    by_job: dict[str, int] = {}
    by_consumer: dict[str, int] = {}
    for row in physical:
        if row["actual_status"] != "header":
            continue
        cost = row["actual_header_cost"]
        if not isinstance(cost, int) or isinstance(cost, bool) or cost < 0:
            continue
        by_job[row["source_job"]] = by_job.get(row["source_job"], 0) + cost
        by_consumer[row["consumer_id"]] = by_consumer.get(row["consumer_id"], 0) + cost
    attributed = sum(by_job.values())
    notes: list[str] = []
    invalid = [row for row in samples if row["used_status"] not in ("header", "missing")]
    numeric = [row for row in samples if row["used_status"] == "header" and isinstance(row["shared_used"], int)
               and not isinstance(row["shared_used"], bool)]
    delta = None
    status = "unknown"
    unattributed = None
    if invalid:
        notes.append("invalid_global_counter_unknown")
    elif len(numeric) < 2:
        notes.append("insufficient_global_snapshots")
    else:
        ordered = sorted(numeric, key=lambda row: (row["observed_at_utc"], row["sample_id"]))
        decreased = False
        for previous, nxt in zip(ordered, ordered[1:]):
            if nxt["shared_used"] < previous["shared_used"]:
                decreased = True
                if nxt["month_utc"] != previous["month_utc"]:
                    notes.append("possible_utc_month_reset_unknown")
                else:
                    notes.append("stale_or_out_of_order_or_external_unknown")
        if decreased:
            status = "unknown"
        else:
            delta = ordered[-1]["shared_used"] - ordered[0]["shared_used"]
            gap = delta - attributed
            if gap == 0:
                status = "consistent_with_header_actuals"
                unattributed = 0
            elif gap > 0:
                status = "unattributed_unknown"
                unattributed = gap
                notes.append("gap_not_assigned_to_any_consumer")
            else:
                status = "unknown"
                notes.append("snapshot_delta_below_header_sum_not_a_refund")
    return {
        "physical_attempts": len(physical),
        "skipped_preflight": sum(1 for row in attempts if row["outcome"] == "skipped_preflight"),
        "refused_historical": sum(1 for row in attempts if row["outcome"] == "refused_historical"),
        "legacy_imports": sum(1 for row in attempts if row["legacy_import"] == 1),
        "retry_attempts": sum(1 for row in physical if row["attempt_ordinal"] > 1),
        "logical_requests": len({row["logical_request_id"] for row in physical}),
        "header_actual_by_source_job": by_job,
        "header_actual_by_consumer": by_consumer,
        "header_actual_total": attributed,
        "estimated_cost_sum_not_actual": sum(
            row["estimated_cost"] for row in physical if isinstance(row["estimated_cost"], int)),
        "unknown_actual_attempts": sum(1 for row in physical if row["actual_status"] != "header"),
        "legacy_costs_are_not_actual": True,
        "global_counter_delta_not_a_consumer_bill": delta,
        "unattributed_status": status,
        "unattributed_amount": unattributed,
        "unattributed_assigned_to": None,
        "refunds_reported": 0,
        "notes": notes,
    }
