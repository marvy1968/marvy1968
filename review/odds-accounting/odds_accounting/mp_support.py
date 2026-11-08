"""Helpers for cross-process ledger tests. Not a service and not scheduled."""

from .boundary import observe_exchange


def write_attempts(ledger_path: str, count: int, source_job: str, series: int, actual: int) -> None:
    for index in range(count):
        observe_exchange(
            source_job=source_job,
            endpoint_template="/v4/sports/{sport}/odds",
            sport="synthetic_sport",
            markets=["h2h"],
            regions=["us"],
            estimated_cost=1,
            estimate_provenance="test",
            account_label="synthetic",
            http_status=200,
            headers={
                "x-requests-last": str(actual),
                "x-requests-used": str(1000 + series + index),
                "x-requests-remaining": "100",
            },
            attempt_id=f"{series:08x}{index:024x}",
            logical_request_id=f"{series:08x}{(index + 1):024x}",
            attempt_ordinal=1,
            ledger_path=ledger_path,
        )


def write_fixed(ledger_path: str, attempt_id: str, source_job: str, actual: str) -> None:
    observe_exchange(
        source_job=source_job,
        endpoint_template="/v4/sports/{sport}/odds",
        sport="synthetic_sport",
        markets=["h2h"],
        regions=["us"],
        estimated_cost=1,
        estimate_provenance="test",
        account_label="synthetic",
        http_status=200,
        headers={"x-requests-last": actual, "x-requests-used": "50", "x-requests-remaining": "50"},
        attempt_id=attempt_id,
        logical_request_id="c0ffee00c0ffee00c0ffee00c0ffee00",
        ledger_path=ledger_path,
    )
