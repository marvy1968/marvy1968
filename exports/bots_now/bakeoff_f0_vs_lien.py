import os
import re
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict

from run_h4_filter_sweep import load_local_dataset

PAIRS = ["EUR_USD", "USD_JPY", "GBP_USD", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"]
SPREAD_BPS = 0.0003  # 3 pips standard friction

# Factor Firewall: Currency exposure constraint (Max 2 USD-long or 2 USD-short)
def get_usd_exposure(active_positions: dict) -> int:
    exp = 0
    for sym, pos in active_positions.items():
        base, quote = sym.split("_")
        if pos["side"] == "LONG":
            if base == "USD": exp += 1
            elif quote == "USD": exp -= 1
        elif pos["side"] == "SHORT":
            if base == "USD": exp -= 1
            elif quote == "USD": exp += 1
    return exp

def prepare_indicators(dfs: Dict[str, pd.DataFrame]):
    for sym, df in dfs.items():
        # Standard ATR14
        prev_c = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"] - prev_c).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()

        # F0 Indicators (Canonical 20/50/200 EMA + ATR Trailing)
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

        # Kathy Lien Indicators (Dual EMA Trend + RSI / MACD Momentum)
        # Typically Lien uses EMA(8)/EMA(21) or EMA(20) pullback + RSI/MACD filter
        df["ema8"] = df["close"].ewm(span=8, adjust=False).mean()
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
        
        # RSI 14
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean().replace(0, np.nan)
        rs = avg_gain / avg_loss
        df["rsi14"] = 100 - (100 / (1 + rs))

        # MACD (12, 26, 9)
        fast = df["close"].ewm(span=12, adjust=False).mean()
        slow = df["close"].ewm(span=26, adjust=False).mean()
        df["macd"] = fast - slow
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    return dfs

def run_simulation(dfs: Dict[str, pd.DataFrame], strategy_mode="F0"):
    # Synchronize all available bar timestamps across pairs
    all_times = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_times = all_times[200:]

    active = {}
    completed_trades = []
    cash = 100_000.0
    equity = []
    hwm = 100_000.0
    max_dd = 0.0

    for i in range(len(eval_times) - 1):
        dt = eval_times[i]
        next_dt = eval_times[i + 1]

        # 1. Process Channel / Signal Exits on Next Bar Open
        closed_syms = []
        for sym, pos in active.items():
            if next_dt not in dfs[sym].index:
                continue
            next_open = float(dfs[sym].loc[next_dt, "open"])

            if pos.get("pending_exit"):
                fill_px = next_open * (1.0 - SPREAD_BPS) if pos["side"] == "LONG" else next_open * (1.0 + SPREAD_BPS)
                pnl = (fill_px - pos["entry_px"]) * pos["units"] if pos["side"] == "LONG" else (pos["entry_px"] - fill_px) * pos["units"]
                cash += pnl
                completed_trades.append({
                    "symbol": sym, "side": pos["side"], "pnl": pnl,
                    "r_mult": pnl / pos["dollar_risk"], "reason": "SIGNAL_EXIT"
                })
                closed_syms.append(sym)

        for s in closed_syms:
            del active[s]

        # 2. Process Stops & Channel Logic Intraday
        closed_intra = []
        for sym, pos in active.items():
            if next_dt not in dfs[sym].index:
                continue
            bar = dfs[sym].loc[next_dt]
            o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

            if pos["side"] == "LONG":
                # Adverse Gap-Through Stop Evaluation
                if l <= pos["stop_px"]:
                    fill_px = min(o, pos["stop_px"]) * (1.0 - SPREAD_BPS)
                    pnl = (fill_px - pos["entry_px"]) * pos["units"]
                    cash += pnl
                    completed_trades.append({
                        "symbol": sym, "side": "LONG", "pnl": pnl,
                        "r_mult": pnl / pos["dollar_risk"], "reason": "STOP"
                    })
                    closed_intra.append(sym)
                    continue

                # Exit signals
                if strategy_mode == "F0":
                    if c < float(bar["ema50"]):
                        pos["pending_exit"] = True
                elif strategy_mode == "LIEN":
                    if c < float(bar["ema21"]) or float(bar["macd"]) < float(bar["macd_signal"]):
                        pos["pending_exit"] = True

            elif pos["side"] == "SHORT":
                if h >= pos["stop_px"]:
                    fill_px = max(o, pos["stop_px"]) * (1.0 + SPREAD_BPS)
                    pnl = (pos["entry_px"] - fill_px) * pos["units"]
                    cash += pnl
                    completed_trades.append({
                        "symbol": sym, "side": "SHORT", "pnl": pnl,
                        "r_mult": pnl / pos["dollar_risk"], "reason": "STOP"
                    })
                    closed_intra.append(sym)
                    continue

                if strategy_mode == "F0":
                    if c > float(bar["ema50"]):
                        pos["pending_exit"] = True
                elif strategy_mode == "LIEN":
                    if c > float(bar["ema21"]) or float(bar["macd"]) > float(bar["macd_signal"]):
                        pos["pending_exit"] = True

        for s in closed_intra:
            del active[s]

        # 3. Mark-to-Market NAV
        mtm = 0.0
        for s, pos in active.items():
            if next_dt in dfs[s].index:
                close_px = float(dfs[s].loc[next_dt, "close"])
                mtm += (close_px - pos["entry_px"]) * pos["units"] if pos["side"] == "LONG" else (pos["entry_px"] - close_px) * pos["units"]
        
        eod_nav = cash + mtm
        if eod_nav > hwm:
            hwm = eod_nav
        dd = (hwm - eod_nav) / hwm if hwm > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        equity.append(eod_nav)

        # 4. Signal Evaluation on Bar Close -> Fill on Next Bar Open
        if len(active) < 3 and (i + 2) < len(eval_times):
            t2_dt = eval_times[i + 2]
            
            for sym in PAIRS:
                if sym in active or len(active) >= 3:
                    continue
                df = dfs.get(sym)
                if df is None or next_dt not in df.index or t2_dt not in df.index:
                    continue

                cur = df.loc[next_dt]
                prev = df.iloc[df.index.get_loc(next_dt) - 1]

                atr = float(cur["atr14"])
                if pd.isna(atr) or atr <= 0:
                    continue

                r_dist = 1.5 * atr
                dollar_risk = eod_nav * 0.0050
                units = int(dollar_risk / r_dist)
                if units < 1:
                    continue

                side = None

                if strategy_mode == "F0":
                    # Canonical F0 Trend Entry
                    if float(cur["close"]) > float(cur["ema20"]) > float(cur["ema50"]) > float(cur["ema200"]):
                        side = "LONG"
                    elif float(cur["close"]) < float(cur["ema20"]) < float(cur["ema50"]) < float(cur["ema200"]):
                        side = "SHORT"

                elif strategy_mode == "LIEN":
                    # Lien Momentum: EMA 8/21 cross + MACD thrust + RSI confirmation
                    c, e8, e21 = float(cur["close"]), float(cur["ema8"]), float(cur["ema21"])
                    macd, macd_sig = float(cur["macd"]), float(cur["macd_signal"])
                    rsi = float(cur["rsi14"])

                    if c > e8 > e21 and macd > macd_sig and 50 < rsi < 70:
                        side = "LONG"
                    elif c < e8 < e21 and macd < macd_sig and 30 < rsi < 50:
                        side = "SHORT"

                if side is not None:
                    # Factor Firewall Check
                    curr_usd_exp = get_usd_exposure(active)
                    base, quote = sym.split("_")
                    usd_delta = 1 if (side == "LONG" and base == "USD") or (side == "SHORT" and quote == "USD") else -1
                    
                    if abs(curr_usd_exp + usd_delta) > 2:
                        continue  # Blocked by firewall

                    t2_open = float(df.loc[t2_dt, "open"])
                    fill_px = t2_open * (1.0 + SPREAD_BPS) if side == "LONG" else t2_open * (1.0 - SPREAD_BPS)
                    stop_px = fill_px - r_dist if side == "LONG" else fill_px + r_dist

                    active[sym] = {
                        "side": side,
                        "entry_date": str(t2_dt),
                        "entry_px": fill_px,
                        "stop_px": stop_px,
                        "units": units,
                        "dollar_risk": dollar_risk,
                        "pending_exit": False
                    }

    tdf = pd.DataFrame(completed_trades)
    if tdf.empty:
        return {"trades": 0, "cagr": 0, "max_dd": 0, "pf": 0, "mean_r": 0, "wr": 0}

    wins = tdf[tdf["pnl"] > 0]
    wr = len(wins) / len(tdf) * 100.0
    gp = wins["pnl"].sum()
    gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
    pf = gp / gl if gl > 0 else 999.0
    net_r = tdf["r_mult"].sum()
    mean_r = tdf["r_mult"].mean()

    years = (eval_times[-1] - eval_times[0]).days / 365.25
    cagr = ((equity[-1] / 100_000.0) ** (1.0 / years) - 1.0) * 100.0

    return {
        "trades": len(tdf),
        "cagr": round(cagr, 2),
        "max_dd": round(max_dd * 100.0, 2),
        "pf": round(pf, 2),
        "mean_r": round(mean_r, 3),
        "net_r": round(net_r, 2),
        "wr": round(wr, 1),
        "final_nav": round(equity[-1], 2)
    }

if __name__ == "__main__":
    print("Loading OANDA H4 certified datasets...")
    dfs = {sym: load_local_dataset(sym, "H4") for sym in PAIRS}
    dfs = prepare_indicators(dfs)

    print("\n" + "=" * 80)
    print("FINAL TWO-SYSTEM BAKE-OFF (HARDENED PORTFOLIO HARNESS)")
    print("=" * 80)

    f0_res = run_simulation(dfs, strategy_mode="F0")
    lien_res = run_simulation(dfs, strategy_mode="LIEN")

    table = pd.DataFrame([
        {"System": "Canonical F0 (Target: 278 Trades | +3.85% CAGR)", **f0_res},
        {"System": "Kathy Lien Momentum (Hardened Engine)", **lien_res}
    ])
    
    print(table[["System", "trades", "cagr", "max_dd", "pf", "mean_r", "wr", "final_nav"]].to_string(index=False))
    print("=" * 80)
