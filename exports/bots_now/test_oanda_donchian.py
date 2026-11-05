from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict
import pandas as pd
import numpy as np

DATA_DIR = Path("/home/moonrider1968/bots/data/oanda")
SPREAD_BPS = 0.0003  # ~3 pips / index spread

@dataclass
class TradeRecord:
    symbol: str
    side: str
    entry_date: str
    exit_date: str
    pnl: float
    r_mult: float
    reason: str

def simulate_oanda_asset(symbol: str, allow_shorts: bool = False):
    p = DATA_DIR / f"{symbol}.csv"
    if not p.exists():
        print(f"File missing: {p}")
        return

    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    active = None
    trades: List[TradeRecord] = []
    cash = 100_000.0
    equity = []
    hwm = 100_000.0
    max_dd = 0.0

    eval_dates = df.index[60:]

    for i in range(len(eval_dates) - 1):
        dt = eval_dates[i]
        next_dt = eval_dates[i + 1]

        # 1. Process Channel Exits on Next-Bar Open
        if active is not None and active["pending_exit"]:
            next_open = float(df.loc[next_dt, "open"])
            if active["side"] == "LONG":
                exit_px = next_open * (1.0 - SPREAD_BPS)
                pnl = (exit_px - active["entry_px"]) * active["units"]
            else:
                exit_px = next_open * (1.0 + SPREAD_BPS)
                pnl = (active["entry_px"] - exit_px) * active["units"]

            cash += pnl
            r_mult = pnl / active["dollar_risk"]
            trades.append(TradeRecord(symbol, active["side"], active["entry_date"], str(next_dt.date()), pnl, r_mult, "CH_EXIT"))
            active = None

        # 2. Process Stops Intraday
        if active is not None:
            bar = df.loc[next_dt]
            o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

            if active["side"] == "LONG":
                if l <= active["stop_px"]:
                    fill_px = min(o, active["stop_px"]) * (1.0 - SPREAD_BPS)
                    pnl = (fill_px - active["entry_px"]) * active["units"]
                    cash += pnl
                    trades.append(TradeRecord(symbol, "LONG", active["entry_date"], str(next_dt.date()), pnl, pnl / active["dollar_risk"], "STOP"))
                    active = None
                elif c < bar["low_20"]:
                    active["pending_exit"] = True

            elif active["side"] == "SHORT":
                if h >= active["stop_px"]:
                    fill_px = max(o, active["stop_px"]) * (1.0 + SPREAD_BPS)
                    pnl = (active["entry_px"] - fill_px) * active["units"]
                    cash += pnl
                    trades.append(TradeRecord(symbol, "SHORT", active["entry_date"], str(next_dt.date()), pnl, pnl / active["dollar_risk"], "STOP"))
                    active = None
                elif c > bar["high_20"]:
                    active["pending_exit"] = True

        # 3. Calculate End of Session NAV
        mtm = 0.0
        if active is not None:
            close_px = float(df.loc[next_dt, "close"])
            if active["side"] == "LONG":
                mtm = (close_px - active["entry_px"]) * active["units"]
            else:
                mtm = (active["entry_px"] - close_px) * active["units"]

        eod_nav = cash + mtm
        if eod_nav > hwm:
            hwm = eod_nav
        dd = (hwm - eod_nav) / hwm if hwm > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        equity.append(eod_nav)

        # 4. Check New Breakouts on Day T Close -> Stage for T+1 Open
        if active is None and (i + 2) < len(eval_dates):
            t2_dt = eval_dates[i + 2]
            cur = df.loc[next_dt]
            prev = df.iloc[df.index.get_loc(next_dt) - 1]

            atr = float(cur["atr14"])
            if pd.isna(atr) or atr <= 0:
                continue

            r_dist = 1.5 * atr
            dollar_risk = eod_nav * 0.0050
            units = int(dollar_risk / r_dist)

            # Long Breakout
            if cur["close"] > cur["high_55"] and prev["close"] <= prev["high_55"]:
                t2_open = float(df.loc[t2_dt, "open"])
                fill_px = t2_open * (1.0 + SPREAD_BPS)
                active = {
                    "side": "LONG",
                    "entry_date": str(t2_dt.date()),
                    "entry_px": fill_px,
                    "stop_px": fill_px - r_dist,
                    "units": units,
                    "dollar_risk": dollar_risk,
                    "pending_exit": False
                }

            # Short Breakout (Currencies only)
            elif allow_shorts and cur["close"] < cur["low_55"] and prev["close"] >= prev["low_55"]:
                t2_open = float(df.loc[t2_dt, "open"])
                fill_px = t2_open * (1.0 - SPREAD_BPS)
                active = {
                    "side": "SHORT",
                    "entry_date": str(t2_dt.date()),
                    "entry_px": fill_px,
                    "stop_px": fill_px + r_dist,
                    "units": units,
                    "dollar_risk": dollar_risk,
                    "pending_exit": False
                }

    tdf = pd.DataFrame([t.__dict__ for t in trades])
    if tdf.empty:
        print(f"[{symbol}] Zero completed trades.")
        return

    wins = tdf[tdf["pnl"] > 0]
    wr = len(wins) / len(tdf) * 100.0
    net_r = tdf["r_mult"].sum()
    pf = wins["pnl"].sum() / abs(tdf[tdf["pnl"] <= 0]["pnl"].sum()) if len(tdf[tdf["pnl"] <= 0]) > 0 else 999.0
    years = (eval_dates[-1] - eval_dates[0]).days / 365.25
    cagr = ((equity[-1] / 100_000.0) ** (1.0 / years) - 1.0) * 100.0

    print(f"============================================================")
    print(f"OANDA TEST: {symbol} (Shorts: {allow_shorts})")
    print(f"============================================================")
    print(f"Trades: {len(tdf)} | Win Rate: {wr:.1f}% | Profit Factor: {pf:.2f}")
    print(f"Net R: {net_r:+.2f}R | Expectancy: {net_r/len(tdf):+.3f}R / trade")
    print(f"CAGR: {cagr:.2f}% | Max Drawdown: {max_dd*100.0:.2f}% | Final NAV: ${equity[-1]:,.2f}")
    print(f"============================================================\n")

if __name__ == "__main__":
    # 1. SPX500_USD (Index CFD: Long-Only strictly)
    simulate_oanda_asset("SPX500_USD", allow_shorts=False)

    # 2. Macro Currencies (FX Pairs: Symmetric Long + Short)
    for pair in ["EUR_USD", "USD_JPY", "GBP_USD", "AUD_USD"]:
        simulate_oanda_asset(pair, allow_shorts=True)
