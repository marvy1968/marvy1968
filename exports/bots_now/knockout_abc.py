from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional
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
    engine: str
    symbol: str
    entry_date: str
    exit_date: str
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
        df["high_20"] = df["high"].shift(1).rolling(20).max()
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

def run_portfolio_sim(dfs: Dict[str, pd.DataFrame], mode: str):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades: List[TradeRecord] = []
    active: Dict[str, dict] = {}
    pending_dip_orders: Dict[str, dict] = {}

    cash = 100_000.0
    equity_curve = []

    for i in range(len(eval_dates) - 1):
        dt = eval_dates[i]
        next_dt = eval_dates[i + 1]
        dt_str = str(dt.date())
        next_dt_str = str(next_dt.date())

        # -------------------------------------------------------------
        # 1. EVALUATE ACTIVE POSITIONS ON TODAY'S BAR
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

            # Engine-specific intra-position lifecycle
            if pos["engine"] == "DONCHIAN":
                if mode in ["B", "C"]:
                    # Governed Scale-In at +1.0R
                    current_r = (h - pos["entry_px"]) / r_dist
                    if not pos["scaled_in"] and current_r >= 1.0:
                        pos["units_lot2"] = pos["units_lot1"]
                        pos["entry_px_lot2"] = pos["entry_px"] + r_dist
                        pos["stop_lot2"] = pos["entry_px"]
                        pos["stop_lot1"] = pos["entry_px"] * (1.0 + FRICTION_BPS)
                        pos["scaled_in"] = True

                # Exit checks
                stop_hit = False
                if pos.get("scaled_in", False):
                    if l <= pos["stop_lot1"]:
                        stop_hit = True
                else:
                    if l <= pos["stop_lot1"]:
                        stop_hit = True

                ch_exit = c < low_20 and not stop_hit

                if stop_hit or ch_exit:
                    if stop_hit:
                        exit_px = pos["stop_lot1"] * (1.0 - FRICTION_BPS)
                        reason = "STOP_BE" if pos.get("scaled_in") else "STOP_INIT"
                    else:
                        exit_px = c * (1.0 - FRICTION_BPS)
                        reason = "CH_EXIT_20D"

                    pnl_l1 = (exit_px - pos["entry_px"]) * pos["units_lot1"]
                    pnl_l2 = (exit_px - pos["entry_px_lot2"]) * pos["units_lot2"] if pos.get("scaled_in") else 0.0
                    tot_pnl = pnl_l1 + pnl_l2

                    cash += tot_pnl
                    completed_trades.append(TradeRecord(
                        engine="DONCHIAN",
                        symbol=sym,
                        entry_date=pos["entry_date"],
                        exit_date=dt_str,
                        net_pnl=tot_pnl,
                        r_multiple=tot_pnl / pos["dollar_risk"],
                        exit_reason=reason
                    ))
                    closed_syms.append(sym)

            elif pos["engine"] == "DIP_SLEEVE":
                # HALF_TRAIL: harvest 50% at +2.5R, stop -> BE, trail remainder on low_20
                if not pos["harvested"]:
                    if h >= pos["tp_px"]:
                        h_qty = pos["units"] // 2
                        if h_qty > 0:
                            h_px = pos["tp_px"] * (1.0 - FRICTION_BPS)
                            pnl_h = (h_px - pos["entry_px"]) * h_qty
                            cash += pnl_h
                            pos["harvested_pnl"] += pnl_h
                            pos["units"] -= h_qty
                            pos["harvested"] = True
                            pos["sl_px"] = max(pos["sl_px"], pos["entry_px"] * (1.0 + FRICTION_BPS))

                stop_hit = l <= pos["sl_px"]
                ch_exit = (pos["harvested"] and c < low_20 and not stop_hit)

                if stop_hit or ch_exit:
                    exit_px = pos["sl_px"] * (1.0 - FRICTION_BPS) if stop_hit else c * (1.0 - FRICTION_BPS)
                    reason = "DIP_STOP" if stop_hit else "DIP_CH20"
                    rem_pnl = (exit_px - pos["entry_px"]) * pos["units"]
                    tot_pnl = rem_pnl + pos["harvested_pnl"]

                    cash += rem_pnl
                    completed_trades.append(TradeRecord(
                        engine="DIP_SLEEVE",
                        symbol=sym,
                        entry_date=pos["entry_date"],
                        exit_date=dt_str,
                        net_pnl=tot_pnl,
                        r_multiple=tot_pnl / pos["dollar_risk"],
                        exit_reason=reason
                    ))
                    closed_syms.append(sym)

        for s in closed_syms:
            del active[s]

        # -------------------------------------------------------------
        # 2. GENERATE SIGNALS FOR NEXT-OPEN EXECUTION
        # -------------------------------------------------------------
        # A. Priority 1: Donchian 55 Breakouts (Today Close > High 55)
        donchian_signals = []
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

            if cur_bar["close"] > cur_bar["high_55"] and prev_bar["close"] <= prev_bar["high_55"]:
                atr = float(cur_bar["atr14"])
                if pd.notna(atr) and atr > 0:
                    donchian_signals.append((sym, atr))

        # Fill Donchian signals on NEXT OPEN
        for sym, atr in donchian_signals:
            if len(active) >= 3:
                break
            next_open = float(dfs[sym].loc[next_dt]["open"])
            fill_px = next_open * (1.0 + FRICTION_BPS)
            r_dist = 1.5 * atr
            dollar_risk = 500.0  # 0.50% of $100k
            units = int(dollar_risk / r_dist)
            if units < 1:
                continue

            active[sym] = {
                "engine": "DONCHIAN",
                "entry_date": next_dt_str,
                "entry_px": fill_px,
                "r_dist": r_dist,
                "units_lot1": units,
                "stop_lot1": fill_px - r_dist,
                "units_lot2": 0,
                "entry_px_lot2": 0.0,
                "stop_lot2": 0.0,
                "dollar_risk": dollar_risk,
                "scaled_in": False
            }

        # B. Priority 2: Dip Sleeve (Only for Book C when idle capacity exists)
        if mode == "C":
            dip_count = sum(1 for p in active.values() if p["engine"] == "DIP_SLEEVE")
            available_slots = 3 - len(active)

            # Check existing limit orders on today's bar
            filled_dips = []
            if dip_count < 1 and available_slots > 0:
                for sym, order in pending_dip_orders.items():
                    if sym in active or len(active) >= 3:
                        continue
                    df = dfs[sym]
                    if next_dt not in df.index:
                        continue

                    next_bar = df.loc[next_dt]
                    l_next, o_next = float(next_bar["low"]), float(next_bar["open"])

                    if l_next <= order["limit_px"]:
                        fill_px = min(o_next, order["limit_px"]) * (1.0 + FRICTION_BPS)
                        units = int(order["dollar_risk"] / order["r_dist"])
                        if units >= 2:
                            active[sym] = {
                                "engine": "DIP_SLEEVE",
                                "entry_date": next_dt_str,
                                "entry_px": fill_px,
                                "tp_px": fill_px + (2.5 * order["r_dist"]),
                                "sl_px": fill_px - order["r_dist"],
                                "r_dist": order["r_dist"],
                                "units": units,
                                "dollar_risk": order["dollar_risk"],
                                "harvested": False,
                                "harvested_pnl": 0.0
                            }
                            filled_dips.append(sym)
                            break  # Strictly max 1 active dip ticket

            for s in filled_dips:
                del pending_dip_orders[s]

            # Expire stale limit orders
            expired = [s for s, o in pending_dip_orders.items() if (dt - pd.to_datetime(o["created_dt"])).days > 15]
            for s in expired:
                del pending_dip_orders[s]

            # Scan for new shallow dip setups (-0.5R off 20D high in strong trend)
            for sym in SYMBOLS:
                if sym in active or sym in pending_dip_orders or len(active) >= 3:
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

                if (c_px > ema20) and (ema20 > sma50) and (slope > 0) and (h_px >= h20):
                    atr = float(cur["atr14"])
                    if pd.notna(atr) and atr > 0:
                        r_dist = 1.5 * atr
                        buy_limit = h_px - (0.5 * r_dist)
                        if buy_limit > sma50:
                            pending_dip_orders[sym] = {
                                "created_dt": dt_str,
                                "limit_px": buy_limit,
                                "r_dist": r_dist,
                                "dollar_risk": 500.0
                            }

        # -------------------------------------------------------------
        # 3. DAILY MARK-TO-MARKET SNAPSHOT
        # -------------------------------------------------------------
        mtm = 0.0
        for s, pos in active.items():
            s_df = dfs[s]
            cur_px = float(s_df.loc[dt]["close"]) if dt in s_df.index else pos["entry_px"]
            if pos["engine"] == "DONCHIAN":
                mtm += (cur_px - pos["entry_px"]) * pos["units_lot1"]
                if pos.get("scaled_in"):
                    mtm += (cur_px - pos["entry_px_lot2"]) * pos["units_lot2"]
            else:
                mtm += (cur_px - pos["entry_px"]) * pos["units"]

        equity_curve.append({"date": dt_str, "nav": cash + mtm})

    return completed_trades, pd.DataFrame(equity_curve)

def audit_book(trades: List[TradeRecord], eq_df: pd.DataFrame, label: str):
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

    donch_trades = tdf[tdf["engine"] == "DONCHIAN"]
    dip_trades = tdf[tdf["engine"] == "DIP_SLEEVE"]

    print("=" * 90)
    print(f"KNOCKOUT AUDIT: {label}")
    print("=" * 90)
    print(f"Total Trades:     {len(tdf)} (Donchian: {len(donch_trades)} | Dip Sleeve: {len(dip_trades)})")
    print(f"Win Rate:         {wr:.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor:    {pf:.3f}")
    print(f"Total Net R:      {tdf['r_multiple'].sum():+.2f}R (Donchian: {donch_trades['r_multiple'].sum():+.1f}R | Dip: {dip_trades['r_multiple'].sum():+.1f}R)")
    print(f"Mean Expectancy:  {tdf['r_multiple'].mean():+.3f}R / trade")
    print(f"Final NAV:        ${final_nav:,.2f}")
    print(f"Compounded CAGR:  {cagr:.2f}%")
    print(f"Max MTM DD:       {max_dd:.2f}%")
    print(f"Daily MTM Sharpe: {sharpe:.2f}")
    print("=" * 90)

if __name__ == "__main__":
    dfs = load_data()

    # Book A: BASE Donchian next-open
    t_a, eq_a = run_portfolio_sim(dfs, mode="A")
    audit_book(t_a, eq_a, "BOOK A: BASE DONCHIAN (Next-Open Fill, 1-Lot)")

    # Book B: GOVERNED Donchian next-open
    t_b, eq_b = run_portfolio_sim(dfs, mode="B")
    audit_book(t_b, eq_b, "BOOK B: GOVERNED DONCHIAN (+1R Add, Next-Open)")

    # Book C: Book B + Governed Dip Sleeve (Max 1 slot, idle capacity only)
    t_c, eq_c = run_portfolio_sim(dfs, mode="C")
    audit_book(t_c, eq_c, "BOOK C: GOVERNED + DIP SLEEVE (Idle Slot Only, Max 1)")
