"""Bracket plans. One full-size order. Take-profit is never the entry price.

Day targets stay inside 2.5R-3R. A missing or unusable target falls back to the
configured R multiple of the stop distance. There is no partial scale-out.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_HALF_UP

from .errors import OrderRejected
from .types import BracketPlan, Signal

_TICK = Decimal("0.01")


def round_px(price: float) -> float:
    return float(Decimal(str(price)).quantize(_TICK, rounding=ROUND_HALF_UP))


def make_client_order_id(mode: str, symbol: str, side: str, expert: str, signal_id: str) -> str:
    mode_c = {"day": "d", "swing": "w", "scalp": "s"}.get(mode, "x")
    side_c = "b" if side in ("long", "buy") else "s"
    digest = hashlib.sha1(signal_id.encode()).hexdigest()[:8]
    expert_c = "".join(ch for ch in expert if ch.isalnum())[:6]
    symbol_c = "".join(ch for ch in symbol if ch.isalnum())[:6]
    client_id = f"cos1-{mode_c}-{symbol_c}-{side_c}-{expert_c}-{digest}"
    if not client_id.startswith("cos1-") or len(client_id) > 48:
        raise OrderRejected("client_order_id")
    return client_id


def stop_client_order_id(symbol: str, lock_r: float, nonce: str) -> str:
    symbol_c = "".join(ch for ch in symbol if ch.isalnum())[:6]
    tag = f"{int(round(lock_r * 10)):02d}"
    nonce_c = "".join(ch for ch in nonce if ch.isalnum())[:10]
    client_id = f"cos1-stp-{symbol_c}-{tag}-{nonce_c}"
    if not client_id.startswith("cos1-") or len(client_id) > 48:
        raise OrderRejected("stop client_order_id")
    return client_id


def flatten_client_order_id(symbol: str, nonce: str) -> str:
    symbol_c = "".join(ch for ch in symbol if ch.isalnum())[:6]
    nonce_c = "".join(ch for ch in nonce if ch.isalnum())[:12]
    return f"cos1-flat-{symbol_c}-{nonce_c}"[:48]


def _fallback_target(entry: float, stop: float, side: str, target_r: float) -> float:
    risk = abs(entry - stop)
    if side == "long":
        return entry + target_r * risk
    return entry - target_r * risk


def build_bracket(sig: Signal, qty: int) -> BracketPlan:
    """Full quantity. Reject a day target inside 2.5R and any take-profit on the entry."""
    if qty < 1:
        raise OrderRejected("qty")
    if sig.log_only:
        raise OrderRejected("log_only")
    entry = round_px(sig.entry)
    stop = round_px(sig.stop)
    risk = abs(entry - stop)
    if risk < 0.01:
        raise OrderRejected("risk")
    if sig.side == "long" and stop >= entry:
        raise OrderRejected("stop side")
    if sig.side == "short" and stop <= entry:
        raise OrderRejected("stop side")
    if sig.mode == "day" and not 2.5 <= float(sig.target_r) <= 3.0:
        raise OrderRejected("day target_r")
    if sig.mode == "swing" and float(sig.target_r) < 2.0:
        raise OrderRejected("swing target_r")
    raw = sig.target if sig.target is not None else _fallback_target(entry, stop, sig.side, sig.target_r)
    take = round_px(raw)
    invalid = take == entry or (sig.side == "long" and take < entry) or (sig.side == "short" and take > entry)
    if invalid:
        take = round_px(_fallback_target(entry, stop, sig.side, sig.target_r))
    if take == entry or (sig.side == "long" and take <= entry) or (sig.side == "short" and take >= entry):
        raise OrderRejected("tp at entry")
    reward = abs(take - entry)
    if sig.mode == "day" and reward + 1e-9 < 2.5 * risk:
        take = round_px(_fallback_target(entry, stop, sig.side, sig.target_r))
        reward = abs(take - entry)
        if reward + 1e-9 < 2.5 * risk:
            raise OrderRejected("day tp inside 2.5R")
    if sig.mode == "swing" and sig.target is None and reward + 1e-9 < 2.0 * risk:
        raise OrderRejected("swing tp inside 2R")
    side = "buy" if sig.side == "long" else "sell"
    tif = "day" if sig.mode in ("day", "scalp") else "gtc"
    return BracketPlan(
        symbol=sig.symbol,
        qty=qty,
        side=side,
        stop=stop,
        take_profit=take,
        client_order_id=make_client_order_id(sig.mode, sig.symbol, sig.side, sig.expert, sig.signal_id),
        time_in_force=tif,
        mode=sig.mode,
        expert=sig.expert,
        signal_id=sig.signal_id,
        entry_hint=entry,
        log_only=False,
    )


def duplicate_client_id(client_order_id: str, open_ids: set[str]) -> bool:
    return client_order_id in open_ids
