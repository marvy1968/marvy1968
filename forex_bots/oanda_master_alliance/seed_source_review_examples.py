from __future__ import annotations

from expert_library.research.review_engine import (
    SourceReviewEngine,
)


def main() -> None:
    engine = SourceReviewEngine()

    review = engine.add_review(
        expert="velez",
        source_id="velez_master_trader",
        title=(
            "Tools and Tactics for "
            "the Master Day Trader"
        ),
        source_type="book",
        section="Initial doctrine registration",
        reviewer="system_seed",
        status="in_progress",
        progress_percentage=5.0,
        primary_source=True,
        source_reference=(
            "Registered book source"
        ),
        notes=(
            "Placeholder review record. "
            "Not a completed book extraction."
        ),
    )

    engine.add_finding(
        review_id=review.review_id,
        expert="velez",
        source_id="velez_master_trader",
        rule_type="confirmation",
        finding=(
            "Use multiple timeframes to "
            "confirm directional structure."
        ),
        forex_adaptation=(
            "Require agreement between 4H, "
            "1H and 15M before a 5M trigger."
        ),
        supporting_excerpt="",
        location_reference=(
            "Initial registered doctrine; "
            "exact location pending review"
        ),
        evidence_level="primary",
        confidence=0.60,
        mechanical=True,
        independently_corroborated=False,
        corroborating_source_ids=[],
        implementation_status="research",
        invalidation=(
            "Do not use when the higher "
            "timeframes disagree."
        ),
    )

    summary = engine.rebuild_summary()

    print(
        "Seed review records created."
    )

    print(
        summary
    )


if __name__ == "__main__":
    main()
