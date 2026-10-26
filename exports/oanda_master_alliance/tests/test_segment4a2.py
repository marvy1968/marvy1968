from __future__ import annotations

from pathlib import Path

from segment4.duplicate_registry import (
    DuplicateRegistry,
)
from segment4.watchdog import (
    RestartHistory,
)


def test_restart_history_limit(
    tmp_path: Path,
) -> None:
    history = RestartHistory(
        tmp_path
        / "restart_history.json",
        {
            "maximum_restart_attempts": 1,
            "restart_window_minutes": 60,
            "restart_backoff_seconds": [
                1,
            ],
        },
    )

    assert history.allowed()

    history.record(
        reason="test",
        pid=123,
        successful_launch=True,
    )

    assert not history.allowed()


def test_duplicate_ids_are_stable() -> None:
    registry = DuplicateRegistry()

    first = registry.make_signal_id(
        instrument="EUR_USD",
        side="buy",
        setup="trend_pullback",
        entry_price=1.12345,
        source_timestamp=(
            "2026-07-15T00:00:00+00:00"
        ),
    )

    second = registry.make_signal_id(
        instrument="EUR_USD",
        side="buy",
        setup="trend_pullback",
        entry_price=1.12345,
        source_timestamp=(
            "2026-07-15T00:00:00+00:00"
        ),
    )

    assert first == second
