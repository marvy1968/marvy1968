#!/usr/bin/env python3

import argparse
import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "crypto_history.duckdb"
REPORT_DIR = ROOT / "reports" / "v7"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "BTC/USD",
    "ETH/USD",
    "DOGE/USD",
    "LINK/USD",
    "AVAX/USD",
]

TIMEFRAMES = ["1h", "4h"]

# Conservative friction assumptions carried forward from prior labs.
FEE_BPS_SIDE = 15.0
SLIPPAGE_BPS_SIDE = 5.0
ROUND_TRIP_COST = 2.0 * (
    FEE_BPS_SIDE + SLIPPAGE_BPS_SIDE
) / 10000.0

FORWARD_START = pd.Timestamp(
    "2024-11-01 00:00:00",
    tz="UTC",
)

N_BLOCKS = 8

MIN_OOS_TRADES = 30
MIN_PF = 1.25
MIN_PROFITABLE_BLOCK_PCT = 50.0
MIN_MC_PROFITABLE = 65.0

# Stronger requirement than simply "positive".
MIN_EXPECTANCY_BPS = 2.0

# Score-tail experiment.
QUANTILES = [
    0.50,
    0.60,
    0.70,
    0.80,
    0.85,
    0.90,
    0.95,
]


def safe_div(a, b, default=0.0):
    if b is None or b == 0:
        return default
    if not np.isfinite(b):
        return default
    return a / b


def discover_table(con):
    tables = con.execute(
        "SHOW TABLES"
    ).fetchall()

    names = [x[0] for x in tables]

    if not names:
        raise RuntimeError(
            "No tables found in crypto_history.duckdb"
        )

    print("DuckDB tables:", names)

    preferred = [
        "bars",
        "crypto_bars",
        "history",
        "market_data",
        "ohlcv",
        "candles",
    ]

    for name in preferred:
        if name in names:
            return name

    # Find a table containing OHLCV-like columns.
    for name in names:
        cols = con.execute(
            f"DESCRIBE {name}"
        ).fetchdf()["column_name"].str.lower().tolist()

        required = {
            "open",
            "high",
            "low",
            "close",
        }

        if required.issubset(set(cols)):
            return name

    raise RuntimeError(
        f"No OHLCV table found. Tables={names}"
    )


def normalize_columns(df):
    rename = {}

    aliases = {
        "timestamp": [
            "timestamp",
            "time",
            "datetime",
            "date",
            "ts",
        ],
        "symbol": [
            "symbol",
            "ticker",
            "asset",
        ],
        "timeframe": [
            "timeframe",
            "tf",
            "interval",
        ],
        "open": ["open", "o"],
        "high": ["high", "h"],
        "low": ["low", "l"],
        "close": ["close", "c"],
        "volume": [
            "volume",
            "v",
            "vol",
        ],
    }

    lower = {
        c.lower(): c
        for c in df.columns
    }

    for target, candidates in aliases.items():
        for candidate in candidates:
            if candidate in lower:
                rename[lower[candidate]] = target
                break

    df = df.rename(columns=rename)

    needed = {
        "timestamp",
        "symbol",
        "open",
        "high",
        "low",
        "close",
    }

    missing = needed - set(df.columns)

    if missing:
        raise RuntimeError(
            f"Missing required columns: {missing}. "
            f"Found={list(df.columns)}"
        )

    if "volume" not in df:
        df["volume"] = 0.0

    return df


def load_raw():
    if not DB.exists():
        raise FileNotFoundError(
            f"Database not found: {DB}"
        )

    con = duckdb.connect(
        str(DB),
        read_only=True,
    )

    table = discover_table(con)

    print("Using table:", table)

    df = con.execute(
        f"SELECT * FROM {table}"
    ).fetchdf()

    con.close()

    df = normalize_columns(df)

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        utc=True,
        errors="coerce",
    )

    df = df.dropna(
        subset=[
            "timestamp",
            "symbol",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    df = df[
        df["symbol"].isin(SYMBOLS)
    ].copy()

    return df


def infer_base_minutes(df):
    times = (
        df["timestamp"]
        .sort_values()
        .drop_duplicates()
    )

    delta = (
        times.diff()
        .dropna()
        .dt.total_seconds()
        .div(60)
    )

    if len(delta) == 0:
        return None

    return float(delta.median())


def resample_symbol(df, timeframe):
    df = (
        df.sort_values("timestamp")
        .set_index("timestamp")
    )

    rule = {
        "1h": "1h",
        "4h": "4h",
    }[timeframe]

    out = df.resample(rule).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )

    out = out.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    out["symbol"] = df["symbol"].iloc[0]
    out["timeframe"] = timeframe

    return out.reset_index()


def prepare_bars(raw):
    frames = []

    has_tf = (
        "timeframe" in raw.columns
        and raw["timeframe"].notna().any()
    )

    for symbol in SYMBOLS:
        sdf = raw[
            raw["symbol"] == symbol
        ].copy()

        if sdf.empty:
            continue

        if has_tf:
            tf_values = (
                sdf["timeframe"]
                .astype(str)
                .str.lower()
            )

            for tf in TIMEFRAMES:
                aliases = {
                    "1h": [
                        "1h",
                        "60min",
                        "60m",
                        "hour",
                    ],
                    "4h": [
                        "4h",
                        "240min",
                        "240m",
                    ],
                }[tf]

                tdf = sdf[
                    tf_values.isin(aliases)
                ].copy()

                if not tdf.empty:
                    tdf["timeframe"] = tf
                    frames.append(tdf)

            continue

        # No timeframe column: resample whatever base history exists.
        print(
            symbol,
            "base median minutes:",
            infer_base_minutes(sdf),
        )

        for tf in TIMEFRAMES:
            frames.append(
                resample_symbol(
                    sdf,
                    tf,
                )
            )

    if not frames:
        raise RuntimeError(
            "Could not construct 1h/4h bars."
        )

    bars = pd.concat(
        frames,
        ignore_index=True,
    )

    bars = bars.sort_values(
        [
            "symbol",
            "timeframe",
            "timestamp",
        ]
    ).drop_duplicates(
        [
            "symbol",
            "timeframe",
            "timestamp",
        ],
        keep="last",
    )

    return bars


def true_range(df):
    prev = df["close"].shift(1)

    a = df["high"] - df["low"]
    b = (df["high"] - prev).abs()
    c = (df["low"] - prev).abs()

    return pd.concat(
        [a, b, c],
        axis=1,
    ).max(axis=1)


def rolling_rank(series, window=100):
    return series.rolling(
        window,
        min_periods=max(20, window // 4),
    ).apply(
        lambda x:
            pd.Series(x).rank(
                pct=True
            ).iloc[-1],
        raw=False,
    )


def build_features(df):
    df = df.copy().sort_values(
        "timestamp"
    )

    tr = true_range(df)

    df["atr"] = tr.rolling(
        14,
        min_periods=14,
    ).mean()

    df["atr_pct"] = (
        df["atr"] /
        df["close"]
    )

    df["atr_rank"] = rolling_rank(
        df["atr_pct"],
        100,
    )

    df["ema20"] = (
        df["close"]
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    df["ema50"] = (
        df["close"]
        .ewm(
            span=50,
            adjust=False,
        )
        .mean()
    )

    df["ema100"] = (
        df["close"]
        .ewm(
            span=100,
            adjust=False,
        )
        .mean()
    )

    df["ret1"] = (
        df["close"]
        .pct_change()
    )

    df["ret3"] = (
        df["close"]
        .pct_change(3)
    )

    df["ret6"] = (
        df["close"]
        .pct_change(6)
    )

    df["ret12"] = (
        df["close"]
        .pct_change(12)
    )

    vol_med = (
        df["volume"]
        .rolling(
            30,
            min_periods=10,
        )
        .median()
    )

    df["vol_ratio"] = (
        df["volume"] /
        vol_med.replace(0, np.nan)
    )

    prior_high = (
        df["high"]
        .shift(1)
        .rolling(
            20,
            min_periods=20,
        )
        .max()
    )

    df["breakout_atr"] = (
        (
            df["close"] -
            prior_high
        ) /
        df["atr"]
    )

    df["distance_ema20_atr"] = (
        (
            df["close"] -
            df["ema20"]
        ) /
        df["atr"]
    )

    df["trend_strength"] = (
        (
            df["ema20"] -
            df["ema50"]
        ) /
        df["atr"]
    )

    # Volatility acceleration.
    short_vol = (
        df["ret1"]
        .rolling(
            6,
            min_periods=4,
        )
        .std()
    )

    long_vol = (
        df["ret1"]
        .rolling(
            24,
            min_periods=12,
        )
        .std()
    )

    df["vol_accel"] = (
        short_vol /
        long_vol.replace(0, np.nan)
    )

    # Failed breakout proxy:
    # recently exceeded prior high but closed back below it.
    failed = (
        (df["high"] > prior_high) &
        (df["close"] <= prior_high)
    ).astype(float)

    df["failed_breakouts"] = (
        failed.shift(1)
        .rolling(
            12,
            min_periods=1,
        )
        .sum()
    )

    # Future returns. Strictly labels, never features.
    for horizon in [3, 6, 12]:
        df[f"future_{horizon}"] = (
            df["close"]
            .shift(-horizon) /
            df["close"] -
            1.0
        )

    return df


def attach_btc(features):
    pieces = []

    for tf in TIMEFRAMES:
        part = features[
            features["timeframe"] == tf
        ].copy()

        btc = part[
            part["symbol"] == "BTC/USD"
        ][
            [
                "timestamp",
                "ret3",
                "ret12",
                "close",
                "ema50",
            ]
        ].copy()

        btc = btc.rename(
            columns={
                "ret3": "btc_ret3",
                "ret12": "btc_ret12",
                "close": "btc_close",
                "ema50": "btc_ema50",
            }
        )

        part = part.merge(
            btc,
            on="timestamp",
            how="left",
        )

        part["rel_strength_3"] = (
            part["ret3"] -
            part["btc_ret3"]
        )

        part["rel_strength_12"] = (
            part["ret12"] -
            part["btc_ret12"]
        )

        part["btc_bull"] = (
            part["btc_close"] >
            part["btc_ema50"]
        ).astype(float)

        pieces.append(part)

    return pd.concat(
        pieces,
        ignore_index=True,
    )


def percentile_score(series):
    return (
        series.rank(
            pct=True,
            method="average",
        )
        .clip(0, 1)
    )


def score_group(df):
    df = df.copy()

    # All components scaled 0..1.
    volume = percentile_score(
        df["vol_ratio"]
        .replace(
            [np.inf, -np.inf],
            np.nan,
        )
        .fillna(0)
    )

    atr = percentile_score(
        df["atr_rank"]
        .fillna(0)
    )

    trend = percentile_score(
        df["trend_strength"]
        .clip(-5, 5)
        .fillna(-5)
    )

    rs = percentile_score(
        df["rel_strength_12"]
        .clip(-0.30, 0.30)
        .fillna(0)
    )

    breakout = percentile_score(
        df["breakout_atr"]
        .clip(-3, 5)
        .fillna(-3)
    )

    vol_accel = percentile_score(
        df["vol_accel"]
        .clip(0, 5)
        .fillna(0)
    )

    # Prefer early moves rather than extremely extended moves.
    extension = (
        df["distance_ema20_atr"]
        .abs()
        .fillna(10)
    )

    early_move = (
        1.0 -
        percentile_score(extension)
    )

    failure_penalty = (
        1.0 -
        percentile_score(
            df["failed_breakouts"]
            .fillna(10)
        )
    )

    btc = (
        df["btc_bull"]
        .fillna(0)
        .clip(0, 1)
    )

    # Fixed weights. These are NOT fitted on forward results.
    df["edge_score"] = (
        0.17 * volume +
        0.13 * atr +
        0.15 * trend +
        0.15 * rs +
        0.15 * breakout +
        0.08 * vol_accel +
        0.07 * early_move +
        0.05 * failure_penalty +
        0.05 * btc
    )

    return df


def build_scores(features):
    pieces = []

    for (symbol, timeframe), group in features.groupby(
        ["symbol", "timeframe"],
        sort=False,
    ):
        scored = score_group(group.copy())

        # Explicitly restore identifiers.  Pandas groupby/apply
        # behavior can otherwise drop grouping columns.
        scored["symbol"] = symbol
        scored["timeframe"] = timeframe

        pieces.append(scored)

    if not pieces:
        raise RuntimeError(
            "No symbol/timeframe groups available for scoring."
        )

    return pd.concat(
        pieces,
        ignore_index=True,
    )


def trade_returns(df, horizon):
    raw = df[
        f"future_{horizon}"
    ].to_numpy(dtype=float)

    return raw - ROUND_TRIP_COST


def stats_from_returns(returns):
    r = np.asarray(
        returns,
        dtype=float,
    )

    r = r[
        np.isfinite(r)
    ]

    if len(r) == 0:
        return {
            "trades": 0,
            "roi_pct": 0.0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_bps": 0.0,
            "max_drawdown_pct": 0.0,
        }

    equity = np.cumprod(
        1.0 + r
    )

    peak = np.maximum.accumulate(
        np.r_[
            1.0,
            equity,
        ]
    )[1:]

    dd = (
        equity / peak -
        1.0
    )

    wins = r[
        r > 0
    ].sum()

    losses = abs(
        r[
            r < 0
        ].sum()
    )

    if losses == 0:
        pf = 999.0 if wins > 0 else 0.0
    else:
        pf = wins / losses

    return {
        "trades": int(len(r)),
        "roi_pct": float(
            (
                np.prod(1.0 + r) -
                1.0
            ) * 100.0
        ),
        "win_rate_pct": float(
            (r > 0).mean() * 100.0
        ),
        "profit_factor": float(pf),
        "expectancy_bps": float(
            r.mean() * 10000.0
        ),
        "max_drawdown_pct": float(
            abs(dd.min()) * 100.0
        ),
    }


def make_blocks(start, end, n):
    edges = pd.date_range(
        start=start,
        end=end,
        periods=n + 1,
    )

    blocks = []

    for i in range(n):
        blocks.append(
            (
                edges[i],
                edges[i + 1],
            )
        )

    return blocks


def monte_carlo(returns, iterations):
    r = np.asarray(
        returns,
        dtype=float,
    )

    r = r[
        np.isfinite(r)
    ]

    if len(r) < 10:
        return {
            "iterations": iterations,
            "trades": int(len(r)),
            "warning": "insufficient trades",
        }

    rng = np.random.default_rng(
        20260811
    )

    final_roi = []
    max_dd = []

    for _ in range(iterations):
        sample = rng.choice(
            r,
            size=len(r),
            replace=True,
        )

        equity = np.cumprod(
            1.0 + sample
        )

        peak = np.maximum.accumulate(
            np.r_[
                1.0,
                equity,
            ]
        )[1:]

        dd = (
            equity / peak -
            1.0
        )

        final_roi.append(
            (
                equity[-1] -
                1.0
            ) * 100.0
        )

        max_dd.append(
            abs(dd.min()) * 100.0
        )

    final_roi = np.asarray(
        final_roi
    )

    max_dd = np.asarray(
        max_dd
    )

    return {
        "iterations": int(iterations),
        "trades": int(len(r)),
        "probability_profitable_pct": float(
            (
                final_roi > 0
            ).mean() * 100.0
        ),
        "median_roi_pct": float(
            np.median(final_roi)
        ),
        "roi_5th_pct": float(
            np.percentile(
                final_roi,
                5,
            )
        ),
        "roi_95th_pct": float(
            np.percentile(
                final_roi,
                95,
            )
        ),
        "dd_95th_pct": float(
            np.percentile(
                max_dd,
                95,
            )
        ),
    }


def evaluate_candidate(
    forward,
    threshold,
    horizon,
    blocks,
):
    # Threshold is a fixed score cutoff established from
    # the development-period quantile.
    chosen = forward[
        forward["edge_score"] >= threshold
    ].copy()

    chosen = chosen[
        chosen[
            f"future_{horizon}"
        ].notna()
    ]

    chosen["net_return"] = (
        chosen[
            f"future_{horizon}"
        ] -
        ROUND_TRIP_COST
    )

    overall = stats_from_returns(
        chosen["net_return"]
    )

    block_results = []

    for i, (start, end) in enumerate(
        blocks,
        1,
    ):
        b = chosen[
            (chosen["timestamp"] >= start) &
            (chosen["timestamp"] < end)
        ]

        s = stats_from_returns(
            b["net_return"]
        )

        s.update(
            {
                "block": i,
                "start": str(start),
                "end": str(end),
                "profitable":
                    s["roi_pct"] > 0,
            }
        )

        block_results.append(s)

    profitable = sum(
        x["profitable"]
        for x in block_results
    )

    profitable_pct = (
        100.0 *
        profitable /
        len(block_results)
    )

    return (
        chosen,
        overall,
        block_results,
        profitable_pct,
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mc",
        type=int,
        default=10000,
    )

    args = parser.parse_args()

    print(
        "===== CRYPTO EDGE QUALITY LAB V7 ====="
    )

    print(
        "Research only. Execution enabled: NO"
    )

    raw = load_raw()
    bars = prepare_bars(raw)

    print(
        "Prepared bars:",
        len(bars),
    )

    feature_parts = []

    for (symbol, tf), group in bars.groupby(
        [
            "symbol",
            "timeframe",
        ]
    ):
        x = build_features(group)
        feature_parts.append(x)

    features = pd.concat(
        feature_parts,
        ignore_index=True,
    )

    features = attach_btc(
        features
    )

    features = build_scores(
        features
    )

    features = features.replace(
        [np.inf, -np.inf],
        np.nan,
    )

    development = features[
        features["timestamp"] <
        FORWARD_START
    ].copy()

    forward = features[
        features["timestamp"] >=
        FORWARD_START
    ].copy()

    if development.empty:
        raise RuntimeError(
            "No development data before forward start."
        )

    if forward.empty:
        raise RuntimeError(
            "No forward data."
        )

    end = forward[
        "timestamp"
    ].max() + pd.Timedelta(seconds=1)

    blocks = make_blocks(
        FORWARD_START,
        end,
        N_BLOCKS,
    )

    candidate_rows = []
    candidate_cache = {}

    # Score thresholds are learned ONLY as score distribution
    # quantiles from the development period. No P/L optimization.
    for tf in TIMEFRAMES:
        dev_tf = development[
            development["timeframe"] == tf
        ]

        fwd_tf = forward[
            forward["timeframe"] == tf
        ]

        if dev_tf.empty or fwd_tf.empty:
            continue

        for q in QUANTILES:
            threshold = float(
                dev_tf[
                    "edge_score"
                ].quantile(q)
            )

            for horizon in [3, 6, 12]:
                (
                    selected,
                    overall,
                    block_results,
                    profitable_pct,
                ) = evaluate_candidate(
                    fwd_tf,
                    threshold,
                    horizon,
                    blocks,
                )

                key = (
                    tf,
                    q,
                    horizon,
                )

                candidate_cache[key] = (
                    selected,
                    block_results,
                )

                row = {
                    "timeframe": tf,
                    "development_score_quantile": q,
                    "score_threshold": threshold,
                    "holding_bars": horizon,
                    **overall,
                    "profitable_block_pct":
                        profitable_pct,
                }

                candidate_rows.append(
                    row
                )

    rankings = pd.DataFrame(
        candidate_rows
    )

    if rankings.empty:
        raise RuntimeError(
            "No V7 candidates generated."
        )

    # Diagnostic ranking only.
    # This does NOT make a failing candidate pass.
    rankings["research_score"] = (
        rankings["profit_factor"]
        .clip(upper=5) * 10.0
        +
        rankings["profitable_block_pct"]
        * 0.25
        +
        rankings["expectancy_bps"]
        * 0.20
        -
        rankings["max_drawdown_pct"]
        * 0.50
    )

    rankings = rankings.sort_values(
        "research_score",
        ascending=False,
    ).reset_index(drop=True)

    # Important:
    # We don't simply declare the highest-scoring candidate valid.
    # It must independently satisfy every gate.
    rankings["gate_trade_count"] = (
        rankings["trades"] >=
        MIN_OOS_TRADES
    )

    rankings["gate_pf"] = (
        rankings["profit_factor"] >=
        MIN_PF
    )

    rankings["gate_blocks"] = (
        rankings[
            "profitable_block_pct"
        ] >=
        MIN_PROFITABLE_BLOCK_PCT
    )

    rankings["gate_expectancy"] = (
        rankings[
            "expectancy_bps"
        ] >=
        MIN_EXPECTANCY_BPS
    )

    prelim = rankings[
        rankings[
            [
                "gate_trade_count",
                "gate_pf",
                "gate_blocks",
                "gate_expectancy",
            ]
        ].all(axis=1)
    ].copy()

    # Initialize MC/report columns even when no candidate
    # survives the preliminary gates.
    rankings["mc_probability_profitable_pct"] = 0.0
    rankings["mc_dd_95th_pct"] = np.nan
    rankings["passed"] = False

    mc_results = []

    # MC every preliminarily viable candidate.
    for idx, row in prelim.iterrows():
        key = (
            row["timeframe"],
            row[
                "development_score_quantile"
            ],
            int(row["holding_bars"]),
        )

        selected, _ = candidate_cache[key]

        mc = monte_carlo(
            selected[
                f"future_{int(row['holding_bars'])}"
            ] - ROUND_TRIP_COST,
            args.mc,
        )

        prob = mc.get(
            "probability_profitable_pct",
            0.0,
        )

        passed_mc = (
            prob >=
            MIN_MC_PROFITABLE
        )

        mc_results.append(
            {
                "ranking_index": int(idx),
                "mc": mc,
                "passed_mc": passed_mc,
            }
        )

        rankings.loc[
            idx,
            "mc_probability_profitable_pct"
        ] = prob

        rankings.loc[
            idx,
            "mc_dd_95th_pct"
        ] = mc.get(
            "dd_95th_pct",
            np.nan,
        )

        rankings.loc[
            idx,
            "passed"
        ] = bool(passed_mc)

    rankings[
        "mc_probability_profitable_pct"
    ] = rankings[
        "mc_probability_profitable_pct"
    ].fillna(0.0)

    rankings["passed"] = (
        rankings["passed"]
        .fillna(False)
        .astype(bool)
    )

    passing = rankings[
        rankings["passed"]
    ].copy()

    # Additional concentration diagnostics for the best
    # actually passing candidate.
    champion = None
    champion_blocks = []
    by_symbol = {}
    champion_mc = {}

    if not passing.empty:
        best_index = passing.index[0]
        best = rankings.loc[
            best_index
        ]

        key = (
            best["timeframe"],
            best[
                "development_score_quantile"
            ],
            int(best["holding_bars"]),
        )

        champion_df, champion_blocks = (
            candidate_cache[key]
        )

        horizon = int(
            best["holding_bars"]
        )

        champion_df = (
            champion_df.copy()
        )

        champion_df[
            "net_return"
        ] = (
            champion_df[
                f"future_{horizon}"
            ] -
            ROUND_TRIP_COST
        )

        for symbol, group in (
            champion_df.groupby("symbol")
        ):
            by_symbol[symbol] = (
                stats_from_returns(
                    group["net_return"]
                )
            )

        champion_mc = monte_carlo(
            champion_df["net_return"],
            args.mc,
        )

        champion = {
            k: (
                bool(v)
                if isinstance(
                    v,
                    (np.bool_, bool),
                )
                else
                float(v)
                if isinstance(
                    v,
                    (
                        np.floating,
                        float,
                    ),
                )
                else
                int(v)
                if isinstance(
                    v,
                    (
                        np.integer,
                        int,
                    ),
                )
                else v
            )
            for k, v in best.to_dict().items()
        }

    # Decile/quality analysis across all forward observations.
    decile_rows = []

    for tf in TIMEFRAMES:
        part = forward[
            forward["timeframe"] == tf
        ].copy()

        part = part.dropna(
            subset=[
                "edge_score",
                "future_6",
            ]
        )

        if len(part) < 20:
            continue

        part["score_decile"] = pd.qcut(
            part["edge_score"],
            q=10,
            labels=False,
            duplicates="drop",
        )

        for decile, group in (
            part.groupby("score_decile")
        ):
            returns = (
                group["future_6"] -
                ROUND_TRIP_COST
            )

            s = stats_from_returns(
                returns
            )

            decile_rows.append(
                {
                    "timeframe": tf,
                    "score_decile":
                        int(decile) + 1,
                    "mean_edge_score":
                        float(
                            group[
                                "edge_score"
                            ].mean()
                        ),
                    **s,
                }
            )

    deciles = pd.DataFrame(
        decile_rows
    )

    # Check whether increasing quality corresponds to increasing
    # forward expectancy. Spearman correlation is diagnostic.
    monotonicity = {}

    if not deciles.empty:
        for tf, group in deciles.groupby(
            "timeframe"
        ):
            if len(group) >= 4:
                corr = group[
                    [
                        "score_decile",
                        "expectancy_bps",
                    ]
                ].corr(
                    method="spearman"
                ).iloc[0, 1]

                monotonicity[tf] = float(
                    corr
                )

    # Hard final decision.
    if champion is not None:
        verdict = "CRYPTO_EDGE_SURVIVED"
        reason = (
            "At least one edge-quality candidate "
            "passed trade-count, PF, expectancy, "
            "forward-block and Monte Carlo gates."
        )
    else:
        verdict = "STOP_CRYPTO"
        reason = (
            "No edge-quality candidate survived "
            "the complete forward/Monte-Carlo gates. "
            "Do not continue parameter tuning on this "
            "crypto strategy family."
        )

    report = {
        "methodology": {
            "version": "v7",
            "purpose":
                "final crypto edge-quality go/no-go test",
            "symbols": SYMBOLS,
            "timeframes": TIMEFRAMES,
            "forward_start":
                str(FORWARD_START),
            "forward_end":
                str(end),
            "forward_blocks":
                N_BLOCKS,
            "fee_bps_per_side":
                FEE_BPS_SIDE,
            "slippage_bps_per_side":
                SLIPPAGE_BPS_SIDE,
            "parameter_optimization":
                False,
            "execution_enabled":
                False,
        },
        "gates": {
            "minimum_oos_trades":
                MIN_OOS_TRADES,
            "minimum_pf":
                MIN_PF,
            "minimum_expectancy_bps":
                MIN_EXPECTANCY_BPS,
            "minimum_profitable_block_pct":
                MIN_PROFITABLE_BLOCK_PCT,
            "minimum_mc_probability_pct":
                MIN_MC_PROFITABLE,
        },
        "verdict": verdict,
        "reason": reason,
        "champion": champion,
        "champion_monte_carlo":
            champion_mc,
        "champion_forward_blocks":
            champion_blocks,
        "champion_by_symbol":
            by_symbol,
        "score_monotonicity_spearman":
            monotonicity,
        "mc_candidates":
            mc_results,
    }

    json_path = (
        REPORT_DIR /
        "crypto_edge_quality_lab_v7_latest.json"
    )

    ranking_path = (
        REPORT_DIR /
        "crypto_edge_quality_lab_v7_rankings.csv"
    )

    decile_path = (
        REPORT_DIR /
        "crypto_edge_quality_lab_v7_deciles.csv"
    )

    json_path.write_text(
        json.dumps(
            report,
            indent=2,
            default=str,
        )
    )

    rankings.to_csv(
        ranking_path,
        index=False,
    )

    deciles.to_csv(
        decile_path,
        index=False,
    )

    print()
    print(
        "========== V7 FINAL RESULT =========="
    )

    print(
        json.dumps(
            {
                "verdict": verdict,
                "passing_candidates":
                    int(len(passing)),
                "candidates_tested":
                    int(len(rankings)),
                "score_monotonicity":
                    monotonicity,
                "champion":
                    champion,
            },
            indent=2,
            default=str,
        )
    )

    print()
    print(
        "Top candidates:"
    )

    display_cols = [
        "timeframe",
        "development_score_quantile",
        "holding_bars",
        "trades",
        "roi_pct",
        "profit_factor",
        "expectancy_bps",
        "max_drawdown_pct",
        "profitable_block_pct",
        "mc_probability_profitable_pct",
        "passed",
    ]

    print(
        rankings[
            display_cols
        ].head(15).to_string(
            index=False
        )
    )

    print()
    print("Reports:")
    print(json_path)
    print(ranking_path)
    print(decile_path)

    print()
    print(
        "FINAL VERDICT:",
        verdict,
    )


if __name__ == "__main__":
    main()
