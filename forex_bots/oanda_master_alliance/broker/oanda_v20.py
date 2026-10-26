from __future__ import annotations

import os
import time
from typing import Any

import requests

from core.common import (
    environment_flag,
    load_environment,
)


class OandaError(RuntimeError):
    pass


class OandaClient:
    PRACTICE_URL = (
        "https://api-fxpractice.oanda.com"
    )

    LIVE_URL = (
        "https://api-fxtrade.oanda.com"
    )

    def __init__(
        self,
        *,
        token: str | None = None,
        account_id: str | None = None,
        environment: str | None = None,
        timeout_seconds: int = 30,
        maximum_retries: int = 3,
        retry_delay_seconds: int = 2,
    ) -> None:
        load_environment()

        self.token = (
            token
            or os.getenv(
                "OANDA_API_TOKEN",
                "",
            )
        ).strip()

        self.account_id = (
            account_id
            or os.getenv(
                "OANDA_ACCOUNT_ID",
                "",
            )
        ).strip()

        requested_environment = (
            environment
            or os.getenv(
                "OANDA_ENV",
                "practice",
            )
        ).strip().lower()

        if requested_environment not in {
            "practice",
            "live",
        }:
            raise ValueError(
                "OANDA_ENV must be practice or live"
            )

        allow_live = environment_flag(
            "OANDA_ALLOW_LIVE",
            False,
        )

        if (
            requested_environment == "live"
            and not allow_live
        ):
            raise PermissionError(
                "Live OANDA access is locked. "
                "OANDA_ALLOW_LIVE is not true."
            )

        self.environment = (
            requested_environment
        )

        self.base_url = (
            self.PRACTICE_URL
            if self.environment
            == "practice"
            else self.LIVE_URL
        )

        self.timeout_seconds = int(
            timeout_seconds
        )

        self.maximum_retries = max(
            1,
            int(maximum_retries),
        )

        self.retry_delay_seconds = max(
            0,
            int(retry_delay_seconds),
        )

        self.session = requests.Session()

        if self.token:
            self.session.headers.update(
                {
                    "Authorization": (
                        f"Bearer {self.token}"
                    ),
                    "Content-Type": (
                        "application/json"
                    ),
                    "Accept-Datetime-Format": (
                        "RFC3339"
                    ),
                }
            )

    @property
    def credentials_present(self) -> bool:
        return bool(
            self.token
            and self.account_id
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        require_account: bool = False,
    ) -> dict[str, Any]:
        if not self.token:
            raise OandaError(
                "OANDA_API_TOKEN is missing"
            )

        if (
            require_account
            and not self.account_id
        ):
            raise OandaError(
                "OANDA_ACCOUNT_ID is missing"
            )

        url = (
            self.base_url
            + endpoint
        )

        last_error: Exception | None = None

        for attempt in range(
            1,
            self.maximum_retries + 1,
        ):
            try:
                response = (
                    self.session.request(
                        method=method,
                        url=url,
                        params=params,
                        json=payload,
                        timeout=(
                            self.timeout_seconds
                        ),
                    )
                )

                if response.status_code in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }:
                    raise OandaError(
                        "Temporary OANDA error "
                        f"{response.status_code}: "
                        f"{response.text[:500]}"
                    )

                if not response.ok:
                    raise OandaError(
                        "OANDA HTTP "
                        f"{response.status_code}: "
                        f"{response.text[:1000]}"
                    )

                if not response.text:
                    return {}

                data = response.json()

                if not isinstance(
                    data,
                    dict,
                ):
                    raise OandaError(
                        "Unexpected OANDA response"
                    )

                return data

            except (
                requests.RequestException,
                ValueError,
                OandaError,
            ) as exc:
                last_error = exc

                if (
                    attempt
                    >= self.maximum_retries
                ):
                    break

                time.sleep(
                    self.retry_delay_seconds
                    * attempt
                )

        raise OandaError(
            str(last_error)
            if last_error
            else "Unknown OANDA request error"
        )

    def account_summary(
        self,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            (
                f"/v3/accounts/"
                f"{self.account_id}/summary"
            ),
            require_account=True,
        )

    def account_details(
        self,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            (
                f"/v3/accounts/"
                f"{self.account_id}"
            ),
            require_account=True,
        )

    def candles(
        self,
        instrument: str,
        *,
        granularity: str,
        count: int,
        price: str = "M",
        daily_alignment: int = 17,
        alignment_timezone: str = (
            "America/New_York"
        ),
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            (
                f"/v3/instruments/"
                f"{instrument}/candles"
            ),
            params={
                "granularity": granularity,
                "count": int(count),
                "price": price,
                "dailyAlignment": int(
                    daily_alignment
                ),
                "alignmentTimezone": (
                    alignment_timezone
                ),
                "smooth": "false",
            },
        )

    def pricing(
        self,
        instruments: list[str],
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            (
                f"/v3/accounts/"
                f"{self.account_id}/pricing"
            ),
            params={
                "instruments": ",".join(
                    instruments
                )
            },
            require_account=True,
        )

    def open_positions(
        self,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            (
                f"/v3/accounts/"
                f"{self.account_id}/"
                "openPositions"
            ),
            require_account=True,
        )

    def open_trades(
        self,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            (
                f"/v3/accounts/"
                f"{self.account_id}/"
                "openTrades"
            ),
            require_account=True,
        )

    def pending_orders(
        self,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            (
                f"/v3/accounts/"
                f"{self.account_id}/"
                "pendingOrders"
            ),
            require_account=True,
        )

# ---------------------------------------------------------------------
# SEGMENT 3 CLIENT COMPATIBILITY ALIAS
# ---------------------------------------------------------------------
#
# Segment 3 expects the name OandaV20Client. Earlier foundation
# segments may have created the same client under another class name.
# Detect the existing implementation and expose a stable alias without
# changing its behavior, credentials, environment, or order settings.

if "OandaV20Client" not in globals():
    _compatible_client = None

    _preferred_names = (
        "OandaClient",
        "OANDAClient",
        "OandaV20",
        "OandaPracticeClient",
    )

    for _candidate_name in _preferred_names:
        _candidate = globals().get(
            _candidate_name
        )

        if isinstance(
            _candidate,
            type,
        ):
            _compatible_client = _candidate
            break

    if _compatible_client is None:
        for _candidate_name, _candidate in list(
            globals().items()
        ):
            if not isinstance(
                _candidate,
                type,
            ):
                continue

            if _candidate_name in {
                "OandaError",
                "OandaV20Client",
            }:
                continue

            _required_methods = (
                "candles",
                "pricing",
                "account_summary",
            )

            if all(
                hasattr(
                    _candidate,
                    method_name,
                )
                for method_name in _required_methods
            ):
                _compatible_client = _candidate
                break

    if _compatible_client is None:
        raise ImportError(
            "No compatible OANDA client class was found "
            "inside broker/oanda_v20.py"
        )

    OandaV20Client = _compatible_client


__all__ = [
    "OandaV20Client",
    "OandaError",
]
