"""Three liquid large caps from each of the 11 GICS sectors, plus SPY as the filter symbol.

The tradable list is 33 names. SPY is loaded for the regime and the top-down
gate and is not part of the 33.
"""

from __future__ import annotations

# Market-cap ranks drift. This is the liquid set frozen for the bot (Nov 2026).
GICS: dict[str, tuple[str, ...]] = {
    "tech": ("AAPL", "NVDA", "MSFT"),
    "comm": ("GOOGL", "META", "NFLX"),
    "disc": ("AMZN", "TSLA", "HD"),
    "health": ("LLY", "JNJ", "ABBV"),
    "fin": ("BRK.B", "JPM", "V"),
    "staples": ("WMT", "COST", "KO"),
    "energy": ("XOM", "CVX", "COP"),
    "ind": ("GE", "CAT", "RTX"),
    "mat": ("LIN", "NEM", "FCX"),
    "util": ("NEE", "SO", "DUK"),
    "reit": ("WELL", "PLD", "EQIX"),
}

INDEX: tuple[str, ...] = ("SPY",)


def sector_of(symbol: str) -> str:
    symbol = symbol.upper()
    if symbol == "SPY":
        return "INDEX"
    for sector, names in GICS.items():
        if symbol in names:
            return sector
    return symbol


def default_universe() -> list[str]:
    names = [name for members in GICS.values() for name in members]
    if len(names) != 33 or len(GICS) != 11:
        raise RuntimeError("universe must be 3 names from each of 11 sectors")
    return list(names)
