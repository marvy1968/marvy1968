from __future__ import annotations

import argparse
import json

from expert_library.research.review_engine import (
    SourceReviewEngine,
)


def split_values(
    value: str,
) -> list[str]:
    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "OANDA Expert Source Review Engine"
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    review = subparsers.add_parser(
        "add-review"
    )

    review.add_argument(
        "--expert",
        required=True,
    )

    review.add_argument(
        "--source-id",
        required=True,
    )

    review.add_argument(
        "--title",
        required=True,
    )

    review.add_argument(
        "--source-type",
        required=True,
    )

    review.add_argument(
        "--section",
        required=True,
    )

    review.add_argument(
        "--reviewer",
        default="marvin",
    )

    review.add_argument(
        "--status",
        choices=[
            "pending",
            "in_progress",
            "completed",
            "recheck_required",
        ],
        default="in_progress",
    )

    review.add_argument(
        "--progress",
        type=float,
        default=0.0,
    )

    review.add_argument(
        "--primary",
        action="store_true",
    )

    review.add_argument(
        "--reference",
        default="",
    )

    review.add_argument(
        "--notes",
        default="",
    )

    finding = subparsers.add_parser(
        "add-finding"
    )

    finding.add_argument(
        "--review-id",
        required=True,
    )

    finding.add_argument(
        "--expert",
        required=True,
    )

    finding.add_argument(
        "--source-id",
        required=True,
    )

    finding.add_argument(
        "--rule-type",
        required=True,
    )

    finding.add_argument(
        "--finding",
        required=True,
    )

    finding.add_argument(
        "--forex-adaptation",
        required=True,
    )

    finding.add_argument(
        "--excerpt",
        default="",
    )

    finding.add_argument(
        "--location",
        required=True,
    )

    finding.add_argument(
        "--evidence",
        choices=[
            "primary",
            "official",
            "secondary",
            "community",
            "inference",
            "unverified",
        ],
        required=True,
    )

    finding.add_argument(
        "--confidence",
        type=float,
        required=True,
    )

    finding.add_argument(
        "--mechanical",
        action="store_true",
    )

    finding.add_argument(
        "--corroborated",
        action="store_true",
    )

    finding.add_argument(
        "--corroborating-sources",
        default="",
    )

    finding.add_argument(
        "--status",
        choices=[
            "research",
            "candidate",
            "validated",
            "rejected",
        ],
        default="research",
    )

    finding.add_argument(
        "--invalidation",
        default="",
    )

    contradiction = subparsers.add_parser(
        "add-contradiction"
    )

    contradiction.add_argument(
        "--expert",
        required=True,
    )

    contradiction.add_argument(
        "--topic",
        required=True,
    )

    contradiction.add_argument(
        "--finding-ids",
        required=True,
    )

    contradiction.add_argument(
        "--description",
        required=True,
    )

    contradiction.add_argument(
        "--severity",
        choices=[
            "low",
            "medium",
            "high",
            "critical",
        ],
        required=True,
    )

    contradiction.add_argument(
        "--status",
        choices=[
            "open",
            "reviewing",
            "resolved",
            "dismissed",
        ],
        default="open",
    )

    contradiction.add_argument(
        "--resolution-notes",
        default="",
    )

    promotion = subparsers.add_parser(
        "evaluate"
    )

    promotion.add_argument(
        "--finding-id",
        required=True,
    )

    promotion.add_argument(
        "--approved-by",
        default="research_engine",
    )

    queue = subparsers.add_parser(
        "next"
    )

    queue.add_argument(
        "--count",
        type=int,
        default=5,
    )

    subparsers.add_parser(
        "summary"
    )

    subparsers.add_parser(
        "validate"
    )

    subparsers.add_parser(
        "report"
    )

    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()

    engine = SourceReviewEngine()

    if arguments.command == "add-review":
        record = engine.add_review(
            expert=arguments.expert,
            source_id=arguments.source_id,
            title=arguments.title,
            source_type=(
                arguments.source_type
            ),
            section=arguments.section,
            reviewer=arguments.reviewer,
            status=arguments.status,
            progress_percentage=(
                arguments.progress
            ),
            primary_source=(
                arguments.primary
            ),
            source_reference=(
                arguments.reference
            ),
            notes=arguments.notes,
        )

        print(
            json.dumps(
                record.to_dict(),
                indent=2,
            )
        )

    elif arguments.command == "add-finding":
        record = engine.add_finding(
            review_id=arguments.review_id,
            expert=arguments.expert,
            source_id=arguments.source_id,
            rule_type=arguments.rule_type,
            finding=arguments.finding,
            forex_adaptation=(
                arguments.forex_adaptation
            ),
            supporting_excerpt=(
                arguments.excerpt
            ),
            location_reference=(
                arguments.location
            ),
            evidence_level=(
                arguments.evidence
            ),
            confidence=arguments.confidence,
            mechanical=(
                arguments.mechanical
            ),
            independently_corroborated=(
                arguments.corroborated
            ),
            corroborating_source_ids=(
                split_values(
                    arguments
                    .corroborating_sources
                )
            ),
            implementation_status=(
                arguments.status
            ),
            invalidation=(
                arguments.invalidation
            ),
        )

        print(
            json.dumps(
                record.to_dict(),
                indent=2,
            )
        )

    elif (
        arguments.command
        == "add-contradiction"
    ):
        record = engine.add_contradiction(
            expert=arguments.expert,
            rule_topic=arguments.topic,
            finding_ids=split_values(
                arguments.finding_ids
            ),
            description=(
                arguments.description
            ),
            severity=arguments.severity,
            resolution_status=(
                arguments.status
            ),
            resolution_notes=(
                arguments.resolution_notes
            ),
        )

        print(
            json.dumps(
                record.to_dict(),
                indent=2,
            )
        )

    elif arguments.command == "evaluate":
        decision = engine.evaluate_promotion(
            arguments.finding_id,
            approved_by=(
                arguments.approved_by
            ),
        )

        print(
            json.dumps(
                decision.to_dict(),
                indent=2,
            )
        )

    elif arguments.command == "next":
        print(
            json.dumps(
                engine.next_queue_items(
                    arguments.count
                ),
                indent=2,
            )
        )

    elif arguments.command in {
        "summary",
        "report",
    }:
        print(
            json.dumps(
                engine.rebuild_summary(),
                indent=2,
            )
        )

    elif arguments.command == "validate":
        errors = engine.validate_storage()

        if errors:
            print(
                "Source review storage "
                "validation failed."
            )

            for error in errors:
                print(
                    f"- {error}"
                )

            raise SystemExit(1)

        print(
            "Source review storage "
            "validation passed."
        )


if __name__ == "__main__":
    main()
