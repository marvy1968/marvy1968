from __future__ import annotations

from experts.base import ExpertEngine
from signals.context import MarketContext
from signals.models import ExpertSignal


class ConnorsExpert(ExpertEngine):
    expert_id = "connors"

    def evaluate(
        self,
        context: MarketContext,
    ) -> ExpertSignal:
        setup = "short_term_mean_reversion"

        if context.regime in {
            "strong_trending",
        }:
            return self.rejected(
                context,
                setup,
                [
                    "strong_trend_blocks_mean_reversion",
                ],
            )

        m15 = context.timeframe(
            "M15"
        )
        h1 = context.timeframe(
            "H1"
        )

        rsi = self.safe_float(
            m15.get(
                "rsi"
            ),
            50.0,
        )

        higher_direction = str(
            h1.get(
                "direction",
                "mixed",
            )
        )

        side = "flat"
        reasons: list[str] = []
        score = 25.0

        if (
            rsi <= 25.0
            and higher_direction
            not in {
                "down",
            }
        ):
            side = "buy"
            score += 45.0
            reasons.append(
                "oversold_without_hostile_h1"
            )

        elif (
            rsi >= 75.0
            and higher_direction
            not in {
                "up",
            }
        ):
            side = "sell"
            score += 45.0
            reasons.append(
                "overbought_without_hostile_h1"
            )

        else:
            return self.rejected(
                context,
                setup,
                [
                    "no_mean_reversion_extreme",
                ],
            )

        original_direction = (
            context.direction
        )

        context.direction = (
            "up"
            if side == "buy"
            else "down"
        )

        entry, stop, target, rr = (
            self.prices_from_atr(
                context,
                stop_atr=1.00,
                target_atr=1.60,
            )
        )

        context.direction = (
            original_direction
        )

        return ExpertSignal(
            expert=self.expert_id,
            instrument=context.instrument,
            side=side,
            score=min(
                100.0,
                score,
            ),
            status="candidate",
            setup=setup,
            regime=context.regime,
            session=context.session,
            entry_style=(
                "mean_reversion_trigger"
            ),
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            reward_risk=rr,
            confidence=score / 100.0,
            reasons=reasons,
            metadata={
                "m15_rsi": rsi,
                "h1_direction": (
                    higher_direction
                ),
            },
        )
