from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.common import ROOT


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


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


@dataclass
class AuthorizationDecision:
    authorized: bool
    authorization_id: str
    instrument: str
    side: str
    mode: str
    reasons: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": utc_now_iso(),
            "authorized": self.authorized,
            "authorization_id": (
                self.authorization_id
            ),
            "instrument": self.instrument,
            "side": self.side,
            "mode": self.mode,
            "reasons": self.reasons,
        }


class ExecutionAuthorization:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config
        self.segment3 = config["segment3"]
        self.files = self.segment3["files"]

    def path(
        self,
        key: str,
    ) -> Path:
        return ROOT / self.files[key]

    @staticmethod
    def authorization_id(
        candidate: dict[str, Any],
    ) -> str:
        payload = "|".join(
            [
                str(
                    candidate.get(
                        "instrument",
                        "",
                    )
                ),
                str(
                    candidate.get(
                        "side",
                        "",
                    )
                ),
                str(
                    candidate.get(
                        "entry_price",
                        "",
                    )
                ),
                str(
                    candidate.get(
                        "stop_price",
                        "",
                    )
                ),
                str(
                    candidate.get(
                        "target_price",
                        "",
                    )
                ),
            ]
        )

        return hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()[:20]

    def processed_ids(
        self,
    ) -> set[str]:
        path = self.path(
            "processed_signals"
        )

        if not path.exists():
            return set()

        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            return set()

        return set(
            payload.get(
                "authorization_ids",
                [],
            )
        )

    def mark_processed(
        self,
        authorization_id: str,
    ) -> None:
        identifiers = self.processed_ids()
        identifiers.add(
            authorization_id
        )

        path = self.path(
            "processed_signals"
        )
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            json.dumps(
                {
                    "updated_at": (
                        utc_now_iso()
                    ),
                    "authorization_ids": (
                        sorted(identifiers)
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def evaluate(
        self,
        *,
        candidate: dict[str, Any],
        risk_approved: bool,
        submit_requested: bool,
    ) -> AuthorizationDecision:
        reasons: list[str] = []

        instrument = str(
            candidate.get(
                "instrument",
                "",
            )
        )
        side = str(
            candidate.get(
                "side",
                "",
            )
        )

        identifier = (
            self.authorization_id(
                candidate
            )
        )

        environment = str(
            self.segment3.get(
                "environment",
                "practice",
            )
        ).lower()

        if not bool(
            self.segment3.get(
                "enabled",
                False,
            )
        ):
            reasons.append(
                "segment3_disabled"
            )

        if not risk_approved:
            reasons.append(
                "risk_not_approved"
            )

        if bool(
            self.segment3.get(
                "practice_only",
                True,
            )
        ) and environment != "practice":
            reasons.append(
                "non_practice_environment_blocked"
            )

        if self.path(
            "kill_switch"
        ).exists():
            reasons.append(
                "kill_switch_enabled"
            )

        if identifier in self.processed_ids():
            reasons.append(
                "duplicate_signal"
            )

        automatic_execution = bool(
            self.segment3.get(
                "automatic_execution",
                False,
            )
        )

        submission_enabled = bool(
            self.segment3.get(
                "practice_order_submission_enabled",
                False,
            )
        )

        if submit_requested:
            if not automatic_execution:
                reasons.append(
                    "automatic_execution_disabled"
                )

            if not submission_enabled:
                reasons.append(
                    "practice_order_submission_disabled"
                )

        mode = (
            "practice_submit"
            if submit_requested
            else "research_preview"
        )

        decision = AuthorizationDecision(
            authorized=not reasons,
            authorization_id=identifier,
            instrument=instrument,
            side=side,
            mode=mode,
            reasons=reasons,
        )

        append_jsonl(
            self.path(
                "authorization_log"
            ),
            decision.to_dict(),
        )

        return decision
