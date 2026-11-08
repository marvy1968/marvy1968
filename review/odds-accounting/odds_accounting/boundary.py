"""Record one Odds API observation. This function does not raise."""

from __future__ import annotations

import json
import os
import uuid
import zlib
from datetime import datetime, timezone

from .endpoints import classify_endpoint
from .headers import cost_basis, extract_usage_headers, parse_count
from .ledger import insert_attempt, ledger_path_from_env, new_id, record_observation_error
from .redact import (
    FORBIDDEN_ARGUMENT_NAMES,
    LEAK,
    SOURCE_JOB,
    exception_types,
    safe_id,
    safe_token,
    safe_tokens,
)

_BOOT_TOKEN = uuid.uuid4().hex


def import_legacy_usage_row(*, source_job: str, endpoint_template: str, legacy_row_id: int,
                            legacy_cost, sport: str | None = None, strategy: str | None = None,
                            account_label: str | None = None, observed_at: datetime | None = None,
                            ledger_path=None) -> dict:
    """Copy one old api_usage row into the new ledger without calling it actual.

    The historical cost column mixes header values and estimates. Both stay in
    legacy_cost_unlabeled. actual_header_cost stays empty, including when the
    old cost was 0. This does not update the historical table.
    """
    if isinstance(legacy_row_id, bool) or not isinstance(legacy_row_id, int) or legacy_row_id < 0:
        return {"ok": False, "stored": False, "reason": "rejected", "attempt_id": None, "duplicate": False}
    cost = legacy_cost if isinstance(legacy_cost, int) and not isinstance(legacy_cost, bool) else None
    job_bits = zlib.crc32(source_job.encode("utf-8")) & 0xFFFFFFFF
    attempt_id = f"{job_bits:08x}{legacy_row_id:024x}"
    logical_request_id = f"{job_bits:08x}{(legacy_row_id + 1):024x}"
    if len(attempt_id) != 32 or len(logical_request_id) != 32:
        return {"ok": False, "stored": False, "reason": "rejected", "attempt_id": None, "duplicate": False}
    return observe_exchange(
        source_job=source_job,
        endpoint_template=endpoint_template,
        sport=sport,
        strategy=strategy,
        account_label=account_label,
        legacy_import=True,
        legacy_cost_unlabeled=cost,
        estimated_cost=None,
        estimate_provenance="legacy_unlabeled_not_actual",
        attempt_id=attempt_id,
        logical_request_id=logical_request_id,
        observed_at=observed_at,
        ledger_path=ledger_path,
    )


def observe_exchange(**kwargs) -> dict:
    try:
        leaked = False
        for key in list(kwargs):
            if key.lower() in FORBIDDEN_ARGUMENT_NAMES:
                kwargs.pop(key, None)
                leaked = True
        result = _observe_exchange(**kwargs)
        if leaked and result.get("stored") and result.get("attempt_id"):
            record_observation_error(result.get("_path"), result["attempt_id"], "redacted_argument")
        result.pop("_path", None)
        return result
    except Exception:
        return {"ok": False, "stored": False, "reason": "internal", "attempt_id": None, "duplicate": False}


def _observe_exchange(
    *,
    source_job: str,
    endpoint_path: str | None = None,
    endpoint_template: str | None = None,
    sport: str | None = None,
    markets=None,
    regions=None,
    bookmakers=None,
    estimated_cost: int | None = None,
    estimate_provenance: str = "not_supplied",
    strategy: str | None = None,
    account_label: str | None = None,
    historical: bool | None = None,
    odds_format: str | None = None,
    logical_request_id: str | None = None,
    attempt_id: str | None = None,
    attempt_ordinal: int = 1,
    cycle_id: str | None = None,
    http_status: int | None = None,
    headers=None,
    transport_error: BaseException | None = None,
    preflight: str | None = None,
    shadow_reservation_id: str | None = None,
    legacy_cost_unlabeled: int | None = None,
    legacy_import: bool = False,
    ledger_path=None,
    observed_at: datetime | None = None,
) -> dict:
    path = ledger_path if ledger_path else ledger_path_from_env()
    if path is None:
        return {"ok": True, "stored": False, "reason": "ledger_unconfigured", "attempt_id": None,
                "duplicate": False}
    from pathlib import Path
    path = Path(path)
    if observed_at is None:
        observed_at = datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        return {"ok": False, "stored": False, "reason": "naive_timestamp", "attempt_id": None,
                "duplicate": False, "_path": path}
    observed_at = observed_at.astimezone(timezone.utc)
    stamp = observed_at.isoformat()
    job = source_job if isinstance(source_job, str) and SOURCE_JOB.fullmatch(source_job) else "rejected-source"
    template, path_sport, path_historical, accepted = classify_endpoint(
        endpoint_path, endpoint_template, sport, historical)
    if not accepted or template is None:
        template = "rejected-endpoint"
        path_sport = None
        path_historical = False
    kept_sport = safe_token(sport) if isinstance(sport, str) else None
    if kept_sport is None:
        kept_sport = path_sport
    market_list, market_drop = safe_tokens(markets)
    region_list, region_drop = safe_tokens(regions)
    book_list, book_drop = safe_tokens(bookmakers)
    estimate, estimate_problem = _clean_estimate(estimated_cost)
    provenance = safe_token(estimate_provenance) or "not_supplied"
    if estimate_problem:
        provenance = estimate_problem
    label = safe_token(account_label) if isinstance(account_label, str) else None
    strategy_token = safe_token(strategy) if isinstance(strategy, str) else None
    odds = odds_format if odds_format in ("american", "decimal") else None
    logical = safe_id(logical_request_id) or new_id()
    attempt = safe_id(attempt_id) or new_id()
    cycle = safe_id(cycle_id) or _BOOT_TOKEN
    reservation = safe_token(shadow_reservation_id) if isinstance(shadow_reservation_id, str) else None
    ordinal = attempt_ordinal if isinstance(attempt_ordinal, int) and not isinstance(attempt_ordinal, bool) and attempt_ordinal >= 0 else 1
    status = http_status if isinstance(http_status, int) and not isinstance(http_status, bool) and 100 <= http_status <= 599 else None
    exc = transport_error if isinstance(transport_error, BaseException) else None
    exc_type, exc_cause = exception_types(exc)
    usage = extract_usage_headers(headers)
    last_value, last_status = (None, "missing")
    used_value, used_status = (None, "missing")
    remaining_value, remaining_status = (None, "missing")
    if usage:
        last_value, last_status = parse_count(usage.get("x-requests-last"))
        used_value, used_status = parse_count(usage.get("x-requests-used"))
        remaining_value, remaining_status = parse_count(usage.get("x-requests-remaining"))
        if "x-requests-last" not in usage:
            last_value, last_status = None, "missing"
        if "x-requests-used" not in usage:
            used_value, used_status = None, "missing"
        if "x-requests-remaining" not in usage:
            remaining_value, remaining_status = None, "missing"
    legacy = _clean_legacy(legacy_cost_unlabeled) if legacy_import else None
    outcome, physical, actual, actual_status, basis = _classify(
        preflight=preflight,
        legacy_import=bool(legacy_import),
        transport_failed=exc is not None and status is None,
        http_status=status,
        last_value=last_value,
        last_status=last_status,
        estimated_cost=estimate,
    )
    if book_list:
        region_units = max(1, (len(book_list) + 9) // 10)
    elif region_list:
        region_units = len(region_list)
    else:
        region_units = None
    shape = {
        "market_count": len(market_list),
        "region_count": len(region_list),
        "bookmaker_count": len(book_list),
        "region_units": region_units,
        "historical": bool(path_historical),
        "odds_format": odds,
    }
    consumer = f"{job}:{os.getpid()}:{_BOOT_TOKEN[:12]}"
    row = {
        "attempt_id": attempt,
        "logical_request_id": logical,
        "attempt_ordinal": 0 if physical == 0 else ordinal,
        "observed_at_utc": stamp,
        "day_utc": stamp[:10],
        "month_utc": stamp[:7],
        "cycle_id": cycle,
        "source_job": job,
        "consumer_id": consumer,
        "strategy": strategy_token,
        "endpoint_template": template,
        "sport": kept_sport,
        "markets_json": json.dumps(market_list, separators=(",", ":")),
        "regions_json": json.dumps(region_list, separators=(",", ":")),
        "bookmakers_json": json.dumps(book_list, separators=(",", ":")),
        "request_shape_json": json.dumps(shape, sort_keys=True, separators=(",", ":")),
        "account_label": label,
        "http_status": status if physical else None,
        "outcome": outcome,
        "is_physical": physical,
        "estimated_cost": estimate,
        "estimate_provenance": provenance,
        "actual_header_cost": actual,
        "actual_status": actual_status,
        "cost_basis": basis,
        "shared_used": used_value if physical else None,
        "shared_used_status": used_status if physical else "missing",
        "shared_remaining": remaining_value if physical else None,
        "shared_remaining_status": remaining_status if physical else "missing",
        "exception_type": exc_type if physical else None,
        "exception_cause_type": exc_cause if physical else None,
        "shadow_reservation_id": reservation,
        "legacy_import": 1 if legacy_import else 0,
        "legacy_cost_unlabeled": legacy,
        "actual_filled_at_utc": None,
        "inserted_at_utc": stamp,
    }
    if any(isinstance(value, str) and LEAK.search(value) for value in row.values()):
        return {"ok": False, "stored": False, "reason": "redacted", "attempt_id": None,
                "duplicate": False, "_path": path}
    result = insert_attempt(path, row)
    result["_path"] = path
    if market_drop or region_drop or book_drop or template == "rejected-endpoint" or job == "rejected-source":
        record_observation_error(path, result.get("attempt_id"), "redacted_field")
    return result


def _clean_estimate(value):
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int):
        return None, "estimate_rejected_not_int"
    if value < 0:
        return None, "negative_estimate_rejected"
    return value, None


def _clean_legacy(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _classify(*, preflight, legacy_import, transport_failed, http_status, last_value, last_status,
              estimated_cost):
    if legacy_import:
        return "legacy_import", 0, None, "legacy_unlabeled", "legacy_unlabeled_not_actual"
    if preflight == "budget":
        basis = "estimate_only_not_actual" if estimated_cost is not None else "unknown"
        return "skipped_preflight", 0, None, "not_applicable", basis
    if preflight == "historical_disabled":
        return "refused_historical", 0, None, "not_applicable", "historical_refused"
    if transport_failed or http_status is None:
        return "transport_exception", 1, None, "absent_transport", "unknown"
    outcome = "success" if http_status == 200 else "http_error"
    if last_status == "header":
        actual, actual_status = last_value, "header"
    else:
        actual, actual_status = None, last_status
    basis = cost_basis(actual_status, estimated_cost, http_status, False)
    return outcome, 1, actual, actual_status, basis
