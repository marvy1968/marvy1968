#!/usr/bin/env python3
"""
Elite 20 Equities: Strict Production-Parity Historical Backtest
Exact Execution Parity with live_equities_elite20_hardened.py (SHA: 546d7fc1df...)
- Signal formed on Bar T close
- Execution at Bar T+1 Open
- Gap-abort guards: Open <= Stop or Open >= Target
- Dynamic integer shares: floor((NAV * 0.0050) / (T+1 Open - Frozen Stop))
- Same-day T+1 intraday evaluation (Conservative Stop > Target tie-break)
- Hard Sector Cap: 1 / Sector | Max 3 Concurrent | Gross Risk <= 1.5% NAV
- Native Alpaca Daily Bars
"""

import os
import sys
import math
import requests
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import numpy as np
import pandas as pd

# Load Alpaca API Environment
ENV_FILE = Path("/home/moonrider1968/bots/.env")
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
DATA_URL = "https://data.alpaca.markets/v2"

INITIAL_NAV = 100_000.0
RISK_PCT = 0.0050           # 0.50% NAV Risk
GROSS_RISK_CAP_PCT = 0.0150 # 1.50% Gross Cap
MAX_CONCURRENT = 3
MAX_PER_SECTOR = 1
FRICTION_BPS = 0.0005       # 5 bps spread + slippage

SYMBOL_TO_SECTOR = {
    "SPY": "INDEX", "QQQ": "INDEX",
    "AAPL": "TECH", "MSFT": "TECH", "NVDA": "TECH", "AVGO": "TECH",
    "GOOGL": "COMM", "META": "COMM", "NFLX": "COMM",
    "WMT": "STAPLES", "COST": "STAPLES", "PG": "STAPLES",
    "XOM": "ENERGY", "CVX": "ENERGY", "COP": "ENERGY",
    "AMZN": "DISC",
    "CAT": "IND", "GE": "IND",
    "JPM": "FIN", "GS": "FIN"
}
ELITE_20_SYMBOLS = sorted(list(SYMBOL_TO_SECTOR.keys()))


@dataclass
class Trade:
    id: int
    symbol: str
    sector: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_px: float
    stop_px: float
    target_px: float
    units: int
    dollar_risk: float
    exit_px: float = 0.0
    exit_reason: str = "OPEN"
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    hold_days: int = 0


def fetch_alpaca_daily(symbols: List[str], lookback_days: int = 1825) -> Dict[str, pd.DataFrame]:
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    dfs = {}
    end_date = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    start_date = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    print(f"📥 Loading Alpaca daily bars for {len(symbols)} tickers ({start_date} to {end_date})...")
    
    # Batch request in chunks of 10 symbols
    chunk_size = 10
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i+chunk_size]
        url = f"{DATA_URL}/stocks/bars"
        params = {
            "symbols": ",".join(chunk),
            "timeframe": "1Day",
            "start": start_date,
            "end": end_date,
            "limit": 10000,
            "adjustment": "all",
            "feed": "sip"
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                for sym, bars in data.get("bars", {}).items():
                    recs = []
                    for b in bars:
                        recs.append({
                            "timestamp": pd.to_datetime(b["t"]).tz_convert("America/New_York").normalize(),
                            "open": float(b["o"]),
                            "high": float(b["h"]),
                            "low": float(b["l"]),
                            "close": float(b["c"]),
                            "volume": int(b["v"])
                        })
                    if recs:
                        df = pd.DataFrame(recs).drop_duplicates("timestamp").set_index("timestamp").sort_index()
                        dfs[sym] = df
            else:
                print(f"Alpaca SIP returned {resp.status_code}. Falling back to IEX feed...")
                params["feed"] = "iex"
                resp = requests.get(url, headers=headers, params=params, timeout=25)
                if resp.status_code == 200:
                    data = resp.json()
                    for sym, bars in data.get("bars", {}).items():
                        recs = [{"timestamp": pd.to_datetime(b["t"]).tz_convert("America/New_York").normalize(),
                                 "open": float(b["o"]), "high": float(b["h"]), "low": float(b["l"]), "close": float(b["c"]), "volume": int(b["v"])} for b in bars]
                        if recs:
                            df = pd.DataFrame(recs).drop_duplicates("timestamp").set_index("timestamp").sort_index()
                            dfs[sym] = df
        except Exception as e:
            print(f"Error fetching {chunk}: {e}")

    # Fallback to yfinance if Alpaca API keys are restricted/expired
    missing = [s for s in symbols if s not in dfs or len(dfs[s]) < 200]
    if missing:
        print(f"⚠️ Missing Alpaca data for {missing}. Supplementing with adjusted data...")
        import yfinance as yf
        for s in missing:
            ydf = yf.download(s, period="5y", interval="1d", progress=False, auto_adjust=True)
            if isinstance(ydf.columns, pd.MultiIndex):
                ydf.columns = [c[0].lower() for c in ydf.columns]
            else:
                ydf.columns = [c.lower() for c in ydf.columns]
            ydf = ydf.dropna()
            ydf.index = pd.to_datetime(ydf.index).tz_localize(None).tz_localize("America/New_York").normalize()
            dfs[s] = ydf

    return dfs


def compute_indicators(dfs: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    processed = {}
    for sym, df in dfs.items():
        d = df.copy()
        d["ma50"] = d["close"].rolling(50).mean()
        d["std50"] = d["close"].rolling(50).std()
        d["lower_25s"] = d["ma50"] - (2.5 * d["std50"])
        d["lower_35s"] = d["ma50"] - (3.5 * d["std50"])
        processed[sym] = d
    return processed


def simulate_production_parity(dfs: Dict[str, pd.DataFrame]):
    all_dates = sorted(list(set().union(*(df.index for df in dfs.values()))))
    
    nav = INITIAL_NAV
    active_trades: List[Trade] = []
    closed_trades: List[Trade] = []
    equity_curve = []
    trade_id_seq = 0
    queued_setups = []

    # Map dates to sequential integer indices for bar lookups
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    for current_date in all_dates:
        # -------------------------------------------------------------
        # STEP 1: Morning Open Execution of Queued T Setups at T+1 Open
        # -------------------------------------------------------------
        new_intraday_entries = []
        if queued_setups:
            active_symbols = {t.symbol for t in active_trades}
            active_sectors = {t.sector for t in active_trades}
            current_open_risk = sum(t.dollar_risk for t in active_trades)

            for sig in queued_setups:
                sym = sig["symbol"]
                sec = sig["sector"]

                if sym in active_symbols or sec in active_sectors or len(active_trades) >= MAX_CONCURRENT:
                    continue

                target_risk_usd = nav * RISK_PCT
                if (current_open_risk + target_risk_usd) > (nav * GROSS_RISK_CAP_PCT + 1e-4):
                    continue

                df = dfs.get(sym)
                if df is None or current_date not in df.index:
                    continue

                bar = df.loc[current_date]
                t1_open = float(bar["open"])

                # Gap-Abort Enforcements (Exact Match to Daemon Lines 243-247)
                if t1_open <= sig["stop_px"] or t1_open >= sig["target_px"]:
                    continue

                actual_stop_dist = t1_open - sig["stop_px"]
                if actual_stop_dist <= 0.05:
                    continue

                # Integer Share Allocation (Daemon Line 254)
                qty = int(math.floor(target_risk_usd / actual_stop_dist))
                if qty < 1:
                    continue

                entry_fill = t1_open * (1.0 + FRICTION_BPS)
                actual_dollar_risk = (entry_fill - sig["stop_px"]) * qty

                trade_id_seq += 1
                new_t = Trade(
                    id=trade_id_seq,
                    symbol=sym,
                    sector=sec,
                    signal_date=sig["signal_date"],
                    entry_date=current_date,
                    exit_date=current_date,
                    entry_px=entry_fill,
                    stop_px=sig["stop_px"],
                    target_px=sig["target_px"],
                    units=qty,
                    dollar_risk=actual_dollar_risk
                )
                active_trades.append(new_t)
                new_intraday_entries.append(new_t)
                active_symbols.add(sym)
                active_sectors.add(sec)
                current_open_risk += actual_dollar_risk

            queued_setups = []

        # -------------------------------------------------------------
        # STEP 2: Intraday Stop/Target Evaluation (Including T+1 Entries)
        # -------------------------------------------------------------
        still_active = []
        for trade in active_trades:
            df = dfs.get(trade.symbol)
            if df is None or current_date not in df.index:
                still_active.append(trade)
                continue

            bar = df.loc[current_date]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            is_closed = False
            exit_px, reason = 0.0, ""

            # Check for gap fills at open (for positions carried overnight)
            if trade not in new_intraday_entries:
                if o <= trade.stop_px:
                    exit_px, reason, is_closed = o * (1.0 - FRICTION_BPS), "STOP_HIT_GAP", True
                elif o >= trade.target_px:
                    exit_px, reason, is_closed = o * (1.0 - FRICTION_BPS), "TARGET_HIT_GAP", True

            # Intraday High / Low Breaches (Conservative: Stop evaluated first)
            if not is_closed:
                if l <= trade.stop_px:
                    exit_px, reason, is_closed = trade.stop_px * (1.0 - FRICTION_BPS), "STOP_HIT", True
                elif h >= trade.target_px:
                    exit_px, reason, is_closed = trade.target_px * (1.0 - FRICTION_BPS), "TARGET_HIT", True

            if is_closed:
                trade.exit_px = exit_px
                trade.exit_date = current_date
                trade.exit_reason = reason
                trade.hold_days = (trade.exit_date - trade.entry_date).days
                pnl = (trade.exit_px - trade.entry_px) * trade.units
                trade.net_pnl = pnl
                trade.r_multiple = pnl / trade.dollar_risk if trade.dollar_risk > 0 else 0.0
                nav += pnl
                closed_trades.append(trade)
            else:
                still_active.append(trade)

        active_trades = still_active

        # -------------------------------------------------------------
        # STEP 3: Mark-to-Market Portfolio Equity Tracking
        # -------------------------------------------------------------
        unrealized = 0.0
        for tr in active_trades:
            df = dfs.get(tr.symbol)
            if df is not None and current_date in df.index:
                cur_close = float(df.loc[current_date]["close"])
                unrealized += (cur_close - tr.entry_px) * tr.units
        equity_curve.append((current_date, nav + unrealized))

        # -------------------------------------------------------------
        # STEP 4: Post-Close Daily Bar Scan (16:15 ET) for Completed Bar T
        # -------------------------------------------------------------
        active_symbols = {t.symbol for t in active_trades}
        active_sectors = {t.sector for t in active_trades}

        if len(active_trades) < MAX_CONCURRENT:
            for sym in ELITE_20_SYMBOLS:
                if sym in active_symbols or SYMBOL_TO_SECTOR[sym] in active_sectors:
                    continue

                df = dfs.get(sym)
                if df is None or current_date not in df.index:
                    continue

                idx = df.index.get_loc(current_date)
                if not isinstance(idx, (int, np.integer)) or idx < 52:
                    continue

                cur = df.iloc[idx]
                prev = df.iloc[idx - 1]

                if any(pd.isna(cur[x]) for x in ["ma50", "std50", "lower_25s", "lower_35s"]):
                    continue

                # Exact Reclaim Logic from Daemon (Line 340)
                reclaim = (
                    float(prev["close"]) < float(prev["lower_25s"])
                    and float(cur["close"]) > float(cur["lower_25s"])
                    and float(cur["close"]) < float(cur["ma50"])
                )

                if reclaim:
                    stop_dist = float(cur["close"] - cur["lower_35s"])
                    if stop_dist > 0.10:
                        queued_setups.append({
                            "symbol": sym,
                            "sector": SYMBOL_TO_SECTOR[sym],
                            "signal_date": current_date,
                            "signal_close": float(cur["close"]),
                            "stop_px": round(float(cur["lower_35s"]), 2),
                            "target_px": round(float(cur["ma50"]), 2),
                            "stop_dist": stop_dist
                        })

    # Terminal Liquidation
    last_date = all_dates[-1]
    for tr in active_trades:
        df = dfs.get(tr.symbol)
        if df is not None and last_date in df.index:
            exit_px = float(df.loc[last_date]["close"]) * (1.0 - FRICTION_BPS)
            pnl = (exit_px - tr.entry_px) * tr.units
            tr.exit_px = exit_px
            tr.exit_date = last_date
            tr.exit_reason = "TERMINAL_CLOSE"
            tr.hold_days = (tr.exit_date - tr.entry_date).days
            tr.net_pnl = pnl
            tr.r_multiple = pnl / tr.dollar_risk if tr.dollar_risk > 0 else 0.0
            nav += pnl
            closed_trades.append(tr)

    return closed_trades, nav, equity_curve


def generate_audit_report(trades: List[Trade], final_nav: float, eq_curve: list):
    eq_df = pd.DataFrame(eq_curve, columns=["date", "equity"]).set_index("date")
    eq_df["cummax"] = eq_df["equity"].cummax()
    eq_df["dd"] = (eq_df["cummax"] - eq_df["equity"]) / eq_df["cummax"]
    max_dd = float(eq_df["dd"].max() * 100.0) if not eq_df.empty else 0.0

    rs = np.array([t.r_multiple for t in trades], dtype=float)
    wins = rs[rs > 0.0]
    losses = rs[rs < 0.0]
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else 0.0
    net_return = (final_nav / INITIAL_NAV - 1.0) * 100.0

    # Top-3 Winner Removal Audit
    rs_sorted = sorted(rs, reverse=True)
    rs_no_top3 = rs_sorted[3:] if len(rs_sorted) > 3 else []
    w_no_top3 = [r for r in rs_no_top3 if r > 0.0]
    l_no_top3 = [abs(r) for r in rs_no_top3 if r < 0.0]
    pf_no_top3 = (sum(w_no_top3) / sum(l_no_top3)) if l_no_top3 else 0.0

    print("\n" + "=" * 80)
    print(" 🏆 PRODUCTION-PARITY AUDIT: ELITE 20 MEAN REVERSION")
    print("=" * 80)
    print(f" Initial Capital:              ${INITIAL_NAV:,.2f}")
    print(f" Final Portfolio NAV:          ${final_nav:,.2f}")
    print(f" Net Compounded Return:        {net_return:+.2f}%")
    print(f" Total Executed Trades:        {len(trades)}")
    print(f" Win Rate:                     {(len(wins)/len(trades)*100):.1f}%")
    print(f" Profit Factor:                {pf:.3f}")
    print(f" Profit Factor (No Top-3):     {pf_no_top3:.3f}")
    print(f" Mean Expectancy:              {rs.mean():+.3f}R")
    print(f" Net Expectancy:               {rs.sum():+.2f}R")
    print(f" True MTM Max Drawdown:        {max_dd:.2f}%")
    print(f" Return / Max DD Ratio:        {(net_return / max_dd):.2f}" if max_dd > 0 else "N/A")
    print("=" * 80)

    # Sector Breakdown
    sec_data = []
    for sec in sorted(list(set(SYMBOL_TO_SECTOR.values()))):
        sub = [t for t in trades if t.sector == sec]
        s_rs = [t.r_multiple for t in sub]
        s_wins = [r for r in s_rs if r > 0]
        s_losses = [abs(r) for r in s_rs if r < 0]
        s_pf = (sum(s_wins) / sum(s_losses)) if s_losses else (np.inf if s_wins else 0.0)
        s_pnl = sum(t.net_pnl for t in sub)
        sec_data.append({
            "Sector": sec,
            "Trades": len(sub),
            "Win%": f"{(len(s_wins)/len(sub)*100):.1f}%" if sub else "0.0%",
            "MeanR": f"{np.mean(s_rs):+.3f}R" if sub else "0.0R",
            "PF": f"{s_pf:.2f}",
            "Total PnL": f"${s_pnl:+,.2f}"
        })

    print("\n 🏢 CONTRIBUTIONS BY SECTOR (PARITY):")
    print(pd.DataFrame(sec_data).to_string(index=False))
    print("=" * 80)

    # Save detailed trade logs
    df_trades = pd.DataFrame([t.__dict__ for t in trades])
    out_csv = Path("/home/moonrider1968/bots/logs/elite20_parity_certified_trades.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df_trades.to_csv(out_csv, index=False)
    print(f"Detailed trade logs saved: {out_csv}\n")


def main():
    raw_dfs = fetch_alpaca_daily(ELITE_20_SYMBOLS, lookback_days=1825)
    dfs = compute_indicators(raw_dfs)
    trades, final_nav, eq = simulate_production_parity(dfs)
    generate_audit_report(trades, final_nav, eq)


if __name__ == "__main__":
    main()
