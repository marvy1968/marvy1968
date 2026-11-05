
# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import os
import sys
import time
import logging
import requests
import numpy as np
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------
# ⚙️ CONFIGURATION & CREDENTIALS
# ---------------------------------------------------------
APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "REDACTED_BROKER_KEY").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "EnXL5FZSVSQzHvVTGLh1venMboWHXhGeQ2Kgy2gjzX5e").strip()
BASE_URL = (os.getenv("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets").strip()
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/logs/multi_scalper_shadow.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

TICKER_UNIVERSE = ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]
ACTIVE_POSITIONS = {}  # symbol -> {side, entry, sl, tp, be_level, risk, qty, strategy, order_id}

FIXED_DOLLAR_RISK = 50.0  # $50 max loss per trade
TARGET_RR = 0.75
MOVE_BE_AT = 0.40

# ---------------------------------------------------------
# 🕒 TIME OF DAY (TOD) LIQUIDITY FILTER
# ---------------------------------------------------------
def is_valid_tod():
    now_utc = datetime.now(timezone.utc)
    hour_est = (now_utc.hour - 4) % 24
    minute = now_utc.minute
    time_decimal = hour_est + (minute / 60.0)

    # 10:00 AM - 11:30 AM & 1:30 PM - 3:30 PM EST
    in_morning = (10.0 <= time_decimal <= 11.5)
    in_afternoon = (13.5 <= time_decimal <= 15.5)
    return in_morning or in_afternoon

# ---------------------------------------------------------
# 📊 TECHNICAL DATA & ALPACA ORDER API
# ---------------------------------------------------------
def fetch_bars(symbol, timeframe="5Min", limit=100):
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    start_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"timeframe": timeframe, "limit": limit, "start": start_date}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=8)
        if r.status_code == 200:
            data = r.json()
            return data.get("bars", []) or []
    except Exception as e:
        logging.warning(f"Error fetching bars for {symbol}: {e}")
    return []

def calculate_atr(bars_slice, period=14):
    if len(bars_slice) < period + 1:
        return bars_slice[-1]["h"] - bars_slice[-1]["l"]
    tr_list = []
    for j in range(1, len(bars_slice)):
        h = bars_slice[j]["h"]
        l = bars_slice[j]["l"]
        cp = bars_slice[j-1]["c"]
        tr = max(h - l, abs(h - cp), abs(l - cp))
        tr_list.append(tr)
    return np.mean(tr_list[-period:]) if tr_list else 1.0

def calculate_rsi(closes, period=2):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def get_spy_regime():
    spy_bars = fetch_bars("SPY", "5Min", 50)
    if len(spy_bars) < 25:
        return "NEUTRAL", 0.0
    closes = [b["c"] for b in spy_bars]
    spy_price = closes[-1]
    spy_sma20 = np.mean(closes[-20:])
    return ("BULLISH" if spy_price >= spy_sma20 else "BEARISH"), spy_price

def place_alpaca_order(symbol, side, qty):
    url = f"{BASE_URL}/v2/orders"
    order_side = "buy" if side == "LONG" else "sell"
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": order_side,
        "type": "market",
        "time_in_force": "day"
    }
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=8)
        if r.status_code in [200, 201]:
            order = r.json()
            logging.info(f"⚡ [ALPACA LIVE PAPER] {side} {qty} shares of {symbol} | OrderID: {order.get('id')}")
            return order.get("id")
        else:
            logging.error(f"❌ Alpaca Order Failed for {symbol}: {r.text}")
    except Exception as e:
        logging.error(f"Exception submitting order for {symbol}: {e}")
    return None

def close_alpaca_position(symbol, side, qty):
    url = f"{BASE_URL}/v2/orders"
    order_side = "sell" if side == "LONG" else "buy"
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": order_side,
        "type": "market",
        "time_in_force": "day"
    }
    try:
        requests.post(url, json=payload, headers=HEADERS, timeout=8)
        logging.info(f"🔄 [ALPACA POSITION CLOSED] {symbol} {order_side.upper()} {qty} shares")
    except Exception as e:
        logging.error(f"Error closing position for {symbol}: {e}")

# ---------------------------------------------------------
# 🛡️ UNIFIED LIFECYCLE & RISK ARBITRATOR
# ---------------------------------------------------------
def manage_positions():
    global ACTIVE_POSITIONS
    symbols_to_remove = []

    for sym, pos in list(ACTIVE_POSITIONS.items()):
        bars = fetch_bars(sym, "5Min", 5)
        if not bars:
            continue
        current_bar = bars[-1]
        high_price = current_bar["h"]
        low_price = current_bar["l"]

        side = pos["side"]
        entry = pos["entry"]
        sl = pos["sl"]
        tp = pos["tp"]
        be_level = pos["be_level"]
        risk = pos["risk"]
        qty = pos["qty"]
        strat = pos["strategy"]

        if side == "LONG":
            # 1. Target Profit Hit
            if high_price >= tp:
                logging.info(f"🟢 [{sym}] [{strat}] TARGET HIT @ ${tp:.2f} (+{TARGET_RR}R Profit)")
                close_alpaca_position(sym, side, qty)
                symbols_to_remove.append(sym)
            # 2. Stop Loss Hit
            elif low_price <= sl:
                pnl_r = (sl - entry) / risk
                logging.info(f"🔴 [{sym}] [{strat}] STOP HIT @ ${sl:.2f} ({pnl_r:+.2f}R PnL)")
                close_alpaca_position(sym, side, qty)
                symbols_to_remove.append(sym)
            # 3. Breakeven Armor Trigger
            elif high_price >= be_level and sl < entry:
                pos["sl"] = entry
                logging.info(f"🛡️ [{sym}] [{strat}] BREAKEVEN TRIGGERED: SL moved to entry ${entry:.2f}")

        elif side == "SHORT":
            # 1. Target Profit Hit
            if low_price <= tp:
                logging.info(f"🟢 [{sym}] [{strat}] SHORT TARGET HIT @ ${tp:.2f} (+{TARGET_RR}R Profit)")
                close_alpaca_position(sym, side, qty)
                symbols_to_remove.append(sym)
            # 2. Stop Loss Hit
            elif high_price >= sl:
                pnl_r = (entry - sl) / risk
                logging.info(f"🔴 [{sym}] [{strat}] SHORT STOP HIT @ ${sl:.2f} ({pnl_r:+.2f}R PnL)")
                close_alpaca_position(sym, side, qty)
                symbols_to_remove.append(sym)
            # 3. Breakeven Armor Trigger
            elif low_price <= be_level and sl > entry:
                pos["sl"] = entry
                logging.info(f"🛡️ [{sym}] [{strat}] SHORT BREAKEVEN TRIGGERED: SL moved to entry ${entry:.2f}")

    for s in symbols_to_remove:
        ACTIVE_POSITIONS.pop(s, None)

# ---------------------------------------------------------
# 🎯 SCANNER & TRIGGER ENGINE (ALL 4 POSITIVE-ROI STRATEGIES)
# ---------------------------------------------------------
def scan_universe():
    if not is_valid_tod():
        logging.info("⏳ Outside optimal TOD momentum windows (10:00-11:30 AM / 1:30-3:30 PM EST). Scanner idle.")
        return

    spy_regime, spy_price = get_spy_regime()
    logging.info(f"🏛️ SPY Regime: {spy_regime} (${spy_price:.2f})")

    for sym in TICKER_UNIVERSE:
        if sym in ACTIVE_POSITIONS:
            continue

        bars = fetch_bars(sym, "5Min", 60)
        if len(bars) < 35:
            continue

        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]
        vols = [b["v"] for b in bars]

        # Day-VWAP Calculation
        typical_prices = (np.array(highs[-30:]) + np.array(lows[-30:]) + np.array(closes[-30:])) / 3.0
        vols_slice = np.array(vols[-30:])
        vwap = np.sum(typical_prices * vols_slice) / (np.sum(vols_slice) + 1e-5)

        price = closes[-1]
        sma20 = np.mean(closes[-20:])
        sma50 = np.mean(closes[-50:])
        atr = calculate_atr(bars[-20:], period=14)
        rsi2 = calculate_rsi(closes[-10:], period=2)
        dist_from_vwap = (price - vwap) / atr

        signal_found = False
        side, sl, tp, be_level, risk, strat = None, None, None, None, None, None

        # ---------------------------------------------------------------------
        # 1. 🥇 MINERVINI VCP + CONNORS RSI(2) (75.9% WR | +0.431R Net EV)
        # ---------------------------------------------------------------------
        prev_ranges = [highs[k] - lows[k] for k in range(-4, -1)]
        is_vcp_coiled = np.mean(prev_ranges) <= (1.1 * atr)

        if is_vcp_coiled and (price >= vwap) and (rsi2 <= 12.0) and (closes[-1] > opens[-1]):
            side = "LONG"
            strat = "MINERVINI_VCP_CONNORS"
            sl = min(lows[-3:]) - (0.05 * atr)
            risk = price - sl
            if risk > 0:
                tp = price + (TARGET_RR * risk)
                be_level = price + (MOVE_BE_AT * risk)
                signal_found = True

        elif is_vcp_coiled and (price <= vwap) and (rsi2 >= 88.0) and (closes[-1] < opens[-1]):
            side = "SHORT"
            strat = "MINERVINI_VCP_CONNORS"
            sl = max(highs[-3:]) + (0.05 * atr)
            risk = sl - price
            if risk > 0:
                tp = price - (TARGET_RR * risk)
                be_level = price - (MOVE_BE_AT * risk)
                signal_found = True

        # ---------------------------------------------------------------------
        # 2. 🥈 VELEZ 20 SMA + CONNORS RSI(2) (70.3% WR | +0.308R Net EV)
        # ---------------------------------------------------------------------
        if not signal_found:
            near_20sma = abs(price - sma20) <= (1.0 * atr)

            if (spy_regime == "BULLISH") and (price >= vwap) and (sma20 > sma50) and near_20sma and (rsi2 <= 10.0):
                side = "LONG"
                strat = "VELEZ_20SMA_CONNORS"
                sl = min(lows[-3:]) - (0.05 * atr)
                risk = price - sl
                if risk > 0:
                    tp = price + (TARGET_RR * risk)
                    be_level = price + (MOVE_BE_AT * risk)
                    signal_found = True

            elif (spy_regime == "BEARISH") and (price <= vwap) and (sma20 < sma50) and near_20sma and (rsi2 >= 90.0):
                side = "SHORT"
                strat = "VELEZ_20SMA_CONNORS"
                sl = max(highs[-3:]) + (0.05 * atr)
                risk = sl - price
                if risk > 0:
                    tp = price - (TARGET_RR * risk)
                    be_level = price - (MOVE_BE_AT * risk)
                    signal_found = True

        # ---------------------------------------------------------------------
        # 3. 🥉 TRIPLE CONFLUENCE MASTER SNIPER (69.4% WR | +0.286R Net EV)
        # ---------------------------------------------------------------------
        if not signal_found:
            near_20sma = abs(price - sma20) <= (1.2 * atr)
            coiled = np.mean([highs[k] - lows[k] for k in range(-3, 0)]) <= (1.2 * atr)

            if (spy_regime == "BULLISH") and (price >= vwap) and (sma20 > sma50) and near_20sma and coiled and (rsi2 <= 10.0):
                side = "LONG"
                strat = "MASTER_SNIPER"
                sl = min(lows[-2:]) - (0.05 * atr)
                risk = price - sl
                if risk > 0:
                    tp = price + (TARGET_RR * risk)
                    be_level = price + (MOVE_BE_AT * risk)
                    signal_found = True

            elif (spy_regime == "BEARISH") and (price <= vwap) and (sma20 < sma50) and near_20sma and coiled and (rsi2 >= 90.0):
                side = "SHORT"
                strat = "MASTER_SNIPER"
                sl = max(highs[-2:]) + (0.05 * atr)
                risk = sl - price
                if risk > 0:
                    tp = price - (TARGET_RR * risk)
                    be_level = price - (MOVE_BE_AT * risk)
                    signal_found = True

        # ---------------------------------------------------------------------
        # 4. ⚓ VWAP 2.0-ATR EXHAUSTION FADE (46.5% WR | +0.090R Net EV)
        # ---------------------------------------------------------------------
        if not signal_found:
            if dist_from_vwap <= -1.8 and closes[-1] > opens[-1]:
                side = "LONG"
                strat = "VWAP_FADE"
                sl = lows[-1] - (0.10 * atr)
                risk = price - sl
                if risk > 0:
                    tp = price + (1.0 * risk)
                    be_level = price + (0.50 * risk)
                    signal_found = True

            elif dist_from_vwap >= 1.8 and closes[-1] < opens[-1]:
                side = "SHORT"
                strat = "VWAP_FADE"
                sl = highs[-1] + (0.10 * atr)
                risk = sl - price
                if risk > 0:
                    tp = price - (1.0 * risk)
                    be_level = price - (0.50 * risk)
                    signal_found = True

        # ---------------------------------------------------------------------
        # 🚀 SUBMIT LIVE PAPER ORDER & REGISTER POSITION
        # ---------------------------------------------------------------------
        if signal_found and risk > 0:
            qty = max(1, int(FIXED_DOLLAR_RISK / risk))
            order_id = place_alpaca_order(sym, side, qty)

            ACTIVE_POSITIONS[sym] = {
                "side": side,
                "entry": price,
                "sl": sl,
                "tp": tp,
                "be_level": be_level,
                "risk": risk,
                "qty": qty,
                "strategy": strat,
                "order_id": order_id
            }
            logging.info(f"🎯 [{sym}] {side} ENTERED via {strat} | Qty: {qty} | Entry: ${price:.2f} | SL: ${sl:.2f} | TP: ${tp:.2f} | BE Trigger: ${be_level:.2f}")

# ---------------------------------------------------------
# 🚀 MAIN DAEMON LOOP
# ---------------------------------------------------------
def main():
    logging.info("=" * 90)
    logging.info("🚀 WEINSTEIN MASTER BRAIN — ALL POSITIVE-ROI COMBOS (LIVE ALPACA PAPER)")
    logging.info("=" * 90)
    
    while True:
        try:
            manage_positions()
            scan_universe()
        except Exception as e:
            logging.error(f"Execution cycle error: {e}")
        time.sleep(20)

if __name__ == "__main__":
    main()
