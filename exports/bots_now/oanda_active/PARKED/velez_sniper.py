#!/usr/bin/env python3
import os
import time
import json
import math
import logging
import sqlite3
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from oandapyV20 import API
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.pricing as pricing
import oandapyV20.endpoints.trades as trades

# =========================
# Logging
# =========================
LOG_FMT = "%(asctime)s | %(levelname)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
logger = logging.getLogger("velez_sniper")


# =========================
# Utility
# =========================
def utc_day_key() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def pip_size(symbol: str) -> float:
    # Most JPY pairs = 0.01, else 0.0001
    return 0.01 if "JPY" in symbol else 0.0001


def spread_pips(symbol: str, bid: float, ask: float) -> float:
    ps = pip_size(symbol)
    if ps <= 0:
        return 0.0
    return (ask - bid) / ps


def rolling_zscore(series: pd.Series, window: int) -> float:
    if len(series) < window + 1:
        return 0.0
    s = series.iloc[-window:]
    mu = s.mean()
    sd = s.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float((series.iloc[-1] - mu) / sd)


def donchian_high_low(df: pd.DataFrame, period: int) -> Tuple[float, float]:
    if len(df) < period:
        return (float(df["high"].max()), float(df["low"].min()))
    h = float(df["high"].tail(period).max())
    l = float(df["low"].tail(period).min())
    return (h, l)


def adx_14(df: pd.DataFrame) -> float:
    """
    Lightweight ADX(14) implementation for OHLC.
    Returns the latest ADX value.
    """
    if len(df) < 30:
        return 0.0

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr1 = (high - low).abs()
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    n = 14
    atr = tr.rolling(n).mean()
    plus_di = 100 * (pd.Series(plus_dm).rolling(n).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(n).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(n).mean()

    val = adx.iloc[-1]
    return float(val) if not np.isnan(val) else 0.0


# =========================
# Config
# =========================
@dataclass
class BotConfig:
    # oanda
    api_key: str
    account_id: str
    environment: str  # "practice" or "live"

    # pairs
    pairs: List[str]
    granularity: str
    candle_count: int

    # loop
    loop_sleep: float
    heartbeat_secs: int

    # sizing
    starting_balance: float
    delta: float
    base_units: int
    max_units: int
    risk_per_trade: float
    max_open_trades: int
    max_trades_per_pair: int

    # triggers
    adx_min: float
    z_score_limit: float
    donchian_period: int
    sma_period: int
    require_trend: bool
    use_donchian_breakout: bool

    # virtual exits
    v_sl_pct: float
    v_be_pct: float
    v_trail_start: float
    v_trail_dist: float

    # drawdown controls
    daily_target_pct: float
    daily_loss_pct: float
    max_dd_pct: float

    # spreads
    max_spread_pips: float
    spread_caps: Dict[str, float]

    # retries
    read_retries: int
    write_retries: int
    pause_secs: float


# =========================
# Bot
# =========================
class OandaVelezSniper:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.client = API(access_token=cfg.api_key, environment=cfg.environment)

        self.base_dir = Path(__file__).resolve().parent
        self.state_path = self.base_dir / "state_open_trades.json"
        self.db_path = self.base_dir / "velez_sniper.db"

        # runtime
        self.day_key = ""
        self.day_start_nav = 0.0
        self.peak_nav = 0.0

        # local registry: trade_id -> dict
        self.open_trades: Dict[str, dict] = {}
        self._load_state()
        self._init_db()

        # sync with broker at start
        self._reconcile_open_trades()

    # ---------- persistence ----------
    def _load_state(self):
        if not self.state_path.exists():
            self.open_trades = {}
            return
        try:
            raw = json.loads(self.state_path.read_text())
            # ensure dict format
            if isinstance(raw, dict):
                self.open_trades = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            else:
                self.open_trades = {}
        except Exception:
            self.open_trades = {}

    def _save_state(self):
        try:
            self.state_path.write_text(json.dumps(self.open_trades, indent=2))
        except Exception as e:
            logger.warning(f"STATE save failed: {e}")

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                trade_id TEXT,
                instrument TEXT,
                side TEXT,
                units INTEGER,
                entry REAL,
                exit REAL,
                pnl REAL,
                reason TEXT
            )"""
        )
        conn.commit()
        conn.close()

    def _log_trade(self, trade_id: str, instrument: str, side: str, units: int,
                   entry: float, exit_px: float, pnl: float, reason: str):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO trade_log (ts, trade_id, instrument, side, units, entry, exit, pnl, reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.datetime.utcnow().isoformat(), str(trade_id), instrument, side, int(units),
             float(entry), float(exit_px), float(pnl), reason)
        )
        conn.commit()
        conn.close()

    # ---------- oanda helpers ----------
    def _req(self, endpoint, write=False):
        retries = self.cfg.write_retries if write else self.cfg.read_retries
        for i in range(retries):
            try:
                return self.client.request(endpoint)
            except Exception as e:
                if i == retries - 1:
                    raise
                time.sleep(self.cfg.pause_secs)
        raise RuntimeError("unreachable")

    def _account_nav(self) -> float:
        r = accounts.AccountSummary(self.cfg.account_id)
        j = self._req(r)
        return safe_float(j.get("account", {}).get("NAV"), 0.0)

    def _get_pricing(self, symbol: str) -> Optional[Tuple[float, float]]:
        r = pricing.PricingInfo(self.cfg.account_id, params={"instruments": symbol})
        j = self._req(r)
        prices = j.get("prices", [])
        if not prices:
            return None
        p = prices[0]
        bids = p.get("bids", [])
        asks = p.get("asks", [])
        if not bids or not asks:
            return None
        bid = safe_float(bids[0].get("price"))
        ask = safe_float(asks[0].get("price"))
        if bid <= 0 or ask <= 0:
            return None
        return (bid, ask)

    def _get_candles(self, symbol: str) -> Optional[pd.DataFrame]:
        r = instruments.InstrumentsCandles(
            instrument=symbol,
            params={"count": int(self.cfg.candle_count), "granularity": self.cfg.granularity, "price": "M"}
        )
        j = self._req(r)
        c = j.get("candles", [])
        rows = []
        for x in c:
            if not x.get("complete", True):
                continue
            m = x.get("mid", {})
            rows.append({
                "time": x.get("time"),
                "open": safe_float(m.get("o")),
                "high": safe_float(m.get("h")),
                "low": safe_float(m.get("l")),
                "close": safe_float(m.get("c")),
                "volume": safe_float(x.get("volume")),
            })
        if len(rows) < 50:
            return None
        return pd.DataFrame(rows)

    def _broker_open_trades(self) -> List[dict]:
        r = trades.OpenTrades(self.cfg.account_id)
        j = self._req(r)
        t = j.get("trades", [])
        return t if isinstance(t, list) else []

    def _reconcile_open_trades(self):
        broker = self._broker_open_trades()
        broker_ids = set(str(t.get("id")) for t in broker if isinstance(t, dict))

        # remove locals that no longer exist
        for tid in list(self.open_trades.keys()):
            if tid not in broker_ids:
                self.open_trades.pop(tid, None)

        # add broker trades not in local registry
        for t in broker:
            tid = str(t.get("id"))
            if tid in self.open_trades:
                continue
            instrument = t.get("instrument")
            units = int(float(t.get("currentUnits", "0")))
            entry = safe_float(t.get("price"))
            side = "LONG" if units > 0 else "SHORT"
            self.open_trades[tid] = {
                "instrument": instrument,
                "units": units,
                "side": side,
                "entry": entry,
                "peak": entry,
                "opened_ts": datetime.datetime.utcnow().isoformat(),
            }

        self._save_state()

    # ---------- sizing ----------
    def _geometric_units(self, nav: float) -> int:
        """
        Fixed-ratio geometric sizing:
          profit = nav - starting_balance
          n = floor( (sqrt(1 + 8*profit/delta) - 1) / 2 )
          units = base_units * (n+1)
        """
        profit = max(0.0, nav - self.cfg.starting_balance)
        n = (math.sqrt(1.0 + 8.0 * profit / max(1e-9, self.cfg.delta)) - 1.0) / 2.0
        step = int(math.floor(n)) + 1
        units = int(self.cfg.base_units * max(1, step))
        units = min(units, int(self.cfg.max_units))
        if units < 1:
            units = 1
        return units

    def _fifo_unique_units(self, units: int) -> int:
        """
        Optional nano-uniqueness: if broker has trade with same units, bump by +1.
        """
        try:
            broker = self._broker_open_trades()
            existing = {abs(int(float(t.get("currentUnits", "0")))) for t in broker if isinstance(t, dict)}
            u = abs(int(units))
            while u in existing:
                u += 1
            return u if units > 0 else -u
        except Exception:
            return units

    # ---------- entries ----------
    def _pair_open_count(self, symbol: str) -> int:
        c = 0
        for t in self.open_trades.values():
            if isinstance(t, dict) and t.get("instrument") == symbol:
                c += 1
        return c

    def _total_open_count(self) -> int:
        return len(self.open_trades)

    def _spread_cap_for(self, symbol: str) -> float:
        return float(self.cfg.spread_caps.get(symbol, self.cfg.max_spread_pips))

    def _trend_ok(self, df: pd.DataFrame, side: str) -> bool:
        """
        Simple trend gate using SMA: for BUY, close above SMA; for SELL, close below SMA.
        """
        if len(df) < self.cfg.sma_period + 5:
            return True
        sma = df["close"].rolling(self.cfg.sma_period).mean().iloc[-1]
        px = df["close"].iloc[-1]
        if np.isnan(sma):
            return True
        return (px >= sma) if side == "BUY" else (px <= sma)

    def _signal(self, symbol: str, df: pd.DataFrame) -> Optional[str]:
        """
        Hybrid:
          - If ADX >= ADX_MIN => momentum mode, trade in direction of Donchian breakout (optional) or z bias.
          - Else => mean reversion: fade zscore beyond Z_SCORE_LIMIT.
        """
        px = float(df["close"].iloc[-1])
        z = rolling_zscore(df["close"], window=self.cfg.sma_period)
        a = adx_14(df)

        don_h, don_l = donchian_high_low(df, self.cfg.donchian_period)

        # momentum mode
        if a >= self.cfg.adx_min:
            if self.cfg.use_donchian_breakout:
                if px > don_h:
                    return "BUY"
                if px < don_l:
                    return "SELL"
                return None
            # simpler: z-score bias in momentum
            if z >= +self.cfg.z_score_limit:
                return "BUY"
            if z <= -self.cfg.z_score_limit:
                return "SELL"
            return None

        # mean reversion mode
        if abs(z) >= self.cfg.z_score_limit:
            return "SELL" if z > 0 else "BUY"

        return None

    def _place_market(self, symbol: str, side: str, units: int) -> Optional[str]:
        """
        Places market order and returns trade_id if filled.
        """
        signed_units = units if side == "BUY" else -units
        data = {
            "order": {
                "type": "MARKET",
                "instrument": symbol,
                "units": str(int(signed_units)),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT"
            }
        }
        r = orders.OrderCreate(self.cfg.account_id, data=data)
        j = self._req(r, write=True)

        # OrderFillTransaction contains tradeOpened/tradeReduced/etc
        oft = j.get("orderFillTransaction", {})
        # find trade id
        tid = None
        if "tradeOpened" in oft and isinstance(oft["tradeOpened"], dict):
            tid = str(oft["tradeOpened"].get("tradeID"))
        # fallback: relatedTransactionIDs might exist but is NOT tradeID reliably
        if not tid:
            return None

        entry = safe_float(oft.get("price"))
        self.open_trades[tid] = {
            "instrument": symbol,
            "units": int(signed_units),
            "side": "LONG" if signed_units > 0 else "SHORT",
            "entry": float(entry),
            "peak": float(entry),
            "opened_ts": datetime.datetime.utcnow().isoformat(),
        }
        self._save_state()
        logger.info(f"TRADE OPEN | {symbol} {side} entry={entry:.5f} units={signed_units}")
        return tid

    # ---------- virtual exits ----------
    def _mid_price(self, bid: float, ask: float) -> float:
        return (bid + ask) / 2.0

    def _profit_pct(self, side: str, entry: float, px: float) -> float:
        if entry <= 0:
            return 0.0
        if side == "LONG":
            return (px - entry) / entry
        return (entry - px) / entry

    def _close_trade(self, trade_id: str, reason: str, exit_px: float):
        t = self.open_trades.get(trade_id)
        if not isinstance(t, dict):
            return
        instrument = t.get("instrument")
        units = int(t.get("units", 0))
        entry = float(t.get("entry", 0.0))
        side = t.get("side", "LONG")

        r = trades.TradeClose(self.cfg.account_id, tradeID=str(trade_id))
        self._req(r, write=True)

        pnl = (exit_px - entry) * units if side == "LONG" else (entry - exit_px) * abs(units)
        self._log_trade(str(trade_id), instrument, side, units, entry, exit_px, pnl, reason)

        logger.info(f"TRADE CLOSE | {instrument} {side} tid={trade_id} exit={exit_px:.5f} reason={reason}")
        self.open_trades.pop(trade_id, None)
        self._save_state()

    def manage_virtual_exits(self):
        """
        Applies:
          - VIRTUAL_SL_PCT hard stop
          - VIRTUAL_BE_PCT breakeven logic
          - VIRTUAL_TRAIL_START & VIRTUAL_TRAIL_DIST trailing
        """
        self._reconcile_open_trades()
        broker = self._broker_open_trades()

        # map broker id -> broker trade dict
        broker_map = {str(t.get("id")): t for t in broker if isinstance(t, dict)}

        for tid in list(self.open_trades.keys()):
            local = self.open_trades.get(tid)
            if not isinstance(local, dict):
                self.open_trades.pop(tid, None)
                continue

            bt = broker_map.get(tid)
            if not bt:
                # broker trade gone
                self.open_trades.pop(tid, None)
                continue

            symbol = local.get("instrument")
            entry = float(local.get("entry", 0.0))
            side = local.get("side", "LONG")

            pxq = self._get_pricing(symbol)
            if not pxq:
                continue
            bid, ask = pxq
            mid = self._mid_price(bid, ask)

            # update peak for trail
            peak = float(local.get("peak", entry))
            if side == "LONG":
                peak = max(peak, mid)
            else:
                peak = min(peak, mid)
            local["peak"] = peak

            prof = self._profit_pct(side, entry, mid)

            # 1) hard stop
            if prof <= -self.cfg.v_sl_pct:
                self._close_trade(tid, "STEALTH_STOP", mid)
                continue

            # 2) breakeven “jump”
            if self.cfg.v_be_pct <= prof < self.cfg.v_trail_start:
                # if it comes back through entry after triggering BE window, exit
                if (side == "LONG" and mid <= entry) or (side == "SHORT" and mid >= entry):
                    self._close_trade(tid, "VIRTUAL_BE", mid)
                    continue

            # 3) trailing
            if prof >= self.cfg.v_trail_start:
                dist = self.cfg.v_trail_dist * entry
                if side == "LONG":
                    trail_line = peak - dist
                    if mid <= trail_line:
                        self._close_trade(tid, "MAX_ROI_TRAIL", mid)
                        continue
                else:
                    trail_line = peak + dist
                    if mid >= trail_line:
                        self._close_trade(tid, "MAX_ROI_TRAIL", mid)
                        continue

        self._save_state()

    # ---------- risk guards ----------
    def _daily_reset_if_needed(self, nav: float):
        dk = utc_day_key()
        if dk != self.day_key:
            self.day_key = dk
            self.day_start_nav = nav
            self.peak_nav = nav
            logger.info(f"DAILY RESET | day_start_nav={nav:.2f}")

    def _update_peaks(self, nav: float):
        if nav > self.peak_nav:
            self.peak_nav = nav

    def _halted(self, nav: float) -> Optional[str]:
        # daily target lock
        if self.day_start_nav > 0:
            if (nav - self.day_start_nav) / self.day_start_nav >= self.cfg.daily_target_pct:
                return "DAILY_TARGET_LOCK"
            if (self.day_start_nav - nav) / self.day_start_nav >= self.cfg.daily_loss_pct:
                return "DAILY_LOSS_HALT"

        # peak-to-valley drawdown
        if self.peak_nav > 0 and nav < self.peak_nav * (1.0 - self.cfg.max_dd_pct):
            return "GLOBAL_DD_HALT"

        return None

    # ---------- main ----------
    def run(self):
        logger.info(f"ONLINE: OANDA {self.cfg.environment.upper()} | Pairs: {', '.join(self.cfg.pairs)}")

        last_hb = 0.0

        while True:
            try:
                nav = self._account_nav()
                if nav <= 0:
                    logger.error("ERROR | NAV parse failed")
                    time.sleep(self.cfg.pause_secs)
                    continue

                self._daily_reset_if_needed(nav)
                self._update_peaks(nav)

                # always manage exits first (protects drawdown)
                self.manage_virtual_exits()

                # heartbeat
                now = time.time()
                if now - last_hb >= self.cfg.heartbeat_secs:
                    last_hb = now
                    logger.info(
                        f"HEARTBEAT | nav={nav:.2f} | open_trades={self._total_open_count()} | "
                        f"max_total={self.cfg.max_open_trades} | sleep={self.cfg.loop_sleep}s"
                    )

                # halt guards
                h = self._halted(nav)
                if h:
                    logger.warning(f"HALT | {h} | sleeping 10 minutes")
                    time.sleep(600)
                    continue

                # open trades cap
                if self._total_open_count() >= self.cfg.max_open_trades:
                    time.sleep(self.cfg.loop_sleep)
                    continue

                # scan entries
                for sym in self.cfg.pairs:
                    if self._total_open_count() >= self.cfg.max_open_trades:
                        break

                    if self._pair_open_count(sym) >= self.cfg.max_trades_per_pair:
                        continue

                    pxq = self._get_pricing(sym)
                    if not pxq:
                        continue
                    bid, ask = pxq
                    sp = spread_pips(sym, bid, ask)
                    cap = self._spread_cap_for(sym)
                    if sp > self.cfg.max_spread_pips or sp > cap:
                        logger.warning(f"TRADE BLOCK | {sym} spread too wide: {sp:.2f} pips > {cap:.2f}")
                        continue

                    df = self._get_candles(sym)
                    if df is None:
                        logger.info(f"SCAN | {sym} | no data")
                        continue

                    sig = self._signal(sym, df)
                    if not sig:
                        continue

                    if self.cfg.require_trend and not self._trend_ok(df, sig):
                        logger.info(f"FILTER | {sym} | trend_required blocked")
                        continue

                    units = self._geometric_units(nav)
                    units = min(units, self.cfg.max_units)
                    units = self._fifo_unique_units(units)

                    # place
                    tid = self._place_market(sym, sig, units)
                    if tid:
                        # after opening a trade, small pause
                        time.sleep(0.5)

                time.sleep(self.cfg.loop_sleep)

            except KeyboardInterrupt:
                logger.info("EXIT: KeyboardInterrupt")
                break
            except Exception as e:
                logger.exception(f"ERROR | {e}")
                time.sleep(self.cfg.pause_secs)


def load_config() -> BotConfig:
    base = Path(__file__).resolve().parent
    load_dotenv(dotenv_path=base / ".env", override=True)

    api_key = os.getenv("OANDA_API_KEY", "").strip()
    account_id = os.getenv("OANDA_ACCOUNT_ID", "").strip()
    env = (os.getenv("OANDA_ENVIRONMENT", "practice").strip().lower() or "practice")
    if env not in ("practice", "live"):
        env = "practice"

    pairs = [x.strip() for x in os.getenv("PAIRS", "EUR_USD,GBP_USD,USD_JPY").split(",") if x.strip()]
    gran = os.getenv("GRANULARITY", "M1").strip()
    candle_count = int(os.getenv("CANDLE_COUNT", "200"))

    loop_sleep = float(os.getenv("LOOP_SLEEP", "2"))
    heartbeat = int(os.getenv("HEARTBEAT_SECS", "20"))

    starting_balance = float(os.getenv("STARTING_BALANCE", "100000"))
    delta = float(os.getenv("DELTA", "250"))
    base_units = int(os.getenv("BASE_UNITS", "50000"))
    max_units = int(os.getenv("MAX_UNITS", "250000"))
    risk_per_trade = float(os.getenv("RISK_PER_TRADE", "0.02"))
    max_open_trades = int(os.getenv("MAX_OPEN_TRADES", "6"))
    max_trades_per_pair = int(os.getenv("MAX_TRADES_PER_PAIR", "1"))

    adx_min = float(os.getenv("ADX_MIN", "12"))
    z_lim = float(os.getenv("Z_SCORE_LIMIT", "0.8"))
    don = int(os.getenv("DONCHIAN_PERIOD", "8"))
    sma = int(os.getenv("SMA_PERIOD", "20"))

    require_trend = os.getenv("REQUIRE_TREND", "0").strip() in ("1", "true", "TRUE", "yes", "YES")
    use_donch = os.getenv("USE_DONCHIAN_BREAKOUT", "0").strip() in ("1", "true", "TRUE", "yes", "YES")

    v_sl = float(os.getenv("VIRTUAL_SL_PCT", "0.0025"))
    v_be = float(os.getenv("VIRTUAL_BE_PCT", "0.0040"))
    v_ts = float(os.getenv("VIRTUAL_TRAIL_START", "0.0100"))
    v_td = float(os.getenv("VIRTUAL_TRAIL_DIST", "0.0030"))

    daily_target = float(os.getenv("DAILY_TARGET_PCT", "0.90"))
    daily_loss = float(os.getenv("DAILY_LOSS_PCT", "0.90"))
    max_dd = float(os.getenv("MAX_DD_PCT", "0.90"))

    max_spread = float(os.getenv("MAX_SPREAD_PIPS", "10.0"))

    # build per-pair caps from env SPREAD_<PAIR>
    spread_caps = {}
    for p in pairs:
        key = f"SPREAD_{p}"
        if os.getenv(key) is not None:
            spread_caps[p] = float(os.getenv(key))
    if not spread_caps:
        spread_caps = {p: max_spread for p in pairs}

    read_retries = int(os.getenv("OANDA_READ_RETRIES", "30"))
    write_retries = int(os.getenv("OANDA_WRITE_RETRIES", "10"))
    pause = float(os.getenv("OANDA_PAUSE_SECS", "2"))

    if not api_key or not account_id:
        raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID in .env")

    return BotConfig(
        api_key=api_key,
        account_id=account_id,
        environment=env,
        pairs=pairs,
        granularity=gran,
        candle_count=candle_count,
        loop_sleep=loop_sleep,
        heartbeat_secs=heartbeat,
        starting_balance=starting_balance,
        delta=delta,
        base_units=base_units,
        max_units=max_units,
        risk_per_trade=risk_per_trade,
        max_open_trades=max_open_trades,
        max_trades_per_pair=max_trades_per_pair,
        adx_min=adx_min,
        z_score_limit=z_lim,
        donchian_period=don,
        sma_period=sma,
        require_trend=require_trend,
        use_donchian_breakout=use_donch,
        v_sl_pct=v_sl,
        v_be_pct=v_be,
        v_trail_start=v_ts,
        v_trail_dist=v_td,
        daily_target_pct=daily_target,
        daily_loss_pct=daily_loss,
        max_dd_pct=max_dd,
        max_spread_pips=max_spread,
        spread_caps=spread_caps,
        read_retries=read_retries,
        write_retries=write_retries,
        pause_secs=pause,
    )


if __name__ == "__main__":
    cfg = load_config()
    bot = OandaVelezSniper(cfg)
    bot.run()

