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
    net_pnl: float
    initial_risk: float
    scaled_in: bool
    exit_reason: str

def load_clean_data():
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

def run_hardened_simulation(dfs: Dict[str, pd.DataFrame], mode: str = "A"):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades: List[TradeRecord] = []
    active: Dict[str, dict] = {}
    
    cash = 100_000.0
    equity_curve = []

    for i in range(len(eval_dates) - 1):
        dt = eval_dates[i]
        next_dt = eval_dates[i + 1]
        dt_str = str(dt.date())
        next_dt_str = str(next_dt.date())

        # -------------------------------------------------------------
        # 1. PROCESS ACTIVE POSITIONS ON DAY T
        # -------------------------------------------------------------
        closed_syms = []
        for sym, pos in active.items():
            df = dfs[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
            low_20 = float(bar["low_20"])
            r_dist = pos["r_dist"]

            # Check if pending scale-in from yesterday gets filled on today's open
            if mode == "B" and pos["pending_scale_in"]:
                open_px = float(bar["open"])
                fill_px_l2 = open_px * (1.0 + FRICTION_BPS)
                pos["units_l2"] = pos["units_l1"]
                pos["entry_px_l2"] = fill_px_l2
                # Decoupled stops: Lot 1 to BE, Lot 2 to Lot 1 Entry
                pos["stop_l1"] = pos["entry_px_l1"] * (1.0 + FRICTION_BPS)
                pos["stop_l2"] = pos["entry_px_l1"]
                pos["scaled_in"] = True
                pos["pending_scale_in"] = False

            # Check Stops and Channel Exits on Day T
            # Lot 1
            l1_stop_hit = l <= pos["stop_l1"]
            l1_ch_hit = c < low_20 and not l1_stop_hit

            # Lot 2 (if active)
            l2_stop_hit = False
            l2_ch_hit = False
            if pos["units_l2"] > 0:
                l2_stop_hit = l <= pos["stop_l2"]
                l2_ch_hit = c < low_20 and not l2_stop_hit

            # Liquidate Lot 2 if hit
            pnl_l2 = 0.0
            if pos["units_l2"] > 0 and (l2_stop_hit or l2_ch_hit):
                exit_px_l2 = pos["stop_l2"] * (1.0 - FRICTION_BPS) if l2_stop_hit else c * (1.0 - FRICTION_BPS)
                pnl_l2 = (exit_px_l2 - pos["entry_px_l2"]) * pos["units_l2"]
                cash += pnl_l2
                pos["realized_l2_pnl"] += pnl_l2
                pos["units_l2"] = 0  # Lot 2 closes independently

            # Liquidate Lot 1 if hit (Closes full position container)
            if l1_stop_hit or l1_ch_hit:
                exit_px_l1 = pos["stop_l1"] * (1.0 - FRICTION_BPS) if l1_stop_hit else c * (1.0 - FRICTION_BPS)
                pnl_l1 = (exit_px_l1 - pos["entry_px_l1"]) * pos["units_l1"]
                cash += pnl_l1

                # If Lot 2 was still open, liquidate at same time
                if pos["units_l2"] > 0:
                    exit_px_l2 = pos["stop_l2"] * (1.0 - FRICTION_BPS) if l1_stop_hit else c * (1.0 - FRICTION_BPS)
                    pnl_l2_rem = (exit_px_l2 - pos["entry_px_l2"]) * pos["units_l2"]
                    cash += pnl_l2_rem
                    pos["realized_l2_pnl"] += pnl_l2_rem
                    pos["units_l2"] = 0

                tot_pnl = pnl_l1 + pos["realized_l2_pnl"]
                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=dt_str,
                    net_pnl=tot_pnl,
                    initial_risk=pos["dollar_risk"],
                    scaled_in=pos["scaled_in"],
                    exit_reason="STOP" if l1_stop_hit else "CH_20D"
                ))
                closed_syms.append(sym)
                continue

            # Check for Scale-In Signal on Day T Close (Fill T+1 Open)
            if mode == "B" and not pos["scaled_in"] and not pos["pending_scale_in"]:
                # Legitimate close above +1.0R
                if c >= (pos["entry_px_l1"] + r_dist):
                    pos["pending_scale_in"] = True

        for s in closed_syms:
            del active[s]

        # -------------------------------------------------------------
        # 2. SCAN NEW BREAKOUTS ON DAY T CLOSE -> FILL T+1 OPEN
        # -------------------------------------------------------------
        # Compute dynamic current NAV for accurate 0.50% sizing
        mtm_t = 0.0
        for s, pos in active.items():
            s_df = dfs[s]
            cur_px = float(s_df.loc[dt]["close"]) if dt in s_df.index else pos["entry_px_l1"]
            mtm_t += (cur_px - pos["entry_px_l1"]) * pos["units_l1"]
            if pos["units_l2"] > 0:
                mtm_t += (cur_px - pos["entry_px_l2"]) * pos["units_l2"]
        current_nav = cash + mtm_t

        for sym in SYMBOLS:
            if sym in active or len(active) >= 3:
                continue
            df = dfs.get(sym)
            if df is None or dt not in df.index or next_dt not in df.index:
                continue

            idx = df.index.get_loc(dt)
            if idx < 1:
                continue

            cur_bar = df.iloc[idx]
            prev_bar = df.iloc[idx - 1]

            # Standard 55-day Donchian Breakout
            if cur_bar["close"] > cur_bar["high_55"] and prev_bar["close"] <= prev_bar["high_55"]:
                atr = float(cur_bar["atr14"])
                if pd.isna(atr) or atr <= 0:
                    continue

                r_dist = 1.5 * atr
                dollar_risk = current_nav * 0.0050  # Strict Dynamic 0.50% NAV
                units = int(dollar_risk / r_dist)
                if units < 1:
                    continue

                next_open = float(dfs[sym].loc[next_dt]["open"])
                fill_px = next_open * (1.0 + FRICTION_BPS)

                active[sym] = {
                    "entry_date": next_dt_str,
                    "entry_px_l1": fill_px,
                    "r_dist": r_dist,
                    "units_l1": units,
                    "stop_l1": fill_px - r_dist,
                    "units_l2": 0,
                    "entry_px_l2": 0.0,
                    "stop_l2": 0.0,
                    "dollar_risk": dollar_risk,
                    "scaled_in": False,
                    "pending_scale_in": False,
                    "realized_l2_pnl": 0.0
                }

        equity_curve.append({"date": dt_str, "nav": current_nav})

    return completed_trades, pd.DataFrame(equity_curve)

def print_audit(trades: List[TradeRecord], eq_df: pd.DataFrame, label: str):
    tdf = pd.DataFrame([t.__dict__ for t in trades])
    if tdf.empty:
        print(f"{label}: No trades.")
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

    r_multiples = tdf["net_pnl"] / tdf["initial_risk"]
    scaled_trades = tdf[tdf["scaled_in"]]

    print("=" * 90)
    print(f"HARDENED REPLAY AUDIT: {label}")
    print("=" * 90)
    print(f"Total Completed Trades:  {len(tdf)}")
    print(f"Scaled-In Trades:        {len(scaled_trades)} ({len(scaled_trades)/len(tdf)*100:.1f}%)")
    print(f"Win Rate:                {wr:.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor (PF):      {pf:.3f}")
    print(f"Total Net R:             {r_multiples.sum():+.2f}R")
    print(f"Mean Expectancy:         {r_multiples.mean():+.3f}R / trade")
    print(f"Final Account NAV ($):   ${final_nav:,.2f}")
    print(f"Compounded CAGR:         {cagr:.2f}%")
    print(f"Max Mark-to-Market DD:   {max_dd:.2f}%")
    print(f"Daily MTM Sharpe Ratio:  {sharpe:.2f}")
    print("=" * 90)

if __name__ == "__main__":
    dfs = load_clean_data()

    # Replay Book A: Baseline 1-Lot (Dynamic NAV, Next-Open Fill)
    t_a, eq_a = run_hardened_simulation(dfs, mode="A")
    print_audit(t_a, eq_a, "BOOK A: HARDENED CONTROL (1-Lot Baseline)")

    # Replay Book B: Governed Scale-In (Decoupled Stops, Next-Open Add Fill, Dynamic NAV)
    t_b, eq_b = run_hardened_simulation(dfs, mode="B")
    print_audit(t_b, eq_b, "BOOK B: HARDENED SCALE-IN (+1.0R Add, Decoupled)")
