"""Central configuration for the 5-expert S&P 500 sector scalper (Alpaca)."""
import os

# ---------------- Alpaca credentials ----------------
API_KEY = os.getenv("APCA_API_KEY_ID", "")
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "")
PAPER = os.getenv("APCA_PAPER", "true").lower() != "false"      # paper by default

# "iex" = free real-time (IEX exchange only, a few % of volume)
# "sip" = full consolidated tape (real-time needs a paid data plan)
LIVE_FEED = os.getenv("APCA_FEED", "iex")
HIST_FEED = os.getenv("APCA_HIST_FEED", "sip")   # SIP history older than ~15 min is available on free plans

# ---------------- Sizing / risk ----------------
POSITION_PCT = 0.005             # each position = 0.5% of total equity (notional)
MAX_POSITIONS_PER_EXPERT = 3     # each expert opens/controls/closes up to 3 symbols
STOCKS_PER_SECTOR = 5
DAILY_LOSS_LIMIT_PCT = 0.02      # no new entries once equity is -2% vs. yesterday's close
MIN_EQUITY_FOR_SHORTS = 2000.0   # Alpaca margin/short requirement
CHECK_SPREAD = LIVE_FEED == "sip"   # IEX quotes aren't NBBO; only filter spreads on SIP
MAX_SPREAD_PCT = 0.0008          # 8 bps
MAX_STOP_ATR = 2.5               # cap stop distance (1-min ATRs)
MIN_STOP_ATR = 0.6               # floor stop distance (avoid noise stop-outs)
MIN_STOP_PCT = 0.001             # floor stop distance as % of price

# ---------------- Session timing (minutes after 09:30 ET) ----------------
FIRST_ENTRY_MOD = 5
LAST_ENTRY_MOD = 360             # 15:30 ET, no new trades after
FLATTEN_MOD = 385                # 15:55 ET, everything flat
BREAKEVEN_TRIGGER_R = 1.0        # at +1R move stop to entry (+0.05R)
STALE_BAR_MIN = 3                # skip a symbol if its last bar is older than this

# ---------------- Sector regime heat map ----------------
ADX_TREND_MIN = 20
REGIME_REFRESH_MIN = 15
INTRADAY_OVERLAY = True          # downgrade daily BULL/BEAR to SIDEWAYS if the sector ETF fights it intraday
OVERLAY_MOVE_PCT = 0.0075

STATE_FILE = "state.json"
LIVE_TRADES_CSV = "trades_live.csv"
HEATMAP_HTML = "heatmap.html"

# 11 GICS sectors -> SPDR ETF + large-cap candidates.
# The bot picks the top 5 per sector by 20-day average dollar volume at the start of each day.
# Verify constituents periodically (index membership changes).
SECTORS = {
    "Technology":        {"etf": "XLK",  "candidates": ["NVDA", "AAPL", "MSFT", "AVGO", "ORCL", "AMD", "PLTR", "CSCO", "CRM", "MU"]},
    "Communication":     {"etf": "XLC",  "candidates": ["META", "GOOGL", "NFLX", "TMUS", "DIS", "T", "VZ", "CMCSA"]},
    "Consumer Disc.":    {"etf": "XLY",  "candidates": ["AMZN", "TSLA", "HD", "MCD", "BKNG", "LOW", "TJX", "SBUX", "NKE"]},
    "Consumer Staples":  {"etf": "XLP",  "candidates": ["WMT", "COST", "PG", "KO", "PEP", "PM", "MDLZ", "MO", "CL"]},
    "Health Care":       {"etf": "XLV",  "candidates": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "ISRG", "AMGN"]},
    "Financials":        {"etf": "XLF",  "candidates": ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "BRK.B"]},
    "Energy":            {"etf": "XLE",  "candidates": ["XOM", "CVX", "COP", "EOG", "SLB", "WMB", "KMI", "PSX", "MPC"]},
    "Industrials":       {"etf": "XLI",  "candidates": ["GE", "CAT", "RTX", "UBER", "HON", "UNP", "BA", "DE", "ETN"]},
    "Materials":         {"etf": "XLB",  "candidates": ["LIN", "SHW", "APD", "ECL", "NEM", "FCX", "CTVA", "NUE"]},
    "Real Estate":       {"etf": "XLRE", "candidates": ["PLD", "AMT", "WELL", "EQIX", "SPG", "O", "PSA", "DLR"]},
    "Utilities":         {"etf": "XLU",  "candidates": ["NEE", "SO", "DUK", "CEG", "VST", "AEP", "SRE", "D"]},
}

# Per-expert parameter overrides (tune these after backtesting), e.g. {"ORB": {"rr": 2.0}}
# ---------------- Direction confirmation (v3) ----------------
# False = v1. True = an expert may only trade a direction that ALL of these agree on:
#   60-min trend structure (close vs EMA20 + EMA20 slope)
#   15-min momentum        (MACD 12/26/9 histogram sign)
#    5-min order flow      (EMA9 vs EMA21 on 5-min AND price vs session VWAP)
# Reversion experts use only the 60-min and 15-min votes (they buy dips / sell rips by design,
# so the 5-min flow is expected to point against them at entry).
DIRECTION_CONFIRM = False

# ---------------- Volatility-contraction + rising-volume filter (v4) ----------------
# Entry allowed only when short-term volatility is SHRINKING while volume is RISING:
#   ATR(5) < VX_ATR_RATIO x ATR(30)                    (recent bars narrower than normal)
#   avg volume last 5 bars > VX_VOL_RATIO x avg of the 30 bars before them
VOL_FILTER = False
VX_ATR_RATIO = 0.85
VX_VOL_RATIO = 1.20

PENDING_EXPIRY_S = 180     # a signal not filled within this many seconds is dropped

# ---------------- Runner exits (v6): -1R stop, no target, profit squeeze ----------------
# Initial stop = -1R. No profit target, no expert exits, no time stop (end-of-day flatten still applies).
# Once the best price reached is >= +1R, trail the stop behind that peak. The allowed give-back
# shrinks as profit grows:  give_back = max(MIN, START - STEP x (peak_R - 1))
#   peak +1R -> stop 0R (breakeven) | +2R -> stop +1.25R | +3R -> +2.5R | +4R and up -> peak - 0.25R
RUNNER_MODE = False
ZONE_FILTER = False       # 365-day zone filter for the 5 experts (--zones)
TRAIL_START_R = 1.0
TRAIL_GIVEBACK_START = 1.0
TRAIL_GIVEBACK_STEP = 0.25
TRAIL_GIVEBACK_MIN = 0.25

# ---------------- Exit geometry variant ----------------
# 1.0 / None = v1 behaviour. v2 = stop at 0.5 x the expert's stop distance, target at 1.5 x it.
STOP_FRACTION = 1.0
TARGET_R = None      # in units of the expert's ORIGINAL stop distance; None = expert's own target

EXPERT_OVERRIDES = {"ORB": {}, "VWAP": {}, "EMA": {}, "RSI2BB": {}, "SQZ": {}}
