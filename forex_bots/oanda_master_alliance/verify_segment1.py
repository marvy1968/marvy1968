from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from broker.oanda_v20 import (
    OandaClient,
)
from core.common import (
    ROOT,
    load_config,
    load_environment,
)
from core.indicators import (
    build_indicator_frame,
)
from market.regime_engine import (
    RegimeEngine,
)
from market.session_engine import (
    active_sessions,
    primary_session,
)


def synthetic_frame(
    bars: int = 500,
) -> pd.DataFrame:
    rng = np.random.default_rng(42)

    timestamps = pd.date_range(
        end=pd.Timestamp.now(tz="UTC"),
        periods=bars,
        freq="5min",
    )

    change = (
        rng.normal(
            0.00005,
            0.00030,
            bars,
        )
    )

    close = (
        1.1000
        + np.cumsum(change)
    )

    open_price = np.concatenate(
        [
            close[:1],
            close[:-1],
        ]
    )

    high = np.maximum(
        open_price,
        close,
    ) + np.abs(
        rng.normal(
            0.00015,
            0.00005,
            bars,
        )
    )

    low = np.minimum(
        open_price,
        close,
    ) - np.abs(
        rng.normal(
            0.00015,
            0.00005,
            bars,
        )
    )

    volume = rng.integers(
        50,
        500,
        bars,
    )

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def main() -> None:
    load_environment()
    config = load_config()

    required_files = [
        ROOT / "config" / "master_ai.yaml",
        ROOT / "core" / "common.py",
        ROOT / "core" / "models.py",
        ROOT / "core" / "storage.py",
        ROOT / "core" / "indicators.py",
        ROOT / "broker" / "oanda_v20.py",
        ROOT / "market" / "data_engine.py",
        ROOT / "market" / "regime_engine.py",
        ROOT / "market" / "currency_strength.py",
        ROOT / "market" / "session_engine.py",
        ROOT / "run_foundation_snapshot.py",
    ]

    missing = [
        str(path)
        for path in required_files
        if not path.exists()
    ]

    if missing:
        raise SystemExit(
            "Missing files:\n"
            + "\n".join(missing)
        )

    frame = synthetic_frame()

    indicator_frame = (
        build_indicator_frame(
            frame,
            config["regime"],
        )
    )

    if indicator_frame[
        "adx"
    ].dropna().empty:
        raise SystemExit(
            "ADX calculation failed"
        )

    regime_engine = RegimeEngine(
        config
    )

    result = (
        regime_engine
        .classify_timeframe(
            "M5",
            frame,
        )
    )

    sessions = active_sessions(
        config["sessions"]
    )

    client = OandaClient(
        environment="practice",
        timeout_seconds=5,
        maximum_retries=1,
    )

    report = {
        "status": "passed",
        "configuration_loaded": True,
        "required_files": len(
            required_files
        ),
        "indicator_rows": len(
            indicator_frame
        ),
        "synthetic_regime": (
            result.regime
        ),
        "synthetic_direction": (
            result.direction
        ),
        "synthetic_trend_votes": (
            result.trend_votes
        ),
        "synthetic_range_votes": (
            result.range_votes
        ),
        "active_sessions": sessions,
        "primary_session": (
            primary_session(
                sessions
            )
        ),
        "credentials_present": (
            client.credentials_present
        ),
        "environment": (
            client.environment
        ),
        "live_access_locked": True,
        "automatic_execution": False,
    }

    print()
    print(
        "OANDA MASTER AI — SEGMENT 1"
    )
    print("=" * 72)
    print(
        json.dumps(
            report,
            indent=2,
        )
    )
    print()
    print(
        "Segment 1 foundation verification passed."
    )


if __name__ == "__main__":
    main()
