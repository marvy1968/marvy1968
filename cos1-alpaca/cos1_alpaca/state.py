"""Persisted bot state: daily and weekly anchors, owned positions, fill inventory."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .clock import ET, as_et
from .types import Fill


@dataclass
class State:
    day_key: str = ""
    day_start_equity: float = 0.0
    week_key: str = ""
    week_start_equity: float = 0.0
    equity_peak: float = 0.0
    exposure_mult: float = 1.0
    drawdown: float = 0.0
    trade_count: int = 0
    seen_fill_ids: list = field(default_factory=list)
    positions: dict = field(default_factory=dict)
    consumed_signals: dict = field(default_factory=dict)
    logged_signals: dict = field(default_factory=dict)
    mode_pnl: dict = field(default_factory=lambda: {"day": 0.0, "swing": 0.0, "scalp": 0.0})
    mode_peak: dict = field(default_factory=lambda: {"day": 0.0, "swing": 0.0, "scalp": 0.0})
    killed_modes: list = field(default_factory=list)
    lots: dict = field(default_factory=dict)
    closed: list = field(default_factory=list)
    intended: list = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    order_meta: dict = field(default_factory=dict)
    last_feed_health_ts: str = ""
    last_config_hash: str = ""


def load_state(path: Path) -> State:
    if not path.is_file():
        return State()
    raw = json.loads(path.read_text())
    state = State()
    for key, value in raw.items():
        if hasattr(state, key):
            setattr(state, key, value)
    for mode in ("day", "swing", "scalp"):
        state.mode_pnl.setdefault(mode, 0.0)
        state.mode_peak.setdefault(mode, 0.0)
    return state


def save_state(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2, default=str))
    os.replace(tmp, path)


def roll_clock(state: State, now: datetime, equity: float) -> None:
    local = as_et(now)
    day_key = local.date().isoformat()
    week_key = local.strftime("%G-W%V")
    if state.day_key != day_key:
        state.day_key = day_key
        state.day_start_equity = equity
    if state.week_key != week_key:
        state.week_key = week_key
        state.week_start_equity = equity
    if state.day_start_equity <= 0:
        state.day_start_equity = equity
    if state.week_start_equity <= 0:
        state.week_start_equity = equity
    if state.equity_peak <= 0:
        state.equity_peak = equity


def mode_from_client_id(client_order_id: str) -> str:
    parts = (client_order_id or "").split("-")
    if len(parts) >= 2 and parts[0] == "cos1":
        return {"d": "day", "w": "swing", "s": "scalp"}.get(parts[1], "")
    return ""


def apply_fills(state: State, fills: list[Fill]) -> list[dict]:
    """FIFO realized P&L from new fills. Inventory is persisted so restarts do not double count."""
    fresh = []
    seen = set(state.seen_fill_ids)
    for fill in sorted(fills, key=lambda item: (item.ts, item.id)):
        if fill.id in seen:
            continue
        seen.add(fill.id)
        state.seen_fill_ids.append(fill.id)
        meta = {}
        if isinstance(state.order_meta, dict):
            meta = state.order_meta.get(fill.client_order_id) or state.order_meta.get(fill.order_id) or {}
        mode = fill.mode or meta.get("mode") or mode_from_client_id(fill.client_order_id) or "day"
        expert = meta.get("expert") or mode
        fresh.extend(_apply_one(state, fill, mode, expert, float(meta.get("risk") or 0.0)))
    for closed in fresh:
        mode = closed["mode"]
        state.mode_pnl[mode] = float(state.mode_pnl.get(mode, 0.0)) + closed["pnl"]
        state.mode_peak[mode] = max(float(state.mode_peak.get(mode, 0.0)), float(state.mode_pnl[mode]))
    state.closed.extend(fresh)
    if len(state.seen_fill_ids) > 5000:
        state.seen_fill_ids = state.seen_fill_ids[-5000:]
    return fresh


def _apply_one(state: State, fill: Fill, mode: str, expert: str, risk_per_share: float) -> list[dict]:
    lots = state.lots.setdefault(fill.symbol, [])
    sign = 1.0 if fill.side == "buy" else -1.0
    qty = float(fill.qty)
    closed = []
    while qty > 1e-9 and lots and (lots[0][0] > 0) != (sign > 0):
        lot_qty, lot_px, lot_mode, lot_expert, lot_risk = _lot(lots[0])
        close_qty = min(qty, abs(lot_qty))
        if lot_qty > 0:
            pnl = (fill.price - lot_px) * close_qty
        else:
            pnl = (lot_px - fill.price) * close_qty
        fee = float(fill.fee or 0.0) * (close_qty / fill.qty) if fill.qty else 0.0
        pnl -= fee
        risk_dollars = lot_risk * close_qty
        closed.append(
            {
                "ts": as_et(fill.ts).isoformat(),
                "mode": lot_mode or mode,
                "expert": lot_expert or expert,
                "pnl": pnl,
                "symbol": fill.symbol,
                "qty": close_qty,
                "risk_dollars": risk_dollars,
                "r": (pnl / risk_dollars) if risk_dollars else None,
            }
        )
        qty -= close_qty
        remain = abs(lot_qty) - close_qty
        if remain <= 1e-9:
            lots.pop(0)
        else:
            lots[0] = [math_copysign(remain, lot_qty), lot_px, lot_mode, lot_expert, lot_risk]
    if qty > 1e-9:
        if not lots:
            state.trade_count += 1
        lots.append([sign * qty, fill.price, mode, expert, risk_per_share])
    return closed


def _lot(row: list):
    qty, price = row[0], row[1]
    mode = row[2] if len(row) > 2 else "day"
    expert = row[3] if len(row) > 3 else mode
    risk = row[4] if len(row) > 4 else 0.0
    return qty, price, mode, expert, float(risk or 0.0)


def math_copysign(mag: float, sign_src: float) -> float:
    return mag if sign_src > 0 else -mag


def window_pnl(state: State, prefix: str) -> float:
    return sum(float(row["pnl"]) for row in state.closed if str(row.get("ts", "")).startswith(prefix))


def day_pnl(state: State) -> float:
    return window_pnl(state, state.day_key)


def week_pnl(state: State) -> float:
    """ISO week prefix is not in the timestamp. Match by the stored week via the date."""
    if not state.week_key or "-W" not in state.week_key:
        return 0.0
    year_s, week_s = state.week_key.split("-W")
    total = 0.0
    for row in state.closed:
        ts = str(row.get("ts", ""))[:19]
        try:
            stamp = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ET)
        iso = stamp.isocalendar()
        if iso.year == int(year_s) and iso.week == int(week_s):
            total += float(row["pnl"])
    return total


def win_rate(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    wins = sum(1 for row in rows if float(row["pnl"]) > 0)
    return wins / len(rows)


def sync_owned(state: State, broker_positions) -> None:
    """Broker quantity wins for symbols we own. Unknown broker positions are not adopted."""
    live = {p.symbol: p for p in broker_positions}
    for symbol in list(state.positions):
        broker = live.get(symbol)
        if broker is None or abs(broker.qty) < 1e-9:
            state.positions.pop(symbol, None)
            continue
        state.positions[symbol]["qty"] = abs(float(broker.qty))
        state.positions[symbol]["side"] = broker.side
