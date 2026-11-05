#!/usr/bin/env python3
"""
heatmap_news_bot_standalone.py (Fixed bar date format)
"""

import os
import sys
import csv
import time
import logging
import traceback
from datetime import datetime, timedelta

import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame

# ===========================================================================
# CONFIG
# ===========================================================================

API_KEY    = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL   = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")

RISK_PER_TRADE_PCT              = 0.01    
MAX_POSITIONS                   = 20      
MAX_PORTFOLIO_PCT               = 0.20    
ATR_PERIOD                      = 14
ATR_STOP_MULT                   = 1.0     
ATR_TARGET_MULT                 = 1.5     
NO_NEW_ENTRY_MIN_BEFORE_CLOSE   = 45      
CLOSE_POSITIONS_MIN_BEFORE_CLOSE = 10     
SCAN_INTERVAL_SEC               = 900     

LOG_PATH    = "/home/moonrider1968/bots/logs/heatmap_bot.log"
TRADES_PATH = "/home/moonrider1968/bots/logs/heatmap_trades.csv"

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AMZN", "META", "TSLA",
    "JPM", "BAC", "XOM", "CVX", "UNH", "JNJ", "HD", "NKE",
]

SECTOR_ETFS = {
    "XLK":  ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "META"],
    "XLF":  ["JPM", "BAC", "GS", "MS", "AXP"],
    "XLE":  ["XOM", "CVX", "COP", "SLB", "EOG"],
    "XLV":  ["UNH", "JNJ", "PFE", "ABBV", "MRK"],
    "XLY":  ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "XLI":  ["GE", "CAT", "HON", "BA", "UPS"],
}

SYMBOL_TO_ETF = {
    sym: etf
    for etf, symbols in SECTOR_ETFS.items()
    for sym in symbols
}

NEGATIVE_KW = [
    "lawsuit", "investigation", "fraud", "recall", "downgrade", "misses",
    "layoffs", "bankruptcy", "sec probe", "data breach", "warning",
    "subpoena", "criminal", "settlement", "default", "suspended",
]
POSITIVE_KW = [
    "upgrade", "beats", "expansion", "partnership", "record", "surge",
    "acquisition", "buyback", "dividend", "raised guidance", "new contract",
    "fda approval", "breakthrough", "outperform", "strong earnings",
]

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("heatmap_bot")

def get_api():
    return REST(API_KEY, API_SECRET, BASE_URL)

def is_market_open(api):
    try:
        return api.get_clock().is_open
    except Exception as e:
        log.warning(f"Error checking market clock: {e}")
        return False

def minutes_to_close(api):
    try:
        clock = api.get_clock()
        now   = pd.Timestamp.now(tz="America/New_York")
        return int((pd.Timestamp(clock.next_close) - now).total_seconds() / 60)
    except Exception:
        return 999

def get_account(api):
    a = api.get_account()
    return {
        "equity":       float(a.equity),
        "cash":         float(a.cash),
        "buying_power": float(a.buying_power),
    }

def get_positions(api):
    return {p.symbol: float(p.market_value) for p in api.list_positions()}

def fetch_bars(api, symbol, timeframe, days=10):
    try:
        end   = datetime.now() - timedelta(minutes=15)
        start = end - timedelta(days=days)
        # Fixed format: Alpaca expects YYYY-MM-DD for start/end parameters
        bars  = api.get_bars(
            symbol, timeframe,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            adjustment="raw",
            feed="iex",
        ).df
        return bars if not bars.empty else pd.DataFrame()
    except Exception as e:
        log.warning(f"fetch_bars {symbol}: {e}")
        return pd.DataFrame()

def calc_atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - pc).abs(),
        (df["low"]  - pc).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

def calc_qty(equity, entry, stop):
    risk     = equity * RISK_PER_TRADE_PCT
    distance = entry - stop
    if distance <= 0:
        return 0
    qty_risk = risk / distance
    qty_cap  = (equity * MAX_PORTFOLIO_PCT / MAX_POSITIONS) / entry
    return max(int(min(qty_risk, qty_cap)), 1)

def place_order(api, symbol, qty, stop, tp):
    try:
        order = api.submit_order(
            symbol=symbol,
            qty=qty,
            side="buy",
            type="market",
            time_in_force="day",
            order_class="bracket",
            stop_loss={"stop_price": round(stop, 2)},
            take_profit={"limit_price": round(tp, 2)},
        )
        log.info(f"ORDER PLACED: {symbol} qty={qty} stop=${stop:.2f} tp=${tp:.2f} id={order.id}")
        write_trade_log(symbol, qty, stop, tp)
        return order
    except Exception as e:
        log.error(f"Order failed {symbol}: {e}")
        return None

def write_trade_log(symbol, qty, stop, tp):
    write_header = not os.path.exists(TRADES_PATH)
    with open(TRADES_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp", "symbol", "qty", "stop", "take_profit"])
        w.writerow([datetime.now().isoformat(), symbol, qty, stop, tp])

def cancel_all_orders(api):
    try:
        api.cancel_all_orders()
        log.info("All orders cancelled.")
    except Exception as e:
        log.error(f"Cancel orders: {e}")

def liquidate_all(api):
    for p in api.list_positions():
        try:
            api.submit_order(symbol=p.symbol, qty=float(p.qty), side="sell", type="market", time_in_force="day")
            log.info(f"Liquidated {p.symbol}")
        except Exception as e:
            log.error(f"Liquidate {p.symbol}: {e}")

def get_hot_sectors(api):
    hot = set()
    end   = datetime.now() - timedelta(minutes=15)
    start = end - timedelta(days=8)

    log.info("Checking sector heatmap...")
    for etf in SECTOR_ETFS:
        try:
            bars = api.get_bars(
                etf, TimeFrame.Day,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                adjustment="raw",
                feed="iex",
            ).df
            if len(bars) < 5:
                continue
            perf = (bars["close"].iloc[-1] - bars["close"].iloc[-5]) / bars["close"].iloc[-5]
            tag  = "HOT  +" if perf > 0 else "cold  "
            log.info(f"  {etf}: {tag}{perf*100:.2f}%")
            if perf > 0:
                hot.add(etf)
        except Exception as e:
            log.warning(f"  {etf}: fetch failed — {e}")

    log.info(f"Hot sectors: {hot or 'none'}")
    return hot

def news_sentiment_ok(api, symbol):
    try:
        news = api.get_news(symbol, limit=5)
        if not news:
            log.info(f"  {symbol}: no recent news headlines (neutral pass)")
            return True

        score = 0
        for item in news:
            headline = getattr(item, "headline", "").lower() if not isinstance(item, dict) else item.get("headline", "").lower()
            score += sum(1 for kw in POSITIVE_KW if kw in headline)
            score -= sum(1 for kw in NEGATIVE_KW if kw in headline)

        ok = score >= 0
        log.info(f"  {symbol}: {len(news)} headlines evaluated. score={score:+d}  -> {'OK' if ok else 'SKIP'}")
        return ok
    except Exception as e:
        log.warning(f"  {symbol}: news fetch skipped/failed ({e}) — defaulting to pass")
        return True

def momentum_ok(df_hourly):
    if len(df_hourly) < 21:
        return False
    sma20 = df_hourly["close"].rolling(20).mean().iloc[-1]
    price = df_hourly["close"].iloc[-1]
    return price > sma20

def run_scan(api):
    log.info("--- Scan start ---")

    if minutes_to_close(api) < NO_NEW_ENTRY_MIN_BEFORE_CLOSE:
        log.info("< 45 min to close — no new entries.")
        return

    acct      = get_account(api)
    equity    = acct["equity"]
    positions = get_positions(api)
    n_open    = len(positions)
    deployed  = sum(positions.values())

    log.info(f"Equity=${equity:,.2f}  Positions={n_open}/{MAX_POSITIONS}")

    if n_open >= MAX_POSITIONS:
        log.info("Max positions reached — skipping.")
        return

    hot_sectors = get_hot_sectors(api)
    if not hot_sectors:
        log.info("No hot sectors today — skipping scan.")
        return

    orders_placed = 0

    for symbol in WATCHLIST:
        if symbol in positions:
            continue

        etf = SYMBOL_TO_ETF.get(symbol)
        if etf is None or etf not in hot_sectors:
            continue

        if not news_sentiment_ok(api, symbol):
            continue

        df_h = fetch_bars(api, symbol, TimeFrame.Hour, days=5)
        if df_h.empty or not momentum_ok(df_h):
            log.info(f"  {symbol}: below 20 SMA or empty bars — skip")
            continue

        df_d = fetch_bars(api, symbol, TimeFrame.Day, days=30)
        if df_d.empty or len(df_d) < 15:
            continue

        cur_atr = calc_atr(df_d, ATR_PERIOD).iloc[-1]
        entry   = float(df_h["close"].iloc[-1])
        stop    = round(entry - ATR_STOP_MULT   * cur_atr, 2)
        tp      = round(entry + ATR_TARGET_MULT * cur_atr, 2)
        qty     = calc_qty(equity, entry, stop)

        if qty <= 0 or (deployed + qty * entry) / equity > MAX_PORTFOLIO_PCT:
            continue

        log.info(f"SIGNAL {symbol}: sector={etf} entry=${entry:.2f} stop=${stop:.2f} tp=${tp:.2f} qty={qty}")

        order = place_order(api, symbol, qty, stop, tp)
        if order:
            deployed      += qty * entry
            orders_placed += 1

        if n_open + orders_placed >= MAX_POSITIONS:
            break

    log.info(f"--- Scan done: {orders_placed} orders placed ---\n")

def run_eod(api):
    log.info("=== EOD: cancelling orders and liquidating positions ===")
    cancel_all_orders(api)
    liquidate_all(api)

def main():
    log.info("=" * 60)
    log.info("HEATMAP & NEWS SENTIMENT BOT (STANDALONE)")
    log.info(f"URL: {BASE_URL}")
    log.info("=" * 60)

    if not API_KEY or not API_SECRET:
        log.error("Missing APCA_API_KEY_ID or APCA_API_SECRET_KEY. Exiting.")
        sys.exit(1)

    api              = get_api()
    eod_done_date    = None
    eod_done         = False
    consecutive_errs = 0

    while True:
        try:
            today = datetime.now().date()
            if today != eod_done_date:
                eod_done_date = today
                eod_done      = False

            if not is_market_open(api):
                log.info("Market closed — sleeping 60s.")
                time.sleep(60)
                consecutive_errs = 0
                continue

            mtc = minutes_to_close(api)
            if mtc <= CLOSE_POSITIONS_MIN_BEFORE_CLOSE and not eod_done:
                run_eod(api)
                eod_done = True
                time.sleep(60)
                continue

            run_scan(api)
            consecutive_errs = 0
            time.sleep(SCAN_INTERVAL_SEC)

        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break

        except Exception as e:
            consecutive_errs += 1
            log.error(f"Loop error #{consecutive_errs}: {e}")
            log.debug(traceback.format_exc())
            sleep = min(60 * (2 ** consecutive_errs), 600)
            log.info(f"Backing off {sleep}s...")
            time.sleep(sleep)
            if consecutive_errs >= 10:
                sys.exit(1)

if __name__ == "__main__":
    main()
