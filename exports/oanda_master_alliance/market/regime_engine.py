from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from core.common import utc_now
from core.indicators import (
    build_indicator_frame,
)
from core.models import (
    IndicatorSnapshot,
    PairRegime,
)


class RegimeEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config
        self.settings = config["regime"]

    def classify_timeframe(
        self,
        timeframe: str,
        frame: pd.DataFrame,
    ) -> IndicatorSnapshot:
        indicator_frame = (
            build_indicator_frame(
                frame,
                self.settings,
            )
        )

        valid = indicator_frame.dropna(
            subset=[
                "ema_fast",
                "ema_slow",
                "ema_context",
                "atr",
                "adx",
                "plus_di",
                "minus_di",
                "rsi",
                "bollinger_upper",
                "bollinger_middle",
                "bollinger_lower",
                "bollinger_width",
                "price_slope",
            ]
        )

        if valid.empty:
            raise ValueError(
                f"{timeframe} has insufficient "
                "indicator history"
            )

        row = valid.iloc[-1]

        trend_votes = 0
        range_votes = 0
        reasons: list[str] = []

        close = float(row["close"])
        ema_fast = float(row["ema_fast"])
        ema_slow = float(row["ema_slow"])
        ema_context = float(
            row["ema_context"]
        )

        adx_value = float(row["adx"])
        plus_di = float(row["plus_di"])
        minus_di = float(row["minus_di"])
        rsi_value = float(row["rsi"])
        slope = float(row["price_slope"])

        atr_percentile = float(
            row.get(
                "atr_percentile",
                np.nan,
            )
        )

        width_percentile = float(
            row.get(
                "bollinger_width_percentile",
                np.nan,
            )
        )

        direction = "mixed"

        bullish_stack = (
            close
            > ema_fast
            > ema_slow
            > ema_context
        )

        bearish_stack = (
            close
            < ema_fast
            < ema_slow
            < ema_context
        )

        partial_bullish = (
            close > ema_fast
            and ema_fast > ema_slow
        )

        partial_bearish = (
            close < ema_fast
            and ema_fast < ema_slow
        )

        if bullish_stack:
            trend_votes += 2
            direction = "up"
            reasons.append(
                "full_bullish_ema_stack"
            )

        elif bearish_stack:
            trend_votes += 2
            direction = "down"
            reasons.append(
                "full_bearish_ema_stack"
            )

        elif partial_bullish:
            trend_votes += 1
            direction = "up"
            reasons.append(
                "partial_bullish_ema_stack"
            )

        elif partial_bearish:
            trend_votes += 1
            direction = "down"
            reasons.append(
                "partial_bearish_ema_stack"
            )

        else:
            range_votes += 1
            reasons.append(
                "ema_structure_mixed"
            )

        if adx_value >= float(
            self.settings[
                "adx_strong_trending"
            ]
        ):
            trend_votes += 2
            reasons.append(
                "strong_adx"
            )

        elif adx_value >= float(
            self.settings[
                "adx_trending"
            ]
        ):
            trend_votes += 1
            reasons.append(
                "trending_adx"
            )

        elif adx_value <= float(
            self.settings[
                "adx_ranging"
            ]
        ):
            range_votes += 2
            reasons.append(
                "low_adx"
            )

        if plus_di > minus_di * 1.15:
            trend_votes += 1

            if direction == "mixed":
                direction = "up"

            reasons.append(
                "positive_directional_movement"
            )

        elif minus_di > plus_di * 1.15:
            trend_votes += 1

            if direction == "mixed":
                direction = "down"

            reasons.append(
                "negative_directional_movement"
            )

        else:
            range_votes += 1
            reasons.append(
                "balanced_directional_movement"
            )

        if abs(slope) >= 0.004:
            trend_votes += 1
            reasons.append(
                "meaningful_price_slope"
            )

            if direction == "mixed":
                direction = (
                    "up"
                    if slope > 0
                    else "down"
                )

        elif abs(slope) <= 0.0015:
            range_votes += 1
            reasons.append(
                "flat_price_slope"
            )

        if (
            42
            <= rsi_value
            <= 58
        ):
            range_votes += 1
            reasons.append(
                "neutral_rsi"
            )

        elif (
            rsi_value >= 60
            and direction == "up"
        ):
            trend_votes += 1
            reasons.append(
                "bullish_rsi_confirmation"
            )

        elif (
            rsi_value <= 40
            and direction == "down"
        ):
            trend_votes += 1
            reasons.append(
                "bearish_rsi_confirmation"
            )

        compression_threshold = float(
            self.settings[
                "compression_percentile"
            ]
        )

        expansion_threshold = float(
            self.settings[
                "expansion_percentile"
            ]
        )

        compression = (
            np.isfinite(width_percentile)
            and width_percentile
            <= compression_threshold
        )

        expansion = (
            np.isfinite(atr_percentile)
            and atr_percentile
            >= expansion_threshold
        )

        if compression:
            range_votes += 2
            reasons.append(
                "bollinger_compression"
            )

        if expansion:
            trend_votes += 1
            reasons.append(
                "atr_expansion"
            )

        strong_votes = int(
            self.settings[
                "strong_trend_votes"
            ]
        )

        minimum_trend = int(
            self.settings[
                "minimum_trend_votes"
            ]
        )

        minimum_range = int(
            self.settings[
                "minimum_range_votes"
            ]
        )

        if (
            trend_votes >= strong_votes
            and trend_votes
            >= range_votes + 2
        ):
            regime = "strong_trending"

        elif (
            trend_votes
            >= minimum_trend
            and trend_votes
            > range_votes
        ):
            regime = "trending"

        elif (
            compression
            and range_votes
            >= minimum_range
        ):
            regime = "compression"

        elif (
            range_votes
            >= minimum_range
            and range_votes
            > trend_votes
        ):
            regime = "ranging"

        elif (
            expansion
            and trend_votes
            >= minimum_trend - 1
        ):
            regime = "breakout_transition"

        else:
            regime = "mixed"

        if regime in {
            "ranging",
            "compression",
            "mixed",
        } and trend_votes <= range_votes:
            direction = "mixed"

        return IndicatorSnapshot(
            timeframe=timeframe,
            timestamp=(
                row["timestamp"]
                .isoformat()
            ),
            close=close,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_context=ema_context,
            atr=float(row["atr"]),
            atr_percentile=(
                atr_percentile
                if np.isfinite(
                    atr_percentile
                )
                else 0.0
            ),
            adx=adx_value,
            plus_di=plus_di,
            minus_di=minus_di,
            rsi=rsi_value,
            bollinger_upper=float(
                row["bollinger_upper"]
            ),
            bollinger_middle=float(
                row["bollinger_middle"]
            ),
            bollinger_lower=float(
                row["bollinger_lower"]
            ),
            bollinger_width=float(
                row["bollinger_width"]
            ),
            bollinger_width_percentile=(
                width_percentile
                if np.isfinite(
                    width_percentile
                )
                else 0.0
            ),
            price_slope=slope,
            volume_ratio=float(
                row.get(
                    "volume_ratio",
                    0.0,
                )
            ),
            trend_votes=trend_votes,
            range_votes=range_votes,
            direction=direction,
            regime=regime,
            reasons=reasons,
        )

    def classify_pair(
        self,
        instrument: str,
        datasets: dict[
            str,
            pd.DataFrame,
        ],
    ) -> PairRegime:
        timeframe_order = [
            self.config[
                "timeframes"
            ]["context"],
            self.config[
                "timeframes"
            ]["structure"],
            self.config[
                "timeframes"
            ]["confirmation"],
            self.config[
                "timeframes"
            ]["execution"],
        ]

        timeframe_order = list(
            dict.fromkeys(
                timeframe_order
            )
        )

        results: list[
            IndicatorSnapshot
        ] = []

        rejection_reasons: list[str] = []

        minimum_bars = (
            self.config[
                "data_quality"
            ]["minimum_bars"]
        )

        maximum_age = (
            self.config[
                "data_quality"
            ]["maximum_age_seconds"]
        )

        now = pd.Timestamp.now(tz="UTC")

        for timeframe in timeframe_order:
            frame = datasets.get(
                timeframe
            )

            if frame is None:
                rejection_reasons.append(
                    f"missing_{timeframe}"
                )
                continue

            required = int(
                minimum_bars[
                    timeframe
                ]
            )

            if len(frame) < required:
                rejection_reasons.append(
                    f"{timeframe}_bars_"
                    f"{len(frame)}_below_"
                    f"{required}"
                )
                continue

            latest = pd.to_datetime(
                frame["timestamp"].max(),
                utc=True,
            )

            age = float(
                (
                    now
                    - latest
                ).total_seconds()
            )

            age_limit = int(
                maximum_age[
                    timeframe
                ]
            )

            if age > age_limit:
                rejection_reasons.append(
                    f"{timeframe}_stale_"
                    f"{age:.0f}s"
                )

            try:
                result = (
                    self.classify_timeframe(
                        timeframe,
                        frame,
                    )
                )

                results.append(result)

            except Exception as exc:
                rejection_reasons.append(
                    f"{timeframe}_"
                    f"{type(exc).__name__}:"
                    f"{exc}"
                )

        if not results:
            return PairRegime(
                instrument=instrument,
                timestamp=(
                    utc_now().isoformat()
                ),
                regime="unclassified",
                direction="mixed",
                confidence=0.0,
                confirmations=0,
                timeframe_results=[],
                data_quality_passed=False,
                rejection_reasons=(
                    rejection_reasons
                ),
            )

        trend_results = [
            item
            for item in results
            if item.regime
            in {
                "trending",
                "strong_trending",
                "breakout_transition",
            }
        ]

        range_results = [
            item
            for item in results
            if item.regime
            in {
                "ranging",
                "compression",
            }
        ]

        up_count = sum(
            item.direction == "up"
            for item in trend_results
        )

        down_count = sum(
            item.direction == "down"
            for item in trend_results
        )

        trend_confirmations = max(
            up_count,
            down_count,
        )

        range_confirmations = len(
            range_results
        )

        required_confirmations = int(
            self.settings[
                "required_timeframe_confirmations"
            ]
        )

        strong_confirmations = int(
            self.settings[
                "strong_required_confirmations"
            ]
        )

        if (
            trend_confirmations
            >= strong_confirmations
            and any(
                item.regime
                == "strong_trending"
                for item in trend_results
            )
        ):
            regime = "strong_trending"
            confirmations = (
                trend_confirmations
            )
            direction = (
                "up"
                if up_count > down_count
                else "down"
            )

        elif (
            trend_confirmations
            >= required_confirmations
        ):
            regime = "trending"
            confirmations = (
                trend_confirmations
            )
            direction = (
                "up"
                if up_count > down_count
                else "down"
            )

        elif (
            range_confirmations
            >= required_confirmations
        ):
            if any(
                item.regime
                == "compression"
                for item in range_results
            ):
                regime = "compression"
            else:
                regime = "ranging"

            confirmations = (
                range_confirmations
            )
            direction = "mixed"

        else:
            regime_counts = Counter(
                item.regime
                for item in results
            )

            regime = (
                regime_counts
                .most_common(1)[0][0]
            )

            confirmations = (
                regime_counts[
                    regime
                ]
            )

            direction = "mixed"

        confidence = min(
            1.0,
            (
                confirmations
                / max(
                    1,
                    len(results),
                )
            )
            * (
                1.0
                if regime
                != "mixed"
                else 0.60
            ),
        )

        data_quality_passed = (
            len(rejection_reasons)
            == 0
        )

        return PairRegime(
            instrument=instrument,
            timestamp=utc_now().isoformat(),
            regime=regime,
            direction=direction,
            confidence=confidence,
            confirmations=confirmations,
            timeframe_results=results,
            data_quality_passed=(
                data_quality_passed
            ),
            rejection_reasons=(
                rejection_reasons
            ),
        )
