import itertools
from pathlib import Path
import pandas as pd
import numpy as np

DATA_DIR = Path("/home/moonrider1968/bots/data/elite20_v31")
SYMBOLS = [
    "AAPL", "AMZN", "AVGO", "CAT", "COP", "COST", "CVX", "GE",
    "GOOGL", "GS", "JPM", "META", "MSFT", "NFLX", "NVDA", "PG",
    "QQQ", "SPY", "WMT", "XOM"
]

EVAL_START = "2021-09-01"
EVAL_END = "2026-09-04"
FRICTION_BPS = 0.0005

def load_data():
    dfs = {}
    for s in SYMBOLS:
        p = DATA_DIR / f"{s}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        df["high_20"] = df["high"].shift(1).rolling(20).max()
        df["low_20"] = df["low"].shift(1).rolling(20).min()
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["sma50"] = df["close"].rolling(50).mean()
        df["sma50_slope"] = df["sma50"].diff(5)

        prev_c = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"] - prev_c).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()

        dfs[s] = df
    return dfs

def simulate_combo(dfs, eval_dates, dip_mult, tp_mult, mode="HALF_TRAIL"):
    completed_trades = []
    active_positions = {}
    pending_orders = {}
    
    cash = 100_000.0
    equity_curve = []

    for dt in eval_dates:
        dt_str = str(dt.date())

        # 1. Update Active Positions
        closed_syms = []
        for sym, pos in active_positions.items():
            df = dfs[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
            low_20 = float(bar["low_20"])

            # Take Profit Check
            if not pos["harvested"]:
                if h >= pos["tp_px"]:
                    if mode == "HALF_TRAIL":
                        half_units = pos["units"] // 2
                        if half_units > 0:
                            harvest_px = pos["tp_px"] * (1.0 - FRICTION_BPS)
                            pnl_harvest = (harvest_px - pos["entry_px"]) * half_units
                            cash += pnl_harvest
                            pos["harvested_pnl"] += pnl_harvest
                            pos["units"] -= half_units
                            pos["harvested"] = True
                            pos["sl_px"] = max(pos["sl_px"], pos["entry_px"] * (1.0 + FRICTION_BPS))
                    else: # FLAT_100
                        exit_px = pos["tp_px"] * (1.0 - FRICTION_BPS)
                        pnl = (exit_px - pos["entry_px"]) * pos["units"]
                        cash += pnl
                        completed_trades.append({
                            "net_pnl": pnl,
                            "r_mult": pnl / pos["dollar_risk"],
                            "win": pnl > 0
                        })
                        closed_syms.append(sym)
                        continue

            # Stop / Channel Exit Check
            stop_hit = l <= pos["sl_px"]
            ch_exit = (mode == "HALF_TRAIL" and pos["harvested"] and c < low_20 and not stop_hit)

            if stop_hit or ch_exit:
                exit_px = pos["sl_px"] * (1.0 - FRICTION_BPS) if stop_hit else c * (1.0 - FRICTION_BPS)
                rem_pnl = (exit_px - pos["entry_px"]) * pos["units"]
                tot_pnl = rem_pnl + pos["harvested_pnl"]
                cash += rem_pnl
                completed_trades.append({
                    "net_pnl": tot_pnl,
                    "r_mult": tot_pnl / pos["dollar_risk"],
                    "win": tot_pnl > 0
                })
                closed_syms.append(sym)

        for s in closed_syms:
            del active_positions[s]

        # 2. Evaluate Limit Orders (Dip Fill)
        filled_syms = []
        for sym, order in pending_orders.items():
            if sym in active_positions or len(active_positions) >= 3:
                continue
            df = dfs[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            l, o = float(bar["low"]), float(bar["open"])

            if l <= order["limit_px"]:
                fill_px = min(o, order["limit_px"]) * (1.0 + FRICTION_BPS)
                units = int(order["dollar_risk"] / order["r_dist"])
                min_units = 2 if mode == "HALF_TRAIL" else 1
                if units >= min_units:
                    active_positions[sym] = {
                        "entry_px": fill_px,
                        "tp_px": fill_px + (tp_mult * order["r_dist"]),
                        "sl_px": fill_px - order["r_dist"],
                        "r_dist": order["r_dist"],
                        "units": units,
                        "dollar_risk": order["dollar_risk"],
                        "harvested": False,
                        "harvested_pnl": 0.0
                    }
                    filled_syms.append(sym)

        for s in filled_syms:
            del pending_orders[s]

        expired = [s for s, o in pending_orders.items() if (dt - pd.to_datetime(o["created_dt"])).days > 15]
        for s in expired:
            del pending_orders[s]

        # 3. Trend Pivot Setups
        for sym in SYMBOLS:
            if sym in active_positions or sym in pending_orders or len(active_positions) >= 3:
                continue
            df = dfs.get(sym)
            if df is None or dt not in df.index:
                continue

            idx = df.index.get_loc(dt)
            if idx < 50:
                continue

            cur = df.iloc[idx]
            c_px, h_px = float(cur["close"]), float(cur["high"])
            ema20, sma50, slope = float(cur["ema20"]), float(cur["sma50"]), float(cur["sma50_slope"])
            h20 = float(cur["high_20"])

            # Strong Trend Gate
            if (c_px > ema20) and (ema20 > sma50) and (slope > 0) and (h_px >= h20):
                atr = float(cur["atr14"])
                if pd.isna(atr) or atr <= 0:
                    continue

                r_dist = 1.5 * atr
                buy_limit = h_px - (dip_mult * r_dist)

                if buy_limit > sma50:
                    pending_orders[sym] = {
                        "created_dt": dt_str,
                        "limit_px": buy_limit,
                        "r_dist": r_dist,
                        "dollar_risk": 500.0
                    }

        # NAV Snapshot
        mtm = 0.0
        for s, pos in active_positions.items():
            s_df = dfs[s]
            cur_px = float(s_df.loc[dt]["close"]) if dt in s_df.index else pos["entry_px"]
            mtm += (cur_px - pos["entry_px"]) * pos["units"]

        equity_curve.append(cash + mtm)

    if not completed_trades:
        return None

    tdf = pd.DataFrame(completed_trades)
    eq_s = pd.Series(equity_curve)

    wins = tdf[tdf["win"]]
    losses = tdf[~tdf["win"]]
    gp = wins["net_pnl"].sum()
    gl = abs(losses["net_pnl"].sum())
    pf = gp / gl if gl > 0 else 999.0
    wr = len(wins) / len(tdf) * 100.0

    final_nav = eq_s.iloc[-1]
    years = len(eval_dates) / 252.0
    cagr = ((final_nav / 100_000.0) ** (1.0 / years) - 1.0) * 100.0

    hwm = eq_s.cummax()
    dd = (hwm - eq_s) / hwm
    max_dd = dd.max() * 100.0

    rets = eq_s.pct_change().dropna()
    sharpe = (rets.mean() / rets.std()) * np.sqrt(252) if rets.std() > 0 else 0.0

    return {
        "dip_r": dip_mult,
        "tp_r": tp_mult,
        "mode": mode,
        "trades": len(tdf),
        "wr%": round(wr, 1),
        "pf": round(pf, 2),
        "net_r": round(tdf["r_mult"].sum(), 1),
        "exp_r": round(tdf["r_mult"].mean(), 3),
        "cagr%": round(cagr, 2),
        "max_dd%": round(max_dd, 2),
        "sharpe": round(sharpe, 2)
    }

if __name__ == "__main__":
    dfs = load_data()
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    dips = [0.5, 1.0, 1.5, 2.0]
    tps = [1.0, 1.5, 2.0, 2.5, 3.0]
    modes = ["HALF_TRAIL", "FLAT_100"]

    results = []
    print(f"Running grid sweep across {len(dips) * len(tps) * len(modes)} parameter sets...")

    for d, t, m in itertools.product(dips, tps, modes):
        res = simulate_combo(dfs, eval_dates, d, t, m)
        if res:
            results.append(res)

    rdf = pd.DataFrame(results)
    
    print("\n" + "=" * 100)
    print("TOP 10 CONFIGURATIONS BY PROFIT FACTOR (PF)")
    print("=" * 100)
    print(rdf.sort_values(by="pf", ascending=False).head(10).to_string(index=False))

    print("\n" + "=" * 100)
    print("TOP 10 CONFIGURATIONS BY SHARPE RATIO")
    print("=" * 100)
    print(rdf.sort_values(by="sharpe", ascending=False).head(10).to_string(index=False))

    print("\n" + "=" * 100)
    print("TOP 10 CONFIGURATIONS BY TOTAL NET R")
    print("=" * 100)
    print(rdf.sort_values(by="net_r", ascending=False).head(10).to_string(index=False))
