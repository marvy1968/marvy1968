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
    r_multiple: float
    exit_reason: str
    entry_regime: str

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
        df["sma50"] = df["close"].rolling(50).mean()
        df["sma200"] = df["close"].rolling(200).mean()

        prev_c = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"] - prev_c).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()
        dfs[s] = df
    return dfs

def run_attribution_sim(dfs):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades: List[TradeRecord] = []
    active: Dict[str, dict] = {}
    cash = 100_000.0
    spy_df = dfs["SPY"]

    for i in range(len(eval_dates) - 1):
        dt = eval_dates[i]
        next_dt = eval_dates[i + 1]
        dt_str = str(dt.date())
        next_dt_str = str(next_dt.date())

        # Regime evaluation on Day T close
        spy_bar = spy_df.loc[dt] if dt in spy_df.index else None
        regime = "BULL" if spy_bar is not None and spy_bar["close"] > spy_bar["sma200"] and spy_bar["sma50"] > spy_bar["sma200"] else "NON_BULL"

        # 1. Process Channel Exits on T+1 Open
        closed_syms = []
        for sym, pos in active.items():
            if next_dt not in dfs[sym].index:
                continue
            next_open = float(dfs[sym].loc[next_dt]["open"])

            if pos["pending_exit_ch"]:
                exit_px = next_open * (1.0 - FRICTION_BPS)
                pnl = (exit_px - pos["entry_px"]) * pos["units"]
                cash += pnl
                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=next_dt_str,
                    net_pnl=pnl,
                    initial_risk=pos["dollar_risk"],
                    r_multiple=pnl / pos["dollar_risk"],
                    exit_reason="CH_20D",
                    entry_regime=pos["regime"]
                ))
                closed_syms.append(sym)

        for s in closed_syms:
            del active[s]

        # 2. Process Stops on T+1 Intraday
        closed_intra = []
        for sym, pos in active.items():
            if next_dt not in dfs[sym].index:
                continue
            bar = dfs[sym].loc[next_dt]
            o, l, c = float(bar["open"]), float(bar["low"]), float(bar["close"])
            low_20 = float(bar["low_20"])

            if l <= pos["stop_px"]:
                fill_px = min(o, pos["stop_px"]) * (1.0 - FRICTION_BPS)
                pnl = (fill_px - pos["entry_px"]) * pos["units"]
                cash += pnl
                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=next_dt_str,
                    net_pnl=pnl,
                    initial_risk=pos["dollar_risk"],
                    r_multiple=pnl / pos["dollar_risk"],
                    exit_reason="STOP",
                    entry_regime=pos["regime"]
                ))
                closed_intra.append(sym)
                continue

            if c < low_20:
                pos["pending_exit_ch"] = True

        for s in closed_intra:
            del active[s]

        # 3. Calculate EOD NAV
        mtm_eod = 0.0
        for s, pos in active.items():
            if next_dt in dfs[s].index:
                close_px = float(dfs[s].loc[next_dt]["close"])
                mtm_eod += (close_px - pos["entry_px"]) * pos["units"]
        eod_nav = cash + mtm_eod

        # 4. Scan Breakouts on Day T+1 Close -> Stage for T+2 Open
        if (i + 2) < len(eval_dates):
            t2_dt = eval_dates[i + 2]
            t2_dt_str = str(t2_dt.date())

            for sym in SYMBOLS:
                if sym in active or len(active) >= 3:
                    continue
                df = dfs.get(sym)
                if df is None or next_dt not in df.index or t2_dt not in df.index:
                    continue

                idx = df.index.get_loc(next_dt)
                if idx < 1:
                    continue

                cur_bar = df.iloc[idx]
                prev_bar = df.iloc[idx - 1]

                if cur_bar["close"] > cur_bar["high_55"] and prev_bar["close"] <= prev_bar["high_55"]:
                    atr = float(cur_bar["atr14"])
                    if pd.isna(atr) or atr <= 0:
                        continue

                    r_dist = 1.5 * atr
                    dollar_risk = eod_nav * 0.0050
                    units = int(dollar_risk / r_dist)
                    if units < 1:
                        continue

                    t2_open = float(dfs[sym].loc[t2_dt]["open"])
                    fill_px = t2_open * (1.0 + FRICTION_BPS)

                    active[sym] = {
                        "entry_date": t2_dt_str,
                        "entry_px": fill_px,
                        "r_dist": r_dist,
                        "units": units,
                        "stop_px": fill_px - r_dist,
                        "dollar_risk": dollar_risk,
                        "pending_exit_ch": False,
                        "regime": regime
                    }

    return pd.DataFrame([t.__dict__ for t in completed_trades])

if __name__ == "__main__":
    dfs = load_data()
    tdf = run_attribution_sim(dfs)

    print("=" * 90)
    print("BOOK A: PERFORMANCE ATTRIBUTION BY SYMBOL")
    print("=" * 90)
    sym_grp = tdf.groupby("symbol").agg(
        trades=("r_multiple", "count"),
        win_rate=("net_pnl", lambda x: f"{(x > 0).mean() * 100:.1f}%"),
        total_net_r=("r_multiple", "sum"),
        mean_r=("r_multiple", "mean"),
        total_pnl=("net_pnl", "sum")
    ).sort_values(by="total_net_r", ascending=False)
    print(sym_grp.to_string())

    print("\n" + "=" * 90)
    print("BOOK A: PERFORMANCE ATTRIBUTION BY REGIME AT ENTRY")
    print("=" * 90)
    reg_grp = tdf.groupby("entry_regime").agg(
        trades=("r_multiple", "count"),
        win_rate=("net_pnl", lambda x: f"{(x > 0).mean() * 100:.1f}%"),
        total_net_r=("r_multiple", "sum"),
        mean_r=("r_multiple", "mean"),
        total_pnl=("net_pnl", "sum")
    )
    print(reg_grp.to_string())
