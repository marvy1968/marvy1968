#!/usr/bin/env python3
"""
Continuous Stitched Out-of-Sample (OOS) Certification Engine
Stitches Folds 1-4 test windows (2025-10-02 to 2026-08-27) into a continuous,
compounded NAV curve. Reports true drawdown, trade attribution, and risk clusters.
"""

import os
import pandas as pd
import numpy as np

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
MANIFEST_PATH = os.path.join(DATA_DIR, "universe_manifest.csv")

FEE_RATE = 0.0010      # Standard 0.10% fee stress
SLIPPAGE_RATE = 0.0010 # Standard 10 bps slippage stress
MAX_POSITIONS = 3
INITIAL_CAPITAL = 100_000.0
RISK_PER_TRADE = 0.01

FROZEN_CANDIDATES = [
    ("Candidate Alpha ($0.25M / 15d / 2.0x)", 250_000, 15, 2.0),
    ("Candidate Beta  ($0.25M / 30d / 2.0x)", 250_000, 30, 2.0),
    ("Candidate Gamma ($0.50M / 15d / 2.0x)", 500_000, 15, 2.0),
    ("Candidate Delta ($2.00M / 30d / 2.0x)", 2_000_000, 30, 2.0),
]

def load_data():
    manifest = pd.read_csv(MANIFEST_PATH)
    dfs = {}
    for symbol in manifest["symbol"]:
        clean_sym = symbol.replace("/", "_")
        csv_path = os.path.join(DATA_DIR, f"{clean_sym}_1d.csv")
        df = pd.read_csv(csv_path, parse_dates=["datetime"], index_col="datetime")
        df.sort_index(inplace=True)

        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum((df["high"] - df["close"].shift(1)).abs(), (df["low"] - df["close"].shift(1)).abs())
        )
        df["atr14"] = df["tr"].rolling(14).mean()
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["sma50"] = df["close"].rolling(50).mean()

        for lb in [15, 30]:
            df[f"donch_high_{lb}"] = df["high"].shift(1).rolling(lb).max()

        df["quote_volume"] = df["close"] * df["volume"]
        df["med_vol_30d"] = df["quote_volume"].rolling(30, min_periods=30).median()
        dfs[symbol] = df

    return manifest, dfs

def simulate_continuous_oos(manifest, dfs, liq_floor, lookback, atr_mult, oos_dates):
    btc_df = dfs["BTC/USD"]
    cash = INITIAL_CAPITAL
    positions = {}
    trade_logs = []
    equity_curve = []

    for i in range(1, len(oos_dates)):
        dt = oos_dates[i]
        prev_dt = oos_dates[i - 1]

        if prev_dt not in btc_df.index or dt not in btc_df.index:
            continue

        btc_prev = btc_df.loc[prev_dt]
        if pd.isna(btc_prev["ema20"]) or pd.isna(btc_prev["sma50"]):
            continue

        macro_bull = bool(btc_prev["close"] > btc_prev["ema20"] > btc_prev["sma50"])

        # Phase 1A: Open Exits
        open_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            open_px = float(row["open"])

            exit_px = None
            reason = None

            if not macro_bull:
                exit_px = open_px * (1.0 - SLIPPAGE_RATE)
                reason = "macro_exit"
            elif open_px <= pos["stop_px"]:
                exit_px = open_px * (1.0 - SLIPPAGE_RATE)
                reason = "gap_stop"

            if exit_px is not None:
                gross = pos["qty"] * exit_px
                fee = gross * FEE_RATE
                net = gross - fee
                cash += net
                pnl = net - (pos["entry_notional"] + pos["entry_fee"])
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                trade_logs.append({
                    "symbol": sym, "entry_dt": pos["entry_dt"], "exit_dt": dt,
                    "entry_px": pos["entry_px"], "exit_px": exit_px, "pnl": pnl,
                    "r": r_mult, "win": pnl > 0, "reason": reason
                })
                open_closed.append(sym)

        for sym in open_closed:
            del positions[sym]

        nav_at_open = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            px = float(df.loc[dt, "open"]) if dt in df.index else pos["last_px"]
            nav_at_open += pos["qty"] * px

        # Phase 1C: Open Entries
        if macro_bull and len(positions) < MAX_POSITIONS:
            candidates = []
            for sym in manifest["symbol"]:
                if sym in positions:
                    continue
                df = dfs[sym]
                if prev_dt not in df.index or dt not in df.index:
                    continue
                prev = df.loc[prev_dt]
                today = df.loc[dt]

                req = [prev.get("med_vol_30d"), prev.get(f"donch_high_{lookback}"), prev.get("atr14")]
                if any(pd.isna(x) for x in req) or float(prev["med_vol_30d"]) < liq_floor:
                    continue

                breakout_level = float(prev[f"donch_high_{lookback}"])
                if float(prev["close"]) > breakout_level:
                    strength = (float(prev["close"]) / breakout_level) - 1.0
                    candidates.append((strength, sym, prev, today))

            candidates.sort(key=lambda x: x[0], reverse=True)

            for strength, sym, prev, today in candidates:
                if len(positions) >= MAX_POSITIONS:
                    break

                raw_entry = float(today["open"])
                entry_px = raw_entry * (1.0 + SLIPPAGE_RATE)
                atr_val = float(prev["atr14"])
                init_risk_prs = atr_mult * atr_val
                if init_risk_prs <= 0 or entry_px <= init_risk_prs:
                    continue

                stop_px = entry_px - init_risk_prs
                dollar_risk = nav_at_open * RISK_PER_TRADE
                qty = min(
                    dollar_risk / init_risk_prs,
                    (nav_at_open * 0.40) / entry_px,
                    (cash / (1.0 + FEE_RATE)) / entry_px
                )
                if qty <= 0:
                    continue

                entry_notional = qty * entry_px
                entry_fee = entry_notional * FEE_RATE
                if (entry_notional + entry_fee) > cash:
                    continue

                cash -= (entry_notional + entry_fee)
                positions[sym] = {
                    "entry_dt": dt,
                    "entry_px": entry_px,
                    "entry_notional": entry_notional,
                    "entry_fee": entry_fee,
                    "qty": qty,
                    "initial_risk_prs": init_risk_prs,
                    "stop_px": stop_px,
                    "last_px": float(today["open"])
                }

        # Phase 2: Intraday Stops
        intraday_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            if float(row["low"]) <= pos["stop_px"]:
                exit_px = pos["stop_px"] * (1.0 - SLIPPAGE_RATE)
                gross = pos["qty"] * exit_px
                fee = gross * FEE_RATE
                net = gross - fee
                cash += net
                pnl = net - (pos["entry_notional"] + pos["entry_fee"])
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                trade_logs.append({
                    "symbol": sym, "entry_dt": pos["entry_dt"], "exit_dt": dt,
                    "entry_px": pos["entry_px"], "exit_px": exit_px, "pnl": pnl,
                    "r": r_mult, "win": pnl > 0, "reason": "atr_stop"
                })
                intraday_closed.append(sym)

        for sym in intraday_closed:
            del positions[sym]

        # Phase 3: Close MTM
        nav_at_close = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            if dt in df.index:
                row = df.loc[dt]
                close_px = float(row["close"])
                pos["last_px"] = close_px
                atr_now = row["atr14"]
                if pd.notna(atr_now):
                    cand_stop = close_px - (atr_mult * float(atr_now))
                    if cand_stop > pos["stop_px"]:
                        pos["stop_px"] = cand_stop
                nav_at_close += pos["qty"] * close_px
            else:
                nav_at_close += pos["qty"] * pos["last_px"]

        equity_curve.append((dt, nav_at_close))

    # Phase 4: Terminal Mark
    terminal_dt = equity_curve[-1][0]
    for sym in list(positions.keys()):
        pos = positions[sym]
        exit_px = pos["last_px"] * (1.0 - SLIPPAGE_RATE)
        gross = pos["qty"] * exit_px
        net = gross - (gross * FEE_RATE)
        cash += net
        pnl = net - (pos["entry_notional"] + pos["entry_fee"])
        init_risk = pos["qty"] * pos["initial_risk_prs"]
        trade_logs.append({
            "symbol": sym, "entry_dt": pos["entry_dt"], "exit_dt": terminal_dt,
            "entry_px": pos["entry_px"], "exit_px": exit_px, "pnl": pnl,
            "r": pnl / init_risk if init_risk > 0 else 0.0, "win": pnl > 0, "reason": "terminal"
        })

    positions.clear()
    equity_curve[-1] = (terminal_dt, cash)

    return pd.DataFrame(trade_logs), equity_curve

def analyze_stitched_results(label, df_trades, equity_curve):
    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    eq = eq[~eq.index.duplicated(keep="last")]
    
    terminal_nav = float(eq.iloc[-1])
    total_trades = len(df_trades)
    
    elapsed_days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400.0
    cagr = (((terminal_nav / INITIAL_CAPITAL) ** (365.25 / elapsed_days)) - 1.0) * 100.0
    
    peak = eq.cummax()
    dd_series = (eq / peak) - 1.0
    max_dd = abs(float(dd_series.min())) * 100.0
    mar = cagr / max_dd if max_dd > 0 else 0.0

    wins = df_trades[df_trades["win"]]
    losses = df_trades[~df_trades["win"]]
    win_rate = (len(wins) / total_trades) * 100.0
    
    avg_win_r = wins["r"].mean() if not wins.empty else 0.0
    avg_loss_r = abs(losses["r"].mean()) if not losses.empty else 0.0
    expectancy = (win_rate/100.0 * avg_win_r) - ((1.0 - win_rate/100.0) * avg_loss_r)
    
    gross_p = wins["pnl"].sum() if not wins.empty else 0.0
    gross_l = abs(losses["pnl"].sum()) if not losses.empty else 0.0
    pf = (gross_p / gross_l) if gross_l > 0 else np.inf

    # Streak & cluster analysis
    df_trades["win_int"] = df_trades["win"].astype(int)
    loss_streaks = []
    current_streak = 0
    for w in df_trades["win"]:
        if not w:
            current_streak += 1
        else:
            if current_streak > 0:
                loss_streaks.append(current_streak)
            current_streak = 0
    if current_streak > 0:
        loss_streaks.append(current_streak)
    max_losing_streak = max(loss_streaks) if loss_streaks else 0

    worst_trade_r = df_trades["r"].min()
    df_trades["roll_5_pnl"] = df_trades["pnl"].rolling(5).sum()
    worst_5_seq_pnl = df_trades["roll_5_pnl"].min()

    print(f"\n" + "=" * 105)
    print(f" 📊 CONTINUOUS STITCHED OOS AUDIT: {label.upper()}")
    print("=" * 105)
    print(f"Calendar Window       : {eq.index[0].strftime('%Y-%m-%d')} to {eq.index[-1].strftime('%Y-%m-%d')} ({len(eq)} bars)")
    print(f"Terminal Equity       : ${terminal_nav:,.2f} (Net Return: {((terminal_nav/INITIAL_CAPITAL)-1)*100:.2f}%)")
    print(f"Annualized CAGR       : {cagr:.2f}%")
    print(f"Maximum Peak-to-Trough: {max_dd:.2f}%")
    print(f"Stitched MAR / Calmar : {mar:.3f}")
    print(f"Total Completed Trades: {total_trades} (Win Rate: {win_rate:.1f}%)")
    print(f"Profit Factor         : {pf:.2f}")
    print(f"Expectancy Per Trade  : {expectancy:.2f}R (Median R: {df_trades['r'].median():.2f}R)")
    print(f"Worst Single Trade    : {worst_trade_r:.2f}R")
    print(f"Max Consecutive Losses: {max_losing_streak} trades")
    print(f"Worst 5-Trade Sequence: ${worst_5_seq_pnl:,.2f}")

    # Top 5 Symbol Contributions
    sym_summary = df_trades.groupby("symbol")["pnl"].sum().sort_values(ascending=False)
    print("\n--- TOP & BOTTOM ASSET PNL CONTRIBUTORS ---")
    print("Top 3 Gainers :", ", ".join([f"{s} (${p:,.0f})" for s, p in sym_summary.head(3).items()]))
    print("Bottom 3 Losers:", ", ".join([f"{s} (${p:,.0f})" for s, p in sym_summary.tail(3).items()]))

    # Monthly Breakdown
    df_m = eq.resample("ME").last()
    df_m_ret = df_m.pct_change().dropna() * 100.0
    print("\n--- MONTHLY PERFORMANCE (%) ---")
    m_str = " | ".join([f"{idx.strftime('%Y-%m')}: {ret:+.1f}%" for idx, ret in df_m_ret.items()])
    print(m_str)

def main():
    manifest, dfs = load_data()
    btc_df = dfs["BTC/USD"]
    all_dates = btc_df.index[60:]
    
    # Strictly the stitched OOS timeline across Folds 1-4 (50% to 100%)
    split_idx = int(len(all_dates) * 0.50)
    oos_dates = all_dates[split_idx:]

    for label, liq, lb, atr in FROZEN_CANDIDATES:
        df_trades, eq_curve = simulate_continuous_oos(manifest, dfs, liq, lb, atr, oos_dates)
        analyze_stitched_results(label, df_trades, eq_curve)

if __name__ == "__main__":
    main()
