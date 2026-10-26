from __future__ import annotations

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


def normalize_frame(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    result = frame.copy()

    result.columns = [
        str(column).strip().lower()
        for column in result.columns
    ]

    missing = (
        REQUIRED_COLUMNS
        - set(result.columns)
    )

    if missing:
        raise ValueError(
            "Missing candle columns: "
            + ", ".join(
                sorted(missing)
            )
        )

    result["timestamp"] = pd.to_datetime(
        result["timestamp"],
        utc=True,
        errors="coerce",
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for column in numeric_columns:
        result[column] = pd.to_numeric(
            result[column],
            errors="coerce",
        )

    result = (
        result.dropna(
            subset=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        )
        .sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return result


def ema(
    series: pd.Series,
    period: int,
) -> pd.Series:
    return series.ewm(
        span=period,
        adjust=False,
        min_periods=period,
    ).mean()


def true_range(
    frame: pd.DataFrame,
) -> pd.Series:
    previous_close = (
        frame["close"].shift(1)
    )

    components = pd.concat(
        [
            frame["high"]
            - frame["low"],
            (
                frame["high"]
                - previous_close
            ).abs(),
            (
                frame["low"]
                - previous_close
            ).abs(),
        ],
        axis=1,
    )

    return components.max(axis=1)


def atr(
    frame: pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    range_series = true_range(frame)

    return range_series.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def directional_movement(
    frame: pd.DataFrame,
    period: int = 14,
) -> tuple[
    pd.Series,
    pd.Series,
    pd.Series,
]:
    upward_move = frame["high"].diff()
    downward_move = (
        -frame["low"].diff()
    )

    plus_dm = pd.Series(
        np.where(
            (
                upward_move
                > downward_move
            )
            & (
                upward_move
                > 0
            ),
            upward_move,
            0.0,
        ),
        index=frame.index,
    )

    minus_dm = pd.Series(
        np.where(
            (
                downward_move
                > upward_move
            )
            & (
                downward_move
                > 0
            ),
            downward_move,
            0.0,
        ),
        index=frame.index,
    )

    atr_series = atr(
        frame,
        period,
    )

    plus_smoothed = plus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    minus_smoothed = minus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    plus_di = (
        100
        * plus_smoothed
        / atr_series.replace(
            0,
            np.nan,
        )
    )

    minus_di = (
        100
        * minus_smoothed
        / atr_series.replace(
            0,
            np.nan,
        )
    )

    denominator = (
        plus_di
        + minus_di
    ).replace(
        0,
        np.nan,
    )

    dx = (
        100
        * (
            plus_di
            - minus_di
        ).abs()
        / denominator
    )

    adx_series = dx.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return (
        plus_di,
        minus_di,
        adx_series,
    )


def rsi(
    series: pd.Series,
    period: int = 14,
) -> pd.Series:
    change = series.diff()

    gains = change.clip(lower=0)
    losses = (
        -change.clip(upper=0)
    )

    average_gain = gains.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    relative_strength = (
        average_gain
        / average_loss.replace(
            0,
            np.nan,
        )
    )

    return (
        100
        - (
            100
            / (
                1
                + relative_strength
            )
        )
    )


def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    standard_deviations: float = 2.0,
) -> tuple[
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
]:
    middle = series.rolling(
        period,
        min_periods=period,
    ).mean()

    deviation = series.rolling(
        period,
        min_periods=period,
    ).std(ddof=0)

    upper = (
        middle
        + standard_deviations
        * deviation
    )

    lower = (
        middle
        - standard_deviations
        * deviation
    )

    width = (
        upper
        - lower
    ) / middle.replace(
        0,
        np.nan,
    )

    return (
        upper,
        middle,
        lower,
        width,
    )


def rolling_percentile_rank(
    series: pd.Series,
    window: int,
) -> pd.Series:
    def percentile(
        values: np.ndarray,
    ) -> float:
        current = values[-1]

        if np.isnan(current):
            return np.nan

        valid = values[
            ~np.isnan(values)
        ]

        if len(valid) == 0:
            return np.nan

        return float(
            np.mean(
                valid
                <= current
            )
        )

    return series.rolling(
        window,
        min_periods=max(
            20,
            window // 3,
        ),
    ).apply(
        percentile,
        raw=True,
    )


def normalized_slope(
    series: pd.Series,
    period: int = 20,
) -> pd.Series:
    shifted = series.shift(period)

    return (
        series
        - shifted
    ) / shifted.replace(
        0,
        np.nan,
    )


def build_indicator_frame(
    frame: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    result = normalize_frame(frame)

    fast_period = int(
        config["ema_fast"]
    )

    slow_period = int(
        config["ema_slow"]
    )

    context_period = int(
        config["ema_context"]
    )

    atr_period = int(
        config["atr_period"]
    )

    adx_period = int(
        config["adx_period"]
    )

    rsi_period = int(
        config["rsi_period"]
    )

    bollinger_period = int(
        config["bollinger_period"]
    )

    bollinger_std = float(
        config["bollinger_std"]
    )

    percentile_window = int(
        config[
            "compression_percentile_window"
        ]
    )

    result["ema_fast"] = ema(
        result["close"],
        fast_period,
    )

    result["ema_slow"] = ema(
        result["close"],
        slow_period,
    )

    result["ema_context"] = ema(
        result["close"],
        context_period,
    )

    result["atr"] = atr(
        result,
        atr_period,
    )

    (
        result["plus_di"],
        result["minus_di"],
        result["adx"],
    ) = directional_movement(
        result,
        adx_period,
    )

    result["rsi"] = rsi(
        result["close"],
        rsi_period,
    )

    (
        result["bollinger_upper"],
        result["bollinger_middle"],
        result["bollinger_lower"],
        result["bollinger_width"],
    ) = bollinger_bands(
        result["close"],
        bollinger_period,
        bollinger_std,
    )

    result["atr_percentile"] = (
        rolling_percentile_rank(
            result["atr"],
            percentile_window,
        )
    )

    result[
        "bollinger_width_percentile"
    ] = rolling_percentile_rank(
        result["bollinger_width"],
        percentile_window,
    )

    result["price_slope"] = (
        normalized_slope(
            result["close"],
            20,
        )
    )

    volume_average = (
        result["volume"]
        .rolling(
            20,
            min_periods=5,
        )
        .mean()
    )

    result["volume_ratio"] = (
        result["volume"]
        / volume_average.replace(
            0,
            np.nan,
        )
    )

    return result
