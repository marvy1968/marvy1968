#!/usr/bin/env python3
"""
E4 Equities Sector-by-Sector Audit: Top 5 Stocks Across All 11 S&P Sectors
Engine: Daily 50 SMA | Long-Only -2.5 Sigma Reclaim Entry | -3.5 Sigma Stop | Centerline Target
Sizing: Dynamic 0.50% NAV Risk | 5-Year Historical Daily Bars
"""

import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Dict

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance"])
    import yfinance as yf

# Top 5 market-cap leaders per S&P 500 GICS Sector (55 Tickers Total)
SECTORS = {
    "Technology (XLK)": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL"],
    "Communication (XLC)": ["GOOGL", "META", "NFLX", "TMUS", "DIS"],
    "Consumer Discretionary (XLY)": ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "Financials (XLF)": ["JPM", "BAC", "WFC", "GS", "MS"],
    "Healthcare (XLV)": ["LLY", "UNH", "JNJ", "ABBV", "MRK"],
    "Industrials (XLI)": ["CAT", "GE", "UNP", "HON", "BA"],
    "Consumer Staples (XLP)": ["WMT", "COST", "PG", "KO", "PEP"],
    "Energy (XLE)": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "Utilities (XLU)": ["NEE", "SO", "DUK", "CEG", "SRE"],
    "Real Estate (XLRE)": ["PLD", "AMT", "EQIX", "PSA", "O"],
    "Materials (XLB)": ["LIN", "SHW", "APD", "ECL", "FCX"]
}

INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050  # 0.50% NAV risk per trade
SLIPPAGE_BPS = 0.0005  # 5 bps slippage

@dataclass
class StockTrade:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_px: float
    stop_px: float
    target_px: float
    units: float
    dollar_risk: float
    exit_px: float = 0.0
    exit_reason: str = "OPEN"
    net_pnl: float = 0.0
    r_multiple: float = 0.0

def load_stock_data(symbols: List[str], period: str = "5y") -> Dict[str, pd.DataFrame]:
    print(f"📥 Loading daily data for {len(symbols)} stocks across 11 sectors ({period})...")
    data = {}
    for sym in symbols:
        df = yf.download(sym, period=period, interval="1d", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        df = df.dropna()
        if len(df) >= 120:
            data[sym] = df
    return data

def generate_signals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = df.copy()
    df["ma50"] = df["close"].rolling(50).mean()
    df["std50"] = df["close"].rolling(50).std()
    df["lower_25s"] = df["ma50"] - (2.5 * df["std50"])
    df["lower_35s"] = df["ma50"] - (3.5 * df["std50"])

    signals = []
    for i in range(55, len(df) - 1):
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        t = df.index[i]

        # Long Reclaim Entry
        if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma50"]:
            stop_dist = cur["close"] - cur["lower_35s"]
            if stop_dist > 0:
                signals.append({
                    "timestamp": t, "symbol": symbol, "side": "BUY",
                    "entry_px": float(cur["close"]), "stop_px": float(cur["lower_35s"]),
                    "target_px": float(cur["ma50"]), "stop_dist": stop_dist
                })
    return pd.DataFrame(signals)

def simulate_sector(all_signals: pd.DataFrame, dfs: Dict[str, pd.DataFrame], max_concurrent: int = 3) -> dict:
    if all_signals.empty:
        return {"trades": 0, "win_rate": 0, "mean_r": 0, "net_pf": 0, "comp_return": 0, "max_dd": 0}

    sorted_sigs = all_signals.sort_values(by=["timestamp", "symbol"]).to_dict("records")
    all_dates = sorted(list(set().union(*(df.index for df in dfs.values()))))

    realized_nav = INITIAL_EQUITY
    active_trades: List[StockTrade] = []
    closed_trades: List[StockTrade] = []
    equity_curve = []

    sig_idx = 0
    num_sigs = len(sorted_sigs)

    for current_date in all_dates:
        still_active = []
        for trade in active_trades:
            if trade.symbol not in dfs or current_date not in dfs[trade.symbol].index:
                still_active.append(trade)
                continue

            bar = dfs[trade.symbol].loc[current_date]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            is_closed = False
            exit_px, reason = 0.0, ""

            if o <= trade.stop_px:
                exit_px, reason, is_closed = o * (1.0 - SLIPPAGE_BPS), "STOP_HIT", True
            elif o >= trade.target_px:
                exit_px, reason, is_closed = o * (1.0 - SLIPPAGE_BPS), "TARGET_HIT", True
            elif l <= trade.stop_px:
                exit_px, reason, is_closed = trade.stop_px * (1.0 - SLIPPAGE_BPS), "STOP_HIT", True
            elif h >= trade.target_px:
                exit_px, reason, is_closed = trade.target_px * (1.0 - SLIPPAGE_BPS), "TARGET_HIT", True

            if is_closed:
                trade.exit_px = exit_px
                trade.exit_time = current_date
                trade.exit_reason = reason
                pnl = (trade.exit_px - trade.entry_px) * trade.units
                trade.net_pnl = pnl
                trade.r_multiple = pnl / trade.dollar_risk if trade.dollar_risk > 0 else 0.0
                realized_nav += pnl
                closed_trades.append(trade)
            else:
                still_active.append(trade)

        active_trades = still_active

        incoming = []
        while sig_idx < num_sigs and sorted_sigs[sig_idx]["timestamp"] == current_date:
            incoming.append(sorted_sigs[sig_idx])
            sig_idx += 1

        for sig in incoming:
            sym = sig["symbol"]
            if any(p.symbol == sym for p in active_trades) or len(active_trades) >= max_concurrent:
                continue

            dollar_risk = realized_nav * RISK_PCT
            units = dollar_risk / sig["stop_dist"] if sig["stop_dist"] > 0 else 0
            if units <= 0:
                continue

            entry_px = sig["entry_px"] * (1.0 + SLIPPAGE_BPS)
            trade_obj = StockTrade(
                symbol=sym, side="BUY", entry_time=current_date, exit_time=current_date,
                entry_px=entry_px, stop_px=sig["stop_px"], target_px=sig["target_px"],
                units=units, dollar_risk=dollar_risk
            )
            active_trades.append(trade_obj)

        unrealized = 0.0
        for tr in active_trades:
            if tr.symbol in dfs and current_date in dfs[tr.symbol].index:
                curr_c = float(dfs[tr.symbol].loc[current_date]["close"])
                unrealized += (curr_c - tr.entry_px) * tr.units
        equity_curve.append((current_date, realized_nav + unrealized))

    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"])
    eq_df["cummax"] = eq_df["equity"].cummax()
    eq_df["dd"] = (eq_df["cummax"] - eq_df["equity"]) / eq_df["cummax"]
    max_dd = float(eq_df["dd"].max() * 100.0) if not eq_df.empty else 0.0

    net_rs = [t.r_multiple for t in closed_trades]
    wins = [r for r in net_rs if r > 0]
    losses = [abs(r) for r in net_rs if r < 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0

    return {
        "trades": len(closed_trades),
        "win_rate": len(wins) / len(closed_trades) * 100.0 if closed_trades else 0.0,
        "mean_r": float(np.mean(net_rs)) if net_rs else 0.0,
        "net_pf": pf,
        "comp_return": ((realized_nav - INITIAL_EQUITY) / INITIAL_EQUITY) * 100.0,
        "max_dd": max_dd
    }

def main():
    print("=" * 115)
    print(" 🔬 SECTOR BREAKDOWN AUDIT: TOP 5 STOCKS PER S&P 500 GICS SECTOR (50 SMA LONG-ONLY)")
    print(" Envelopes: -2.5 Sigma Reclaim Entry | -3.5 Sigma Hard Stop | 50 SMA Centerline Target")
    print(" Sizing: Dynamic 0.50% NAV Risk | Max 3 Concurrent Positions Per Sector")
    print("=" * 115)

    all_symbols = [sym for syms in SECTORS.values() for sym in syms]
    dfs = load_stock_data(all_symbols, period="5y")

    sector_results = []
    for sector_name, symbols in SECTORS.items():
        sec_dfs = {sym: dfs[sym] for sym in symbols if sym in dfs}
        sigs_list = [generate_signals(sec_dfs[sym], sym) for sym in sec_dfs]
        all_sigs = pd.concat(sigs_list, ignore_index=True) if sigs_list else pd.DataFrame()

        res = simulate_sector(all_sigs, sec_dfs, max_concurrent=3)
        sector_results.append({
            "Sector": sector_name,
            "Trades": res["trades"],
            "Win Rate": f"{res['win_rate']:.1f}%",
            "Mean Expectancy": f"{res['mean_r']:+.3f}R",
            "Profit Factor": f"{res['net_pf']:.2f}",
            "5Y Comp Return": f"{res['comp_return']:+.2f}%",
            "True MaxDD": f"{res['max_dd']:.2f}%"
        })

    df_res = pd.DataFrame(sector_results)
    print("\n" + df_res.to_string(index=False))
    print("=" * 115)

if __name__ == "__main__":
    main()
