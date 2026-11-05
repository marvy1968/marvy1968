import math
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict
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

@dataclass
class TradeRecord:
    symbol: str
    entry_date: str
    exit_date: str
    lots_added: int
    net_pnl: float
    r_multiple: float
    exit_reason: str

def load_data():
    dfs = {}
    for s in SYMBOLS:
        p = DATA_DIR / f"{s}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        df["high_55"] = df["high"].shift(1).rolling(55).max()
        df["low_20"] = df["low"].shift(1).rolling(20).min()

        prev_c = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"] - prev_c).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()

        dfs[s] = df
    return dfs

def run_simulation(dfs: Dict[str, pd.DataFrame], mode: str = "BASE"):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades: List[TradeRecord] = []
    active_positions: Dict[str, dict] = {}
    
    initial_nav = 100_000.0
    cash = initial_nav
    equity_curve = []

    for dt in eval_dates:
        dt_str = str(dt.date())

        # 1. Update and Check Active Positions
        closed_syms = []
        for sym, pos in active_positions.items():
            df = dfs[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            
            r_dist = pos["r_dist"]
            current_r = (h - pos["entry_px"]) / r_dist

            if mode == "USER_RAW":
                if not pos["half_cut"] and l <= (pos["entry_px"] - 0.5 * r_dist):
                    cut_qty = pos["units_lot1"] // 2
                    pos["units_lot1"] -= cut_qty
                    cash += cut_qty * (pos["entry_px"] - 0.5 * r_dist) * (1.0 - FRICTION_BPS)
                    pos["half_cut"] = True

                if not pos["added_05"] and current_r >= 0.5:
                    add_units = int(pos["initial_units"] * 0.5)
                    pos["units_added"] += add_units
                    pos["added_05"] = True

                if not pos["added_10"] and current_r >= 1.0:
                    add_units = pos["initial_units"]
                    pos["units_added"] += add_units
                    pos["added_10"] = True

                stop_level = pos["entry_px"] - r_dist
                stop_hit = l <= stop_level
                ch_exit = c < bar["low_20"] and not stop_hit

            elif mode == "GOVERNED_PYRAMID":
                if not pos["added_10"] and current_r >= 1.0:
                    add_units = pos["initial_units"]
                    pos["units_lot2"] = add_units
                    pos["entry_px_lot2"] = pos["entry_px"] + r_dist
                    pos["stop_lot2"] = pos["entry_px"]
                    pos["stop_lot1"] = pos["entry_px"] * (1.0 + FRICTION_BPS)
                    pos["added_10"] = True

                stop_hit = False
                if pos["units_lot2"] > 0:
                    if l <= pos["stop_lot1"]:
                        stop_hit = True
                else:
                    if l <= pos["stop_lot1"]:
                        stop_hit = True

                ch_exit = c < bar["low_20"] and not stop_hit

            else:  # BASE
                stop_hit = l <= pos["stop_px"]
                ch_exit = c < bar["low_20"] and not stop_hit

            if stop_hit or ch_exit:
                if stop_hit:
                    reason = "STOP_OUT"
                    if mode == "GOVERNED_PYRAMID" and pos["added_10"]:
                        exit_px = pos["entry_px"] * (1.0 - FRICTION_BPS)
                    else:
                        exit_px = (pos["entry_px"] - r_dist) * (1.0 - FRICTION_BPS)
                else:
                    reason = "CH_EXIT_20D"
                    exit_px = c * (1.0 - FRICTION_BPS)

                if mode == "USER_RAW":
                    tot_units = pos["units_lot1"] + pos["units_added"]
                    pnl = (exit_px - pos["entry_px"]) * tot_units
                elif mode == "GOVERNED_PYRAMID":
                    pnl_lot1 = (exit_px - pos["entry_px"]) * pos["units_lot1"]
                    pnl_lot2 = (exit_px - pos["entry_px_lot2"]) * pos["units_lot2"] if pos["units_lot2"] > 0 else 0.0
                    pnl = pnl_lot1 + pnl_lot2
                else:
                    pnl = (exit_px - pos["entry_px"]) * pos["units"]

                cash += pnl
                r_mult = pnl / pos["dollar_risk"]

                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=dt_str,
                    lots_added=1 if pos.get("added_10") else 0,
                    net_pnl=pnl,
                    r_multiple=r_mult,
                    exit_reason=reason
                ))
                closed_syms.append(sym)

        for s in closed_syms:
            del active_positions[s]

        # 2. Check for New Donchian Breakouts
        for sym in SYMBOLS:
            if sym in active_positions or len(active_positions) >= 3:
                continue
            df = dfs.get(sym)
            if df is None or dt not in df.index:
                continue

            idx = df.index.get_loc(dt)
            if idx < 1:
                continue

            cur_bar = df.iloc[idx]
            prev_bar = df.iloc[idx - 1]

            if cur_bar["close"] > cur_bar["high_55"] and prev_bar["close"] <= prev_bar["high_55"]:
                entry_fill = float(cur_bar["open"]) * (1.0 + FRICTION_BPS)
                atr = float(cur_bar["atr14"])
                if pd.isna(atr) or atr <= 0:
                    continue

                r_dist = 1.5 * atr
                dollar_risk = 500.0  # 0.50% of $100k
                units = int(dollar_risk / r_dist)
                if units < 1:
                    continue

                if mode == "BASE":
                    active_positions[sym] = {
                        "entry_date": dt_str,
                        "entry_px": entry_fill,
                        "stop_px": entry_fill - r_dist,
                        "r_dist": r_dist,
                        "units": units,
                        "dollar_risk": dollar_risk
                    }
                elif mode == "USER_RAW":
                    active_positions[sym] = {
                        "entry_date": dt_str,
                        "entry_px": entry_fill,
                        "r_dist": r_dist,
                        "initial_units": units,
                        "units_lot1": units,
                        "units_added": 0,
                        "dollar_risk": dollar_risk,
                        "half_cut": False,
                        "added_05": False,
                        "added_10": False
                    }
                elif mode == "GOVERNED_PYRAMID":
                    active_positions[sym] = {
                        "entry_date": dt_str,
                        "entry_px": entry_fill,
                        "r_dist": r_dist,
                        "initial_units": units,
                        "units_lot1": units,
                        "stop_lot1": entry_fill - r_dist,
                        "units_lot2": 0,
                        "entry_px_lot2": 0.0,
                        "stop_lot2": 0.0,
                        "dollar_risk": dollar_risk,
                        "added_10": False
                    }

        # Daily NAV Snapshot (Handles missing session bars via point-in-time forward-fill)
        mtm_unrealized = 0.0
        for s, pos in active_positions.items():
            s_df = dfs[s]
            if dt in s_df.index:
                cur_px = float(s_df.loc[dt]["close"])
            else:
                prior = s_df.loc[:dt]
                cur_px = float(prior["close"].iloc[-1]) if not prior.empty else pos["entry_px"]

            if mode == "GOVERNED_PYRAMID":
                mtm_unrealized += (cur_px - pos["entry_px"]) * pos["units_lot1"]
                if pos["units_lot2"] > 0:
                    mtm_unrealized += (cur_px - pos["entry_px_lot2"]) * pos["units_lot2"]
            elif mode == "USER_RAW":
                tot = pos["units_lot1"] + pos["units_added"]
                mtm_unrealized += (cur_px - pos["entry_px"]) * tot
            else:
                mtm_unrealized += (cur_px - pos["entry_px"]) * pos["units"]

        equity_curve.append({"date": dt_str, "nav": cash + mtm_unrealized})

    return completed_trades, pd.DataFrame(equity_curve)

def print_metrics(trades: List[TradeRecord], eq_df: pd.DataFrame, label: str):
    tdf = pd.DataFrame([t.__dict__ for t in trades])
    if tdf.empty:
        print(f"{label}: No trades completed.")
        return

    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    wr = len(wins) / len(tdf) * 100.0

    gp = wins["net_pnl"].sum()
    gl = abs(losses["net_pnl"].sum())
    pf = gp / gl if gl > 0 else 999.0

    final_nav = eq_df["nav"].iloc[-1]
    years = (pd.to_datetime(eq_df["date"].iloc[-1]) - pd.to_datetime(eq_df["date"].iloc[0])).days / 365.25
    cagr = ((final_nav / 100_000.0) ** (1.0 / years) - 1.0) * 100.0

    eq_df["hwm"] = eq_df["nav"].cummax()
    eq_df["dd"] = (eq_df["hwm"] - eq_df["nav"]) / eq_df["hwm"]
    max_dd = eq_df["dd"].max() * 100.0

    rets = eq_df["nav"].pct_change().dropna()
    sharpe = (rets.mean() / rets.std()) * np.sqrt(252) if rets.std() > 0 else 0.0

    pyramided_trades = tdf[tdf["lots_added"] > 0]

    print("=" * 85)
    print(f"PORTFOLIO PERFORMANCE AUDIT: {label}")
    print("=" * 85)
    print(f"Evaluation Span:         {eq_df['date'].iloc[0]} to {eq_df['date'].iloc[-1]} ({years:.2f} yrs)")
    print(f"Total Completed Trades:  {len(tdf)}")
    print(f"Pyramided Trades (Adds): {len(pyramided_trades)} ({len(pyramided_trades)/len(tdf)*100:.1f}%)")
    print(f"Win Rate:                {wr:.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor (PF):      {pf:.3f}")
    print(f"Total Net R:             {tdf['r_multiple'].sum():+.2f}R")
    print(f"Mean Expectancy:         {tdf['r_multiple'].mean():+.3f}R / trade")
    print(f"Final Account NAV ($):   ${final_nav:,.2f}")
    print(f"Compounded CAGR:         {cagr:.2f}%")
    print(f"Max Mark-to-Market DD:   {max_dd:.2f}%")
    print(f"Daily MTM Sharpe Ratio:  {sharpe:.2f}")
    print("=" * 85)

if __name__ == "__main__":
    dfs = load_data()

    # 1. BASE: No Scaling
    t_base, eq_base = run_simulation(dfs, mode="BASE")
    print_metrics(t_base, eq_base, "1. BASE (No Scale, 1 Lot)")

    # 2. USER_RAW: Original Rule (-0.5R Cut, +0.5R and +1.0R Adds, Shared Stop)
    t_user, eq_user = run_simulation(dfs, mode="USER_RAW")
    print_metrics(t_user, eq_user, "2. USER_RAW (Naive Pyramid + Cut)")

    # 3. GOVERNED_PYRAMID: Optimized Protocol (+1.0R Add Only, Lot 1 to BE, Decoupled Stop)
    t_gov, eq_gov = run_simulation(dfs, mode="GOVERNED_PYRAMID")
    print_metrics(t_gov, eq_gov, "3. GOVERNED_PYRAMID (+1.0R Add, Self-Funding)")
