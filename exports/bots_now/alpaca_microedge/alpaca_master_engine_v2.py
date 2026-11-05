#!/usr/bin/env python3


# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import os
import time
import json
import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Set

import pandas as pd
import pytz
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

NY = pytz.timezone("America/New_York")

DB_FILE = os.getenv("DB_FILE", "alpaca_master_top3.db")
STATE_FILE = os.getenv("STATE_FILE", "alpaca_master_top3_state.json")

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_SECRET_KEY")
BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

if not API_KEY or not API_SECRET or not BASE_URL:
    raise RuntimeError("Missing Alpaca environment variables. Check .env")

# ------------------------
# CORE SETTINGS
# ------------------------
BASE_RISK = float(os.getenv("BASE_RISK", "0.002"))
SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "15"))
COOLDOWN_TREND = int(os.getenv("COOLDOWN_TREND", "180"))
COOLDOWN_RANGE = int(os.getenv("COOLDOWN_RANGE", "90"))
COOLDOWN_MICRO = int(os.getenv("COOLDOWN_MICRO", "60"))
EXIT_COOLDOWN = int(os.getenv("EXIT_COOLDOWN", "15"))
MAX_OPEN = int(os.getenv("MAX_OPEN", "10"))
MAX_NEW_PER_SCAN = int(os.getenv("MAX_NEW_PER_SCAN", "2"))
MAX_NOTIONAL_PER_TRADE = float(os.getenv("MAX_NOTIONAL_PER_TRADE", "25000"))
FETCH_SLEEP_SECONDS = float(os.getenv("FETCH_SLEEP_SECONDS", "0.35"))

# ------------------------
# TREND EXITS
# ------------------------
STOP_TREND_MIN = float(os.getenv("STOP_TREND_MIN", "0.0025"))
STOP_TREND_MAX = float(os.getenv("STOP_TREND_MAX", "0.0120"))
TRAIL_ACTIVATION = float(os.getenv("TRAIL_ACTIVATION", "0.0030"))
TRAIL_PCT = float(os.getenv("TRAIL_PCT", "0.0015"))
CEILING_PCT = float(os.getenv("CEILING_PCT", "0.0150"))

# ------------------------
# RANGE EXITS
# ------------------------
STOP_RANGE_MIN = float(os.getenv("STOP_RANGE_MIN", "0.0015"))
STOP_RANGE_MAX = float(os.getenv("STOP_RANGE_MAX", "0.0075"))
TP_RANGE = float(os.getenv("TP_RANGE", "0.0022"))
MID_EXIT_RANGE = float(os.getenv("MID_EXIT_RANGE", "0.0012"))

# ------------------------
# MICRO EXITS
# ------------------------
STOP_MICRO_MIN = float(os.getenv("STOP_MICRO_MIN", "0.0012"))
STOP_MICRO_MAX = float(os.getenv("STOP_MICRO_MAX", "0.0040"))
TP_MICRO = float(os.getenv("TP_MICRO", "0.0032"))
MICRO_TRAIL_ACTIVATION = float(os.getenv("MICRO_TRAIL_ACTIVATION", "0.0020"))
MICRO_TRAIL_PCT = float(os.getenv("MICRO_TRAIL_PCT", "0.0010"))

# ------------------------
# FILTERS
# ------------------------
SPREAD_LIMIT_TREND = float(os.getenv("SPREAD_LIMIT_TREND", "0.0025"))
SPREAD_LIMIT_RANGE = float(os.getenv("SPREAD_LIMIT_RANGE", "0.0015"))
SPREAD_LIMIT_MICRO = float(os.getenv("SPREAD_LIMIT_MICRO", "0.0012"))

RVOL_TREND_MIN = float(os.getenv("RVOL_TREND_MIN", "1.10"))
RVOL_RANGE_MIN = float(os.getenv("RVOL_RANGE_MIN", "0.85"))
RVOL_MICRO_MIN = float(os.getenv("RVOL_MICRO_MIN", "1.20"))

CHOP_SCORE_MIN = float(os.getenv("CHOP_SCORE_MIN", "0.58"))
ATR_PCT_MAX_RANGE = float(os.getenv("ATR_PCT_MAX_RANGE", "0.0100"))
RANGE_LOOKBACK = int(os.getenv("RANGE_LOOKBACK", "30"))
RANGE_TOUCH_TOL = float(os.getenv("RANGE_TOUCH_TOL", "0.0015"))
MIN_RANGE_WIDTH_PCT = float(os.getenv("MIN_RANGE_WIDTH_PCT", "0.0040"))
MAX_RANGE_WIDTH_PCT = float(os.getenv("MAX_RANGE_WIDTH_PCT", "0.0150"))
MID_DEAD_ZONE_PCT = float(os.getenv("MID_DEAD_ZONE_PCT", "0.0015"))
BREAKOUT_VOLUME_MULT = float(os.getenv("BREAKOUT_VOLUME_MULT", "1.60"))
VWAP_DEV_LIMIT = float(os.getenv("VWAP_DEV_LIMIT", "0.0080"))

EMA_FAST = int(os.getenv("EMA_FAST", "20"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "200"))

SYMBOL_STATS_TRADES = int(os.getenv("SYMBOL_STATS_TRADES", "30"))
SYMBOL_WEIGHT_MAX_BOOST = float(os.getenv("SYMBOL_WEIGHT_MAX_BOOST", "1.25"))
SYMBOL_WEIGHT_MIN_BOOST = float(os.getenv("SYMBOL_WEIGHT_MIN_BOOST", "0.75"))

# Reduced universe for stability
SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META",
    "TSLA", "AMD", "SPY", "QQQ", "JPM",
    "BAC", "NFLX", "AVGO", "MU", "PLTR",
]


@dataclass
class Signal:
    symbol: str
    side: str
    regime: str
    trigger: str
    price: float
    stop_pct: float
    spread_limit: float
    heat: float = 0.0


class AlpacaMasterTop3:
    def __init__(self):
        self.api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version="v2")
        self.last_trade: Dict[str, float] = {}
        self.trade_peaks: Dict[str, float] = {}
        self.last_exit_attempt: Dict[str, float] = {}
        self.entry_meta: Dict[str, Dict] = {}
        self.init_db()
        self.load_state()

    # ------------------------
    # DB / STATE
    # ------------------------
    def init_db(self):
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    side TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    pnl REAL,
                    peak_pnl REAL,
                    regime_vote TEXT,
                    trigger_used TEXT,
                    exit_reason TEXT,
                    timestamp TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    symbol TEXT,
                    event TEXT,
                    detail TEXT
                )
                """
            )
            conn.commit()

    def log_event(self, symbol: str, event: str, detail: str):
        try:
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute(
                    "INSERT INTO events (ts, symbol, event, detail) VALUES (?, ?, ?, ?)",
                    (datetime.now(NY).isoformat(), symbol, event, detail),
                )
                conn.commit()
        except Exception as e:
            logging.error(f"DB EVENT ERR {symbol}: {e}")

    def save_state(self):
        payload = {
            "last_trade": self.last_trade,
            "trade_peaks": self.trade_peaks,
            "last_exit_attempt": self.last_exit_attempt,
            "entry_meta": self.entry_meta,
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.last_trade = payload.get("last_trade", {})
            self.trade_peaks = payload.get("trade_peaks", {})
            self.last_exit_attempt = payload.get("last_exit_attempt", {})
            self.entry_meta = payload.get("entry_meta", {})
        except Exception as e:
            logging.error(f"STATE LOAD ERR: {e}")

    # ------------------------
    # API HELPERS
    # ------------------------
    def market_open(self) -> bool:
        try:
            return self.api.get_clock().is_open
        except Exception as e:
            logging.error(f"CLOCK ERR: {e}")
            return False

    def fetch_symbol_bars(self, sym: str) -> Optional[pd.DataFrame]:
        now_utc = datetime.now(timezone.utc)
        start_utc = now_utc - timedelta(minutes=260)
        try:
            bars = self.api.get_bars(
                sym,
                tradeapi.TimeFrame.Minute,
                start=start_utc.isoformat(),
                end=now_utc.isoformat(),
                adjustment="raw",
                feed="iex",
            ).df
            if bars is None or bars.empty:
                return None
            bars = bars.copy()
            bars.index.name = "timestamp"
            return bars
        except Exception as e:
            logging.warning(f"DATA FETCH ERR {sym}: {e}")
            return None

    def fetch_batch_data(self) -> Optional[Dict[str, pd.DataFrame]]:
        out: Dict[str, pd.DataFrame] = {}
        for sym in SYMBOLS:
            bars = self.fetch_symbol_bars(sym)
            if bars is not None and not bars.empty:
                out[sym] = bars
            time.sleep(FETCH_SLEEP_SECONDS)
        return out if out else None

    def latest_quote(self, symbol: str):
        try:
            return self.api.get_latest_quote(symbol)
        except Exception as e:
            logging.error(f"LATEST QUOTE ERR {symbol}: {e}")
            return None

    def open_orders(self) -> List:
        try:
            return self.api.list_orders(status="open")
        except Exception as e:
            logging.error(f"LIST OPEN ORDERS ERR: {e}")
            return []

    def open_order_symbols(self) -> Set[str]:
        return {o.symbol for o in self.open_orders()}

    def positions_map(self) -> Dict[str, object]:
        out = {}
        try:
            for p in self.api.list_positions():
                out[p.symbol] = p
        except Exception as e:
            logging.error(f"LIST POS ERR: {e}")
        return out

    def cancel_symbol_orders(self, symbol: str):
        for o in self.open_orders():
            if o.symbol == symbol:
                try:
                    self.api.cancel_order(o.id)
                except Exception as e:
                    logging.error(f"CANCEL ORDER ERR {symbol}: {e}")

    # ------------------------
    # FEATURE ENGINE
    # ------------------------
    @staticmethod
    def compute_rsi(close: pd.Series, window: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=window).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=window).mean()
        loss = loss.replace(0, pd.NA)
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()

        out["rsi"] = self.compute_rsi(out["close"])

        exp1 = out["close"].ewm(span=12, adjust=False).mean()
        exp2 = out["close"].ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        out["macd_hist"] = macd - macd_signal

        sma20 = out["close"].rolling(20).mean()
        std20 = out["close"].rolling(20).std()
        out["bb_width"] = (4 * std20) / sma20

        out["ema9"] = out["close"].ewm(span=9).mean()
        out["ema21"] = out["close"].ewm(span=21).mean()
        out["ema20"] = out["close"].ewm(span=EMA_FAST).mean()
        out["ema50"] = out["close"].ewm(span=50).mean()
        out["ema200"] = out["close"].ewm(span=EMA_SLOW).mean()
        out["ema_split"] = (out["ema20"] - out["ema50"]).abs() / out["ema50"]

        out["atr"] = (out["high"] - out["low"]).rolling(14).mean()
        out["atr_pct"] = out["atr"] / out["close"]

        out["vol_avg_50"] = out["volume"].rolling(50).mean()
        out["rvol"] = out["volume"] / out["vol_avg_50"]

        tpv = (out["close"] * out["volume"]).cumsum()
        tv = out["volume"].cumsum().replace(0, pd.NA)
        out["vwap"] = tpv / tv

        return out

    def chop_score(self, df: pd.DataFrame) -> float:
        tail = df.tail(RANGE_LOOKBACK)
        if len(tail) < RANGE_LOOKBACK:
            return 0.0

        range_hi = float(tail["high"].max())
        range_lo = float(tail["low"].min())
        price = float(tail["close"].iloc[-1])
        width = range_hi - range_lo

        if price <= 0 or width <= 0:
            return 0.0

        width_pct = width / price
        atr_pct = float(tail["atr_pct"].iloc[-1]) if not pd.isna(tail["atr_pct"].iloc[-1]) else 0.0
        ema20 = float(tail["ema20"].iloc[-1])
        ema200 = float(tail["ema200"].iloc[-1])

        slope_penalty = min(abs(ema20 - ema200) / max(price, 1e-9) * 20.0, 1.0)
        atr_component = 1.0 - min(atr_pct / ATR_PCT_MAX_RANGE, 1.0)

        closes = tail["close"].tolist()
        swing_count = 0
        for i in range(2, len(closes)):
            if (closes[i - 2] < closes[i - 1] > closes[i]) or (closes[i - 2] > closes[i - 1] < closes[i]):
                swing_count += 1
        swing_component = min(swing_count / 8.0, 1.0)

        mid = (range_hi + range_lo) / 2.0
        center_bias = 1.0 - min(abs(price - mid) / max(width, 1e-9) * 2.0, 1.0)

        width_component = 0.0
        if MIN_RANGE_WIDTH_PCT <= width_pct <= MAX_RANGE_WIDTH_PCT:
            width_component = 1.0
        elif width_pct < MIN_RANGE_WIDTH_PCT:
            width_component = max(width_pct / max(MIN_RANGE_WIDTH_PCT, 1e-9), 0.0)
        else:
            width_component = max(
                1.0 - ((width_pct - MAX_RANGE_WIDTH_PCT) / max(MAX_RANGE_WIDTH_PCT, 1e-9)),
                0.0,
            )

        score = (
            0.30 * atr_component
            + 0.25 * (1.0 - slope_penalty)
            + 0.20 * swing_component
            + 0.15 * center_bias
            + 0.10 * width_component
        )

        return max(0.0, min(score, 1.0))

    def breakout_risk(self, df: pd.DataFrame) -> bool:
        if len(df) < 25:
            return False

        price = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        vwap = float(df["vwap"].iloc[-1]) if not pd.isna(df["vwap"].iloc[-1]) else price
        rvol = float(df["rvol"].iloc[-1]) if not pd.isna(df["rvol"].iloc[-1]) else 0.0
        bb_width = float(df["bb_width"].iloc[-1]) if not pd.isna(df["bb_width"].iloc[-1]) else 0.0
        bb_avg = float(df["bb_width"].rolling(20).mean().iloc[-1]) if not pd.isna(df["bb_width"].rolling(20).mean().iloc[-1]) else bb_width

        momentum_push = (
            (price > prev_close and df["close"].iloc[-2] > df["close"].iloc[-3]) or
            (price < prev_close and df["close"].iloc[-2] < df["close"].iloc[-3])
        )

        near_vwap = abs(price - vwap) / max(price, 1e-9) < 0.002
        width_expanding = bb_width > bb_avg * 1.2 if bb_avg > 0 else False
        volume_surge = rvol > BREAKOUT_VOLUME_MULT

        return volume_surge and width_expanding and momentum_push and near_vwap

    def regime_vote(self, df: pd.DataFrame) -> str:
        if len(df) < max(EMA_SLOW, 60):
            return "CHOP"

        rsi = float(df["rsi"].iloc[-1]) if not pd.isna(df["rsi"].iloc[-1]) else 50.0
        hist_curr = abs(float(df["macd_hist"].iloc[-1])) if not pd.isna(df["macd_hist"].iloc[-1]) else 0.0
        hist_prev = abs(float(df["macd_hist"].iloc[-2])) if not pd.isna(df["macd_hist"].iloc[-2]) else 0.0
        bb_width = float(df["bb_width"].iloc[-1]) if not pd.isna(df["bb_width"].iloc[-1]) else 0.0
        bb_avg = float(df["bb_width"].rolling(20).mean().iloc[-1]) if not pd.isna(df["bb_width"].rolling(20).mean().iloc[-1]) else bb_width
        ema_split = float(df["ema_split"].iloc[-1]) if not pd.isna(df["ema_split"].iloc[-1]) else 0.0

        trend_votes = sum([
            1 if (rsi > 60 or rsi < 40) else 0,
            1 if (hist_curr > hist_prev) else 0,
            1 if (bb_width > bb_avg) else 0,
            1 if (ema_split > 0.001) else 0,
        ])

        if trend_votes >= 3:
            return "TREND"

        if self.chop_score(df) >= CHOP_SCORE_MIN:
            return "RANGE"

        return "CHOP"

    # ------------------------
    # EXECUTION LAYER
    # ------------------------
    def adaptive_stop_pct(self, df: pd.DataFrame, regime: str) -> float:
        if df is None or df.empty or "atr" not in df.columns:
            if regime == "TREND":
                return STOP_TREND_MIN
            if regime == "RANGE":
                return STOP_RANGE_MIN
            return STOP_MICRO_MIN

        price = float(df["close"].iloc[-1])
        atr = float(df["atr"].iloc[-1]) if not pd.isna(df["atr"].iloc[-1]) else 0.0

        if price <= 0 or atr <= 0:
            if regime == "TREND":
                return STOP_TREND_MIN
            if regime == "RANGE":
                return STOP_RANGE_MIN
            return STOP_MICRO_MIN

        atr_pct = atr / price

        if regime == "TREND":
            raw = atr_pct * 1.5
            return min(max(raw, STOP_TREND_MIN), STOP_TREND_MAX)

        if regime == "RANGE":
            raw = atr_pct * 0.9
            return min(max(raw, STOP_RANGE_MIN), STOP_RANGE_MAX)

        raw = atr_pct * 0.7
        return min(max(raw, STOP_MICRO_MIN), STOP_MICRO_MAX)

    def quote_imbalance(self, symbol: str) -> Optional[dict]:
        try:
            q = self.latest_quote(symbol)
            if q is None:
                return None

            bid = float(q.bidprice)
            ask = float(q.askprice)
            bid_sz = float(getattr(q, "bidsize", 0) or 0)
            ask_sz = float(getattr(q, "asksize", 0) or 0)

            if bid <= 0 or ask <= 0 or ask < bid:
                return None

            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid if mid > 0 else None
            total_sz = bid_sz + ask_sz
            imbalance = 0.0 if total_sz <= 0 else (bid_sz - ask_sz) / total_sz

            return {
                "spread_pct": spread_pct,
                "imbalance": imbalance,
            }
        except Exception as e:
            logging.error(f"IMBALANCE ERR {symbol}: {e}")
            return None

    def passes_orderbook_filter(self, symbol: str, side: str, spread_limit: float) -> bool:
        info = self.quote_imbalance(symbol)
        if info is None:
            self.log_event(symbol, "SKIP", "quote_unavailable")
            return False

        spread_pct = info["spread_pct"]
        imbalance = info["imbalance"]

        if spread_pct is None or spread_pct > spread_limit:
            self.log_event(symbol, "SKIP", f"spread_too_wide:{spread_pct}")
            return False

        if side == "BUY" and imbalance < -0.20:
            self.log_event(symbol, "SKIP", f"ask_pressure:{imbalance:.3f}")
            return False

        if side == "SELL" and imbalance > 0.20:
            self.log_event(symbol, "SKIP", f"bid_pressure:{imbalance:.3f}")
            return False

        return True

    def macro_volatility_state(self, bars_map) -> dict:
        try:
            if not bars_map or "SPY" not in bars_map:
                return {"state": "UNKNOWN", "risk_mult": 0.5, "pause_entries": False}

            spy = bars_map["SPY"].copy()
            if len(spy) < 30:
                return {"state": "UNKNOWN", "risk_mult": 0.5, "pause_entries": False}

            close = spy["close"]
            ret_5 = abs((close.iloc[-1] / close.iloc[-6]) - 1.0) if len(close) >= 6 else 0.0
            atr = (spy["high"] - spy["low"]).rolling(14).mean().iloc[-1]
            atr_pct = float(atr / close.iloc[-1]) if close.iloc[-1] > 0 and not pd.isna(atr) else 0.0

            if ret_5 > 0.008 or atr_pct > 0.006:
                return {"state": "CHAOS", "risk_mult": 0.0, "pause_entries": True}

            if ret_5 > 0.004 or atr_pct > 0.0035:
                return {"state": "HIGH_VOL", "risk_mult": 0.5, "pause_entries": False}

            return {"state": "NORMAL", "risk_mult": 1.0, "pause_entries": False}
        except Exception as e:
            logging.error(f"MACRO FILTER ERR: {e}")
            return {"state": "UNKNOWN", "risk_mult": 0.5, "pause_entries": False}

    def risk_multiplier(self, macro_state: dict, regime: str) -> float:
        base = float(macro_state.get("risk_mult", 1.0))
        if regime == "RANGE":
            return base * 0.85
        if regime == "MICRO":
            return base * 0.75
        return base

    def symbol_performance_boost(self, symbol: str) -> float:
        try:
            with sqlite3.connect(DB_FILE) as conn:
                rows = conn.execute(
                    """
                    SELECT pnl
                    FROM trades
                    WHERE symbol = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (symbol, SYMBOL_STATS_TRADES),
                ).fetchall()

            if not rows:
                return 1.0

            pnls = [float(r[0]) for r in rows if r[0] is not None]
            if not pnls:
                return 1.0

            avg_pnl = sum(pnls) / len(pnls)
            win_rate = sum(1 for x in pnls if x > 0) / len(pnls)

            score = 1.0 + (avg_pnl * 10.0) + ((win_rate - 0.5) * 0.4)
            return max(SYMBOL_WEIGHT_MIN_BOOST, min(score, SYMBOL_WEIGHT_MAX_BOOST))
        except Exception as e:
            logging.error(f"SYMBOL BOOST ERR {symbol}: {e}")
            return 1.0

    # ------------------------
    # SIGNALS
    # ------------------------
    def trend_signal(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < max(EMA_SLOW, 60):
            return None

        price = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        rvol = float(df["rvol"].iloc[-1]) if not pd.isna(df["rvol"].iloc[-1]) else 0.0
        vwap = float(df["vwap"].iloc[-1]) if not pd.isna(df["vwap"].iloc[-1]) else price
        ema20 = float(df["ema20"].iloc[-1])
        ema200 = float(df["ema200"].iloc[-1])
        atr = float(df["atr"].iloc[-1]) if not pd.isna(df["atr"].iloc[-1]) else 0.0

        if rvol < RVOL_TREND_MIN:
            return None

        if abs(price - vwap) / max(price, 1e-9) > VWAP_DEV_LIMIT:
            return None

        trigger = (atr / max(price, 1e-9)) * 1.5
        dist = (price - ema20) / max(ema20, 1e-9)
        stop_pct = self.adaptive_stop_pct(df, "TREND")

        if price > ema200 and price > vwap and dist < -trigger and price > prev_close:
            return Signal(symbol, "BUY", "TREND", "VELEZ_PULLBACK_LONG", price, stop_pct, SPREAD_LIMIT_TREND)

        if price < ema200 and price < vwap and dist > trigger and price < prev_close:
            return Signal(symbol, "SELL", "TREND", "VELEZ_PULLBACK_SHORT", price, stop_pct, SPREAD_LIMIT_TREND)

        return None

    def range_signal(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < max(RANGE_LOOKBACK, 60):
            return None

        tail = df.tail(RANGE_LOOKBACK).copy()
        price = float(tail["close"].iloc[-1])
        prev_close = float(tail["close"].iloc[-2])
        rvol = float(tail["rvol"].iloc[-1]) if not pd.isna(tail["rvol"].iloc[-1]) else 0.0
        vwap = float(tail["vwap"].iloc[-1]) if not pd.isna(tail["vwap"].iloc[-1]) else price
        atr_pct = float(tail["atr_pct"].iloc[-1]) if not pd.isna(tail["atr_pct"].iloc[-1]) else 0.0

        if rvol < RVOL_RANGE_MIN:
            return None

        if atr_pct > ATR_PCT_MAX_RANGE:
            return None

        range_hi = float(tail["high"].max())
        range_lo = float(tail["low"].min())
        range_mid = (range_hi + range_lo) / 2.0
        range_width = range_hi - range_lo
        range_width_pct = range_width / max(price, 1e-9)

        if range_width_pct < MIN_RANGE_WIDTH_PCT or range_width_pct > MAX_RANGE_WIDTH_PCT:
            return None

        if abs(price - range_mid) / max(price, 1e-9) < MID_DEAD_ZONE_PCT:
            return None

        if abs(price - vwap) / max(price, 1e-9) > VWAP_DEV_LIMIT:
            return None

        if self.breakout_risk(tail):
            return None

        last_open = float(tail["open"].iloc[-1])
        last_close = float(tail["close"].iloc[-1])

        near_top = (range_hi - price) / max(price, 1e-9) <= RANGE_TOUCH_TOL
        near_bottom = (price - range_lo) / max(price, 1e-9) <= RANGE_TOUCH_TOL

        reject_top = near_top and last_close < last_open and last_close < prev_close
        bounce_bottom = near_bottom and last_close > last_open and last_close > prev_close

        stop_pct = self.adaptive_stop_pct(df, "RANGE")

        if reject_top:
            return Signal(symbol, "SELL", "RANGE", "RANGE_REJECT_TOP", price, stop_pct, SPREAD_LIMIT_RANGE)

        if bounce_bottom:
            return Signal(symbol, "BUY", "RANGE", "RANGE_BOUNCE_BOTTOM", price, stop_pct, SPREAD_LIMIT_RANGE)

        return None

    def micro_signal(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < max(EMA_SLOW, 40):
            return None

        price = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        ema20 = float(df["ema20"].iloc[-1])
        ema200 = float(df["ema200"].iloc[-1])
        vwap = float(df["vwap"].iloc[-1]) if not pd.isna(df["vwap"].iloc[-1]) else price
        atr = float(df["atr"].iloc[-1]) if not pd.isna(df["atr"].iloc[-1]) else 0.0
        rvol = float(df["rvol"].iloc[-1]) if not pd.isna(df["rvol"].iloc[-1]) else 0.0

        if rvol < RVOL_MICRO_MIN:
            return None

        if atr <= 0:
            return None

        pullback = abs(price - ema20) / max(price, 1e-9)
        near_vwap = abs(price - vwap) / max(price, 1e-9) < 0.003

        if price > ema200 and ema20 > vwap and near_vwap and pullback < 0.003 and price > prev_close:
            stop_pct = self.adaptive_stop_pct(df, "MICRO")
            return Signal(symbol, "BUY", "MICRO", "MICRO_RECLAIM_LONG", price, stop_pct, SPREAD_LIMIT_MICRO)

        if price < ema200 and ema20 < vwap and near_vwap and pullback < 0.003 and price < prev_close:
            stop_pct = self.adaptive_stop_pct(df, "MICRO")
            return Signal(symbol, "SELL", "MICRO", "MICRO_RECLAIM_SHORT", price, stop_pct, SPREAD_LIMIT_MICRO)

        return None

    def signal_candidates(self, symbol: str, raw_df: pd.DataFrame) -> List[Signal]:
        if raw_df is None or raw_df.empty or len(raw_df) < max(EMA_SLOW, 60):
            return []

        df = self.enrich(raw_df)
        regime = self.regime_vote(df)
        out: List[Signal] = []

        if regime == "TREND":
            sig = self.trend_signal(symbol, df)
            if sig:
                out.append(sig)
        elif regime == "RANGE":
            sig = self.range_signal(symbol, df)
            if sig:
                out.append(sig)

        micro = self.micro_signal(symbol, df)
        if micro:
            out.append(micro)

        return out

    def heat_score(self, sig: Signal, df: pd.DataFrame, macro_state: dict) -> float:
        score = 0.0

        rvol = float(df["rvol"].iloc[-1]) if not pd.isna(df["rvol"].iloc[-1]) else 0.0
        atr_pct = float(df["atr_pct"].iloc[-1]) if not pd.isna(df["atr_pct"].iloc[-1]) else 0.0
        ema20 = float(df["ema20"].iloc[-1])
        ema200 = float(df["ema200"].iloc[-1])
        price = float(df["close"].iloc[-1])
        vwap = float(df["vwap"].iloc[-1]) if not pd.isna(df["vwap"].iloc[-1]) else price

        score += min(rvol, 2.0) * 20.0

        if sig.regime == "TREND":
            align = 1.0 if ((sig.side == "BUY" and price > ema200 and price > vwap) or (sig.side == "SELL" and price < ema200 and price < vwap)) else 0.0
            score += align * 30.0
            score += max(0.0, 10.0 - abs(price - ema20) / max(price, 1e-9) * 5000.0)

        elif sig.regime == "RANGE":
            score += self.chop_score(df) * 35.0
            score += max(0.0, 10.0 - abs(price - vwap) / max(price, 1e-9) * 4000.0)

        elif sig.regime == "MICRO":
            score += 25.0
            score += max(0.0, 12.0 - abs(price - vwap) / max(price, 1e-9) * 5000.0)
            score += max(0.0, 8.0 - atr_pct * 500.0)

        score *= self.risk_multiplier(macro_state, sig.regime)
        score *= self.symbol_performance_boost(sig.symbol)

        if "VELEZ" in sig.trigger:
            score += 6.0
        if "MICRO" in sig.trigger:
            score += 5.0
        if "RANGE" in sig.trigger:
            score += 4.0

        return round(max(score, 0.0), 3)

    def adaptive_position_size_with_macro(self, price: float, stop_pct: float, macro_mult: float) -> int:
        try:
            account = self.api.get_account()
            equity = float(account.equity)
        except Exception as e:
            logging.error(f"ACCOUNT ERR: {e}")
            return 1

        risk_dollars = equity * BASE_RISK * macro_mult
        stop_dollars = max(price * stop_pct, 0.01)

        qty = int(risk_dollars / stop_dollars)
        qty = max(qty, 1)

        max_qty_by_notional = int(MAX_NOTIONAL_PER_TRADE / max(price, 0.01))
        qty = min(qty, max(max_qty_by_notional, 1))

        return max(qty, 1)

    def can_enter(self, symbol: str, regime: str, open_positions: Set[str], open_orders: Set[str]) -> bool:
        if symbol in open_positions:
            return False
        if symbol in open_orders:
            return False
        if len(open_positions) >= MAX_OPEN:
            return False

        last_t = self.last_trade.get(symbol, 0)
        if regime == "RANGE":
            cooldown = COOLDOWN_RANGE
        elif regime == "MICRO":
            cooldown = COOLDOWN_MICRO
        else:
            cooldown = COOLDOWN_TREND

        if time.time() - last_t < cooldown:
            return False

        return True

    def execute_entry(self, sig: Signal, macro_state: dict):
        try:
            if macro_state.get("pause_entries", False):
                self.log_event(sig.symbol, "SKIP", f"macro_pause:{macro_state.get('state')}")
                return

            if not self.passes_orderbook_filter(sig.symbol, sig.side, sig.spread_limit):
                return

            macro_mult = self.risk_multiplier(macro_state, sig.regime)
            qty = self.adaptive_position_size_with_macro(sig.price, sig.stop_pct, macro_mult)

            self.api.submit_order(
                symbol=sig.symbol,
                qty=qty,
                side="buy" if sig.side == "BUY" else "sell",
                type="market",
                time_in_force="day",
            )

            self.last_trade[sig.symbol] = time.time()
            self.trade_peaks[sig.symbol] = 0.0
            self.entry_meta[sig.symbol] = {
                "regime": sig.regime,
                "trigger": sig.trigger,
                "entry_price": sig.price,
                "stop_pct": sig.stop_pct,
                "macro_state": macro_state.get("state", "UNKNOWN"),
                "heat": sig.heat,
                "entered_at": datetime.now(NY).isoformat(),
            }
            self.save_state()

            self.log_event(
                sig.symbol,
                "ENTRY",
                f"{sig.side}|{sig.regime}|{sig.trigger}|qty={qty}|heat={sig.heat}|stop={sig.stop_pct:.4%}|macro={macro_state.get('state')}"
            )
            logging.info(
                f"ENTRY {sig.symbol} {sig.side} | {sig.regime} | {sig.trigger} | "
                f"qty={qty} | heat={sig.heat} | stop={sig.stop_pct:.4%} | macro={macro_state.get('state')}"
            )

        except Exception as e:
            logging.error(f"ENTRY ERR {sig.symbol}: {e}")
            self.log_event(sig.symbol, "ENTRY_ERR", str(e))

    def close_pos(self, symbol: str, pnl: float, peak_pnl: float, entry: float, exit_px: float, side_num: int, reason: str):
        try:
            now = time.time()

            if now - self.last_exit_attempt.get(symbol, 0) < EXIT_COOLDOWN:
                logging.info(f"SKIP CLOSE {symbol}: exit cooldown")
                return

            open_order_symbols = self.open_order_symbols()
            if symbol in open_order_symbols:
                logging.info(f"SKIP CLOSE {symbol}: open order exists")
                return

            self.cancel_symbol_orders(symbol)
            self.api.close_position(symbol)
            self.last_exit_attempt[symbol] = now

            trade_data = self.entry_meta.get(symbol, {})
            regime = trade_data.get("regime", "UNKNOWN")
            trigger = trade_data.get("trigger", "UNKNOWN")
            side_str = "BUY" if side_num == 1 else "SELL"

            with sqlite3.connect(DB_FILE) as conn:
                conn.execute(
                    """
                    INSERT INTO trades
                    (symbol, side, entry_price, exit_price, pnl, peak_pnl, regime_vote, trigger_used, exit_reason, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        side_str,
                        entry,
                        exit_px,
                        pnl,
                        peak_pnl,
                        regime,
                        trigger,
                        reason,
                        datetime.now(NY).isoformat(),
                    ),
                )
                conn.commit()

            self.log_event(symbol, "EXIT", f"{reason}|pnl={pnl:.4%}|peak={peak_pnl:.4%}")
            logging.info(f"CLOSE {symbol} [{reason}] PnL: {pnl:.4%} | Peak: {peak_pnl:.4%}")

            self.trade_peaks.pop(symbol, None)
            self.entry_meta.pop(symbol, None)
            self.save_state()

        except Exception as e:
            logging.error(f"CLOSE ERR {symbol}: {e}")
            self.log_event(symbol, "CLOSE_ERR", str(e))

    def manage_positions(self):
        try:
            positions = self.api.list_positions()
            open_syms = set()

            for p in positions:
                sym = p.symbol
                open_syms.add(sym)

                qty = float(p.qty)
                entry = float(p.avg_entry_price)
                price = float(p.current_price)
                side_num = 1 if qty > 0 else -1

                pnl = ((price - entry) * side_num) / entry

                if sym not in self.trade_peaks:
                    self.trade_peaks[sym] = pnl
                elif pnl > self.trade_peaks[sym]:
                    self.trade_peaks[sym] = pnl

                peak = self.trade_peaks[sym]
                meta = self.entry_meta.get(sym, {})
                regime = meta.get("regime", "TREND")
                stop_pct = float(meta.get("stop_pct", STOP_TREND_MIN if regime == "TREND" else STOP_RANGE_MIN))

                if regime == "TREND":
                    if pnl <= -stop_pct:
                        self.close_pos(sym, pnl, peak, entry, price, side_num, "HARD_STOP_TREND")
                    elif peak >= TRAIL_ACTIVATION and (peak - pnl) >= TRAIL_PCT:
                        self.close_pos(sym, pnl, peak, entry, price, side_num, "TRAIL_EXIT")
                    elif pnl >= CEILING_PCT:
                        self.close_pos(sym, pnl, peak, entry, price, side_num, "CEILING_HIT")

                elif regime == "RANGE":
                    if pnl <= -stop_pct:
                        self.close_pos(sym, pnl, peak, entry, price, side_num, "HARD_STOP_RANGE")
                    elif pnl >= TP_RANGE:
                        self.close_pos(sym, pnl, peak, entry, price, side_num, "RANGE_TP")
                    elif pnl >= MID_EXIT_RANGE:
                        self.close_pos(sym, pnl, peak, entry, price, side_num, "RANGE_MID_EXIT")

                elif regime == "MICRO":
                    if pnl <= -stop_pct:
                        self.close_pos(sym, pnl, peak, entry, price, side_num, "HARD_STOP_MICRO")
                    elif pnl >= TP_MICRO:
                        self.close_pos(sym, pnl, peak, entry, price, side_num, "MICRO_TP")
                    elif peak >= MICRO_TRAIL_ACTIVATION and (peak - pnl) >= MICRO_TRAIL_PCT:
                        self.close_pos(sym, pnl, peak, entry, price, side_num, "MICRO_TRAIL")

            for sym in list(self.trade_peaks.keys()):
                if sym not in open_syms:
                    self.trade_peaks.pop(sym, None)
                    self.entry_meta.pop(sym, None)

            self.save_state()

        except Exception as e:
            logging.error(f"MANAGE ERR: {e}")
            self.log_event("SYSTEM", "MANAGE_ERR", str(e))

    def run(self):
        logging.info("ALPACA MASTER TOP3 ENGINE ARMED")

        while True:
            try:
                if not self.market_open():
                    logging.info("MARKET CLOSED - waiting 60s")
                    time.sleep(60)
                    continue

                self.manage_positions()

                bars_map = self.fetch_batch_data()
                if not bars_map:
                    time.sleep(10)
                    continue

                macro_state = self.macro_volatility_state(bars_map)
                logging.info(
                    f"MACRO STATE {macro_state['state']} | "
                    f"risk_mult={macro_state['risk_mult']} | pause={macro_state['pause_entries']}"
                )

                open_positions_map = self.positions_map()
                open_positions = set(open_positions_map.keys())
                open_orders = self.open_order_symbols()

                if len(open_positions) >= MAX_OPEN:
                    time.sleep(5)
                    continue

                candidates: List[Signal] = []

                for sym in SYMBOLS:
                    raw_df = bars_map.get(sym)
                    if raw_df is None or len(raw_df) < 60:
                        continue

                    sigs = self.signal_candidates(sym, raw_df)
                    if not sigs:
                        continue

                    df = self.enrich(raw_df)
                    for sig in sigs:
                        if not self.can_enter(sym, sig.regime, open_positions, open_orders):
                            continue
                        sig.heat = self.heat_score(sig, df, macro_state)
                        candidates.append(sig)

                if candidates:
                    candidates.sort(key=lambda x: x.heat, reverse=True)
                    take_n = min(MAX_NEW_PER_SCAN, max(0, MAX_OPEN - len(open_positions)))
                    chosen = candidates[:take_n]

                    for sig in chosen:
                        self.execute_entry(sig, macro_state)
                        time.sleep(0.25)

                time.sleep(SCAN_SECONDS)

            except Exception as e:
                logging.error(f"MAIN LOOP ERR: {e}")
                self.log_event("SYSTEM", "MAIN_LOOP_ERR", str(e))
                time.sleep(10)


if __name__ == "__main__":
    AlpacaMasterTop3().run()
