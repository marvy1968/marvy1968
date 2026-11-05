#!/usr/bin/env python3
"""
Universe Ablation & Concentration Audit Engine (G3-B5 Crypto Adaptation)
Tests Candidate Alpha and Beta across:
1. Full N=30 Universe (Control)
2. Mature N=24 Cohort (Only 719-bar ceiling assets)
3. Full Universe minus HYPE, PUMP, VVV (Exclusion test)
Includes deterministic Block-Bootstrap Monte Carlo (5,000 paths, seed=120826).
"""

import os
import pandas as pd
import numpy as np

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
MANIFEST_PATH = os.path.join(DATA_DIR, "universe_manifest.csv")

FEE_RATE = 0.0010      # 0.10%
SLIPPAGE_RATE = 0.0010 # 10 bps
MAX_POSITIONS = 3
INITIAL_CAPITAL = 100_000.0
RISK_PER_TRADE = 0.01

RNG_SEED = 120826
N_BOOTSTRAPS = 5000
BLOCK_SIZE_DAYS = 7

CANDIDATES = [
    ("Candidate Alpha", 250_000, 15, 2.0),
    ("Candidate Beta",  250_000, 30, 2.0),
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

def simulate_cohort(allowed_symbols, dfs, liq_floor, lookback, atr_mult, oos_dates):
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

                trade_logs.append({"symbol": sym, "pnl": pnl, "r": r_mult, "win": pnl > 0, "reason": reason})
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
            for sym in allowed_symbols:
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

                trade_logs.append({"symbol": sym, "pnl": pnl, "r": r_mult, "win": pnl > 0, "reason": "atr_stop"})
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
        trade_logs.append({"symbol": sym, "pnl": pnl, "r": pnl / init_risk if init_risk > 0 else 0.0, "win": pnl > 0, "reason": "terminal"})

    positions.clear()
    equity_curve[-1] = (terminal_dt, cash)

    return pd.DataFrame(trade_logs), equity_curve

def run_mc_bootstrap(equity_curve):
    rng = np.random.default_rng(RNG_SEED)
    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    daily_returns = eq.pct_change().dropna().values
    n_days = len(daily_returns)
    n_blocks = int(np.ceil(n_days / BLOCK_SIZE_DAYS))
    blocks = [daily_returns[i:i + BLOCK_SIZE_DAYS] for i in range(0, n_days - BLOCK_SIZE_DAYS + 1)]

    sim_returns = []
    sim_max_dds = []

    for _ in range(N_BOOTSTRAPS):
        chosen_indices = rng.choice(len(blocks), size=n_blocks, replace=True)
        synth_returns = np.concatenate([blocks[idx] for idx in chosen_indices])[:n_days]
        synth_nav = INITIAL_CAPITAL * np.cumprod(1.0 + synth_returns)
        peak = np.maximum.accumulate(synth_nav)
        dd = (synth_nav - peak) / peak

        sim_returns.append(((synth_nav[-1] / INITIAL_CAPITAL) - 1.0) * 100.0)
        sim_max_dds.append(abs(np.min(dd)) * 100.0)

    sim_returns = np.array(sim_returns)
    sim_max_dds = np.array(sim_max_dds)

    return {
        "p_profit": (sim_returns > 0.0).sum() / N_BOOTSTRAPS * 100.0,
        "p5_ret": np.percentile(sim_returns, 5),
        "med_ret": np.median(sim_returns),
        "mean_dd": np.mean(sim_max_dds),
        "p95_dd": np.percentile(sim_max_dds, 95),
        "p99_dd": np.percentile(sim_max_dds, 99)
    }

def main():
    print("=" * 115)
    print(" 🔬 FROZEN UNIVERSE ABLATION & CONCENTRATION AUDIT (CONTINUOUS OOS)")
    print("=" * 115)

    manifest, dfs = load_data()
    btc_df = dfs["BTC/USD"]
    all_dates = btc_df.index[60:]
    oos_dates = all_dates[int(len(all_dates) * 0.50):]

    # Universe Cohort Definitions
    all_symbols = manifest["symbol"].tolist()
    mature_symbols = manifest[manifest["hit_720_ceiling"]]["symbol"].tolist()
    ex_breadwinners = [s for s in all_symbols if s not in ["HYPE/USD", "PUMP/USD", "VVV/USD"]]

    cohorts = [
        ("1. Full N=30 Universe (Control)", all_symbols),
        ("2. Mature N=24 Cohort (719-bar Ceiling)", mature_symbols),
        ("3. Exclusion N=27 (No HYPE/PUMP/VVV)", ex_breadwinners)
    ]

    for cand_name, liq, lb, atr in CANDIDATES:
        print(f"\n" + "=" * 115)
        print(f" 🎯 CANDIDATE: {cand_name.upper()} (Lookback={lb}d, ATR={atr}x, LiqFloor=${liq/1e6:.2f}M)")
        print("=" * 115)

        rows = []
        for cohort_name, sym_list in cohorts:
            df_trades, eq_curve = simulate_cohort(sym_list, dfs, liq, lb, atr, oos_dates)
            
            eq = pd.Series([x[1] for x in eq_curve], index=[x[0] for x in eq_curve], dtype=float)
            terminal_nav = float(eq.iloc[-1])
            net_ret = ((terminal_nav / INITIAL_CAPITAL) - 1.0) * 100.0
            elapsed_days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400.0
            cagr = (((terminal_nav / INITIAL_CAPITAL) ** (365.25 / elapsed_days)) - 1.0) * 100.0
            peak = eq.cummax()
            max_dd = abs(float(((eq / peak) - 1.0).min())) * 100.0
            mar = cagr / max_dd if max_dd > 0 else 0.0

            total_trades = len(df_trades)
            wins = df_trades[df_trades["win"]]
            losses = df_trades[~df_trades["win"]]
            win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
            
            avg_w = wins["r"].mean() if not wins.empty else 0.0
            avg_l = abs(losses["r"].mean()) if not losses.empty else 0.0
            exp_r = (win_rate/100.0 * avg_w) - ((1.0 - win_rate/100.0) * avg_l)
            gross_p = wins["pnl"].sum() if not wins.empty else 0.0
            gross_l = abs(losses["pnl"].sum()) if not losses.empty else 0.0
            pf = (gross_p / gross_l) if gross_l > 0 else np.nan

            mc = run_mc_bootstrap(eq_curve)

            rows.append({
                "Universe Cohort": cohort_name,
                "Symbols": len(sym_list),
                "OOS Ret (%)": f"{net_ret:+.2f}%",
                "OOS MaxDD": f"{max_dd:.2f}%",
                "MAR": f"{mar:.3f}",
                "PF": f"{pf:.2f}",
                "Exp (R)": f"{exp_r:+.2f}R",
                "Trades": total_trades,
                "WinRate": f"{win_rate:.1f}%",
                "MC P(Profit)": f"{mc['p_profit']:.1f}%",
                "MC Med Ret": f"{mc['med_ret']:+.2f}%",
                "MC p5 Ret": f"{mc['p5_ret']:+.2f}%",
                "MC p95 DD": f"{mc['p95_dd']:.2f}%"
            })

        df_out = pd.DataFrame(rows)
        print(df_out.to_string(index=False))

if __name__ == "__main__":
    main()
