"""Compare the orders we intended with the broker's open orders and fills."""

from __future__ import annotations

from .protect import is_stop_order, iter_orders, status_confirmed


def reconcile(intended: list[dict], open_orders, positions, config_hash: str) -> list[dict]:
    flags: list[dict] = []
    by_client: dict[str, list] = {}
    open_ids = set()
    for order in iter_orders(open_orders):
        open_ids.add(order.id)
        key = order.client_order_id or ""
        if key:
            by_client.setdefault(key, []).append(order)
    for key, rows in by_client.items():
        if len(rows) > 1:
            flags.append(
                {
                    "kind": "duplicate",
                    "client_order_id": key,
                    "symbol": rows[0].symbol,
                    "detail": f"{len(rows)} open orders share {key}",
                    "config_hash": config_hash,
                }
            )
    stops: dict[str, int] = {}
    for order in iter_orders(open_orders):
        if is_stop_order(order) and status_confirmed(order.status):
            if str(order.client_order_id or "").startswith("cos1-") or str(getattr(order, "parent_client_order_id", "") or "").startswith("cos1-"):
                stops[order.symbol] = stops.get(order.symbol, 0) + 1
    for symbol, count in stops.items():
        if count > 1:
            flags.append(
                {
                    "kind": "duplicate",
                    "client_order_id": "",
                    "symbol": symbol,
                    "detail": f"{count} working cos1 stops",
                    "config_hash": config_hash,
                }
            )
    held = {p.symbol for p in positions if abs(p.qty) > 0}
    for item in intended:
        if item.get("role") != "stop":
            continue
        symbol = item.get("symbol", "")
        if symbol not in held:
            continue
        client_id = item.get("client_order_id") or ""
        order_id = item.get("order_id") or ""
        present = (client_id and client_id in by_client) or (order_id and order_id in open_ids)
        if not present:
            flags.append(
                {
                    "kind": "missing",
                    "client_order_id": client_id,
                    "symbol": symbol,
                    "detail": "intended stop is not open at the broker",
                    "config_hash": config_hash,
                }
            )
    return flags
