

# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

CRYPTO_4H_OPTIMIZATION_MATRIX = {
    "BTC/USD":  {"RANGE_BREAKOUT": {"sl_pct": 0.015, "tp_pct": 0.035}, "VELEZ_20EMA": {"sl_pct": 0.012, "tp_pct": 0.030}},
    "ETH/USD":  {"RANGE_BREAKOUT": {"sl_pct": 0.020, "tp_pct": 0.045}, "VELEZ_20EMA": {"sl_pct": 0.015, "tp_pct": 0.035}},
    "SOL/USD":  {"RANGE_BREAKOUT": {"sl_pct": 0.025, "tp_pct": 0.055}, "VELEZ_20EMA": {"sl_pct": 0.020, "tp_pct": 0.045}},
    "DOGE/USD": {"RANGE_BREAKOUT": {"sl_pct": 0.030, "tp_pct": 0.065}, "VELEZ_20EMA": {"sl_pct": 0.025, "tp_pct": 0.050}},
    "LINK/USD": {"RANGE_BREAKOUT": {"sl_pct": 0.025, "tp_pct": 0.055}, "VELEZ_20EMA": {"sl_pct": 0.020, "tp_pct": 0.040}},
}

import os
import sys
import time
import json
import requests
from datetime import datetime, timezone

# --- ALPACA PAPER CONFIGURATION ---
ALPACA_API_KEY = "REDACTED"
ALPACA_SECRET_KEY = "REDACTED"
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json"
}

# Dynamic MFE/MAE Parameter Matrix compiled from empirical shadow telemetry
STOCK_OPTIMIZATION_MATRIX = {
    "TSLA": {
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0004,
            "tp_pct": 0.0005
        },
        "VELEZ_20EMA": {
            "sl_pct": 0.0003,
            "tp_pct": 0.0003
        }
    },
    "AMD": {
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0005,
            "tp_pct": 0.0006
        },
        "VELEZ_20EMA": {
            "sl_pct": 0.0004,
            "tp_pct": 0.0004
        }
    },
    "NVDA": {
        "MICRO_MOMENTUM": {
            "sl_pct": 0.0006,
            "tp_pct": 0.0006
        },
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0004,
            "tp_pct": 0.0005
        }
    },
    "AAPL": {
        "VELEZ_20EMA": {
            "sl_pct": 0.0002,
            "tp_pct": 0.0002
        },
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0002,
            "tp_pct": 0.0002
        }
    },
    "MSFT": {
        "MICRO_MOMENTUM": {
            "sl_pct": 0.0003,
            "tp_pct": 0.0004
        },
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0003,
            "tp_pct": 0.0004
        }
    },
    "AMZN": {
        "MICRO_MOMENTUM": {
            "sl_pct": 0.0004,
            "tp_pct": 0.0005
        },
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0003,
            "tp_pct": 0.0003
        }
    },
    "SPY": {
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0001,
            "tp_pct": 0.0001
        },
        "VELEZ_20EMA": {
            "sl_pct": 0.0001,
            "tp_pct": 0.0001
        }
    },
    "QQQ": {
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0001,
            "tp_pct": 0.0001
        },
        "VOL_SQUEEZE": {
            "sl_pct": 0.0002,
            "tp_pct": 0.0002
        }
    },
    "META": {
        "MICRO_MOMENTUM": {
            "sl_pct": 0.0004,
            "tp_pct": 0.0005
        },
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0003,
            "tp_pct": 0.0004
        }
    },
    "NFLX": {
        "MICRO_MOMENTUM": {
            "sl_pct": 0.0005,
            "tp_pct": 0.0006
        },
        "RANGE_BREAKOUT": {
            "sl_pct": 0.0004,
            "tp_pct": 0.0005
        }
    }
}

KELLY_RISK_MATRIX = {
    ("AAPL", "MICRO_MOMENTUM", "BUY"): 0.02,
    ("AAPL", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("AAPL", "VOL_SQUEEZE", "BUY"): 0.025,
    ("AAPL", "VOL_SQUEEZE", "SELL"): 0.025,
    ("AMD", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("AMD", "VELEZ_20EMA", "SELL"): 0.025,
    ("AMZN", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("AMZN", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("AMZN", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("META", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("META", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("META", "RANGE_BREAKOUT", "BUY"): 0.02,
    ("MSFT", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("MSFT", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("MSFT", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("MSFT", "VOL_SQUEEZE", "SELL"): 0.025,
    ("NFLX", "MICRO_MOMENTUM", "BUY"): 0.015,
    ("NFLX", "RANGE_BREAKOUT", "BUY"): 0.015,
    ("NVDA", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("NVDA", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("NVDA", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("NVDA", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("QQQ", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("QQQ", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("QQQ", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("QQQ", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("QQQ", "VOL_SQUEEZE", "BUY"): 0.025,
    ("QQQ", "VOL_SQUEEZE", "SELL"): 0.025,
    ("SPY", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("SPY", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("SPY", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("SPY", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("SPY", "VELEZ_20EMA", "BUY"): 0.025,
    ("SPY", "VELEZ_20EMA", "SELL"): 0.025,
    ("SPY", "VOL_SQUEEZE", "BUY"): 0.025,
    ("SPY", "VOL_SQUEEZE", "SELL"): 0.025,
    ("TSLA", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("TSLA", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("TSLA", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("TSLA", "RANGE_BREAKOUT", "SELL"): 0.025,
}

LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/logs/alpaca_paper_execution.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log_event(msg):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def get_account_equity():
    try:
        r = requests.get(f"{ALPACA_PAPER_URL}/v2/account", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            return float(r.json().get('equity', 100000.0))
        else:
            log_event(f"Account query failed: {r.status_code} - {r.text}")
    except Exception as e:
        log_event(f"Error fetching account equity: {e}")
    return 100000.0

def get_dynamic_risk_pct(ticker, strategy, side):
    return KELLY_RISK_MATRIX.get((ticker, strategy, side.upper()), 0.005)

def calculate_bracket_params(ticker, strategy, price, side):
    rules = STOCK_OPTIMIZATION_MATRIX.get(ticker, {}).get(
        strategy, {"sl_pct": 0.0005, "tp_pct": 0.0006}
    )
    sl_pct = max(rules["sl_pct"], 0.0005) # Enforce 0.05% minimum to clear bid/ask spread
    tp_pct = max(rules["tp_pct"], 0.0005)

    sl_dist = max(round(price * sl_pct, 2), 0.05) # Minimum $0.05 stop offset
    tp_dist = max(round(price * tp_pct, 2), 0.05)

    if side.upper() == "BUY":
        stop_price = round(price - sl_dist, 2)
        limit_price = round(price + tp_dist, 2)
    else:
        stop_price = round(price + sl_dist, 2)
        limit_price = round(price - tp_dist, 2)

    return stop_price, limit_price, sl_pct, tp_pct

def submit_alpaca_bracket_order(ticker, strategy, side, current_price):
    equity = get_account_equity()
    risk_pct = get_dynamic_risk_pct(ticker, strategy, side)
    
    stop_price, limit_price, sl_pct, tp_pct = calculate_bracket_params(ticker, strategy, current_price, side)
    risk_dollars = equity * risk_pct
    price_risk = abs(current_price - stop_price)
    
    # Calculate raw share quantity based on Kelly risk
    raw_qty = int(risk_dollars / price_risk) if price_risk > 0 else 1
    
    # Safety Cap: Never exceed 25% of total equity per single scalp trade
    max_notional = equity * 0.25
    max_qty = max(int(max_notional / current_price), 1)
    
    qty = max(min(raw_qty, max_qty), 1)

    order_payload = {
        "symbol": ticker,
        "qty": qty,
        "side": side.lower(),
        "type": "market",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {"limit_price": str(limit_price)},
        "stop_loss": {"stop_price": str(stop_price)}
    }

    try:
        url = f"{ALPACA_PAPER_URL}/v2/orders"
        r = requests.post(url, headers=HEADERS, json=order_payload, timeout=5)
        if r.status_code in [200, 201]:
            data = r.json()
            log_event(f"✅ [DYNAMIC KELLY EXECUTION] {ticker} | {side.upper()} {qty} shares (~${qty*current_price:,.2f} Notional) @ ~${current_price:.2f} | "
                      f"SL: ${stop_price:.2f} | TP: ${limit_price:.2f} | "
                      f"Order ID: {data.get('id')}")
            return True
        else:
            log_event(f"❌ [ALPACA REJECTED] {ticker}: {r.status_code} - {r.text}")
    except Exception as e:
        log_event(f"Execution Exception for {ticker}: {e}")
    return False
