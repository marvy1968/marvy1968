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
import logging
import sqlite3
from pathlib import Path
from datetime import datetime

import pandas as pd
import pytz
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

# =========================
# SETUP
# =========================
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

NY = pytz.timezone("America/New_York")
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "alpaca_microedge_v3.db"
STATE_PATH = BASE_DIR / "alpaca_microedge_v3_state.json"


def safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def now_ny():
    return datetime.now(NY)


class AlpacaMicroEdgeV3:
    def __init__(self):
        self.api = tradeapi.REST(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
            api_version="v2"
        )

        # --- account / sizing ---
        self.starting_balance = float(os.getenv("STARTING_BALANCE", "100000"))
        self.base_risk_dollars = float(os.getenv("BASE_RISK_DOLLARS", "1000"))
        self.max_position_dollars = float(os.getenv("MAX_POSITION_DOLLARS", "5000"))

        # --- exits ---
        self.stop_pct = float(os.getenv("STOP_PCT", "0.0025"))
        self.be_trigger = float(os.getenv("BE_TRIGGER", "0.0040"))
        self.tp_pct = float(os.getenv("TP_PCT", "0.0075"))
        self.max_hold_secs = int(os.getenv("MAX_HOLD_SECS", "900"))
        self.daily_loss_limit_pct = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.02"))

        # --- risk / limits ---
        self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
        self.max_trades_per_symbol_day = int(os.getenv("MAX_TRADES_PER_SYMBOL_DAY", "5"))
        self.cooldown_secs = int(os.getenv("COOLDOWN_SECS", "600"))
        self.max_spread_pct = float(os.getenv("MAX_SPREAD_PCT", "0.0015"))
        self.min_dollar_vol = float(os.getenv("MIN_DOLLAR_VOL", "2000000"))
        self.loop_sleep = int(os.getenv("LOOP_SLEEP_SECS", "10"))

        # --- learner grid ---
        self.trigger_mult_options = [1.6, 1.8, 2.0, 2.2]
        self.vol_gate_options = [0.0010, 0.0015, 0.0020]

        self.current_params = {
            "trigger_mult": float(os.getenv("TRIGGER_MULT", "2.0")),
            "vol_gate": float(os.getenv("VOL_GATE", "0.0015")),
        }

        # --- universe ---
        env_universe = os.getenv("UNIVERSE", "NVDA,TSLA,AMD,META,AAPL,MSFT,TQQQ,AMZN")
        self.universe = [x.strip().upper() for x in env_universe.split(",") if x.strip()]

        # --- runtime state ---
        self.last_trade = {}
        self.entry_times = {}
        self.day_key = None
        self.day_start_equity = None
        self.learned_day = None

        self._init_db()
        self._load_state()

    # =========================
    # DB / STATE
    # =========================
    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    symbol TEXT,
                    side TEXT,
                    entry REAL,
                    exit REAL,
                    pnl REAL,
                    reason TEXT,
                    trigger_mult REAL,
                    vol_gate REAL,
                    entry_ts TEXT,
                    exit_ts TEXT
                )
            """)

    def _load_state(self):
        if STATE_PATH.exists():
            try:
                s = json.loads(STATE_PATH.read_text())
                self.last_trade = s.get("last_trade", {})
                self.entry_times = s.get("entry_times", {})
                self.day_key = s.get("day_key")
                self.day_start_equity = s.get("day_start_equity")
                self.learned_day = s.get("learned_day")
                self.current_params = s.get("current_params", self.current_params)
            except Exception as e:
                logging.warning(f"STATE LOAD WARN: {e}")

    def _save_state(self):
        STATE_PATH.write_text(json.dumps({
            "last_trade": self.last_trade,
            "entry_times": self.entry_times,
            "day_key": self.day_key,
            "day_start_equity": self.day_start_equity,
            "learned_day": self.learned_day,
            "current_params": self.current_params,
        }, indent=2))

    def log_trade(self, symbol, side, entry, exit_px, pnl, reason, entry_ts):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol, side, entry, exit_px, pnl, reason,
                    self.current_params["trigger_mult"],
                    self.current_params["vol_gate"],
                    entry_ts,
                    now_ny().isoformat()
                )
            )

    def trade_count_today(self, symbol):
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE symbol=? AND DATE(exit_ts)=DATE(?)",
                (symbol, now_ny().isoformat())
            ).fetchone()
        return int(row[0]) if row else 0

    # =========================
    # LEARNER
    # =========================
    def evaluate_params(self):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                df = pd.read_sql_query("SELECT * FROM trades", conn)

            if len(df) < 30:
                logging.info("🧠 LEARNER: not enough trades yet")
                return

            grouped = (
                df.groupby(["trigger_mult", "vol_gate"])
                  .agg(
                      trades=("pnl", "count"),
                      avg_pnl=("pnl", "mean"),
                      win_rate=("pnl", lambda x: (x > 0).mean())
                  )
            )

            grouped = grouped[grouped["trades"] >= 6]
            if grouped.empty:
                logging.info("🧠 LEARNER: no bucket has enough samples")
                return

            grouped["score"] = grouped["avg_pnl"] * 0.7 + grouped["win_rate"] * 0.3
            best = grouped.sort_values("score", ascending=False).index[0]

            self.current_params = {
                "trigger_mult": float(best[0]),
                "vol_gate": float(best[1]),
            }

            logging.info(f"🧠 LEARNER UPDATE: {self.current_params}")
            self._save_state()

        except Exception as e:
            logging.error(f"LEARNER ERR: {e}")

    def symbol_multiplier(self, symbol):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT pnl FROM trades WHERE symbol=? ORDER BY exit_ts DESC LIMIT 20",
                    (symbol,)
                ).fetchall()

            if not rows:
                return 1.0

            pnls = [safe_float(r[0], 0.0) for r in rows]
            avg_pnl = sum(pnls) / len(pnls)

            if avg_pnl > 0:
                return 1.15
            elif avg_pnl < 0:
                return 0.85
            return 1.0

        except Exception:
            return 1.0

    # =========================
    # SESSION / SAFETY
    # =========================
    def session_mode(self):
        now = now_ny()

        if now.weekday() >= 5:
            return "SLEEP"
        if now.hour < 9 or (now.hour == 9 and now.minute < 30):
            return "SLEEP"
        if now.hour >= 16:
            return "SLEEP"
        if now.hour == 15 and now.minute >= 55:
            return "DUMP"

        if (now.hour == 9 and now.minute >= 35) or (now.hour == 10 and now.minute < 30):
            return "OPEN"
        if (now.hour > 10 and now.hour < 14) or (now.hour == 14 and now.minute < 30):
            return "MIDDAY"
        return "POWER"

    def daily_breaker_ok(self):
        today = str(now_ny().date())
        equity = safe_float(self.api.get_account().equity, self.starting_balance)

        if self.day_key != today:
            self.day_key = today
            self.day_start_equity = equity
            self._save_state()

        if not self.day_start_equity or self.day_start_equity <= 0:
            self.day_start_equity = equity

        pnl_pct = (equity - self.day_start_equity) / self.day_start_equity

        if pnl_pct <= -self.daily_loss_limit_pct:
            logging.critical(f"🚨 DAILY BREAKER HIT: {pnl_pct:.2%}")
            try:
                self.api.close_all_positions()
            except Exception as e:
                logging.error(f"CLOSE ALL ERR: {e}")
            return False

        return True

    def pending_symbols(self):
        try:
            return {o.symbol for o in self.api.list_orders(status="open")}
        except Exception:
            return set()

    # =========================
    # DATA / SIGNALS
    # =========================
    def get_bars(self, sym):
        return self.api.get_bars(sym, "1Min", limit=100, adjustment="all").df

    def detect_entry(self, sym):
        bars = self.get_bars(sym)
        if bars is None or bars.empty or len(bars) < 50:
            return None, {}

        price = safe_float(bars["close"].iloc[-1], 0.0)
        if price <= 0:
            return None, {}

        dollar_vol = (bars["close"] * bars["volume"]).tail(20).mean()
        if dollar_vol < self.min_dollar_vol:
            return None, {}

        try:
            q = self.api.get_latest_quote(sym)
            bid = safe_float(getattr(q, "bid_price", 0.0))
            ask = safe_float(getattr(q, "ask_price", 0.0))
            if bid > 0 and ask > 0:
                spread_pct = (ask - bid) / price
                if spread_pct > self.max_spread_pct:
                    return None, {}
        except Exception:
            pass

        ema20 = bars["close"].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = bars["close"].ewm(span=50, adjust=False).mean().iloc[-1]

        tr = pd.concat([
            (bars["high"] - bars["low"]).abs(),
            (bars["high"] - bars["close"].shift()).abs(),
            (bars["low"] - bars["close"].shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]

        if safe_float(ema20, 0.0) <= 0 or safe_float(atr, 0.0) <= 0:
            return None, {}

        dist = (price - ema20) / ema20
        trigger = (atr / price) * self.current_params["trigger_mult"]

        recent_range = (bars["high"].tail(3).max() - bars["low"].tail(3).min()) / price
        if recent_range < self.current_params["vol_gate"]:
            return None, {}

        trend = "neutral"
        if price > ema50:
            trend = "up"
        elif price < ema50:
            trend = "down"

        last_close = safe_float(bars["close"].iloc[-1], 0.0)
        prev_close = safe_float(bars["close"].iloc[-2], 0.0)

        if trend == "up" and dist < -trigger:
            if last_close > prev_close:
                return "buy", {
                    "price": price, "dist": dist, "trigger": trigger, "trend": trend
                }

        if trend == "down" and dist > trigger:
            if last_close < prev_close:
                return "sell", {
                    "price": price, "dist": dist, "trigger": trigger, "trend": trend
                }

        return None, {}

    # =========================
    # EXECUTION / EXITS
    # =========================
    def get_qty(self, price, symbol):
        dollars = min(self.base_risk_dollars, self.max_position_dollars)
        dollars *= self.symbol_multiplier(symbol)
        return max(int(dollars / max(price, 0.01)), 1)

    def close_symbol(self, sym, reason):
        pos_map = {p.symbol: p for p in self.api.list_positions()}
        if sym not in pos_map:
            return

        p = pos_map[sym]
        entry = safe_float(p.avg_entry_price, 0.0)
        curr = safe_float(p.current_price, 0.0)
        side_num = 1 if p.side == "long" else -1

        if entry <= 0 or curr <= 0:
            return

        pnl = ((curr - entry) * side_num) / entry
        entry_ts = datetime.fromtimestamp(self.entry_times.get(sym, time.time()), NY).isoformat()

        try:
            self.api.close_position(sym)
        except Exception as e:
            logging.error(f"CLOSE ERR {sym}: {e}")
            return

        self.log_trade(sym, p.side, entry, curr, pnl, reason, entry_ts)
        self.entry_times.pop(sym, None)
        logging.info(f"✅ {reason} EXIT: {sym} | PnL: {pnl:.3%}")

    def manage_exits(self):
        try:
            positions = self.api.list_positions()
        except Exception as e:
            logging.error(f"LIST POSITIONS ERR: {e}")
            return

        for p in positions:
            sym = p.symbol
            entry = safe_float(p.avg_entry_price, 0.0)
            curr = safe_float(p.current_price, 0.0)
            side_num = 1 if p.side == "long" else -1

            if entry <= 0 or curr <= 0:
                continue

            pnl = ((curr - entry) * side_num) / entry

            opened_at = self.entry_times.get(sym)
            if opened_at and (time.time() - opened_at) > self.max_hold_secs:
                self.close_symbol(sym, "TIME_EXIT")
                continue

            if pnl <= -self.stop_pct:
                self.close_symbol(sym, "STOP")

            elif pnl >= self.tp_pct:
                self.close_symbol(sym, "TP")

            elif pnl >= self.be_trigger:
                if (side_num == 1 and curr <= entry) or (side_num == -1 and curr >= entry):
                    self.close_symbol(sym, "BE")

    # =========================
    # MAIN LOOP
    # =========================
    def run(self):
        logging.info("ALPACA MICRO EDGE V3 RUNNING")

        while True:
            try:
                mode = self.session_mode()

                if mode == "SLEEP":
                    time.sleep(self.loop_sleep)
                    continue

                if mode == "DUMP":
                    try:
                        self.api.close_all_positions()
                    except Exception as e:
                        logging.error(f"DUMP ERR: {e}")
                    time.sleep(60)
                    continue

                today = str(now_ny().date())
                if now_ny().hour == 15 and now_ny().minute >= 50 and self.learned_day != today:
                    self.evaluate_params()
                    self.learned_day = today
                    self._save_state()

                if not self.daily_breaker_ok():
                    time.sleep(60)
                    continue

                self.manage_exits()

                positions = self.api.list_positions()
                position_symbols = {p.symbol for p in positions}
                pending = self.pending_symbols()

                if len(positions) >= self.max_open_positions:
                    time.sleep(self.loop_sleep)
                    continue

                for sym in self.universe:
                    if sym in position_symbols or sym in pending:
                        continue

                    if (time.time() - self.last_trade.get(sym, 0)) < self.cooldown_secs:
                        continue

                    if self.trade_count_today(sym) >= self.max_trades_per_symbol_day:
                        continue

                    signal, meta = self.detect_entry(sym)
                    if not signal:
                        continue

                    qty = self.get_qty(meta["price"], sym)

                    try:
                        self.api.submit_order(
                            symbol=sym,
                            qty=qty,
                            side=signal,
                            type="market",
                            time_in_force="day"
                        )
                    except Exception as e:
                        logging.error(f"ORDER ERR {sym}: {e}")
                        continue

                    self.last_trade[sym] = time.time()
                    self.entry_times[sym] = time.time()
                    self._save_state()

                    logging.info(
                        f"🚀 ENTRY {sym} | side={signal} | qty={qty} | "
                        f"price={meta['price']:.2f} | dist={meta['dist']:.3%} | "
                        f"trigger={meta['trigger']:.3%} | trend={meta['trend']} | "
                        f"params={self.current_params}"
                    )

                time.sleep(self.loop_sleep)

            except Exception as e:
                logging.error(f"SYSTEM ERR: {e}")
                time.sleep(max(5, self.loop_sleep))


if __name__ == "__main__":
    AlpacaMicroEdgeV3().run()
