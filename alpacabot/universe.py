"""The trading universe: the 5 largest S&P 500 members (by market cap) in each of the 11 GICS sectors, with that sector's SPDR ETF.

Picked by market cap as of AS_OF; refresh it each quarter (index changes, cap shifts). Used by the intraday paper bot (paper.SYMBOLS,
plus SPY/QQQ/IWM for the market gate) and the daily Sector Top-Down swing runner. Note for backtests: a list picked with today's
caps is a winners' list for past years (survivorship), so judge historical results on it against the placebo and the control universe.
"""

AS_OF = "2026-10-14"

SECTOR_TOP5 = {
    "XLK": ["NVDA", "MSFT", "AAPL", "AVGO", "ORCL"],        # information technology
    "XLC": ["GOOGL", "META", "NFLX", "TMUS", "DIS"],        # communication services
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "BKNG"],           # consumer discretionary
    "XLF": ["BRK.B", "JPM", "V", "MA", "BAC"],              # financials
    "XLV": ["LLY", "JNJ", "ABBV", "UNH", "MRK"],            # health care
    "XLI": ["GE", "CAT", "RTX", "GEV", "UBER"],             # industrials
    "XLP": ["WMT", "COST", "PG", "KO", "PM"],               # consumer staples
    "XLE": ["XOM", "CVX", "COP", "WMB", "EOG"],             # energy
    "XLU": ["NEE", "SO", "CEG", "DUK", "VST"],              # utilities
    "XLRE": ["PLD", "AMT", "WELL", "EQIX", "SPG"],          # real estate
    "XLB": ["LIN", "NEM", "SHW", "ECL", "APD"],             # materials
}

SECTOR_OF = {s: etf for etf, syms in SECTOR_TOP5.items() for s in syms}
STOCKS = [s for syms in SECTOR_TOP5.values() for s in syms]
SECTOR_ETFS = list(SECTOR_TOP5)

# The hindsight-free control list (2016's largest US companies, cli.SECTOR_TD_CONTROL) with each stock's sector ETF, for breadth tests.
CONTROL_SECTOR_OF = {"XOM": "XLE", "CVX": "XLE", "JNJ": "XLV", "PFE": "XLV", "MRK": "XLV", "GE": "XLI", "JPM": "XLF", "WFC": "XLF",
                     "BAC": "XLF", "C": "XLF", "T": "XLC", "VZ": "XLC", "DIS": "XLC", "PG": "XLP", "KO": "XLP", "WMT": "XLP",
                     "INTC": "XLK", "CSCO": "XLK", "ORCL": "XLK", "IBM": "XLK", "HD": "XLY"}

# Owner 2026-10-22 ("1 month test on small, mid and large cap stocks"): liquid mid caps and small caps with the sector ETF each one rates in the
# heat map. Picked for volume and tight spreads, size class approximate (market caps move); a stock the data feed cannot load is skipped.
MID_CAPS = {"AFRM": "XLF", "SOFI": "XLF", "RKLB": "XLI", "HIMS": "XLV", "DKNG": "XLY", "CHWY": "XLY", "ROKU": "XLC", "ELF": "XLP",
            "CELH": "XLP", "PATH": "XLK", "U": "XLK", "TOST": "XLK"}
SMALL_CAPS = {"MARA": "XLK", "RIOT": "XLK", "IONQ": "XLK", "SOUN": "XLK", "BBAI": "XLK", "PLUG": "XLI", "JOBY": "XLI", "ACHR": "XLI",
              "LCID": "XLY", "RIVN": "XLY", "OPEN": "XLRE", "RUN": "XLU"}
