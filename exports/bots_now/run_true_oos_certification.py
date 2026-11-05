#!/usr/bin/env python3
"""
True Out-of-Sample (OOS) Certification Engine (G3-B5 Crypto Adaptation)
1. Runs full 540-grid parameter optimization strictly on In-Sample (IS) window (first 60%).
2. Identifies and freezes top IS configurations.
3. Evaluates frozen models on the unseen 40% OOS Holdout window exactly once.
"""

import os
import itertools
import pandas as pd
import numpy as np

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
MANIFEST_PATH = os.path.join(DATA_DIR, "universe_manifest.csv")

# =====================================================================
# GRID DEFINITIONS & RISK CONFIGURATION
# =====================================================================
LIQUIDITY_CUTOFFS = [250_000, 500_000, 1_000_000, 2_000_000, 5_000_000] # 5
DONCHIAN_LOOKBACKS = [15, 20, 30, 55]                                    # 4
ATR_MULTIPLIERS = [2.0, 2.5, 3.0]                                        # 3
FEE_TIERS = [0.0006, 0.0010, 0.0020]                                     # 3 (0.06%, 0.10%, 0.20%)
SLIPPAGE_TIERS = [0.0005, 0.0010, 0.0020]                                # 3 (5 bps, 10 bps, 20 bps)

IS_RATIO = 0.60
MAX_POSITIONS = 3
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

        for lb in DONCHIAN_LOOKBACKS:
            df[f"donch_high_{lb}"] = df["high"].shift(1).rolling(lb).max()

        df["quote_volume"] = df["close"] * df["volume"]
        df["med_vol_30d"] = df["quote_volume"].rolling(30, min_periods=30).median()
        dfs[symbol] = df

    return manifest, dfs

def simulate_segment(manifest, dfs, liq_floor, lookback, atr_mult, fee_rate, slippage_rate, dates):
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

        # Phase 1A: Open Exits (Macro Risk-Off & Gap Stops)
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
                exit_px = open_px * (1.0 - slippage_rate)
                reason = "macro_exit"
            elif open_px <= pos["stop_px"]:
                exit_px = open_px * (1.0 - slippage_rate)
                reason = "gap_stop"

            if exit_px is not None:
                gross = pos["qty"] * exit_px
                fee = gross * fee_rate
                net = gross - fee
                cash += net
                total_cost = pos["entry_notional"] + pos["entry_fee"]
                pnl = net - total_cost
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                trade_logs.append({"symbol": sym, "pnl": pnl, "r": r_mult, "win": pnl > 0, "reason": reason})
                open_closed.append(sym)

        for sym in open_closed:
            del positions[sym]

        # Phase 1B: Mark NAV at Open
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

                required = [prev.get("med_vol_30d"), prev.get(f"donch_high_{lookback}"), prev.get("atr14")]
                if any(pd.isna(x) for x in required):
                    continue
                if float(prev["med_vol_30d"]) < liq_floor:
                    continue

                breakout_level = float(prev[f"donch_high_{lookback}"])
                if float(prev["close"]) <= breakout_level:
                    continue

                strength = (float(prev["close"]) / breakout_level) - 1.0
                candidates.append((strength, sym, prev, today))

            candidates.sort(key=lambda x: x[0], reverse=True)

            for strength, sym, prev, today in candidates:
                if len(positions) >= MAX_POSITIONS:
                    break

                raw_entry = float(today["open"])
                entry_px = raw_entry * (1.0 + slippage_rate)
                atr_val = float(prev["atr14"])
                init_risk_prs = atr_mult * atr_val

                if init_risk_prs <= 0 or entry_px <= init_risk_prs:
                    continue

                stop_px = entry_px - init_risk_prs
                dollar_risk = nav_at_open * RISK_PER_TRADE
                qty_by_risk = dollar_risk / init_risk_prs
                qty_by_cap = (nav_at_open * 0.40) / entry_px
                qty_by_cash = (cash / (1.0 + fee_rate)) / entry_px

                qty = min(qty_by_risk, qty_by_cap, qty_by_cash)
                if qty <= 0:
                    continue

                entry_notional = qty * entry_px
                entry_fee = entry_notional * fee_rate
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

        # Phase 2: Intraday Stops (Old + New Positions)
        intraday_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            if float(row["low"]) <= pos["stop_px"]:
                exit_px = pos["stop_px"] * (1.0 - slippage_rate)
                gross = pos["qty"] * exit_px
                fee = gross * fee_rate
                net = gross - fee
                cash += net
                total_cost = pos["entry_notional"] + pos["entry_fee"]
                pnl = net - total_cost
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                trade_logs.append({"symbol": sym, "pnl": pnl, "r": r_mult, "win": pnl > 0, "reason": "atr_stop"})
                intraday_closed.append(sym)

        for sym in intraday_closed:
            del positions[sym]

        # Phase 3: Close MTM & Stop Ratchet for t+1
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
    if not equity_curve:
        return None

    terminal_dt = equity_curve[-1][0]
    for sym in list(positions.keys()):
        pos = positions[sym]
        exit_px = pos["last_px"] * (1.0 - slippage_rate)
        gross = pos["qty"] * exit_px
        net = gross - (gross * fee_rate)
        cash += net
        total_cost = pos["entry_notional"] + pos["entry_fee"]
        pnl = net - total_cost
        init_risk = pos["qty"] * pos["initial_risk_prs"]
        trade_logs.append({"symbol": sym, "pnl": pnl, "r": pnl / init_risk if init_risk > 0 else 0.0, "win": pnl > 0, "reason": "terminal"})

    positions.clear()
    equity_curve[-1] = (terminal_dt, cash)

    total_trades = len(trade_logs)
    if total_trades == 0:
        return {"cagr": 0.0, "max_dd": 0.0, "mar": 0.0, "trades": 0, "win_rate": 0.0, "expectancy": 0.0, "pf": 0.0}

    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    eq = eq[~eq.index.duplicated(keep="last")]
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

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else np.inf

    return {
        "cagr": round(cagr, 2),
        "max_dd": round(max_dd, 2),
        "mar": round(mar, 3),
        "trades": total_trades,
        "win_rate": round(win_rate, 2),
        "expectancy": round(expectancy, 3),
        "pf": round(pf, 2)
    }

def main():
    print("=" * 115)
    print(" 🏛️ RIGOROUS OUT-OF-SAMPLE (OOS) CERTIFICATION ENGINE (NO SELECTION LEAKAGE)")
    print("=" * 115)

    manifest, dfs = load_data()
    btc_df = dfs["BTC/USD"]
    all_dates = btc_df.index[60:]
    split_idx = int(len(all_dates) * IS_RATIO)

    is_dates = all_dates[:split_idx]
    oos_dates = all_dates[split_idx:]

    print(f"Full Available Timeline : {all_dates[0].strftime('%Y-%m-%d')} to {all_dates[-1].strftime('%Y-%m-%d')} ({len(all_dates)} bars)")
    print(f"In-Sample Training (60%): {is_dates[0].strftime('%Y-%m-%d')} to {is_dates[-1].strftime('%Y-%m-%d')} ({len(is_dates)} bars)")
    print(f"Out-of-Sample Gate (40%): {oos_dates[0].strftime('%Y-%m-%d')} to {oos_dates[-1].strftime('%Y-%m-%d')} ({len(oos_dates)} bars)")
    print("-" * 115)

    # STEP 1: Run complete 540-grid strictly on IS training set
    combinations = list(itertools.product(
        LIQUIDITY_CUTOFFS, DONCHIAN_LOOKBACKS, ATR_MULTIPLIERS, FEE_TIERS, SLIPPAGE_TIERS
    ))
    print(f"Sweeping {len(combinations)} parameter permutations STRICTLY across In-Sample training data...")

    is_results = []
    for liq, lb, atr, fee, slip in combinations:
        res = simulate_segment(manifest, dfs, liq, lb, atr, fee, slip, is_dates)
        if res and res["trades"] >= 8:  # Minimum statistical support in IS window
            is_results.append({
                "liq_floor": liq,
                "lookback": lb,
                "atr_mult": atr,
                "fee": fee,
                "slip": slip,
                **res
            })

    df_is = pd.DataFrame(is_results)
    
    # STEP 2: Isolate Top 10 IS Configurations (Ranked by In-Sample MAR)
    top_is = df_is.sort_values(by="mar", ascending=False).head(10).reset_index(drop=True)

    print("\n" + "=" * 115)
    print(" 🏆 TOP 10 IN-SAMPLE FROZEN CONFIGURATIONS (SELECTED BLIND OF OOS DATA)")
    print("=" * 115)
    cols_is = ["liq_floor", "lookback", "atr_mult", "fee", "slip", "cagr", "max_dd", "mar", "trades", "win_rate", "expectancy", "pf"]
    print(top_is[cols_is].to_string(index=False))

    # STEP 3: Unseal Out-of-Sample (OOS) Holdout and evaluate the frozen configs ONCE
    print("\n" + "=" * 115)
    print(" 🛡️ EVALUATING FROZEN CANDIDATES ON THE UNSEEN OUT-OF-SAMPLE (OOS) HOLDOUT")
    print("=" * 115)

    comparison_rows = []
    for idx, row in top_is.iterrows():
        # Evaluate on unseen OOS data
        res_oos = simulate_segment(
            manifest, dfs, int(row["liq_floor"]), int(row["lookback"]),
            float(row["atr_mult"]), float(row["fee"]), float(row["slip"]), oos_dates
        )

        wfe = (res_oos["mar"] / row["mar"]) if row["mar"] > 0 else 0.0

        comparison_rows.append({
            "Rank": idx + 1,
            "Params (Liq/LB/ATR)": f"${row['liq_floor']/1e6:.2f}M | {int(row['lookback'])}d | {row['atr_mult']}x",
            "Friction (Fee/Slip)": f"{row['fee']*100:.2f}% | {row['slip']*10000:.0f}bp",
            "IS CAGR": f"{row['cagr']:.1f}%",
            "IS MaxDD": f"{row['max_dd']:.1f}%",
            "IS MAR": row["mar"],
            "IS Trd": row["trades"],
            "OOS CAGR": f"{res_oos['cagr']:.1f}%",
            "OOS MaxDD": f"{res_oos['max_dd']:.1f}%",
            "OOS MAR": res_oos["mar"],
            "OOS Trd": res_oos["trades"],
            "OOS WR": f"{res_oos['win_rate']:.1f}%",
            "OOS Exp": f"{res_oos['expectancy']:.2f}R",
            "WFE": round(wfe, 2)
        })

    df_comp = pd.DataFrame(comparison_rows)
    print(df_comp.to_string(index=False))

if __name__ == "__main__":
    main()
