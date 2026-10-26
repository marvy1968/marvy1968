from __future__ import annotations

import os
from typing import Any

from segment4b4.campaign_state import (
    CampaignState,
)
from segment4b4.checkpoints import (
    CampaignCheckpointManager,
)
from segment4b4.common import (
    append_jsonl,
    read_json,
    resolve_path,
    utc_now_iso,
)
from segment4b4.models import (
    CampaignCycleResult,
)
from segment4b4.shadow_bridge import (
    ShadowStateBridge,
)
from segment4b4.subprocess_runner import (
    SafeStageRunner,
    StageResult,
)


class ShadowCampaignEngine:
    """
    Orchestrates one research-only shadow campaign cycle.

    The engine runs:
      1. Segment 2 market and alliance refresh.
      2. Segment 3 risk-preview analysis.
      3. Segment 4B-2 shadow-trading cycle.
      4. Segment 4B-3 readiness analysis.

    It never calls a broker order endpoint.
    """

    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config

        self.state = CampaignState(
            config
        )

        self.bridge = ShadowStateBridge()

        campaign = config.get(
            "campaign",
            {},
        )

        if not isinstance(
            campaign,
            dict,
        ):
            campaign = {}

        self.runner = SafeStageRunner(
            timeout_seconds=int(
                campaign.get(
                    "stage_timeout_seconds",
                    600,
                )
            )
        )

        self.history_path = resolve_path(
            config,
            "campaign_history",
        )

        self.error_path = resolve_path(
            config,
            "campaign_errors",
        )

    def _load_control(
        self,
    ) -> dict[str, Any]:
        payload = read_json(
            self.state.control_path,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            return {}

        return payload

    @staticmethod
    def _safe_payload(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(payload)

        result.update(
            {
                "automatic_execution": False,
                "order_submission": False,
            }
        )

        return result

    @staticmethod
    def _stage_error(
        stage: StageResult,
    ) -> str:
        if stage.error:
            return stage.error

        return (
            f"{stage.name} failed with "
            f"exit code {stage.return_code}"
        )

    def _write_nonrunning_status(
        self,
        *,
        previous: dict[str, Any],
        cycle_number: int,
        state_name: str,
        message: str,
    ) -> dict[str, Any]:
        payload = self._safe_payload(
            {
                **previous,
                "timestamp": utc_now_iso(),
                "state": state_name,
                "running": False,
                "pid": None,
                "cycle_number": cycle_number,
                "paused": self.state.paused(),
                "shutdown_requested": (
                    self.state.shutdown_requested()
                ),
                "message": message,
            }
        )

        self.state.save(
            payload
        )

        return payload

    def _run_stage(
        self,
        *,
        name: str,
        script: str,
        arguments: tuple[str, ...] = (),
    ) -> StageResult:
        return self.runner.run_python(
            name,
            script,
            *arguments,
        )

    def _run_pipeline(
        self,
    ) -> tuple[
        list[StageResult],
        list[str],
    ]:
        stages: list[StageResult] = []
        errors: list[str] = []

        definitions = [
            (
                "segment2_refresh",
                "run_segment2.py",
                (
                    "--refresh-foundation",
                ),
            ),
            (
                "segment3_research_preview",
                "run_segment3.py",
                (),
            ),
            (
                "segment4b2_shadow_cycle",
                "run_segment4b2.py",
                (),
            ),
            (
                "segment4b3_readiness",
                "run_segment4b3.py",
                (),
            ),
        ]

        for name, script, arguments in definitions:
            stage = self._run_stage(
                name=name,
                script=script,
                arguments=arguments,
            )

            stages.append(stage)

            if not stage.successful:
                errors.append(
                    self._stage_error(
                        stage
                    )
                )

                # Later stages depend on earlier stages.
                break

        return stages, errors

    @staticmethod
    def _opened_this_cycle(
        *,
        open_before: int,
        closed_before: int,
        open_after: int,
        closed_after: int,
    ) -> int:
        return max(
            0,
            (
                open_after
                + closed_after
                - open_before
                - closed_before
            ),
        )

    @staticmethod
    def _closed_this_cycle(
        *,
        closed_before: int,
        closed_after: int,
    ) -> int:
        return max(
            0,
            closed_after - closed_before,
        )

    def run_cycle(
        self,
    ) -> dict[str, Any]:
        previous = self.state.load()
        control = self._load_control()

        cycle_number = (
            int(
                previous.get(
                    "cycle_number",
                    0,
                )
            )
            + 1
        )

        if not bool(
            control.get(
                "campaign_enabled",
                True,
            )
        ):
            return self._write_nonrunning_status(
                previous=previous,
                cycle_number=cycle_number,
                state_name="campaign_disabled",
                message=(
                    "Campaign is disabled in "
                    "campaign_control.json."
                ),
            )

        if (
            self.state.paused()
            or bool(
                control.get(
                    "manual_pause",
                    False,
                )
            )
        ):
            return self._write_nonrunning_status(
                previous=previous,
                cycle_number=cycle_number,
                state_name="paused",
                message=(
                    "Campaign is paused for "
                    "manual review."
                ),
            )

        if self.state.shutdown_requested():
            return self._write_nonrunning_status(
                previous=previous,
                cycle_number=cycle_number,
                state_name="shutdown_requested",
                message=(
                    "Graceful campaign shutdown "
                    "has been requested."
                ),
            )

        started_at = utc_now_iso()

        shadow_before = (
            self.bridge.summary()
        )

        open_before = int(
            shadow_before.get(
                "open_trade_count",
                0,
            )
        )

        closed_before = int(
            shadow_before.get(
                "closed_trade_count",
                0,
            )
        )

        stages, errors = (
            self._run_pipeline()
        )

        shadow_after = (
            self.bridge.summary()
        )

        open_after = int(
            shadow_after.get(
                "open_trade_count",
                0,
            )
        )

        closed_after = int(
            shadow_after.get(
                "closed_trade_count",
                0,
            )
        )

        opened_this_cycle = (
            self._opened_this_cycle(
                open_before=open_before,
                closed_before=closed_before,
                open_after=open_after,
                closed_after=closed_after,
            )
        )

        closed_this_cycle = (
            self._closed_this_cycle(
                closed_before=closed_before,
                closed_after=closed_after,
            )
        )

        shadow_status = (
            self.bridge.status()
        )

        candidates_loaded = int(
            shadow_status.get(
                "candidates_loaded",
                0,
            )
            or 0
        )

        candidates_approved = int(
            shadow_status.get(
                "candidates_approved",
                0,
            )
            or 0
        )

        open_trades_checked = int(
            shadow_status.get(
                "open_trades_checked",
                0,
            )
            or 0
        )

        checkpoint = (
            CampaignCheckpointManager(
                self.config,
                control,
            ).evaluate(
                closed_after
            )
        )

        successful = (
            len(errors) == 0
        )

        prior_error_count = int(
            previous.get(
                "consecutive_errors",
                0,
            )
        )

        consecutive_errors = (
            0
            if successful
            else prior_error_count + 1
        )

        cycle_result = CampaignCycleResult(
            timestamp=utc_now_iso(),
            cycle_number=cycle_number,
            successful=successful,
            candidates_loaded=(
                candidates_loaded
            ),
            candidates_approved=(
                candidates_approved
            ),
            shadow_trades_opened=(
                opened_this_cycle
            ),
            open_trades_checked=(
                open_trades_checked
            ),
            shadow_trades_closed=(
                closed_this_cycle
            ),
            total_open_trades=open_after,
            total_closed_trades=(
                closed_after
            ),
            checkpoint=(
                checkpoint.checkpoint
            ),
            target_closed_trades=(
                checkpoint.target_trades
            ),
            campaign_complete=(
                checkpoint.campaign_complete
            ),
            errors=errors,
        )

        stage_payloads = [
            stage.to_dict()
            for stage in stages
        ]

        history_payload = {
            **cycle_result.to_dict(),
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "shadow_before": shadow_before,
            "shadow_after": shadow_after,
            "checkpoint_decision": (
                checkpoint.to_dict()
            ),
            "stages": stage_payloads,
        }

        append_jsonl(
            self.history_path,
            history_payload,
        )

        if errors:
            append_jsonl(
                self.error_path,
                self._safe_payload(
                    {
                        "timestamp": (
                            utc_now_iso()
                        ),
                        "cycle_number": (
                            cycle_number
                        ),
                        "errors": errors,
                        "stages": (
                            stage_payloads
                        ),
                    }
                ),
            )

        if checkpoint.campaign_complete:
            state_name = (
                "campaign_complete"
            )

        elif checkpoint.stop_requested:
            state_name = (
                "checkpoint_hold"
            )

        elif successful:
            state_name = (
                "cycle_complete"
            )

        else:
            state_name = (
                "cycle_failed"
            )

        status = self._safe_payload(
            {
                "timestamp": utc_now_iso(),
                "state": state_name,
                "running": False,
                "pid": None,
                "cycle_number": (
                    cycle_number
                ),
                "consecutive_errors": (
                    consecutive_errors
                ),
                "open_shadow_trades": (
                    open_after
                ),
                "closed_shadow_trades": (
                    closed_after
                ),
                "target_closed_trades": (
                    checkpoint.target_trades
                ),
                "checkpoint": (
                    checkpoint.checkpoint
                ),
                "campaign_complete": (
                    checkpoint.campaign_complete
                ),
                "checkpoint_stop_requested": (
                    checkpoint.stop_requested
                ),
                "checkpoint_reason": (
                    checkpoint.reason
                ),
                "paused": (
                    self.state.paused()
                ),
                "shutdown_requested": (
                    self.state.shutdown_requested()
                ),
                "last_cycle_successful": (
                    successful
                ),
                "last_cycle": (
                    cycle_result.to_dict()
                ),
                "stages": stage_payloads,
            }
        )

        self.state.save(
            status
        )

        return status

    def run_locked_cycle(
        self,
    ) -> dict[str, Any]:
        self.state.acquire_lock()

        try:
            current = self.state.load()

            current.update(
                {
                    "timestamp": (
                        utc_now_iso()
                    ),
                    "state": (
                        "running_cycle"
                    ),
                    "running": True,
                    "pid": os.getpid(),
                    "paused": (
                        self.state.paused()
                    ),
                    "shutdown_requested": (
                        self.state.shutdown_requested()
                    ),
                    "automatic_execution": (
                        False
                    ),
                    "order_submission": (
                        False
                    ),
                }
            )

            self.state.save(
                current
            )

            return self.run_cycle()

        finally:
            self.state.release_lock()
