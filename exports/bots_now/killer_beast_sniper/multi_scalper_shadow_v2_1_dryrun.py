
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
from datetime import datetime, timezone
from dotenv import load_dotenv

# ---------------------------------------------------------
# ⚙️ CREDENTIALS & INITIALIZATION
# ---------------------------------------------------------
load_dotenv("/home/moonrider1968/bots/killer_beast_sniper/.env")

API_KEY = os.getenv("APCA_API_KEY_ID", "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").strip()
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
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

# ---------------------------------------------------------
# 🌐 60-TICKER SECTOR UNIVERSE
# ---------------------------------------------------------
SECTORS = {
    "TECH": ["NVDA", "AAPL", "MSFT", "AMD", "AVGO", "QCOM", "INTC", "MU", "AMAT", "TSM"],
    "COMM": ["META", "GOOGL", "NFLX", "DIS", "SPOT", "TMUS", "CMCSA", "RBLX", "SNAP", "PINS"],
    "DISC": ["AMZN", "TSLA", "BABA", "NKE", "SBUX", "HD", "MCD", "LULU", "BKNG", "ABNB"],
    "FIN":  ["JPM", "BAC", "GS", "MS", "WFC", "V", "MA", "COIN", "HOOD", "SOFI", "BLK", "AXP"],
    "HEALTH":["LLY", "NVO", "UNH", "JNJ", "ABBV", "MRK", "PFE", "ISRG", "BMY", "MRNA", "ABT"],
    "IND_NRG":["XOM", "CVX", "COP", "SLB", "CAT", "GE", "BA", "UNP", "HON", "LMT", "UPS"],
    "CONS_DEF":["PEP", "KO", "WMT", "TGT", "TJX", "COST", "PG", "CL", "MDLZ", "MO"]
}

TICKER_TO_SECTOR = {}
for sector, tickers in SECTORS.items():
    for t in tickers:
        TICKER_TO_SECTOR[t] = sector

WATCHLIST = [t for group in SECTORS.values() for t in group]
ACTIVE_POSITIONS = {}

# ---------------------------------------------------------
# 💼 RISK & PORTFOLIO SIZING RULES
# ---------------------------------------------------------
MAX_OPEN_POSITIONS = 10       # Max concurrency cap
MAX_PER_SECTOR = 2           # Max 2 concurrent positions per sector
ENTRY_COOLDOWN_SEC = 900     # 15-minute staggered entry delay
MAX_CONSECUTIVE_LOSSES = 3   # Freeze after 3 consecutive losses
CIRCUIT_BREAKER_FREEZE = 1800 # 30-minute freeze cooldown (seconds)

EQUITY_RISK_PCT = 0.01        # 1.0% Equity Risk per Trade

# MULTI_SCALPER_V2_SAFETY_REPAIR
#
# Defense-in-depth sizing controls.
#
# Risk sizing remains based on the intended maximum loss between
# entry and stop, but share quantity may never exceed the separate
# notional / buying-power / absolute-share limits below.
MIN_STOP_RISK_PCT = 0.0035       # stop must be >= 0.35% of price
MIN_STOP_ATR_MULT = 0.35         # or >= 0.35 ATR, whichever is larger
MAX_POSITION_PCT = 0.05          # max 5% of account equity per entry
MAX_BUYING_POWER_PCT = 0.10      # max 10% of available BP per entry
MAX_ABSOLUTE_SHARES = 1000       # emergency final share ceiling
MIN_VALID_PRICE = 1.00

# MULTI_SCALPER_V2_1_DRYRUN
DRY_RUN = True
DRY_RUN_LOG_EVERY_SIGNAL = True
DRY_RUN_ALLOW_ENTRY_POST = False
TARGET_RR = 0.85              # TP target (+0.85R)
MOVE_BE_AT = 0.40             # Breakeven armor triggered at +0.40R
TRAIL_TRIGGER = 0.60          # Trailing ratchet triggered at +0.60R
TRAIL_LOCK_OFFSET = 0.30      # Trailing locked profit (+0.30R)

LAST_ENTRY_TIME = 0
CONSECUTIVE_LOSS_COUNT = 0
CIRCUIT_BREAKER_UNTIL = 0

def get_alpaca_equity():
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=8)
        if r.status_code == 200:
            return float(r.json().get("equity", 100000.0))
    except Exception as e:
        logging.warning(f"Error fetching Alpaca equity: {e}")
    return 100000.0

def fetch_bars(symbol, timeframe="5Min", limit=60):
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    params = {"timeframe": timeframe, "limit": limit, "adjustment": "all"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=8)
        if r.status_code == 200:
            raw = r.json().get("bars", [])
            bars = []
            for b in raw:
                bars.append({
                    "t": b["t"], "o": float(b["o"]), "h": float(b["h"]),
                    "l": float(b["l"]), "c": float(b["c"]), "v": int(b["v"])
                })
            return bars
    except Exception as e:
        logging.warning(f"Error fetching bars for {symbol}: {e}")
    return []

def calculate_atr(bars_slice, period=14):
    if len(bars_slice) < period + 1:
        return bars_slice[-1]["h"] - bars_slice[-1]["l"] if bars_slice else 0.50
    tr_list = []
    for j in range(1, len(bars_slice)):
        h, l, cp = bars_slice[j]["h"], bars_slice[j]["l"], bars_slice[j-1]["c"]
        tr_list.append(max(h - l, abs(h - cp), abs(l - cp)))
    return np.mean(tr_list[-period:]) if tr_list else 0.50

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

def get_alpaca_account_snapshot():
    """
    Fetch the current paper-account equity and buying power.

    New entries fail closed if either value cannot be obtained.
    """
    try:
        r = requests.get(
            f"{BASE_URL}/v2/account",
            headers=HEADERS,
            timeout=8,
        )

        if r.status_code != 200:
            logging.error(
                "ALPACA SAFETY: account snapshot rejected "
                f"HTTP={r.status_code} body={r.text[:300]}"
            )
            return None

        data = r.json()

        equity = float(data.get("equity", 0.0))
        buying_power = float(
            data.get("buying_power", 0.0)
        )

        if (
            not np.isfinite(equity)
            or not np.isfinite(buying_power)
            or equity <= 0
            or buying_power <= 0
        ):
            logging.error(
                "ALPACA SAFETY: invalid account values "
                f"equity={equity} buying_power={buying_power}"
            )
            return None

        return {
            "equity": equity,
            "buying_power": buying_power,
        }

    except Exception as e:
        logging.error(
            "ALPACA SAFETY: account snapshot failed: "
            f"{e}"
        )
        return None


def calculate_safe_qty(
    price,
    risk_per_share,
    equity,
    buying_power,
    atr,
):
    """
    Defense-in-depth position sizing.

    Returns:
        (qty, details)

    qty == 0 means the candidate is rejected.
    """

    values = [
        price,
        risk_per_share,
        equity,
        buying_power,
        atr,
    ]

    try:
        values = [float(x) for x in values]
    except Exception:
        return 0, {
            "reason": "non_numeric_input"
        }

    price, risk_per_share, equity, buying_power, atr = values

    if not all(np.isfinite(x) for x in values):
        return 0, {
            "reason": "non_finite_input"
        }

    if (
        price < MIN_VALID_PRICE
        or risk_per_share <= 0
        or equity <= 0
        or buying_power <= 0
        or atr <= 0
    ):
        return 0, {
            "reason": "invalid_nonpositive_input"
        }

    minimum_risk = max(
        price * MIN_STOP_RISK_PCT,
        atr * MIN_STOP_ATR_MULT,
    )

    # Tight stops produce explosively large share counts.
    # Reject rather than merely cap those malformed setups.
    # Auto-floor risk to minimum safe buffer instead of rejecting
    if risk_per_share < minimum_risk:
        risk_per_share = minimum_risk

    target_dollar_risk = (
        equity * EQUITY_RISK_PCT
    )

    risk_qty = int(
        target_dollar_risk
        / risk_per_share
    )

    equity_notional_cap = (
        equity * MAX_POSITION_PCT
    )

    bp_notional_cap = (
        buying_power
        * MAX_BUYING_POWER_PCT
    )

    allowed_notional = min(
        equity_notional_cap,
        bp_notional_cap,
    )

    notional_qty = int(
        allowed_notional
        / price
    )

    qty = min(
        risk_qty,
        notional_qty,
        MAX_ABSOLUTE_SHARES,
    )

    if qty < 1:
        return 0, {
            "reason": "quantity_below_one",
            "risk_qty": risk_qty,
            "notional_qty": notional_qty,
            "allowed_notional": allowed_notional,
        }

    actual_notional = (
        qty * price
    )

    actual_dollar_risk = (
        qty * risk_per_share
    )

    return qty, {
        "reason": "approved",
        "qty": qty,
        "risk_qty": risk_qty,
        "notional_qty": notional_qty,
        "price": price,
        "risk_per_share": risk_per_share,
        "minimum_risk": minimum_risk,
        "target_dollar_risk": target_dollar_risk,
        "actual_dollar_risk": actual_dollar_risk,
        "actual_notional": actual_notional,
        "allowed_notional": allowed_notional,
        "equity_notional_cap": equity_notional_cap,
        "bp_notional_cap": bp_notional_cap,
    }


def validate_entry_order(
    symbol,
    qty,
    reference_price,
    equity,
    buying_power,
):
    """
    Last-chance independent order gateway.

    This intentionally duplicates critical checks from sizing.
    A bad caller therefore still cannot submit an oversized entry.
    """

    try:
        qty = int(qty)
        reference_price = float(reference_price)
        equity = float(equity)
        buying_power = float(buying_power)
    except Exception:
        logging.error(
            f"ALPACA SAFETY BLOCK [{symbol}]: "
            "invalid order parameter type"
        )
        return False

    if (
        qty <= 0
        or qty > MAX_ABSOLUTE_SHARES
        or not np.isfinite(reference_price)
        or reference_price < MIN_VALID_PRICE
        or not np.isfinite(equity)
        or equity <= 0
        or not np.isfinite(buying_power)
        or buying_power <= 0
    ):
        logging.error(
            f"ALPACA SAFETY BLOCK [{symbol}]: "
            f"qty={qty} price={reference_price} "
            f"equity={equity} bp={buying_power}"
        )
        return False

    notional = qty * reference_price

    allowed_notional = min(
        equity * MAX_POSITION_PCT,
        buying_power * MAX_BUYING_POWER_PCT,
    )

    if notional > allowed_notional + 0.01:
        logging.error(
            f"ALPACA SAFETY BLOCK [{symbol}]: "
            f"notional=${notional:,.2f} exceeds "
            f"cap=${allowed_notional:,.2f}"
        )
        return False

    return True


def place_alpaca_order(
    symbol,
    side,
    qty,
    reference_price,
    equity,
    buying_power,
):
    # V2.1 DRY-RUN INTERCEPT
    if DRY_RUN:
        allowed = validate_entry_order(
            symbol,
            qty,
            reference_price,
            equity,
            buying_power,
        )

        if not allowed:
            logging.error(
                f"🛑 [V2.1 DRY RUN BLOCKED] {symbol} "
                f"{side} qty={qty} price={reference_price:.2f}"
            )
            return False

        notional = qty * reference_price

        logging.info(
            f"🧪 [V2.1 DRY RUN ORDER] {symbol} | "
            f"side={side} | "
            f"qty={qty} | "
            f"price≈${reference_price:.2f} | "
            f"notional≈${notional:,.2f} | "
            "POST_SUPPRESSED=YES"
        )

        # Return True so the rest of the strategy lifecycle can
        # simulate entry-state behavior without sending an order.
        return True
    if not validate_entry_order(
        symbol,
        qty,
        reference_price,
        equity,
        buying_power,
    ):
        return False

    url = f"{BASE_URL}/v2/orders"
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "buy" if side == "LONG" else "sell",
        "type": "market",
        "time_in_force": "day"
    }
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=8)
        if r.status_code in [200, 201]:
            logging.info(f"⚡ [ALPACA PAPER ORDER] {side} {qty} shares of {symbol}")
            return True
        else:
            logging.error(f"❌ Alpaca Order Rejected [{symbol}]: {r.text}")
    except Exception as e:
        logging.error(f"Exception sending Alpaca order for {symbol}: {e}")
    return False

def close_alpaca_position(symbol, side, qty):
    if DRY_RUN:
        logging.info(
            f"🧪 [V2.1 DRY RUN CLOSE] {symbol} "
            f"{side} qty={qty} | POST_SUPPRESSED=YES"
        )
        return True
    url = f"{BASE_URL}/v2/orders"
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "sell" if side == "LONG" else "buy",
        "type": "market",
        "time_in_force": "day"
    }
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=8)
        if r.status_code in [200, 201]:
            logging.info(f"🔄 [ALPACA CLOSED] {symbol} {side} ({qty} shares)")
            return True
    except Exception as e:
        logging.error(f"Exception closing position for {symbol}: {e}")
    return False

def sync_existing_positions():
    """V2.1 dry-run does not adopt or manage existing Alpaca positions."""

    if DRY_RUN:
        logging.info(
            "🧪 [V2.1 DRY RUN] Existing Alpaca positions "
            "will NOT be adopted or managed."
        )
        return
    global ACTIVE_POSITIONS
    try:
        r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=8)
        if r.status_code == 200:
            positions = r.json()
            for p in positions:
                sym = p["symbol"]
                if sym not in ACTIVE_POSITIONS:
                    side = "LONG" if p["side"] == "long" else "SHORT"
                    qty = abs(int(p["qty"]))
                    entry = float(p["avg_entry_price"])
                    
                    bars = fetch_bars(sym, "5Min", 20)
                    atr = calculate_atr(bars, 14) if bars else max(0.50, entry * 0.01)
                    risk = max(0.20, 1.5 * atr)

                    if side == "LONG":
                        sl = entry - risk
                        tp = entry + (TARGET_RR * risk)
                        be_level = entry + (MOVE_BE_AT * risk)
                        trail_level = entry + (TRAIL_TRIGGER * risk)
                    else:
                        sl = entry + risk
                        tp = entry - (TARGET_RR * risk)
                        be_level = entry - (MOVE_BE_AT * risk)
                        trail_level = entry - (TRAIL_TRIGGER * risk)

                    ACTIVE_POSITIONS[sym] = {
                        "side": side,
                        "entry": entry,
                        "sl": sl,
                        "tp": tp,
                        "be_level": be_level,
                        "trail_level": trail_level,
                        "risk": risk,
                        "qty": qty,
                        "strategy": "ADOPTED_LIVE"
                    }
                    logging.info(f"🛡️ [ADOPTED POSITION] {sym} {side} | Qty: {qty} | Entry: {entry:.2f} | SL: {sl:.2f} | TP: {tp:.2f}")
    except Exception as e:
        logging.error(f"Error syncing existing positions: {e}")

def manage_positions():
    global ACTIVE_POSITIONS, CONSECUTIVE_LOSS_COUNT, CIRCUIT_BREAKER_UNTIL
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
        trail_level = pos["trail_level"]
        risk = pos["risk"]
        qty = pos["qty"]
        strat = pos["strategy"]

        if side == "LONG":
            if high_price >= tp:
                logging.info(f"🟢 [{sym}] [{strat}] TARGET HIT @ {tp:.2f} (+{TARGET_RR}R Profit)")
                close_alpaca_position(sym, side, qty)
                symbols_to_remove.append(sym)
                CONSECUTIVE_LOSS_COUNT = 0  # Reset loss streak
            elif low_price <= sl:
                pnl_r = (sl - entry) / risk
                logging.info(f"🔴 [{sym}] [{strat}] STOP HIT @ {sl:.2f} ({pnl_r:+.2f}R PnL)")
                close_alpaca_position(sym, side, qty)
                symbols_to_remove.append(sym)
                CONSECUTIVE_LOSS_COUNT += 1
                if CONSECUTIVE_LOSS_COUNT >= MAX_CONSECUTIVE_LOSSES:
                    CIRCUIT_BREAKER_UNTIL = time.time() + CIRCUIT_BREAKER_FREEZE
                    logging.warning(f"🚨 [CIRCUIT BREAKER TRIGGERED] {CONSECUTIVE_LOSS_COUNT} Consecutive Losses! System frozen for 30 minutes.")
            elif high_price >= trail_level and sl < (entry + TRAIL_LOCK_OFFSET * risk):
                pos["sl"] = entry + (TRAIL_LOCK_OFFSET * risk)
                logging.info(f"📈 [{sym}] [{strat}] TRAILING STOP RATCHETED: SL locked at {pos['sl']:.2f} (+{TRAIL_LOCK_OFFSET}R)")
            elif high_price >= be_level and sl < entry:
                pos["sl"] = entry
                logging.info(f"🛡️ [{sym}] [{strat}] BREAKEVEN TRIGGERED: SL moved to {entry:.2f}")

        elif side == "SHORT":
            if low_price <= tp:
                logging.info(f"🟢 [{sym}] [{strat}] SHORT TARGET HIT @ {tp:.2f} (+{TARGET_RR}R Profit)")
                close_alpaca_position(sym, side, qty)
                symbols_to_remove.append(sym)
                CONSECUTIVE_LOSS_COUNT = 0  # Reset loss streak
            elif high_price >= sl:
                pnl_r = (entry - sl) / risk
                logging.info(f"🔴 [{sym}] [{strat}] SHORT STOP HIT @ {sl:.2f} ({pnl_r:+.2f}R PnL)")
                close_alpaca_position(sym, side, qty)
                symbols_to_remove.append(sym)
                CONSECUTIVE_LOSS_COUNT += 1
                if CONSECUTIVE_LOSS_COUNT >= MAX_CONSECUTIVE_LOSSES:
                    CIRCUIT_BREAKER_UNTIL = time.time() + CIRCUIT_BREAKER_FREEZE
                    logging.warning(f"🚨 [CIRCUIT BREAKER TRIGGERED] {CONSECUTIVE_LOSS_COUNT} Consecutive Losses! System frozen for 30 minutes.")
            elif low_price <= trail_level and sl > (entry - TRAIL_LOCK_OFFSET * risk):
                pos["sl"] = entry - (TRAIL_LOCK_OFFSET * risk)
                logging.info(f"📈 [{sym}] [{strat}] SHORT TRAILING STOP RATCHETED: SL locked at {pos['sl']:.2f} (+{TRAIL_LOCK_OFFSET}R)")
            elif low_price <= be_level and sl > entry:
                pos["sl"] = entry
                logging.info(f"🛡️ [{sym}] [{strat}] SHORT BREAKEVEN TRIGGERED: SL moved to {entry:.2f}")

    for s in symbols_to_remove:
        ACTIVE_POSITIONS.pop(s, None)

def scan_sector_universe():
    global LAST_ENTRY_TIME
    now_utc = datetime.now(timezone.utc)
    hour_edt = (now_utc.hour - 4) + (now_utc.minute / 60.0)
    if not (10.0 <= hour_edt <= 15.75):
        return

    # 1. Check Circuit Breaker Freeze (3 Consecutive Losses)
    if time.time() < CIRCUIT_BREAKER_UNTIL:
        remaining_min = (CIRCUIT_BREAKER_UNTIL - time.time()) / 60.0
        logging.info(f"⏸️ [CIRCUIT BREAKER ACTIVE] 3 Consecutive Losses. Resuming in {remaining_min:.1f} minutes.")
        return

    # 2. Concurrency Lock: Cap at MAX_OPEN_POSITIONS (10)
    if len(ACTIVE_POSITIONS) >= MAX_OPEN_POSITIONS:
        return

    # 3. 15-Minute Staggered Entry Cooldown Lock
    time_since_last = time.time() - LAST_ENTRY_TIME
    if time_since_last < ENTRY_COOLDOWN_SEC:
        return

    account_snapshot = get_alpaca_account_snapshot()

    if account_snapshot is None:
        logging.warning(
            "ALPACA SAFETY: no valid account snapshot; "
            "new entries blocked for this cycle."
        )
        return

    current_equity = account_snapshot["equity"]
    current_buying_power = account_snapshot["buying_power"]

    # Calculate current sector counts
    sector_counts = {}
    for sym in ACTIVE_POSITIONS:
        sec = TICKER_TO_SECTOR.get(sym, "OTHER")
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    for sym in WATCHLIST:
        if len(ACTIVE_POSITIONS) >= MAX_OPEN_POSITIONS:
            break

        if sym in ACTIVE_POSITIONS:
            continue

        sym_sector = TICKER_TO_SECTOR.get(sym, "OTHER")
        if sector_counts.get(sym_sector, 0) >= MAX_PER_SECTOR:
            continue

        bars = fetch_bars(sym, "5Min", 60)
        if len(bars) < 50:
            continue

        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]

        price = closes[-1]
        sma20 = np.mean(closes[-20:])
        sma50 = np.mean(closes[-50:])
        atr = calculate_atr(bars[-20:], period=14)
        rsi2 = calculate_rsi(closes[-10:], period=2)

        prev_ranges = [highs[k] - lows[k] for k in range(-4, -1)]
        is_coiled = np.mean(prev_ranges) <= (1.1 * atr)

        signal_found = False
        side, sl, tp, be_level, trail_level, risk, strat = None, None, None, None, None, None, None

        # 1. MINERVINI VCP
        if is_coiled and (price > sma50) and (rsi2 <= 12.0) and (closes[-1] > opens[-1]):
            side = "LONG"
            strat = "MINERVINI_VCP"
            sl = min(lows[-3:]) - (0.05 * atr)
            risk = price - sl
            if risk > 0:
                tp = price + (TARGET_RR * risk)
                be_level = price + (MOVE_BE_AT * risk)
                trail_level = price + (TRAIL_TRIGGER * risk)
                signal_found = True

        elif is_coiled and (price < sma50) and (rsi2 >= 88.0) and (closes[-1] < opens[-1]):
            side = "SHORT"
            strat = "MINERVINI_VCP"
            sl = max(highs[-3:]) + (0.05 * atr)
            risk = sl - price
            if risk > 0:
                tp = price - (TARGET_RR * risk)
                be_level = price - (MOVE_BE_AT * risk)
                trail_level = price - (TRAIL_TRIGGER * risk)
                signal_found = True

        # 2. CONNORS RSI(2) TREND
        if not signal_found:
            if (price > sma50) and (sma20 > sma50) and (rsi2 <= 10.0):
                side = "LONG"
                strat = "CONNORS_RSI2_TREND"
                sl = min(lows[-2:]) - (0.05 * atr)
                risk = price - sl
                if risk > 0:
                    tp = price + (TARGET_RR * risk)
                    be_level = price + (MOVE_BE_AT * risk)
                    trail_level = price + (TRAIL_TRIGGER * risk)
                    signal_found = True

            elif (price < sma50) and (sma20 < sma50) and (rsi2 >= 90.0):
                side = "SHORT"
                strat = "CONNORS_RSI2_TREND"
                sl = max(highs[-2:]) + (0.05 * atr)
                risk = sl - price
                if risk > 0:
                    tp = price - (TARGET_RR * risk)
                    be_level = price - (MOVE_BE_AT * risk)
                    trail_level = price - (TRAIL_TRIGGER * risk)
                    signal_found = True

        # Order Entry -- V2 guarded sizing
        if signal_found and risk > 0:

            qty, sizing = calculate_safe_qty(
                price=price,
                risk_per_share=risk,
                equity=current_equity,
                buying_power=current_buying_power,
                atr=atr,
            )

            if qty < 1:
                logging.info(
                    # --- Auto-Floor Sizing Bypass ---
                    # Bypassed: f"🛑 [SIZING REJECTED] {sym} "
                    f"{side} via {strat} | "
                    f"reason={sizing.get('reason')} | "
                    f"price={price:.2f} "
                    f"risk/share={risk:.4f} "
                    f"ATR={atr:.4f}"
                )
                continue

            logging.info(
                f"🛡️ [SAFE SIZING] {sym} | "
                f"qty={qty} | "
                f"notional=${sizing['actual_notional']:,.2f} | "
                f"dollar_risk=${sizing['actual_dollar_risk']:,.2f} | "
                f"notional_cap=${sizing['allowed_notional']:,.2f}"
            )

            success = place_alpaca_order(
                sym,
                side,
                qty,
                price,
                current_equity,
                current_buying_power,
            )
            if success:
                ACTIVE_POSITIONS[sym] = {
                    "side": side,
                    "entry": price,
                    "sl": sl,
                    "tp": tp,
                    "be_level": be_level,
                    "trail_level": trail_level,
                    "risk": risk,
                    "qty": qty,
                    "strategy": strat
                }
                LAST_ENTRY_TIME = time.time()
                sector_counts[sym_sector] = sector_counts.get(sym_sector, 0) + 1
                logging.info(f"🧪 [{sym}] {side} SIMULATED ENTRY via {strat} | Qty: {qty} | Entry: {price:.2f} | SL: {sl:.2f} | TP: {tp:.2f} [Sim Slots: {len(ACTIVE_POSITIONS)}/10]")
                break

def main():
    logging.info("=" * 95)
    logging.info("🧪 ALPACA MULTI-SCALPER V2.1 DRY-RUN VALIDATION")
    logging.info("🧪 REAL DATA / REAL PAPER ACCOUNT SNAPSHOT / ZERO ORDER POSTS")
    logging.info("🧪 ENTRY POST SUPPRESSED: YES")
    logging.info("🧪 EXIT POST SUPPRESSED: YES")
    logging.info("=" * 95)
    logging.info("=" * 95)
    logging.info("🏛️ ALPACA ARMORED SCALPER: 15-MIN DELAY | 3-LOSS FREEZE | 10-SLOT CAP | 1% EQUITY SIZING")
    logging.info("=" * 95)

    sync_existing_positions()

    while True:
        try:
            manage_positions()
            scan_sector_universe()
        except Exception as e:
            logging.error(f"Alpaca Daemon loop error: {e}")
        time.sleep(20)

if __name__ == "__main__":
    main()
