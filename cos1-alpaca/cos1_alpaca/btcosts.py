"""Cost-model interface compatible with a btcosts.py harness.

Field names match ``alpacabot.engine.Costs`` and ``Portfolio``:
slip_bps (adverse, per side), fee_bps_sell, commission (per share),
max_pos_frac (live notional cap), plus an explicit feed label.

There is no btcosts.py / brain_replay_bt.py in this checkout. If the VM has
Claude's harness, set COS1_BTCOSTS to that file. It must define Costs and
Portfolio. Unknown extra fields are ignored by the backtest; missing
spread_bps is treated as 0 so the harness numbers stay intact.

These defaults are the harness defaults (2 bps slip, 0.4 bps sell-side
regulatory fee, commission 0, 25% position cap). They are not retuned.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass

from .errors import FeedRefused

FEED = "iex"


@dataclass
class Costs:
    slip_bps: float = 2.0
    fee_bps_sell: float = 0.4
    commission: float = 0.0
    entry: str = "market"
    limit_offset: float = 0.0
    limit_wait: int = 3
    spread_bps: float = 0.0
    feed: str = FEED

    def pnl_per_share(self, side: int, entry_px: float, exit_px: float) -> float:
        return pnl_per_share(side, entry_px, exit_px, self)


@dataclass
class Portfolio:
    equity: float = 100_000.0
    risk_pct: float = 0.005
    max_positions: int = 3
    max_pos_frac: float = 0.25
    daily_loss_limit: float = 0.02
    pdt: bool = False
    min_qty: int = 1


def pnl_per_share(side: int, entry_px: float, exit_px: float, costs: Costs) -> float:
    """Per-share P&L after the sell-side fee, both commissions, and spread.

    Slippage belongs in the fill prices (same convention as alpacabot.engine.Trade).
    ``side`` is +1 long, -1 short.
    """
    sell_leg = exit_px if side == 1 else entry_px
    fee = sell_leg * costs.fee_bps_sell / 1e4 + 2.0 * costs.commission
    spread = ((entry_px + exit_px) / 2.0) * getattr(costs, "spread_bps", 0.0) / 1e4
    return side * (exit_px - entry_px) - fee - spread


def slip_multiplier(costs: Costs) -> float:
    return costs.slip_bps / 1e4


def apply_entry(raw_open: float, side: int, costs: Costs) -> float:
    """Adverse entry fill. Longs pay up, shorts sell down."""
    slip = slip_multiplier(costs)
    return raw_open * (1.0 + slip) if side == 1 else raw_open * (1.0 - slip)


def apply_stop_exit(raw_px: float, side: int, costs: Costs) -> float:
    slip = slip_multiplier(costs)
    return raw_px * (1.0 - slip) if side == 1 else raw_px * (1.0 + slip)


def feed_label(costs: Costs | None = None) -> str:
    feed = getattr(costs, "feed", FEED) if costs is not None else FEED
    if str(feed).lower() != "iex":
        raise FeedRefused(str(feed))
    return "iex"


def load_external():
    """Return an external harness module, or None to use this file."""
    path = os.environ.get("COS1_BTCOSTS")
    if not path:
        return None
    spec = importlib.util.spec_from_file_location("cos1_btcosts_ext", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "Costs") or not hasattr(module, "Portfolio"):
        raise ImportError("COS1_BTCOSTS must define Costs and Portfolio")
    return module
