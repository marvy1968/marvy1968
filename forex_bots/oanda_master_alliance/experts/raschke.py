from __future__ import annotations

from experts.base import ExpertEngine
from signals.context import MarketContext
from signals.models import ExpertSignal


class RaschkeExpert(ExpertEngine):
    expert_id = "raschke"

    def evaluate(
        self,
        context: MarketContext,
    ) -> ExpertSignal:
        setup = "momentum_expansion"

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
        m5 = context.timeframe(
            "M5"
        )

        score = 35.0
        reasons: list[str] = []

        aligned = sum(
            1
            for result in [
                h1,
                m15,
                m5,
            ]
            if result.get(
                "direction"
            )
            == context.direction
        )

        score += aligned * 10.0

        if aligned >= 2:
            reasons.append(
                "momentum_timeframes_aligned"
            )

        atr_percentile = self.safe_float(
            m15.get(
                "atr_percentile"
            )
        )

        if 0.55 <= atr_percentile <= 0.92:
            score += 15.0
            reasons.append(
                "healthy_volatility_expansion"
            )

        elif atr_percentile > 0.92:
            score -= 8.0
            reasons.append(
                "possibly_overextended"
            )

        adx = self.safe_float(
            m15.get(
                "adx"
            )
        )

        if adx >= 25:
            score += 12.0
            reasons.append(
                "adx_expansion"
            )

        volume_ratio = self.safe_float(
            m15.get(
                "volume_ratio"
            )
        )

        if volume_ratio >= 0.80:
            score += 8.0
            reasons.append(
                "participation_confirmed"
            )

        entry, stop, target, rr = (
            self.prices_from_atr(
                context,
                stop_atr=1.10,
                target_atr=2.20,
            )
        )

        status = (
            "candidate"
            if score >= 55
            and aligned >= 2
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
                "momentum_reentry"
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
                "aligned_timeframes": aligned,
                "atr_percentile": atr_percentile,
                "adx": adx,
                "volume_ratio": volume_ratio,
            },
        )
