from __future__ import annotations

from experts.base import ExpertEngine
from signals.context import MarketContext
from signals.models import ExpertSignal


class VelezExpert(ExpertEngine):
    expert_id = "velez"

    def evaluate(
        self,
        context: MarketContext,
    ) -> ExpertSignal:
        setup = "trend_pullback"

        if context.regime not in {
            "trending",
            "strong_trending",
        }:
            return self.rejected(
                context,
                setup,
                [
                    "regime_not_trending",
                ],
            )

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

        h4 = context.timeframe(
            "H4"
        )
        h1 = context.timeframe(
            "H1"
        )
        m15 = context.timeframe(
            "M15"
        )
        m5 = context.timeframe(
            "M5"
        )

        expected = context.direction

        agreement = sum(
            1
            for result in [
                h4,
                h1,
                m15,
                m5,
            ]
            if result.get(
                "direction"
            )
            == expected
        )

        if agreement < 3:
            return self.rejected(
                context,
                setup,
                [
                    "insufficient_mtf_alignment",
                ],
            )

        score = 45.0
        reasons = [
            "trend_regime",
            "directional_structure",
        ]

        score += agreement * 8.0

        h1_adx = self.safe_float(
            h1.get(
                "adx"
            )
        )

        if h1_adx >= 25:
            score += 8.0
            reasons.append(
                "h1_adx_confirmed"
            )

        m15_rsi = self.safe_float(
            m15.get(
                "rsi"
            ),
            50.0,
        )

        pullback_ready = (
            42.0 <= m15_rsi <= 68.0
            if context.side == "buy"
            else 32.0 <= m15_rsi <= 58.0
        )

        if pullback_ready:
            score += 10.0
            reasons.append(
                "controlled_pullback_zone"
            )
            entry_style = (
                "pullback_confirmation"
            )

        else:
            score -= 12.0
            reasons.append(
                "wait_for_better_pullback"
            )
            entry_style = (
                "wait_for_pullback"
            )

        entry, stop, target, rr = (
            self.prices_from_atr(
                context,
                stop_atr=1.25,
                target_atr=2.50,
            )
        )

        status = (
            "candidate"
            if score >= 55.0
            and pullback_ready
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
            entry_style=entry_style,
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
                "mtf_agreement": agreement,
                "m15_rsi": m15_rsi,
                "h1_adx": h1_adx,
            },
        )
