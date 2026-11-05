"""
oanda_zone_bot.py — Forex version, at feature parity with alpaca_zone_bot.py
==============================================================================

This is the OANDA counterpart to alpaca_zone_bot.py, built to bring the
forex side back up to the same feature set the stock bot ended up with:
candlestick patterns, wick rejection, multi-touch support/resistance with
a breakout/breakdown VETO, tiered pyramiding into a zone, tiered profit
scale-out, a tighter-of-ATR-or-1% hard stop, and a circuit breaker that
actually computes trailing drawdown (earlier forex code didn't have the
S/R layer or pyramiding at all; a stock-bot regression pasted in this
conversation also had a circuit breaker that could never trigger — the
version here is the verified, tested implementation).

One structural difference from the stock bot, deliberately: this bot is
LONG AND SHORT. Buying a stock and shorting a stock are different actions
requiring different mechanics; in forex, selling a pair *is* the short —
so a "sell zone" here opens/adds to a short position with the same
pyramid-deeper logic the "buy zone" uses for longs, and profit-scaling
works symmetrically in both directions.

Tolerances are pip-scale (FIB_TOLERANCE_PIPS, SR TOLERANCE_PIPS), not
percentage-of-price — this is the OPPOSITE of the original stocks bug
(forex pip math leaking into stock-scale calculations). Getting this
backwards here would reintroduce the same class of bug in reverse.

No sector-heatmap or news-catalyst equivalent is included. "Sector" has
no direct forex analog, and OANDA doesn't expose a news endpoint the way
Alpaca does — bolting on a fake version of either would be the same
mistake already made twice with the stock bot's stubs. If you want a
comparable filter (e.g. avoiding entries during the illiquid rollover
hour, or gating on cross-pair correlation), that's a real feature to
design deliberately, not a placeholder to paste in.

pip install oandapyV20 pandas numpy python-dotenv
"""

import os
import json
import time
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.orders as orders


# ======================================================================
# LOGGING
# ======================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("oanda_zone_bot.log"), logging.StreamHandler()],
)
log = logging.getLogger("oanda_zone_bot")


# ======================================================================
# CONFIG
# ======================================================================
class Config:
    ACCESS_TOKEN = os.environ.get("OANDA_API_TOKEN") or os.environ.get("OANDA_API_KEY", "")
    ENVIRONMENT = os.environ.get("OANDA_ENV", "practice")
    ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "")
    DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

    INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"]
    GRANULARITY = "H1"
    CANDLE_COUNT = 300
    POLL_SECONDS = 300

    SWING_N = 3
    BB_PERIOD = 20
    BB_STD = 2
    FIB_LEVELS = [0.382, 0.5, 0.618]
    FIB_TOLERANCE_PIPS = 15
    ATR_PERIOD = 14
    MIN_SCORE_TO_FLAG = 3.0

    STATE_FILE = "oanda_state.json"


class SessionConfig:
    # Forex trades ~24/5; the analog to "avoid the open" is avoiding the
    # illiquid rollover window and weekends, not a fixed market-open hour.
    ROLLOVER_UTC_HOUR = 21          # ~5pm ET rollover, in UTC (adjust for DST as needed)
    ROLLOVER_BUFFER_MINUTES = 20    # blocks new entries this many minutes each side of rollover


class Weights:
    FIB = 1.0
    BB_TOUCH = 1.0
    BB_SQUEEZE = 0.25
    PATTERN = 1.5
    WICK_REJECTION = 1.0
    TREND_ALIGN = 0.5
    SR_ZONE_BASE = 1.0
    SR_TOUCH_BONUS = 0.25
    SR_TOUCH_BONUS_CAP = 1.0
    SR_CONFIRMED_BOUNCE = 1.5


class SRConfig:
    TOLERANCE_PIPS = 15          # zone-clustering tolerance, pip-scale not percentage
    MIN_TOUCHES = 3
    MAX_ZONE_AGE_BARS = 150


class EntryConfig:
    # (min score to reach this tier, CUMULATIVE % of equity allocated once reached)
    SCALE_TIERS = [
        (3.0, 0.25),
        (4.0, 0.50),
        (5.0, 1.00),
        (6.0, 1.50),
    ]


class ExitConfig:
    # (ATR multiple from avg entry, % of the position-at-that-time to close)
    SCALE_OUT_LEVELS = [
        (1.0, 0.25),
        (1.75, 0.35),
        (2.5, 0.40),
    ]


class RiskConfig:
    DRAWDOWN_LOOKBACK_HOURS = 8
    DRAWDOWN_PAUSE_PCT = -1.0
    PAUSE_HOURS = 8
    STOP_ATR_MULT = 2.0
    HARD_STOP_PCT = 1.0          # tighter of this or the ATR stop governs, same as the stock bot


# ======================================================================
# OANDA CLIENT
# ======================================================================
def get_client():
    return oandapyV20.API(access_token=Config.ACCESS_TOKEN, environment=Config.ENVIRONMENT)


def pip_size(instrument):
    return 0.01 if "JPY" in instrument else 0.0001


def fetch_candles(client, instrument, granularity=Config.GRANULARITY, count=Config.CANDLE_COUNT):
    params = {"count": count, "granularity": granularity, "price": "M"}
    r = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)
    rows = []
    for c in r.response["candles"]:
        if not c["complete"]:
            continue
        rows.append({
            "time": pd.to_datetime(c["time"]),
            "open": float(c["mid"]["o"]), "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]),
            "volume": c["volume"],
        })
    return pd.DataFrame(rows).set_index("time")


def get_account_balance(client):
    r = accounts.AccountSummary(accountID=Config.ACCOUNT_ID)
    client.request(r)
    return float(r.response["account"]["balance"])


# ======================================================================
# SWING / FIB / BOLLINGER / ATR
# ======================================================================
def detect_swings(df, n=Config.SWING_N):
    highs, lows = df["high"].values, df["low"].values
    length = len(df)
    swing_high = np.full(length, False)
    swing_low = np.full(length, False)
    for i in range(n, length - n):
        wh = highs[i - n:i + n + 1]
        wl = lows[i - n:i + n + 1]
        if highs[i] == wh.max() and np.argmax(wh) == n:
            swing_high[i] = True
        if lows[i] == wl.min() and np.argmin(wl) == n:
            swing_low[i] = True
    df = df.copy()
    df["swing_high"] = swing_high
    df["swing_low"] = swing_low
    return df


def get_last_swing_leg(df):
    swings = df[(df["swing_high"]) | (df["swing_low"])]
    if len(swings) < 2:
        return None
    p1, p2 = swings.iloc[-2], swings.iloc[-1]
    if p1["swing_low"] and p2["swing_high"]:
        return {"direction": "up", "low": p1["low"], "high": p2["high"]}
    if p1["swing_high"] and p2["swing_low"]:
        return {"direction": "down", "low": p2["low"], "high": p1["high"]}
    return None


def fib_zones(leg):
    if leg is None:
        return {}
    low, high = leg["low"], leg["high"]
    diff = high - low
    zones = {}
    for level in Config.FIB_LEVELS:
        zones[level] = (high - diff * level) if leg["direction"] == "up" else (low + diff * level)
    return zones


def add_bollinger(df, period=Config.BB_PERIOD, std=Config.BB_STD):
    df = df.copy()
    df["bb_mid"] = df["close"].rolling(period).mean()
    rstd = df["close"].rolling(period).std()
    df["bb_upper"] = df["bb_mid"] + std * rstd
    df["bb_lower"] = df["bb_mid"] - std * rstd
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    df["bb_squeeze"] = df["bb_width"] < df["bb_width"].rolling(50).quantile(0.2)
    df["bb_walk_up"] = df["close"] >= df["bb_upper"]
    df["bb_walk_down"] = df["close"] <= df["bb_lower"]
    return df


def add_atr(df, period=Config.ATR_PERIOD):
    df = df.copy()
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(period).mean()
    return df


# ======================================================================
# CANDLESTICK PATTERNS
# ======================================================================
def _body(r): return abs(r["close"] - r["open"])
def _range(r): return r["high"] - r["low"]
def _upper_wick(r): return r["high"] - max(r["open"], r["close"])
def _lower_wick(r): return min(r["open"], r["close"]) - r["low"]
def _is_bullish(r): return r["close"] > r["open"]
def _is_bearish(r): return r["close"] < r["open"]


def bullish_pin_bar(r):
    body, rng, lw = _body(r), _range(r), _lower_wick(r)
    return rng > 0 and lw >= rng * 0.6 and body <= rng * 0.3

def bearish_pin_bar(r):
    body, rng, uw = _body(r), _range(r), _upper_wick(r)
    return rng > 0 and uw >= rng * 0.6 and body <= rng * 0.3

def bullish_engulfing(prev, curr):
    return (_is_bearish(prev) and _is_bullish(curr)
            and curr["close"] >= prev["open"] and curr["open"] <= prev["close"])

def bearish_engulfing(prev, curr):
    return (_is_bullish(prev) and _is_bearish(curr)
            and curr["close"] <= prev["open"] and curr["open"] >= prev["close"])

def doji(r, threshold=0.1):
    rng = _range(r)
    return rng > 0 and _body(r) <= rng * threshold

def morning_star(c1, c2, c3):
    return (_is_bearish(c1) and _body(c1) > _range(c1) * 0.5
            and _body(c2) <= _range(c2) * 0.35
            and _is_bullish(c3) and c3["close"] >= (c1["open"] + c1["close"]) / 2
            and max(c2["open"], c2["close"]) < c1["close"])

def evening_star(c1, c2, c3):
    return (_is_bullish(c1) and _body(c1) > _range(c1) * 0.5
            and _body(c2) <= _range(c2) * 0.35
            and _is_bearish(c3) and c3["close"] <= (c1["open"] + c1["close"]) / 2
            and min(c2["open"], c2["close"]) > c1["close"])

def three_white_soldiers(c1, c2, c3):
    return (_is_bullish(c1) and _is_bullish(c2) and _is_bullish(c3)
            and c2["close"] > c1["close"] and c3["close"] > c2["close"]
            and c2["open"] > c1["open"] and c3["open"] > c2["open"])

def three_black_crows(c1, c2, c3):
    return (_is_bearish(c1) and _is_bearish(c2) and _is_bearish(c3)
            and c2["close"] < c1["close"] and c3["close"] < c2["close"]
            and c2["open"] < c1["open"] and c3["open"] < c2["open"])

def inside_bar(prev, curr):
    return curr["high"] <= prev["high"] and curr["low"] >= prev["low"]


def detect_candle_patterns(df):
    patterns = []
    if len(df) < 3:
        return patterns
    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if bullish_pin_bar(c3): patterns.append(("bullish_pin_bar", "buy"))
    if bearish_pin_bar(c3): patterns.append(("bearish_pin_bar", "sell"))
    if bullish_engulfing(c2, c3): patterns.append(("bullish_engulfing", "buy"))
    if bearish_engulfing(c2, c3): patterns.append(("bearish_engulfing", "sell"))
    if doji(c3): patterns.append(("doji", "neutral"))
    if morning_star(c1, c2, c3): patterns.append(("morning_star", "buy"))
    if evening_star(c1, c2, c3): patterns.append(("evening_star", "sell"))
    if three_white_soldiers(c1, c2, c3): patterns.append(("three_white_soldiers", "buy"))
    if three_black_crows(c1, c2, c3): patterns.append(("three_black_crows", "sell"))
    if inside_bar(c2, c3): patterns.append(("inside_bar", "neutral"))
    return patterns


# ======================================================================
# MULTI-TOUCH SUPPORT/RESISTANCE (pip-scale tolerance, not percentage)
# ======================================================================
def detect_sr_zones(df, instrument):
    tol_price = SRConfig.TOLERANCE_PIPS * pip_size(instrument)
    length = len(df)
    cutoff_idx = max(0, length - SRConfig.MAX_ZONE_AGE_BARS)
    recent = df.iloc[cutoff_idx:]

    def cluster(points):
        if not points:
            return []
        points = sorted(points, key=lambda p: p[1])
        clusters = [[points[0]]]
        for idx, price in points[1:]:
            cluster_level = np.mean([p[1] for p in clusters[-1]])
            if abs(price - cluster_level) <= tol_price:
                clusters[-1].append((idx, price))
            else:
                clusters.append([(idx, price)])
        zones = []
        for c in clusters:
            level = float(np.mean([p[1] for p in c]))
            zones.append({"price": level, "touches": len(c), "last_touch_idx": max(p[0] for p in c)})
        return zones

    support_points = [(i, recent["low"].iloc[i]) for i in range(len(recent)) if recent["swing_low"].iloc[i]]
    resistance_points = [(i, recent["high"].iloc[i]) for i in range(len(recent)) if recent["swing_high"].iloc[i]]
    return {"support": cluster(support_points), "resistance": cluster(resistance_points)}


def find_interacted_zone(zones, latest, instrument):
    """Zone whose price falls within this candle's [low, high] range plus a
    small pip tolerance — same range-containment logic as the stock bot,
    just pip-scale instead of percentage-of-price."""
    tol_price = SRConfig.TOLERANCE_PIPS * pip_size(instrument)
    candidates = [z for z in zones if latest["low"] - tol_price <= z["price"] <= latest["high"] + tol_price]
    if not candidates:
        return None
    return min(candidates, key=lambda z: abs(latest["close"] - z["price"]))


def confirms_bullish_bounce(df):
    if len(df) < 3:
        return False
    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if bullish_pin_bar(c3) or bullish_engulfing(c2, c3) or morning_star(c1, c2, c3) \
       or three_white_soldiers(c1, c2, c3):
        return True
    body = _body(c3)
    return body > 0 and _lower_wick(c3) > body * 2


def confirms_bearish_bounce(df):
    if len(df) < 3:
        return False
    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if bearish_pin_bar(c3) or bearish_engulfing(c2, c3) or evening_star(c1, c2, c3) \
       or three_black_crows(c1, c2, c3):
        return True
    body = _body(c3)
    return body > 0 and _upper_wick(c3) > body * 2


def is_clean_breakdown(df, zone_price):
    latest = df.iloc[-1]
    if latest["close"] >= zone_price:
        return False
    body, rng = _body(latest), _range(latest)
    if rng == 0:
        return False
    return _is_bearish(latest) and body >= rng * 0.6 and _lower_wick(latest) < body


def is_clean_breakout(df, zone_price):
    latest = df.iloc[-1]
    if latest["close"] <= zone_price:
        return False
    body, rng = _body(latest), _range(latest)
    if rng == 0:
        return False
    return _is_bullish(latest) and body >= rng * 0.6 and _upper_wick(latest) < body


# ======================================================================
# SCORING
# ======================================================================
def score_zone(df, instrument):
    df = detect_swings(df)
    df = add_bollinger(df)
    leg = get_last_swing_leg(df)
    zones = fib_zones(leg)
    patterns = detect_candle_patterns(df)

    latest = df.iloc[-1]
    price = latest["close"]
    pip = pip_size(instrument)
    tolerance = Config.FIB_TOLERANCE_PIPS * pip

    result = {"instrument": instrument, "time": df.index[-1], "price": price,
              "zone_type": None, "confluence": [], "score": 0.0}
    votes = {"buy": 0.0, "sell": 0.0}

    for level, fib_price in zones.items():
        if abs(price - fib_price) <= tolerance:
            side = "buy" if leg["direction"] == "up" else "sell"
            result["confluence"].append(f"fib_{level}_{side}")
            votes[side] += Weights.FIB

    if latest["bb_walk_down"]:
        result["confluence"].append("bb_lower_touch"); votes["buy"] += Weights.BB_TOUCH
    if latest["bb_walk_up"]:
        result["confluence"].append("bb_upper_touch"); votes["sell"] += Weights.BB_TOUCH
    if latest["bb_squeeze"]:
        result["confluence"].append("bb_squeeze_watch")
        votes["buy"] += Weights.BB_SQUEEZE; votes["sell"] += Weights.BB_SQUEEZE

    for name, side in patterns:
        result["confluence"].append(f"pattern_{name}")
        if side == "buy": votes["buy"] += Weights.PATTERN
        elif side == "sell": votes["sell"] += Weights.PATTERN

    body = _body(latest)
    if body > 0:
        if _lower_wick(latest) > body * 2:
            result["confluence"].append("strong_lower_wick"); votes["buy"] += Weights.WICK_REJECTION
        if _upper_wick(latest) > body * 2:
            result["confluence"].append("strong_upper_wick"); votes["sell"] += Weights.WICK_REJECTION

    if leg is not None:
        if leg["direction"] == "up" and votes["buy"] > votes["sell"]:
            result["confluence"].append("trend_aligned"); votes["buy"] += Weights.TREND_ALIGN
        elif leg["direction"] == "down" and votes["sell"] > votes["buy"]:
            result["confluence"].append("trend_aligned"); votes["sell"] += Weights.TREND_ALIGN

    # --- Multi-touch support/resistance, with breakdown/breakout VETO ---
    sr_zones = detect_sr_zones(df, instrument)
    breakdown_veto = False
    breakout_veto = False

    support_zone = find_interacted_zone(sr_zones["support"], latest, instrument)
    if support_zone and support_zone["touches"] >= SRConfig.MIN_TOUCHES:
        if is_clean_breakdown(df, support_zone["price"]):
            result["confluence"].append(f"support_breakdown_{support_zone['touches']}touch_VETOED_BUY")
            breakdown_veto = True
        else:
            touch_bonus = min((support_zone["touches"] - SRConfig.MIN_TOUCHES) * Weights.SR_TOUCH_BONUS,
                               Weights.SR_TOUCH_BONUS_CAP)
            result["confluence"].append(f"support_zone_{support_zone['touches']}touches")
            votes["buy"] += Weights.SR_ZONE_BASE + touch_bonus
            if confirms_bullish_bounce(df):
                result["confluence"].append("confirmed_bounce_off_support")
                votes["buy"] += Weights.SR_CONFIRMED_BOUNCE

    resistance_zone = find_interacted_zone(sr_zones["resistance"], latest, instrument)
    if resistance_zone and resistance_zone["touches"] >= SRConfig.MIN_TOUCHES:
        if is_clean_breakout(df, resistance_zone["price"]):
            result["confluence"].append(f"resistance_breakout_{resistance_zone['touches']}touch_VETOED_SELL")
            breakout_veto = True
        else:
            touch_bonus = min((resistance_zone["touches"] - SRConfig.MIN_TOUCHES) * Weights.SR_TOUCH_BONUS,
                               Weights.SR_TOUCH_BONUS_CAP)
            result["confluence"].append(f"resistance_zone_{resistance_zone['touches']}touches")
            votes["sell"] += Weights.SR_ZONE_BASE + touch_bonus
            if confirms_bearish_bounce(df):
                result["confluence"].append("confirmed_rejection_off_resistance")
                votes["sell"] += Weights.SR_CONFIRMED_BOUNCE

    if breakdown_veto:
        votes["buy"] = 0.0
    if breakout_veto:
        votes["sell"] = 0.0

    if votes["buy"] > votes["sell"]:
        result["zone_type"] = "buy"; result["score"] = round(votes["buy"], 2)
    elif votes["sell"] > votes["buy"]:
        result["zone_type"] = "sell"; result["score"] = round(votes["sell"], 2)

    return result


# ======================================================================
# STATE PERSISTENCE
# ======================================================================
def load_state():
    if os.path.exists(Config.STATE_FILE):
        with open(Config.STATE_FILE) as f:
            return json.load(f)
    return {"reference_equity": None, "paused_until": None, "trade_log": [], "positions": {}}


def save_state(state):
    with open(Config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def default_position_state():
    return {"side": None, "held_units": 0.0, "avg_entry_price": 0.0, "tier_idx": -1,
            "exit_plan_base_units": 0.0, "exit_tiers_fired": []}


def record_trade(state, instrument, side, units, price, realized_pnl=0.0, at=None):
    at = at or datetime.now(timezone.utc)
    state["trade_log"].append({
        "time": at.isoformat(),
        "instrument": instrument, "side": side, "units": units, "price": price,
        "realized_pnl": realized_pnl,
    })


# ======================================================================
# CIRCUIT BREAKER — the verified, working version (computes trailing P&L
# and actually sets paused_until; a regression pasted earlier in this
# conversation silently stripped that and could never trigger)
# ======================================================================
def check_circuit_breaker(state, now=None):
    """`now` defaults to real current time for live use. A backtester passes
    the SIMULATED bar's timestamp here so the trailing-window math evaluates
    against simulated time, not real wall-clock time — without this, a
    backtest would silently compare historical trade timestamps against
    today's date, which would break the pause logic entirely."""
    now = now or datetime.now(timezone.utc)

    if state.get("paused_until"):
        paused_until = datetime.fromisoformat(state["paused_until"])
        if now < paused_until:
            return True
        else:
            state["paused_until"] = None

    if not state.get("reference_equity"):
        return False

    cutoff = now - timedelta(hours=RiskConfig.DRAWDOWN_LOOKBACK_HOURS)
    recent_pnl = sum(
        t["realized_pnl"] for t in state["trade_log"]
        if datetime.fromisoformat(t["time"]) >= cutoff
    )
    pct = (recent_pnl / state["reference_equity"]) * 100

    if pct <= RiskConfig.DRAWDOWN_PAUSE_PCT:
        pause_until = now + timedelta(hours=RiskConfig.PAUSE_HOURS)
        state["paused_until"] = pause_until.isoformat()
        log.warning(f"CIRCUIT BREAKER TRIPPED: trailing {RiskConfig.DRAWDOWN_LOOKBACK_HOURS}h "
                    f"P&L = {pct:.2f}% of reference equity. Pausing new entries until {pause_until.isoformat()}.")
        return True

    return False


# ======================================================================
# SESSION FILTER (forex analog to the stock bot's market-hours gate)
# ======================================================================
def is_rollover_window(now=None):
    """Same `now` override pattern as check_circuit_breaker, and for the
    same reason — a backtester must evaluate this against simulated time."""
    now = now or datetime.now(timezone.utc)
    if now.weekday() == 5 or (now.weekday() == 6 and now.hour < 21):
        return True  # weekend — forex market closed
    rollover = now.replace(hour=SessionConfig.ROLLOVER_UTC_HOUR, minute=0, second=0, microsecond=0)
    buffer = timedelta(minutes=SessionConfig.ROLLOVER_BUFFER_MINUTES)
    return abs((now - rollover).total_seconds()) <= buffer.total_seconds()


# ======================================================================
# ORDER HELPERS
# ======================================================================
def submit_order(client, instrument, units):
    """units: positive to buy/go long, negative to sell/go short (OANDA convention)."""
    if Config.DRY_RUN:
        log.info(f"[DRY RUN] {'BUY' if units > 0 else 'SELL'} {instrument} units={units}")
        return {"dry_run": True}

    data = {"order": {"instrument": instrument, "units": str(int(units)),
                       "type": "MARKET", "positionFill": "DEFAULT"}}
    r = orders.OrderCreate(accountID=Config.ACCOUNT_ID, data=data)
    client.request(r)
    log.info(f"Order submitted: {instrument} units={units} -> {r.response}")
    return r.response


# ======================================================================
# ENTRY: pyramid into buy zone (long) or sell zone (short), symmetric
# ======================================================================
def handle_entry_zone(client, state, instrument, result, equity, atr_now, at=None):
    zone_side = result["zone_type"]                      # "buy" or "sell"
    pos_side = "long" if zone_side == "buy" else "short"  # what this would open/add to

    pos_state = state["positions"].setdefault(instrument, default_position_state())

    if pos_state["side"] is not None and pos_state["side"] != pos_side:
        return  # already holding the opposite direction; don't auto-flip

    score = result["score"]
    price = result["price"]

    target_idx = -1
    for i, (min_score, _) in enumerate(EntryConfig.SCALE_TIERS):
        if score >= min_score:
            target_idx = i
    if target_idx <= pos_state["tier_idx"]:
        return

    prev_pct = EntryConfig.SCALE_TIERS[pos_state["tier_idx"]][1] if pos_state["tier_idx"] >= 0 else 0.0
    target_pct = EntryConfig.SCALE_TIERS[target_idx][1]
    incremental_equity = equity * (target_pct - prev_pct) / 100.0
    if incremental_equity < 1.0:
        return

    units_magnitude = incremental_equity / price   # simplified; see forex_zone_bot notes
                                                     # on cross-pair currency conversion for sizing
    signed_units = units_magnitude if pos_side == "long" else -units_magnitude

    log.info(f"{instrument} {zone_side.upper()} signal score={score} tier={target_idx} "
             f"(equity {prev_pct}% -> {target_pct}%) units={signed_units:.0f} "
             f"confluence={result['confluence']}")

    order = submit_order(client, instrument, signed_units)
    if order is None:
        return

    new_total_units = pos_state["held_units"] + units_magnitude
    pos_state["avg_entry_price"] = (
        (pos_state["avg_entry_price"] * pos_state["held_units"] + price * units_magnitude) / new_total_units
    )
    pos_state["held_units"] = new_total_units
    pos_state["side"] = pos_side
    pos_state["tier_idx"] = target_idx
    pos_state["exit_plan_base_units"] = new_total_units

    record_trade(state, instrument, zone_side, units_magnitude, price, at=at)


# ======================================================================
# EXIT: scale out the farther price advances favorably; tighter-of-two
# protective stop closes the full remainder
# ======================================================================
def handle_exit_zone(client, state, instrument, df, atr_now, at=None):
    pos_state = state["positions"].get(instrument)
    if not pos_state or pos_state["held_units"] <= 0:
        return

    side = pos_state["side"]           # "long" or "short"
    price = df.iloc[-1]["close"]
    entry = pos_state["avg_entry_price"]
    direction = 1 if side == "long" else -1   # +1: profit is price UP; -1: profit is price DOWN

    # --- Protective stop: tighter of ATR stop or hard 1% stop ---
    stop_candidates = []
    if RiskConfig.STOP_ATR_MULT is not None:
        stop_candidates.append(("atr_stop", entry - direction * atr_now * RiskConfig.STOP_ATR_MULT))
    if RiskConfig.HARD_STOP_PCT is not None:
        stop_candidates.append(("hard_1pct_stop", entry - direction * entry * (RiskConfig.HARD_STOP_PCT / 100)))

    if stop_candidates:
        # Tighter stop = triggers sooner on the losing side: for a long
        # that's the HIGHER of the two candidate prices (closer to entry
        # from below); for a short it's the LOWER of the two (closer to
        # entry from above).
        if side == "long":
            stop_reason, stop_price = max(stop_candidates, key=lambda x: x[1])
        else:
            stop_reason, stop_price = min(stop_candidates, key=lambda x: x[1])
        stop_hit = (price <= stop_price) if side == "long" else (price >= stop_price)
        if stop_hit:
            units = pos_state["held_units"]
            loss_pct = direction * (price - entry) / entry * 100
            log.warning(f"{instrument}: {stop_reason} hit on {side} ({price:.5f} vs stop {stop_price:.5f}, "
                        f"{loss_pct:.2f}%), closing full position ({units:.0f} units).")
            signed_close_units = -units if side == "long" else units
            order = submit_order(client, instrument, signed_close_units)
            if order is not None:
                pnl = direction * (price - entry) * units
                record_trade(state, instrument, "close", units, price, realized_pnl=pnl, at=at)
                state["positions"][instrument] = default_position_state()
            return

    # --- Scaled profit-taking ---
    for idx, (atr_mult, pct) in enumerate(ExitConfig.SCALE_OUT_LEVELS):
        if idx in pos_state["exit_tiers_fired"]:
            continue
        target_price = entry + direction * atr_now * atr_mult
        target_hit = (price >= target_price) if side == "long" else (price <= target_price)
        if not target_hit:
            continue

        units_to_close = min(pos_state["held_units"], round(pos_state["exit_plan_base_units"] * pct))
        if units_to_close <= 0:
            pos_state["exit_tiers_fired"].append(idx)
            continue

        log.info(f"{instrument}: exit tier {idx} reached ({atr_mult}x ATR favorable on {side}, "
                 f"price {price:.5f} vs target {target_price:.5f}), closing {units_to_close:.0f} units "
                 f"({pct*100:.0f}% of position basis).")

        signed_close_units = -units_to_close if side == "long" else units_to_close
        order = submit_order(client, instrument, signed_close_units)
        if order is not None:
            pnl = direction * (price - entry) * units_to_close
            record_trade(state, instrument, "close", units_to_close, price, realized_pnl=pnl, at=at)
            pos_state["held_units"] -= units_to_close
            pos_state["exit_tiers_fired"].append(idx)

        if pos_state["held_units"] <= 0.5:
            state["positions"][instrument] = default_position_state()
            break


# ======================================================================
# MAIN LOOP
# ======================================================================
def run_once(client, state):
    equity = get_account_balance(client)

    if state["reference_equity"] is None:
        state["reference_equity"] = equity
        log.info(f"Reference equity set to {equity:.2f} (used for the -1%/8h circuit breaker).")

    paused = check_circuit_breaker(state)
    if paused:
        log.info("Circuit breaker active — new entries blocked this cycle. Exits still run.")

    rollover = is_rollover_window()
    if rollover:
        log.info("Weekend or rollover window — new entries blocked this cycle. Exits still run.")

    for instrument in Config.INSTRUMENTS:
        try:
            df = fetch_candles(client, instrument)
        except Exception as e:
            log.error(f"{instrument}: failed to fetch candles: {e}")
            continue

        if df.empty or len(df) < max(Config.BB_PERIOD, 50):
            continue

        df = add_atr(df)
        atr_now = df.iloc[-1]["atr"]
        if pd.isna(atr_now) or atr_now == 0:
            continue

        # Exits always run, regardless of pause/rollover — same principle
        # as the stock bot: never freeze your only exit mechanism.
        handle_exit_zone(client, state, instrument, df, atr_now)

        if paused or rollover:
            continue

        result = score_zone(df, instrument)
        if result["zone_type"] and result["score"] >= Config.MIN_SCORE_TO_FLAG:
            handle_entry_zone(client, state, instrument, result, equity, atr_now)

    save_state(state)


def main():
    if not Config.ACCESS_TOKEN or not Config.ACCOUNT_ID:
        log.error("OANDA_API_TOKEN/OANDA_API_KEY and OANDA_ACCOUNT_ID must be set. Check your .env.")
        return

    client = get_client()
    state = load_state()

    log.info(f"Starting oanda_zone_bot. DRY_RUN={Config.DRY_RUN}. Environment={Config.ENVIRONMENT}. "
             f"Polling every {Config.POLL_SECONDS}s.")

    while True:
        cycle_start = datetime.now(timezone.utc)
        log.info(f"--- Scan cycle {cycle_start.isoformat()} ---")
        try:
            run_once(client, state)
        except Exception as e:
            log.error(f"Cycle failed: {e}")
        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        time.sleep(max(0, Config.POLL_SECONDS - elapsed))


if __name__ == "__main__":
    main()

