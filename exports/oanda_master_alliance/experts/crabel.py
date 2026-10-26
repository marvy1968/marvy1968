from __future__ import annotations

from experts.base import ExpertEngine
from signals.context import MarketContext
from signals.models import ExpertSignal


class CrabelExpert(ExpertEngine):
    expert_id = "crabel"

    def evaluate(
        self,
        context: MarketContext,
    ) -> ExpertSignal:
        setup = "range_contraction_breakout"

        m15 = context.frame(
            "M15"
        )

        if len(m15) < 10:
            return self.rejected(
                context,
                setup,
                [
                    "insufficient_candles",
                ],
            )

        ranges = (
            m15["high"]
            - m15["low"]
        )

        latest_range = float(
            ranges.iloc[-1]
        )

        previous = ranges.iloc[
            -8:-1
        ]

        nr7 = (
            latest_range
            <= float(
                previous.min()
            )
        )

        inside_bar = (
            float(
                m15.iloc[-1][
                    "high"
                ]
            )
            <= float(
                m15.iloc[-2][
                    "high"
                ]
            )
            and float(
                m15.iloc[-1][
                    "low"
                ]
            )
            >= float(
                m15.iloc[-2][
                    "low"
                ]
            )
        )

        score = 25.0
        reasons: list[str] = []

        if nr7:
            score += 25.0
            reasons.append(
                "nr7_contraction"
            )

        if inside_bar:
            score += 20.0
            reasons.append(
                "inside_bar_contraction"
            )

        if context.regime in {
            "compression",
            "breakout_transition",
            "trending",
            "strong_trending",
        }:
            score += 12.0
            reasons.append(
                "regime_supports_expansion"
            )

        m5 = context.timeframe(
            "M5"
        )

        atr_percentile = (
            self.safe_float(
                m5.get(
                    "atr_percentile"
                )
            )
        )

        if atr_percentile >= 0.55:
            score += 10.0
            reasons.append(
                "trigger_volatility_present"
            )

        entry, stop, target, rr = (
            self.prices_from_atr(
                context,
                stop_atr=1.00,
                target_atr=2.20,
            )
        )

        status = (
            "candidate"
            if score >= 55.0
            and (
                nr7
                or inside_bar
            )
            else "watch"
        )

        return ExpertSignal(
            expert=self.expert_id,
            instrument=context.instrument,
            side=context.side,
            score=min(
                100.0,
                score,
            ),
            status=status,
            setup=setup,
            regime=context.regime,
            session=context.session,
            entry_style=(
                "contraction_breakout"
            ),
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            reward_risk=rr,
            confidence=min(
                1.0,
                score / 100.0,
            ),
            reasons=reasons,
            metadata={
                "nr7": nr7,
                "inside_bar": inside_bar,
                "latest_range": latest_range,
                "atr_percentile": (
                    atr_percentile
                ),
            },
        )
