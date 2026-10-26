from __future__ import annotations

from expert_library.research.review_engine import (
    EVIDENCE_WEIGHTS,
    SourceReviewEngine,
)


def test_evidence_weights() -> None:
    assert (
        EVIDENCE_WEIGHTS["primary"]
        > EVIDENCE_WEIGHTS["secondary"]
        > EVIDENCE_WEIGHTS["community"]
    )


def test_strong_finding_scores_high() -> None:
    engine = SourceReviewEngine()

    score, _ = engine.score_finding(
        {
            "evidence_level": "primary",
            "confidence": 0.90,
            "mechanical": True,
            "independently_corroborated": True,
            "corroborating_source_ids": [
                "source_b",
            ],
            "invalidation": (
                "Disable in opposing regime"
            ),
            "forex_adaptation": (
                "Use MTF filter"
            ),
        }
    )

    assert score >= 82.0


def test_weak_finding_scores_lower() -> None:
    engine = SourceReviewEngine()

    score, _ = engine.score_finding(
        {
            "evidence_level": "community",
            "confidence": 0.40,
            "mechanical": False,
            "independently_corroborated": False,
            "corroborating_source_ids": [],
            "invalidation": "",
            "forex_adaptation": "",
        }
    )

    assert score < 50.0
