#!/usr/bin/env python3
"""
PAIRWISE VELEZ HYBRID TOURNAMENT
Tests Velez 20/200 paired with each of the 4 passing systems.
"""

import inspect
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from tournament_engine import (
    load_and_prep_data,
    Position,
    close_trade,
    check_open_gap,
    check_intraday_ohlc,
    calculate_mtm_nav,
    INITIAL_NAV,
    RISK_PER_TRADE_PCT,
    SLIPPAGE_PCT,
    MAX_POSITIONS_PER_EXPERT,
    sell_regulatory_fees,
    UNIVERSE,
    BENCHMARK,
)

# =============================================================================
# HYBRID EXPERTS
# =============================================================================

def signal_velez_sector_topdown(df: pd.DataFrame, idx: int, pos: Optional[Position], spy_df: pd.DataFrame) -> dict:
    """Hybrid A: Velez + Sector Relative Strength Filter"""
    row = df.iloc[idx]
    date = df.index[idx]

    if date not in spy_df.index or idx < 20:
        return {"signal": "HOLD"}

    if pd.isna(row["sma200"]) or pd.isna(row["ema20"]) or pd.isna(row["atr14"]):
        return {"signal": "HOLD"}

    stock_base = float(df["close"].iloc[idx - 20])
    spy_hist = spy_df.loc[:date]
    if len(spy_hist) < 21:
        return {"signal": "HOLD"}

    spy_now = float(spy_hist["close"].iloc[-1])
    spy_base = float(spy_hist["close"].iloc[-21])
    if stock_base <= 0 or spy_base <= 0:
        return {"signal": "HOLD"}

    stock_perf_20 = (float(row["close"]) / stock_base) - 1.0
    spy_perf_20 = (spy_now / spy_base) - 1.0

    if pos is None:
        if (
            row["close"] > row["sma200"]
            and row["close"] > row["ema20"]
            and abs(row["low"] - row["ema20"]) / row["ema20"] < 0.015
            and row["close"] > row["open"]
            and row["atr_ratio"] <= 0.055
            and stock_perf_20 > (spy_perf_20 + 0.03)
        ):
            return {
                "signal": "BUY",
                "stop": float(row["low"]) - 0.5 * float(row["atr14"]),
                "target_r": 3.0,
            }
    return {"signal": "HOLD"}


def signal_velez_momentum_burst(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    """Hybrid B: Velez + Momentum Burst Volume Surge"""
    row = df.iloc[idx]
    if pd.isna(row["sma200"]) or pd.isna(row["ema20"]) or pd.isna(row["vol_surge"]):
        return {"signal": "HOLD"}

    if pos is None:
        if (
            row["close"] > row["sma200"]
            and row["close"] > row["ema20"]
            and abs(row["low"] - row["ema20"]) / row["ema20"] < 0.015
            and row["close"] > row["open"]
            and row["vol_surge"] > 1.50
            and row["roc5"] > 0.0
            and row["atr_ratio"] <= 0.055
        ):
            return {
                "signal": "BUY",
                "stop": float(row["low"]) - 0.5 * float(row["atr14"]),
                "target_r": 3.0,
            }
    return {"signal": "HOLD"}


def signal_velez_weinstein_stage2(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    """Hybrid C: Velez + Weinstein Stage 2 Alignment"""
    row = df.iloc[idx]
    if pd.isna(row["sma200"]) or pd.isna(row["sma50"]) or pd.isna(row["ema20"]):
        return {"signal": "HOLD"}

    if pos is None:
        if (
            row["ema20"] > row["sma50"] > row["sma200"]
            and row["close"] > row["ema20"]
            and abs(row["low"] - row["ema20"]) / row["ema20"] < 0.015
            and row["close"] > row["open"]
            and row["vol_surge"] > 1.25
            and row["atr_ratio"] <= 0.055
        ):
            return {
                "signal": "BUY",
                "stop": float(row["low"]) - 0.5 * float(row["atr14"]),
                "target_r": 3.0,
            }
    return {"signal": "HOLD"}


def signal_velez_confluence(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    """Hybrid D: Velez + Confluence Oversold Timing"""
    row = df.iloc[idx]
    if pd.isna(row["sma200"]) or pd.isna(row["ema20"]) or pd.isna(row["rsi2"]):
        return {"signal": "HOLD"}

    if pos is None:
        if (
            row["close"] > row["sma200"]
            and row["close"] > row["ema20"]
            and abs(row["low"] - row["ema20"]) / row["ema20"] < 0.02
            and row["close"] > row["open"]
            and row["rsi2"] < 35.0
            and row["atr_ratio"] <= 0.055
        ):
            return {
                "signal": "BUY",
                "stop": float(row["low"]) - 0.5 * float(row["atr14"]),
                "target_r": 3.0,
            }
    return {"signal": "HOLD"}


HYBRIDS = {
    "Velez + Sector TopDown (#8)": signal_velez_sector_topdown,
    "Velez + Momentum Burst (#5)": signal_velez_momentum_burst,
    "Velez + Weinstein Stage2 (#3)": signal_velez_weinstein_stage2,
    "Velez + Multi Confluence (#7)": signal_velez_confluence,
}

# =============================================================================
# FLEXIBLE BACKTEST RUNNER
# =============================================================================

def run_hybrid_backtest(name: str, fn: Callable, dfs: Dict[str, pd.DataFrame]) -> Tuple[dict, List, pd.DataFrame]:
    spy_df = dfs[BENCHMARK]
    common_dates = sorted(set.intersection(*[set(dfs[s].index) for s in UNIVERSE + [BENCHMARK]]))
    needs_spy = len(inspect.signature(fn).parameters) == 4

    realized_nav = INITIAL_NAV
    open_positions: Dict[str, Position] = {}
    trades: List = []
    equity_rows: List[dict] = []

    def call_signal(df, idx, pos):
        return fn(df, idx, pos, spy_df) if needs_spy else fn(df, idx, pos)

    for t_idx in range(199, len(common_dates) - 1):
        curr_date = common_dates[t_idx]
        next_date = common_dates[t_idx + 1]

        # 1. Evaluate at bar t close
        exit_intents: Dict[str, str] = {}
        for sym, pos in list(open_positions.items()):
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)
            res = call_signal(df, idx, pos)
            if res.get("signal") == "EXIT":
                exit_intents[sym] = res.get("reason", "SIGNAL_EXIT")

        entry_intents: List[Tuple[str, dict]] = []
        for sym in UNIVERSE:
            if sym in open_positions:
                continue
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)
            res = call_signal(df, idx, None)
            if res.get("signal") in ("BUY", "SELL"):
                entry_intents.append((sym, res))

        # 2. t+1 Open: Gap exits & signal exits
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            row = dfs[sym].loc[next_date]
            gap_event = check_open_gap(pos, row)
            if gap_event:
                px, reason = gap_event
                t, nav_chg = close_trade(name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        for sym, reason in exit_intents.items():
            if sym in open_positions:
                pos = open_positions[sym]
                row = dfs[sym].loc[next_date]
                raw_open = float(row["open"])
                px = raw_open * (1.0 - SLIPPAGE_PCT) if pos.side == "BUY" else raw_open * (1.0 + SLIPPAGE_PCT)
                t, nav_chg = close_trade(name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        # 3. t+1 Open: Execute new entries
        for sym, res in entry_intents:
            if len(open_positions) >= MAX_POSITIONS_PER_EXPERT or sym in open_positions:
                continue

            row = dfs[sym].loc[next_date]
            side = res["signal"]
            raw_open = float(row["open"])
            entry_fill = raw_open * (1.0 + SLIPPAGE_PCT) if side == "BUY" else raw_open * (1.0 - SLIPPAGE_PCT)
            initial_stop = float(res["stop"])

            if (side == "BUY" and initial_stop >= entry_fill) or (side == "SELL" and initial_stop <= entry_fill):
                continue

            risk_per_share = abs(entry_fill - initial_stop)
            if not np.isfinite(risk_per_share) or risk_per_share <= 0:
                continue

            nav_open = calculate_mtm_nav(realized_nav, open_positions, dfs, next_date, price_col="open")
            risk_dollars = nav_open * RISK_PER_TRADE_PCT
            qty = int(risk_dollars / risk_per_share)
            if qty <= 0:
                continue

            entry_fees = sell_regulatory_fees(entry_fill, qty) if side == "SELL" else 0.0
            realized_nav -= entry_fees

            target_r = res.get("target_r")
            tp = None
            if target_r is not None:
                tp = entry_fill + (float(target_r) * risk_per_share) if side == "BUY" else entry_fill - (float(target_r) * risk_per_share)

            open_positions[sym] = Position(
                symbol=sym,
                side=side,
                entry_date=next_date,
                entry_price=entry_fill,
                initial_stop=initial_stop,
                current_stop=initial_stop,
                initial_risk_per_share=risk_per_share,
                qty=qty,
                tp=tp,
                entry_fees=entry_fees,
            )

        # 4. t+1 Intraday: Conservative stop-first
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            row = dfs[sym].loc[next_date]
            event = check_intraday_ohlc(pos, row)
            if event:
                px, reason = event
                t, nav_chg = close_trade(name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        # 5. MTM Close
        mtm = calculate_mtm_nav(realized_nav, open_positions, dfs, next_date, price_col="close")
        equity_rows.append({"date": next_date, "realized_nav": realized_nav, "mtm_nav": mtm, "positions": len(open_positions)})

    # Final Close Liquidation
    final_date = common_dates[-1]
    for sym in list(open_positions.keys()):
        pos = open_positions[sym]
        raw_close = float(dfs[sym].loc[final_date, "close"])
        px = raw_close * (1.0 - SLIPPAGE_PCT) if pos.side == "BUY" else raw_close * (1.0 + SLIPPAGE_PCT)
        t, nav_chg = close_trade(name, pos, final_date, px, "END_OF_TEST")
        realized_nav += nav_chg
        trades.append(t)

    equity_df = pd.DataFrame(equity_rows).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    pnls = np.array([t.pnl for t in trades], dtype=float)
    rvals = np.array([t.r_mult for t in trades], dtype=float)

    wins = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    pf = (wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)
    win_rate = float((pnls > 0).mean() * 100.0) if len(pnls) else 0.0
    exp_r = float(np.mean(rvals)) if len(rvals) else 0.0
    med_r = float(np.median(rvals)) if len(rvals) else 0.0

    eq = equity_df["mtm_nav"].to_numpy()
    if len(eq):
        peaks = np.maximum.accumulate(eq)
        drawdowns = (peaks - eq) / np.where(peaks != 0, peaks, np.nan)
        max_dd = float(np.nanmax(drawdowns) * 100.0)
    else:
        max_dd = 0.0

    total_net = realized_nav - INITIAL_NAV
    total_ret = (realized_nav / INITIAL_NAV - 1.0) * 100.0
    passes_gate = bool(len(trades) >= 15 and pf >= 1.40 and exp_r >= 0.15 and max_dd <= 8.0)

    metrics = {
        "trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": pf,
        "expectancy_r": exp_r,
        "median_r": med_r,
        "net_pnl": total_net,
        "return_pct": total_ret,
        "max_drawdown": max_dd,
        "final_nav": realized_nav,
        "pass_phase1": passes_gate,
    }

    return metrics, trades, equity_df


def main():
    print("=" * 110)
    print("PAIRWISE VELEZ HYBRID TOURNAMENT")
    print("Testing Confluence of Velez 20/200 with the 4 Qualified Strategies")
    print("=" * 110)

    dfs = load_and_prep_data()
    leaderboard = []

    for name, fn in HYBRIDS.items():
        metrics, trades, equity = run_hybrid_backtest(name, fn, dfs)

        leaderboard.append({
            "Hybrid Strategy": name,
            "Trades": metrics["trades"],
            "Win Rate": f"{metrics['win_rate']:.1f}%",
            "PF": f"{metrics['profit_factor']:.2f}",
            "Exp (R)": f"{metrics['expectancy_r']:+.3f}R",
            "Median R": f"{metrics['median_r']:+.3f}R",
            "Net PnL": f"${metrics['net_pnl']:+,.2f}",
            "Return": f"{metrics['return_pct']:+.2f}%",
            "Max DD": f"{metrics['max_drawdown']:.2f}%",
            "Gate": "PASS" if metrics["pass_phase1"] else "FAIL",
        })

    summary_df = pd.DataFrame(leaderboard)
    print(summary_df.to_string(index=False))
    print("=" * 110)
    print("QUALIFICATION: PF >= 1.40 | Exp >= +0.15R | Max DD <= 8.00% | Trades >= 15")
    print("=" * 110)


if __name__ == "__main__":
    main()
