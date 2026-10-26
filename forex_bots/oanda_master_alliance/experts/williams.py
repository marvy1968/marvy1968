from __future__ import annotations

from experts.base import ExpertEngine
from signals.context import MarketContext
from signals.models import ExpertSignal


class WilliamsExpert(ExpertEngine):
    expert_id = "williams"

    def evaluate(
        self,
        context: MarketContext,
    ) -> ExpertSignal:
        setup = "volatility_breakout"

        if context.side not in {
            "buy",
            "sell",
        }:
            return self.rejected(
                context,
                setup,
                [
                    "no_direction",
                ],
            )

        h1 = context.timeframe(
            "H1"
        )
        m15 = context.timeframe(
            "M15"
        )

        score = 30.0
        reasons: list[str] = []

        momentum = float(
            context.pair_momentum
        )

        momentum_aligned = (
            momentum > 0
            if context.side == "buy"
            else momentum < 0
        )

        if momentum_aligned:
            score += 25.0
            reasons.append(
                "pair_momentum_aligned"
            )

        h1_atr_percentile = (
            self.safe_float(
                h1.get(
                    "atr_percentile"
                )
            )
        )

        if 0.55 <= h1_atr_percentile <= 0.95:
            score += 18.0
            reasons.append(
                "h1_volatility_supportive"
            )

        m15_slope = self.safe_float(
            m15.get(
                "price_slope"
            )
        )

        slope_aligned = (
            m15_slope > 0
            if context.side == "buy"
            else m15_slope < 0
        )

        if slope_aligned:
            score += 15.0
            reasons.append(
                "price_slope_aligned"
            )

        entry, stop, target, rr = (
            self.prices_from_atr(
                context,
                stop_atr=1.15,
                target_atr=2.30,
            )
        )

        status = (
            "candidate"
            if score >= 55.0
            and momentum_aligned
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
                "volatility_relative_trigger"
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
                "pair_momentum": momentum,
                "h1_atr_percentile": (
                    h1_atr_percentile
                ),
                "m15_price_slope": (
                    m15_slope
                ),
            },
        )
