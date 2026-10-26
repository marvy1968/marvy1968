from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]

REVIEW_DIRECTORY = (
    ROOT
    / "research"
    / "source_reviews"
)

REVIEW_LOG = (
    REVIEW_DIRECTORY
    / "source_reviews.jsonl"
)

FINDINGS_LOG = (
    REVIEW_DIRECTORY
    / "source_findings.jsonl"
)

CONTRADICTIONS_LOG = (
    REVIEW_DIRECTORY
    / "contradictions.jsonl"
)

PROMOTIONS_LOG = (
    REVIEW_DIRECTORY
    / "promotion_decisions.jsonl"
)

QUEUE_FILE = (
    ROOT
    / "reports"
    / "expert_library"
    / "research_queue.csv"
)

SUMMARY_FILE = (
    ROOT
    / "reports"
    / "expert_library"
    / "source_review_summary.json"
)

REPORT_FILE = (
    ROOT
    / "reports"
    / "expert_library"
    / "source_review_report.md"
)


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def stable_id(
    *values: str,
) -> str:
    payload = "|".join(
        str(value).strip().lower()
        for value in values
    )

    return hashlib.sha1(
        payload.encode(
            "utf-8"
        )
    ).hexdigest()[:16]


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


def read_jsonl(
    path: Path,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        for line_number, line in enumerate(
            handle,
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            try:
                records.append(
                    json.loads(
                        line
                    )
                )

            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path.name}: malformed JSON "
                    f"on line {line_number}: {exc}"
                ) from exc

    return records


def latest_by_id(
    records: Iterable[dict[str, Any]],
    key: str,
) -> dict[str, dict[str, Any]]:
    latest: dict[
        str,
        dict[str, Any],
    ] = {}

    for record in records:
        identifier = str(
            record.get(
                key,
                ""
            )
        ).strip()

        if identifier:
            latest[
                identifier
            ] = record

    return latest


@dataclass
class ReviewRecord:
    review_id: str
    expert: str
    source_id: str
    title: str
    source_type: str
    section: str
    reviewer: str
    status: str
    progress_percentage: float
    primary_source: bool
    source_reference: str
    notes: str = ""
    reviewed_at: str = field(
        default_factory=lambda: (
            utc_now().isoformat()
        )
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


@dataclass
class FindingRecord:
    finding_id: str
    review_id: str
    expert: str
    source_id: str
    rule_type: str
    finding: str
    forex_adaptation: str
    supporting_excerpt: str
    location_reference: str
    evidence_level: str
    confidence: float
    mechanical: bool
    independently_corroborated: bool
    corroborating_source_ids: list[str]
    implementation_status: str
    invalidation: str
    created_at: str = field(
        default_factory=lambda: (
            utc_now().isoformat()
        )
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


@dataclass
class ContradictionRecord:
    contradiction_id: str
    expert: str
    rule_topic: str
    finding_ids: list[str]
    description: str
    severity: str
    resolution_status: str
    resolution_notes: str = ""
    created_at: str = field(
        default_factory=lambda: (
            utc_now().isoformat()
        )
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


@dataclass
class PromotionDecision:
    promotion_id: str
    finding_id: str
    expert: str
    decision: str
    score: float
    reasons: list[str]
    target_status: str
    approved_by: str
    created_at: str = field(
        default_factory=lambda: (
            utc_now().isoformat()
        )
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


EVIDENCE_WEIGHTS = {
    "primary": 1.00,
    "official": 0.95,
    "secondary": 0.70,
    "community": 0.40,
    "inference": 0.25,
    "unverified": 0.10,
}


RULE_TYPES = {
    "market_selection",
    "regime",
    "entry",
    "confirmation",
    "stop",
    "target",
    "management",
    "risk",
    "avoidance",
}


REVIEW_STATUSES = {
    "pending",
    "in_progress",
    "completed",
    "recheck_required",
}


IMPLEMENTATION_STATUSES = {
    "research",
    "candidate",
    "validated",
    "rejected",
}


PROMOTION_DECISIONS = {
    "promote",
    "hold",
    "reject",
}


class SourceReviewEngine:
    def add_review(
        self,
        *,
        expert: str,
        source_id: str,
        title: str,
        source_type: str,
        section: str,
        reviewer: str,
        status: str,
        progress_percentage: float,
        primary_source: bool,
        source_reference: str,
        notes: str = "",
    ) -> ReviewRecord:
        status = status.strip().lower()

        if status not in REVIEW_STATUSES:
            raise ValueError(
                f"Invalid review status: {status}"
            )

        progress = float(
            progress_percentage
        )

        if not (
            0.0
            <= progress
            <= 100.0
        ):
            raise ValueError(
                "progress_percentage must "
                "be between 0 and 100"
            )

        review_id = stable_id(
            expert,
            source_id,
            section,
        )

        record = ReviewRecord(
            review_id=review_id,
            expert=expert.strip().lower(),
            source_id=source_id.strip(),
            title=title.strip(),
            source_type=(
                source_type
                .strip()
                .lower()
            ),
            section=section.strip(),
            reviewer=reviewer.strip(),
            status=status,
            progress_percentage=progress,
            primary_source=bool(
                primary_source
            ),
            source_reference=(
                source_reference.strip()
            ),
            notes=notes.strip(),
        )

        append_jsonl(
            REVIEW_LOG,
            record.to_dict(),
        )

        return record

    def add_finding(
        self,
        *,
        review_id: str,
        expert: str,
        source_id: str,
        rule_type: str,
        finding: str,
        forex_adaptation: str,
        supporting_excerpt: str,
        location_reference: str,
        evidence_level: str,
        confidence: float,
        mechanical: bool,
        independently_corroborated: bool,
        corroborating_source_ids: list[str],
        implementation_status: str = "research",
        invalidation: str = "",
    ) -> FindingRecord:
        rule_type = (
            rule_type
            .strip()
            .lower()
        )

        if rule_type not in RULE_TYPES:
            raise ValueError(
                f"Invalid rule type: {rule_type}"
            )

        evidence_level = (
            evidence_level
            .strip()
            .lower()
        )

        if evidence_level not in EVIDENCE_WEIGHTS:
            raise ValueError(
                "Invalid evidence level: "
                f"{evidence_level}"
            )

        implementation_status = (
            implementation_status
            .strip()
            .lower()
        )

        if (
            implementation_status
            not in IMPLEMENTATION_STATUSES
        ):
            raise ValueError(
                "Invalid implementation status: "
                f"{implementation_status}"
            )

        confidence_value = float(
            confidence
        )

        if not (
            0.0
            <= confidence_value
            <= 1.0
        ):
            raise ValueError(
                "confidence must be between 0 and 1"
            )

        finding_id = stable_id(
            review_id,
            rule_type,
            finding,
            location_reference,
        )

        record = FindingRecord(
            finding_id=finding_id,
            review_id=review_id.strip(),
            expert=expert.strip().lower(),
            source_id=source_id.strip(),
            rule_type=rule_type,
            finding=finding.strip(),
            forex_adaptation=(
                forex_adaptation.strip()
            ),
            supporting_excerpt=(
                supporting_excerpt.strip()
            ),
            location_reference=(
                location_reference.strip()
            ),
            evidence_level=evidence_level,
            confidence=confidence_value,
            mechanical=bool(
                mechanical
            ),
            independently_corroborated=bool(
                independently_corroborated
            ),
            corroborating_source_ids=[
                str(item).strip()
                for item
                in corroborating_source_ids
                if str(item).strip()
            ],
            implementation_status=(
                implementation_status
            ),
            invalidation=invalidation.strip(),
        )

        append_jsonl(
            FINDINGS_LOG,
            record.to_dict(),
        )

        return record

    def add_contradiction(
        self,
        *,
        expert: str,
        rule_topic: str,
        finding_ids: list[str],
        description: str,
        severity: str,
        resolution_status: str = "open",
        resolution_notes: str = "",
    ) -> ContradictionRecord:
        severity = severity.strip().lower()

        if severity not in {
            "low",
            "medium",
            "high",
            "critical",
        }:
            raise ValueError(
                f"Invalid severity: {severity}"
            )

        resolution_status = (
            resolution_status
            .strip()
            .lower()
        )

        if resolution_status not in {
            "open",
            "reviewing",
            "resolved",
            "dismissed",
        }:
            raise ValueError(
                "Invalid resolution status: "
                f"{resolution_status}"
            )

        clean_finding_ids = [
            str(item).strip()
            for item in finding_ids
            if str(item).strip()
        ]

        if len(
            clean_finding_ids
        ) < 2:
            raise ValueError(
                "A contradiction requires at "
                "least two finding IDs"
            )

        contradiction_id = stable_id(
            expert,
            rule_topic,
            *sorted(
                clean_finding_ids
            ),
        )

        record = ContradictionRecord(
            contradiction_id=(
                contradiction_id
            ),
            expert=expert.strip().lower(),
            rule_topic=rule_topic.strip(),
            finding_ids=clean_finding_ids,
            description=description.strip(),
            severity=severity,
            resolution_status=(
                resolution_status
            ),
            resolution_notes=(
                resolution_notes.strip()
            ),
        )

        append_jsonl(
            CONTRADICTIONS_LOG,
            record.to_dict(),
        )

        return record

    def score_finding(
        self,
        finding: dict[str, Any],
    ) -> tuple[float, list[str]]:
        reasons: list[str] = []

        evidence_level = str(
            finding.get(
                "evidence_level",
                "unverified",
            )
        ).lower()

        evidence_score = (
            EVIDENCE_WEIGHTS.get(
                evidence_level,
                0.10,
            )
        )

        confidence = float(
            finding.get(
                "confidence",
                0.0,
            )
        )

        mechanical = bool(
            finding.get(
                "mechanical",
                False,
            )
        )

        corroborated = bool(
            finding.get(
                "independently_corroborated",
                False,
            )
        )

        corroborating_sources = list(
            finding.get(
                "corroborating_source_ids",
                [],
            )
            or []
        )

        has_invalidation = bool(
            str(
                finding.get(
                    "invalidation",
                    "",
                )
            ).strip()
        )

        has_forex_adaptation = bool(
            str(
                finding.get(
                    "forex_adaptation",
                    "",
                )
            ).strip()
        )

        score = (
            evidence_score * 35.0
            + confidence * 30.0
            + (
                12.0
                if mechanical
                else 0.0
            )
            + (
                10.0
                if corroborated
                else 0.0
            )
            + min(
                len(
                    corroborating_sources
                )
                * 3.0,
                6.0,
            )
            + (
                4.0
                if has_invalidation
                else 0.0
            )
            + (
                3.0
                if has_forex_adaptation
                else 0.0
            )
        )

        if evidence_score >= 0.95:
            reasons.append(
                "primary or official evidence"
            )

        else:
            reasons.append(
                "evidence is below primary level"
            )

        if confidence >= 0.80:
            reasons.append(
                "high extraction confidence"
            )

        else:
            reasons.append(
                "confidence below 0.80"
            )

        if mechanical:
            reasons.append(
                "rule can be expressed mechanically"
            )

        else:
            reasons.append(
                "rule remains discretionary"
            )

        if corroborated:
            reasons.append(
                "independently corroborated"
            )

        else:
            reasons.append(
                "not independently corroborated"
            )

        if has_invalidation:
            reasons.append(
                "invalidation condition documented"
            )

        else:
            reasons.append(
                "missing invalidation condition"
            )

        return (
            min(
                round(
                    score,
                    2,
                ),
                100.0,
            ),
            reasons,
        )

    def open_contradictions_for_finding(
        self,
        finding_id: str,
    ) -> list[dict[str, Any]]:
        contradictions = latest_by_id(
            read_jsonl(
                CONTRADICTIONS_LOG
            ),
            "contradiction_id",
        ).values()

        return [
            item
            for item in contradictions
            if (
                finding_id
                in list(
                    item.get(
                        "finding_ids",
                        [],
                    )
                    or []
                )
                and str(
                    item.get(
                        "resolution_status",
                        "open",
                    )
                )
                not in {
                    "resolved",
                    "dismissed",
                }
            )
        ]

    def evaluate_promotion(
        self,
        finding_id: str,
        *,
        approved_by: str = "research_engine",
        record_decision: bool = True,
    ) -> PromotionDecision:
        findings = latest_by_id(
            read_jsonl(
                FINDINGS_LOG
            ),
            "finding_id",
        )

        if finding_id not in findings:
            raise KeyError(
                f"Unknown finding: {finding_id}"
            )

        finding = findings[
            finding_id
        ]

        score, reasons = self.score_finding(
            finding
        )

        contradictions = (
            self.open_contradictions_for_finding(
                finding_id
            )
        )

        if contradictions:
            reasons.append(
                f"{len(contradictions)} unresolved "
                "contradiction(s)"
            )

        evidence_level = str(
            finding.get(
                "evidence_level",
                "unverified",
            )
        ).lower()

        confidence = float(
            finding.get(
                "confidence",
                0.0,
            )
        )

        mechanical = bool(
            finding.get(
                "mechanical",
                False,
            )
        )

        corroborated = bool(
            finding.get(
                "independently_corroborated",
                False,
            )
        )

        if contradictions:
            decision = "hold"
            target_status = "research"

        elif (
            score >= 82.0
            and evidence_level
            in {
                "primary",
                "official",
            }
            and confidence >= 0.80
            and mechanical
            and corroborated
        ):
            decision = "promote"
            target_status = "candidate"

        elif (
            score < 45.0
            or evidence_level
            in {
                "unverified",
                "inference",
            }
            and confidence < 0.50
        ):
            decision = "reject"
            target_status = "rejected"

        else:
            decision = "hold"
            target_status = "research"

        promotion_id = stable_id(
            finding_id,
            decision,
            str(
                score
            ),
        )

        record = PromotionDecision(
            promotion_id=promotion_id,
            finding_id=finding_id,
            expert=str(
                finding.get(
                    "expert",
                    "",
                )
            ),
            decision=decision,
            score=score,
            reasons=reasons,
            target_status=target_status,
            approved_by=approved_by,
        )

        if record_decision:
            append_jsonl(
                PROMOTIONS_LOG,
                record.to_dict(),
            )

        return record

    def rebuild_summary(
        self,
    ) -> dict[str, Any]:
        reviews = latest_by_id(
            read_jsonl(
                REVIEW_LOG
            ),
            "review_id",
        )

        findings = latest_by_id(
            read_jsonl(
                FINDINGS_LOG
            ),
            "finding_id",
        )

        contradictions = latest_by_id(
            read_jsonl(
                CONTRADICTIONS_LOG
            ),
            "contradiction_id",
        )

        promotions = latest_by_id(
            read_jsonl(
                PROMOTIONS_LOG
            ),
            "promotion_id",
        )

        completed_reviews = [
            item
            for item in reviews.values()
            if str(
                item.get(
                    "status",
                    ""
                )
            )
            == "completed"
        ]

        open_contradictions = [
            item
            for item in contradictions.values()
            if str(
                item.get(
                    "resolution_status",
                    "open",
                )
            )
            not in {
                "resolved",
                "dismissed",
            }
        ]

        promotion_counts = {
            "promote": 0,
            "hold": 0,
            "reject": 0,
        }

        for finding_id in findings:
            decision = self.evaluate_promotion(
                finding_id,
                record_decision=False,
            )

            promotion_counts[
                decision.decision
            ] += 1

        experts = sorted(
            {
                str(
                    item.get(
                        "expert",
                        "",
                    )
                )
                for item in (
                    list(
                        reviews.values()
                    )
                    + list(
                        findings.values()
                    )
                )
                if str(
                    item.get(
                        "expert",
                        "",
                    )
                ).strip()
            }
        )

        summary = {
            "timestamp": utc_now().isoformat(),
            "research_only": True,
            "automatic_trading_enabled": False,
            "reviews": len(
                reviews
            ),
            "completed_reviews": len(
                completed_reviews
            ),
            "findings": len(
                findings
            ),
            "contradictions": len(
                contradictions
            ),
            "open_contradictions": len(
                open_contradictions
            ),
            "recorded_promotion_decisions": len(
                promotions
            ),
            "current_promotion_evaluation": (
                promotion_counts
            ),
            "experts_with_reviews": experts,
        }

        SUMMARY_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        SUMMARY_FILE.write_text(
            json.dumps(
                summary,
                indent=2,
            ),
            encoding="utf-8",
        )

        self.build_report(
            summary=summary,
            reviews=list(
                reviews.values()
            ),
            findings=list(
                findings.values()
            ),
            contradictions=list(
                contradictions.values()
            ),
        )

        return summary

    def build_report(
        self,
        *,
        summary: dict[str, Any],
        reviews: list[dict[str, Any]],
        findings: list[dict[str, Any]],
        contradictions: list[dict[str, Any]],
    ) -> Path:
        lines = [
            (
                "# OANDA Master Alliance "
                "— Source Review Engine"
            ),
            "",
            "## Summary",
            "",
            (
                "- Reviews: "
                f"**{summary['reviews']}**"
            ),
            (
                "- Completed reviews: "
                f"**{summary['completed_reviews']}**"
            ),
            (
                "- Extracted findings: "
                f"**{summary['findings']}**"
            ),
            (
                "- Contradictions: "
                f"**{summary['contradictions']}**"
            ),
            (
                "- Open contradictions: "
                f"**{summary['open_contradictions']}**"
            ),
            (
                "- Promotion candidates: "
                f"**{summary['current_promotion_evaluation']['promote']}**"
            ),
            (
                "- Findings on hold: "
                f"**{summary['current_promotion_evaluation']['hold']}**"
            ),
            (
                "- Rejected findings: "
                f"**{summary['current_promotion_evaluation']['reject']}**"
            ),
            "- Automatic trading enabled: **no**",
            "",
            "## Source reviews",
            "",
        ]

        if reviews:
            lines.extend(
                [
                    (
                        "| Expert | Source | Section | "
                        "Status | Progress |"
                    ),
                    "|---|---|---|---|---:|",
                ]
            )

            for item in sorted(
                reviews,
                key=lambda row: (
                    str(
                        row.get(
                            "expert",
                            "",
                        )
                    ),
                    str(
                        row.get(
                            "title",
                            "",
                        )
                    ),
                    str(
                        row.get(
                            "section",
                            "",
                        )
                    ),
                ),
            ):
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(
                                item.get(
                                    "expert",
                                    "",
                                )
                            ),
                            str(
                                item.get(
                                    "title",
                                    "",
                                )
                            ).replace(
                                "|",
                                "/",
                            ),
                            str(
                                item.get(
                                    "section",
                                    "",
                                )
                            ).replace(
                                "|",
                                "/",
                            ),
                            str(
                                item.get(
                                    "status",
                                    "",
                                )
                            ),
                            (
                                f"{float(item.get('progress_percentage', 0.0)):.1f}%"
                            ),
                        ]
                    )
                    + " |"
                )

        else:
            lines.append(
                "No source reviews have been recorded."
            )

        lines.extend(
            [
                "",
                "## Findings",
                "",
            ]
        )

        if findings:
            lines.extend(
                [
                    (
                        "| Expert | Type | Evidence | "
                        "Confidence | Score | Decision | Finding |"
                    ),
                    "|---|---|---|---:|---:|---|---|",
                ]
            )

            for item in sorted(
                findings,
                key=lambda row: (
                    str(
                        row.get(
                            "expert",
                            "",
                        )
                    ),
                    str(
                        row.get(
                            "rule_type",
                            "",
                        )
                    ),
                ),
            ):
                decision = self.evaluate_promotion(
                    str(
                        item.get(
                            "finding_id",
                            "",
                        )
                    ),
                    record_decision=False,
                )

                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(
                                item.get(
                                    "expert",
                                    "",
                                )
                            ),
                            str(
                                item.get(
                                    "rule_type",
                                    "",
                                )
                            ),
                            str(
                                item.get(
                                    "evidence_level",
                                    "",
                                )
                            ),
                            (
                                f"{float(item.get('confidence', 0.0)):.2f}"
                            ),
                            f"{decision.score:.1f}",
                            decision.decision,
                            str(
                                item.get(
                                    "finding",
                                    "",
                                )
                            )
                            .replace(
                                "|",
                                "/",
                            )
                            .replace(
                                "\n",
                                " ",
                            ),
                        ]
                    )
                    + " |"
                )

        else:
            lines.append(
                "No findings have been extracted."
            )

        lines.extend(
            [
                "",
                "## Open contradictions",
                "",
            ]
        )

        open_items = [
            item
            for item in contradictions
            if str(
                item.get(
                    "resolution_status",
                    "open",
                )
            )
            not in {
                "resolved",
                "dismissed",
            }
        ]

        if open_items:
            for item in open_items:
                lines.extend(
                    [
                        (
                            "### "
                            + str(
                                item.get(
                                    "expert",
                                    "unknown",
                                )
                            )
                            + " — "
                            + str(
                                item.get(
                                    "rule_topic",
                                    "unknown",
                                )
                            )
                        ),
                        "",
                        (
                            "- Severity: **"
                            + str(
                                item.get(
                                    "severity",
                                    "unknown",
                                )
                            )
                            + "**"
                        ),
                        (
                            "- Status: **"
                            + str(
                                item.get(
                                    "resolution_status",
                                    "open",
                                )
                            )
                            + "**"
                        ),
                        (
                            "- Description: "
                            + str(
                                item.get(
                                    "description",
                                    "",
                                )
                            )
                        ),
                        "",
                    ]
                )

        else:
            lines.append(
                "No unresolved contradictions."
            )

        lines.extend(
            [
                "",
                "## Promotion standard",
                "",
                (
                    "A rule can become a candidate only "
                    "when it has strong primary or official "
                    "evidence, confidence of at least 0.80, "
                    "a mechanical definition, independent "
                    "corroboration and no unresolved "
                    "contradiction."
                ),
                "",
                "## Guardrails",
                "",
                "- Research records do not place orders.",
                (
                    "- Candidate status does not mean "
                    "production approval."
                ),
                (
                    "- Every candidate still requires "
                    "pair-specific backtesting, walk-forward "
                    "testing and Monte Carlo analysis."
                ),
                (
                    "- No OANDA or Alpaca settings are "
                    "modified by this engine."
                ),
                "",
            ]
        )

        REPORT_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        REPORT_FILE.write_text(
            "\n".join(
                lines
            ),
            encoding="utf-8",
        )

        return REPORT_FILE

    def queue_rows(
        self,
    ) -> list[dict[str, str]]:
        if not QUEUE_FILE.exists():
            return []

        with QUEUE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            return list(
                csv.DictReader(
                    handle
                )
            )

    def next_queue_items(
        self,
        count: int = 5,
    ) -> list[dict[str, str]]:
        rows = self.queue_rows()

        pending = [
            row
            for row in rows
            if str(
                row.get(
                    "status",
                    "pending",
                )
            ).strip().lower()
            not in {
                "completed",
                "rejected",
            }
        ]

        return sorted(
            pending,
            key=lambda row: (
                int(
                    row.get(
                        "priority",
                        999,
                    )
                    or 999
                ),
                str(
                    row.get(
                        "expert",
                        "",
                    )
                ),
            ),
        )[
            :max(
                int(
                    count
                ),
                0,
            )
        ]

    def validate_storage(
        self,
    ) -> list[str]:
        errors: list[str] = []

        reviews = read_jsonl(
            REVIEW_LOG
        )

        findings = read_jsonl(
            FINDINGS_LOG
        )

        contradictions = read_jsonl(
            CONTRADICTIONS_LOG
        )

        review_ids = {
            str(
                item.get(
                    "review_id",
                    "",
                )
            )
            for item in reviews
        }

        finding_ids = {
            str(
                item.get(
                    "finding_id",
                    "",
                )
            )
            for item in findings
        }

        for finding in findings:
            review_id = str(
                finding.get(
                    "review_id",
                    "",
                )
            )

            if (
                review_id
                and review_id
                not in review_ids
            ):
                errors.append(
                    "finding references unknown "
                    f"review: {review_id}"
                )

            confidence = float(
                finding.get(
                    "confidence",
                    -1,
                )
            )

            if not (
                0.0
                <= confidence
                <= 1.0
            ):
                errors.append(
                    "finding confidence outside "
                    f"[0,1]: {finding.get('finding_id')}"
                )

        for contradiction in contradictions:
            for finding_id in list(
                contradiction.get(
                    "finding_ids",
                    [],
                )
                or []
            ):
                if finding_id not in finding_ids:
                    errors.append(
                        "contradiction references "
                        f"unknown finding: {finding_id}"
                    )

        return errors
