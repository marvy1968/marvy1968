from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple
from .types import EngineContext, EngineSignal, clamp
from .experts import (
    TrendBreakoutExpert,
    MeanReversionExpert,
    SqueezeExpert,
    ReversalExpert,
    RiskExecutionExpert,
)

@dataclass
class BrainDecision:
    # "long" / "short" / None
    direction: Optional[str]
    confidence: float
    block: bool
    reason: str
    signals: List[EngineSignal]

class ExpertBrain:
    """
    Runs 5 experts IN PARALLEL, then:
      1) Applies hard blocks from risk/execution expert
      2) Chooses the best directional signal by score
      3) Adds regime-aware weighting
    """
    def __init__(self):
        self.e_trend = TrendBreakoutExpert()
        self.e_mr = MeanReversionExpert()
        self.e_sq = SqueezeExpert()
        self.e_rev = ReversalExpert()
        self.e_risk = RiskExecutionExpert()

        self.experts = [self.e_trend, self.e_mr, self.e_sq, self.e_rev]
        self.risk_expert = self.e_risk

    def decide(self, c: EngineContext) -> BrainDecision:
        risk_sig = self.risk_expert.evaluate(c)
        sigs: List[EngineSignal] = [risk_sig]

        # hard block takes precedence
        if risk_sig.block:
            return BrainDecision(
                direction=None,
                confidence=0.0,
                block=True,
                reason=f"BLOCKED: {risk_sig.reason}",
                signals=sigs,
            )

        # evaluate strategy experts in parallel
        strat_sigs = [e.evaluate(c) for e in self.experts]
        sigs.extend(strat_sigs)

        # score each directional signal
        best: Optional[Tuple[float, EngineSignal]] = None
        for s in strat_sigs:
            if not s.direction:
                continue

            # base score = confidence
            score = float(s.confidence)

            # regime weighting (institutional style allocation)
            if c.regime == "trend":
                if s.name in ("trend_breakout", "squeeze_expansion"):
                    score *= 1.12
                if s.name in ("mean_reversion",):
                    score *= 0.85
            else:  # range
                if s.name in ("mean_reversion", "reversal_exhaustion"):
                    score *= 1.10
                if s.name in ("trend_breakout",):
                    score *= 0.80

            # bias alignment bonus
            if c.bias == s.direction:
                score *= 1.08

            if best is None or score > best[0]:
                best = (score, s)

        if best is None:
            return BrainDecision(
                direction=None,
                confidence=0.0,
                block=False,
                reason="no expert directional edge",
                signals=sigs,
            )

        score, s = best
        return BrainDecision(
            direction=s.direction,
            confidence=clamp(score, 0.0, 1.0),
            block=False,
            reason=f"{s.name}: {s.reason}",
            signals=sigs,
        )
