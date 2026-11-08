"""Status of the six Kalshi DEMO caller paths named in the rank-1 review.

None of these modules is imported. Each one still calls the live place_order,
which mints a client id at POST, and each one reads the environment or a
service client. A line number is not integration. The status is blocked.
"""

from __future__ import annotations

CALLER_PATHS = (
    {
        "id": "runner_game",
        "source": "kalshi-demo/runner.py:162-167",
        "status": "blocked",
        "reason": "runner reads DEMO_DRY, positions(), and logs after the live place_order",
    },
    {
        "id": "runner_props",
        "source": "kalshi-demo/runner.py:211-216",
        "status": "blocked",
        "reason": "props loop reads DEMO_DRY and logs after the live place_order",
    },
    {
        "id": "me_kalshi",
        "source": "kalshi-demo/me_kalshi.py:344-351",
        "status": "blocked",
        "reason": "me_kalshi reads switch files and logs after the live place_order",
    },
    {
        "id": "live_runner",
        "source": "kalshi-demo/live_runner.py:111-115",
        "status": "blocked",
        "reason": "live_runner GETs the market through the live client before place_order",
    },
    {
        "id": "nba_pacer",
        "source": "kalshi-demo/kalshi_nba_pacer.py:196-200",
        "status": "blocked",
        "reason": "nba pacer GETs the market through the live client and reads KALSHI_NBA_LIVE_MIN10",
    },
    {
        "id": "marv_cbb",
        "source": "kalshi-demo/marv_cbb_kalshi.py:57-61",
        "status": "blocked",
        "reason": "marv_cbb trade() reads yaml, positions(), and calls the live place_order",
    },
    {
        "id": "live_props",
        "source": "marv-feed/live_props/live_props.py:273-278",
        "status": "blocked",
        "reason": "live_props builds the live client, reads trade switches, and logs after place_order",
    },
)


def blocked_ids() -> tuple[str, ...]:
    return tuple(item["id"] for item in CALLER_PATHS if item["status"] == "blocked")
