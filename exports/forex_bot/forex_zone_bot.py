"""
Forex Buy/Sell Zone Detection Bot (OANDA v20 API)
==================================================

Combines:
  - Confirmed swing high/low structure
  - Fibonacci retracement confluence
  - Bollinger Band extremes + squeeze/walk detection
  - Expert-level candlestick patterns (pin bar, engulfing, morning/evening
    star, doji, inside bar, three soldiers/crows)
  - Wick rejection strength scoring

Outputs a ranked list of BUY / SELL zone candidates with a confidence
score and the specific reasons ("confluence") behind each one.

This is a decision-support / signal-generation bot. It does NOT place
live orders by default — see `place_order()` at the bottom, which is
commented out. Test everything on an OANDA PRACTICE account first.

pip install oandapyV20 pandas numpy
"""

import os
import numpy as np
import pandas as pd
import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders

try:
    from dotenv import load_dotenv
    load_dotenv()   # reads a local .env file if present; never commit that file
except ImportError:
    pass  # dotenv is optional — env vars can also be set directly in the shell


# ======================================================================
# CONFIG
# ======================================================================
class Config:
    # Credentials are read from environment variables, never hardcoded here.
    # Set these in a local .env file (see .env.example) or your shell.
    # Accepts either OANDA_API_TOKEN or OANDA_API_KEY as the token var name.
    ACCESS_TOKEN = os.environ.get("OANDA_API_TOKEN") or os.environ.get("OANDA_API_KEY", "")
    ENVIRONMENT = os.environ.get("OANDA_ENV", "practice")   # "practice" or "live"
    ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "")
    BASE_URL = os.environ.get("OANDA_URL", "")   # informational; oandapyV20 derives
                                                   # the host from ENVIRONMENT, so this
                                                   # only matters if it disagrees below

    DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

    INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY"]
    GRANULARITY = "H1"                # M15, M30, H1, H4, D
    CANDLE_COUNT = 300

    SWING_N = 3                       # bars each side to confirm a swing
    BB_PERIOD = 20
    BB_STD = 2
    FIB_LEVELS = [0.382, 0.5, 0.618]
    FIB_TOLERANCE_PIPS = 15           # proximity tolerance, converted per-pair

    MIN_SCORE_TO_FLAG = 3.0           # minimum confluence score to report


# ======================================================================
# 1. DATA FETCH
# ======================================================================
def fetch_candles(instrument, granularity=Config.GRANULARITY, count=Config.CANDLE_COUNT):
    client = oandapyV20.API(access_token=Config.ACCESS_TOKEN, environment=Config.ENVIRONMENT)
    params = {"count": count, "granularity": granularity, "price": "M"}
    r = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)

    rows = []
    for c in r.response["candles"]:
        if not c["complete"]:
            continue
        rows.append({
            "time": pd.to_datetime(c["time"]),
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
            "volume": c["volume"],
        })
    return pd.DataFrame(rows).set_index("time")


def pip_size(instrument):
    """JPY pairs quote 2 decimal places, most others 4."""
    return 0.01 if "JPY" in instrument else 0.0001


# ======================================================================
# 2. SWING STRUCTURE
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


# ======================================================================
# 3. BOLLINGER BANDS
# ======================================================================
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


# ======================================================================
# 4. CANDLESTICK PATTERN LIBRARY
# ======================================================================
def _body(r):
    return abs(r["close"] - r["open"])

def _range(r):
    return r["high"] - r["low"]

def _upper_wick(r):
    return r["high"] - max(r["open"], r["close"])

def _lower_wick(r):
    return min(r["open"], r["close"]) - r["low"]

def _is_bullish(r):
    return r["close"] > r["open"]

def _is_bearish(r):
    return r["close"] < r["open"]


def bullish_pin_bar(r):
    """Long lower wick, small body near the top — hammer-type rejection."""
    body, rng, lw = _body(r), _range(r), _lower_wick(r)
    if rng == 0:
        return False
    return lw >= rng * 0.6 and body <= rng * 0.3


def bearish_pin_bar(r):
    """Long upper wick, small body near the bottom — shooting-star rejection."""
    body, rng, uw = _body(r), _range(r), _upper_wick(r)
    if rng == 0:
        return False
    return uw >= rng * 0.6 and body <= rng * 0.3


def bullish_engulfing(prev, curr):
    return (_is_bearish(prev) and _is_bullish(curr)
            and curr["close"] >= prev["open"] and curr["open"] <= prev["close"])


def bearish_engulfing(prev, curr):
    return (_is_bullish(prev) and _is_bearish(curr)
            and curr["close"] <= prev["open"] and curr["open"] >= prev["close"])


def doji(r, threshold=0.1):
    rng = _range(r)
    if rng == 0:
        return False
    return _body(r) <= rng * threshold


def morning_star(c1, c2, c3):
    """3-candle bullish reversal: down candle, small indecision candle, strong up candle."""
    down_trend = _is_bearish(c1) and _body(c1) > _range(c1) * 0.5
    indecision = _body(c2) <= _range(c2) * 0.35
    strong_up = _is_bullish(c3) and c3["close"] >= (c1["open"] + c1["close"]) / 2
    gapped_down = max(c2["open"], c2["close"]) < c1["close"]
    return down_trend and indecision and strong_up and gapped_down


def evening_star(c1, c2, c3):
    up_trend = _is_bullish(c1) and _body(c1) > _range(c1) * 0.5
    indecision = _body(c2) <= _range(c2) * 0.35
    strong_down = _is_bearish(c3) and c3["close"] <= (c1["open"] + c1["close"]) / 2
    gapped_up = min(c2["open"], c2["close"]) > c1["close"]
    return up_trend and indecision and strong_down and gapped_up


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
    """Scan the last few candles and return a list of pattern names found
    at the most recent bar (single, 2-candle, and 3-candle patterns)."""
    patterns = []
    if len(df) < 3:
        return patterns

    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    if bullish_pin_bar(c3):
        patterns.append(("bullish_pin_bar", "buy"))
    if bearish_pin_bar(c3):
        patterns.append(("bearish_pin_bar", "sell"))
    if bullish_engulfing(c2, c3):
        patterns.append(("bullish_engulfing", "buy"))
    if bearish_engulfing(c2, c3):
        patterns.append(("bearish_engulfing", "sell"))
    if doji(c3):
        patterns.append(("doji", "neutral"))
    if morning_star(c1, c2, c3):
        patterns.append(("morning_star", "buy"))
    if evening_star(c1, c2, c3):
        patterns.append(("evening_star", "sell"))
    if three_white_soldiers(c1, c2, c3):
        patterns.append(("three_white_soldiers", "buy"))
    if three_black_crows(c1, c2, c3):
        patterns.append(("three_black_crows", "sell"))
    if inside_bar(c2, c3):
        patterns.append(("inside_bar", "neutral"))

    return patterns


# ======================================================================
# 5. ZONE / SIGNAL ENGINE
# ======================================================================
def evaluate_instrument(instrument):
    df = fetch_candles(instrument)
    if df.empty or len(df) < max(Config.BB_PERIOD, 50):
        return None

    df = detect_swings(df)
    df = add_bollinger(df)
    leg = get_last_swing_leg(df)
    zones = fib_zones(leg)
    patterns = detect_candle_patterns(df)

    latest = df.iloc[-1]
    price = latest["close"]
    pip = pip_size(instrument)
    tolerance = Config.FIB_TOLERANCE_PIPS * pip

    result = {
        "instrument": instrument,
        "time": df.index[-1],
        "price": price,
        "zone_type": None,
        "confluence": [],
        "score": 0.0,
    }

    votes = {"buy": 0.0, "sell": 0.0}

    # --- Fibonacci confluence ---
    for level, fib_price in zones.items():
        if abs(price - fib_price) <= tolerance:
            side = "buy" if leg["direction"] == "up" else "sell"
            result["confluence"].append(f"fib_{level}_{side}")
            votes[side] += 1.0

    # --- Bollinger confluence ---
    if latest["bb_walk_down"]:
        result["confluence"].append("bb_lower_touch")
        votes["buy"] += 1.0
    if latest["bb_walk_up"]:
        result["confluence"].append("bb_upper_touch")
        votes["sell"] += 1.0
    if latest["bb_squeeze"]:
        result["confluence"].append("bb_squeeze_breakout_watch")
        votes["buy"] += 0.25
        votes["sell"] += 0.25

    # --- Candlestick pattern confluence ---
    for name, side in patterns:
        result["confluence"].append(f"pattern_{name}")
        if side == "buy":
            votes["buy"] += 1.5   # patterns weighted heaviest — direct price action
        elif side == "sell":
            votes["sell"] += 1.5

    # --- Wick rejection strength (extra weight beyond pin-bar pattern match) ---
    body = _body(latest)
    if body > 0:
        if _lower_wick(latest) > body * 2:
            result["confluence"].append("strong_lower_wick_rejection")
            votes["buy"] += 1.0
        if _upper_wick(latest) > body * 2:
            result["confluence"].append("strong_upper_wick_rejection")
            votes["sell"] += 1.0

    # --- Trend/structure alignment bonus ---
    if leg is not None:
        if leg["direction"] == "up" and votes["buy"] > votes["sell"]:
            result["confluence"].append("trend_aligned")
            votes["buy"] += 0.5
        elif leg["direction"] == "down" and votes["sell"] > votes["buy"]:
            result["confluence"].append("trend_aligned")
            votes["sell"] += 0.5

    if votes["buy"] > votes["sell"]:
        result["zone_type"] = "buy"
        result["score"] = round(votes["buy"], 2)
    elif votes["sell"] > votes["buy"]:
        result["zone_type"] = "sell"
        result["score"] = round(votes["sell"], 2)

    return result


# ======================================================================
# 6. SCAN MULTIPLE INSTRUMENTS AND RANK
# ======================================================================
def scan_all(instruments_list=Config.INSTRUMENTS):
    results = []
    for inst in instruments_list:
        try:
            r = evaluate_instrument(inst)
            if r and r["zone_type"] and r["score"] >= Config.MIN_SCORE_TO_FLAG:
                results.append(r)
        except Exception as e:
            print(f"[WARN] {inst} failed: {e}")
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def print_report(results):
    if not results:
        print("No high-confidence zones found this scan.")
        return
    print(f"\n{'='*70}\nRANKED BUY/SELL ZONES\n{'='*70}")
    for r in results:
        print(f"\n{r['instrument']}  |  {r['zone_type'].upper()}  |  "
              f"score={r['score']}  |  price={r['price']:.5f}  |  {r['time']}")
        print(f"  Confluence: {', '.join(r['confluence'])}")


# ======================================================================
# 7. (OPTIONAL) TELEGRAM ALERTS
# ======================================================================
def send_telegram_alert(message):
    """Sends a message via Telegram bot. Requires TELEGRAM_BOT_TOKEN and
    TELEGRAM_CHAT_ID env vars. Silently skips if either is missing."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    import urllib.request
    import urllib.parse
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        print(f"[WARN] Telegram alert failed: {e}")


def alert_results(results):
    if not results:
        return
    lines = [f"{r['instrument']} — {r['zone_type'].upper()} (score {r['score']}) @ {r['price']:.5f}"
              for r in results]
    send_telegram_alert("Forex zone scan:\n" + "\n".join(lines))


# ======================================================================
# 8. (OPTIONAL) ORDER PLACEMENT — disabled by default
# ======================================================================
def place_order(instrument, side, units=1000):
    """
    Places a market order on OANDA. Only call this deliberately, after
    backtesting/forward-testing, and only on a PRACTICE account first.
    Respects DRY_RUN=true (default) by logging instead of sending the order.
    """
    if not Config.ACCESS_TOKEN or not Config.ACCOUNT_ID:
        raise RuntimeError("OANDA_API_TOKEN / OANDA_ACCOUNT_ID not set in environment.")

    if Config.DRY_RUN:
        print(f"[DRY RUN] Would place {side.upper()} order: {instrument} x{units} "
              f"on {Config.ENVIRONMENT}. Set DRY_RUN=false to actually send orders.")
        return {"dry_run": True, "instrument": instrument, "side": side, "units": units}

    client = oandapyV20.API(access_token=Config.ACCESS_TOKEN, environment=Config.ENVIRONMENT)
    order_units = units if side == "buy" else -units
    data = {
        "order": {
            "instrument": instrument,
            "units": str(order_units),
            "type": "MARKET",
            "positionFill": "DEFAULT",
        }
    }
    r = orders.OrderCreate(accountID=Config.ACCOUNT_ID, data=data)
    client.request(r)
    return r.response


# ======================================================================
# MAIN
# ======================================================================
def check_env():
    """Confirms required env vars are present without ever printing their values."""
    checks = {
        "OANDA_API_TOKEN / OANDA_API_KEY": bool(Config.ACCESS_TOKEN),
        "OANDA_ACCOUNT_ID": bool(Config.ACCOUNT_ID),
        "OANDA_ENV": bool(Config.ENVIRONMENT),
    }
    print("Environment check:")
    for name, found in checks.items():
        print(f"  [{'OK' if found else 'MISSING'}] {name}")

    expected_host = {"practice": "api-fxpractice.oanda.com", "live": "api-fxtrade.oanda.com"}
    if Config.BASE_URL and Config.ENVIRONMENT in expected_host:
        if expected_host[Config.ENVIRONMENT] not in Config.BASE_URL:
            print(f"  [WARN] OANDA_URL doesn't match OANDA_ENV='{Config.ENVIRONMENT}' — "
                  f"double check these agree.")

    return all(checks.values())


if __name__ == "__main__":
    env_ok = check_env()
    if not env_ok:
        print("\nFix the missing variables above (in your .env file) before continuing.")
    else:
        results = scan_all()
        print_report(results)
        alert_results(results)   # no-op if Telegram env vars aren't set

        # Example (commented out) — act on the top-ranked signal.
        # DRY_RUN defaults to true, so this logs instead of sending a real order
        # until you deliberately set DRY_RUN=false.
        # if results:
        #     top = results[0]
        #     place_order(top["instrument"], top["zone_type"], units=1000)

