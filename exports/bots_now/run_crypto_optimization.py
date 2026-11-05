#!/usr/bin/env python3
"""
Track A Chronologically Certified Optimization Engine (G3-B5 Crypto Adaptation)
Enforces strict 4-phase intra-bar chronological event ordering:
Phase 1: Open Exits (Macro & Gap) -> Mark Open NAV -> Open Entries
Phase 2: Intraday Stops (Old + New Positions)
Phase 3: Close MTM & Ratchet Updates for t+1
"""

import os
import itertools
import pandas as pd
import numpy as np

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
MANIFEST_PATH = os.path.join(DATA_DIR, "universe_manifest.csv")

# =====================================================================
# 540-COMBINATION GRID DEFINITIONS
# =====================================================================
LIQUIDITY_CUTOFFS = [250_000, 500_000, 1_000_000, 2_000_000, 5_000_000] # 5
DONCHIAN_LOOKBACKS = [15, 20, 30, 55]                                    # 4
ATR_MULTIPLIERS = [2.0, 2.5, 3.0]                                        # 3
FEE_TIERS = [0.0006, 0.0010, 0.0020]                                     # 3 (0.06%, 0.10%, 0.20%)
SLIPPAGE_TIERS = [0.0005, 0.0010, 0.0020]                                # 3 (5 bps, 10 bps, 20 bps)

MAX_POSITIONS = 3
INITIAL_CAPITAL = 100_000.0
RISK_PER_TRADE = 0.01  # 1.0% R-risk

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
            np.maximum(
                (df["high"] - df["close"].shift(1)).abs(),
                (df["low"] - df["close"].shift(1)).abs()
            )
        )
        df["atr14"] = df["tr"].rolling(14).mean()
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["sma50"] = df["close"].rolling(50).mean()

        for lb in DONCHIAN_LOOKBACKS:
            # Shifted Donchian channel: highest high of past N days EXCLUDING current day
            df[f"donch_high_{lb}"] = df["high"].shift(1).rolling(lb).max()

        # Pure t-1 point-in-time liquidity (no double-shift)
        df["quote_volume"] = df["close"] * df["volume"]
        df["med_vol_30d"] = df["quote_volume"].rolling(30, min_periods=30).median()
        dfs[symbol] = df

    return manifest, dfs

def simulate_engine(manifest, dfs, liq_floor, lookback, atr_mult, fee_rate, slippage_rate):
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

        # Phase 0: Signals derived strictly from completed t-1 bar
        macro_bull = bool(btc_prev["close"] > btc_prev["ema20"] > btc_prev["sma50"])

        # =========================================================
        # PHASE 1: OPENING EXECUTION (00:00 UTC)
        # =========================================================
        # 1A. Open Exits (Macro Risk-Off & Gap-Through Stops)
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
                exit_fee = gross * fee_rate
                net = gross - exit_fee
                cash += net

                total_cost = pos["entry_notional"] + pos["entry_fee"]
                pnl = net - total_cost
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                trade_logs.append({
                    "symbol": sym,
                    "entry_dt": pos["entry_dt"],
                    "exit_dt": dt,
                    "entry_px": pos["entry_px"],
                    "exit_px": exit_px,
                    "pnl": pnl,
                    "r": r_mult,
                    "win": pnl > 0,
                    "reason": reason
                })
                open_closed.append(sym)

        for sym in open_closed:
            del positions[sym]

        # 1B. Mark Open NAV (Cash + Open valuations of remaining positions)
        nav_at_open = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            px = float(df.loc[dt, "open"]) if dt in df.index else pos["last_px"]
            nav_at_open += pos["qty"] * px

        # 1C. Submit Opening Entries
        newly_opened = []
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

                required = [
                    prev.get("med_vol_30d"),
                    prev.get(f"donch_high_{lookback}"),
                    prev.get("atr14"),
                ]
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
                atr_value = float(prev["atr14"])

                initial_risk_prs = atr_mult * atr_value
                if initial_risk_prs <= 0 or entry_px <= initial_risk_prs:
                    continue

                stop_px = entry_px - initial_risk_prs
                dollar_risk = nav_at_open * RISK_PER_TRADE
                qty_by_risk = dollar_risk / initial_risk_prs
                max_pos_notional = nav_at_open * 0.40
                qty_by_cap = max_pos_notional / entry_px
                max_afford_notional = cash / (1.0 + fee_rate)
                qty_by_cash = max_afford_notional / entry_px

                qty = min(qty_by_risk, qty_by_cap, qty_by_cash)
                if qty <= 0:
                    continue

                entry_notional = qty * entry_px
                entry_fee = entry_notional * fee_rate
                total_cash_req = entry_notional + entry_fee

                if total_cash_req > cash:
                    continue

                cash -= total_cash_req
                pos_record = {
                    "entry_dt": dt,
                    "entry_px": entry_px,
                    "entry_notional": entry_notional,
                    "entry_fee": entry_fee,
                    "qty": qty,
                    "initial_risk_prs": initial_risk_prs,
                    "stop_px": stop_px,
                    "last_px": float(today["open"]),
                }
                positions[sym] = pos_record
                newly_opened.append(sym)

        # =========================================================
        # PHASE 2: INTRADAY EXECUTION (High / Low)
        # =========================================================
        # Intraday stops evaluate across BOTH pre-existing and newly opened positions
        intraday_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            low_px = float(row["low"])

            if low_px <= pos["stop_px"]:
                exit_px = pos["stop_px"] * (1.0 - slippage_rate)
                gross = pos["qty"] * exit_px
                exit_fee = gross * fee_rate
                net = gross - exit_fee
                cash += net

                total_cost = pos["entry_notional"] + pos["entry_fee"]
                pnl = net - total_cost
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                trade_logs.append({
                    "symbol": sym,
                    "entry_dt": pos["entry_dt"],
                    "exit_dt": dt,
                    "entry_px": pos["entry_px"],
                    "exit_px": exit_px,
                    "pnl": pnl,
                    "r": r_mult,
                    "win": pnl > 0,
                    "reason": "atr_stop"
                })
                intraday_closed.append(sym)

        for sym in intraday_closed:
            del positions[sym]

        # =========================================================
        # PHASE 3: CLOSE MTM & TRAILING UPDATES FOR t+1
        # =========================================================
        nav_at_close = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            if dt in df.index:
                row = df.loc[dt]
                close_px = float(row["close"])
                pos["last_px"] = close_px
                atr_now = row["atr14"]

                # Ratchet trailing stop at close for session t+1
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

    # =============================================================
    # PHASE 4: TERMINAL LIQUIDATION
    # =============================================================
    terminal_dt = equity_curve[-1][0]
    for sym in list(positions.keys()):
        pos = positions[sym]
        exit_px = pos["last_px"] * (1.0 - slippage_rate)
        gross = pos["qty"] * exit_px
        exit_fee = gross * fee_rate
        net = gross - exit_fee
        cash += net

        total_cost = pos["entry_notional"] + pos["entry_fee"]
        pnl = net - total_cost
        init_risk = pos["qty"] * pos["initial_risk_prs"]

        trade_logs.append({
            "symbol": sym,
            "entry_dt": pos["entry_dt"],
            "exit_dt": terminal_dt,
            "entry_px": pos["entry_px"],
            "exit_px": exit_px,
            "pnl": pnl,
            "r": pnl / init_risk if init_risk > 0 else 0.0,
            "win": pnl > 0,
            "reason": "terminal",
        })

    positions.clear()
    equity_curve[-1] = (terminal_dt, cash)

    # =============================================================
    # PHASE 5: PERFORMANCE & DRAWDOWN COMPUTATION
    # =============================================================
    total_trades = len(trade_logs)
    if total_trades < 10:
        return None

    eq = pd.Series(
        [x[1] for x in equity_curve],
        index=[x[0] for x in equity_curve],
        dtype=float,
    )
    eq = eq[~eq.index.duplicated(keep="last")]

    peak = eq.cummax()
    drawdown = (eq / peak) - 1.0
    max_dd = abs(float(drawdown.min()))

    elapsed_days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400.0, 1.0)
    years = elapsed_days / 365.25
    terminal_nav = float(eq.iloc[-1])

    cagr = ((terminal_nav / INITIAL_CAPITAL) ** (1.0 / years) - 1.0) if terminal_nav > 0 else -1.0

    wins = [t for t in trade_logs if t["pnl"] > 0]
    losses = [t for t in trade_logs if t["pnl"] <= 0]
    win_rate = len(wins) / total_trades

    avg_win_r = np.mean([t["r"] for t in wins]) if wins else 0.0
    avg_loss_r = abs(np.mean([t["r"] for t in losses])) if losses else 0.0
    payoff_ratio = (avg_win_r / avg_loss_r) if avg_loss_r > 0 else np.nan
    expectancy_r = (win_rate * avg_win_r) - ((1.0 - win_rate) * avg_loss_r)
    mar = (cagr / max_dd) if max_dd > 0 else np.nan

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else np.inf

    return {
        "liq_floor_m": liq_floor / 1_000_000.0,
        "lookback_d": lookback,
        "atr_mult": atr_mult,
        "fee_pct": fee_rate * 100.0,
        "slippage_bps": slippage_rate * 10_000.0,
        "terminal_nav": round(terminal_nav, 2),
        "cagr_pct": round(cagr * 100.0, 2),
        "max_dd_pct": round(max_dd * 100.0, 2),
        "mar_ratio": round(mar, 3),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate * 100.0, 2),
        "payoff_ratio": round(payoff_ratio, 2),
        "expectancy_r": round(expectancy_r, 3),
        "profit_factor": round(profit_factor, 2),
    }

def main():
    print("=" * 105)
    print(" 🚀 RUNNING CHRONOLOGICALLY CERTIFIED OPTIMIZATION GRID (540 COMBINATIONS)")
    print("=" * 105)

    manifest, dfs = load_data()
    combinations = list(itertools.product(
        LIQUIDITY_CUTOFFS, DONCHIAN_LOOKBACKS, ATR_MULTIPLIERS, FEE_TIERS, SLIPPAGE_TIERS
    ))
    print(f"Loaded {len(dfs)} assets across {len(manifest)} manifest records.")
    print(f"Sweeping {len(combinations)} parameter permutations...")

    results = []
    for liq, lb, atr, fee, slip in combinations:
        res = simulate_engine(manifest, dfs, liq, lb, atr, fee, slip)
        if res:
            results.append(res)

    df_res = pd.DataFrame(results)
    out_csv = os.path.join(DATA_DIR, "optimization_results_540_grid.csv")
    df_res.to_csv(out_csv, index=False)

    print("\n" + "=" * 105)
    print(" 🏆 TOP 15 PARAMETER CONFIGURATIONS (RANKED BY MAR RATIO)")
    print("=" * 105)
    top_mar = df_res.sort_values(by="mar_ratio", ascending=False).head(15)
    cols = ["liq_floor_m", "lookback_d", "atr_mult", "fee_pct", "slippage_bps", "cagr_pct", "max_dd_pct", "mar_ratio", "win_rate_pct", "expectancy_r", "profit_factor", "total_trades"]
    print(top_mar[cols].to_string(index=False))

    print("\n" + "=" * 105)
    print(" 🛡️ FRICTION STRESS MATRIX (MEAN MAR BY FEE & SLIPPAGE)")
    print("=" * 105)
    pivot = df_res.pivot_table(index="fee_pct", columns="slippage_bps", values="mar_ratio", aggfunc="mean")
    print(pivot.round(3).to_string())

if __name__ == "__main__":
    main()
