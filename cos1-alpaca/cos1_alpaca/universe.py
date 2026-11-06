"""Liquid S&P 500 names, SPY, and sector ETFs. One sector label per symbol."""

from __future__ import annotations

# Sector ETF -> constituents. The ETF itself maps to the same sector, so a
# position in XLK blocks a second XLK-sector name (the firewall).
SECTOR_MEMBERS: dict[str, tuple[str, ...]] = {
    "XLK": ("AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "CSCO", "ADBE", "ACN", "INTU", "AMAT", "QCOM", "TXN", "IBM", "NOW"),
    "XLF": ("JPM", "BAC", "WFC", "GS", "MS", "AXP", "V", "MA", "BLK"),
    "XLV": ("LLY", "UNH", "JNJ", "MRK", "ABBV", "TMO", "AMGN"),
    "XLE": ("XOM", "CVX", "COP"),
    "XLY": ("AMZN", "HD", "MCD", "SBUX", "LOW", "NKE", "BKNG"),
    "XLP": ("PG", "KO", "PEP", "COST", "WMT", "PM"),
    "XLI": ("CAT", "GE", "HON", "UNP", "BA", "RTX"),
    "XLC": ("META", "GOOGL", "NFLX", "DIS", "CMCSA", "T", "VZ"),
    "XLU": ("NEE",),
    "XLRE": ("PLD",),
    "XLB": ("LIN",),
}

SECTOR_ETFS: tuple[str, ...] = tuple(SECTOR_MEMBERS)
INDEX: tuple[str, ...] = ("SPY",)


def sector_of(symbol: str) -> str:
    symbol = symbol.upper()
    if symbol == "SPY":
        return "INDEX"
    if symbol in SECTOR_MEMBERS:
        return symbol
    for etf, names in SECTOR_MEMBERS.items():
        if symbol in names:
            return etf
    return symbol


def default_universe() -> list[str]:
    names = {etf for etf in SECTOR_ETFS}
    names.update(INDEX)
    for members in SECTOR_MEMBERS.values():
        names.update(members)
    return sorted(names)
