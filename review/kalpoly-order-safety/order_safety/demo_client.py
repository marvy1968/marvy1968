"""Review-only Kalshi DEMO order client.

The injected callable is the only HTTP boundary. Tests pass a fake. This
class does not import the live order client, does not move shard balances,
and does not mint a client id at POST time.

A missing fill_count stays unknown. A cancel response stays cancel_unknown.
A client id this ledger did not create is ambiguous and is not adopted.
"""

from __future__ import annotations

from .demo_body import funding_need_cents, order_body
from .host import assert_demo_base
from .machine import OrderMachine, SubmitBlocked

_ORDER_PATH = "/portfolio/events/orders"
_CALLER = "demo_protocol"


class FundingUnsupported(RuntimeError):
    """Automatic shard transfer is a bankroll side effect and is not authorized."""


class AmbiguousLegacyOrder(RuntimeError):
    """A client id this ledger did not create cannot be attached by similarity."""


class DemoClient:
    def __init__(self, machine: OrderMachine, http, *, account_label: str, base_url: str):
        self.host = assert_demo_base(base_url)
        self.base_url = base_url
        self.machine = machine
        self.http = http
        self.account_label = account_label

    def place_order(self, ticker: str, side: str, count: int, limit_cents: int, *,
                    decision_id: str, shard_fact, rest_enabled: bool, now_s: int | None = None,
                    rest_seconds: int = 300) -> dict:
        self._refuse_funding(shard_fact, limit_cents, count)
        if not isinstance(rest_enabled, bool):
            raise ValueError("rest_enabled")
        expiration = None
        if rest_enabled:
            if isinstance(now_s, bool) or not isinstance(now_s, int):
                raise ValueError("now_s")
            if isinstance(rest_seconds, bool) or not isinstance(rest_seconds, int) or not 1 <= rest_seconds <= 86400:
                raise ValueError("rest_seconds")
            expiration = now_s + rest_seconds

        def post_ioc(intent: dict) -> dict:
            body = order_body(
                intent["ticker"], intent["side"], intent["count"], intent["limit_cents"],
                intent["client_order_id"], time_in_force="immediate_or_cancel")
            return _interpret(self.http(_request("POST", _ORDER_PATH, body, self.base_url)), intent)

        parent = self.machine.submit(
            post_ioc, venue="kalshi_demo", mode="demo", account_label=self.account_label,
            decision_id=decision_id, ticker=ticker, side=side, count=count, limit_cents=limit_cents,
            caller=_CALLER, base_url=self.base_url)
        child = None
        if rest_enabled and parent["state"] == "partial":
            def post_rest(intent: dict) -> dict:
                body = order_body(
                    intent["ticker"], intent["side"], intent["count"], intent["limit_cents"],
                    intent["client_order_id"], time_in_force="good_till_canceled",
                    expiration_ts=expiration)
                return _interpret(self.http(_request("POST", _ORDER_PATH, body, self.base_url)), intent)

            child = self.machine.open_resting_child(
                post_rest, parent_intent_id=parent["intent_id"], account_label=self.account_label,
                base_url=self.base_url, caller=_CALLER, expiration_ts=expiration)
        return {"parent": parent, "child": child}

    def cancel(self, intent_id: str) -> dict:
        def delete(payload: dict) -> dict:
            order_id = payload.get("venue_order_id")
            if not isinstance(order_id, str) or not order_id:
                raise SubmitBlocked("cancel has no venue order id")
            self.http(_request("DELETE", _ORDER_PATH + "/" + order_id, None, self.base_url))
            return {"received": True}

        return self.machine.request_cancel(
            delete, intent_id=intent_id, account_label=self.account_label, venue="kalshi_demo",
            mode="demo", base_url=self.base_url)

    def sweep_resting(self, now_s: int, legacy_rows=None) -> dict:
        """Cancel expired resting children. A cancel response is not cancellation.

        legacy_rows is the shape of resting_orders.jsonl. Any order id that is
        not already this ledger's venue id blocks the sweep and sends nothing.
        """
        if isinstance(now_s, bool) or not isinstance(now_s, int):
            raise ValueError("now_s")
        rows = self.machine.list_intents(
            venue="kalshi_demo", mode="demo", account_label=self.account_label)
        known_ids = {row["venue_order_id"] for row in rows if row.get("venue_order_id")}
        if legacy_rows is not None:
            if not isinstance(legacy_rows, list):
                raise ValueError("legacy_rows")
            for item in legacy_rows:
                if not isinstance(item, dict) or item.get("order_id") not in known_ids:
                    self.machine.block_for(
                        venue="kalshi_demo", mode="demo", account_label=self.account_label,
                        base_url=self.base_url, reason="legacy order id is ambiguous")
                    raise AmbiguousLegacyOrder("legacy order id is ambiguous")
        sent = []
        for row in rows:
            if row["role"] != "resting_child" or row["expiration_ts"] is None:
                continue
            if row["expiration_ts"] > now_s:
                continue
            if row["state"] in {"cancelled", "cancel_requested", "cancel_unknown", "rejected", "filled"}:
                continue
            result = self.cancel(row["intent_id"])
            sent.append(result["intent_id"])
        return {"sent": sent}

    def reconcile(self, snapshot: dict) -> dict:
        return self.machine.reconcile(
            snapshot, venue="kalshi_demo", mode="demo", account_label=self.account_label,
            base_url=self.base_url)

    def _refuse_funding(self, shard_fact, limit_cents: int, count: int) -> None:
        need = funding_need_cents(limit_cents, count)
        if not isinstance(shard_fact, dict):
            raise FundingUnsupported("shard balance is unknown; automatic funding is not authorized")
        index = shard_fact.get("exchange_index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise FundingUnsupported("shard index is unknown; automatic funding is not authorized")
        if index == 0:
            return
        balances = shard_fact.get("balances_cents")
        if not isinstance(balances, dict) or index not in balances or 0 not in balances:
            raise FundingUnsupported("shard balance is unknown; automatic funding is not authorized")
        have = balances[index]
        if isinstance(have, bool) or not isinstance(have, int):
            raise FundingUnsupported("shard balance is unknown; automatic funding is not authorized")
        if need - have > 0:
            raise FundingUnsupported("automatic shard transfer is not authorized")


def _request(method: str, path: str, body, base_url: str) -> dict:
    return {"method": method, "path": path, "json": body, "base_url": base_url}


def _interpret(response, intent: dict) -> dict:
    """Map a venue body onto an explicit outcome. Missing data stays unknown."""
    if not isinstance(response, dict):
        return {"outcome": "malformed"}
    echoed = response.get("client_order_id")
    if echoed is not None and echoed != intent["client_order_id"]:
        return {"outcome": "wrong_client"}
    if "ticker" in response and response.get("ticker") != intent["ticker"]:
        return {"outcome": "wrong_order"}
    wire_side = "ask" if intent["side"] == "no" else "bid"
    if "side" in response and response.get("side") != wire_side:
        return {"outcome": "wrong_order"}
    order_id = response.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return {"outcome": "malformed"}
    fill = response.get("fill_count")
    if isinstance(fill, bool) or not isinstance(fill, int):
        return {"outcome": "malformed"}
    return {"outcome": "ack", "order_id": order_id, "fill_count": fill}
