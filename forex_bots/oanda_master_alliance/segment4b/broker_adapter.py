from __future__ import annotations

import inspect
import os
from typing import Any

from dotenv import load_dotenv

from broker.oanda_v20 import OandaV20Client


class ReadOnlyOandaAdapter:
    """
    Read-only OANDA adapter.

    This module performs GET requests only. It contains no order-create,
    order-replace, order-cancel, trade-close or position-close methods.
    """

    def __init__(self) -> None:
        load_dotenv(
            ".env",
            override=True,
        )

        token = os.getenv(
            "OANDA_API_TOKEN",
            "",
        ).strip()

        account_id = os.getenv(
            "OANDA_ACCOUNT_ID",
            "",
        ).strip()

        environment = os.getenv(
            "OANDA_ENV",
            "practice",
        ).strip().lower()

        if not token:
            raise ValueError(
                "OANDA_API_TOKEN is missing."
            )

        if not account_id:
            raise ValueError(
                "OANDA_ACCOUNT_ID is missing."
            )

        if environment != "practice":
            raise ValueError(
                "Segment 4B-1 permits the practice environment only."
            )

        self.token = token
        self.account_id = account_id
        self.environment = environment
        self.client = self._construct_client()

    def _construct_client(self) -> Any:
        signature = inspect.signature(
            OandaV20Client
        )

        parameters = signature.parameters

        candidates = {
            "token": self.token,
            "api_token": self.token,
            "access_token": self.token,
            "account_id": self.account_id,
            "environment": self.environment,
            "timeout_seconds": 30,
            "maximum_retries": 3,
            "retry_delay_seconds": 2,
        }

        kwargs: dict[str, Any] = {}

        accepts_kwargs = any(
            parameter.kind
            == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

        for name, value in candidates.items():
            if (
                accepts_kwargs
                or name in parameters
            ):
                kwargs[name] = value

        return OandaV20Client(
            **kwargs
        )

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        require_account: bool = True,
    ) -> dict[str, Any]:
        request_method = getattr(
            self.client,
            "_request",
            None,
        )

        if not callable(
            request_method
        ):
            raise RuntimeError(
                "The installed OANDA client does not expose "
                "a compatible read-only request method."
            )

        arguments: dict[str, Any] = {}

        if params:
            arguments["params"] = params

        request_signature = inspect.signature(
            request_method
        )

        if (
            "require_account"
            in request_signature.parameters
        ):
            arguments[
                "require_account"
            ] = require_account

        payload = request_method(
            "GET",
            path,
            **arguments,
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise TypeError(
                f"Unexpected OANDA response for {path}."
            )

        return payload

    def account_summary(
        self,
    ) -> dict[str, Any]:
        native = getattr(
            self.client,
            "account_summary",
            None,
        )

        if callable(
            native
        ):
            payload = native()

            if isinstance(
                payload,
                dict,
            ):
                return payload

        return self._get(
            (
                f"/v3/accounts/"
                f"{self.account_id}/summary"
            )
        )

    def open_positions(
        self,
    ) -> dict[str, Any]:
        native = getattr(
            self.client,
            "open_positions",
            None,
        )

        if callable(
            native
        ):
            payload = native()

            if isinstance(
                payload,
                dict,
            ):
                return payload

        return self._get(
            (
                f"/v3/accounts/"
                f"{self.account_id}/openPositions"
            )
        )

    def open_trades(
        self,
    ) -> dict[str, Any]:
        native = getattr(
            self.client,
            "open_trades",
            None,
        )

        if callable(
            native
        ):
            payload = native()

            if isinstance(
                payload,
                dict,
            ):
                return payload

        return self._get(
            (
                f"/v3/accounts/"
                f"{self.account_id}/openTrades"
            )
        )

    def pending_orders(
        self,
    ) -> dict[str, Any]:
        native = getattr(
            self.client,
            "pending_orders",
            None,
        )

        if callable(
            native
        ):
            payload = native()

            if isinstance(
                payload,
                dict,
            ):
                return payload

        return self._get(
            (
                f"/v3/accounts/"
                f"{self.account_id}/pendingOrders"
            )
        )

    def pricing(
        self,
        instruments: list[str],
    ) -> dict[str, Any]:
        native = getattr(
            self.client,
            "pricing",
            None,
        )

        if callable(
            native
        ):
            payload = native(
                instruments
            )

            if isinstance(
                payload,
                dict,
            ):
                return payload

        return self._get(
            (
                f"/v3/accounts/"
                f"{self.account_id}/pricing"
            ),
            params={
                "instruments": ",".join(
                    instruments
                )
            },
        )
