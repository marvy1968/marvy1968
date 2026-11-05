import os
from dotenv import load_dotenv

load_dotenv(".env")
import time
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Any, Tuple

import numpy as np
import pandas as pd
from oandapyV20 import API
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.pricing as pricing
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.trades as trades

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logging.getLogger("oandapyV20").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


@dataclass
class BotConfig:
    instrument: str = "EUR_USD"

    # ===== HIGH-FREQUENCY TEST PROFILE =====
    tf: str = "M15"
    max_stale_secs: int = 2400  # M15: allow up to 40 min age
    pricing_heartbeat: bool = True  # log pricing heartbeat
    pricing_stale_secs: float = 10.0  # pricing tick staleness guard
    oanda_pause_secs: int = 60  # pause duration on lag/maintenance
    max_consecutive_stale: int = 3  # pause after N stale cycles
    loop_sleep: int = 10
    cooldown_seconds: int = 60          # shorter cooldown -> more trades

    # Trend gate (loosened slightly for more opportunities)
    adx_min: float = 18.0
    # --- Range entries (more trades in chop) ---
    adx_trend_strong: float = 30.0   # trend-only above this; range/hybrid below
    # --- Range module controls (safer mean reversion) ---
    adx_range_max: float = 24.0          # only fade when ADX is below this
    adx_dead_low: float = 24.0          # no-trade zone floor (24..adx_trend_strong)
    range_edge_atr_mult: float = 0.25    # how close to band counts as "edge"
    weak_break_atr_mult: float = 0.12    # allow only tiny pokes past band
    range_entry_atr_frac: float = 0.25   # how close to edge (in ATR) to fade
    range_allow: bool = True             # enable range module
    max_consecutive_api_errors: int = 6  # pause after N api errors
    api_error_pause_secs: int = 120  # pause when API errors spike




    # Daily Williams bias (NOT sole trigger anymore)
    k_vol: float = 0.6

    # Intraday breakout trigger (NEW)
    donchian_len: int = 10
    donchian_buffer_pips: float = 2.0    # tiny buffer to reduce false triggers

    # Risk controls
    max_dd_pct: float = 0.05
    daily_loss_pct: float = 0.02

    # Indicators
    atr_period: int = 14
    adx_period: int = 14

    # Risk sizing
    risk_pct_per_trade: float = 0.01     # 1% per trade (TEST)
    max_units: int = 500000
    min_units: int = 1000

    # Broker-side safety stop (also used for sizing)
    disaster_atr_mult: float = 2.8
    min_sl_pips: float = 10.0

    # Stealth trailing (internal)
    trail_atr_mult_trend: float = 2.0
    trail_atr_mult_range: float = 1.2
    trail_step_atr: float = 0.25
    breakeven_at_r: float = 1.0
    breakeven_buffer_pips: float = 0.2
    lock_profit_at_r: float = 2.0
    lock_profit_to_r: float = 0.8

class OandaHFDonchianBot:
    def __init__(self, cfg: BotConfig, token: str, account_id: str, env: str = "practice"):
        self.cfg = cfg
        self.account_id = str(account_id)
        self.client = API(access_token=token, environment=("live" if env == "live" else "practice"))
        self._client_request = self.client.request
        self.peak_equity = 0.0
        self.trade_peaks: Dict[str, float] = {}
        self.trade_risk = {}  # {trade_id: initial_risk_dist_price}

        self.day_key_utc: Optional[str] = None
        self.day_start_equity: float = 0.0

        self.last_entry_ts: float = 0.0
        self._loop_i = 0
        self._log_every = 30  # loops
        self._stale_hits = 0
        self._api_err_hits = 0

        logging.info(f"BOOT | env={env} instrument={self.cfg.instrument}")

    # ---------- Helpers ----------
    @staticmethod
    def _utc_day_key() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _pip_size(self) -> float:
        return 0.01 if self.cfg.instrument.endswith("_JPY") else 0.0001

    def _pip_value_per_unit_usd(self) -> float:
        # For EUR_USD: 1 pip per 1 unit ≈ $0.0001
        return 0.01 if self.cfg.instrument.endswith("_JPY") else 0.0001
    def classify_regime(self, adx: float) -> str:
        """
        Market regime detection:
          - trend: ADX >= adx_trend_strong
          - range: ADX <= adx_range_max
          - dead : between range_max and trend_strong
        """
        if adx >= self.cfg.adx_trend_strong:
            return "trend"
        if adx <= self.cfg.adx_range_max:
            return "range"
        return "dead"
    def _reset_daily_if_needed(self, nav: float):
        dk = self._utc_day_key()
        if dk != self.day_key_utc:
            self.day_key_utc = dk
            self.day_start_equity = nav
            logging.info(f"DAILY RESET | day_start_equity={nav:.2f}")

    # ---------- OANDA ----------

    def _oanda_request(self, endpoint, write: bool = False):
        """Wrapper around OANDA requests to survive 503/maintenance windows + add circuit breaker."""
        max_attempts = 30
        for attempt in range(1, max_attempts + 1):
            try:
                out = self._client_request(endpoint)
                # success -> reset api error streak
                self._api_err_hits = 0
                return out
            except Exception as e:
                msg = str(e).lower()

                # maintenance / 503 backoff (existing behavior)
                if "503" in msg or "maintenance" in msg:
                    wait = min(300, 5 * attempt)
                    logging.warning("OANDA maintenance/503 (attempt %d) -> sleep %ss", attempt, wait)
                    time.sleep(wait)
                    continue

                # other errors -> count + breaker
                self._api_err_hits = getattr(self, "_api_err_hits", 0) + 1
                lim = int(getattr(self.cfg, "max_consecutive_api_errors", 6))
                logging.warning("OANDA error (%d/%d): %s", self._api_err_hits, lim, e)

                if self._api_err_hits >= lim:
                    self._api_err_hits = 0
                    self._pause_for_api_errors(f"consecutive api errors >= {lim}")

                # short backoff
                time.sleep(min(10, attempt))
                continue

        raise RuntimeError("OANDA request unavailable after retries")

    def account_nav(self) -> float:
        # NAV fetch with retry/backoff for OANDA maintenance (503)
        for attempt in range(1, 31):
            try:
                r = accounts.AccountSummary(self.account_id)
                return float(self._oanda_request(r)["account"]["NAV"])
            except Exception as e:
                msg = str(e)
                if "503" in msg or "maintenance" in msg.lower():
                    wait = min(300, 5 * attempt)
                    logging.warning("OANDA maintenance/503 on NAV (attempt %d) -> sleep %ss", attempt, wait)
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError("NAV unavailable after retries")

    def open_trade(self) -> Optional[Dict[str, Any]]:
        """FIFO-safe: only ONE open trade for the instrument."""
        r = trades.OpenTrades(self.account_id)
        tlist = self._oanda_request(r).get("trades", [])
        tlist = [t for t in tlist if t.get("instrument") == self.cfg.instrument]
        if not tlist:
            return None
        tlist.sort(key=lambda x: int(x["id"]))  # oldest
        return tlist[0]

    def close_trade(self, trade_id: str, reason: str):
        logging.warning(f"CLOSE | trade={trade_id} reason={reason}")
        self._oanda_request(trades.TradeClose(self.account_id, tradeID=trade_id), write=True)
        self.trade_peaks.pop(trade_id, None)

    # ---------- Indicators / Signals ----------
    def close_all_trades(self) -> int:
        """Close every open trade (FIFO-friendly). Returns number of close requests sent."""
        try:
            r = trades.OpenTrades(self.account_id)
            self._oanda_request(r)
            ts = (r.response or {}).get("trades", [])
        except Exception as e:
            logging.exception("close_all_trades: failed to fetch open trades: %s", e)
            return 0
    
        n = 0
        for t in ts:
            tid = t.get("id")
            if not tid:
                continue
            try:
                self._oanda_request(trades.TradeClose(self.account_id, tradeID=tid), write=True)
                n += 1
                logging.warning("FLATTEN: requested close tradeID=%s", tid)
            except Exception as e:
                logging.exception("FLATTEN: close tradeID=%s failed: %s", tid, e)
        return n

    def _pause_for_lag(self, reason: str):
        import time
        pause = int(getattr(self.cfg, "oanda_pause_secs", 60))
        logging.warning("⏸️ PAUSE_FOR_LAG: %s | sleep=%ss", reason, pause)
        time.sleep(max(1, pause))

    def get_market_data(self) -> Dict[str, float]:
        r = instruments.InstrumentsCandles(
            self.cfg.instrument,
            {"count": 400, "granularity": self.cfg.tf, "price": "M"}
        )
        c = self._oanda_request(r)["candles"]
        rows = [{"t": x["time"], "h": float(x["mid"]["h"]), "l": float(x["mid"]["l"]), "c": float(x["mid"]["c"])}
        for x in c if x.get("complete")]
        df = pd.DataFrame(rows)

        if len(df) < max(80, self.cfg.donchian_len + 5):
            raise RuntimeError("Not enough candles to compute indicators.")

        prev_close = df["c"].shift(1)
        tr = np.maximum(df["h"] - df["l"], np.maximum((df["h"] - prev_close).abs(), (df["l"] - prev_close).abs()))
        df["tr"] = tr

        atr = df["tr"].rolling(self.cfg.atr_period).mean().iloc[-1]

        # Correct Wilder DM for ADX
        up_move = df["h"].diff()
        down_move = -df["l"].diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        atr_sm = df["tr"].rolling(self.cfg.adx_period).mean()
        plus_di = 100 * (pd.Series(plus_dm).rolling(self.cfg.adx_period).mean() / (atr_sm + 1e-12))
        minus_di = 100 * (pd.Series(minus_dm).rolling(self.cfg.adx_period).mean() / (atr_sm + 1e-12))
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12))
        adx = dx.rolling(self.cfg.adx_period).mean().iloc[-1]

        price = float(df["c"].iloc[-1])

 # Donchian channel (exclude current candle)
        n = self.cfg.donchian_len
        don_high = float(df["h"].iloc[-(n+1):-1].max())
        don_low = float(df["l"].iloc[-(n+1):-1].min())

        # Daily Williams gates -> directional bias
        d_req = instruments.InstrumentsCandles(self.cfg.instrument, {"count": 2, "granularity": "D", "price": "M"})
        d = self._oanda_request(d_req)["candles"]
        prev_day = d[0]["mid"]
        prev_close_d = float(prev_day["c"])
        prev_range = float(prev_day["h"]) - float(prev_day["l"])
        buy_gate = prev_close_d + (self.cfg.k_vol * prev_range)
        sell_gate = prev_close_d - (self.cfg.k_vol * prev_range)

        return {
            "atr": float(atr),
            "adx": float(adx),
            "price": price,
            "don_high": don_high,
            "don_low": don_low,
            "buy_gate": buy_gate,
            "sell_gate": sell_gate,
        }

    def bias_from_daily_gate(self, price: float, buy_gate: float, sell_gate: float) -> str:
        # bias helps direction but does not block all trades
        if price > buy_gate:
            return "bull"
        if price < sell_gate:
            return "bear"
        return "neutral"
    # ===== MODULE 2: REGIME CLASSIFIER =====
    def classify_regime(self, adx: float) -> str:
        """Market regime detection based on ADX thresholds."""
        if adx >= self.cfg.adx_trend_strong:
            return "trend"
        if adx <= self.cfg.adx_range_max:
            return "range"
        return "dead"
    # ===== /MODULE 2 =====
    # ===== MODULE 3: TREND ENTRY GATE =====
    def trend_entry_allowed(
        self,
        regime: str,
        bias: str,
        long_break: bool,
        short_break: bool,
    ) -> Optional[str]:
        """
        Centralized trend entry logic.
        Only allows Donchian trades in confirmed trend regimes.
        """
        if regime != "trend":
            return None

        if long_break and bias in ("bull", "neutral"):
            return "long"

        if short_break and bias in ("bear", "neutral"):
            return "short"

        return None
    # ===== /MODULE 3 =====
    # ---------- Risk ----------
    def equity_guard(self, nav: float) -> Tuple[bool, str]:
        self._reset_daily_if_needed(nav)

        if self.peak_equity <= 0:
            self.peak_equity = nav
        if nav > self.peak_equity:
            self.peak_equity = nav

        dd = (self.peak_equity - nav) / self.peak_equity if self.peak_equity > 0 else 0.0
        if dd >= self.cfg.max_dd_pct:
            return True, f"MAX_DD {dd:.2%}"

        day_pnl = (nav - self.day_start_equity) / self.day_start_equity if self.day_start_equity > 0 else 0.0
        if day_pnl <= -self.cfg.daily_loss_pct:
            return True, f"DAILY_LOSS {day_pnl:.2%}"

        return False, ""

    def units_for_risk(self, nav: float, atr: float) -> Tuple[int, float]:
        """
        Size units so SL hit ~= risk_pct_per_trade of equity.
        Stop distance is ATR-based (disaster stop).
        Returns: (units, sl_distance_price)
        """
        pip = self._pip_size()
        sl_dist_price = max(self.cfg.min_sl_pips * pip, atr * self.cfg.disaster_atr_mult)

        stop_pips = sl_dist_price / pip
        pip_value_per_unit = self._pip_value_per_unit_usd()

        risk_dollars = nav * self.cfg.risk_pct_per_trade
        denom = max(stop_pips * pip_value_per_unit, 1e-9)
        units = int(risk_dollars / denom)

        units = max(self.cfg.min_units, min(units, self.cfg.max_units))
        return units, sl_dist_price

    # ---------- Execution ----------
    def place_market_order_with_sl(self, units: int, ref_price: float, sl_dist_price: float):
        sl_price = (ref_price - sl_dist_price) if units > 0 else (ref_price + sl_dist_price)

        data = {
            "order": {
                "type": "MARKET",
                "instrument": self.cfg.instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": f"{sl_price:.5f}"},
            }
        }
        self._oanda_request(orders.OrderCreate(self.account_id, data=data), write=True)
        logging.info(f"ORDER | units={units} SL={sl_price:.5f}")

    # ---------- Stealth trailing ----------
    def handle_stealth_trail_dynamic(self, price: float, atr: float, regime: str):
        t = self.open_trade()
        if not t:
            self.trade_peaks.clear()
            return

        tid = t["id"]
        units = int(float(t["currentUnits"]))
        side = 1 if units > 0 else -1
        entry = float(t["price"])

        if tid not in self.trade_peaks:
            self.trade_peaks[tid] = price

        step = max(self._pip_size(), atr * self.cfg.trail_step_atr)
        if side == 1 and price - self.trade_peaks[tid] >= step:
            self.trade_peaks[tid] = price
        if side == -1 and self.trade_peaks[tid] - price >= step:
            self.trade_peaks[tid] = price

        peak = self.trade_peaks[tid]
        mult = self.cfg.trail_atr_mult_trend if regime == "trend" else self.cfg.trail_atr_mult_range
        trail = max(atr * mult, self._pip_size() * 3)

        r_mult = ((price - entry) / trail) if side == 1 else ((entry - price) / trail)

        trigger = (peak - trail) if side == 1 else (peak + trail)

        # Break-even
        be = entry + (self.cfg.breakeven_buffer_pips * self._pip_size() * side)
        if r_mult >= self.cfg.breakeven_at_r:
            trigger = max(trigger, be) if side == 1 else min(trigger, be)

        # Profit lock
        if r_mult >= self.cfg.lock_profit_at_r:
            lock = entry + (self.cfg.lock_profit_to_r * trail * side)
            trigger = max(trigger, lock) if side == 1 else min(trigger, lock)

        if (side == 1 and price <= trigger) or (side == -1 and price >= trigger):
            self.close_trade(tid, reason=f"STEALTH_TRAIL r={r_mult:.2f} regime={regime} trigger={trigger:.5f}")

    # ---------- Run ----------

    def handle_stealth_trail_menedez(self, price: float, atr: float, regime: str):
        """Dynamic internal trailing stop (ATR + R staging, never loosens)."""
        r = trades.OpenTrades(self.account_id)
        data = self._oanda_request(r)
        open_trades = data.get("trades", [])

        if not open_trades:
            self.trade_peaks.clear()
            self.trade_risk.clear()
            return

        for t in open_trades:
            tid = t["id"]
            units = float(t["currentUnits"])
            side = 1 if units > 0 else -1

            entry = float(t["price"])

            sl_price = None
            sl = t.get("stopLossOrder")
            if sl and "price" in sl:
                try:
                    sl_price = float(sl["price"])
                except Exception:
                    sl_price = None

            if tid not in self.trade_risk:
                if sl_price is not None:
                    self.trade_risk[tid] = abs(entry - sl_price)
                else:
                    self.trade_risk[tid] = max(atr * 1.2, 1e-6)

            risk = max(self.trade_risk.get(tid, 1e-6), 1e-6)

            if tid not in self.trade_peaks:
                self.trade_peaks[tid] = price

            peak = self.trade_peaks[tid]
            if side == 1 and price > peak:
                peak = price
            elif side == -1 and price < peak:
                peak = price
            self.trade_peaks[tid] = peak

            pnl = (price - entry) * side
            r_mult = pnl / risk

            trail_mult = self.cfg.trail_atr_mult_trend if regime == "trend" else self.cfg.trail_atr_mult_range
            trail_dist = max(atr * trail_mult, self._pip_size() * 5)

            trigger = (peak - trail_dist) if side == 1 else (peak + trail_dist)

            be = entry + (self.cfg.breakeven_buffer_pips * self._pip_size() * side)
            if r_mult >= self.cfg.breakeven_at_r:
                trigger = max(trigger, be) if side == 1 else min(trigger, be)

            if r_mult >= self.cfg.lock_profit_at_r:
                lock = entry + (self.cfg.lock_profit_to_r * risk * side)
                trigger = max(trigger, lock) if side == 1 else min(trigger, lock)

            if (side == 1 and price <= trigger) or (side == -1 and price >= trigger):
                logging.info(f"STEALTH_TRAIL | close {tid} r={r_mult:.2f} regime={regime} trigger={trigger:.5f}")
                self._oanda_request(trades.TradeClose(self.account_id, tradeID=tid), write=True)
                self.trade_peaks.pop(tid, None)
                self.trade_risk.pop(tid, None)

    def run(self):
        logging.info("HF DONCHIAN BOT ACTIVE (FIFO-safe, 1% risk TEST, more trades)")
        while True:
            try:
                nav = self.account_nav()

                # ===== EQUITY GUARD (STOP + FLATTEN) =====
                hit, why = self.equity_guard(nav)
                if hit:
                    logging.warning("🛑 EQUITY_GUARD TRIPPED: %s | nav=%.2f -> flatten + pause", why, nav)
                    try:
                        self.close_all_trades()
                    except Exception as e:
                        logging.exception("EQUITY_GUARD flatten failed: %s", e)
                    time.sleep(60)
                    continue
                # ==========================================

                # ===== HEARTBEAT =====
                now = time.time()
                last = getattr(self, "_hb_ts", 0.0)
                hb = float(getattr(self.cfg, "heartbeat_secs", 60))
                if now - last >= hb:
                    self._hb_ts = now
                    logging.info("💓 heartbeat | nav=%.2f", nav)
                # ===================

                halt, why = self.equity_guard(nav)
                if halt:
                    logging.critical(f"HALT | {why}")
                    break

                m = self.get_market_data()

                # ===== STALE DATA SKIP =====
                if (not m) or m.get("stale"):
                    time.sleep(self.cfg.loop_sleep)
                    continue
                # ===========================

                price = m["price"]
                atr = m["atr"]
                adx = m["adx"]

                regime = "trend" if adx >= getattr(self.cfg, "adx_trend_strong", self.cfg.adx_min) else "range"
                bias = self.bias_from_daily_gate(price, m["buy_gate"], m["sell_gate"])

                # Manage open position first
                if self.open_trade() is not None:
                    self.handle_stealth_trail_menedez(price, atr, regime)
                    time.sleep(self.cfg.loop_sleep)
                    continue

                # Cooldown gate (prevents rapid-fire re-entries)
                if time.time() - self.last_entry_ts < self.cfg.cooldown_seconds:
                    time.sleep(self.cfg.loop_sleep)
                    continue
                # Intraday Donchian breakout trigger
                buf = self.cfg.donchian_buffer_pips * self._pip_size()
                long_break = price > (m["don_high"] + buf)
                short_break = price < (m["don_low"] - buf)

                trend_side = self.trend_entry_allowed(
                    regime, bias, long_break, short_break
                )

                # --- SAFE HYBRID DEAD-ZONE (no new entries in the middle) ---
                if getattr(self.cfg, "adx_dead_low", 24.0) <= adx < getattr(
                    self.cfg, "adx_trend_strong", self.cfg.adx_min
                ):
                    time.sleep(self.cfg.loop_sleep)
                    continue

                # --- risk sizing (used by BOTH trend + range modules) ---
                units, sl_dist = self.units_for_risk(nav, atr)

                # ===== MODULE 4: TREND EXECUTION (Donchian entries) =====
                if trend_side == "long":
                    logging.info(
                        f"DONCHIAN BUY | units={units} ADX={adx:.1f} bias={bias}"
                    )
                    self.place_market_order_with_sl(units, price, sl_dist)
                    self.last_entry_ts = time.time()
                    time.sleep(self.cfg.loop_sleep)
                    continue

                if trend_side == "short":
                    logging.info(
                        f"DONCHIAN SELL | units={-units} ADX={adx:.1f} bias={bias}"
                    )
                    self.place_market_order_with_sl(-units, price, sl_dist)
                    self.last_entry_ts = time.time()
                    time.sleep(self.cfg.loop_sleep)
                    continue
                # ===== /MODULE 4 =====
                in_cd = (time.time() - self.last_entry_ts) < self.cfg.cooldown_seconds
                # --- source-of-truth heartbeat (uses entry-gate variables) ---
                long_trigger = (m['don_high'] + buf)
                short_trigger = (m['don_low'] - buf)
                d_long = long_trigger - price
                d_short = price - short_trigger
                logging.info(
                    "HEARTBEAT | price=%.5f adx=%.1f regime=%s donH=%.5f donL=%.5f buf=%.5f "
                    "LT=%.5f ST=%.5f dL=%.5f dS=%.5f bias=%s long=%s short=%s cooldown=%s"
                    % (price, adx, regime, m['don_high'], m['don_low'], buf, long_trigger, short_trigger, d_long, d_short,
                       bias, long_break, short_break, in_cd)
                )
                self._loop_i += 1
                # --- Range module (smart) ---
                # Trade mean-reversion ONLY in low ADX AND only near channel edges.
                if getattr(self.cfg, "range_allow", True) and adx < getattr(self.cfg, "adx_range_max", self.cfg.adx_min):
                    edge = getattr(self.cfg, "range_edge_atr_mult", 0.35) * max(atr, 1e-6)
                    near_low  = price <= (m["don_low"]  + edge)
                    near_high = price >= (m["don_high"] - edge)
                    # --- RANGE CHECK (debug) ---
                    # throttled debug so we can tune edge/weak without spam 
                    if self._loop_i % max(getattr(self, "_log_every", 10), 10) == 0:
                        weak_dbg = getattr(self.cfg, "weak_break_atr_mult", 0.20) * max(atr, 1e-6)
                        too_far_below_dbg = price < (m["don_low"]  - weak_dbg)
                        too_far_above_dbg = price > (m["don_high"] + weak_dbg)
                        logging.info(
                            "RANGE CHECK | adx=%.1f atr=%.5f edge=%.5f weak=%.5f nearL=%s nearH=%s "
                            "tooLow=%s tooHigh=%s donL=%.5f donH=%.5f price=%.5f"
                            % (
                                adx, atr, edge, weak_dbg,
                                near_low, near_high,
                                too_far_below_dbg, too_far_above_dbg,
                                m["don_low"], m["don_high"], price
                            )
                        )
                    # Weak-break filter: allow tiny pokes past the band, but skip strong expansion.
                    weak = getattr(self.cfg, "weak_break_atr_mult", 0.20) * max(atr, 1e-6)
                    too_far_below = price < (m["don_low"]  - weak)
                    too_far_above = price > (m["don_high"] + weak)
                    if (too_far_below or too_far_above):
                        edge_dbg = edge
                        nearL_dbg = near_low
                        nearH_dbg = near_high
                        # skip dumb fades
                    else:
                        if near_low and bias in ("bull", "neutral"):
                            units, sl_dist = self.units_for_risk(nav, atr)
                            logging.info(f"RANGE BUY | units={units} ADX={adx:.1f} edge={edge:.5f}")
                            self.place_market_order_with_sl(units, price, sl_dist)
                            self.last_entry_ts = time.time()
                            time.sleep(self.cfg.loop_sleep)
                            continue

                        if near_high and bias in ("bear", "neutral"):
                            units, sl_dist = self.units_for_risk(nav, atr)
                            logging.info(f"RANGE SELL | units={-units} ADX={adx:.1f} edge={edge:.5f}")
                            self.place_market_order_with_sl(-units, price, sl_dist)
                            self.last_entry_ts = time.time()
                            time.sleep(self.cfg.loop_sleep)
                            continue
                        time.sleep(self.cfg.loop_sleep)
                        continue


                time.sleep(self.cfg.loop_sleep)

            except Exception as e:
                logging.error(f"RUNTIME ERROR: {e}", exc_info=True)
                time.sleep(10)

if __name__ == "__main__":
    account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
    token = os.environ.get("OANDA_API_TOKEN", "")
    env = os.environ.get("OANDA_ENV", "practice")

    if not account_id or not token:
        raise SystemExit(
            "Missing OANDA_ACCOUNT_ID / OANDA_API_TOKEN environment variables"
        )

    cfg = BotConfig()

    # --- optional overrides from .env ---
    cfg.max_stale_secs = int(os.getenv("MAX_STALE_SECS", str(cfg.max_stale_secs)))
    cfg.max_consecutive_stale = int(os.getenv("MAX_CONSEC_STALE", str(cfg.max_consecutive_stale)))
    cfg.oanda_pause_secs = int(os.getenv("OANDA_PAUSE_SECS", str(cfg.oanda_pause_secs)))
    cfg.pricing_stale_secs = float(os.getenv("PRICING_STALE_SECS", str(cfg.pricing_stale_secs)))
    cfg.pricing_heartbeat = os.getenv("PRICING_HEARTBEAT", "1").strip().lower() not in ("0","false","no")
    cfg.max_consecutive_api_errors = int(os.getenv("MAX_CONSEC_API_ERRORS", str(cfg.max_consecutive_api_errors)))
    cfg.api_error_pause_secs = int(os.getenv("API_ERROR_PAUSE_SECS", str(cfg.api_error_pause_secs)))
    bot = OandaHFDonchianBot(cfg, token, account_id, env=env)
    bot.run()
