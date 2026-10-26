from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from brain.correlation import (
    filter_correlated_decisions,
)
from core.common import ROOT
from experts.registry import (
    build_experts,
)
from signals.context import (
    MarketContext,
    load_contexts,
)
from signals.models import (
    AllianceDecision,
    ExpertSignal,
)


CONFIG_FILE = (
    ROOT
    / "config"
    / "segment2.yaml"
)

SIGNAL_DIRECTORY = (
    ROOT
    / "state"
    / "signals"
)

DECISION_DIRECTORY = (
    ROOT
    / "state"
    / "decisions"
)

EVENT_LOG = (
    ROOT
    / "logs"
    / "segment2_events.jsonl"
)

LATEST_SIGNALS = (
    SIGNAL_DIRECTORY
    / "latest_expert_signals.json"
)

LATEST_DECISIONS = (
    DECISION_DIRECTORY
    / "latest_alliance_decisions.json"
)

LATEST_SHORTLIST = (
    DECISION_DIRECTORY
    / "latest_shortlist.json"
)


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def append_jsonl(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                payload,
                default=str,
            )
            + "\n"
        )


def atomic_json(
    path: Path,
    payload: Any,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            payload,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    temporary.replace(
        path
    )


def load_segment2_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            str(CONFIG_FILE)
        )

    return yaml.safe_load(
        CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )


class AllianceEngine:
    def __init__(
        self,
    ) -> None:
        self.config = (
            load_segment2_config()
        )

        self.segment = self.config[
            "segment2"
        ]

        self.experts = build_experts(
            self.config
        )

    def strength_alignment_score(
        self,
        context: MarketContext,
        side: str,
    ) -> float:
        difference = (
            context.strength_difference
        )

        if side == "buy":
            aligned = difference > 0

        elif side == "sell":
            aligned = difference < 0

        else:
            return 0.0

        magnitude = min(
            abs(
                difference
            )
            / 2.5,
            1.0,
        )

        return (
            100.0 * magnitude
            if aligned
            else 0.0
        )

    def timeframe_score(
        self,
        context: MarketContext,
        side: str,
    ) -> float:
        expected = (
            "up"
            if side == "buy"
            else "down"
        )

        matches = sum(
            1
            for result in (
                context.timeframe_results
            )
            if result.get(
                "direction"
            )
            == expected
        )

        total = max(
            len(
                context.timeframe_results
            ),
            1,
        )

        return (
            matches
            / total
        ) * 100.0

    def regime_score(
        self,
        context: MarketContext,
        side: str,
    ) -> float:
        if side != context.side:
            return 20.0

        mapping = {
            "strong_trending": 100.0,
            "trending": 85.0,
            "breakout_transition": 80.0,
            "compression": 60.0,
            "ranging": 35.0,
            "mixed": 20.0,
        }

        return mapping.get(
            context.regime,
            20.0,
        )

    def session_score(
        self,
        context: MarketContext,
    ) -> float:
        session_config = (
            self.config[
                "sessions"
            ].get(
                context.session,
                {},
            )
        )

        quality = float(
            session_config.get(
                "quality",
                0.70,
            )
        )

        preferred = set(
            session_config.get(
                "preferred_pairs",
                [],
            )
        )

        if (
            preferred
            and context.instrument
            not in preferred
        ):
            quality *= 0.80

        return quality * 100.0

    def overextension_penalty(
        self,
        context: MarketContext,
        side: str,
    ) -> tuple[
        float,
        list[str],
    ]:
        settings = self.config[
            "overextension"
        ]

        m15 = context.timeframe(
            "M15"
        )

        m5 = context.timeframe(
            "M5"
        )

        rsi_values = [
            float(
                result.get(
                    "rsi",
                    50.0,
                )
            )
            for result in [
                m15,
                m5,
            ]
        ]

        atr_percentile = max(
            float(
                m15.get(
                    "atr_percentile",
                    0.0,
                )
            ),
            float(
                m5.get(
                    "atr_percentile",
                    0.0,
                )
            ),
        )

        penalty = 0.0
        reasons: list[str] = []

        if side == "buy":
            if max(
                rsi_values
            ) >= float(
                settings[
                    "bullish_rsi_block"
                ]
            ):
                penalty += 30.0
                reasons.append(
                    "bullish_rsi_overextension"
                )

            elif max(
                rsi_values
            ) >= float(
                settings[
                    "bullish_rsi_penalty"
                ]
            ):
                penalty += 15.0
                reasons.append(
                    "bullish_rsi_elevated"
                )

        elif side == "sell":
            if min(
                rsi_values
            ) <= float(
                settings[
                    "bearish_rsi_block"
                ]
            ):
                penalty += 30.0
                reasons.append(
                    "bearish_rsi_overextension"
                )

            elif min(
                rsi_values
            ) <= float(
                settings[
                    "bearish_rsi_penalty"
                ]
            ):
                penalty += 15.0
                reasons.append(
                    "bearish_rsi_depressed"
                )

        if atr_percentile >= float(
            settings[
                "maximum_atr_percentile_for_chase"
            ]
        ):
            penalty += 10.0
            reasons.append(
                "atr_expansion_extreme"
            )

        return penalty, reasons

    def consolidate_side(
        self,
        context: MarketContext,
        signals: list[ExpertSignal],
        side: str,
    ) -> AllianceDecision:
        active = [
            signal
            for signal in signals
            if (
                signal.side == side
                and signal.status
                in {
                    "candidate",
                    "watch",
                }
            )
        ]

        candidates = [
            signal
            for signal in active
            if signal.status
            == "candidate"
        ]

        opposing = [
            signal
            for signal in signals
            if (
                signal.side
                not in {
                    side,
                    "flat",
                }
                and signal.status
                in {
                    "candidate",
                    "watch",
                }
            )
        ]

        if active:
            expert_score = sum(
                signal.score
                for signal in active
            ) / len(
                active
            )

            entry_score = sum(
                100.0
                if signal.status
                == "candidate"
                else 55.0
                for signal in active
            ) / len(
                active
            )

            reward_risk = sum(
                signal.reward_risk
                for signal in active
            ) / len(
                active
            )

            entry_price = sum(
                signal.entry_price
                for signal in active
            ) / len(
                active
            )

            stop_price = sum(
                signal.stop_price
                for signal in active
            ) / len(
                active
            )

            target_price = sum(
                signal.target_price
                for signal in active
            ) / len(
                active
            )

        else:
            expert_score = 0.0
            entry_score = 0.0
            reward_risk = 0.0
            entry_price = (
                context.latest_price()
            )
            stop_price = entry_price
            target_price = entry_price

        regime_score = self.regime_score(
            context,
            side,
        )

        strength_score = (
            self.strength_alignment_score(
                context,
                side,
            )
        )

        timeframe_score = (
            self.timeframe_score(
                context,
                side,
            )
        )

        session_score = self.session_score(
            context
        )

        rr_score = min(
            reward_risk
            / float(
                self.config[
                    "risk"
                ][
                    "preferred_reward_risk"
                ]
            ),
            1.0,
        ) * 100.0

        weights = self.config[
            "weights"
        ]

        alliance_score = (
            expert_score
            * float(
                weights[
                    "expert_consensus"
                ]
            )
            + regime_score
            * float(
                weights[
                    "regime_alignment"
                ]
            )
            + strength_score
            * float(
                weights[
                    "currency_strength"
                ]
            )
            + timeframe_score
            * float(
                weights[
                    "multi_timeframe"
                ]
            )
            + entry_score
            * float(
                weights[
                    "entry_quality"
                ]
            )
            + rr_score
            * float(
                weights[
                    "reward_risk"
                ]
            )
            + session_score
            * float(
                weights[
                    "session_quality"
                ]
            )
        )

        penalty, penalty_reasons = (
            self.overextension_penalty(
                context,
                side,
            )
        )

        alliance_score = max(
            0.0,
            alliance_score
            - penalty
        )

        rejection_reasons: list[
            str
        ] = []

        reasons = [
            f"expert_score={expert_score:.1f}",
            f"regime_score={regime_score:.1f}",
            f"strength_score={strength_score:.1f}",
            f"timeframe_score={timeframe_score:.1f}",
            f"entry_score={entry_score:.1f}",
            f"session_score={session_score:.1f}",
        ]

        reasons.extend(
            penalty_reasons
        )

        minimum_experts = int(
            self.segment[
                "minimum_confirming_experts"
            ]
        )

        minimum_score = float(
            self.segment[
                "minimum_alliance_score"
            ]
        )

        minimum_rr = float(
            self.config[
                "risk"
            ][
                "minimum_reward_risk"
            ]
        )

        if len(
            candidates
        ) < minimum_experts:
            rejection_reasons.append(
                "insufficient_confirming_experts"
            )

        if alliance_score < minimum_score:
            rejection_reasons.append(
                "alliance_score_below_threshold"
            )

        if reward_risk < minimum_rr:
            rejection_reasons.append(
                "reward_risk_below_threshold"
            )

        if (
            self.segment[
                "block_mixed_regime"
            ]
            and context.regime == "mixed"
        ):
            rejection_reasons.append(
                "mixed_regime_block"
            )

        if (
            not self.segment[
                "allow_countertrend"
            ]
            and side != context.side
        ):
            rejection_reasons.append(
                "countertrend_block"
            )

        if opposing:
            alliance_score = max(
                0.0,
                alliance_score
                - (
                    len(
                        opposing
                    )
                    * 4.0
                ),
            )

            reasons.append(
                "opposing_expert_penalty"
            )

        decision = (
            "candidate"
            if not rejection_reasons
            else "watch"
            if active
            else "rejected"
        )

        confidence = min(
            1.0,
            alliance_score / 100.0,
        )

        risk_notes = [
            "research_only",
            "no_order_placement",
            "practice_environment",
            (
                "wait_for_pullback"
                if penalty_reasons
                else "entry_quality_acceptable"
            ),
        ]

        return AllianceDecision(
            instrument=context.instrument,
            side=side,
            decision=decision,
            alliance_score=round(
                alliance_score,
                2,
            ),
            confidence=round(
                confidence,
                4,
            ),
            confirming_experts=len(
                candidates
            ),
            opposing_experts=len(
                opposing
            ),
            regime=context.regime,
            session=context.session,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            reward_risk=reward_risk,
            expert_signals=[
                signal.to_dict()
                for signal in active
            ],
            reasons=reasons,
            rejection_reasons=(
                rejection_reasons
            ),
            risk_notes=risk_notes,
            metadata={
                "strength_difference": (
                    context
                    .strength_difference
                ),
                "context_direction": (
                    context.direction
                ),
                "context_confidence": (
                    context.confidence
                ),
                "context_confirmations": (
                    context.confirmations
                ),
                "overextension_penalty": (
                    penalty
                ),
            },
        )

    def evaluate_context(
        self,
        context: MarketContext,
    ) -> tuple[
        list[ExpertSignal],
        list[AllianceDecision],
    ]:
        signals: list[
            ExpertSignal
        ] = []

        for expert in self.experts:
            try:
                signal = expert.evaluate(
                    context
                )

            except Exception as exc:
                signal = ExpertSignal(
                    expert=expert.expert_id,
                    instrument=(
                        context.instrument
                    ),
                    side="flat",
                    score=0.0,
                    status="error",
                    setup="unknown",
                    regime=context.regime,
                    session=context.session,
                    entry_style="none",
                    entry_price=(
                        context.latest_price()
                    ),
                    stop_price=(
                        context.latest_price()
                    ),
                    target_price=(
                        context.latest_price()
                    ),
                    reward_risk=0.0,
                    confidence=0.0,
                    rejection_reasons=[
                        (
                            f"{type(exc).__name__}:"
                            f"{exc}"
                        )
                    ],
                )

            signals.append(
                signal
            )

        sides = {
            signal.side
            for signal in signals
            if signal.side in {
                "buy",
                "sell",
            }
        }

        if not sides and context.side in {
            "buy",
            "sell",
        }:
            sides.add(
                context.side
            )

        decisions = [
            self.consolidate_side(
                context,
                signals,
                side,
            )
            for side in sorted(
                sides
            )
        ]

        return signals, decisions

    def run(
        self,
    ) -> dict[str, Any]:
        timestamp = utc_now()

        contexts = load_contexts(
            int(
                self.segment[
                    "maximum_snapshot_age_minutes"
                ]
            )
        )

        all_signals: list[
            ExpertSignal
        ] = []

        all_decisions: list[
            AllianceDecision
        ] = []

        for context in contexts.values():
            signals, decisions = (
                self.evaluate_context(
                    context
                )
            )

            all_signals.extend(
                signals
            )

            all_decisions.extend(
                decisions
            )

        candidates = [
            decision
            for decision in all_decisions
            if decision.decision
            == "candidate"
        ]

        shortlist = (
            filter_correlated_decisions(
                candidates,
                maximum_currency_exposure=int(
                    self.config[
                        "risk"
                    ][
                        "maximum_currency_exposure"
                    ]
                ),
                maximum_positions=int(
                    self.segment[
                        "maximum_signals_per_cycle"
                    ]
                ),
            )
        )

        signal_payload = {
            "timestamp": (
                timestamp.isoformat()
            ),
            "research_only": True,
            "automatic_execution": False,
            "signals": [
                signal.to_dict()
                for signal in all_signals
            ],
        }

        decision_payload = {
            "timestamp": (
                timestamp.isoformat()
            ),
            "research_only": True,
            "automatic_execution": False,
            "decisions": [
                decision.to_dict()
                for decision in all_decisions
            ],
        }

        shortlist_payload = {
            "timestamp": (
                timestamp.isoformat()
            ),
            "research_only": True,
            "automatic_execution": False,
            "candidate_count": len(
                candidates
            ),
            "shortlist_count": len(
                shortlist
            ),
            "shortlist": [
                decision.to_dict()
                for decision in shortlist
            ],
        }

        atomic_json(
            LATEST_SIGNALS,
            signal_payload,
        )

        atomic_json(
            LATEST_DECISIONS,
            decision_payload,
        )

        atomic_json(
            LATEST_SHORTLIST,
            shortlist_payload,
        )

        append_jsonl(
            EVENT_LOG,
            {
                "timestamp": (
                    timestamp.isoformat()
                ),
                "event": (
                    "segment2_cycle_complete"
                ),
                "contexts": len(
                    contexts
                ),
                "expert_signals": len(
                    all_signals
                ),
                "alliance_decisions": len(
                    all_decisions
                ),
                "candidates": len(
                    candidates
                ),
                "shortlist": len(
                    shortlist
                ),
                "automatic_execution": (
                    False
                ),
            },
        )

        return {
            "timestamp": (
                timestamp.isoformat()
            ),
            "pairs_evaluated": len(
                contexts
            ),
            "experts": len(
                self.experts
            ),
            "expert_signals": len(
                all_signals
            ),
            "alliance_decisions": len(
                all_decisions
            ),
            "candidates": len(
                candidates
            ),
            "shortlist": len(
                shortlist
            ),
            "automatic_execution": False,
        }
