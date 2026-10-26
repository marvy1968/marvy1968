#!/usr/bin/env python3
import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import v20

load_dotenv("/home/moonrider1968/bots/.env")

OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_HOST = "api-fxpractice.oanda.com"
NY_TZ = ZoneInfo("America/New_York")

# Risk Settings
RISK_PCT_PER_TRADE = 0.0050  # 0.50% NAV Risk per trade
MAX_UNITS_CAP = 250000       # Hard ceiling (2.5 standard lots)

CONFIGS = {
    "GBP_USD": {"granularity": "M15"},
    "EUR_USD": {"granularity": "H1"}
}

ctx = v20.Context(OANDA_HOST, 443, ssl=True, token=OANDA_API_KEY)

def get_account_nav() -> float:
    try:
        resp = ctx.account.get(OANDA_ACCOUNT_ID)
        if resp.status == 200:
            return float(resp.body.get("account").NAV)
    except Exception as e:
        print(f"[!] Error fetching NAV: {e}")
    return 50000.0

def calculate_dynamic_units(nav: float, stop_dist_price: float) -> int:
    if stop_dist_price <= 0:
        return 10000
    risk_dollars = nav * RISK_PCT_PER_TRADE
    units = int(risk_dollars / stop_dist_price)
    return max(1000, min(units, MAX_UNITS_CAP))

def fetch_data(pair, granularity, count=100):
    try:
        resp = ctx.instrument.candles(pair, granularity=granularity, count=count, price="M")
        if resp.status != 200:
            return None
        records = []
        for c in resp.body.get('candles', []):
            if not c.complete:
                continue
            records.append({
                "time": c.time,
                "open": float(c.mid.o),
                "high": float(c.mid.h),
                "low": float(c.mid.l),
                "close": float(c.mid.c)
            })
        df = pd.DataFrame(records)
        if len(df) < 50:
            return None
        
        close, high, low = df['close'], df['high'], df['low']

        # ATR 14
        tr = np.maximum(high - low, np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()))
        df['atr14'] = tr.rolling(14).mean()

        # ADX Filter
        up = high - high.shift(1)
        down = low.shift(1) - low
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        plus_di = 100 * (pd.Series(plus_dm).rolling(14).mean() / df['atr14'])
        minus_di = 100 * (pd.Series(minus_dm).rolling(14).mean() / df['atr14'])
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan))
        df['adx'] = dx.rolling(14).mean()

        # Core Reversion Metrics
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        rs = gain.rolling(14).mean() / loss.rolling(14).mean().replace(0, np.nan)
        df['rsi'] = 100 - (100 / (1 + rs))

        df['bb_mid'] = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        df['bb_lower'] = df['bb_mid'] - (2 * bb_std)
        df['bb_upper'] = df['bb_mid'] + (2 * bb_std)

        low14 = low.rolling(14).min()
        high14 = high.rolling(14).max()
        df['stoch_k'] = 100 * ((close - low14) / (high14 - low14))
        df['stoch_d'] = df['stoch_k'].rolling(3).mean()
        df['zscore'] = (close - df['bb_mid']) / bb_std

        tp = (high + low + close) / 3
        mad = (tp - tp.rolling(20).mean()).abs().rolling(20).mean()
        df['cci'] = (tp - tp.rolling(20).mean()) / (0.015 * mad)

        # 3/5 Consensus Entry
        b1 = df['rsi'] < 32
        b2 = close <= df['bb_lower']
        b3 = (df['stoch_k'] < 25) & (df['stoch_k'] > df['stoch_d'])
        b4 = df['zscore'] < -1.8
        b5 = df['cci'] < -90
        df['buy_votes'] = b1.astype(int) + b2.astype(int) + b3.astype(int) + b4.astype(int) + b5.astype(int)

        s1 = df['rsi'] > 68
        s2 = close >= df['bb_upper']
        s3 = (df['stoch_k'] > 75) & (df['stoch_k'] < df['stoch_d'])
        s4 = df['zscore'] > 1.8
        s5 = df['cci'] > 90
        df['short_votes'] = s1.astype(int) + s2.astype(int) + s3.astype(int) + s4.astype(int) + s5.astype(int)

        # 2/3 Exit Consensus
        lx1 = df['rsi'] > 50
        lx2 = close >= df['bb_mid']
        lx3 = df['stoch_k'] > 65
        df['long_exit'] = (lx1.astype(int) + lx2.astype(int) + lx3.astype(int)) >= 2

        sx1 = df['rsi'] < 50
        sx2 = close <= df['bb_mid']
        sx3 = df['stoch_k'] < 35
        df['short_exit'] = (sx1.astype(int) + sx2.astype(int) + sx3.astype(int)) >= 2

        return df
    except Exception as e:
        print(f"[!] Error processing {pair}: {e}")
        return None

def get_open_positions():
    try:
        resp = ctx.position.list_open(OANDA_ACCOUNT_ID)
        if resp.status != 200:
            return {}
        pos_map = {}
        for p in resp.body.get("positions", []):
            inst = p.instrument
            long_units = int(p.long.units)
            short_units = int(p.short.units)
            if long_units > 0:
                pos_map[inst] = {"side": "LONG", "units": long_units}
            elif short_units < 0:
                pos_map[inst] = {"side": "SHORT", "units": abs(short_units)}
        return pos_map
    except Exception as e:
        print(f"[!] Error fetching positions: {e}")
        return {}

def scan_and_execute():
    now_str = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    open_pos = get_open_positions()
    current_nav = get_account_nav()

    print("=" * 86)
    print(f"[{now_str}] RUNNING OANDA CONSENSUS SCAN | NAV: ${current_nav:,.2f} | Risk/Trade: 0.50%")
    print(f"{'PAIR':<9} | {'TF':<4} | {'CLOSE':>8} | {'ADX':>5} | {'BUY/5':>5} | {'SHORT/5':>7} | {'POSITION':<8} | {'ACTION'}")
    print("-" * 86)

    for pair, cfg in CONFIGS.items():
        df = fetch_data(pair, cfg["granularity"])
        if df is None:
            continue
        
        last = df.iloc[-1]
        adx = float(last['adx'])
        close = float(last['close'])
        atr = float(last['atr14'])
        buy_v = int(last['buy_votes'])
        short_v = int(last['short_votes'])
        tf = cfg['granularity']

        current_pos = open_pos.get(pair, {}).get("side", "FLAT")
        action_taken = "NO SETUP"

        # 1. Position Exits
        if current_pos == "LONG" and last['long_exit']:
            action_taken = "EXIT LONG (2/3 met)"
            ctx.position.close(OANDA_ACCOUNT_ID, pair, longUnits="ALL")
        elif current_pos == "SHORT" and last['short_exit']:
            action_taken = "EXIT SHORT (2/3 met)"
            ctx.position.close(OANDA_ACCOUNT_ID, pair, shortUnits="ALL")

        # 2. Entries (only when flat)
        elif current_pos == "FLAT":
            stop_distance = 1.75 * atr
            units = calculate_dynamic_units(current_nav, stop_distance)

            if adx < 32:
                if buy_v >= 3:
                    sl_px = round(close - stop_distance, 5)
                    action_taken = f"BUY {units:,} @ {close:.5f} (SL: {sl_px:.5f})"
                    ctx.order.market(
                        OANDA_ACCOUNT_ID,
                        order={
                            "instrument": pair,
                            "units": str(units),
                            "timeInForce": "FOK",
                            "type": "MARKET",
                            "positionFill": "DEFAULT",
                            "stopLossOnFill": {"price": f"{sl_px:.5f}", "timeInForce": "GTC"}
                        }
                    )
                elif short_v >= 3:
                    sl_px = round(close + stop_distance, 5)
                    action_taken = f"SELL {-units:,} @ {close:.5f} (SL: {sl_px:.5f})"
                    ctx.order.market(
                        OANDA_ACCOUNT_ID,
                        order={
                            "instrument": pair,
                            "units": str(-units),
                            "timeInForce": "FOK",
                            "type": "MARKET",
                            "positionFill": "DEFAULT",
                            "stopLossOnFill": {"price": f"{sl_px:.5f}", "timeInForce": "GTC"}
                        }
                    )
            else:
                action_taken = f"VETO (ADX {adx:.1f} >= 32)"

        print(f"{pair:<9} | {tf:<4} | {close:>8.5f} | {adx:>5.1f} | {buy_v:>5} | {short_v:>7} | {current_pos:<8} | {action_taken}")
    print("=" * 86)

if __name__ == "__main__":
    scan_and_execute()
