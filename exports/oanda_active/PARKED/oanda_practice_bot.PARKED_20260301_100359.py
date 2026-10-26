import os
import traceback
import math
from oandapyV20.endpoints import pricing
from dotenv import load_dotenv
load_dotenv()
import time
import logging
import faulthandler, signal
faulthandler.enable()
faulthandler.register(signal.SIGUSR1, all_threads=True)
from dataclasses import dataclass
from typing import Dict, Optional, Any, Tuple

import numpy as np
import pandas as pd
from oandapyV20 import API
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.pricing as pricing
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.trades as trades

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | %(message)s")
logging.getLogger("oandapyV20").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


@dataclass
class BotConfig:
    instrument: str = "EUR_USD"
    instruments: str = "EUR_USD,GBP_USD,USD_JPY"  # comma-separated
    risk_pct: float = 0.35  # default; overwritten by expert engine

    # ===== HIGH-FREQUENCY TEST PROFILE =====
    tf: str = "M5"
    loop_sleep: int = 10
    cooldown_seconds: int = 60          # shorter cooldown -> more trades

    # Trend gate (loosened slightly for more opportunities)
    adx_min: float = 18.0
    # --- Range entries (more trades in chop) ---
    adx_trend_strong: float = 26.0   # trend-only above this; range/hybrid below
    # --- Range module controls (safer mean reversion) ---
    adx_range_max: float = 24.0          # only fade when ADX is below this
    adx_dead_low: float = 25.8          # no-trade zone floor (24..adx_trend_strong)

    # ===== EXPERT ENGINE V2 (ROI↑, DD↓) =====
    expert_allow: bool = True

    # Minervini trend filter (only trade in direction of higher-timeframe alignment)
    minervini_allow: bool = True
    minervini_ema_fast: int = 50
    minervini_ema_slow: int = 200
    minervini_require_trend_for_trend_entries: bool = True  # block trend entries if bias != trend

    # Volatility squeeze filter (avoid chop; prefer squeeze->expansion)
    squeeze_allow: bool = True
    squeeze_bb_n: int = 20
    squeeze_bb_k: float = 2.0
    squeeze_kc_n: int = 20
    squeeze_kc_k: float = 1.5
    squeeze_require_for_trend_entries: bool = False  # start conservative later if needed

    # Confirm filter (avoid false breakouts)
    confirm_allow: bool = True
    confirm_min_break_pips:float = 0.3   # require break distance beyond trigger
    confirm_min_atr_mult: float = 0.05    # OR break distance >= 0.10 * ATR

    # Risk brain (adaptive risk throttling)
    risk_brain_allow: bool = True
    # --- APEX Brain (5 engines) ---
    brain_allow: bool = True
    brain_log: bool = True
    brain_min_conf: float = 55.0
    risk_base_pct: float = 0.015           # base risk in %
    risk_min_pct: float = 0.0075
    risk_max_pct: float = 0.02
    risk_loss_streak_step: float = 0.08   # reduce risk per consecutive loss
    risk_spread_penalty: float = 0.15     # reduce risk when spread elevated
    risk_adx_penalty_dead: float = 0.15   # reduce risk in “meh” ADX zones

    range_edge_atr_mult: float = 0.15    # how close to band counts as "edge"
    weak_break_atr_mult: float = 0.20    # allow only tiny pokes past band
    range_entry_atr_frac: float = 0.40   # how close to edge (in ATR) to fade
    range_allow: bool = True             # enable range module


    # Daily Williams bias (NOT sole trigger anymore)
    k_vol: float = 0.6

    # Intraday breakout trigger (NEW)
    donchian_len: int = 10
    donchian_buffer_pips: float = 2.0    # tiny buffer to reduce false triggers

    # Risk controls
    max_dd_pct: float = 0.06
    daily_loss_pct: float = 0.025
    tp_atr_mult: float = float(os.getenv('TP_ATR_MULT','2.4'))
    sl_atr_mult: float = float(os.getenv('SL_ATR_MULT','1.8'))
    max_spread_pips: float = float(os.getenv('MAX_SPREAD_PIPS','1.5'))
    max_open_trades: int = int(os.getenv('MAX_OPEN_TRADES','3'))

    # Indicators
    atr_period: int = 14
    adx_period: int = 14

    # Risk sizing
    risk_pct_per_trade: float = float(os.getenv('RISK_PCT_PER_TRADE','0.015'))  # from env
    max_units: int = int(os.getenv('MAX_UNITS','75000'))
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

    # ===========================
    # EXPERT ENGINE V2 HELPERS
    # ===========================
    def _ema(self, xs, n):
        if xs is None or len(xs) < n or n <= 1:
            return None
        k = 2.0 / (n + 1.0)
        ema = xs[0]
        for v in xs[1:]:
            ema = (v * k) + (ema * (1.0 - k))
        return float(ema)

    def _sma(self, xs, n):
        if xs is None or len(xs) < n or n <= 1:
            return None
        return float(sum(xs[-n:]) / n)

    def _std(self, xs, n):
        if xs is None or len(xs) < n or n <= 1:
            return None
        m = self._sma(xs, n)
        var = sum((x - m) ** 2 for x in xs[-n:]) / n
        return float(math.sqrt(var))

    def _expert_bias_v2(self, closes):
        # returns: "bull", "bear", or "none"
        if not closes or len(closes) < max(getattr(self.cfg,'minervini_ema_fast',50), getattr(self.cfg,'minervini_ema_slow',200)) + 5:
            return "none"
        ef = self._ema(closes[-(getattr(self.cfg,'minervini_ema_fast',50)+5):], getattr(self.cfg,'minervini_ema_fast',50))
        es = self._ema(closes[-(getattr(self.cfg,'minervini_ema_slow',200)+5):], getattr(self.cfg,'minervini_ema_slow',200))
        if ef is None or es is None:
            return "none"
        if ef > es:
            return "bull"
        if ef < es:
            return "bear"
        return "none"

        # True when BB width < KC width (squeeze on)
        n_bb = int(getattr(self.cfg,'squeeze_bb_n',20))
        k_bb = float(getattr(self.cfg,'squeeze_bb_k',2.0))
        n_kc = int(getattr(self.cfg,'squeeze_kc_n',20))
        k_kc = float(getattr(self.cfg,'squeeze_kc_k',1.5))
        if not closes or len(closes) < max(n_bb, n_kc) + 5:
            return None
        sma = self._sma(closes, n_bb)
        sd  = self._std(closes, n_bb)
        if sma is None or sd is None:
            return None
        bb_up = sma + k_bb * sd
        bb_dn = sma - k_bb * sd
        bb_w = bb_up - bb_dn
        # KC approx: SMA +/- k * ATRproxy; use avg abs returns as ATR proxy if real ATR not available
        rets = [abs(closes[i]-closes[i-1]) for i in range(1, len(closes))]
        atrp = self._sma(rets, n_kc) if rets else None
        if atrp is None:
            return None
        kc_w = (2.0 * k_kc * atrp)
        return bool(bb_w < kc_w)

    def _expert_confirm_v2(self, price, long_trigger, short_trigger, atr, pip_size):
        # returns tuple (ok_long, ok_short)
        min_pips = float(getattr(self.cfg,'confirm_min_break_pips',0.8))
        min_atr_mult = float(getattr(self.cfg,'confirm_min_atr_mult',0.10))
        # require break distance beyond trigger
        req_dist = max(min_pips * pip_size, (min_atr_mult * atr if atr else 0.0))
        ok_long  = (price - long_trigger) >= req_dist
        ok_short = (short_trigger - price) >= req_dist
        return ok_long, ok_short, req_dist

    def _expert_risk_pct_v2(self, spread_pips, adx):
        # adaptive risk: start from base, reduce for loss streak / high spread / meh ADX
        base = float(getattr(self.cfg,'risk_base_pct',0.50))
        rmin = float(getattr(self.cfg,'risk_min_pct',0.25))
        rmax = float(getattr(self.cfg,'risk_max_pct',1.00))
        step = float(getattr(self.cfg,'risk_loss_streak_step',0.10))
        sp_pen = float(getattr(self.cfg,'risk_spread_penalty',0.15))
        adx_pen = float(getattr(self.cfg,'risk_adx_penalty_dead',0.15))

        ls = int(getattr(self, 'loss_streak', 0) or 0)
        r = base - (ls * step)

        # spread penalty (assume 1.0 pip is “normal-ish”)
        if spread_pips is not None and spread_pips > 1.2:
            r -= sp_pen

        # ADX dead-ish zone penalty
        adx_dead_low = float(getattr(self.cfg,'adx_dead_low', 24.0))
        adx_trend_strong = float(getattr(self.cfg,'adx_trend_strong', 26.0))
        if adx is not None and adx_dead_low <= adx < adx_trend_strong:
            r -= adx_pen

        r = max(rmin, min(rmax, r))
        return r


class OandaHFDonchianBot:
    def __init__(self, account_id: str, token: str, env: str = "practice"):
        self.cfg = BotConfig()
        self.account_id = account_id
        self.client = API(access_token=token, environment=("live" if env == "live" else "practice"))

        self.peak_equity = 0.0
        self.trade_peaks: Dict[str, float] = {}
        self.trade_risk = {}  # {trade_id: initial_risk_dist_price}

        self.day_key_utc: Optional[str] = None
        self.day_start_equity: float = 0.0

        self.last_entry_ts: float = 0.0
        self._loop_i = 0
        self._log_every = 30  # loops

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

    def _reset_daily_if_needed(self, nav: float):
        dk = self._utc_day_key()
        if dk != self.day_key_utc:
            self.day_key_utc = dk
            self.day_start_equity = nav
            logging.info(f"DAILY RESET | day_start_equity={nav:.2f}")

    # ---------- OANDA ----------
    def account_nav(self) -> float:
        r = accounts.AccountSummary(self.account_id)
        return float(self.client.request(r)["account"]["NAV"])

    def open_trade(self) -> Optional[Dict[str, Any]]:
        """FIFO-safe: only ONE open trade for the instrument."""
        r = trades.OpenTrades(self.account_id)
        tlist = self.client.request(r).get("trades", [])
        tlist = [t for t in tlist if t.get("instrument") == self.cfg.instrument]
        if not tlist:
            return None
        tlist.sort(key=lambda x: int(x["id"]))  # oldest
        return tlist[0]

    def close_trade(self, trade_id: str, reason: str):
        logging.warning(f"CLOSE | trade={trade_id} reason={reason}")
        self.client.request(trades.TradeClose(self.account_id, tradeID=trade_id))
        self.trade_peaks.pop(trade_id, None)

    # ---------- Indicators / Signals ----------
    def get_market_data(self) -> Dict[str, float]:
        r = instruments.InstrumentsCandles(
            self.cfg.instrument,
            {"count": 400, "granularity": self.cfg.tf, "price": "M"}
        )
        c = self.client.request(r)["candles"]
        rows = [{"h": float(x["mid"]["h"]), "l": float(x["mid"]["l"]), "c": float(x["mid"]["c"])}
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
        d = self.client.request(d_req)["candles"]
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

    def _risk_pct_for_entry(self) -> float:
        # --- FORCE env clamp refresh (live) ---
        import os
        try:
            env_min = os.getenv('RISK_MIN_PCT', os.getenv('risk_min_pct'))
            env_max = os.getenv('RISK_MAX_PCT', os.getenv('risk_max_pct'))
            if env_min is not None:
                self.cfg.risk_min_pct = float(env_min)
            if env_max is not None:
                self.cfg.risk_max_pct = float(env_max)
            if getattr(self.cfg, 'risk_min_pct', 0) > getattr(self.cfg, 'risk_max_pct', 0):
                self.cfg.risk_min_pct, self.cfg.risk_max_pct = self.cfg.risk_max_pct, self.cfg.risk_min_pct
        except Exception:
            pass

        """Return risk pct for sizing.
        Preference:
          1) dynamic cfg.risk_pct (expert/brain sets this)
          2) env fixed risk_pct_per_trade
        Always clamped to [risk_min_pct, risk_max_pct].
        """
        # fixed fallback (env)
        base = float(getattr(self.cfg, "risk_pct_per_trade", 0.01))

        try:
            if getattr(self.cfg, "brain_allow", True) and getattr(self.cfg, "risk_brain_allow", True):
                # dynamic risk set by expert engine (you already update self.cfg.risk_pct in the loop)
                dyn = getattr(self.cfg, "risk_pct", None)
                if dyn is not None:
                    base = float(dyn)
        except Exception:
            pass

        # clamp
        mn = float(getattr(self.cfg, "risk_min_pct", base))
        mx = float(getattr(self.cfg, "risk_max_pct", base))
        if mn > mx:
            mn, mx = mx, mn
        return max(mn, min(base, mx))


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

        risk_pct = self._risk_pct_for_entry()
        risk_dollars = nav * risk_pct
        logging.info(f"RISK_USED | risk_pct={risk_pct:.4f} dyn_cfg_risk={float(getattr(self.cfg,'risk_pct',-1)):.4f} env_risk_pct_per_trade={float(getattr(self.cfg,'risk_pct_per_trade',-1)):.4f} nav={nav:.2f} risk_dollars={risk_dollars:.2f}")
        denom = max(stop_pips * pip_value_per_unit, 1e-9)
        units = int(risk_dollars / denom)

        units = max(self.cfg.min_units, min(units, self.cfg.max_units))
        return units, sl_dist_price

    # ---------- Execution ----------
    def place_market_order_with_sl(self, units: int, ref_price: float, sl_dist_price: float):
        sl_price = (ref_price - sl_dist_price) if units > 0 else (ref_price + sl_dist_price)
        # --- Patch A3: define TP based on SL distance + config multipliers ---
        try:
            base_dist = sl_dist_price / max(getattr(self.cfg, 'sl_atr_mult', 1.0), 1e-9)
            tp_dist_price = base_dist * float(getattr(self.cfg, 'tp_atr_mult', 1.3))
        except Exception:
            tp_dist_price = sl_dist_price * 1.3
        tp_price = (ref_price + tp_dist_price) if units > 0 else (ref_price - tp_dist_price)
        logging.info(f"ENTRY_ATTEMPT | units={units} max_units={self.cfg.max_units} ref={ref_price:.5f} SL={sl_price:.5f} TP={tp_price:.5f}")
        # --- Patch A4: enforce max_open_trades + max_spread_pips at submit time ---
        try:
            if getattr(self.cfg, 'max_open_trades', 1) <= 0:
                logging.warning('GATE | max_open_trades<=0 (blocked)')
                return
            # if there is already an open trade, do not open another
            if self.open_trade() is not None and int(getattr(self.cfg, 'max_open_trades', 1)) <= 1:
                logging.info('GATE | max_open_trades reached (already open)')
                return
        except Exception:
            pass

        try:
            pip_size = 0.01 if ('_JPY' in self.cfg.instrument) else 0.0001
            r = pricing.PricingInfo(self.account_id, params={'instruments': getattr(self.cfg, 'instruments', self.cfg.instrument)})
            pd = self.client.request(r)
            pr = pd.get('prices', [])[0]
            bid = float(pr['bids'][0]['price'])
            ask = float(pr['asks'][0]['price'])
            spread_pips = abs(ask - bid) / pip_size
            if spread_pips > float(getattr(self.cfg, 'max_spread_pips', 1.2)):
                logging.info(f"GATE | spread too high {spread_pips:.2f}p > {self.cfg.max_spread_pips:.2f}p (skip order)")
                return
        except Exception as e:
            logging.warning(f"GATE | spread check failed (blocking order): {e}")
            return
        logging.info(f"ORDER_SUBMIT | units={units} bid={bid:.5f} ask={ask:.5f} spread_pips={spread_pips:.2f} SL={sl_price:.5f} TP={tp_price:.5f}")



        data = {
            "order": {
                "type": "MARKET",
                "instrument": self.cfg.instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": f"{sl_price:.5f}"},
                "takeProfitOnFill": {"price": f"{tp_price:.5f}"},
            }
        }
        self.client.request(orders.OrderCreate(self.account_id, data=data))
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
        data = self.client.request(r)
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
                self.client.request(trades.TradeClose(self.account_id, tradeID=tid))
                self.trade_peaks.pop(tid, None)
                self.trade_risk.pop(tid, None)


    # --- EXPERT v2 helper: bias detection (must be inside class) ---
    def _expert_bias_v2(self, closes):
        """
        Returns: 'bull', 'bear', 'flat', 'none'
        Safe: insufficient history -> 'none'
        """
        try:
            if closes is None:
                return "none"
            try:
                closes = list(closes)
            except Exception:
                return "none"
            closes = [float(x) for x in closes if x is not None]
            if len(closes) < 60:
                return "none"

            ef_len = int(getattr(self.cfg, "minervini_ema_fast", 20))
            es_len = int(getattr(self.cfg, "minervini_ema_slow", 50))
            flat_thresh = float(getattr(self.cfg, "minervini_flat_thresh", 0.0008))
            slope_n = int(getattr(self.cfg, "minervini_slope_n", 5))

            def ema(vals, n):
                if n <= 1 or len(vals) < n:
                    return None
                k = 2.0 / (n + 1.0)
                e = sum(vals[:n]) / float(n)
                for v in vals[n:]:
                    e = (v - e) * k + e
                return e

            ef = ema(closes, ef_len)
            es = ema(closes, es_len)
            if ef is None or es is None:
                return "none"

            last = closes[-1]
            if last <= 0:
                return "none"

            rel = (ef - es) / last

            # light slope sanity (don’t over-gate)
            if len(closes) >= max(es_len + slope_n + 5, 80):
                e2 = []
                for i in range(slope_n, 0, -1):
                    sub = closes[:-i]
                    es_i = ema(sub, es_len)
                    if es_i is not None:
                        e2.append(es_i)
                # if slope calc fails, just proceed
                # (no-op)

            if abs(rel) < flat_thresh:
                return "flat"
            if rel > 0:
                return "bull"
            if rel < 0:
                return "bear"
            return "flat"
        except Exception:
            return "none"

    # =========================
    # EXPERT v2 helper methods
    # =========================
    def _expert_sma(self, xs, n):
        try:
            if xs is None or n <= 0 or len(xs) < n:
                return None
            return sum(xs[-n:]) / float(n)
        except Exception:
            return None

    def _expert_std(self, xs, n):
        try:
            if xs is None or n <= 1 or len(xs) < n:
                return None
            import math
            seg = xs[-n:]
            mu = sum(seg) / float(n)
            var = sum((x - mu) ** 2 for x in seg) / float(n)
            return math.sqrt(var)
        except Exception:
            return None

    def _expert_ema(self, xs, n):
        try:
            if xs is None or n <= 1 or len(xs) < n:
                return None
            k = 2.0 / (n + 1.0)
            ema = xs[-n]
            for x in xs[-n+1:]:
                ema = (x * k) + (ema * (1.0 - k))
            return ema
        except Exception:
            return None

    def _expert_bias_v2(self, closes):
        """
        Returns one of: 'bull', 'bear', 'none'
        Safe: if insufficient history -> 'none'
        """
        try:
            if not closes:
                return "none"
            fast = int(getattr(self.cfg, 'minervini_ema_fast', 50))
            slow = int(getattr(self.cfg, 'minervini_ema_slow', 200))
            need = max(fast, slow) + 5
            if len(closes) < need:
                return "none"
            ef = self._expert_ema(closes[-(fast+5):], fast)
            es = self._expert_ema(closes[-(slow+5):], slow)
            if ef is None or es is None:
                return "none"
            if ef > es:
                return "bull"
            if ef < es:
                return "bear"
            return "none"
        except Exception:
            return "none"

        """
        True when BB width < KC width (squeeze on).
        Uses simple ATR proxy via average abs returns (no OHLC required).
        Returns: True/False or None if not enough data.
        """
        try:
            if not closes:
                return None
            n_bb = int(getattr(self.cfg, 'squeeze_bb_n', 20))
            k_bb = float(getattr(self.cfg, 'squeeze_bb_k', 2.0))
            n_kc = int(getattr(self.cfg, 'squeeze_kc_n', 20))
            k_kc = float(getattr(self.cfg, 'squeeze_kc_k', 1.5))
            need = max(n_bb, n_kc) + 5
            if len(closes) < need:
                return None

            sma = self._expert_sma(closes, n_bb)
            sd  = self._expert_std(closes, n_bb)
            if sma is None or sd is None:
                return None
            bb_w = (sma + k_bb * sd) - (sma - k_bb * sd)

            rets = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
            atrp = self._expert_sma(rets, n_kc) if rets else None
            if atrp is None:
                return None
            kc_w = 2.0 * k_kc * atrp
            return bool(bb_w < kc_w)
        except Exception:
            return None

    def _expert_confirm_v2(self, price, long_trigger, short_trigger, atr, pip_size):
        """
        Returns (ok_long, ok_short, req_dist)
        Confirms break beyond trigger by min(pips) and/or ATR fraction.
        """
        try:
            min_pips = float(getattr(self.cfg, 'confirm_min_break_pips', 0.8))
            min_atr_mult = float(getattr(self.cfg, 'confirm_min_atr_mult', 0.10))
            req_dist = max(min_pips * float(pip_size), (min_atr_mult * float(atr) if atr else 0.0))
            ok_long  = (float(price) - float(long_trigger)) >= req_dist
            ok_short = (float(short_trigger) - float(price)) >= req_dist
            return ok_long, ok_short, req_dist
        except Exception:
            return False, False, 0.0

    def _expert_risk_pct_v2(self, spread_pips, adx):
        """
        Risk brain v2 (GEAR-BASED):
          - Uses env gears + spread + ADX + loss streak
          - Never returns 0
          - Final clamp uses env RISK_MIN_PCT / RISK_MAX_PCT (or cfg fallbacks)
        """
        import os
        # --- startup guard: spread may be None until first LIVE_PRICE ---
        if spread_pips is None:
            spread_pips = getattr(self, "_last_spread_pips", None)
        if spread_pips is None:
            return float(os.getenv("RISK_GEAR_LOW", os.getenv("RISK_MIN_PCT", "0.003")))

        # --- read env thresholds ---
        # --- read env thresholds ---
        soft = float(os.getenv("SPREAD_SOFT_PIPS", "1.2"))
        hard = float(os.getenv("SPREAD_HARD_PIPS", "1.8"))
        adx_mid  = float(os.getenv("ADX_MID",  "30"))
        adx_high = float(os.getenv("ADX_HIGH", "45"))
        g_low  = float(os.getenv("RISK_GEAR_LOW",  "0.003"))
        g_mid  = float(os.getenv("RISK_GEAR_MID",  "0.008"))
        g_high = float(os.getenv("RISK_GEAR_HIGH", "0.015"))
        g_max  = float(os.getenv("RISK_GEAR_MAX",  "0.030"))
        # --- env clamp (authoritative) ---
        env_min = float(os.getenv("RISK_MIN_PCT", os.getenv("risk_min_pct", str(getattr(self.cfg, "risk_min_pct", 0.003)))))
        env_max = float(os.getenv("RISK_MAX_PCT", os.getenv("risk_max_pct", str(getattr(self.cfg, "risk_max_pct", 0.03)))))
        if env_min > env_max:
            env_min, env_max = env_max, env_min
        # Normalize inputs
        sp = float(spread_pips) if spread_pips is not None else None
        ax = float(adx) if adx is not None else 0.0
        # Loss streak penalty
        ls = int(getattr(self, 'loss_streak', 0) or 0)
        loss_penalty = 1.0
        if ls >= 2: loss_penalty = 0.85
        if ls >= 4: loss_penalty = 0.70
        if ls >= 6: loss_penalty = 0.55
        # --- decide gear ---
        if sp is None:
            base = g_low
        else:
            if sp >= hard:
                base = g_low
            else:
                if ax >= adx_high and sp <= soft:
                    base = g_max
                elif ax >= adx_high:
                    base = g_high
                elif ax >= adx_mid:
                    base = g_mid
                else:
                    base = g_low
                if sp > soft:
                    t = min(1.0, max(0.0, (sp - soft) / max(1e-9, (hard - soft))))
                    base *= (1.0 - 0.4 * t)
        base *= loss_penalty
        out = max(env_min, min(base, env_max))
        return float(out)
    def _apply_dynamic_tuning_v2(self, adx, regime, atr, spr_pips):
        """
        Dynamic trade throttling (V2):
        - Sets self._dyn_confirm_mult (confirmation distance multiplier)
        - Sets self._dyn_block (hard trade block)
        """
        # defaults
        self._dyn_confirm_mult = 1.0
        self._dyn_block = False
        
        # (optional debug)
        logging.info("EXPERT|DYNDBG adx=%s regime=%s atr=%s spr_pips=%s", adx, regime, atr, spr_pips)
        
        # --- Spread control (tighter; DD↓) ---
        if spr_pips is not None:
            # hard block when spreads are ugly
            if spr_pips >= 2.0:
                self._dyn_block = True
                return
            # scale confirmation distance as spreads worsen
            elif spr_pips >= 1.7:
                self._dyn_confirm_mult = max(self._dyn_confirm_mult, 1.35)
            elif spr_pips >= 1.5:
                self._dyn_confirm_mult = max(self._dyn_confirm_mult, 1.20)
            elif spr_pips >= 1.3:
                self._dyn_confirm_mult = max(self._dyn_confirm_mult, 1.10)
        
        # --- ADX / regime tuning ---
        if regime == "range":
            if adx is not None and adx < 20:
                self._dyn_confirm_mult *= 1.20
        elif regime == "trend":
            if adx is not None and adx > 30:
                self._dyn_confirm_mult *= 0.85
        
        # --- Volatility guard ---
        if atr is not None and atr <= 0:
            self._dyn_block = True
    def run(self):

        self._expert_v2_errs = getattr(self, '_expert_v2_errs', 0)
        self._expert_v2_disabled = getattr(self, '_expert_v2_disabled', False)

        logging.info("HF DONCHIAN BOT ACTIVE (FIFO-safe, dynamic cfg.risk_pct w/ env fallback)")
        while True:
            try:
                nav = self.account_nav()
        # --- FX market open gate ---
                if hasattr(self, "fx_market_open_ny_week") and not self.fx_market_open_ny_week():
                    logging.info("MARKET CLOSED | FX weekend gate active")
                    time.sleep(60)
                    continue
                halt, why = self.equity_guard(nav)
                if halt:
                    logging.critical(f"HALT | {why}")
                    break

                m = self.get_market_data()

                try:
                    bias = self.bias_from_daily_gate(float(m.get("price", 0.0)), m["buy_gate"], m["sell_gate"])
                except Exception:
                    bias = "neutral"
                adx = m["adx"]
                regime = "trend" if adx >= getattr(self.cfg, "adx_trend_strong", self.cfg.adx_min) else "range"
                edge_dbg = None
                nearL_dbg = False
                nearH_dbg = False


                price = m["price"]
                atr = m["atr"]
                adx = m["adx"]

                regime = "trend" if adx >= getattr(self.cfg, "adx_trend_strong", self.cfg.adx_min) else "range"
                # bias = self.bias_from_daily_gate(price, m["buy_gate"], m["sell_gate"])  # (disabled duplicate)
                # --- RAWBREAK (global per-loop probe) ---
                try:
                    buf_local = float(getattr(self.cfg, "donchian_buffer_pips", 0.0)) * float(self._pip_size())
                    donH = float(m.get("don_high", float("nan")))
                    donL = float(m.get("don_low",  float("nan")))
                    lt_rb = donH + buf_local
                    st_rb = donL - buf_local

                    rawL = (price > lt_rb) if (lt_rb == lt_rb) else None
                    rawS = (price < st_rb) if (st_rb == st_rb) else None

                    logging.info(
                        "EXPERT|RAWBREAK v2 price=%.5f lt=%.5f st=%.5f rawL=%s rawS=%s buf=%.5f regime=%s bias=%s",
                        price, lt_rb, st_rb, rawL, rawS, buf_local, regime, bias
                    )

                    logging.info("EXPERT|RISK_HB cfg.risk_pct=%.4f env_risk_pct_per_trade=%.4f cfg_min=%.4f cfg_max=%.4f ENV_MIN=%.4f ENV_MAX=%.4f", float(getattr(self.cfg,'risk_pct',-1)), float(getattr(self.cfg,'risk_pct_per_trade',-1)), float(getattr(self.cfg,'risk_min_pct',-1)), float(getattr(self.cfg,'risk_max_pct',-1)), float(os.getenv('RISK_MIN_PCT', os.getenv('risk_min_pct','-1'))), float(os.getenv('RISK_MAX_PCT', os.getenv('risk_max_pct','-1'))))
                except Exception:
                    logging.exception("EXPERT|RAWBREAK v2 error")
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

                raw_long_break = long_break
                raw_short_break = short_break
                raw_lt = (m['don_high'] + buf)
                raw_st = (m['don_low']  - buf)

                # --- SAFE HYBRID DEAD-ZONE (no new entries in the middle) ---
                if getattr(self.cfg, "adx_dead_low", 24.0) <= adx < getattr(self.cfg, "adx_trend_strong", self.cfg.adx_min):
                    logging.info(
                        "GATE | dead_zone_sleep adx=%.1f in [%.1f, %.1f) loop_sleep=%s" % (
                            adx,
                            float(getattr(self.cfg,'adx_dead_low',24.0)),
                            float(getattr(self.cfg,'adx_trend_strong', getattr(self.cfg,'adx_min', 25.0))),
                            self.cfg.loop_sleep,
                        )
                    )
                    time.sleep(self.cfg.loop_sleep)
                    continue

                # --- risk sizing (used by BOTH trend + range modules) ---
                # ===== EXPERT ENGINE V2 (runtime gates + adaptive risk) =====
                if getattr(self.cfg,'expert_allow',True):
                    try:
                        if getattr(self, '_expert_v2_disabled', False):
                            # v2 disabled due to repeated errors
                            bias2 = 'none'
                        else:
                            bias2 = self._expert_bias_v2(closes if 'closes' in locals() else None)
                # spread in pips (bid/ask preferred; cached fallback)
                        spr_pips = None
                        try:
                            pip_size = float(getattr(self, "pip_size", 0.0001) or 0.0001)
                            lb = locals().get("bid", None)
                            la = locals().get("ask", None)
                            if lb is None or la is None:
                                lb = getattr(self, "_last_bid", None)
                                la = getattr(self, "_last_ask", None)
                            if lb is not None and la is not None:
                                spr_pips = (float(la) - float(lb)) / pip_size
                        except Exception:
                            spr_pips = None

                        # fallback to cached helper if still missing
                        if spr_pips is None:
                            try:
                                spr_pips = self._get_spr_pips_v2()
                            except Exception:
                                spr_pips = None

                        # force from cached spread if still None (startup ordering)
                        if spr_pips is None:
                            spr_pips = getattr(self, "_last_spread_pips", None)

                                                # adaptive risk_pct (brain-controlled)
                        if getattr(self.cfg, "risk_brain_allow", True):
                            brain_in_spread = getattr(self, "_last_spread_pips", spr_pips)
                            brain_out = None
                            try:
                                brain_out = float(self._expert_risk_pct_v2(brain_in_spread, adx))
                                self.cfg.risk_pct = brain_out
                            except Exception as ex:
                                logging.exception("EXPERT|RISK v2 failed (fallback to MIN): %s", ex)
                                try:
                                    import os
                                    self.cfg.risk_pct = float(os.getenv("RISK_MIN_PCT", "0.003"))
                                except Exception:
                                    self.cfg.risk_pct = 0.003

                            logging.info(
                                "EXPERT|RISK v2 brain_out=%s cfg_risk=%.4f loss_streak=%s spread_in=%s spr_pips=%s adx=%.1f",
                                str(brain_out),
                                float(getattr(self.cfg, "risk_pct", 0.50)),
                                int(getattr(self, "_loss_streak", 0)),
                                str(brain_in_spread),
                                str(spr_pips),
                                float(adx) if adx is not None else -1.0
                            )

                        self._apply_dynamic_tuning_v2(adx, regime, atr, spr_pips)

                        logging.info(
                            "EXPERT|DYN v2 mult=%.2f block=%s",
                            float(getattr(self, "_dyn_confirm_mult", 1.0)),
                            bool(getattr(self, "_dyn_block", False)),
                        )

                        if getattr(self, "_dyn_block", False):
                            logging.info("EXPERT|DYN v2 HARD BLOCK → skipping entry")
                            time.sleep(self.cfg.loop_sleep)
                            continue

                        # confirm filter
                        okL=okS=True
                        req_dist=0.0
                        if getattr(self.cfg,'confirm_allow',True):
                            # --- FIX: define triggers safely before confirm (prevents UnboundLocalError) ---
                            try:
                                lt = (locals().get('long_trigger', None)
                                      or locals().get('LT', None)
                                      or locals().get('donH', None))
                                st = (locals().get('short_trigger', None)
                                      or locals().get('ST', None)
                                      or locals().get('donL', None))
                                if lt is None or st is None:
                                    okL, okS, req_dist = False, False, 0.0
                                    # --- PATCH: ensure confirm triggers exist (lt/st) before CONFIRM v2 ---
                                    try:
                                        if 'lt' not in locals() or lt is None:
                                            lt = (m['don_high'] + buf)
                                        if 'st' not in locals() or st is None:
                                            st = (m['don_low'] - buf)
                                    except Exception:
                                        lt, st = None, None
                                    logging.info("EXPERT|CONFIRM v2 skipped (missing triggers) lt=%s st=%s", lt, st)
                                    if getattr(self, "_dyn_block", False):
                                        logging.info("EXPERT|DYN v2 blocked (spread/volatility)")
                                        continue

                                else:
                                    okL, okS, req_dist = self._expert_confirm_v2(price, lt, st, atr, self._pip_size())
                                    req_dist *= getattr(self, "_dyn_confirm_mult", 1.0)

                            except Exception:
                                okL, okS, req_dist = False, False, 0.0
                                logging.exception("EXPERT|CONFIRM v2 exception")

                            logging.info("EXPERT|CONFIRM v2 req_dist=%.5f okL=%s okS=%s", float(req_dist), okL, okS)

                        # minervini gating for trend entries (do not block if bias2='none')
                        if getattr(self.cfg,'minervini_allow',True) and getattr(self.cfg,'minervini_require_trend_for_trend_entries',True):
                            if bias2 in ('bull','bear'):
                                logging.info("EXPERT|MINERVINI v2 bias=%s", bias2)
                            else:
                                logging.info("EXPERT|MINERVINI v2 bias=none (no block)")
                                # --- PATCH: bias2 fallback (avoid bias unknown=none) ---
                                try:
                                    if 'bias2' in locals() and (bias2 is None or str(bias2).lower() == 'none'):
                                        bias2 = bias
                                except Exception:
                                    pass

                        # compute squeeze flag safely
                        sq = None
                        try:
                            if getattr(self.cfg, "squeeze_allow", True):
                                sq = self._expert_squeeze_v2(closes if "closes" in locals() else None)
                        except Exception:
                            sq = None

                        # squeeze info
                        if getattr(self.cfg,'squeeze_allow',True):
                            logging.info("EXPERT|SQUEEZE v2 squeeze=%s", sq)
                        # Apply gating to triggers (trend module): STRICT (only when bias known) + confirm-safe
                        # - If bias2 is 'bull'/'bear', enforce direction
                        # - If bias2 is None/'none', do NOT block (unknown/neutral)
                        # - Confirm applies ONLY when lt/st exist (not None)
                        logging.info(
                            "EXPERT|GATECHK v2 bias2=%s bias=%s okL=%s okS=%s long_break=%s short_break=%s lt=%s st=%s",
                            locals().get('bias2', None),
                            locals().get('bias', None),
                            locals().get('okL', None),
                            locals().get('okS', None),
                            locals().get('long_break', None),
                            locals().get('short_break', None),
                            locals().get('lt', None),
                            locals().get('st', None),
                        )

                        # --- Entry direction gate ---
                        if "long_break" in locals() and "short_break" in locals():
                            _b2 = locals().get("bias2", None)
                            _bias_known = _b2 in ("bull", "bear")

                            try:
                                if long_break and short_break:
                                    # Prefer trend bias if known
                                    if _bias_known:
                                        if _b2 == 'bull':
                                            short_break = False
                                            logging.info("EXPERT|GATE v2 resolve double-trigger -> keep LONG (bias bull)")
                                        elif _b2 == 'bear':
                                            long_break = False
                                            logging.info("EXPERT|GATE v2 resolve double-trigger -> keep SHORT (bias bear)")
                                    else:
                                        # If bias unknown, prefer the side closer to its trigger (uses d_long/d_short if available)
                                        try:
                                            _lt = locals().get('lt', None)
                                            _st = locals().get('st', None)
                                            dl = abs(price - _lt) if (_lt is not None) else None
                                            ds = abs(price - _st) if (_st is not None) else None
                                            if (dl is not None) and (ds is not None):
                                                if dl <= ds:
                                                    short_break = False
                                                    logging.info("EXPERT|GATE v2 resolve double-trigger -> keep LONG (closer trigger)")
                                                else:
                                                    long_break = False
                                                    logging.info("EXPERT|GATE v2 resolve double-trigger -> keep SHORT (closer trigger)")
                                            else:
                                                # No distance info; safest is disable both
                                                long_break = False
                                                short_break = False
                                                logging.info("EXPERT|GATE v2 resolve double-trigger -> disable BOTH (no distance info)")
                                        except Exception:
                                            long_break = False
                                            short_break = False
                                            logging.info("EXPERT|GATE v2 resolve double-trigger -> disable BOTH (distance calc failed)")
                            except Exception:
                                logging.exception("EXPERT|ERROR v2 double-trigger guard")

                            # --- FIX: confirm applies ONLY to the active side ---
                            try:
                                if getattr(self.cfg,'confirm_allow',True):
                                    if long_break and (locals().get('okL', False) is not True):
                                        long_break = False
                                        logging.info("EXPERT|GATE v2 block_long (confirm)")
                                    if short_break and (locals().get('okS', False) is not True):
                                        short_break = False
                                        logging.info("EXPERT|GATE v2 block_short (confirm)")
                            except Exception:
                                logging.exception("EXPERT|ERROR v2 confirm gate")

                            if getattr(self.cfg,'minervini_allow',True) and _bias_known:
                                if long_break and _b2 != 'bull':
                                    long_break = False
                                    logging.info("EXPERT|GATE v2 block_long (need bull bias)")
                                if short_break and _b2 != 'bear':
                                    short_break = False
                                    logging.info("EXPERT|GATE v2 block_short (need bear bias)")
                            elif getattr(self.cfg,'minervini_allow',True) and not _bias_known:
                                logging.info("EXPERT|GATE v2 skip (bias unknown=%s)", _b2)
                        
                            # --- Confirm gate (safe) ---
                            if getattr(self.cfg,'confirm_allow',True):
                                _lt = locals().get('lt', None)
                                _st = locals().get('st', None)
                                if _lt is None or _st is None:
                                    logging.info("EXPERT|GATE v2 skip (confirm triggers missing lt/st)")
                                else:
                                    _okL = locals().get('okL', None)
                                    _okS = locals().get('okS', None)
                                    if (_okL is False) and long_break:
                                        long_break = False
                                        logging.info("EXPERT|GATE v2 block_long (confirm)")
                                    if (_okS is False) and short_break:
                                        short_break = False
                                        logging.info("EXPERT|GATE v2 block_short (confirm)")
                    except Exception:
                        logging.exception("EXPERT|ERROR v2")
                units, sl_dist = self.units_for_risk(nav, atr)
                in_cd = (time.time() - self.last_entry_ts) < self.cfg.cooldown_seconds
                # --- source-of-truth heartbeat (uses entry-gate variables) ---
                long_trigger = (m['don_high'] + buf)
                short_trigger = (m['don_low'] - buf)
                d_long = price - long_trigger
                d_short = price - short_trigger
                # --- Patch A10b: LIVE_PRICE debug (bid/ask/time) ---
                try:
                    r = pricing.PricingInfo(self.account_id, params={'instruments': getattr(self.cfg, 'instruments', self.cfg.instrument)})
                    pd = self.client.request(r)
                    pr = (pd.get('prices') or [None])[0]
                    if pr:
                        _bid = float(pr['bids'][0]['price'])
                        _ask = float(pr['asks'][0]['price'])
                        # --- FIX: SOURCE cache bid/ask + spread_pips (persists across loop order) ---
                        try:
                            self._last_bid = _bid
                            self._last_ask = _ask
                            pip_size = getattr(self, "pip_size", 0.0001) or 0.0001
                            self._last_spread_pips = ((_ask - _bid) / pip_size) if (_bid is not None and _ask is not None) else None
                        except Exception:
                            pass

                        _mid = (_bid + _ask) / 2.0
                        logging.info("LIVE_PRICE | bid=%.5f ask=%.5f mid=%.5f time=%s" % (_bid, _ask, _mid, pr.get('time','na')))

                        # --- FIX: use LIVE_PRICE mid as loop price (source-of-truth) ---
                        try:
                            price = float(_mid)
                            bid = float(_bid)
                            ask = float(_ask)
                        except Exception:
                            pass
                        # --- HARD BLOCK: stale ticks / bad spread (must CONTINUE) ---
                        try:
                            spr_pips = self._get_spr_pips_v2()
                            _t = pr.get("time", None)

                            if self._stale_spread_gate_v1(_t, spr_pips, bid=_bid, ask=_ask):
                                logging.warning(
                                    "EXPERT|STALE v1 tick_time=%s stale_ticks=%s spr_pips=%s bid=%s ask=%s",
                                    _t, getattr(self, "_stale_ticks", None), spr_pips, _bid, _ask
                                )
                                time.sleep(self.cfg.loop_sleep)
                                continue
                        except Exception:
                            pass
                        # --- FIX: cache latest bid/ask (supports _bid/_ask or bid/ask) ---
                        try:
                            lb = locals().get("_bid", None)
                            la = locals().get("_ask", None)
                            if lb is None or la is None:
                                lb = locals().get("bid", None)
                                la = locals().get("ask", None)
                            self._last_bid = lb
                            self._last_ask = la
                        except Exception:
                            pass
                        # --- FIX: cache latest bid/ask for later spread calc (scope-safe) ---
                        try:
                            self._last_bid = bid
                            self._last_ask = ask
                            pip_size = getattr(self, "pip_size", 0.0001) or 0.0001
                            if bid is not None and ask is not None:
                                self._last_spread_pips = (ask - bid) / pip_size
                            else:
                                self._last_spread_pips = None
                            # optional: show cache working (comment out if too chatty)
                            # logging.info("EXPERT|CACHE bid=%s ask=%s", self._last_bid, self._last_ask)
                        except Exception:
                            pass

                    else:
                        logging.warning("LIVE_PRICE | no prices returned")
                except Exception as ex:
                    logging.warning("LIVE_PRICE failed: %s" % (ex,))

                # --- TUNE: spread_pips from cached bid/ask (scope-safe) ---
                try:
                    pip_size = getattr(self, "pip_size", 0.0001) or 0.0001

                    _bid = getattr(self, "_last_bid", None)
                    _ask = getattr(self, "_last_ask", None)

                    if _bid is not None and _ask is not None:
                        spread_pips = (_ask - _bid) / pip_size
                    else:
                        # fallback if local vars exist
                        spread_pips = ((ask - bid) / pip_size) if ('bid' in locals() and 'ask' in locals() and bid is not None and ask is not None) else None
                except Exception:
                    spread_pips = None

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
                    if self._loop_i % max(getattr(self, '_log_every', 10), 10) == 0:
                        weak_dbg = getattr(self.cfg, 'weak_break_atr_mult', 0.20) * max(atr, 1e-6)
                        too_far_below_dbg = price < (m['don_low']  - weak_dbg)
                        too_far_above_dbg = price > (m['don_high'] + weak_dbg)
                        logging.info(
                            "RANGE CHECK | adx=%.1f atr=%.5f edge=%.5f weak=%.5f nearL=%s nearH=%s "
                            "tooLow=%s tooHigh=%s donL=%.5f donH=%.5f price=%.5f"
                            % (adx, atr, edge, weak_dbg, near_low, near_high,
                               too_far_below_dbg, too_far_above_dbg,
                               m['don_low'], m['don_high'], price)
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

                    if short_break and bias in ("bear", "neutral"):
                        logging.info(f"DONCHIAN SELL | units={-units} ADX={adx:.1f} bias={bias}")
                        self.place_market_order_with_sl(-units, price, sl_dist)
                        self.last_entry_ts = time.time()
                        time.sleep(self.cfg.loop_sleep)
                        continue

                time.sleep(self.cfg.loop_sleep)

            except Exception as e:
                logging.error(f"RUNTIME ERROR: {e}", exc_info=True)
                time.sleep(10)


# ===============================

# bind so self._get_spr_pips_v2 exists before bot starts running

# ===============================
# PATCH — spread helper (V2) [EARLY]
# ===============================
def _get_spr_pips_v2(self):
    """
    Returns spread in pips using best available cache.
    Priority:
      1) self._last_spread_pips
      2) (self._last_ask - self._last_bid) / pip_size
    """
    try:
        pip_size = getattr(self, "pip_size", None)
        if not pip_size:
            pip_size = getattr(self, "_pip_size", None)
        if callable(pip_size):
            pip_size = pip_size()
        pip_size = float(pip_size or 0.0001) or 0.0001

        sp = getattr(self, "_last_spread_pips", None)
        if sp is not None:
            return float(sp)

        lb = getattr(self, "_last_bid", None)
        la = getattr(self, "_last_ask", None)
        if lb is not None and la is not None:
            return (float(la) - float(lb)) / pip_size

        return None
    except Exception:
        return None

OandaHFDonchianBot._get_spr_pips_v2 = _get_spr_pips_v2






# ===============================
# PATCH — STALE TICK + SPREAD HARD GATE (V1)
# ===============================
def _stale_spread_gate_v1(self, tick_time, spr_pips, bid=None, ask=None):
    """
    Returns True to BLOCK trading when:
      - spread is too high, OR
      - tick_time AND quote are unchanged for several loops (stale feed)
    """
    try:
        if not hasattr(self, "_last_tick_time"):
            self._last_tick_time = None
            self._last_bid = None
            self._last_ask = None
            self._stale_ticks = 0

        same_time = (tick_time == self._last_tick_time)
        same_quote = (
            bid is not None and ask is not None and
            self._last_bid is not None and self._last_ask is not None and
            bid == self._last_bid and ask == self._last_ask
        )

        if same_time and same_quote:
            self._stale_ticks += 1
        else:
            self._stale_ticks = 0

        self._last_tick_time = tick_time
        if bid is not None:
            self._last_bid = bid
        if ask is not None:
            self._last_ask = ask

        # Spread hard gate
        if spr_pips is not None and spr_pips >= 2.0:
            return True

        # Stale hard gate (less trigger-happy than 2-3)
        if self._stale_ticks >= 5:
            return True

        return False
    except Exception:
        return False

OandaHFDonchianBot._stale_spread_gate_v1 = _stale_spread_gate_v1

if __name__ == "__main__":
    account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
    token = os.environ.get("OANDA_API_TOKEN", "")
    env = os.environ.get("OANDA_ENV", "practice")
    if not account_id or not token:
        raise SystemExit("Missing OANDA_ACCOUNT_ID / OANDA_API_TOKEN environment variables.")
    bot = OandaHFDonchianBot(account_id, token, env=env)
    bot.run()

# ===============================
# STEP 3 — DYNAMIC TUNING (V2)
# ===============================
# (removed duplicate top-level _apply_dynamic_tuning_v2)

# ===============================
# STEP 3 — DYNAMIC TUNING (V2)
# ===============================
# (removed duplicate top-level _apply_dynamic_tuning_v2)

# ===============================
# STEP 3 — DYNAMIC TUNING (V2)  (append-safe + bound to class)
# ===============================
# (removed duplicate top-level _apply_dynamic_tuning_v2)

# bind into bot class (so self._apply_dynamic_tuning_v2 always resolves)
# ===============================
# PATCH — spr_pips fallback helper (clean)
# ===============================
# (removed stray _get_spr_pips_v2 duplicate)

# ===============================
# PATCH — FX MARKET OPEN GATE
# ===============================
from datetime import datetime, timezone

def fx_market_open_ny_week(self) -> bool:
    """
    Returns True when FX market is open.
    Approx: Sunday 22:00 UTC -> Friday 22:00 UTC
    """
    now = datetime.now(timezone.utc)
    wd = now.weekday()  # Mon=0 ... Sun=6

    # Saturday = closed
    if wd == 5:
        return False

    # Sunday opens ~22:00 UTC
    if wd == 6:
        return now.hour >= 22

    # Friday closes ~22:00 UTC
    if wd == 4:
        return now.hour < 22

    return True

# bind to class
OandaHFDonchianBot.fx_market_open_ny_week = fx_market_open_ny_week


# ============================================================
# APEX-STYLE BRAIN + 5 ENGINES (SCAFFOLD)  [SAFE / OFF BY DEFAULT]
# ============================================================
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

@dataclass
class EngineSignal:
    name: str
    direction: str          # "long", "short", "none"
    confidence: float       # 0..100
    sl_dist: float          # price distance (not pips)
    reason: str
    meta: Dict[str, Any]

class BaseEngine:
    name = "base"
    def signal(self, bot, m, price, atr, adx, regime, bias) -> EngineSignal:
        return EngineSignal(self.name, "none", 0.0, 0.0, "base", {})

# -----------------------
# Engine 1: Donchian Breakout
# -----------------------
class DonchianBreakoutEngine(BaseEngine):
    name = "donchian_breakout"
    def signal(self, bot, m, price, atr, adx, regime, bias) -> EngineSignal:
        try:
            buf = float(getattr(bot.cfg, "donchian_buffer_pips", 0.0)) * float(bot._pip_size())
            lt = float(m["don_high"]) + buf
            st = float(m["don_low"])  - buf

            long_break  = price > lt
            short_break = price < st

            if not (long_break or short_break):
                return EngineSignal(self.name, "none", 0.0, 0.0, "no break", {"lt": lt, "st": st})

            # bias gate
            if long_break and bias not in ("bull", "neutral"):
                return EngineSignal(self.name, "none", 0.0, 0.0, "blocked by bias", {"bias": bias})
            if short_break and bias not in ("bear", "neutral"):
                return EngineSignal(self.name, "none", 0.0, 0.0, "blocked by bias", {"bias": bias})

            # confirm v2 gate if available (bot already computes okL/okS in main loop)
            okL = m.get("okL", None) if isinstance(m, dict) else None
            okS = m.get("okS", None) if isinstance(m, dict) else None
            # If confirm says "no", don’t fire
            if (okL is False) and long_break:
                return EngineSignal(self.name, "none", 0.0, 0.0, "confirm blocked long", {"okL": okL})
            if (okS is False) and short_break:
                return EngineSignal(self.name, "none", 0.0, 0.0, "confirm blocked short", {"okS": okS})            
            # confidence: stronger if trend regime + higher ADX
            conf = 55.0
            if regime == "trend": conf += 10.0
            if adx is not None:
                conf += max(0.0, min(20.0, (float(adx) - 20.0))) * 0.5

            # approximate SL distance from ATR
            sl_dist = max(float(getattr(bot.cfg, "min_sl_pips", 8)) * bot._pip_size(),
                          float(atr) * float(getattr(bot.cfg, "disaster_atr_mult", 1.0)))

            if long_break:
                return EngineSignal(self.name, "long", conf, sl_dist, "donchian break up", {"lt": lt, "st": st})
            return EngineSignal(self.name, "short", conf, sl_dist, "donchian break down", {"lt": lt, "st": st})
        except Exception as e:
            return EngineSignal(self.name, "none", 0.0, 0.0, f"err:{e}", {})

# -----------------------
# Engine 2: Range Fade (mean reversion near bands)
# -----------------------
class RangeFadeEngine(BaseEngine):
    name = "range_fade"
    def signal(self, bot, m, price, atr, adx, regime, bias) -> EngineSignal:
        try:
            if regime != "range":
                return EngineSignal(self.name, "none", 0.0, 0.0, "not range", {})
            edge = float(getattr(bot.cfg, "range_edge_atr_mult", 0.15)) * max(float(atr), 1e-6)
            near_low  = price <= (float(m["don_low"])  + edge)
            near_high = price >= (float(m["don_high"]) - edge)

            if near_low and bias in ("bull", "neutral"):
                sl_dist = max(float(getattr(bot.cfg, "min_sl_pips", 8)) * bot._pip_size(),
                              float(atr) * float(getattr(bot.cfg, "disaster_atr_mult", 1.0)))
                return EngineSignal(self.name, "long", 52.0, sl_dist, "fade near low", {"edge": edge})
            if near_high and bias in ("bear", "neutral"):
                sl_dist = max(float(getattr(bot.cfg, "min_sl_pips", 8)) * bot._pip_size(),
                              float(atr) * float(getattr(bot.cfg, "disaster_atr_mult", 1.0)))
                return EngineSignal(self.name, "short", 52.0, sl_dist, "fade near high", {"edge": edge})

            return EngineSignal(self.name, "none", 0.0, 0.0, "no edge", {"edge": edge})
        except Exception as e:
            return EngineSignal(self.name, "none", 0.0, 0.0, f"err:{e}", {})

# -----------------------
# Engine 3: Trend Continuation (simple scaffold)
# -----------------------
class TrendContinuationEngine(BaseEngine):
    name = "trend_continuation"
    def signal(self, bot, m, price, atr, adx, regime, bias) -> EngineSignal:
        try:
            if regime != "trend":
                return EngineSignal(self.name, "none", 0.0, 0.0, "not trend", {})
            # placeholder: follow bias only when ADX strong
            if adx is None or float(adx) < float(getattr(bot.cfg, "adx_trend_strong", 25)):
                return EngineSignal(self.name, "none", 0.0, 0.0, "adx not strong", {"adx": adx})

            sl_dist = max(float(getattr(bot.cfg, "min_sl_pips", 8)) * bot._pip_size(),
                          float(atr) * float(getattr(bot.cfg, "disaster_atr_mult", 1.0)))
            if bias == "bull":
                return EngineSignal(self.name, "long", 50.0, sl_dist, "trend follow bull", {})
            if bias == "bear":
                return EngineSignal(self.name, "short", 50.0, sl_dist, "trend follow bear", {})
            return EngineSignal(self.name, "none", 0.0, 0.0, "bias neutral", {})
        except Exception as e:
            return EngineSignal(self.name, "none", 0.0, 0.0, f"err:{e}", {})

# -----------------------
# Engine 4: Fakeout / Failed Break (scaffold)
# -----------------------
class FakeoutEngine(BaseEngine):
    name = "fakeout"
    def signal(self, bot, m, price, atr, adx, regime, bias) -> EngineSignal:
        # Safe placeholder (returns none until we implement wick/failed-break rules)
        return EngineSignal(self.name, "none", 0.0, 0.0, "stub", {})

# -----------------------
# Engine 5: Session / Time Filter (scaffold)
# -----------------------
class SessionEngine(BaseEngine):
    name = "session"
    def signal(self, bot, m, price, atr, adx, regime, bias) -> EngineSignal:
        # Safe placeholder (returns none until we implement NY/London logic)
        return EngineSignal(self.name, "none", 0.0, 0.0, "stub", {})

# ============================================================
# BRAIN (winner-take-one) + Virtual Stop plan (stub)
# ============================================================
class ApexBrain:
    def __init__(self, engines: List[BaseEngine]):
        self.engines = engines
        # virtual stop / R-plan config (matches your Alpaca idea)
        self.tp_1R_pct = 0.10
        self.tp_2R_pct = 0.50
        self.tp_3R_pct = 0.90

    def pick(self, bot, m, price, atr, adx, regime, bias) -> EngineSignal:
        signals = [e.signal(bot, m, price, atr, adx, regime, bias) for e in self.engines]
        # keep only actionable
        acts = [s for s in signals if s.direction in ("long", "short") and s.confidence > 0]
        if not acts:
            return EngineSignal("brain", "none", 0.0, 0.0, "no engine signals", {"signals": signals})
        # pick highest confidence
        acts.sort(key=lambda s: s.confidence, reverse=True)
        best = acts[0]
        return EngineSignal(best.name, best.direction, best.confidence, best.sl_dist, best.reason, best.meta)

def _brain_init_if_needed(self):
    if getattr(self, "_apex_brain", None) is None:
        self._apex_brain = ApexBrain([
            DonchianBreakoutEngine(),
            RangeFadeEngine(),
            TrendContinuationEngine(),
            FakeoutEngine(),
            SessionEngine(),
        ])
    return self._apex_brain

OandaHFDonchianBot._brain_init_if_needed = _brain_init_if_needed

