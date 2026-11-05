#!/usr/bin/env python3
"""
PHASE 1B — STANDARDIZED PROTOTYPE TOURNAMENT (OPTIMIZED ENGINE)
RESEARCH ONLY — NO BROKER CONNECTIONS — NO ORDERS
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path("/home/moonrider1968/bots/stock_data")
OUTPUT_DIR = Path("/home/moonrider1968/bots/tournament_phase1b_output")

UNIVERSE = ["AAPL", "NVDA", "MSFT", "AMD", "TSLA", "AMZN", "GOOGL", "META", "XLK", "QQQ"]
BENCHMARK = "SPY"

INITIAL_NAV = 100_000.0
RISK_PER_TRADE_PCT = 0.0035  # 0.35%
SLIPPAGE_PCT = 0.0005        # 0.05%
MAX_POSITIONS_PER_EXPERT = 3

# Regulatory sell-side fee rates
SEC_RATE = 0.0000278
FINRA_TAF_PER_SHARE = 0.000166
CAT_PER_SHARE = 0.0000265

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Trade:
    symbol: str
    expert: str
    side: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    qty: int
    initial_stop: float
    initial_risk_per_share: float
    initial_risk_dollars: float
    gross_pnl: float
    fees: float
    pnl: float
    pnl_pct: float
    r_mult: float
    exit_reason: str


@dataclass
class Position:
    symbol: str
    side: str
    entry_date: pd.Timestamp
    entry_price: float
    initial_stop: float
    current_stop: float
    initial_risk_per_share: float
    qty: int
    tp: Optional[float]
    entry_fees: float


# =============================================================================
# COST MODEL
# =============================================================================

def sell_regulatory_fees(price: float, qty: int) -> float:
    if qty <= 0 or price <= 0:
        return 0.0
    notional = float(price) * int(qty)
    sec = notional * SEC_RATE
    finra = min(8.30, FINRA_TAF_PER_SHARE * qty)
    cat = CAT_PER_SHARE * qty
    return float(sec + finra + cat)


# =============================================================================
# DATA PREPARATION (OPTIMIZED PIPELINE)
# =============================================================================

def load_and_prep_data() -> Dict[str, pd.DataFrame]:
    dfs: Dict[str, pd.DataFrame] = {}
    required = UNIVERSE + [BENCHMARK]

    for sym in required:
        p = DATA_DIR / f"{sym}_daily.csv"
        if not p.exists():
            raise FileNotFoundError(f"Missing required data file: {p}")

        df = pd.read_csv(p, parse_dates=["timestamp"])
        df = df.set_index("timestamp").sort_index()
        df = df[~df.index.duplicated(keep="last")].copy()

        # Moving Averages
        close = df["close"]
        df["ema20"] = close.ewm(span=20, adjust=False).mean()
        df["sma5"] = close.rolling(5, min_periods=5).mean()
        df["sma50"] = close.rolling(50, min_periods=50).mean()
        df["sma200"] = close.rolling(200, min_periods=200).mean()

        # Donchian Channels (shifted to prevent lookahead)
        df["donch_high_30"] = df["high"].rolling(30, min_periods=30).max().shift(1)
        df["donch_low_30"] = df["low"].rolling(30, min_periods=30).min().shift(1)

        # Average True Range (ATR 14)
        prev_close = close.shift(1)
        tr = np.maximum(
            df["high"] - df["low"],
            np.maximum((df["high"] - prev_close).abs(), (df["low"] - prev_close).abs())
        )
        df["atr14"] = tr.rolling(14, min_periods=14).mean()
        df["atr_ratio"] = df["atr14"] / close.replace(0, np.nan)
        atr_baseline = df["atr14"].rolling(30, min_periods=30).mean()
        df["atr_expansion"] = df["atr14"] / atr_baseline.replace(0, np.nan)

        # RSI(2)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(2, min_periods=2).mean()
        loss = (-delta.clip(upper=0)).rolling(2, min_periods=2).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi2"] = 100.0 - (100.0 / (1.0 + rs))
        df.loc[(loss == 0) & (gain > 0), "rsi2"] = 100.0
        df.loc[(loss == 0) & (gain == 0), "rsi2"] = 50.0

        # Bollinger Bands
        df["bb_mid"] = close.rolling(20, min_periods=20).mean()
        df["bb_std"] = close.rolling(20, min_periods=20).std()
        df["bb_upper"] = df["bb_mid"] + 2.0 * df["bb_std"]
        df["bb_lower"] = df["bb_mid"] - 2.0 * df["bb_std"]

        # Volume / Momentum
        df["vol_sma20"] = df["volume"].rolling(20, min_periods=20).mean()
        df["vol_surge"] = df["volume"] / df["vol_sma20"].replace(0, np.nan)
        df["roc5"] = close.pct_change(5)

        dfs[sym] = df

    return dfs


# =============================================================================
# STRATEGY ADAPTERS
# =============================================================================

def signal_velez_reversal(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pos is None:
        if pd.isna(row["sma200"]) or pd.isna(row["ema20"]) or pd.isna(row["atr14"]):
            return {"signal": "HOLD"}

        if (
            row["close"] > row["sma200"]
            and row["close"] > row["ema20"]
            and abs(row["low"] - row["ema20"]) / row["ema20"] < 0.015
            and row["close"] > row["open"]
            and row["atr_ratio"] <= 0.055
        ):
            return {
                "signal": "BUY",
                "stop": float(row["low"]) - 0.5 * float(row["atr14"]),
                "target_r": 3.0,
            }
    return {"signal": "HOLD"}


def signal_g3_b5_donchian(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["donch_high_30"]) or pd.isna(row["atr14"]):
        return {"signal": "HOLD"}

    if pos is None:
        if row["close"] > row["donch_high_30"] and row["atr_ratio"] <= 0.055:
            return {
                "signal": "BUY",
                "stop": float(row["close"]) - 2.0 * float(row["atr14"]),
                "target_r": None,
            }
    else:
        if pos.side == "BUY":
            candidate = float(row["close"]) - 2.0 * float(row["atr14"])
            if candidate > pos.current_stop:
                pos.current_stop = candidate

    return {"signal": "HOLD"}


def signal_weinstein_stage2(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["sma200"]) or pd.isna(row["sma50"]) or pd.isna(row["donch_high_30"]):
        return {"signal": "HOLD"}

    if pos is None:
        if (
            row["close"] > row["sma50"] > row["sma200"]
            and row["close"] > row["donch_high_30"]
            and row["vol_surge"] > 1.25
        ):
            return {
                "signal": "BUY",
                "stop": float(row["sma50"]) - 0.5 * float(row["atr14"]),
                "target_r": None,
            }
    else:
        if row["close"] < row["sma50"]:
            return {"signal": "EXIT", "reason": "STAGE2_BREAKDOWN"}

    return {"signal": "HOLD"}


def signal_weinstein_stage4_short(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["sma200"]) or pd.isna(row["sma50"]) or pd.isna(row["donch_low_30"]):
        return {"signal": "HOLD"}

    if pos is None:
        if (
            row["close"] < row["sma50"] < row["sma200"]
            and row["close"] < row["donch_low_30"]
            and row["vol_surge"] > 1.20
        ):
            return {
                "signal": "SELL",
                "stop": float(row["close"]) + 1.5 * float(row["atr14"]),
                "target_r": 2.5,
            }
    return {"signal": "HOLD"}


def signal_momentum_burst(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["roc5"]) or pd.isna(row["vol_surge"]) or pd.isna(row["ema20"]):
        return {"signal": "HOLD"}

    if pos is None:
        if (
            row["roc5"] > 0.04
            and row["vol_surge"] > 1.80
            and row["close"] > row["ema20"]
            and row["atr_expansion"] > 1.10
        ):
            return {
                "signal": "BUY",
                "stop": float(row["close"]) - 1.5 * float(row["atr14"]),
                "target_r": 2.0,
            }
    return {"signal": "HOLD"}


def signal_connors_mean_rev(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["sma200"]) or pd.isna(row["rsi2"]) or pd.isna(row["sma5"]):
        return {"signal": "HOLD"}

    if pos is None:
        if row["close"] > row["sma200"] and row["rsi2"] < 10.0:
            return {
                "signal": "BUY",
                "stop": float(row["low"]) - float(row["atr14"]),
                "target_r": None,
            }
    else:
        if row["close"] > row["sma5"]:
            return {"signal": "EXIT", "reason": "CONNORS_MEAN_REVERT"}

    return {"signal": "HOLD"}


def signal_multi_confluence(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["ema20"]) or pd.isna(row["bb_lower"]) or pd.isna(row["rsi2"]):
        return {"signal": "HOLD"}

    if pos is None:
        score = sum([
            row["close"] > row["ema20"],
            row["close"] > row["sma50"],
            row["low"] <= row["bb_lower"] * 1.01,
            row["rsi2"] < 25.0,
            row["vol_surge"] > 1.0,
        ])
        if score >= 4:
            return {
                "signal": "BUY",
                "stop": float(row["low"]) - 0.75 * float(row["atr14"]),
                "target_r": 2.5,
            }
    return {"signal": "HOLD"}


def signal_sector_topdown(df: pd.DataFrame, idx: int, pos: Optional[Position], spy_df: pd.DataFrame) -> dict:
    row = df.iloc[idx]
    date = df.index[idx]

    if date not in spy_df.index or idx < 20 or pd.isna(row["ema20"]):
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
        if stock_perf_20 > (spy_perf_20 + 0.03) and row["close"] > row["ema20"] > row["sma50"]:
            return {
                "signal": "BUY",
                "stop": float(row["ema20"]) - float(row["atr14"]),
                "target_r": None,
            }
    else:
        if row["close"] < row["ema20"]:
            return {"signal": "EXIT", "reason": "TOPDOWN_EMA20_BREAK"}

    return {"signal": "HOLD"}


EXPERTS: Dict[str, Callable] = {
    "1. Velez 20/200 Reversal": signal_velez_reversal,
    "2. G3-B5 Donchian Trend": signal_g3_b5_donchian,
    "3. Weinstein Stage 2 Long": signal_weinstein_stage2,
    "4. Weinstein Stage 4 Short": signal_weinstein_stage4_short,
    "5. Momentum Burst": signal_momentum_burst,
    "6. Connors Mean-Rev": signal_connors_mean_rev,
    "7. Multi-Layer Confluence": signal_multi_confluence,
    "8. Sector Top-Down": signal_sector_topdown,
}

# =============================================================================
# EXECUTION & SETTLEMENT HELPERS
# =============================================================================

def close_trade(
    expert: str,
    pos: Position,
    exit_date: pd.Timestamp,
    exit_price: float,
    exit_reason: str,
) -> Tuple[Trade, float]:
    qty = pos.qty
    if pos.side == "BUY":
        gross = (exit_price - pos.entry_price) * qty
        exit_fees = sell_regulatory_fees(exit_price, qty)
        pnl_pct = (exit_price / pos.entry_price) - 1.0
    else:
        gross = (pos.entry_price - exit_price) * qty
        exit_fees = 0.0
        pnl_pct = (pos.entry_price - exit_price) / pos.entry_price

    total_fees = pos.entry_fees + exit_fees
    net_pnl = gross - total_fees
    init_risk_dollars = pos.initial_risk_per_share * qty
    r_mult = net_pnl / init_risk_dollars if init_risk_dollars > 0 else 0.0

    trade = Trade(
        symbol=pos.symbol,
        expert=expert,
        side=pos.side,
        entry_date=str(pos.entry_date.date()),
        entry_price=pos.entry_price,
        exit_date=str(exit_date.date()),
        exit_price=exit_price,
        qty=qty,
        initial_stop=pos.initial_stop,
        initial_risk_per_share=pos.initial_risk_per_share,
        initial_risk_dollars=init_risk_dollars,
        gross_pnl=gross,
        fees=total_fees,
        pnl=net_pnl,
        pnl_pct=pnl_pct,
        r_mult=r_mult,
        exit_reason=exit_reason,
    )
    nav_change = gross - exit_fees
    return trade, nav_change


def check_open_gap(pos: Position, row: pd.Series) -> Optional[Tuple[float, str]]:
    open_px = float(row["open"])
    if pos.side == "BUY":
        if open_px <= pos.current_stop:
            return open_px * (1.0 - SLIPPAGE_PCT), "STOP_GAP_LOSS"
        if pos.tp is not None and open_px >= pos.tp:
            return max(pos.tp, open_px) * (1.0 - SLIPPAGE_PCT), "TARGET_GAP"
    else:
        if open_px >= pos.current_stop:
            return open_px * (1.0 + SLIPPAGE_PCT), "STOP_GAP_LOSS"
        if pos.tp is not None and open_px <= pos.tp:
            return min(pos.tp, open_px) * (1.0 + SLIPPAGE_PCT), "TARGET_GAP"
    return None


def check_intraday_ohlc(pos: Position, row: pd.Series) -> Optional[Tuple[float, str]]:
    high = float(row["high"])
    low = float(row["low"])
    stop = pos.current_stop
    tp = pos.tp

    if pos.side == "BUY":
        stop_hit = low <= stop
        tp_hit = tp is not None and high >= tp
        if stop_hit:
            return stop * (1.0 - SLIPPAGE_PCT), "STOP_LOSS"
        if tp_hit:
            return tp * (1.0 - SLIPPAGE_PCT), "TAKE_PROFIT"
    else:
        stop_hit = high >= stop
        tp_hit = tp is not None and low <= tp
        if stop_hit:
            return stop * (1.0 + SLIPPAGE_PCT), "STOP_LOSS"
        if tp_hit:
            return tp * (1.0 + SLIPPAGE_PCT), "TAKE_PROFIT"
    return None


def calculate_mtm_nav(realized_nav: float, open_positions: Dict[str, Position], dfs: Dict[str, pd.DataFrame], date: pd.Timestamp, price_col: str = "close") -> float:
    nav = realized_nav
    for sym, pos in open_positions.items():
        if date in dfs[sym].index:
            px = float(dfs[sym].loc[date, price_col])
            unrealized = (px - pos.entry_price) * pos.qty if pos.side == "BUY" else (pos.entry_price - px) * pos.qty
            nav += unrealized
    return nav


# =============================================================================
# BACKTEST CORE ENGINE
# =============================================================================

def backtest_expert(name: str, fn: Callable, dfs: Dict[str, pd.DataFrame]) -> Tuple[dict, List[Trade], pd.DataFrame]:
    spy_df = dfs[BENCHMARK]
    common_dates = sorted(set.intersection(*[set(dfs[s].index) for s in UNIVERSE + [BENCHMARK]]))

    if len(common_dates) < 250:
        raise RuntimeError(f"Insufficient history: {len(common_dates)} common bars.")

    realized_nav = INITIAL_NAV
    open_positions: Dict[str, Position] = {}
    trades: List[Trade] = []
    equity_rows: List[dict] = []

    for t_idx in range(199, len(common_dates) - 1):
        curr_date = common_dates[t_idx]
        next_date = common_dates[t_idx + 1]

        # 1. Signals at bar t close
        exit_intents: Dict[str, str] = {}
        for sym, pos in list(open_positions.items()):
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)
            res = fn(df, idx, pos, spy_df) if name == "8. Sector Top-Down" else fn(df, idx, pos)
            if res.get("signal") == "EXIT":
                exit_intents[sym] = res.get("reason", "SIGNAL_EXIT")

        entry_intents: List[Tuple[str, dict]] = []
        for sym in UNIVERSE:
            if sym in open_positions:
                continue
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)
            res = fn(df, idx, None, spy_df) if name == "8. Sector Top-Down" else fn(df, idx, None)
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

        # 3. t+1 Open: Entries
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

        # 4. t+1 Intraday: Stop vs TP (Stop First)
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

        # 5. Mark-to-market at close
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


# =============================================================================
# DRIVER
# =============================================================================

def main():
    print("=" * 110)
    print("PHASE 1B — STANDARDIZED PROTOTYPE TOURNAMENT ENGINE")
    print("Execution Model: Strict t -> t+1 Open Fill | Conservative Stop-First | True MTM Drawdown")
    print("=" * 110)

    dfs = load_and_prep_data()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    leaderboard = []

    for name, fn in EXPERTS.items():
        metrics, trades, equity = backtest_expert(name, fn, dfs)
        slug = name.lower().replace(" ", "_").replace("/", "_").replace(".", "")

        pd.DataFrame([t.__dict__ for t in trades]).to_csv(OUTPUT_DIR / f"{slug}_trades.csv", index=False)
        equity.to_csv(OUTPUT_DIR / f"{slug}_equity.csv", index=False)

        leaderboard.append({
            "Expert": name,
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
    print("GATES: PF >= 1.40 | Expectancy >= +0.15R | MTM Max DD <= 8.00% | Trades >= 15")
    summary_df.to_csv(OUTPUT_DIR / "phase1b_leaderboard.csv", index=False)
    print(f"Artifacts saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
