#!/usr/bin/env python3
"""
EQUITIES 15-EXPERT INTRADAY TOURNAMENT ENGINE (TYPE-STRICT & OPTIMIZED)
Partition: Development Set (2017-01-03 to 2022-12-30)
Dataset: Canonical 1-Min SIP Parquet Files
Execution: Causal Next-Bar Open with Slippage & Regulatory Friction
"""

import sys
import time
from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

DATA_DIR = Path("/home/moonrider1968/bots/data_alpaca_sip_1m")
DEV_START = pd.Timestamp("2017-01-03 00:00:00", tz="UTC")
DEV_END = pd.Timestamp("2022-12-30 23:59:59", tz="UTC")

SYMBOLS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AVGO",
    "GOOGL", "META", "AMZN", "TSLA", "JPM", "GS",
    "XOM", "CVX", "WMT", "COST", "CAT", "GE"
]

def simulate_day_trades(day_df: pd.DataFrame, signals: list, is_etf: bool) -> list:
    results = []
    if not signals or len(day_df) < 20:
        return results

    slip_pct = 0.00005 if is_etf else 0.00015
    n_bars = len(day_df)
    
    # Fast array extraction for intrabar forward scan
    opens = day_df["open"].values
    highs = day_df["high"].values
    lows = day_df["low"].values
    closes = day_df["close"].values
    times_et = day_df["time_et"].dt.time.values

    # Only 1 trade per strategy per day
    seen_strats = set()

    for sig in signals:
        strat = sig["strat"]
        if strat in seen_strats:
            continue

        bar_idx = sig["bar_idx"]
        if bar_idx >= n_bars - 2:
            continue

        # Fill causal on next bar open
        fill_idx = bar_idx + 1
        side = sig["side"]
        entry_raw = opens[fill_idx]
        entry_px = entry_raw * (1.0 + slip_pct) if side == "BUY" else entry_raw * (1.0 - slip_pct)

        stop_px = sig["stop_px"]
        target_px = sig["target_px"]
        risk = abs(entry_px - stop_px)
        if risk <= 0.01:
            continue

        hit = None
        exit_px = None

        # Trace forward until 15:55 ET square-off or Target/Stop hit
        for j in range(fill_idx + 1, n_bars):
            if times_et[j] >= dt.time(15, 55):
                hit = "EOD"
                exit_px = closes[j] * (1.0 - slip_pct if side == "BUY" else 1.0 + slip_pct)
                break

            if side == "BUY":
                if lows[j] <= stop_px:
                    hit = "STOP"
                    exit_px = stop_px * (1.0 - slip_pct)
                    break
                elif highs[j] >= target_px:
                    hit = "TARGET"
                    exit_px = target_px * (1.0 - slip_pct)
                    break
            else:
                if highs[j] >= stop_px:
                    hit = "STOP"
                    exit_px = stop_px * (1.0 + slip_pct)
                    break
                elif lows[j] <= target_px:
                    hit = "TARGET"
                    exit_px = target_px * (1.0 + slip_pct)
                    break

        if hit:
            seen_strats.add(strat)
            raw_pnl = (exit_px - entry_px) if side == "BUY" else (entry_px - exit_px)
            net_pnl = raw_pnl - (0.0002 * entry_px)  # SEC & FINRA regulatory friction
            pnl_r = net_pnl / risk
            results.append({
                "strat": strat,
                "pnl_r": pnl_r,
                "win": 1 if pnl_r > 0 else 0,
                "hit": hit
            })

    return results

def extract_signals_for_day(day_df: pd.DataFrame) -> list:
    signals = []
    n = len(day_df)
    if n < 60:
        return signals

    c = day_df["close"].values
    h = day_df["high"].values
    l = day_df["low"].values
    o = day_df["open"].values
    v = day_df["volume"].values

    # Session VWAP
    typ = (h + l + c) / 3.0
    cum_vol = np.cumsum(v)
    cum_typ = np.cumsum(typ * v)
    vwap = np.where(cum_vol > 0, cum_typ / cum_vol, c)

    # ATR(14)
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).rolling(14, min_periods=1).mean().values

    orb_5m_h = np.max(h[:5])
    orb_5m_l = np.min(l[:5])
    orb_15m_h = np.max(h[:15])
    orb_15m_l = np.min(l[:15])

    for i in range(15, n - 10):
        # 1. ORB 5M
        if i == 15 and c[i] > orb_5m_h:
            r = max(c[i] - orb_5m_l, atr[i])
            signals.append({"strat": "01_ORB_5M", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+1.5*r})

        # 2. ORB 15M
        if i == 16 and c[i] > orb_15m_h and v[i] > np.mean(v[max(0, i-5):i]) * 1.3:
            r = max(c[i] - orb_15m_l, atr[i] * 1.5)
            signals.append({"strat": "02_ORB_15M", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+2.0*r})

        # 3. VWAP Pullback Reclaim
        if c[i] > vwap[i] and l[i-1] <= vwap[i-1] and c[i-1] < vwap[i-1]:
            r = max(c[i] - l[i], atr[i])
            signals.append({"strat": "03_VWAP_PULLBACK", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+2.0*r})

        # 4. VWAP Exhaustion Fade
        if c[i] < (vwap[i] - 2.5 * atr[i]):
            r = 1.5 * atr[i]
            signals.append({"strat": "04_VWAP_MEAN_REV", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": vwap[i]})

        # 5. Opening Drive
        if i == 17 and np.all(c[i-3:i] > o[i-3:i]) and c[i] > orb_15m_h:
            r = 1.2 * atr[i]
            signals.append({"strat": "05_OPENING_DRIVE", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+1.5*r})

        # 6. Relative Volume Momentum
        vol_ma = np.mean(v[max(0, i-20):i]) if i >= 20 else np.mean(v[:i])
        if vol_ma > 0 and (v[i] / vol_ma) > 2.5 and c[i] > vwap[i] and c[i] > c[i-1]:
            r = 1.2 * atr[i]
            signals.append({"strat": "06_RVOL_MOMENTUM", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+2.0*r})

        # 7. Velez Elephant Bar
        body = abs(c[i] - o[i])
        if body > (2.0 * atr[i]) and c[i] > o[i] and c[i] > vwap[i]:
            r = max(c[i] - l[i], atr[i])
            signals.append({"strat": "07_VELEZ_ELEPHANT", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+2.0*r})

        # 8. Gap & Go
        if i == 15 and o[0] > c[0] * 1.006 and c[i] > orb_5m_h:
            r = 1.5 * atr[i]
            signals.append({"strat": "08_GAP_AND_GO", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+2.0*r})

        # 9. First 9-EMA Pullback
        if i >= 25:
            ema9 = pd.Series(c[:i+1]).ewm(span=9).mean().values[-1]
            if c[i] > ema9 and l[i] <= ema9 and c[i-1] > ema9:
                r = 1.0 * atr[i]
                signals.append({"strat": "09_FIRST_PULLBACK", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+1.8*r})

        # 10. Failed Breakout Trap Fade
        if h[i-1] > orb_15m_h and c[i] < orb_15m_h:
            r = max(h[i-1] - c[i], atr[i])
            signals.append({"strat": "10_FAILED_BO_REV", "side": "SELL", "bar_idx": i, "stop_px": c[i]+r, "target_px": c[i]-2.0*r})

        # 11. Volatility Squeeze Breakout
        if i >= 20:
            std20 = np.std(c[i-20:i])
            sma20 = np.mean(c[i-20:i])
            if std20 < (0.4 * atr[i]) and c[i] > (sma20 + 2.0 * std20):
                r = 1.2 * atr[i]
                signals.append({"strat": "11_BB_SQUEEZE", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+2.5*r})

        # 12. RSI Extreme Scalp
        if i >= 14:
            delta = np.diff(c[i-14:i+1])
            gain = np.mean(np.where(delta > 0, delta, 0))
            loss = np.mean(np.where(delta < 0, -delta, 0))
            rsi = 100 - (100 / (1 + (gain / (loss + 1e-9))))
            if rsi < 18 and c[i] > o[i]:
                r = 1.0 * atr[i]
                signals.append({"strat": "12_RSI_EXTREME", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+1.5*r})

        # 13. Donchian-20 Breakout
        if i >= 20:
            d_high = np.max(h[i-20:i])
            if c[i] > d_high:
                r = 1.5 * atr[i]
                signals.append({"strat": "13_DONCHIAN_20", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+3.0*r})

        # 14. Momentum Velocity Trend
        if i >= 10 and c[i] > vwap[i] and c[i] > np.max(c[i-10:i]):
            r = 1.2 * atr[i]
            signals.append({"strat": "14_MOMENTUM_TREND", "side": "BUY", "bar_idx": i, "stop_px": c[i]-r, "target_px": c[i]+2.0*r})

        # 15. Lunch Mean Reversion
        if i == 90 and abs(c[i] - vwap[i]) > (2.0 * atr[i]):
            side = "SELL" if c[i] > vwap[i] else "BUY"
            r = 1.5 * atr[i]
            target = vwap[i]
            stop = c[i] + r if side == "SELL" else c[i] - r
            signals.append({"strat": "15_LUNCH_MEAN_REV", "side": side, "bar_idx": i, "stop_px": stop, "target_px": target})

    return signals

def main():
    print("=" * 80)
    print("EQUITIES 15-EXPERT INTRADAY TOURNAMENT (DEVELOPMENT SET: 2017-2022)")
    print(f"Universe: {len(SYMBOLS)} symbols | Causal Next-Bar Execution | Canonical SIP")
    print("=" * 80)

    all_trades = []
    t0 = time.time()

    for sym in SYMBOLS:
        f = DATA_DIR / f"{sym}_1MIN_SIP_2017_2026.parquet"
        if not f.exists():
            continue

        df = pd.read_parquet(f)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df_dev = df[(df["time"] >= DEV_START) & (df["time"] <= DEV_END)].copy()
        del df

        df_dev["time_et"] = df_dev["time"].dt.tz_convert("America/New_York")
        df_dev["date"] = df_dev["time_et"].dt.date
        is_etf = sym in ["SPY", "QQQ"]

        sym_trades = []
        for _, day_df in df_dev.groupby("date"):
            sigs = extract_signals_for_day(day_df)
            trades = simulate_day_trades(day_df, sigs, is_etf)
            sym_trades.extend(trades)

        all_trades.extend(sym_trades)
        print(f"  {sym:<6} -> Processed Development Fills: {len(sym_trades):>6,}")

    if not all_trades:
        print("Zero trades executed.")
        return

    res_df = pd.DataFrame(all_trades)
    dur = time.time() - t0

    print("\n" + "=" * 80)
    print(f"15-EXPERT TOURNAMENT SCORECARD — DEV (2017-2022) [Runtime: {dur:.1f}s]")
    print("=" * 80)

    scorecard = res_df.groupby("strat").agg(
        Trades=("pnl_r", "count"),
        Win_Rate=("win", lambda s: f"{s.mean()*100:.1f}%"),
        Total_R=("pnl_r", "sum"),
        Expectancy_R=("pnl_r", "mean"),
        Profit_Factor=("pnl_r", lambda s: round(s[s > 0].sum() / abs(s[s < 0].sum()) if abs(s[s < 0].sum()) > 0 else 0, 2))
    ).reset_index()

    scorecard = scorecard.sort_values(by="Total_R", ascending=False).reset_index(drop=True)
    scorecard["Total_R"] = scorecard["Total_R"].round(1)
    scorecard["Expectancy_R"] = scorecard["Expectancy_R"].round(3)

    print(scorecard.to_string(index=False))
    print("=" * 80)

if __name__ == "__main__":
    main()
