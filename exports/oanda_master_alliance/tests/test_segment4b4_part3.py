from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from segment4b4.campaign_health import (
    parse_timestamp,
)


def test_parse_timestamp() -> None:
    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    parsed = parse_timestamp(
        timestamp
    )

    assert parsed is not None
    assert parsed.tzinfo is not None


def test_invalid_timestamp() -> None:
    assert (
        parse_timestamp(
            "not-a-timestamp"
        )
        is None
    )


def test_old_timestamp() -> None:
    value = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            hours=1
        )
    ).isoformat()

    parsed = parse_timestamp(
        value
    )

    assert parsed is not None
