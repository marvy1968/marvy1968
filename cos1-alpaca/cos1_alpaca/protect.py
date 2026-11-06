"""Broker stop replacement and the staircase trail.

The old stop stays live until the new stop is confirmed. If a position has no
working stop and a new one cannot be confirmed, the position is flattened.
Stops are broker orders, not virtual prices.
"""

from __future__ import annotations

import math

CONFIRMED = {"new", "accepted", "pending_new", "held", "open", "partially_filled", "accepted_for_bidding"}
STOP_TYPES = {"stop", "stop_limit"}


def iter_orders(orders):
    """Yield each order and every nested bracket/OCO leg."""
    for order in orders or []:
        yield order
        yield from iter_orders(getattr(order, "legs", None) or [])


def is_stop_order(order) -> bool:
    kind = str(getattr(order, "type", "") or "").lower().replace("-", "_").replace(" ", "_")
    return kind in STOP_TYPES


def desired_lock_r(favorable_r: float, breakeven_r: float = 1.0, step_r: float = 0.5) -> float | None:
    """First lock is break-even at +1R. After that the floor steps by ``step_r``."""
    if favorable_r < breakeven_r or step_r <= 0:
        return None
    steps = math.floor((favorable_r - breakeven_r) / step_r + 1e-9)
    return steps * step_r


def desired_stop(entry: float, risk: float, side: str, lock_r: float) -> float:
    if side == "long":
        return entry + lock_r * risk
    return entry - lock_r * risk


def is_tighter(new_stop: float, current_stop: float, side: str) -> bool:
    if side == "long":
        return new_stop > current_stop + 0.009
    return new_stop < current_stop - 0.009


def favorable_r(entry: float, extreme: float, risk: float, side: str) -> float:
    if risk <= 0:
        return 0.0
    if side == "long":
        return (extreme - entry) / risk
    return (entry - extreme) / risk


def status_confirmed(status: str | None) -> bool:
    return str(status or "").lower() in CONFIRMED


class ProtectError(Exception):
    pass


def replace_stop(broker, position: dict, new_stop: float, new_client_id: str, journal) -> str:
    """Submit the new stop first. Cancel the old one only after the new one rests.

    Returns replaced, kept_old, or flattened.
    """
    from .orders import round_px

    new_stop = round_px(new_stop)
    side = "sell" if position["side"] == "long" else "buy"
    old_id = position.get("stop_order_id") or ""
    try:
        order = broker.submit_stop(
            symbol=position["symbol"],
            qty=int(abs(position["qty"])),
            side=side,
            stop_price=new_stop,
            client_order_id=new_client_id,
            time_in_force="gtc" if position.get("mode") == "swing" else "day",
        )
    except Exception as exc:
        journal("stop_submit_failed", position["symbol"], str(type(exc).__name__))
        if old_id and broker.stop_is_working(old_id):
            return "kept_old"
        return _flatten(broker, position, journal, "stop_submit_failed")
    if not status_confirmed(getattr(order, "status", "")):
        journal("stop_unconfirmed", position["symbol"], getattr(order, "status", ""))
        if old_id and broker.stop_is_working(old_id):
            return "kept_old"
        return _flatten(broker, position, journal, "stop_unconfirmed")
    if old_id and old_id != order.id:
        try:
            broker.cancel_order(old_id)
        except Exception as exc:
            journal("old_stop_cancel_failed", position["symbol"], type(exc).__name__)
    position["stop_order_id"] = order.id
    position["stop"] = new_stop
    journal("stop_replaced", position["symbol"], new_client_id)
    return "replaced"


def ensure_protected(broker, position: dict, open_orders, journal, nonce: str) -> str:
    """A working cos1 stop must exist. Otherwise place one, or flatten."""
    from .orders import stop_client_order_id

    if _working(position, open_orders):
        return "ok"
    client_id = stop_client_order_id(position["symbol"], 0.0, nonce)
    try:
        return replace_stop(broker, position, float(position["stop"]), client_id, journal)
    except Exception as exc:
        journal("protect_failed", position["symbol"], type(exc).__name__)
        return _flatten(broker, position, journal, "protect_failed")


def _working(position: dict, open_orders) -> bool:
    """A working or HELD stop, including a nested bracket/OCO leg, already reserves the shares.

    Child legs often have no ``cos1-`` client id and type ``stop_limit``. Submitting
    another stop then fails with Alpaca 40310000 (insufficient qty).
    """
    wanted = str(position.get("stop_order_id") or "")
    for order in iter_orders(open_orders):
        if not status_confirmed(getattr(order, "status", "")):
            continue
        if wanted and str(getattr(order, "id", "")) == wanted:
            return True
        if getattr(order, "symbol", None) != position["symbol"]:
            continue
        if not is_stop_order(order):
            continue
        client_id = str(getattr(order, "client_order_id", "") or "")
        parent_id = str(getattr(order, "parent_client_order_id", "") or "")
        klass = str(getattr(order, "order_class", "") or "").lower()
        if (
            client_id.startswith("cos1-")
            or parent_id.startswith("cos1-")
            or klass in {"bracket", "oco"}
            or client_id == ""
        ):
            return True
    return False


def _flatten(broker, position: dict, journal, reason: str) -> str:
    try:
        broker.close_position(
            symbol=position["symbol"],
            qty=int(abs(position["qty"])),
            side="sell" if position["side"] == "long" else "buy",
            client_order_id=f"cos1-flat-{position['symbol']}-{reason}"[:48],
        )
    except Exception as exc:
        journal("flatten_failed", position["symbol"], f"{reason}:{type(exc).__name__}")
        return "unprotected"
    journal("flattened", position["symbol"], reason)
    position["flattened"] = True
    return "flattened"
