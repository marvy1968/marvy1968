"""Liquidity, VWAP extension, and the earnings-date guard.

Earnings dates are read from a CSV (symbol,YYYY-MM-DD) when one is configured
and present. Without that file the guard is a stub: it does not block, and it
reports ``stub`` so the gap is visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import LiquidityCfg
from .indicators import overextended, rvol
from .types import Bar, Quote


@dataclass
class EarningsGuard:
    stub: bool
    dates: dict[str, set[date]] = field(default_factory=dict)

    def status(self, symbol: str, day: date, sessions: list[date]) -> tuple[bool, str]:
        """Return (blocked, reason). Block the session before, of, and after earnings."""
        if self.stub:
            return False, "stub"
        known = self.dates.get(symbol.upper(), set())
        if not known:
            return False, "ok"
        window = {day}
        ordered = sorted(set(sessions))
        if day in ordered:
            idx = ordered.index(day)
            if idx > 0:
                window.add(ordered[idx - 1])
            if idx + 1 < len(ordered):
                window.add(ordered[idx + 1])
        if known & window:
            return True, "earnings"
        return False, "ok"


def load_earnings(path: str | None) -> EarningsGuard:
    if not path:
        return EarningsGuard(stub=True)
    file_path = Path(path)
    if not file_path.is_file():
        return EarningsGuard(stub=True)
    dates: dict[str, set[date]] = {}
    for line in file_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "," not in stripped:
            continue
        symbol, raw_day = stripped.split(",", 1)
        dates.setdefault(symbol.strip().upper(), set()).add(date.fromisoformat(raw_day.strip()[:10]))
    return EarningsGuard(stub=False, dates=dates)


def quote_spread_bps(quote: Quote | None) -> float | None:
    if quote is None or quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
        return None
    mid = (quote.ask + quote.bid) / 2.0
    if mid <= 0:
        return None
    return (quote.ask - quote.bid) / mid * 1e4


def liquidity_ok(
    *,
    price: float,
    quote: Quote | None,
    bars: list[Bar],
    cfg: LiquidityCfg,
    dollar_volume: float | None = None,
) -> tuple[bool, str]:
    """Fail closed per name: a missing quote or an unknown RVOL skips the entry.

    ``dollar_volume`` is the average daily dollar volume. Intraday bar volume is
    not a day's liquidity.
    """
    if price < cfg.min_price:
        return False, "thin_price"
    spread = quote_spread_bps(quote)
    if spread is None:
        return False, "no_quote"
    if spread > cfg.max_spread_bps:
        return False, "spread"
    ratio = rvol(bars)
    if ratio is None:
        return False, "rvol_unknown"
    if ratio < cfg.min_rvol:
        return False, "rvol"
    if dollar_volume is None:
        if not bars:
            return False, "thin_dollar"
        dollar_volume = price * sum(b.volume for b in bars[-20:]) / min(20, len(bars))
    if dollar_volume < cfg.min_dollar_volume:
        return False, "thin_dollar"
    return True, "ok"


def vwap_chase(close: float, vwap: float | None, std: float | None, max_std: float) -> bool:
    return overextended(close, vwap, std, max_std)
