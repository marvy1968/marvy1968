"""Cross-process helper for the intent race test. Not a service."""

import time


def contend(path: str, decision_id: str, counter_path: str) -> None:
    from .callers import place_demo
    from .machine import OrderMachine

    machine = OrderMachine(path)

    def transport(order: dict) -> dict:
        with open(counter_path, "a", encoding="utf-8") as handle:
            handle.write(order["client_order_id"] + "\n")
        time.sleep(0.2)
        return {"outcome": "ack", "order_id": "venue-1", "fill_count": order["count"]}

    place_demo(
        machine, transport, caller="runner", account_label="demo-a", decision_id=decision_id,
        ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="yes", count=2, limit_cents=40)
