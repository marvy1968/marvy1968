#!/usr/bin/env python3
"""
VALEZE2 -> OANDA H4 PARITY TEST
===============================

Strategies
----------
V1 = Velez 200-SMA Rubber-Band Snapback, adapted to FX/H4
C1 = Connors RSI(2) Strict Panic, adapted to FX/H4
F0 = Existing OANDA baseline loaded from an existing event CSV ONLY.
     F0 is NOT reconstructed here because its exact signal rules are not
     duplicated in the Valeze2 source document.

Design goals
------------
* No look-ahead.
* H4 signal generated only from completed bar t.
* Entry occurs on next bar open t+1.
* Intrabar stop handling.
* Close-derived recovery exits execute next bar open.
* Spread/slippage expressed explicitly.
* 0.50% dynamic NAV risk.
* Max three concurrent trades in portfolio simulation.
* Pair/year breakdowns.
* Long-only source-faithful test by default.
* Optional mirrored long+short research mode.

Important FX adaptations
------------------------
1. Equity $0.15 stop offset has no universal meaning in FX.
   V1 uses 0.15 * ATR14 beyond signal-bar low/high instead.
2. Raw equity SMA200 slope units are not portable.
   Non-bear/non-bull is normalized as 20-bar percentage slope.
3. Equity volume is not involved in V1 or C1, so no synthetic volume proxy
   is required for these first two tests.

This is a RESEARCH/BACKTEST SCRIPT. It places NO orders.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# =====================================================================
# CONFIG
# =====================================================================

ROOT = Path("/home/moonrider1968/bots")
DATA_DIR = ROOT / "oanda_data"
OUT_DIR = ROOT / "valeze2_oanda_h4_results"

PAIRS = [
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "AUD_USD",
    "USD_CAD",
    "NZD_USD",
    "USD_CHF",
    "EUR_JPY",
    "GBP_JPY",
]

INITIAL_NAV = 100_000.00
RISK_PCT = 0.0050
MAX_POSITIONS = 3

# Require enough history for SMA200 + stable indicators.
WARMUP_BARS = 220

# ---------------------------------------------------------------------
# V1 FX adaptation
# ---------------------------------------------------------------------
V1_RSI2_MAX = 25.0
V1_SMA200_PROX_ATR = 0.35

# Equity source says SMA200_Slope >= -0.15.
# Raw dollar slope does not port across FX prices.
# Here: allow at most -0.15% change over the prior 20 H4 bars.
V1_SMA200_SLOPE_LOOKBACK = 20
V1_MIN_SMA200_SLOPE_PCT = -0.15

# Equity source uses $0.15 below candle low.
# FX-normalized first-pass replacement:
V1_STOP_OFFSET_ATR = 0.15

V1_FIXED_TARGET_R = 1.50

# ---------------------------------------------------------------------
# C1 source parameters
# ---------------------------------------------------------------------
C1_RSI2_ENTRY = 5.0
C1_RSI2_EXIT = 70.0
C1_STOP_ATR = 2.50

# ---------------------------------------------------------------------
# Execution friction
# ---------------------------------------------------------------------
# Conservative first-pass round-trip cost approximation.
#
# We model HALF of this spread on entry and HALF on exit, so total
# transaction spread ~= listed pips.
#
# If you later have historical bid/ask H4 bars, replace this static
# approximation with actual bid/ask execution.
SPREAD_PIPS = {
    "EUR_USD": 0.8,
    "GBP_USD": 1.2,
    "USD_JPY": 0.9,
    "AUD_USD": 1.0,
    "USD_CAD": 1.2,
    "NZD_USD": 1.3,
    "USD_CHF": 1.2,
    "EUR_JPY": 1.3,
    "GBP_JPY": 1.8,
}

# Additional one-way adverse slippage.
SLIPPAGE_PIPS_ONE_WAY = 0.10


# =====================================================================
# DATA STRUCTURES
# =====================================================================

@dataclass
class Trade:
    strategy: str
    instrument: str
    direction: str

    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp

    entry_price: float
    stop_price: float
    exit_price: float

    initial_risk_price: float
    gross_r: float
    cost_r: float
    net_r: float

    exit_reason: str
    holding_bars: int

    signal_rsi2: float
    signal_atr14: float
    signal_sma200_slope_pct: float


# =====================================================================
# INDICATORS
# =====================================================================

def rsi(series: pd.Series, period: int) -> pd.Series:
    """
    Wilder RSI.
    """
    delta = series.diff()

    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(
        alpha=1.0 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1.0 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))

    # Correct mathematical edge cases.
    out = out.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)

    return out


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(
        alpha=1.0 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()

    x["sma5"] = x["close"].rolling(5).mean()
    x["sma20"] = x["close"].rolling(20).mean()
    x["sma200"] = x["close"].rolling(200).mean()

    x["atr14"] = atr(x, 14)
    x["rsi2"] = rsi(x["close"], 2)

    lb = V1_SMA200_SLOPE_LOOKBACK
    x["sma200_slope_pct"] = (
        (x["sma200"] / x["sma200"].shift(lb) - 1.0) * 100.0
    )

    return x


# =====================================================================
# DATA LOADING
# =====================================================================

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename = {}

    for c in df.columns:
        lc = str(c).strip().lower()

        if lc in {"time", "timestamp", "datetime", "date"}:
            rename[c] = "time"
        elif lc in {"open", "o", "mid_o", "mid_open"}:
            rename[c] = "open"
        elif lc in {"high", "h", "mid_h", "mid_high"}:
            rename[c] = "high"
        elif lc in {"low", "l", "mid_l", "mid_low"}:
            rename[c] = "low"
        elif lc in {"close", "c", "mid_c", "mid_close"}:
            rename[c] = "close"
        elif lc in {"volume", "vol"}:
            rename[c] = "volume"

    return df.rename(columns=rename)


def candidate_files(pair: str) -> List[Path]:
    """
    Flexible discovery without silently selecting unrelated data.
    """
    aliases = {
        pair.lower(),
        pair.replace("_", "").lower(),
        pair.replace("_", "-").lower(),
    }

    found = []

    if not DATA_DIR.exists():
        return found

    for p in DATA_DIR.rglob("*.csv"):
        name = p.stem.lower()

        if any(alias in name for alias in aliases):
            found.append(p)

    # Prefer obvious H4 files.
    found.sort(
        key=lambda p: (
            0 if "h4" in p.stem.lower() else 1,
            len(str(p)),
            str(p),
        )
    )

    return found


def load_pair(pair: str) -> pd.DataFrame:
    files = candidate_files(pair)

    if not files:
        raise FileNotFoundError(
            f"No CSV located for {pair} under {DATA_DIR}"
        )

    errors = []

    for path in files:
        try:
            df = pd.read_csv(path)
            df = normalize_columns(df)

            required = {"open", "high", "low", "close"}

            if not required.issubset(df.columns):
                errors.append(
                    f"{path}: missing {sorted(required - set(df.columns))}"
                )
                continue

            if "time" in df.columns:
                dt = pd.to_datetime(
                    df["time"],
                    utc=True,
                    errors="coerce",
                )
                df = df.loc[dt.notna()].copy()
                df.index = dt[dt.notna()]
            else:
                # Check whether CSV index accidentally contains timestamps.
                first = df.columns[0]
                maybe_dt = pd.to_datetime(
                    df[first],
                    utc=True,
                    errors="coerce",
                )

                if maybe_dt.notna().mean() > 0.95:
                    df = df.loc[maybe_dt.notna()].copy()
                    df.index = maybe_dt[maybe_dt.notna()]
                else:
                    errors.append(
                        f"{path}: no usable timestamp column"
                    )
                    continue

            for c in ["open", "high", "low", "close"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")

            df = (
                df[["open", "high", "low", "close"]]
                .dropna()
                .sort_index()
            )

            df = df[~df.index.duplicated(keep="last")]

            if len(df) < WARMUP_BARS + 10:
                errors.append(
                    f"{path}: only {len(df)} usable bars"
                )
                continue

            print(
                f"[DATA] {pair:8s} -> {path} "
                f"({len(df):,} bars, "
                f"{df.index.min()} -> {df.index.max()})"
            )

            return add_indicators(df)

        except Exception as exc:
            errors.append(f"{path}: {exc}")

    raise RuntimeError(
        f"Could not load {pair}.\n  " + "\n  ".join(errors)
    )


# =====================================================================
# FX PRICE HELPERS
# =====================================================================

def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("_JPY") else 0.0001


def execution_cost_price(pair: str) -> float:
    """
    Approximate total round-trip adverse transaction cost in price units.
    """
    total_pips = SPREAD_PIPS.get(pair, 1.2)
    total_pips += 2.0 * SLIPPAGE_PIPS_ONE_WAY

    return total_pips * pip_size(pair)


# =====================================================================
# SIGNALS
# =====================================================================

def v1_long_signal(row: pd.Series) -> bool:
    required = [
        row["sma200"],
        row["atr14"],
        row["rsi2"],
        row["sma200_slope_pct"],
    ]

    if not all(np.isfinite(v) for v in required):
        return False

    # FX-normalized non-bear SMA200 condition.
    slope_ok = (
        row["sma200_slope_pct"] >= V1_MIN_SMA200_SLOPE_PCT
    )

    # Test/undercut within 0.35 ATR of SMA200.
    proximity_ok = (
        row["low"]
        <= row["sma200"] + V1_SMA200_PROX_ATR * row["atr14"]
    )

    stretched = row["rsi2"] <= V1_RSI2_MAX

    # Use the explicit source confirmation path:
    # top-30% close AND back above SMA200.
    rng = row["high"] - row["low"]

    if rng <= 0:
        return False

    close_location = (row["close"] - row["low"]) / rng

    confirmation = (
        close_location >= 0.70
        and row["close"] >= row["sma200"]
    )

    return bool(
        slope_ok
        and proximity_ok
        and stretched
        and confirmation
    )


def v1_short_signal(row: pd.Series) -> bool:
    """
    Mirrored research hypothesis ONLY.
    Not part of the source equity strategy.
    """
    required = [
        row["sma200"],
        row["atr14"],
        row["rsi2"],
        row["sma200_slope_pct"],
    ]

    if not all(np.isfinite(v) for v in required):
        return False

    slope_ok = (
        row["sma200_slope_pct"]
        <= -V1_MIN_SMA200_SLOPE_PCT
    )

    proximity_ok = (
        row["high"]
        >= row["sma200"] - V1_SMA200_PROX_ATR * row["atr14"]
    )

    stretched = row["rsi2"] >= (100.0 - V1_RSI2_MAX)

    rng = row["high"] - row["low"]

    if rng <= 0:
        return False

    close_location = (row["close"] - row["low"]) / rng

    confirmation = (
        close_location <= 0.30
        and row["close"] <= row["sma200"]
    )

    return bool(
        slope_ok
        and proximity_ok
        and stretched
        and confirmation
    )


def c1_long_signal(row: pd.Series) -> bool:
    required = [
        row["sma200"],
        row["atr14"],
        row["rsi2"],
    ]

    if not all(np.isfinite(v) for v in required):
        return False

    return bool(
        row["close"] > row["sma200"]
        and row["rsi2"] < C1_RSI2_ENTRY
    )


def c1_short_signal(row: pd.Series) -> bool:
    """
    Mirrored research hypothesis ONLY.
    """
    required = [
        row["sma200"],
        row["atr14"],
        row["rsi2"],
    ]

    if not all(np.isfinite(v) for v in required):
        return False

    return bool(
        row["close"] < row["sma200"]
        and row["rsi2"] > (100.0 - C1_RSI2_ENTRY)
    )


# =====================================================================
# SINGLE-TRADE REPLAY
# =====================================================================

def replay_v1(
    pair: str,
    df: pd.DataFrame,
    signal_i: int,
    direction: str,
) -> Optional[Trade]:

    entry_i = signal_i + 1

    if entry_i >= len(df):
        return None

    sig = df.iloc[signal_i]
    entry_bar = df.iloc[entry_i]

    signal_time = df.index[signal_i]
    entry_time = df.index[entry_i]

    atr14 = float(sig["atr14"])

    if not np.isfinite(atr14) or atr14 <= 0:
        return None

    raw_entry = float(entry_bar["open"])

    if direction == "long":
        stop = (
            float(sig["low"])
            - V1_STOP_OFFSET_ATR * atr14
        )

        if raw_entry <= stop:
            # Open gapped through stop: invalid new entry.
            return None

        risk = raw_entry - stop

    else:
        stop = (
            float(sig["high"])
            + V1_STOP_OFFSET_ATR * atr14
        )

        if raw_entry >= stop:
            return None

        risk = stop - raw_entry

    if risk <= 0:
        return None

    fixed_target = (
        raw_entry + V1_FIXED_TARGET_R * risk
        if direction == "long"
        else raw_entry - V1_FIXED_TARGET_R * risk
    )

    exit_price = None
    exit_time = None
    exit_reason = None
    exit_i = None

    # Begin replay on actual entry bar.
    for j in range(entry_i, len(df)):
        bar = df.iloc[j]

        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])

        # -------------------------------------------------------------
        # 1. Adverse open gap through stop
        # -------------------------------------------------------------
        if direction == "long" and o <= stop:
            exit_price = o
            exit_reason = "stop_gap"
            exit_i = j
            exit_time = df.index[j]
            break

        if direction == "short" and o >= stop:
            exit_price = o
            exit_reason = "stop_gap"
            exit_i = j
            exit_time = df.index[j]
            break

        # -------------------------------------------------------------
        # 2. Stop-first assumption if both stop and profit target are
        #    inside the same H4 bar. Conservative.
        # -------------------------------------------------------------
        if direction == "long":
            stop_hit = l <= stop
            fixed_hit = h >= fixed_target

        else:
            stop_hit = h >= stop
            fixed_hit = l <= fixed_target

        if stop_hit:
            exit_price = stop
            exit_reason = "stop"
            exit_i = j
            exit_time = df.index[j]
            break

        # -------------------------------------------------------------
        # 3. Dynamic SMA20 touch.
        # Only counts if SMA20 lies profitably beyond entry.
        # -------------------------------------------------------------
        sma20 = float(bar["sma20"]) if np.isfinite(bar["sma20"]) else np.nan

        sma_hit = False

        if np.isfinite(sma20):
            if direction == "long":
                if sma20 > raw_entry and h >= sma20:
                    sma_hit = True
            else:
                if sma20 < raw_entry and l <= sma20:
                    sma_hit = True

        # Whichever favorable target is reached first intrabar cannot be
        # known from OHLC. If both are touched in same bar, use the
        # less favorable of the two = conservative.
        if fixed_hit or sma_hit:
            candidates = []

            if fixed_hit:
                candidates.append(
                    (fixed_target, "fixed_1.5R")
                )

            if sma_hit:
                candidates.append(
                    (sma20, "sma20_touch")
                )

            if direction == "long":
                px, reason = min(candidates, key=lambda z: z[0])
            else:
                px, reason = max(candidates, key=lambda z: z[0])

            exit_price = float(px)
            exit_reason = reason
            exit_i = j
            exit_time = df.index[j]
            break

    if exit_price is None:
        # Force mark at final available close for research completeness.
        exit_i = len(df) - 1
        exit_time = df.index[exit_i]
        exit_price = float(df.iloc[exit_i]["close"])
        exit_reason = "end_of_data"

    gross_r = (
        (exit_price - raw_entry) / risk
        if direction == "long"
        else (raw_entry - exit_price) / risk
    )

    cost_price = execution_cost_price(pair)
    cost_r = cost_price / risk
    net_r = gross_r - cost_r

    return Trade(
        strategy="V1",
        instrument=pair,
        direction=direction,
        signal_time=signal_time,
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=raw_entry,
        stop_price=stop,
        exit_price=exit_price,
        initial_risk_price=risk,
        gross_r=gross_r,
        cost_r=cost_r,
        net_r=net_r,
        exit_reason=exit_reason,
        holding_bars=int(exit_i - entry_i + 1),
        signal_rsi2=float(sig["rsi2"]),
        signal_atr14=atr14,
        signal_sma200_slope_pct=float(
            sig["sma200_slope_pct"]
        ),
    )


def replay_c1(
    pair: str,
    df: pd.DataFrame,
    signal_i: int,
    direction: str,
) -> Optional[Trade]:

    entry_i = signal_i + 1

    if entry_i >= len(df):
        return None

    sig = df.iloc[signal_i]
    entry_bar = df.iloc[entry_i]

    atr14 = float(sig["atr14"])

    if not np.isfinite(atr14) or atr14 <= 0:
        return None

    raw_entry = float(entry_bar["open"])

    if direction == "long":
        stop = raw_entry - C1_STOP_ATR * atr14
        risk = raw_entry - stop
    else:
        stop = raw_entry + C1_STOP_ATR * atr14
        risk = stop - raw_entry

    if risk <= 0:
        return None

    exit_price = None
    exit_time = None
    exit_reason = None
    exit_i = None

    # Close-based profit condition becomes actionable NEXT bar open.
    pending_close_exit = False
    pending_reason = None

    for j in range(entry_i, len(df)):
        bar = df.iloc[j]

        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])
        c = float(bar["close"])

        # -------------------------------------------------------------
        # Execute profit signal from prior completed bar.
        # -------------------------------------------------------------
        if pending_close_exit:
            exit_price = o
            exit_reason = pending_reason
            exit_i = j
            exit_time = df.index[j]
            break

        # -------------------------------------------------------------
        # Gap stop.
        # -------------------------------------------------------------
        if direction == "long" and o <= stop:
            exit_price = o
            exit_reason = "stop_gap"
            exit_i = j
            exit_time = df.index[j]
            break

        if direction == "short" and o >= stop:
            exit_price = o
            exit_reason = "stop_gap"
            exit_i = j
            exit_time = df.index[j]
            break

        # -------------------------------------------------------------
        # Intrabar hard stop.
        # -------------------------------------------------------------
        if direction == "long" and l <= stop:
            exit_price = stop
            exit_reason = "stop"
            exit_i = j
            exit_time = df.index[j]
            break

        if direction == "short" and h >= stop:
            exit_price = stop
            exit_reason = "stop"
            exit_i = j
            exit_time = df.index[j]
            break

        # -------------------------------------------------------------
        # Source close-based recovery exit.
        # -------------------------------------------------------------
        rsi2_val = bar["rsi2"]
        sma5_val = bar["sma5"]

        if not (
            np.isfinite(rsi2_val)
            and np.isfinite(sma5_val)
        ):
            continue

        if direction == "long":
            if rsi2_val >= C1_RSI2_EXIT:
                pending_close_exit = True
                pending_reason = "rsi2_recovery_next_open"

            elif c >= sma5_val:
                pending_close_exit = True
                pending_reason = "sma5_recovery_next_open"

        else:
            if rsi2_val <= (100.0 - C1_RSI2_EXIT):
                pending_close_exit = True
                pending_reason = "rsi2_recovery_next_open"

            elif c <= sma5_val:
                pending_close_exit = True
                pending_reason = "sma5_recovery_next_open"

    if exit_price is None:
        exit_i = len(df) - 1
        exit_time = df.index[exit_i]
        exit_price = float(df.iloc[exit_i]["close"])
        exit_reason = "end_of_data"

    gross_r = (
        (exit_price - raw_entry) / risk
        if direction == "long"
        else (raw_entry - exit_price) / risk
    )

    cost_price = execution_cost_price(pair)
    cost_r = cost_price / risk
    net_r = gross_r - cost_r

    return Trade(
        strategy="C1",
        instrument=pair,
        direction=direction,
        signal_time=df.index[signal_i],
        entry_time=df.index[entry_i],
        exit_time=exit_time,
        entry_price=raw_entry,
        stop_price=stop,
        exit_price=exit_price,
        initial_risk_price=risk,
        gross_r=gross_r,
        cost_r=cost_r,
        net_r=net_r,
        exit_reason=exit_reason,
        holding_bars=int(exit_i - entry_i + 1),
        signal_rsi2=float(sig["rsi2"]),
        signal_atr14=atr14,
        signal_sma200_slope_pct=float(
            sig["sma200_slope_pct"]
        ),
    )


# =====================================================================
# EVENT GENERATION
# =====================================================================

def generate_strategy_trades(
    pair: str,
    df: pd.DataFrame,
    strategy: str,
    direction_mode: str,
) -> List[Trade]:

    out: List[Trade] = []

    # Prevent same strategy/pair from entering again while previous
    # generated trade is still active.
    blocked_until: Optional[pd.Timestamp] = None

    start_i = WARMUP_BARS

    for i in range(start_i, len(df) - 1):
        t = df.index[i]

        if blocked_until is not None and t <= blocked_until:
            continue

        row = df.iloc[i]

        candidates = []

        if strategy == "V1":
            if v1_long_signal(row):
                candidates.append("long")

            if (
                direction_mode == "both"
                and v1_short_signal(row)
            ):
                candidates.append("short")

        elif strategy == "C1":
            if c1_long_signal(row):
                candidates.append("long")

            if (
                direction_mode == "both"
                and c1_short_signal(row)
            ):
                candidates.append("short")

        else:
            raise ValueError(strategy)

        # Long and short should never both trigger under these definitions.
        for direction in candidates:
            if strategy == "V1":
                trade = replay_v1(
                    pair,
                    df,
                    i,
                    direction,
                )
            else:
                trade = replay_c1(
                    pair,
                    df,
                    i,
                    direction,
                )

            if trade is not None:
                out.append(trade)
                blocked_until = trade.exit_time
                break

    return out


def trades_to_df(trades: List[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()

    return pd.DataFrame(
        [t.__dict__ for t in trades]
    ).sort_values(
        ["entry_time", "instrument", "strategy"]
    ).reset_index(drop=True)


# =====================================================================
# METRICS
# =====================================================================

def profit_factor(r: pd.Series) -> float:
    wins = r[r > 0].sum()
    losses = -r[r < 0].sum()

    if losses <= 0:
        return np.inf if wins > 0 else np.nan

    return float(wins / losses)


def max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return np.nan

    peak = equity.cummax()
    dd = equity / peak - 1.0

    return float(-dd.min() * 100.0)


def simple_event_metrics(
    df: pd.DataFrame,
    r_col: str = "net_r",
) -> Dict[str, float]:

    if df.empty:
        return {
            "Trades": 0,
            "WinPct": np.nan,
            "MeanR": np.nan,
            "PF": np.nan,
            "MedianR": np.nan,
            "AvgBars": np.nan,
        }

    r = pd.to_numeric(
        df[r_col],
        errors="coerce",
    ).dropna()

    return {
        "Trades": int(len(r)),
        "WinPct": float((r > 0).mean() * 100.0),
        "MeanR": float(r.mean()),
        "PF": profit_factor(r),
        "MedianR": float(r.median()),
        "AvgBars": float(
            df["holding_bars"].mean()
        ) if "holding_bars" in df.columns else np.nan,
    }


# =====================================================================
# CHRONOLOGICAL DYNAMIC-RISK PORTFOLIO
# =====================================================================

def portfolio_sim(
    trades: pd.DataFrame,
    initial_nav: float = INITIAL_NAV,
    risk_pct: float = RISK_PCT,
    max_positions: int = MAX_POSITIONS,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """
    Chronological simplified R-based portfolio.

    Each accepted trade risks risk_pct of NAV measured at its entry.
    Concurrent slots are enforced by actual entry/exit timestamps.

    Realized NAV is changed on trade close. Open positions are represented
    for slot control; true within-trade MTM DD requires replaying every H4
    price path across all open positions simultaneously. Therefore this
    function reports REALIZED equity MaxDD and labels it accordingly.

    The trade-level R statistics remain exact for this research engine's
    execution assumptions.
    """

    if trades.empty:
        return pd.DataFrame(), {
            "StartNAV": initial_nav,
            "EndNAV": initial_nav,
            "ReturnPct": 0.0,
            "RealizedMaxDDPct": 0.0,
            "Accepted": 0,
            "SkippedSlots": 0,
        }

    x = trades.copy()
    x["entry_time"] = pd.to_datetime(
        x["entry_time"],
        utc=True,
    )
    x["exit_time"] = pd.to_datetime(
        x["exit_time"],
        utc=True,
    )

    x = x.sort_values(
        ["entry_time", "instrument"]
    ).reset_index(drop=True)

    nav = float(initial_nav)

    open_positions = []
    ledger = []
    skipped = 0

    # Event stream consisting of entries. Before each entry, realize all
    # trades whose exits are <= this entry timestamp.
    def realize_due(now):
        nonlocal nav, open_positions

        still_open = []

        for pos in sorted(
            open_positions,
            key=lambda p: p["exit_time"],
        ):
            if pos["exit_time"] <= now:
                pnl = pos["risk_dollars"] * pos["net_r"]
                nav += pnl

                ledger.append(
                    {
                        "time": pos["exit_time"],
                        "type": "exit",
                        "strategy": pos["strategy"],
                        "instrument": pos["instrument"],
                        "net_r": pos["net_r"],
                        "risk_dollars": pos["risk_dollars"],
                        "pnl": pnl,
                        "nav": nav,
                    }
                )
            else:
                still_open.append(pos)

        open_positions = still_open

    for _, row in x.iterrows():
        now = row["entry_time"]

        realize_due(now)

        if len(open_positions) >= max_positions:
            skipped += 1
            continue

        risk_dollars = nav * risk_pct

        pos = {
            "strategy": row.get("strategy", ""),
            "instrument": row["instrument"],
            "entry_time": row["entry_time"],
            "exit_time": row["exit_time"],
            "net_r": float(row["net_r"]),
            "risk_dollars": risk_dollars,
        }

        open_positions.append(pos)

        ledger.append(
            {
                "time": now,
                "type": "entry",
                "strategy": pos["strategy"],
                "instrument": pos["instrument"],
                "net_r": np.nan,
                "risk_dollars": risk_dollars,
                "pnl": 0.0,
                "nav": nav,
            }
        )

    # Realize remaining trades.
    if open_positions:
        for pos in sorted(
            open_positions,
            key=lambda p: p["exit_time"],
        ):
            pnl = pos["risk_dollars"] * pos["net_r"]
            nav += pnl

            ledger.append(
                {
                    "time": pos["exit_time"],
                    "type": "exit",
                    "strategy": pos["strategy"],
                    "instrument": pos["instrument"],
                    "net_r": pos["net_r"],
                    "risk_dollars": pos["risk_dollars"],
                    "pnl": pnl,
                    "nav": nav,
                }
            )

    led = pd.DataFrame(ledger).sort_values(
        ["time", "type"]
    ).reset_index(drop=True)

    exit_curve = led[led["type"] == "exit"]["nav"]

    metrics = {
        "StartNAV": initial_nav,
        "EndNAV": nav,
        "ReturnPct": (nav / initial_nav - 1.0) * 100.0,
        "RealizedMaxDDPct": max_drawdown_pct(exit_curve),
        "Accepted": int((led["type"] == "exit").sum()),
        "SkippedSlots": skipped,
    }

    return led, metrics


# =====================================================================
# F0 DISCOVERY
# =====================================================================

F0_REQUIRED = {
    "instrument",
    "entry_time",
    "entry_price",
    "stop_price",
    "target_price",
    "outcome",
}


def discover_f0_csv() -> Optional[Path]:
    """
    Find an existing F0 event file without guessing its signal rules.

    Strong preference:
      * required event columns
      * exactly 628 rows
      * all nine expected instruments if possible

    If exactly one strong candidate exists, use it.
    Otherwise print candidates and require --f0-events.
    """

    candidates = []

    skip_dirs = {
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
    }

    for path in ROOT.rglob("*.csv"):
        if any(part in skip_dirs for part in path.parts):
            continue

        try:
            header = pd.read_csv(
                path,
                nrows=0,
            )

            if not F0_REQUIRED.issubset(header.columns):
                continue

            df = pd.read_csv(path)

            score = 0

            if len(df) == 628:
                score += 10

            if "event_id" in df.columns:
                score += 2

            if "r_multiple" in df.columns:
                score += 2

            if "direction" in df.columns:
                score += 1

            syms = set(
                df["instrument"]
                .astype(str)
                .str.replace("/", "_", regex=False)
                .unique()
            )

            if set(PAIRS).issubset(syms):
                score += 5

            candidates.append(
                (score, len(df), path)
            )

        except Exception:
            continue

    candidates.sort(
        key=lambda z: (-z[0], abs(z[1] - 628), str(z[2]))
    )

    strong = [
        x for x in candidates
        if x[0] >= 10
    ]

    if len(strong) == 1:
        print(
            f"[F0] Auto-selected existing event file: "
            f"{strong[0][2]} ({strong[0][1]} rows)"
        )
        return strong[0][2]

    if candidates:
        print()
        print("[F0] Existing event CSV candidates:")
        for score, rows, path in candidates[:20]:
            print(
                f"      score={score:2d} rows={rows:5d} {path}"
            )
        print()

    if len(strong) > 1:
        print(
            "[F0] Multiple strong F0 candidates exist. "
            "Use --f0-events /exact/path.csv"
        )
    else:
        print(
            "[F0] No uniquely verified 628-event F0 CSV found."
        )

    return None


def load_f0(path: Path) -> pd.DataFrame:
    x = pd.read_csv(path)

    missing = F0_REQUIRED - set(x.columns)

    if missing:
        raise ValueError(
            f"F0 CSV missing columns: {sorted(missing)}"
        )

    x["instrument"] = (
        x["instrument"]
        .astype(str)
        .str.replace("/", "_", regex=False)
    )

    x["entry_time"] = pd.to_datetime(
        x["entry_time"],
        utc=True,
        errors="coerce",
    )

    if "exit_time" in x.columns:
        x["exit_time"] = pd.to_datetime(
            x["exit_time"],
            utc=True,
            errors="coerce",
        )

    # Preserve existing certified R if available.
    if "r_multiple" in x.columns:
        x["net_r"] = pd.to_numeric(
            x["r_multiple"],
            errors="coerce",
        )

    elif "pnl" in x.columns:
        # Cannot reconstruct R safely unless known risk dollars are supplied.
        raise ValueError(
            "F0 contains pnl but no r_multiple. "
            "Do not infer R from PnL without the original event risk."
        )

    else:
        # Last conservative option: outcomes that are actual numeric R.
        maybe = pd.to_numeric(
            x["outcome"],
            errors="coerce",
        )

        if maybe.notna().mean() > 0.95:
            x["net_r"] = maybe
        else:
            raise ValueError(
                "F0 CSV has no r_multiple and outcome is not numeric R."
            )

    x["strategy"] = "F0"

    return x


# =====================================================================
# REPORTS
# =====================================================================

def strategy_summary(name: str, trades: pd.DataFrame) -> Dict:
    event = simple_event_metrics(trades)

    result = {
        "Strategy": name,
        **event,
    }

    if (
        not trades.empty
        and {"entry_time", "exit_time", "net_r"}.issubset(trades.columns)
    ):
        ledger, port = portfolio_sim(trades)

        result.update(
            {
                "ReturnPct": port["ReturnPct"],
                "RealizedMaxDDPct": port["RealizedMaxDDPct"],
                "Accepted": port["Accepted"],
                "SkippedSlots": port["SkippedSlots"],
            }
        )
    else:
        result.update(
            {
                "ReturnPct": np.nan,
                "RealizedMaxDDPct": np.nan,
                "Accepted": np.nan,
                "SkippedSlots": np.nan,
            }
        )

    return result


def breakdown(
    trades: pd.DataFrame,
    key: str,
) -> pd.DataFrame:

    rows = []

    if trades.empty:
        return pd.DataFrame()

    for value, grp in trades.groupby(key):
        m = simple_event_metrics(grp)
        rows.append(
            {
                key: value,
                **m,
            }
        )

    return pd.DataFrame(rows)


def year_breakdown(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()

    x = trades.copy()

    x["entry_time"] = pd.to_datetime(
        x["entry_time"],
        utc=True,
    )

    x["year"] = x["entry_time"].dt.year

    return breakdown(x, "year")


def print_table(df: pd.DataFrame, title: str) -> None:
    print()
    print("=" * 120)
    print(title)
    print("=" * 120)

    if df.empty:
        print("NO DATA")
        return

    with pd.option_context(
        "display.max_columns", 50,
        "display.width", 220,
        "display.float_format", lambda x: f"{x:,.4f}",
    ):
        print(df.to_string(index=False))


# =====================================================================
# MAIN
# =====================================================================

def main() -> int:
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--direction-mode",
        choices=["long", "both"],
        default="long",
        help=(
            "long = source-faithful long-only. "
            "both = additionally test mirrored FX shorts."
        ),
    )

    ap.add_argument(
        "--f0-events",
        type=Path,
        default=None,
        help="Exact existing F0 events CSV.",
    )

    args = ap.parse_args()

    OUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("=" * 120)
    print("VALEZE2 -> OANDA H4 PARITY / FEASIBILITY TEST")
    print("=" * 120)
    print(f"Data directory       : {DATA_DIR}")
    print(f"Pairs                : {', '.join(PAIRS)}")
    print(f"Direction mode       : {args.direction_mode}")
    print(f"Initial NAV          : ${INITIAL_NAV:,.2f}")
    print(f"Risk per trade       : {RISK_PCT * 100:.2f}%")
    print(f"Portfolio slot cap   : {MAX_POSITIONS}")
    print()
    print("V1: 200-SMA Rubber-Band Snapback")
    print(
        f"    RSI2 <= {V1_RSI2_MAX:.1f} | "
        f"distance <= {V1_SMA200_PROX_ATR:.2f} ATR | "
        f"SMA200 20-bar slope >= "
        f"{V1_MIN_SMA200_SLOPE_PCT:.2f}%"
    )
    print(
        f"    Stop = signal low - "
        f"{V1_STOP_OFFSET_ATR:.2f} ATR | "
        f"exit SMA20 or +{V1_FIXED_TARGET_R:.2f}R"
    )
    print()
    print("C1: Connors RSI(2) Strict Panic")
    print(
        f"    Close > SMA200 | RSI2 < {C1_RSI2_ENTRY:.1f} | "
        f"stop {C1_STOP_ATR:.2f} ATR"
    )
    print(
        f"    recovery exit RSI2 >= {C1_RSI2_EXIT:.1f} "
        f"or Close >= SMA5"
    )
    print("=" * 120)
    print()

    all_v1: List[Trade] = []
    all_c1: List[Trade] = []

    loaded = {}

    for pair in PAIRS:
        df = load_pair(pair)
        loaded[pair] = df

        v1 = generate_strategy_trades(
            pair,
            df,
            "V1",
            args.direction_mode,
        )

        c1 = generate_strategy_trades(
            pair,
            df,
            "C1",
            args.direction_mode,
        )

        all_v1.extend(v1)
        all_c1.extend(c1)

        print(
            f"[SIGNALS] {pair:8s} "
            f"V1={len(v1):4d} "
            f"C1={len(c1):4d}"
        )

    v1_df = trades_to_df(all_v1)
    c1_df = trades_to_df(all_c1)

    v1_df.to_csv(
        OUT_DIR / "v1_trades.csv",
        index=False,
    )

    c1_df.to_csv(
        OUT_DIR / "c1_trades.csv",
        index=False,
    )

    # -------------------------------------------------------------
    # F0: load existing certified event source only.
    # -------------------------------------------------------------
    f0_df = pd.DataFrame()

    f0_path = args.f0_events

    if f0_path is None:
        f0_path = discover_f0_csv()

    if f0_path is not None:
        try:
            f0_df = load_f0(f0_path)

            print(
                f"[F0] Loaded {len(f0_df):,} events from {f0_path}"
            )

        except Exception as exc:
            print(
                f"[F0] Could not certify/load F0: {exc}"
            )
            f0_df = pd.DataFrame()

    # -------------------------------------------------------------
    # Overall scorecard
    # -------------------------------------------------------------
    rows = []

    if not f0_df.empty:
        rows.append(
            strategy_summary("F0", f0_df)
        )

    rows.append(
        strategy_summary("V1", v1_df)
    )

    rows.append(
        strategy_summary("C1", c1_df)
    )

    summary = pd.DataFrame(rows)

    preferred_cols = [
        "Strategy",
        "Trades",
        "WinPct",
        "MeanR",
        "PF",
        "MedianR",
        "AvgBars",
        "ReturnPct",
        "RealizedMaxDDPct",
        "Accepted",
        "SkippedSlots",
    ]

    summary = summary[
        [c for c in preferred_cols if c in summary.columns]
    ]

    print_table(
        summary,
        "OVERALL F0 vs V1 vs C1 SCORECARD",
    )

    summary.to_csv(
        OUT_DIR / "overall_scorecard.csv",
        index=False,
    )

    # -------------------------------------------------------------
    # Pair breakdown
    # -------------------------------------------------------------
    pair_frames = []

    for name, tdf in [
        ("F0", f0_df),
        ("V1", v1_df),
        ("C1", c1_df),
    ]:
        if tdf.empty:
            continue

        b = breakdown(
            tdf,
            "instrument",
        )

        b.insert(0, "Strategy", name)
        pair_frames.append(b)

    pair_report = (
        pd.concat(pair_frames, ignore_index=True)
        if pair_frames
        else pd.DataFrame()
    )

    print_table(
        pair_report,
        "PAIR BREAKDOWN",
    )

    pair_report.to_csv(
        OUT_DIR / "pair_breakdown.csv",
        index=False,
    )

    # -------------------------------------------------------------
    # Year breakdown
    # -------------------------------------------------------------
    year_frames = []

    for name, tdf in [
        ("F0", f0_df),
        ("V1", v1_df),
        ("C1", c1_df),
    ]:
        if tdf.empty:
            continue

        b = year_breakdown(tdf)

        if b.empty:
            continue

        b.insert(0, "Strategy", name)
        year_frames.append(b)

    year_report = (
        pd.concat(year_frames, ignore_index=True)
        if year_frames
        else pd.DataFrame()
    )

    print_table(
        year_report,
        "YEAR BREAKDOWN",
    )

    year_report.to_csv(
        OUT_DIR / "year_breakdown.csv",
        index=False,
    )

    # -------------------------------------------------------------
    # Combined V1+C1 exploratory portfolio
    # -------------------------------------------------------------
    combined = pd.concat(
        [v1_df, c1_df],
        ignore_index=True,
    ).sort_values(
        ["entry_time", "strategy", "instrument"]
    )

    if not combined.empty:
        led, combined_metrics = portfolio_sim(
            combined
        )

        led.to_csv(
            OUT_DIR / "v1_c1_combined_ledger.csv",
            index=False,
        )

        combined_table = pd.DataFrame(
            [
                {
                    "Portfolio": "V1+C1",
                    **combined_metrics,
                }
            ]
        )

        print_table(
            combined_table,
            "EXPLORATORY V1+C1 PORTFOLIO",
        )

        combined_table.to_csv(
            OUT_DIR / "v1_c1_combined_summary.csv",
            index=False,
        )

    # -------------------------------------------------------------
    # Research gates
    # -------------------------------------------------------------
    print()
    print("=" * 120)
    print("FIRST-PASS RESEARCH GATES")
    print("=" * 120)

    for name, tdf in [
        ("V1", v1_df),
        ("C1", c1_df),
    ]:
        m = simple_event_metrics(tdf)

        trade_gate = m["Trades"] >= 75
        exp_gate = (
            np.isfinite(m["MeanR"])
            and m["MeanR"] > 0.10
        )
        pf_gate = (
            np.isfinite(m["PF"])
            and m["PF"] >= 1.20
        )

        if not tdf.empty:
            pb = breakdown(
                tdf,
                "instrument",
            )
            positive_pairs = int(
                (pb["MeanR"] > 0).sum()
            )
        else:
            positive_pairs = 0

        breadth_gate = positive_pairs >= 6

        passed = all(
            [
                trade_gate,
                exp_gate,
                pf_gate,
                breadth_gate,
            ]
        )

        print(
            f"{name}: "
            f"{'PASS' if passed else 'FAIL'} | "
            f"Trades {m['Trades']} "
            f"{'PASS' if trade_gate else 'FAIL'} | "
            f"MeanR {m['MeanR']:+.3f} "
            f"{'PASS' if exp_gate else 'FAIL'} | "
            f"PF {m['PF']:.3f} "
            f"{'PASS' if pf_gate else 'FAIL'} | "
            f"Positive pairs {positive_pairs}/9 "
            f"{'PASS' if breadth_gate else 'FAIL'}"
        )

    print()
    print("NOTE:")
    print(
        "  These are feasibility gates, NOT final certification. "
        "Any survivor still requires walk-forward/OOS,"
    )
    print(
        "  historical bid/ask friction, MTM portfolio drawdown, "
        "parameter perturbation, and Monte Carlo/stress testing."
    )

    if f0_df.empty:
        print()
        print("F0 STATUS: NOT COMPARED")
        print(
            "The script deliberately did not reconstruct F0 from incomplete "
            "strategy information."
        )
        print(
            "If auto-discovery printed the correct F0 file above, rerun with:"
        )
        print(
            "  python3 run_valeze2_oanda_h4_parity.py "
            "--f0-events /exact/path/to/f0_events.csv"
        )

    print()
    print(
        f"Results written to: {OUT_DIR}"
    )
    print("=" * 120)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
