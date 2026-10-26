from __future__ import annotations

from experts.base import ExpertEngine
from signals.context import MarketContext
from signals.models import ExpertSignal


class BrooksExpert(ExpertEngine):
    expert_id = "brooks"

    def evaluate(
        self,
        context: MarketContext,
    ) -> ExpertSignal:
        setup = "price_action_context"

        m15 = context.frame(
            "M15"
        )

        if len(m15) < 25:
            return self.rejected(
                context,
                setup,
                [
                    "insufficient_candles",
                ],
            )

        recent = m15.tail(
            20
        )

        latest = recent.iloc[-1]
        prior = recent.iloc[-2]

        body = abs(
            float(
                latest["close"]
                - latest["open"]
            )
        )

        candle_range = max(
            float(
                latest["high"]
                - latest["low"]
            ),
            1e-12,
        )

        body_ratio = (
            body / candle_range
        )

        bullish_signal = (
            float(latest["close"])
            > float(latest["open"])
            and float(latest["close"])
            > float(prior["high"])
        )

        bearish_signal = (
            float(latest["close"])
            < float(latest["open"])
            and float(latest["close"])
            < float(prior["low"])
        )

        signal_side = (
            "buy"
            if bullish_signal
            else "sell"
            if bearish_signal
            else "flat"
        )

        score = 30.0
        reasons = [
            "price_action_context_evaluated",
        ]

        if signal_side == context.side:
            score += 30.0
            reasons.append(
                "signal_bar_direction_confirmed"
            )

        elif signal_side == "flat":
            score -= 5.0
            reasons.append(
                "no_decisive_signal_bar"
            )

        else:
            return self.rejected(
                context,
                setup,
                [
                    "signal_bar_opposes_context",
                ],
            )

        if body_ratio >= 0.60:
            score += 15.0
            reasons.append(
                "decisive_signal_body"
            )

        if context.regime in {
            "trending",
            "strong_trending",
            "breakout_transition",
        }:
            score += 12.0
            reasons.append(
                "context_supports_follow_through"
            )

        entry, stop, target, rr = (
            self.prices_from_atr(
                context,
                stop_atr=1.20,
                target_atr=2.10,
            )
        )

        status = (
            "candidate"
            if score >= 55.0
            and signal_side == context.side
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
                "signal_bar_follow_through"
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
                "signal_side": signal_side,
                "body_ratio": body_ratio,
            },
        )
