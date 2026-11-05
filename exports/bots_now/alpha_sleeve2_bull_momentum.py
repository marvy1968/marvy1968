#!/usr/bin/env python3

import os
import time
import json
import sys
import datetime
import traceback
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alpaca_trade_api.rest import REST, TimeFrame


# ============================================================
# ALPHA SLEEVE #2
# BULL MOMENTUM CONTINUATION
# PAPER VALIDATION ENGINE
# ============================================================

API_KEY = (
    os.getenv("ALPACA_API_KEY")
    or os.getenv("APCA_API_KEY_ID")
)

API_SECRET = (
    os.getenv("ALPACA_SECRET_KEY")
    or os.getenv("APCA_API_SECRET_KEY")
)

BASE_URL = os.getenv(
    "APCA_API_BASE_URL",
    "https://paper-api.alpaca.markets",
)

api = REST(
    API_KEY,
    API_SECRET,
    BASE_URL,
)

NY_TZ = ZoneInfo("America/New_York")

SESSION_START = datetime.time(10, 0)
SESSION_END = datetime.time(15, 50)

BASE_RISK_PCT = 0.005
MAX_POSITIONS = 3
MAX_PORTFOLIO_RISK_PCT = 0.022
MAX_SECTOR_RISK_PCT = 0.016

CACHE_SECONDS = 3600
SCAN_SECONDS = 60

STATE_FILE = (
    "/home/moonrider1968/"
    "alpha_sleeve2_bull_state.json"
)

CACHE_FILE = (
    "/home/moonrider1968/"
    "alpha_sleeve2_stage_cache.json"
)


# ============================================================
# 4-OF-6 SECTOR LEADER UNIVERSE
# ============================================================

SECTOR_UNIVERSE = {

    "XLK": [
        "AAPL",
        "MSFT",
        "NVDA",
        "AVGO",
        "AMD",
        "CRM",
    ],

    "XLF": [
        "JPM",
        "BAC",
        "WFC",
        "GS",
        "MS",
        "AXP",
    ],

    "XLE": [
        "XOM",
        "CVX",
        "COP",
        "SLB",
        "EOG",
        "MPC",
    ],

    "XLY": [
        "AMZN",
        "TSLA",
        "HD",
        "MCD",
        "BKNG",
        "TJX",
    ],

    "XLI": [
        "GE",
        "CAT",
        "UNP",
        "HON",
        "RTX",
        "ETN",
    ],

    "XLV": [
        "LLY",
        "UNH",
        "JNJ",
        "ABBV",
        "MRK",
        "TMO",
    ],

    "XLP": [
        "PG",
        "COST",
        "KO",
        "PEP",
        "WMT",
        "PM",
    ],

    "XLC": [
        "META",
        "GOOGL",
        "NFLX",
        "DIS",
        "TMUS",
        "CMCSA",
    ],

    "XLB": [
        "LIN",
        "APD",
        "SHW",
        "ECL",
        "FCX",
        "NEM",
    ],

    "XLU": [
        "NEE",
        "SO",
        "DUK",
        "CEG",
        "SRE",
        "AEP",
    ],

    "XLRE": [
        "PLD",
        "AMT",
        "EQIX",
        "PSA",
        "O",
        "DLR",
    ],
}


TICKER_SECTOR = {

    symbol: sector

    for sector, symbols
    in SECTOR_UNIVERSE.items()

    for symbol
    in symbols
}


# ============================================================
# JSON
# ============================================================

def load_json(path):

    if not os.path.exists(path):
        return {}

    try:

        with open(path, "r") as f:
            return json.load(f)

    except Exception:
        return {}


def save_json(path, data):

    tmp = path + ".tmp"

    with open(tmp, "w") as f:

        json.dump(
            data,
            f,
            indent=2,
            default=str,
        )

    os.replace(
        tmp,
        path,
    )


# ============================================================
# DATA
# ============================================================

def get_bars(
    symbol,
    timeframe,
    limit,
):

    try:

        df = api.get_bars(

            symbol,
            timeframe,

            limit=limit,

            adjustment="raw",

        ).df

        if (
            df is None
            or df.empty
        ):

            return None

        if df.index.tz is None:

            df.index = (
                df.index
                .tz_localize("UTC")
                .tz_convert(NY_TZ)
            )

        else:

            df.index = (
                df.index
                .tz_convert(NY_TZ)
            )

        return df.copy()

    except Exception as exc:

        print(
            f"DATA ERROR {symbol}: {exc}"
        )

        return None


# ============================================================
# INDICATORS
# ============================================================

def true_range(df):

    prev = df["close"].shift(1)

    return pd.concat(

        [

            df["high"]
            - df["low"],

            (
                df["high"]
                - prev
            ).abs(),

            (
                df["low"]
                - prev
            ).abs(),

        ],

        axis=1,

    ).max(axis=1)


def atr(df, n=14):

    return (
        true_range(df)
        .rolling(n)
        .mean()
    )


def adx_di(df, n=14):

    h = df["high"]
    l = df["low"]

    tr = true_range(df)

    atr_n = (
        tr
        .rolling(n)
        .mean()
    )

    up = h.diff()

    down = (
        l.shift(1)
        - l
    )

    pos_dm = pd.Series(

        np.where(

            (up > down)
            & (up > 0),

            up,

            0.0,

        ),

        index=df.index,
    )

    neg_dm = pd.Series(

        np.where(

            (down > up)
            & (down > 0),

            down,

            0.0,

        ),

        index=df.index,
    )

    pos_di = (

        100

        * pos_dm
        .rolling(n)
        .mean()

        / (
            atr_n
            + 1e-9
        )
    )

    neg_di = (

        100

        * neg_dm
        .rolling(n)
        .mean()

        / (
            atr_n
            + 1e-9
        )
    )

    dx = (

        100

        * (
            pos_di
            - neg_di
        ).abs()

        / (
            pos_di
            + neg_di
            + 1e-9
        )
    )

    adx = (
        dx
        .rolling(n)
        .mean()
    )

    return (
        adx,
        pos_di,
        neg_di,
    )


def rth_vwap(df):

    rth = (

        df
        .between_time(
            "09:30",
            "16:00",
        )
        .copy()
    )

    result = pd.Series(

        index=df.index,

        dtype=float,
    )

    if rth.empty:
        return result

    session_date = (
        pd.Series(
            rth.index.date,
            index=rth.index,
        )
    )

    typical = (

        rth["high"]

        + rth["low"]

        + rth["close"]

    ) / 3.0

    pv = (

        typical

        * rth["volume"]
    )

    cumulative_pv = (

        pv
        .groupby(
            session_date
        )
        .cumsum()
    )

    cumulative_volume = (

        rth["volume"]
        .groupby(
            session_date
        )
        .cumsum()
    )

    result.loc[
        rth.index
    ] = (

        cumulative_pv

        / (
            cumulative_volume
            + 1e-9
        )
    )

    return result


# ============================================================
# MACRO PERMISSION
#
# SPY Daily Close > EMA20 > SMA50
# ============================================================

def macro_bull_permission():

    df = get_bars(
        "SPY",
        TimeFrame.Day,
        100,
    )

    if (
        df is None
        or len(df) < 55
    ):

        return False

    ema20 = (

        df["close"]
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    sma50 = (

        df["close"]
        .rolling(50)
        .mean()
    )

    close = float(
        df["close"]
        .iloc[-1]
    )

    e20 = float(
        ema20.iloc[-1]
    )

    s50 = float(
        sma50.iloc[-1]
    )

    allowed = (

        close > e20

        and e20 > s50
    )

    print(
        f"MACRO | "
        f"SPY={close:.2f} "
        f"EMA20={e20:.2f} "
        f"SMA50={s50:.2f} "
        f"PERMISSION={allowed}"
    )

    return allowed


# ============================================================
# TRUE WEINSTEIN STAGE 2
#
# Price > 30-week SMA
# SMA rising
# Price >= 95% of previous 20-week high
# ============================================================

def stage2(symbol):

    df = get_bars(

        symbol,
        TimeFrame.Week,
        60,
    )

    if (
        df is None
        or len(df) < 35
    ):

        return False

    sma30 = (

        df["close"]
        .rolling(30)
        .mean()
    )

    slope = (

        sma30
        .diff(3)
    )

    close = float(
        df["close"]
        .iloc[-1]
    )

    sma = float(
        sma30.iloc[-1]
    )

    slp = float(
        slope.iloc[-1]
    )

    resistance = float(

        df["high"]
        .iloc[-21:-1]
        .max()
    )

    if not all(
        np.isfinite(
            [
                close,
                sma,
                slp,
                resistance,
            ]
        )
    ):

        return False

    return bool(

        close > sma

        and slp > 0

        and close
        >= (
            0.95
            * resistance
        )
    )


# ============================================================
# CACHED 4/6 SECTOR BREADTH
# ============================================================

def refresh_stage_cache():

    prior = load_json(
        CACHE_FILE
    )

    old_sectors = (
        prior.get(
            "sectors",
            {}
        )
    )

    stage_map = {}

    sectors = {}

    print(
        "\nREFRESHING "
        "BULL STAGE-2 CACHE"
    )

    for sector, symbols in (
        SECTOR_UNIVERSE.items()
    ):

        count = 0

        for symbol in symbols:

            status = stage2(
                symbol
            )

            stage_map[
                symbol
            ] = status

            count += int(
                status
            )

            time.sleep(
                0.04
            )

        old_count = (

            old_sectors
            .get(
                sector,
                {}
            )
            .get(
                "count",
                count,
            )
        )

        trajectory = (

            "EXPANDING"

            if count > old_count

            else "CONTRACTING"

            if count < old_count

            else "STABLE"
        )

        sectors[
            sector
        ] = {

            "count":
                count,

            "qualified":
                count >= 4,

            "trajectory":
                trajectory,
        }

        print(

            f"{sector} | "
            f"{count}/6 | "
            f"{trajectory}"
        )

    cache = {

        "timestamp":
            time.time(),

        "stage_map":
            stage_map,

        "sectors":
            sectors,
    }

    save_json(
        CACHE_FILE,
        cache,
    )

    return cache


def market_structure(
    force=False,
):

    cache = load_json(
        CACHE_FILE
    )

    valid = (

        cache.get(
            "stage_map"
        )

        and cache.get(
            "sectors"
        )

        and (
            time.time()

            - cache.get(
                "timestamp",
                0,
            )
        )
        < CACHE_SECONDS
    )

    if (
        valid
        and not force
    ):

        return cache

    return refresh_stage_cache()


# ============================================================
# INTRADAY BREAKOUT DATA
# ============================================================

def intraday_frame(symbol):

    df = get_bars(

        symbol,

        TimeFrame.Minute * 15,

        220,
    )

    if (
        df is None
        or len(df) < 80
    ):

        return None

    df["ema9"] = (

        df["close"]
        .ewm(
            span=9,
            adjust=False,
        )
        .mean()
    )

    df["ema21"] = (

        df["close"]
        .ewm(
            span=21,
            adjust=False,
        )
        .mean()
    )

    df["bb_mid"] = (

        df["close"]
        .rolling(20)
        .mean()
    )

    df["bb_std"] = (

        df["close"]
        .rolling(20)
        .std()
    )

    df["bb_upper"] = (

        df["bb_mid"]

        + (
            2.0
            * df["bb_std"]
        )
    )

    df["atr"] = atr(
        df,
        14,
    )

    df["vol_sma20"] = (

        df["volume"]
        .rolling(20)
        .mean()
    )

    df["vwap"] = (
        rth_vwap(
            df
        )
    )

    (
        df["adx"],
        df["pos_di"],
        df["neg_di"],

    ) = adx_di(
        df,
        14,
    )

    # LOOKAHEAD SAFE
    #
    # Current bar must break ABOVE
    # prior 20-bar high.

    df["donchian20"] = (

        df["high"]
        .rolling(20)
        .max()
        .shift(1)
    )

    df["swing_low10"] = (

        df["low"]
        .rolling(10)
        .min()
        .shift(1)
    )

    return df


# ============================================================
# ORDER / STOP HELPERS
# ============================================================

def nested_parent(
    parent_id,
):

    try:

        return api.get_order(
            parent_id,
            nested=True,
        )

    except TypeError:

        return api.get_order(
            parent_id
        )


def discover_stop_leg(
    parent,
):

    legs = (

        getattr(
            parent,
            "legs",
            None,
        )

        or []
    )

    for leg in legs:

        order_type = (

            getattr(
                leg,
                "order_type",
                None,
            )

            or getattr(
                leg,
                "type",
                None,
            )
        )

        if (
            str(
                order_type
            ).lower()
            == "stop"
        ):

            return str(
                leg.id
            )

    return None


def sync_orders(
    positions,
    state,
):

    changed = False

    for symbol in list(
        state.keys()
    ):

        if symbol not in positions:
            continue

        parent_id = (
            state[symbol]
            .get(
                "parent_order_id"
            )
        )

        if not parent_id:
            continue

        try:

            parent = nested_parent(
                parent_id
            )

            fill = getattr(
                parent,
                "filled_avg_price",
                None,
            )

            if (
                fill is not None

                and not state[
                    symbol
                ].get(
                    "fill_synced"
                )
            ):

                actual_fill = float(
                    fill
                )

                stop = float(

                    state[
                        symbol
                    ][
                        "initial_sl"
                    ]
                )

                state[
                    symbol
                ][
                    "actual_fill_px"
                ] = actual_fill

                state[
                    symbol
                ][
                    "initial_risk"
                ] = max(

                    actual_fill
                    - stop,

                    0.01,
                )

                state[
                    symbol
                ][
                    "fill_synced"
                ] = True

                changed = True

            stop_id = (
                discover_stop_leg(
                    parent
                )
            )

            if stop_id:

                state[
                    symbol
                ][
                    "child_stop_id"
                ] = stop_id

                changed = True

        except Exception as exc:

            print(
                f"SYNC WARNING "
                f"{symbol}: "
                f"{exc}"
            )

    if changed:

        save_json(
            STATE_FILE,
            state,
        )


def replace_stop(
    symbol,
    new_stop,
    state,
):

    stop_id = (

        state[
            symbol
        ]
        .get(
            "child_stop_id"
        )
    )

    if not stop_id:

        print(
            f"CRITICAL {symbol}: "
            f"no child stop ID."
        )

        return False

    try:

        replacement = (

            api.replace_order(

                order_id=stop_id,

                stop_price=round(
                    float(
                        new_stop
                    ),
                    2,
                ),
            )
        )

        state[
            symbol
        ][
            "child_stop_id"
        ] = str(

            getattr(
                replacement,
                "id",
                stop_id,
            )
        )

        state[
            symbol
        ][
            "broker_sl"
        ] = round(
            float(
                new_stop
            ),
            2,
        )

        print(

            f"RATCHET {symbol}: "
            f"broker stop "
            f"${new_stop:.2f}"
        )

        return True

    except Exception as exc:

        # Never cancel the existing stop.

        print(

            f"CRITICAL {symbol}: "
            f"stop replacement failed. "
            f"Old stop remains active. "
            f"{exc}"
        )

        return False


# ============================================================
# MANAGE OPEN RUNNERS
# ============================================================

def manage_positions(
    positions,
    state,
):

    for symbol, position in (
        positions.items()
    ):

        if symbol not in state:
            continue

        meta = state[
            symbol
        ]

        entry = float(

            meta.get(
                "actual_fill_px"
            )

            or meta[
                "signal_entry_px"
            ]
        )

        initial_risk = float(

            meta.get(
                "initial_risk",
                0,
            )
        )

        if initial_risk <= 0:
            continue

        df = intraday_frame(
            symbol
        )

        if df is None:
            continue

        row = df.iloc[-1]

        current = float(
            position.current_price
        )

        atr_value = float(
            row["atr"]
        )

        ema21 = float(
            row["ema21"]
        )

        old_stop = float(

            meta.get(
                "broker_sl",
                meta["initial_sl"],
            )
        )

        tier = int(
            meta.get(
                "tier",
                0,
            )
        )

        mfe_r = (

            current
            - entry

        ) / (

            initial_risk
            + 1e-9
        )

        # +1.8R:
        # snap to BE + .05 ATR

        if (
            mfe_r >= 1.8
            and tier < 1
        ):

            new_stop = max(

                old_stop,

                entry
                + (
                    0.05
                    * atr_value
                ),
            )

            if replace_stop(

                symbol,
                new_stop,
                state,

            ):

                meta[
                    "tier"
                ] = 1

                tier = 1

                old_stop = new_stop

        # +2.8R:
        # runner mode

        if (
            mfe_r >= 2.8
            and tier < 2
        ):

            new_stop = max(

                old_stop,

                ema21
                - (
                    1.2
                    * atr_value
                ),
            )

            if replace_stop(

                symbol,
                new_stop,
                state,

            ):

                meta[
                    "tier"
                ] = 2

                tier = 2

                old_stop = new_stop

        elif tier == 2:

            new_stop = max(

                old_stop,

                ema21
                - (
                    1.2
                    * atr_value
                ),
            )

            if (
                new_stop
                > old_stop
                + 0.05
            ):

                replace_stop(

                    symbol,
                    new_stop,
                    state,
                )

    save_json(
        STATE_FILE,
        state,
    )


# ============================================================
# OPEN RISK
# ============================================================

def open_risk(
    positions,
    state,
):

    total = 0.0

    sector_risk = {}

    for symbol, position in (
        positions.items()
    ):

        if symbol not in state:
            continue

        meta = state[
            symbol
        ]

        entry = float(

            meta.get(
                "actual_fill_px"
            )

            or meta[
                "signal_entry_px"
            ]
        )

        stop = float(

            meta.get(
                "broker_sl",
                meta["initial_sl"],
            )
        )

        quantity = abs(
            float(
                position.qty
            )
        )

        risk = (

            quantity

            * max(
                0.0,
                entry
                - stop,
            )
        )

        total += risk

        sector = meta[
            "sector"
        ]

        sector_risk[
            sector
        ] = (

            sector_risk.get(
                sector,
                0.0,
            )

            + risk
        )

    return (
        total,
        sector_risk,
    )


# ============================================================
# ENTRY
# ============================================================

def submit_breakout(

    symbol,
    sector,
    row,
    nav,
    remaining_total,
    remaining_sector,
    state,
):

    entry = float(
        row["close"]
    )

    atr_value = float(
        row["atr"]
    )

    swing_stop = float(
        row["swing_low10"]
    )

    ema_stop = (

        float(
            row["ema21"]
        )

        - (
            1.2
            * atr_value
        )
    )

    # Use the tighter valid structural stop.

    initial_stop = max(

        swing_stop,

        ema_stop,
    )

    if (
        initial_stop >= entry
    ):

        initial_stop = (

            entry

            - (
                1.2
                * atr_value
            )
        )

    risk_per_share = (

        entry
        - initial_stop
    )

    if risk_per_share <= 0:
        return False

    base_risk = (

        nav
        * BASE_RISK_PCT
    )

    allowed_risk = min(

        base_risk,
        remaining_total,
        remaining_sector,
    )

    qty = int(

        allowed_risk

        / risk_per_share
    )

    if qty <= 0:
        return False

    print(
        "\n"
        "============================================================"
    )

    print(
        f"BULL MOMENTUM BREAKOUT "
        f"{symbol} / {sector}"
    )

    print(
        f"Entry Signal : ${entry:.2f}"
    )

    print(
        f"Initial Stop : ${initial_stop:.2f}"
    )

    print(
        f"Qty          : {qty}"
    )

    print(
        f"Risk         : "
        f"${qty * risk_per_share:.2f}"
    )

    print(
        "============================================================"
    )

    try:

        order = api.submit_order(

            symbol=symbol,

            qty=qty,

            side="buy",

            type="market",

            # GTC because runner may
            # remain across sessions.

            time_in_force="gtc",

            order_class="oto",

            stop_loss={

                "stop_price":
                    round(
                        initial_stop,
                        2,
                    )
            },
        )

        state[
            symbol
        ] = {

            "sleeve":
                "BULL_MOMENTUM",

            "side":
                "LONG",

            "sector":
                sector,

            "signal_entry_px":
                entry,

            "actual_fill_px":
                None,

            "initial_sl":
                initial_stop,

            "broker_sl":
                initial_stop,

            "initial_risk":
                risk_per_share,

            "qty":
                qty,

            "tier":
                0,

            "parent_order_id":
                str(
                    order.id
                ),

            "child_stop_id":
                None,

            "fill_synced":
                False,

            "opened_at":
                str(
                    datetime.datetime.now(
                        NY_TZ
                    )
                ),
        }

        save_json(
            STATE_FILE,
            state,
        )

        print(
            f"ORDER SUBMITTED "
            f"{symbol} "
            f"Parent={order.id}"
        )

        return True

    except Exception as exc:

        print(
            f"ORDER ERROR "
            f"{symbol}: "
            f"{exc}"
        )

        return False


# ============================================================
# SCAN
# ============================================================

def scan_cycle():

    now = datetime.datetime.now(
        NY_TZ
    )

    clock = api.get_clock()

    positions = {

        p.symbol: p

        for p in api.list_positions()
    }

    state = load_json(
        STATE_FILE
    )

    # Existing runners are managed
    # even before 10:00.

    sync_orders(
        positions,
        state,
    )

    state = load_json(
        STATE_FILE
    )

    manage_positions(
        positions,
        state,
    )

    state = load_json(
        STATE_FILE
    )

    # Clean closed positions.

    stale = [

        symbol

        for symbol
        in state

        if symbol
        not in positions
    ]

    for symbol in stale:

        state.pop(
            symbol,
            None,
        )

    if stale:

        save_json(
            STATE_FILE,
            state,
        )

    # No new entries outside
    # 10:00-15:50 ET.

    if not clock.is_open:
        return

    if not (

        SESSION_START
        <= now.time()
        < SESSION_END

    ):

        return

    # Macro gate.

    if not macro_bull_permission():

        print(
            "BULL SLEEVE BLOCKED: "
            "macro permission false."
        )

        return

    if (
        len(positions)
        >= MAX_POSITIONS
    ):

        return

    account = api.get_account()

    nav = float(
        account.equity
    )

    portfolio_cap = (

        nav
        * MAX_PORTFOLIO_RISK_PCT
    )

    sector_cap = (

        nav
        * MAX_SECTOR_RISK_PCT
    )

    current_risk, sector_risk = (

        open_risk(
            positions,
            state,
        )
    )

    remaining_total = (

        portfolio_cap

        - current_risk
    )

    if remaining_total <= 0:
        return

    structure = market_structure()

    stage_map = (
        structure[
            "stage_map"
        ]
    )

    sectors = (
        structure[
            "sectors"
        ]
    )

    for sector, symbols in (
        SECTOR_UNIVERSE.items()
    ):

        sector_meta = (
            sectors[
                sector
            ]
        )

        # 4/6 gate.

        if not sector_meta[
            "qualified"
        ]:

            continue

        remaining_sector = (

            sector_cap

            - sector_risk.get(
                sector,
                0.0,
            )
        )

        if remaining_sector <= 0:
            continue

        for symbol in symbols:

            if symbol in positions:
                continue

            # Individual Weinstein Stage 2.

            if not stage_map.get(
                symbol,
                False,
            ):

                continue

            df = intraday_frame(
                symbol
            )

            if df is None:
                continue

            row = df.iloc[-1]

            prev = df.iloc[-2]

            required = [

                row["ema9"],
                row["ema21"],
                row["bb_mid"],
                row["bb_upper"],
                row["atr"],
                row["vol_sma20"],
                row["vwap"],
                row["adx"],
                row["pos_di"],
                row["neg_di"],
                row["donchian20"],
                row["swing_low10"],
            ]

            if any(

                pd.isna(x)

                for x
                in required
            ):

                continue

            # ==================================================
            # DONCHIAN BREAKOUT
            #
            # Current close breaks prior
            # rolling 20-bar high.
            # ==================================================

            breakout = (

                row["close"]
                > row["donchian20"]

                and prev["close"]
                <= prev["donchian20"]
            )

            # Relative volume expansion.

            volume_expansion = (

                row["volume"]

                > (
                    1.25

                    * row[
                        "vol_sma20"
                    ]
                )
            )

            # Momentum alignment.

            momentum = (

                row["ema9"]
                > row["ema21"]
                > row["bb_mid"]

                and row["adx"]
                >= 20

                and row["pos_di"]
                > row["neg_di"]

                and row["close"]
                >= row["vwap"]
            )

            if not (

                breakout

                and volume_expansion

                and momentum

            ):

                continue

            print(

                f"QUALIFIED BREAKOUT | "
                f"{symbol} | "
                f"{sector} "
                f"{sector_meta['count']}/6 | "
                f"ADX={row['adx']:.1f} | "
                f"RVOL="
                f"{row['volume']/row['vol_sma20']:.2f}"
            )

            if submit_breakout(

                symbol=symbol,

                sector=sector,

                row=row,

                nav=nav,

                remaining_total=
                    remaining_total,

                remaining_sector=
                    remaining_sector,

                state=state,
            ):

                # Only one new entry
                # per scan cycle.

                return


# ============================================================
# STARTUP
# ============================================================

def startup_checks():

    print(
        "============================================================"
    )

    print(
        "ALPHA SLEEVE #2"
    )

    print(
        "BULL MOMENTUM CONTINUATION"
    )

    print(
        "PAPER VALIDATION"
    )

    print(
        "============================================================"
    )

    if (
        not API_KEY
        or not API_SECRET
    ):

        print(
            "FAIL: Alpaca credentials missing."
        )

        return False

    if (
        "paper-api.alpaca.markets"
        not in BASE_URL
    ):

        print(
            f"FAIL: not paper endpoint: "
            f"{BASE_URL}"
        )

        return False

    try:

        account = api.get_account()

        clock = api.get_clock()

        print(
            f"Account: "
            f"{account.status}"
        )

        print(
            f"Equity: "
            f"${float(account.equity):,.2f}"
        )

        print(
            f"Market Open: "
            f"{clock.is_open}"
        )

        print(
            f"Timestamp: "
            f"{clock.timestamp}"
        )

    except Exception as exc:

        print(
            f"FAIL API: "
            f"{exc}"
        )

        return False

    universe_size = sum(

        len(symbols)

        for symbols
        in SECTOR_UNIVERSE.values()
    )

    print(
        f"Universe: "
        f"{universe_size} stocks"
    )

    print(
        f"Sectors: "
        f"{len(SECTOR_UNIVERSE)}"
    )

    if universe_size != 66:

        print(
            "FAIL: expected "
            "11 sectors x 6 = 66."
        )

        return False

    print(
        "PAPER MODE CONFIRMED"
    )

    print(
        "STARTUP_CHECKS=PASS"
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    if not startup_checks():

        sys.exit(1)

    # Warm weekly Stage/Breadth cache.

    market_structure(
        force=True
    )

    print(
        "\n"
        "Alpha Sleeve #2 running."
    )

    print(
        "09:30-10:00 ET: "
        "analysis / runner management"
    )

    print(
        "10:00-15:50 ET: "
        "new entries permitted"
    )

    print(
        "15:50+: "
        "no new entries"
    )

    print(
        "Existing +2.8R runners "
        "may remain across sessions."
    )

    while True:

        try:

            scan_cycle()

            time.sleep(
                SCAN_SECONDS
            )

        except KeyboardInterrupt:

            print(
                "Stopped manually."
            )

            break

        except Exception as exc:

            print(
                f"DAEMON ERROR: "
                f"{exc}"
            )

            traceback.print_exc()

            time.sleep(
                SCAN_SECONDS
            )


if __name__ == "__main__":

    main()
