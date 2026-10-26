from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Iterable

from expert_library.models import ExpertProfile
from expert_library.profiles.catalog import (
    build_profiles,
)


class ExpertLibrary:
    def __init__(
        self,
        profiles: Iterable[ExpertProfile]
        | None = None,
    ) -> None:
        items = list(
            profiles
            if profiles is not None
            else build_profiles()
        )

        self._profiles = {
            item.expert_id: item
            for item in items
        }

    def names(self) -> list[str]:
        return sorted(
            self._profiles
        )

    def get(
        self,
        expert_id: str,
    ) -> ExpertProfile:
        key = (
            expert_id
            .strip()
            .lower()
        )

        if key not in self._profiles:
            raise KeyError(
                f"Unknown expert: {expert_id}"
            )

        return self._profiles[key]

    def all(self) -> list[ExpertProfile]:
        return [
            self._profiles[key]
            for key in self.names()
        ]

    def validate(self) -> list[str]:
        errors: list[str] = []

        all_source_ids: set[str] = set()
        all_rule_ids: set[str] = set()
        all_strategy_ids: set[str] = set()

        for profile in self.all():
            local_source_ids = {
                item.source_id
                for item in profile.sources
            }

            for item in profile.sources:
                if item.source_id in all_source_ids:
                    errors.append(
                        "duplicate source_id: "
                        f"{item.source_id}"
                    )

                all_source_ids.add(
                    item.source_id
                )

                if (
                    item.evidence_level
                    == "primary"
                    and not item.reference
                ):
                    errors.append(
                        f"{item.source_id}: "
                        "primary source has no reference"
                    )

            for item in profile.doctrine:
                if item.rule_id in all_rule_ids:
                    errors.append(
                        "duplicate rule_id: "
                        f"{item.rule_id}"
                    )

                all_rule_ids.add(
                    item.rule_id
                )

                if not (
                    0.0
                    <= item.confidence
                    <= 1.0
                ):
                    errors.append(
                        f"{item.rule_id}: "
                        "confidence outside [0,1]"
                    )

                for source_id in item.source_ids:
                    if (
                        source_id
                        not in local_source_ids
                        and source_id
                        not in all_source_ids
                    ):
                        errors.append(
                            f"{item.rule_id}: "
                            "unresolved source "
                            f"{source_id}"
                        )

            for item in profile.strategies:
                if (
                    item.strategy_id
                    in all_strategy_ids
                ):
                    errors.append(
                        "duplicate strategy_id: "
                        f"{item.strategy_id}"
                    )

                all_strategy_ids.add(
                    item.strategy_id
                )

                if (
                    item.status
                    != "research_only"
                ):
                    errors.append(
                        f"{item.strategy_id}: "
                        "must default to "
                        "research_only"
                    )

                if not item.entry_rules:
                    errors.append(
                        f"{item.strategy_id}: "
                        "has no entry rules"
                    )

                if not item.exit_rules:
                    errors.append(
                        f"{item.strategy_id}: "
                        "has no exit rules"
                    )

                if not item.risk_rules:
                    errors.append(
                        f"{item.strategy_id}: "
                        "has no risk rules"
                    )

        return errors

    def coverage(self) -> dict:
        profiles = self.all()

        sources = [
            source
            for profile in profiles
            for source in profile.sources
        ]

        doctrine = [
            rule
            for profile in profiles
            for rule in profile.doctrine
        ]

        strategies = [
            strategy
            for profile in profiles
            for strategy in profile.strategies
        ]

        return {
            "experts": len(profiles),
            "sources_registered": len(
                sources
            ),
            "sources_reviewed": sum(
                bool(item.reviewed)
                for item in sources
            ),
            "source_types": dict(
                Counter(
                    item.source_type
                    for item in sources
                )
            ),
            "evidence_levels": dict(
                Counter(
                    item.evidence_level
                    for item in sources
                )
            ),
            "doctrine_rules": len(
                doctrine
            ),
            "mechanical_rules": sum(
                bool(item.mechanical)
                for item in doctrine
            ),
            "strategies": len(
                strategies
            ),
            "production_enabled": False,
        }

    def export(
        self,
        output_directory: str | Path,
    ) -> Path:
        output = Path(
            output_directory
        )

        output.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {
            "metadata": {
                "library": (
                    "OANDA Master Alliance "
                    "Expert Library"
                ),
                "version": "0.1.0",
                "research_only": True,
                "automatic_trading_enabled": (
                    False
                ),
            },
            "coverage": self.coverage(),
            "experts": [
                item.to_dict()
                for item in self.all()
            ],
        }

        destination = (
            output
            / "expert_library.json"
        )

        destination.write_text(
            json.dumps(
                payload,
                indent=2,
            ),
            encoding="utf-8",
        )

        return destination
