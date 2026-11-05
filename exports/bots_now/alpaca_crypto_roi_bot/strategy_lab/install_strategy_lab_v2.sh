#!/usr/bin/env bash
set -euo pipefail

LAB="$HOME/bots/alpaca_crypto_roi_bot/strategy_lab"
cd "$LAB"

mkdir -p v2 reports/v2 logs/v2

cp -a reports/strategy_rankings_latest.csv \
  "reports/v2/strategy_rankings_v1_backup_$(date -u +%Y%m%dT%H%M%SZ).csv" \
  2>/dev/null || true

cat > v2/strategy_lab_v2.py <<'PY'
#!/usr/bin/env python3

import argparse
import json
import math
import random
from dataclasses import dataclass, asdict
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "crypto_history.duckdb"
REPORTS = ROOT / "reports" / "v2"
REPORTS.mkdir(parents=True, exist_ok=True)

START_EQUITY = 10_000.0

# Conservative round-trip cost assumptions.
# Override from command line if desired.
DEFAULT_FEE_BPS = 15.0
DEFAULT_SLIPPAGE_BPS = 5.0

# ------------------------------------------------------------
# Indicators
# ------------------------------------------------------------

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def sma(s, n):
    return s.rolling(n).mean()

def atr(df, n=14):
    pc = df.close.shift(1)
    tr = pd.concat([
        df.high - df.low,
        (df.high - pc).abs(),
        (df.low - pc).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100/(1+rs)

def adx(df, n=14):
    up = df.high.diff()
    dn = -df.low.diff()

    plus_dm = pd.Series(
        np.where((up > dn) & (up > 0), up, 0.0),
        index=df.index
    )
    minus_dm = pd.Series(
        np.where((dn > up) & (dn > 0), dn, 0.0),
        index=df.index
    )

    a = atr(df, n)
    plus_di = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / a

    dx = 100 * (plus_di-minus_di).abs() / \
        (plus_di+minus_di).replace(0, np.nan)

    return dx.ewm(alpha=1/n, adjust=False).mean()

# ------------------------------------------------------------
# Data
# ------------------------------------------------------------

def load_symbol(symbol):
    con = duckdb.connect(str(DB), read_only=True)

    df = con.execute("""
        SELECT *
        FROM bars
        WHERE symbol = ?
        ORDER BY timestamp
    """, [symbol]).df()

    con.close()

    rename = {}
    for c in df.columns:
        lc = c.lower()
        if lc in {"timestamp","time","datetime"}:
            rename[c] = "timestamp"
        elif lc in {"open","high","low","close","volume"}:
            rename[c] = lc

    df = df.rename(columns=rename)

    required = {"timestamp","open","high","low","close"}
    missing = required - set(df.columns)

    if missing:
        raise RuntimeError(
            f"{symbol}: missing DB columns {sorted(missing)}; "
            f"found {list(df.columns)}"
        )

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.drop_duplicates("timestamp")
    df = df.set_index("timestamp").sort_index()

    return df

def resample(df, timeframe):
    if timeframe == "15min":
        return df.copy()

    rule = {
        "1h": "1h",
        "4h": "4h"
    }[timeframe]

    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last"
    }

    if "volume" in df.columns:
        agg["volume"] = "sum"

    return df.resample(rule).agg(agg).dropna()

# ------------------------------------------------------------
# Strategies
# ------------------------------------------------------------

def breakout_signal(df, lookback=20, trend=50, adx_min=18):
    x = df.copy()

    x["atr"] = atr(x)
    x["ema"] = ema(x.close, trend)
    x["adx"] = adx(x)

    # shift is critical: today's high cannot define today's breakout.
    x["hh"] = x.high.shift(1).rolling(lookback).max()

    x["entry"] = (
        (x.close > x.hh) &
        (x.close > x["ema"]) &
        (x["adx"] >= adx_min)
    )

    return x

def pullback_signal(df, trend=50, rsi_entry=42):
    x = df.copy()

    x["atr"] = atr(x)
    x["ema_fast"] = ema(x.close, 20)
    x["ema_slow"] = ema(x.close, trend)
    x["rsi"] = rsi(x.close)

    x["entry"] = (
        (x["ema_fast"] > x["ema_slow"]) &
        (x.close > x["ema_slow"]) &
        (x.close <= x["ema_fast"] * 1.01) &
        (x["rsi"] <= rsi_entry) &
        (x["rsi"].shift(1) < x["rsi"])
    )

    return x

def velez_signal(df):
    """
    Crypto adaptation of a Velez-style momentum/pullback scalp:
    established trend -> orderly pullback -> reversal bar -> continuation.

    This is an implementation inspired by that trading philosophy,
    not a claim to reproduce a proprietary system.
    """

    x = df.copy()

    x["ema20"] = ema(x.close, 20)
    x["ema50"] = ema(x.close, 50)
    x["atr"] = atr(x, 14)
    x["rsi"] = rsi(x.close, 14)

    body = (x.close - x.open).abs()
    rng = (x.high - x.low).replace(0, np.nan)

    bullish_bar = (
        (x.close > x.open) &
        ((x.close - x.low) / rng > .65)
    )

    trend = (
        (x["ema20"] > x["ema50"]) &
        (x["ema20"] > x["ema20"].shift(3))
    )

    pullback = (
        (x.low <= x["ema20"] * 1.005) &
        (x.close > x["ema50"]) &
        (x["rsi"].between(38, 58))
    )

    reversal = (
        bullish_bar &
        (x.close > x.high.shift(1)) &
        (body > body.rolling(20).median() * .70)
    )

    x["entry"] = trend & pullback & reversal

    return x

# ------------------------------------------------------------
# Backtester
# ------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    strategy: str
    timeframe: str
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    pnl: float
    return_pct: float
    bars: int
    reason: str

def run_backtest(
    raw,
    symbol,
    timeframe,
    strategy,
    params,
    fee_bps,
    slippage_bps,
    start=None,
    end=None
):
    df = resample(raw, timeframe)

    if start is not None:
        df = df[df.index >= start]

    if end is not None:
        df = df[df.index < end]

    if len(df) < 100:
        return metrics([], START_EQUITY)

    if strategy == "breakout":
        x = breakout_signal(
            df,
            int(params["lookback"]),
            int(params["trend"]),
            params["adx_min"]
        )

    elif strategy == "pullback":
        x = pullback_signal(
            df,
            int(params["trend"]),
            params["rsi_entry"]
        )

    elif strategy == "velez":
        x = velez_signal(df)

    else:
        raise ValueError(strategy)

    stop_atr = params.get("stop_atr", 1.6)
    target_r = params.get("target_r", 2.5)
    max_hold = int(params.get("max_hold", 12))

    roundtrip_cost = (fee_bps + slippage_bps) * 2 / 10000

    trades = []
    equity = START_EQUITY

    i = 60

    while i < len(x)-2:

        if not bool(x.entry.iloc[i]):
            i += 1
            continue

        # Execute next bar to avoid same-bar lookahead.
        j = i + 1

        entry = float(x.open.iloc[j])

        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue

        a = float(x.atr.iloc[i])

        if not np.isfinite(a) or a <= 0:
            i += 1
            continue

        stop = entry - stop_atr*a
        risk = entry-stop
        target = entry + target_r*risk

        # Risk 0.5% of current equity.
        risk_dollars = equity * .005
        qty = risk_dollars / risk

        exit_px = None
        reason = "time"
        exit_idx = min(j+max_hold, len(x)-1)

        for k in range(j, exit_idx+1):

            lo = float(x.low.iloc[k])
            hi = float(x.high.iloc[k])

            # Conservative same-bar assumption:
            # if stop and target both touched, stop occurs first.
            if lo <= stop:
                exit_px = stop
                exit_idx = k
                reason = "stop"
                break

            if hi >= target:
                exit_px = target
                exit_idx = k
                reason = "target"
                break

        if exit_px is None:
            exit_px = float(x.close.iloc[exit_idx])

        gross = qty * (exit_px-entry)
        cost = qty * entry * roundtrip_cost
        pnl = gross-cost

        equity += pnl

        trades.append(
            Trade(
                symbol=symbol,
                strategy=strategy,
                timeframe=timeframe,
                entry_time=str(x.index[j]),
                exit_time=str(x.index[exit_idx]),
                entry=entry,
                exit=exit_px,
                pnl=pnl,
                return_pct=pnl / max(equity-pnl,1) * 100,
                bars=exit_idx-j+1,
                reason=reason
            )
        )

        i = exit_idx + 1

    return metrics(trades, equity)

def metrics(trades, ending_equity):
    if not trades:
        return {
            "trades": 0,
            "roi_pct": 0.0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown_pct": 0.0,
            "trade_list": []
        }

    pnl = np.array([t.pnl for t in trades])

    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    pf = (
        wins.sum()/abs(losses.sum())
        if len(losses)
        else 999.0
    )

    eq = START_EQUITY + np.cumsum(pnl)
    peak = np.maximum.accumulate(
        np.r_[START_EQUITY, eq]
    )

    curve = np.r_[START_EQUITY, eq]

    dd = (peak-curve)/peak*100

    return {
        "trades": len(trades),
        "roi_pct": (ending_equity/START_EQUITY-1)*100,
        "win_rate_pct": (pnl > 0).mean()*100,
        "profit_factor": float(pf),
        "expectancy": float(pnl.mean()),
        "max_drawdown_pct": float(dd.max()),
        "trade_list": [asdict(t) for t in trades]
    }

# ------------------------------------------------------------
# Parameter grids
# ------------------------------------------------------------

def grids(strategy):

    if strategy == "breakout":
        for lookback in [15,20,25,30]:
            for stop in [1.3,1.6,1.9]:
                for target in [2.0,2.5,3.0]:
                    yield {
                        "lookback": lookback,
                        "trend": 50,
                        "adx_min": 18,
                        "stop_atr": stop,
                        "target_r": target,
                        "max_hold": 12
                    }

    elif strategy == "pullback":
        for rsi_e in [38,42,46]:
            for stop in [1.3,1.6,1.9]:
                for target in [1.8,2.2,2.6]:
                    yield {
                        "trend": 50,
                        "rsi_entry": rsi_e,
                        "stop_atr": stop,
                        "target_r": target,
                        "max_hold": 12
                    }

    elif strategy == "velez":
        for stop in [1.0,1.3,1.6]:
            for target in [1.5,2.0,2.5]:
                for hold in [4,6,8]:
                    yield {
                        "stop_atr": stop,
                        "target_r": target,
                        "max_hold": hold
                    }

# ------------------------------------------------------------
# Walk-forward
# ------------------------------------------------------------

def score(m):
    if m["trades"] < 5:
        return -999

    return (
        m["roi_pct"]
        + min(m["profit_factor"],3)*2
        - m["max_drawdown_pct"]*.75
    )

def walkforward(
    raw,
    symbol,
    timeframe,
    strategy,
    fee_bps,
    slippage_bps
):
    d0 = raw.index.min()
    d1 = raw.index.max()

    # Expanding training period + untouched sequential test periods.
    initial_train = pd.Timedelta(days=365)
    test_len = pd.Timedelta(days=120)

    train_end = d0 + initial_train

    windows = []

    while train_end + test_len <= d1:

        test_end = train_end + test_len

        best_params = None
        best_score = -1e18
        best_train = None

        for p in grids(strategy):

            m = run_backtest(
                raw,
                symbol,
                timeframe,
                strategy,
                p,
                fee_bps,
                slippage_bps,
                start=d0,
                end=train_end
            )

            s = score(m)

            if s > best_score:
                best_score = s
                best_params = p
                best_train = m

        test = run_backtest(
            raw,
            symbol,
            timeframe,
            strategy,
            best_params,
            fee_bps,
            slippage_bps,
            start=train_end,
            end=test_end
        )

        windows.append({
            "train_start": str(d0),
            "train_end": str(train_end),
            "test_start": str(train_end),
            "test_end": str(test_end),
            "parameters": best_params,
            "train": {
                k:v for k,v in best_train.items()
                if k != "trade_list"
            },
            "test": {
                k:v for k,v in test.items()
                if k != "trade_list"
            },
            "trades": test["trade_list"]
        })

        train_end = test_end

    return windows

# ------------------------------------------------------------
# Monte Carlo
# ------------------------------------------------------------

def monte_carlo(trades, iterations=10000):
    pnl = np.array([t["pnl"] for t in trades], dtype=float)

    if len(pnl) < 10:
        return {
            "iterations": iterations,
            "trades": len(pnl),
            "warning": "Insufficient OOS trades for reliable Monte Carlo"
        }

    final_returns = []
    max_dd = []

    rng = np.random.default_rng(42)

    for _ in range(iterations):
        sample = rng.choice(pnl, size=len(pnl), replace=True)

        curve = START_EQUITY + np.cumsum(sample)
        curve = np.r_[START_EQUITY, curve]

        peak = np.maximum.accumulate(curve)
        dd = (peak-curve)/peak*100

        final_returns.append(
            (curve[-1]/START_EQUITY-1)*100
        )

        max_dd.append(dd.max())

    return {
        "iterations": iterations,
        "trades": len(pnl),
        "probability_profitable_pct":
            float(np.mean(np.array(final_returns)>0)*100),
        "median_roi_pct":
            float(np.median(final_returns)),
        "roi_5th_pct":
            float(np.percentile(final_returns,5)),
        "roi_95th_pct":
            float(np.percentile(final_returns,95)),
        "median_max_dd_pct":
            float(np.median(max_dd)),
        "dd_95th_pct":
            float(np.percentile(max_dd,95))
    }

# ------------------------------------------------------------
# Robustness
# ------------------------------------------------------------

def perturb(params, pct):
    out = {}

    for k,v in params.items():
        if isinstance(v,(int,float)):
            factor = 1 + random.uniform(-pct,pct)
            nv = v*factor

            if isinstance(v,int):
                nv = max(1,int(round(nv)))

            out[k] = nv
        else:
            out[k] = v

    return out

def robustness(
    raw,
    symbol,
    timeframe,
    strategy,
    base_params,
    fee_bps,
    slippage_bps,
    trials=100
):
    rows = []

    random.seed(42)

    for _ in range(trials):
        p = perturb(base_params,.20)

        m = run_backtest(
            raw,
            symbol,
            timeframe,
            strategy,
            p,
            fee_bps,
            slippage_bps
        )

        rows.append({
            "params":p,
            "roi":m["roi_pct"],
            "pf":m["profit_factor"],
            "dd":m["max_drawdown_pct"],
            "trades":m["trades"]
        })

    profitable = [r for r in rows if r["roi"] > 0]

    return {
        "trials": trials,
        "profitable_pct":
            len(profitable)/max(len(rows),1)*100,
        "median_roi_pct":
            float(np.median([r["roi"] for r in rows])),
        "median_pf":
            float(np.median([r["pf"] for r in rows])),
        "worst_dd_pct":
            float(max(r["dd"] for r in rows)),
        "results": rows
    }

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--fee-bps",
        type=float,
        default=DEFAULT_FEE_BPS
    )

    ap.add_argument(
        "--slippage-bps",
        type=float,
        default=DEFAULT_SLIPPAGE_BPS
    )

    ap.add_argument(
        "--mc",
        type=int,
        default=10000
    )

    args = ap.parse_args()

    candidates = [
        ("ETH/USD","4h","breakout"),
        ("DOGE/USD","4h","breakout"),
        ("DOGE/USD","4h","pullback"),

        # Velez-style competitors
        ("BTC/USD","15min","velez"),
        ("ETH/USD","15min","velez"),
        ("DOGE/USD","15min","velez"),
        ("LINK/USD","15min","velez"),
        ("AVAX/USD","15min","velez"),

        ("BTC/USD","1h","velez"),
        ("ETH/USD","1h","velez"),
        ("DOGE/USD","1h","velez")
    ]

    final = []

    cache = {}

    for symbol,timeframe,strategy in candidates:

        print(
            f"\n===== {symbol} {timeframe} {strategy} =====",
            flush=True
        )

        if symbol not in cache:
            cache[symbol] = load_symbol(symbol)

        raw = cache[symbol]

        wf = walkforward(
            raw,
            symbol,
            timeframe,
            strategy,
            args.fee_bps,
            args.slippage_bps
        )

        oos_trades = []

        for w in wf:
            oos_trades.extend(w["trades"])

        profitable_windows = sum(
            w["test"]["roi_pct"] > 0
            for w in wf
        )

        test_rois = [
            w["test"]["roi_pct"]
            for w in wf
        ]

        test_dd = [
            w["test"]["max_drawdown_pct"]
            for w in wf
        ]

        test_pf = [
            w["test"]["profit_factor"]
            for w in wf
            if w["test"]["trades"] > 0
        ]

        # most frequently selected parameter set
        if wf:
            parameter_strings = [
                json.dumps(w["parameters"],sort_keys=True)
                for w in wf
            ]

            mode = max(
                set(parameter_strings),
                key=parameter_strings.count
            )

            base_params = json.loads(mode)
        else:
            base_params = next(grids(strategy))

        rob = robustness(
            raw,
            symbol,
            timeframe,
            strategy,
            base_params,
            args.fee_bps,
            args.slippage_bps
        )

        mc = monte_carlo(
            oos_trades,
            args.mc
        )

        result = {
            "symbol":symbol,
            "timeframe":timeframe,
            "strategy":strategy,
            "windows":len(wf),
            "profitable_windows":profitable_windows,
            "profitable_window_pct":
                profitable_windows/max(len(wf),1)*100,
            "oos_trades":len(oos_trades),
            "avg_oos_roi_pct":
                float(np.mean(test_rois))
                if test_rois else 0,
            "worst_oos_dd_pct":
                float(max(test_dd))
                if test_dd else 0,
            "avg_oos_pf":
                float(np.mean(test_pf))
                if test_pf else 0,
            "monte_carlo":mc,
            "robustness": {
                k:v for k,v in rob.items()
                if k != "results"
            },
            "base_parameters":base_params,
            "walkforward":wf
        }

        final.append(result)

        print(
            f"OOS trades={result['oos_trades']} "
            f"windows={result['windows']} "
            f"profitable={result['profitable_window_pct']:.1f}% "
            f"ROI/window={result['avg_oos_roi_pct']:.2f}% "
            f"DD={result['worst_oos_dd_pct']:.2f}% "
            f"PF={result['avg_oos_pf']:.2f}",
            flush=True
        )

        print(
            "Robustness profitable="
            f"{result['robustness']['profitable_pct']:.1f}% "
            "MC profitable="
            f"{mc.get('probability_profitable_pct','n/a')}",
            flush=True
        )

    # Rank conservatively.
    def rank_score(r):

        mcprob = r["monte_carlo"].get(
            "probability_profitable_pct",0
        )

        robust = r["robustness"]["profitable_pct"]

        return (
            r["avg_oos_roi_pct"] * 2
            + r["profitable_window_pct"] / 10
            + mcprob / 10
            + robust / 10
            + min(r["avg_oos_pf"],3)
            - r["worst_oos_dd_pct"]
        )

    for r in final:
        r["validation_score"] = rank_score(r)

        r["passed"] = (
            r["oos_trades"] >= 25
            and r["profitable_window_pct"] >= 60
            and r["avg_oos_roi_pct"] > 0
            and r["avg_oos_pf"] >= 1.25
            and r["worst_oos_dd_pct"] <= 10
            and r["robustness"]["profitable_pct"] >= 55
            and r["monte_carlo"].get(
                "probability_profitable_pct",0
            ) >= 70
        )

    final.sort(
        key=lambda r:r["validation_score"],
        reverse=True
    )

    json_path = REPORTS / "strategy_lab_v2_latest.json"

    json_path.write_text(
        json.dumps(final,indent=2),
        encoding="utf-8"
    )

    rows = []

    for r in final:
        rows.append({
            "symbol":r["symbol"],
            "timeframe":r["timeframe"],
            "strategy":r["strategy"],
            "oos_trades":r["oos_trades"],
            "profitable_window_pct":
                r["profitable_window_pct"],
            "avg_oos_roi_pct":
                r["avg_oos_roi_pct"],
            "worst_oos_dd_pct":
                r["worst_oos_dd_pct"],
            "avg_oos_pf":
                r["avg_oos_pf"],
            "robust_profitable_pct":
                r["robustness"]["profitable_pct"],
            "mc_profitable_pct":
                r["monte_carlo"].get(
                    "probability_profitable_pct",0
                ),
            "mc_95_dd_pct":
                r["monte_carlo"].get(
                    "dd_95th_pct",None
                ),
            "passed":r["passed"],
            "score":r["validation_score"]
        })

    ranking = pd.DataFrame(rows)

    ranking.to_csv(
        REPORTS / "strategy_lab_v2_rankings.csv",
        index=False
    )

    print("\n\n========== STRATEGY LAB V2 ==========")
    print(
        ranking.to_string(index=False)
    )

    print(
        "\nJSON:",
        json_path
    )

    print(
        "CSV:",
        REPORTS / "strategy_lab_v2_rankings.csv"
    )

if __name__ == "__main__":
    main()
PY

chmod +x v2/strategy_lab_v2.py

echo
echo "=================================================="
echo " STRATEGY LAB V2 INSTALLED"
echo "=================================================="
echo
echo "Database:"
echo "  $LAB/data/crypto_history.duckdb"
echo
echo "Existing trading bot modified: NO"
echo
echo "Candidates:"
echo "  ETH/USD  4h breakout"
echo "  DOGE/USD 4h breakout"
echo "  DOGE/USD 4h pullback"
echo "  Velez-style BTC/ETH/DOGE/LINK/AVAX"
echo
echo "Validation:"
echo "  expanding walk-forward"
echo "  next-bar execution"
echo "  fees"
echo "  slippage"
echo "  +/-20% parameter perturbation"
echo "  10,000-run Monte Carlo"
echo
echo "PASS requires:"
echo "  >=25 OOS trades"
echo "  >=60% profitable windows"
echo "  positive OOS ROI"
echo "  PF >=1.25"
echo "  DD <=10%"
echo "  >=55% perturbations profitable"
echo "  >=70% MC probability profitable"
echo
