#!/usr/bin/env python3
"""
Anchored Multi-Fold Walk-Forward Stability Engine (4 Chronological Folds)
Evaluates out-of-sample parameter robustness across multiple expanding-window cycles.
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

TARGET_STRUCTURES = [
    ("Candidate Alpha", 250_000, 15, 2.0),
    ("Candidate Beta", 250_000, 30, 2.0),
    ("Candidate Gamma", 500_000, 15, 2.0),
    ("Candidate Delta", 2_000_000, 30, 2.0)
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

        for lb in [15, 20, 30, 55]:
            df[f"donch_high_{lb}"] = df["high"].shift(1).rolling(lb).max()

        df["quote_volume"] = df["close"] * df["volume"]
        df["med_vol_30d"] = df["quote_volume"].rolling(30, min_periods=30).median()
        dfs[symbol] = df

    return manifest, dfs

def simulate_window(manifest, dfs, liq_floor, lookback, atr_mult, dates):
    btc_df = dfs["BTC/USD"]
    cash = INITIAL_CAPITAL
    positions = {}
    trade_logs = []
    equity_curve = []

    for i in range(1, len(dates)):
        dt = dates[i]
        prev_dt = dates[i - 1]

        if prev_dt not in btc_df.index or dt not in btc_df.index:
            continue

        btc_prev = btc_df.loc[prev_dt]
        if pd.isna(btc_prev["ema20"]) or pd.isna(btc_prev["sma50"]):
            continue

        macro_bull = bool(btc_prev["close"] > btc_prev["ema20"] > btc_prev["sma50"])

        open_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            open_px = float(row["open"])

            exit_px = None
            if not macro_bull:
                exit_px = open_px * (1.0 - SLIPPAGE_RATE)
            elif open_px <= pos["stop_px"]:
                exit_px = open_px * (1.0 - SLIPPAGE_RATE)

            if exit_px is not None:
                gross = pos["qty"] * exit_px
                net = gross - (gross * FEE_RATE)
                cash += net
                pnl = net - (pos["entry_notional"] + pos["entry_fee"])
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                trade_logs.append({"pnl": pnl, "r": pnl / init_risk if init_risk > 0 else 0.0, "win": pnl > 0})
                open_closed.append(sym)

        for sym in open_closed:
            del positions[sym]

        nav_at_open = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            px = float(df.loc[dt, "open"]) if dt in df.index else pos["last_px"]
            nav_at_open += pos["qty"] * px

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

        intraday_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            if float(row["low"]) <= pos["stop_px"]:
                exit_px = pos["stop_px"] * (1.0 - SLIPPAGE_RATE)
                gross = pos["qty"] * exit_px
                net = gross - (gross * FEE_RATE)
                cash += net
                pnl = net - (pos["entry_notional"] + pos["entry_fee"])
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                trade_logs.append({"pnl": pnl, "r": pnl / init_risk if init_risk > 0 else 0.0, "win": pnl > 0})
                intraday_closed.append(sym)

        for sym in intraday_closed:
            del positions[sym]

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

    if not equity_curve:
        return None

    terminal_dt = equity_curve[-1][0]
    for sym in list(positions.keys()):
        pos = positions[sym]
        exit_px = pos["last_px"] * (1.0 - SLIPPAGE_RATE)
        gross = pos["qty"] * exit_px
        net = gross - (gross * FEE_RATE)
        cash += net
        pnl = net - (pos["entry_notional"] + pos["entry_fee"])
        init_risk = pos["qty"] * pos["initial_risk_prs"]
        trade_logs.append({"pnl": pnl, "r": pnl / init_risk if init_risk > 0 else 0.0, "win": pnl > 0})

    positions.clear()
    equity_curve[-1] = (terminal_dt, cash)

    total_trades = len(trade_logs)
    if total_trades == 0:
        return {"cagr": 0.0, "max_dd": 0.0, "mar": 0.0, "trades": 0, "win_rate": 0.0, "expectancy": 0.0}

    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    peak = eq.cummax()
    max_dd = abs(float(((eq / peak) - 1.0).min())) * 100.0
    elapsed_days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400.0, 1.0)
    cagr = (((float(eq.iloc[-1]) / INITIAL_CAPITAL) ** (365.25 / elapsed_days)) - 1.0) * 100.0

    wins = [t for t in trade_logs if t["win"]]
    losses = [t for t in trade_logs if not t["win"]]
    win_rate = (len(wins) / total_trades) * 100.0
    avg_win_r = np.mean([t["r"] for t in wins]) if wins else 0.0
    avg_loss_r = abs(np.mean([t["r"] for t in losses])) if losses else 0.0
    expectancy = (win_rate/100.0 * avg_win_r) - ((1.0 - win_rate/100.0) * avg_loss_r)
    mar = cagr / max_dd if max_dd > 0 else 0.0

    return {
        "cagr": round(cagr, 2),
        "max_dd": round(max_dd, 2),
        "mar": round(mar, 3),
        "trades": total_trades,
        "win_rate": round(win_rate, 2),
        "expectancy": round(expectancy, 3)
    }

def main():
    print("=" * 115)
    print(" 🔄 ANCHORED MULTI-FOLD WALK-FORWARD STABILITY (4 EXPANDING CYCLES)")
    print("=" * 115)

    manifest, dfs = load_data()
    btc_df = dfs["BTC/USD"]
    all_dates = btc_df.index[60:]
    n_bars = len(all_dates)

    # 4 Expanding Folds
    folds = [
        ("Fold 1 (Early 2025)", all_dates[:int(n_bars * 0.50)], all_dates[int(n_bars * 0.50):int(n_bars * 0.65)]),
        ("Fold 2 (Mid 2025)", all_dates[:int(n_bars * 0.65)], all_dates[int(n_bars * 0.65):int(n_bars * 0.80)]),
        ("Fold 3 (Late 2025 / Early 2026)", all_dates[:int(n_bars * 0.80)], all_dates[int(n_bars * 0.80):int(n_bars * 0.90)]),
        ("Fold 4 (Modern 2026)", all_dates[:int(n_bars * 0.90)], all_dates[int(n_bars * 0.90):])
    ]

    for label, is_d, oos_d in folds:
        print(f"\n--- {label.upper()} ---")
        print(f"Train Window: {is_d[0].strftime('%Y-%m-%d')} to {is_d[-1].strftime('%Y-%m-%d')} ({len(is_d)} bars)")
        print(f"Test Window : {oos_d[0].strftime('%Y-%m-%d')} to {oos_d[-1].strftime('%Y-%m-%d')} ({len(oos_d)} bars)")

        fold_rows = []
        for name, liq, lb, atr in TARGET_STRUCTURES:
            res_is = simulate_window(manifest, dfs, liq, lb, atr, is_d)
            res_oos = simulate_window(manifest, dfs, liq, lb, atr, oos_d)

            wfe = (res_oos["mar"] / res_is["mar"]) if res_is["mar"] > 0 else 0.0

            fold_rows.append({
                "Candidate": name,
                "IS MAR": res_is["mar"],
                "IS CAGR": f"{res_is['cagr']:.1f}%",
                "OOS MAR": res_oos["mar"],
                "OOS CAGR": f"{res_oos['cagr']:.1f}%",
                "OOS MaxDD": f"{res_oos['max_dd']:.1f}%",
                "OOS Exp (R)": f"{res_oos['expectancy']:.2f}R",
                "OOS Trades": res_oos["trades"],
                "WFE": round(wfe, 2)
            })

        df_fold = pd.DataFrame(fold_rows)
        print(df_fold.to_string(index=False))

if __name__ == "__main__":
    main()
