"""Request JSON transcribed from kalshi_demo.py place_order / _place_ioc.

Source: kalshi_demo.py lines 96-100 and 145-156, export SHA256
8088d86794d197bee4ac7da7f8434d407631093c1bbfd27f14b5150a6e031f27.

The client_order_id argument is supplied by the ledger. This module does not
generate an id, read the environment, move funds, or open a socket.
"""

from __future__ import annotations


def book_price_dollars(side: str, limit_cents: int) -> str:
    """Same conversion as _place_ioc: YES bids at the limit, NO asks at 100 minus the limit."""
    price_cents = int(limit_cents)
    book_px = price_cents if side != "no" else 100 - price_cents
    return f"{book_px / 100.0:.4f}"


def order_body(ticker: str, side: str, count: int, limit_cents: int, client_order_id: str, *,
               time_in_force: str, expiration_ts: int | None = None) -> dict:
    body = {
        "ticker": ticker,
        "client_order_id": client_order_id,
        "side": "ask" if side == "no" else "bid",
        "count": f"{int(count)}.00",
        "price": book_price_dollars(side, limit_cents),
        "time_in_force": time_in_force,
        "self_trade_prevention_type": "taker_at_cross",
    }
    if expiration_ts is not None:
        body["expiration_ts"] = expiration_ts
    return body


def funding_need_cents(limit_cents: int, count: int) -> int:
    """Price times count plus the 200-cent buffer from _place_ioc. No transfer is sent."""
    return int(limit_cents) * int(count) + 200
