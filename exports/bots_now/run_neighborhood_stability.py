#!/usr/bin/env python3
"""
Parameter Neighborhood Stability Engine
Maps the 2D surface across granular Donchian lookbacks and ATR multipliers
to verify plateau smoothness (ruling out overfit parameter spikes).
"""

import os
import pandas as pd
import numpy as np

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
MANIFEST_PATH = os.path.join(DATA_DIR, "universe_manifest.csv")

DONCHIAN_EXTENDED = [10, 15, 20, 25, 30, 40, 55]
ATR_EXTENDED = [1.5, 2.0, 2.5, 3.0, 3.5]
LIQUIDITY_FLOOR = 250_000
FEE_RATE = 0.0010      # 0.10%
SLIPPAGE_RATE = 0.0010 # 10 bps
INITIAL_CAPITAL = 100_000.0
RISK_PER_TRADE = 0.01

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

        for lb in DONCHIAN_EXTENDED:
            df[f"donch_high_{lb}"] = df["high"].shift(1).rolling(lb).max()

        df["quote_volume"] = df["close"] * df["volume"]
        df["med_vol_30d"] = df["quote_volume"].rolling(30, min_periods=30).median()
        dfs[symbol] = df

    return manifest, dfs

def simulate(manifest, dfs, lookback, atr_mult):
    btc_df = dfs["BTC/USD"]
    dates = btc_df.index[60:]
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

        # Phase 1: Open Exits
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
                trade_logs.append({"pnl": pnl, "r": pnl / init_risk if init_risk > 0 else 0.0})
                open_closed.append(sym)

        for sym in open_closed:
            del positions[sym]

        nav_at_open = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            px = float(df.loc[dt, "open"]) if dt in df.index else pos["last_px"]
            nav_at_open += pos["qty"] * px

        # Phase 1C: Open Entries
        if macro_bull and len(positions) < 3:
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
                if any(pd.isna(x) for x in req) or float(prev["med_vol_30d"]) < LIQUIDITY_FLOOR:
                    continue

                breakout_level = float(prev[f"donch_high_{lookback}"])
                if float(prev["close"]) > breakout_level:
                    strength = (float(prev["close"]) / breakout_level) - 1.0
                    candidates.append((strength, sym, prev, today))

            candidates.sort(key=lambda x: x[0], reverse=True)

            for strength, sym, prev, today in candidates:
                if len(positions) >= 3:
                    break

                raw_entry = float(today["open"])
                entry_px = raw_entry * (1.0 + SLIPPAGE_RATE)
                atr_val = float(prev["atr14"])
                init_risk_prs = atr_mult * atr_val
                if init_risk_prs <= 0 or entry_px <= init_risk_prs:
                    continue

                stop_px = entry_px - init_risk_prs
                dollar_risk = nav_at_open * RISK_PER_TRADE
                qty_by_risk = dollar_risk / init_risk_prs
                qty_by_cap = (nav_at_open * 0.40) / entry_px
                qty_by_cash = (cash / (1.0 + FEE_RATE)) / entry_px

                qty = min(qty_by_risk, qty_by_cap, qty_by_cash)
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
                net = gross - (gross * FEE_RATE)
                cash += net
                pnl = net - (pos["entry_notional"] + pos["entry_fee"])
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                trade_logs.append({"pnl": pnl, "r": pnl / init_risk if init_risk > 0 else 0.0})
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

    if not equity_curve or len(trade_logs) < 10:
        return None

    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    peak = eq.cummax()
    max_dd = abs(float(((eq / peak) - 1.0).min())) * 100.0
    elapsed_days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400.0, 1.0)
    cagr = (((float(eq.iloc[-1]) / INITIAL_CAPITAL) ** (365.25 / elapsed_days)) - 1.0) * 100.0
    mar = cagr / max_dd if max_dd > 0 else 0.0

    return {"cagr": cagr, "max_dd": max_dd, "mar": mar, "trades": len(trade_logs)}

def main():
    print("=" * 95)
    print(" 🗺️ PARAMETER NEIGHBORHOOD STABILITY HEATMAP (MAR RATIO)")
    print("=" * 95)

    manifest, dfs = load_data()
    matrix_mar = pd.DataFrame(index=DONCHIAN_EXTENDED, columns=ATR_EXTENDED)
    matrix_cagr = pd.DataFrame(index=DONCHIAN_EXTENDED, columns=ATR_EXTENDED)

    for lb in DONCHIAN_EXTENDED:
        for atr in ATR_EXTENDED:
            res = simulate(manifest, dfs, lb, atr)
            if res:
                matrix_mar.loc[lb, atr] = round(res["mar"], 2)
                matrix_cagr.loc[lb, atr] = round(res["cagr"], 1)
            else:
                matrix_mar.loc[lb, atr] = np.nan
                matrix_cagr.loc[lb, atr] = np.nan

    print("\n--- MAR RATIO SURFACE (Lookback Rows × ATR Multiplier Cols) ---")
    print(matrix_mar.to_string())

    print("\n--- CAGR (%) SURFACE (Lookback Rows × ATR Multiplier Cols) ---")
    print(matrix_cagr.to_string())

if __name__ == "__main__":
    main()
