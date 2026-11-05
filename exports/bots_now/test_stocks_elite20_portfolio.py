#!/usr/bin/env python3
"""
E4 Equities Production Basket: Elite 20 Multi-Sector Portfolio Engine
Engine: Daily 50 SMA | Long-Only -2.5 Sigma Reclaim Entry | -3.5 Sigma Stop | Centerline Target
Risk Framework: Dynamic 0.50% NAV Risk | Gross 1.50% Open Risk Cap (Max 3 Concurrent Positions)
Concurrency Management: Max 1 concurrent position per sector to enforce true cross-market diversification.
"""

import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance"])
    import yfinance as yf

# Elite 20 Multi-Sector Basket mapped to Sector Groups
SYMBOL_TO_SECTOR = {
    # Index Anchors
    "SPY": "INDEX", "QQQ": "INDEX",
    # Tech (Tier 1)
    "AAPL": "TECH", "MSFT": "TECH", "NVDA": "TECH", "AVGO": "TECH",
    # Communication (Tier 1)
    "GOOGL": "COMM", "META": "COMM", "NFLX": "COMM",
    # Consumer Staples (Tier 1)
    "WMT": "STAPLES", "COST": "STAPLES", "PG": "STAPLES",
    # Energy (Tier 1)
    "XOM": "ENERGY", "CVX": "ENERGY", "COP": "ENERGY",
    # Discretionary / Industrials / Financials (Tier 2 Leaders)
    "AMZN": "DISC",
    "CAT": "IND", "GE": "IND",
    "JPM": "FIN", "GS": "FIN"
}

ELITE_20_SYMBOLS = list(SYMBOL_TO_SECTOR.keys())
INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050      # 0.50% NAV per trade
GROSS_RISK_CAP = 0.0150 # 1.50% Gross Open Risk (Max 3 open)
MAX_CONCURRENT = 3
MAX_PER_SECTOR = 1     # Cross-sector risk firewall
SLIPPAGE_BPS = 0.0005  # 5 bps estimated equity slippage

@dataclass
class StockTrade:
    trade_id: int
    symbol: str
    sector: str
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
    hold_days: int = 0

def load_stock_data(symbols: List[str], period: str = "5y") -> Dict[str, pd.DataFrame]:
    print(f"📥 Loading daily bars for Elite 20 basket ({len(symbols)} tickers, {period})...")
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

        # Long Reclaim Setup
        if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma50"]:
            stop_dist = cur["close"] - cur["lower_35s"]
            if stop_dist > 0:
                signals.append({
                    "timestamp": t,
                    "symbol": symbol,
                    "sector": SYMBOL_TO_SECTOR[symbol],
                    "side": "BUY",
                    "entry_px": float(cur["close"]),
                    "stop_px": float(cur["lower_35s"]),
                    "target_px": float(cur["ma50"]),
                    "stop_dist": stop_dist
                })
    return pd.DataFrame(signals)

def simulate_elite20_portfolio(all_signals: pd.DataFrame, dfs: Dict[str, pd.DataFrame]) -> Tuple[dict, pd.DataFrame, List[StockTrade]]:
    if all_signals.empty:
        return {}, pd.DataFrame(), []

    sorted_sigs = all_signals.sort_values(by=["timestamp", "symbol"]).to_dict("records")
    all_dates = sorted(list(set().union(*(df.index for df in dfs.values()))))

    realized_nav = INITIAL_EQUITY
    active_trades: List[StockTrade] = []
    closed_trades: List[StockTrade] = []
    equity_curve = []

    sig_idx = 0
    num_sigs = len(sorted_sigs)
    trade_counter = 0

    for current_date in all_dates:
        # 1. Evaluate Active Positions
        still_active = []
        for trade in active_trades:
            if trade.symbol not in dfs or current_date not in dfs[trade.symbol].index:
                still_active.append(trade)
                continue

            bar = dfs[trade.symbol].loc[current_date]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            is_closed = False
            exit_px, reason = 0.0, ""

            # Stop / Target check with gap logic
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
                trade.hold_days = (trade.exit_time - trade.entry_time).days
                pnl = (trade.exit_px - trade.entry_px) * trade.units
                trade.net_pnl = pnl
                trade.r_multiple = pnl / trade.dollar_risk if trade.dollar_risk > 0 else 0.0
                realized_nav += pnl
                closed_trades.append(trade)
            else:
                still_active.append(trade)

        active_trades = still_active

        # 2. Admit New Signals
        incoming = []
        while sig_idx < num_sigs and sorted_sigs[sig_idx]["timestamp"] == current_date:
            incoming.append(sorted_sigs[sig_idx])
            sig_idx += 1

        for sig in incoming:
            sym = sig["symbol"]
            sec = sig["sector"]

            # Checks: Duplicate symbol, Portfolio Gross Risk Cap (Max 3), Sector Cap (Max 1)
            if any(p.symbol == sym for p in active_trades):
                continue
            if len(active_trades) >= MAX_CONCURRENT:
                continue
            if sum(1 for p in active_trades if p.sector == sec) >= MAX_PER_SECTOR:
                continue

            dollar_risk = realized_nav * RISK_PCT
            units = dollar_risk / sig["stop_dist"] if sig["stop_dist"] > 0 else 0
            if units <= 0:
                continue

            trade_counter += 1
            entry_px = sig["entry_px"] * (1.0 + SLIPPAGE_BPS)
            trade_obj = StockTrade(
                trade_id=trade_counter, symbol=sym, sector=sec, side="BUY",
                entry_time=current_date, exit_time=current_date,
                entry_px=entry_px, stop_px=sig["stop_px"], target_px=sig["target_px"],
                units=units, dollar_risk=dollar_risk
            )
            active_trades.append(trade_obj)

        # 3. Mark to Market Unrealized Equity
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

    stats = {
        "trades": len(closed_trades),
        "win_rate": len(wins) / len(closed_trades) * 100.0 if closed_trades else 0.0,
        "mean_r": float(np.mean(net_rs)) if net_rs else 0.0,
        "net_pf": pf,
        "comp_return": ((realized_nav - INITIAL_EQUITY) / INITIAL_EQUITY) * 100.0,
        "max_dd": max_dd,
        "final_nav": realized_nav,
        "avg_hold_days": np.mean([t.hold_days for t in closed_trades]) if closed_trades else 0
    }
    return stats, eq_df, closed_trades

def main():
    print("=" * 115)
    print(" 🏆 ELITE 20 EQUITIES UNIFIED PORTFOLIO AUDIT (50 SMA LONG-ONLY)")
    print(f" Universe ({len(ELITE_20_SYMBOLS)} Tickers): {', '.join(ELITE_20_SYMBOLS)}")
    print(f" Constraints: Dynamic 0.50% Risk | Gross 1.50% Cap (Max 3 Open) | Sector Cap: Max 1 Open/Sector")
    print("=" * 115)

    dfs = load_stock_data(ELITE_20_SYMBOLS, period="5y")
    sigs_list = [generate_signals(dfs[sym], sym) for sym in dfs]
    all_sigs = pd.concat(sigs_list, ignore_index=True) if sigs_list else pd.DataFrame()

    stats, eq_df, closed_trades = simulate_elite20_portfolio(all_sigs, dfs)

    print("\n" + "=" * 65)
    print(" 📊 PORTFOLIO PERFORMANCE SUMMARY (5-YEAR HISTORICAL)")
    print("=" * 65)
    print(f" Initial Equity:                ${INITIAL_EQUITY:,.2f}")
    print(f" Final Portfolio NAV:           ${stats['final_nav']:,.2f}")
    print(f" Net Compounded Return:         {stats['comp_return']:+.2f}%")
    print(f" Total Executed Trades:         {stats['trades']}")
    print(f" Portfolio Win Rate:            {stats['win_rate']:.1f}%")
    print(f" Mean Expectancy:               {stats['mean_r']:+.3f}R")
    print(f" Profit Factor:                 {stats['net_pf']:.2f}")
    print(f" True MTM Max Drawdown:         {stats['max_dd']:.2f}%")
    print(f" Return / Max Drawdown Ratio:   {stats['comp_return'] / stats['max_dd']:.2f}")
    print(f" Average Holding Period:        {stats['avg_hold_days']:.1f} calendar days")
    print("=" * 65)

    # Breakdown per sector performance
    sector_summary = {}
    for t in closed_trades:
        sec = t.sector
        if sec not in sector_summary:
            sector_summary[sec] = {"trades": 0, "wins": 0, "net_pnl": 0.0, "rs": []}
        sector_summary[sec]["trades"] += 1
        if t.r_multiple > 0:
            sector_summary[sec]["wins"] += 1
        sector_summary[sec]["net_pnl"] += t.net_pnl
        sector_summary[sec]["rs"].append(t.r_multiple)

    sec_rows = []
    for sec, data in sorted(sector_summary.items()):
        wr = (data["wins"] / data["trades"]) * 100.0 if data["trades"] > 0 else 0.0
        sec_rows.append({
            "Sector": sec,
            "Trades": data["trades"],
            "Win Rate": f"{wr:.1f}%",
            "Mean Expectancy": f"{np.mean(data['rs']):+.3f}R",
            "Total PnL ($)": f"${data['net_pnl']:+,.2f}"
        })
    print("\n 🏢 CONTRIBUTIONS BY SECTOR:")
    print(pd.DataFrame(sec_rows).to_string(index=False))
    print("=" * 65)

if __name__ == "__main__":
    main()
