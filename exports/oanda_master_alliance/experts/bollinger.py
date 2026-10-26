from __future__ import annotations

from experts.base import ExpertEngine
from signals.context import MarketContext
from signals.models import ExpertSignal


class BollingerExpert(ExpertEngine):
    expert_id = "bollinger"

    def evaluate(
        self,
        context: MarketContext,
    ) -> ExpertSignal:
        setup = "bollinger_structure"

        m15 = context.timeframe(
            "M15"
        )

        close = self.safe_float(
            m15.get(
                "close"
            )
        )

        upper = self.safe_float(
            m15.get(
                "bollinger_upper"
            )
        )

        lower = self.safe_float(
            m15.get(
                "bollinger_lower"
            )
        )

        width_percentile = (
            self.safe_float(
                m15.get(
                    "bollinger_width_percentile"
                )
            )
        )

        if not all(
            [
                close,
                upper,
                lower,
            ]
        ):
            return self.rejected(
                context,
                setup,
                [
                    "missing_bollinger_values",
                ],
            )

        score = 25.0
        reasons: list[str] = []

        if context.regime in {
            "compression",
            "breakout_transition",
        }:
            score += 30.0
            reasons.append(
                "compression_or_transition"
            )

        if context.side == "buy":
            if close >= upper:
                score += 25.0
                reasons.append(
                    "upper_band_breakout"
                )

            elif close > (
                lower
                + (
                    upper - lower
                )
                * 0.55
            ):
                score += 12.0
                reasons.append(
                    "above_band_midzone"
                )

        elif context.side == "sell":
            if close <= lower:
                score += 25.0
                reasons.append(
                    "lower_band_breakout"
                )

            elif close < (
                lower
                + (
                    upper - lower
                )
                * 0.45
            ):
                score += 12.0
                reasons.append(
                    "below_band_midzone"
                )

        if width_percentile >= 0.65:
            score += 12.0
            reasons.append(
                "band_expansion"
            )

        entry, stop, target, rr = (
            self.prices_from_atr(
                context,
                stop_atr=1.15,
                target_atr=2.10,
            )
        )

        status = (
            "candidate"
            if score >= 55.0
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
                "band_expansion_confirmation"
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
                "close": close,
                "upper_band": upper,
                "lower_band": lower,
                "width_percentile": (
                    width_percentile
                ),
            },
        )
