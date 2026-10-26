from __future__ import annotations

import json
import sys
from pathlib import Path

from broker.oanda_v20 import (
    OandaClient,
)
from core.common import (
    ROOT,
    append_jsonl,
    atomic_write_json,
    ensure_directories,
    load_config,
    load_environment,
    utc_now,
)
from core.models import MarketSnapshot
from market.currency_strength import (
    CurrencyStrengthEngine,
)
from market.data_engine import (
    MarketDataEngine,
)
from market.regime_engine import (
    RegimeEngine,
)
from market.session_engine import (
    active_sessions,
    primary_session,
)


def main() -> None:
    ensure_directories()
    load_environment()

    config = load_config()

    oanda_settings = config["oanda"]

    client = OandaClient(
        environment=str(
            oanda_settings[
                "environment"
            ]
        ),
        timeout_seconds=int(
            oanda_settings[
                "request_timeout_seconds"
            ]
        ),
        maximum_retries=int(
            oanda_settings[
                "maximum_retries"
            ]
        ),
        retry_delay_seconds=int(
            oanda_settings[
                "retry_delay_seconds"
            ]
        ),
    )

    if not client.credentials_present:
        raise SystemExit(
            "OANDA credentials are missing. "
            "Edit ~/oanda_master_alliance/.env"
        )

    data_engine = MarketDataEngine(
        client,
        config,
    )

    datasets = (
        data_engine.refresh_all()
    )

    quotes = (
        data_engine.current_quotes()
    )

    regime_engine = RegimeEngine(
        config
    )

    regimes = {}

    errors: list[str] = []

    for instrument, frames in (
        datasets.items()
    ):
        try:
            regimes[instrument] = (
                regime_engine.classify_pair(
                    instrument,
                    frames,
                )
            )

        except Exception as exc:
            errors.append(
                f"{instrument}:"
                f"{type(exc).__name__}:"
                f"{exc}"
            )

    strength_engine = (
        CurrencyStrengthEngine(
            lookback_bars=12
        )
    )

    strength = strength_engine.calculate(
        datasets,
        timeframe=config[
            "timeframes"
        ]["structure"],
    )

    sessions = active_sessions(
        config["sessions"]
    )

    maximum_spreads = (
        config[
            "data_quality"
        ]["maximum_spread_pips"]
    )

    for instrument, quote in (
        quotes.items()
    ):
        maximum = float(
            maximum_spreads.get(
                instrument,
                999.0,
            )
        )

        if (
            quote.spread_pips
            > maximum
        ):
            errors.append(
                f"{instrument}:spread_"
                f"{quote.spread_pips:.2f}_"
                f"above_{maximum:.2f}"
            )

        if not quote.tradeable:
            errors.append(
                f"{instrument}:not_tradeable"
            )

    data_quality_passed = (
        not errors
        and all(
            regime.data_quality_passed
            for regime in regimes.values()
        )
        and len(regimes)
        == len(config["pairs"])
    )

    snapshot = MarketSnapshot(
        timestamp=utc_now().isoformat(),
        account_environment=(
            client.environment
        ),
        session=primary_session(
            sessions
        ),
        active_sessions=sessions,
        quotes=quotes,
        regimes=regimes,
        currency_strength=strength,
        data_quality_passed=(
            data_quality_passed
        ),
        errors=errors,
    )

    snapshot_path = (
        ROOT
        / config["logging"][
            "market_snapshot"
        ]
    )

    regime_path = (
        ROOT
        / config["logging"][
            "regime_snapshot"
        ]
    )

    strength_path = (
        ROOT
        / config["logging"][
            "currency_strength_snapshot"
        ]
    )

    atomic_write_json(
        snapshot_path,
        snapshot.to_dict(),
    )

    atomic_write_json(
        regime_path,
        {
            instrument: regime.to_dict()
            for instrument, regime
            in regimes.items()
        },
    )

    atomic_write_json(
        strength_path,
        strength.to_dict(),
    )

    append_jsonl(
        ROOT
        / config["logging"][
            "event_log"
        ],
        {
            "timestamp": (
                utc_now().isoformat()
            ),
            "event": (
                "foundation_snapshot_complete"
            ),
            "pairs": len(regimes),
            "session": snapshot.session,
            "data_quality_passed": (
                data_quality_passed
            ),
            "errors": errors,
        },
    )

    summary = {
        "timestamp": snapshot.timestamp,
        "environment": (
            snapshot.account_environment
        ),
        "session": snapshot.session,
        "active_sessions": (
            snapshot.active_sessions
        ),
        "pairs": len(regimes),
        "quotes": len(quotes),
        "data_quality_passed": (
            data_quality_passed
        ),
        "errors": errors,
        "regimes": {
            instrument: {
                "regime": regime.regime,
                "direction": (
                    regime.direction
                ),
                "confidence": round(
                    regime.confidence,
                    4,
                ),
                "confirmations": (
                    regime.confirmations
                ),
                "data_quality": (
                    regime
                    .data_quality_passed
                ),
            }
            for instrument, regime
            in regimes.items()
        },
        "currency_strength": {
            currency: round(
                value,
                4,
            )
            for currency, value
            in strength.values.items()
        },
    }

    print(
        json.dumps(
            summary,
            indent=2,
        )
    )

    if not data_quality_passed:
        sys.exit(2)


if __name__ == "__main__":
    main()
