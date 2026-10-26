from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import pandas as pd

from signals.context import MarketContext
from signals.models import ExpertSignal


class ExpertEngine(ABC):
    expert_id = "base"

    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config

    @abstractmethod
    def evaluate(
        self,
        context: MarketContext,
    ) -> ExpertSignal:
        raise NotImplementedError

    @staticmethod
    def safe_float(
        value: Any,
        default: float = 0.0,
    ) -> float:
        try:
            result = float(value)

            if np.isnan(result):
                return default

            return result

        except (
            TypeError,
            ValueError,
        ):
            return default

    @staticmethod
    def latest(
        frame: pd.DataFrame,
    ) -> pd.Series:
        if frame.empty:
            raise ValueError(
                "No candle data"
            )

        return frame.iloc[-1]

    @staticmethod
    def recent_high(
        frame: pd.DataFrame,
        lookback: int,
    ) -> float:
        return float(
            frame["high"]
            .tail(lookback)
            .max()
        )

    @staticmethod
    def recent_low(
        frame: pd.DataFrame,
        lookback: int,
    ) -> float:
        return float(
            frame["low"]
            .tail(lookback)
            .min()
        )

    @staticmethod
    def calculate_rr(
        side: str,
        entry: float,
        stop: float,
        target: float,
    ) -> float:
        risk = (
            entry - stop
            if side == "buy"
            else stop - entry
        )

        reward = (
            target - entry
            if side == "buy"
            else entry - target
        )

        if risk <= 0:
            return 0.0

        return reward / risk

    def prices_from_atr(
        self,
        context: MarketContext,
        *,
        stop_atr: float,
        target_atr: float,
    ) -> tuple[
        float,
        float,
        float,
        float,
    ]:
        entry = context.latest_price()
        atr = context.latest_atr()
        side = context.side

        if side == "buy":
            stop = entry - (
                atr * stop_atr
            )

            target = entry + (
                atr * target_atr
            )

        elif side == "sell":
            stop = entry + (
                atr * stop_atr
            )

            target = entry - (
                atr * target_atr
            )

        else:
            stop = entry
            target = entry

        reward_risk = self.calculate_rr(
            side,
            entry,
            stop,
            target,
        )

        return (
            entry,
            stop,
            target,
            reward_risk,
        )

    def rejected(
        self,
        context: MarketContext,
        setup: str,
        reasons: list[str],
    ) -> ExpertSignal:
        price = context.latest_price()

        return ExpertSignal(
            expert=self.expert_id,
            instrument=context.instrument,
            side="flat",
            score=0.0,
            status="rejected",
            setup=setup,
            regime=context.regime,
            session=context.session,
            entry_style="none",
            entry_price=price,
            stop_price=price,
            target_price=price,
            reward_risk=0.0,
            confidence=0.0,
            rejection_reasons=reasons,
        )
