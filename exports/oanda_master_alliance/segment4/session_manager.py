from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from segment4.common import (
    ROOT,
    read_json,
)


@dataclass
class SessionDecision:
    allowed: bool
    reason: str
    session_name: str
    active_sessions: list[str]
    market_snapshot_present: bool
    environment: str
    weekend: bool

    def to_dict(
        self,
    ) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "session_name": self.session_name,
            "active_sessions": self.active_sessions,
            "market_snapshot_present": (
                self.market_snapshot_present
            ),
            "environment": self.environment,
            "weekend": self.weekend,
        }


class SessionManager:
    def __init__(
        self,
        config: dict,
    ) -> None:
        self.config = config

    def evaluate(
        self,
    ) -> SessionDecision:
        now_et = datetime.now(
            ZoneInfo(
                "America/New_York"
            )
        )

        weekend = (
            now_et.weekday()
            >= 5
        )

        snapshot_path = (
            ROOT
            / "state/market_snapshot.json"
        )

        snapshot = read_json(
            snapshot_path,
            default={},
        )

        if not isinstance(
            snapshot,
            dict,
        ):
            snapshot = {}

        environment = str(
            snapshot.get(
                "environment",
                "unknown",
            )
        ).lower()

        session_name = str(
            snapshot.get(
                "session",
                "unknown",
            )
        )

        active_sessions_raw = snapshot.get(
            "active_sessions",
            [],
        )

        active_sessions = [
            str(
                item
            )
            for item in active_sessions_raw
            if str(
                item
            ).strip()
        ]

        if weekend and not bool(
            self.config.get(
                "allow_weekend_runtime",
                False,
            )
        ):
            return SessionDecision(
                allowed=False,
                reason="weekend_runtime_blocked",
                session_name=session_name,
                active_sessions=active_sessions,
                market_snapshot_present=(
                    snapshot_path.exists()
                ),
                environment=environment,
                weekend=True,
            )

        if environment == "live":
            return SessionDecision(
                allowed=False,
                reason="live_environment_blocked",
                session_name=session_name,
                active_sessions=active_sessions,
                market_snapshot_present=(
                    snapshot_path.exists()
                ),
                environment=environment,
                weekend=weekend,
            )

        allow_outside = bool(
            self.config.get(
                "allow_outside_active_session",
                False,
            )
        )

        if (
            not active_sessions
            and not allow_outside
        ):
            return SessionDecision(
                allowed=False,
                reason="no_active_forex_session",
                session_name=session_name,
                active_sessions=active_sessions,
                market_snapshot_present=(
                    snapshot_path.exists()
                ),
                environment=environment,
                weekend=weekend,
            )

        return SessionDecision(
            allowed=True,
            reason="session_allowed",
            session_name=session_name,
            active_sessions=active_sessions,
            market_snapshot_present=(
                snapshot_path.exists()
            ),
            environment=environment,
            weekend=weekend,
        )
