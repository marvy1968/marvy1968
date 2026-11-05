#!/usr/bin/env python3

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "crypto_history.duckdb"
REPORTS = ROOT / "reports" / "v6"

REPORTS.mkdir(parents=True, exist_ok=True)

START_EQUITY = 10000.0
RISK_PCT = 0.005

SYMBOLS = [
    "BTC/USD",
    "ETH/USD",
    "DOGE/USD",
    "LINK/USD",
    "AVAX/USD",
]

TIMEFRAMES = ["1h", "4h"]


def ema(series, n):
    return series.ewm(
        span=n,
        adjust=False,
    ).mean()


def atr(df, n=14):
    previous = df.close.shift(1)

    tr = pd.concat(
        [
            df.high - df.low,
            (df.high - previous).abs(),
            (df.low - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.rolling(n).mean()


def adx(df, n=14):
    up = df.high.diff()
    down = -df.low.diff()

    plus = pd.Series(
        np.where(
            (up > down) & (up > 0),
            up,
            0.0,
        ),
        index=df.index,
    )

    minus = pd.Series(
        np.where(
            (down > up) & (down > 0),
            down,
            0.0,
        ),
        index=df.index,
    )

    a = atr(df, n)

    plus_di = (
        100
        * plus.ewm(
            alpha=1 / n,
            adjust=False,
        ).mean()
        / a
    )

    minus_di = (
        100
        * minus.ewm(
            alpha=1 / n,
            adjust=False,
        ).mean()
        / a
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(
            0,
            np.nan,
        )
    )

    return dx.ewm(
        alpha=1 / n,
        adjust=False,
    ).mean()


def load(symbol):
    con = duckdb.connect(
        str(DB),
        read_only=True,
    )

    df = con.execute(
        """
        SELECT
            timestamp,
            open,
            high,
            low,
            close,
            volume
        FROM bars
        WHERE symbol = ?
        ORDER BY timestamp
        """,
        [symbol],
    ).df()

    con.close()

    if df.empty:
        raise RuntimeError(
            f"No data for {symbol}"
        )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        utc=True,
    )

    return (
        df
        .drop_duplicates("timestamp")
        .set_index("timestamp")
        .sort_index()
    )


def resample(df, timeframe):
    return (
        df.resample(timeframe)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna()
    )


def build_features(df, btc):
    x = df.copy()

    b = btc.reindex(
        x.index
    ).ffill()

    x["atr"] = atr(x)
    x["atr_pct"] = (
        x["atr"] / x.close
    )

    x["atr_rank"] = (
        x["atr_pct"]
        .rolling(100)
        .rank(pct=True)
    )

    x["adx"] = adx(x)

    x["ema20"] = ema(
        x.close,
        20,
    )

    x["ema50"] = ema(
        x.close,
        50,
    )

    x["ema200"] = ema(
        x.close,
        200,
    )

    x["vol20"] = (
        x.volume
        .rolling(20)
        .mean()
    )

    x["vol_ratio"] = (
        x.volume
        / x["vol20"].replace(
            0,
            np.nan,
        )
    )

    x["hh20"] = (
        x.high
        .shift(1)
        .rolling(20)
        .max()
    )

    x["hh30"] = (
        x.high
        .shift(1)
        .rolling(30)
        .max()
    )

    x["ll20"] = (
        x.low
        .shift(1)
        .rolling(20)
        .min()
    )

    x["range20"] = (
        (x["hh20"] - x["ll20"])
        / x.close
    )

    x["range_rank"] = (
        x["range20"]
        .rolling(100)
        .rank(pct=True)
    )

    x["ret12"] = (
        x.close
        .pct_change(12)
    )

    x["btc50"] = ema(
        b.close,
        50,
    )

    x["btc200"] = ema(
        b.close,
        200,
    )

    x["btc_bull"] = (
        (b.close > x["btc50"])
        & (
            x["btc50"]
            > x["btc200"]
        )
    )

    x["btc_bear"] = (
        (b.close < x["btc50"])
        & (
            x["btc50"]
            < x["btc200"]
        )
    )

    return x


def classify_regime(x):
    trend = (
        (x["ema20"] > x["ema50"])
        & (
            x["ema50"]
            > x["ema200"]
        )
        & (
            x["adx"] >= 20
        )
    )

    expansion = (
        (x["atr_rank"] >= 0.65)
        & (
            x["vol_ratio"] >= 1.15
        )
    )

    compression = (
        (x["range_rank"] <= 0.30)
        & (
            x["atr_rank"] <= 0.45
        )
    )

    release = (
        compression
        .shift(1)
        .fillna(False)
        & (
            x["atr_rank"] >= 0.55
        )
        & (
            x["vol_ratio"] >= 1.20
        )
    )

    regime = pd.Series(
        "cash",
        index=x.index,
        dtype=object,
    )

    regime.loc[
        trend & expansion
    ] = "breakout"

    regime.loc[
        trend & ~expansion
    ] = "momentum"

    regime.loc[
        release
    ] = "vol_release"

    return regime


def build_signals(x, regime):
    signal = pd.Series(
        False,
        index=x.index,
    )

    strategy = pd.Series(
        "cash",
        index=x.index,
        dtype=object,
    )

    mask = (
        (regime == "breakout")
        & (
            x.close > x["hh30"]
        )
        & x["btc_bull"]
    )

    signal.loc[mask] = True
    strategy.loc[mask] = "breakout"

    mask = (
        (regime == "momentum")
        & (
            x["ret12"] > 0.02
        )
        & (
            x.close > x["ema20"]
        )
        & ~x["btc_bear"]
    )

    signal.loc[mask] = True
    strategy.loc[mask] = "momentum"

    mask = (
        (regime == "vol_release")
        & (
            x.close > x["hh20"]
        )
        & ~x["btc_bear"]
    )

    signal.loc[mask] = True
    strategy.loc[mask] = "vol_release"

    return (
        signal.fillna(False),
        strategy,
    )


@dataclass
class Trade:
    symbol: str
    timeframe: str
    strategy: str
    entry_time: str
    exit_time: str
    pnl: float
    return_pct: float
    bars: int
    reason: str


def summarize(trades):
    if not trades:
        return {
            "trades": 0,
            "roi_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown_pct": 0.0,
            "trade_list": [],
        }

    pnl = np.array(
        [
            t["pnl"]
            for t in trades
        ],
        dtype=float,
    )

    wins = pnl[
        pnl > 0
    ]

    losses = pnl[
        pnl < 0
    ]

    if (
        len(losses)
        and abs(
            losses.sum()
        ) > 0
    ):
        pf = float(
            wins.sum()
            / abs(
                losses.sum()
            )
        )
    elif len(wins):
        pf = float("inf")
    else:
        pf = 0.0

    curve = np.r_[
        START_EQUITY,
        START_EQUITY
        + np.cumsum(pnl),
    ]

    peak = np.maximum.accumulate(
        curve
    )

    dd = float(
        (
            (
                peak - curve
            )
            / peak
            * 100
        ).max()
    )

    return {
        "trades":
            len(trades),

        "roi_pct":
            float(
                (
                    curve[-1]
                    / START_EQUITY
                    - 1
                )
                * 100
            ),

        "profit_factor":
            pf,

        "expectancy":
            float(
                pnl.mean()
            ),

        "max_drawdown_pct":
            dd,

        "trade_list":
            trades,
    }


def backtest(
    x,
    symbol,
    timeframe,
    fee_bps,
    slip_bps,
    start,
    end,
):
    y = x[
        (x.index >= start)
        & (x.index < end)
    ].copy()

    if len(y) < 250:
        return summarize([])

    regimes = classify_regime(y)

    signal, strategy = (
        build_signals(
            y,
            regimes,
        )
    )

    equity = START_EQUITY
    trades = []

    roundtrip_cost = (
        fee_bps
        + slip_bps
    ) * 2 / 10000

    i = 210

    while i < len(y) - 2:

        if not bool(
            signal.iloc[i]
        ):
            i += 1
            continue

        strategy_name = str(
            strategy.iloc[i]
        )

        j = i + 1

        entry = float(
            y.open.iloc[j]
        )

        a = float(
            y["atr"].iloc[i]
        )

        if (
            not np.isfinite(entry)
            or not np.isfinite(a)
            or a <= 0
        ):
            i += 1
            continue

        if strategy_name == "breakout":
            stop_atr = 1.6
            target_r = 2.5
            max_hold = 12

        elif strategy_name == "momentum":
            stop_atr = 1.8
            target_r = 2.0
            max_hold = 18

        else:
            stop_atr = 1.5
            target_r = 2.2
            max_hold = 10

        stop = (
            entry
            - stop_atr * a
        )

        risk = (
            entry - stop
        )

        target = (
            entry
            + target_r * risk
        )

        previous_equity = equity

        qty = (
            equity
            * RISK_PCT
            / risk
        )

        exit_idx = min(
            j + max_hold,
            len(y) - 1,
        )

        exit_px = None
        reason = "time"

        for k in range(
            j,
            exit_idx + 1,
        ):

            if (
                float(
                    y.low.iloc[k]
                )
                <= stop
            ):
                exit_px = stop
                exit_idx = k
                reason = "stop"
                break

            if (
                float(
                    y.high.iloc[k]
                )
                >= target
            ):
                exit_px = target
                exit_idx = k
                reason = "target"
                break

        if exit_px is None:
            exit_px = float(
                y.close.iloc[
                    exit_idx
                ]
            )

        pnl = (
            qty
            * (
                exit_px
                - entry
            )
            - qty
            * entry
            * roundtrip_cost
        )

        equity += pnl

        trades.append(
            asdict(
                Trade(
                    symbol=symbol,
                    timeframe=timeframe,
                    strategy=strategy_name,
                    entry_time=str(
                        y.index[j]
                    ),
                    exit_time=str(
                        y.index[
                            exit_idx
                        ]
                    ),
                    pnl=float(pnl),
                    return_pct=float(
                        pnl
                        / max(
                            previous_equity,
                            1,
                        )
                        * 100
                    ),
                    bars=int(
                        exit_idx
                        - j
                        + 1
                    ),
                    reason=reason,
                )
            )
        )

        i = exit_idx + 1

    return summarize(
        trades
    )


def monte_carlo(
    trades,
    iterations,
):
    pnl = np.array(
        [
            t["pnl"]
            for t in trades
        ],
        dtype=float,
    )

    if len(pnl) < 10:
        return {
            "iterations":
                iterations,

            "trades":
                len(pnl),

            "warning":
                "insufficient trades",
        }

    rng = (
        np.random.default_rng(
            42
        )
    )

    rois = []
    drawdowns = []

    for _ in range(
        iterations
    ):
        sample = rng.choice(
            pnl,
            size=len(pnl),
            replace=True,
        )

        curve = np.r_[
            START_EQUITY,
            START_EQUITY
            + np.cumsum(
                sample
            ),
        ]

        peak = (
            np.maximum.accumulate(
                curve
            )
        )

        rois.append(
            float(
                (
                    curve[-1]
                    / START_EQUITY
                    - 1
                )
                * 100
            )
        )

        drawdowns.append(
            float(
                (
                    (
                        peak - curve
                    )
                    / peak
                    * 100
                ).max()
            )
        )

    rois = np.array(
        rois
    )

    return {
        "iterations":
            iterations,

        "trades":
            len(pnl),

        "probability_profitable_pct":
            float(
                (
                    rois > 0
                ).mean()
                * 100
            ),

        "median_roi_pct":
            float(
                np.median(
                    rois
                )
            ),

        "roi_5th_pct":
            float(
                np.percentile(
                    rois,
                    5,
                )
            ),

        "roi_95th_pct":
            float(
                np.percentile(
                    rois,
                    95,
                )
            ),

        "dd_95th_pct":
            float(
                np.percentile(
                    drawdowns,
                    95,
                )
            ),
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--fee-bps",
        type=float,
        default=15.0,
    )

    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--train-days",
        type=int,
        default=450,
    )

    parser.add_argument(
        "--block-days",
        type=int,
        default=90,
    )

    parser.add_argument(
        "--mc",
        type=int,
        default=10000,
    )

    args = parser.parse_args()

    raw = {
        symbol:
            load(symbol)
        for symbol
        in SYMBOLS
    }

    feature_cache = {}

    for timeframe in TIMEFRAMES:

        btc = resample(
            raw["BTC/USD"],
            timeframe,
        )

        for symbol in SYMBOLS:

            feature_cache[
                (
                    symbol,
                    timeframe,
                )
            ] = build_features(
                resample(
                    raw[symbol],
                    timeframe,
                ),
                btc,
            )

    common_start = max(
        df.index.min()
        for df
        in feature_cache.values()
    )

    common_end = min(
        df.index.max()
        for df
        in feature_cache.values()
    )

    forward_start = (
        common_start
        + pd.Timedelta(
            days=args.train_days
        )
    )

    blocks = []
    all_trades = []

    block_start = (
        forward_start
    )

    block_number = 1

    while (
        block_start
        < common_end
    ):

        block_end = min(
            block_start
            + pd.Timedelta(
                days=args.block_days
            ),
            common_end
            + pd.Timedelta(
                seconds=1
            ),
        )

        block_trades = []

        for symbol in SYMBOLS:

            for timeframe in TIMEFRAMES:

                result = backtest(
                    feature_cache[
                        (
                            symbol,
                            timeframe,
                        )
                    ],
                    symbol,
                    timeframe,
                    args.fee_bps,
                    args.slippage_bps,
                    block_start,
                    block_end,
                )

                block_trades.extend(
                    result[
                        "trade_list"
                    ]
                )

        block_trades.sort(
            key=lambda t:
                t["entry_time"]
        )

        summary = summarize(
            block_trades
        )

        all_trades.extend(
            block_trades
        )

        blocks.append({
            "block":
                block_number,

            "start":
                str(block_start),

            "end":
                str(block_end),

            "trades":
                summary["trades"],

            "roi_pct":
                summary["roi_pct"],

            "profit_factor":
                summary[
                    "profit_factor"
                ],

            "expectancy":
                summary[
                    "expectancy"
                ],

            "max_drawdown_pct":
                summary[
                    "max_drawdown_pct"
                ],

            "profitable":
                summary[
                    "roi_pct"
                ] > 0,
        })

        print(
            f"Block {block_number}: "
            f"trades={summary['trades']} "
            f"ROI={summary['roi_pct']:.2f}% "
            f"PF={summary['profit_factor']} "
            f"DD={summary['max_drawdown_pct']:.2f}%",
            flush=True,
        )

        block_start = (
            block_end
        )

        block_number += 1

    all_trades.sort(
        key=lambda t:
            t["entry_time"]
    )

    aggregate = summarize(
        all_trades
    )

    mc = monte_carlo(
        all_trades,
        args.mc,
    )

    profitable_blocks = sum(
        b["profitable"]
        for b in blocks
    )

    profitable_block_pct = (
        profitable_blocks
        / max(
            len(blocks),
            1,
        )
        * 100
    )

    trade_df = pd.DataFrame(
        all_trades
    )

    by_strategy = {}
    by_symbol = {}

    if not trade_df.empty:

        for name, group in (
            trade_df.groupby(
                "strategy"
            )
        ):
            m = summarize(
                group.to_dict(
                    "records"
                )
            )

            by_strategy[
                name
            ] = {
                k: v
                for k, v
                in m.items()
                if k != "trade_list"
            }

        for name, group in (
            trade_df.groupby(
                "symbol"
            )
        ):
            m = summarize(
                group.to_dict(
                    "records"
                )
            )

            by_symbol[
                name
            ] = {
                k: v
                for k, v
                in m.items()
                if k != "trade_list"
            }

    passed = (
        aggregate["trades"] >= 30
        and aggregate["roi_pct"] > 0
        and np.isfinite(
            aggregate[
                "profit_factor"
            ]
        )
        and aggregate[
            "profit_factor"
        ] >= 1.25
        and profitable_block_pct >= 50
        and mc.get(
            "probability_profitable_pct",
            0,
        ) >= 65
    )

    result = {
        "methodology": {
            "symbols":
                SYMBOLS,

            "timeframes":
                TIMEFRAMES,

            "forward_start":
                str(
                    forward_start
                ),

            "forward_end":
                str(
                    common_end
                ),

            "parameter_optimization":
                False,

            "execution_enabled":
                False,
        },

        "aggregate_oos": {
            k: v
            for k, v
            in aggregate.items()
            if k != "trade_list"
        },

        "forward_blocks":
            blocks,

        "profitable_blocks":
            profitable_blocks,

        "profitable_block_pct":
            profitable_block_pct,

        "by_strategy":
            by_strategy,

        "by_symbol":
            by_symbol,

        "monte_carlo":
            mc,

        "research_gate": {
            "minimum_oos_trades": 30,
            "minimum_pf": 1.25,
            "minimum_profitable_block_pct": 50.0,
            "minimum_mc_probability_pct": 65.0,
            "passed": passed,
        },
    }

    (
        REPORTS
        / "crypto_regime_switch_lab_v6_latest.json"
    ).write_text(
        json.dumps(
            result,
            indent=2,
        ),
        encoding="utf-8",
    )

    pd.DataFrame(
        blocks
    ).to_csv(
        REPORTS
        / "crypto_regime_switch_lab_v6_blocks.csv",
        index=False,
    )

    pd.DataFrame(
        all_trades
    ).to_csv(
        REPORTS
        / "crypto_regime_switch_lab_v6_oos_trades.csv",
        index=False,
    )

    print(
        "\n===== CRYPTO REGIME SWITCH LAB V6 ====="
    )

    print(
        json.dumps({
            "forward_blocks":
                len(blocks),

            "profitable_blocks":
                profitable_blocks,

            "profitable_block_pct":
                profitable_block_pct,

            "oos_trades":
                aggregate[
                    "trades"
                ],

            "oos_roi_pct":
                aggregate[
                    "roi_pct"
                ],

            "oos_pf":
                aggregate[
                    "profit_factor"
                ],

            "oos_expectancy":
                aggregate[
                    "expectancy"
                ],

            "oos_max_dd_pct":
                aggregate[
                    "max_drawdown_pct"
                ],

            "mc_probability_profitable_pct":
                mc.get(
                    "probability_profitable_pct"
                ),

            "mc_dd_95th_pct":
                mc.get(
                    "dd_95th_pct"
                ),

            "passed":
                passed,
        }, indent=2)
    )

    print(
        "\nBy strategy:"
    )

    print(
        json.dumps(
            by_strategy,
            indent=2,
        )
    )

    print(
        "\nBy symbol:"
    )

    print(
        json.dumps(
            by_symbol,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
