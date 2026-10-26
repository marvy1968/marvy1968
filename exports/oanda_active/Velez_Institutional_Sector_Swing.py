#!/usr/bin/env python3
"""
Institutional Velez Sector Swing Bot
- Top 10 stocks per S&P 500 sector
- Trending + Mean Reversion
- Full portfolio risk management
"""

import os
import json
import time
import logging
from datetime import datetime
from dataclasses import dataclass, asdict
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, Optional, List
from collections import defaultdict

# ==================== TOP 10 PER SECTOR ====================
SECTOR_TOP_STOCKS = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "CRM", "ADBE", "ORCL", "CSCO", "AMD", "INTC"],
    "Healthcare": ["UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY"],
    "Financials": ["BRK.B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "BLK", "AXP"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "TJX", "BKNG", "CMG", "F"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "CHTR", "EA", "TTWO"],
    "Industrials": ["GE", "CAT", "HON", "UPS", "BA", "RTX", "UNP", "LMT", "DE", "MMM"],
    "Consumer Staples": ["PG", "KO", "PEP", "COST", "WMT", "MO", "PM", "MDLZ", "CL", "KMB"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HAL"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PEG", "XEL", "ED"],
    "Real Estate": ["PLD", "AMT", "EQIX", "PSA", "O", "WELL", "AVB", "EQR", "SPG", "DLR"],
    "Materials": ["LIN", "APD", "SHW", "FCX", "NEM", "ECL", "DD", "PPG", "LYB", "NUE"]
}

ALL_SYMBOLS = [stock for sector in SECTOR_TOP_STOCKS.values() for stock in sector]

CONFIG = {
    "bot_name": "Velez_Institutional_Sector_Swing_v1",
    "max_concurrent_trades": 10,
    "max_trades_per_sector": 2,
    "risk_per_trade_pct": 0.008,
    "kelly_fraction": 0.5,
    "max_portfolio_risk": 0.05,
    "max_drawdown_pause": 0.08,
    "poll_interval_sec": 300,
    "log_dir": "./logs",
    "state_file": "./state/velez_institutional_sector.json",
    "params": {
        "ema_short": 8,
        "ema_long": 21,
        "atr_mult": 1.7,
        "target_rr": 2.0,
        "max_hold_bars": 180,
        "adx_trend_threshold": 22,
        "box_lookback": 30,
    }
}

@dataclass
class VirtualTrade:
    symbol: str
    side: str
    entry_price: float
    entry_time: str
    sl_price: float
    atr_at_entry: float
    risk_dist: float
    position_size: float
    sector: str = ""
    bars_held: int = 0
    peak_price: float = 0.0

class InstitutionalSectorSwingBot:
    def __init__(self, config):
        self.cfg = config
        self.params = config["params"]
        self.virtual_trades: Dict[str, VirtualTrade] = {}
        self.equity = 100000.0
        self.peak_equity = 100000.0
        self.recent_pnls = []
        self.sector_exposure = defaultdict(int)
        self.logger = self._setup_logging()
        self._load_shared_config()
        self._load_state()
        self._init_clients()
        self.symbols = ALL_SYMBOLS
        self.symbol_to_sector = {stock: sector for sector, stocks in SECTOR_TOP_STOCKS.items() for stock in stocks}
        self._load_shared_config()



    def _load_shared_config(self):
        config_path = Path("trading_config.json")
        if config_path.exists():
            try:
                with open(config_path) as f:
                    shared = json.load(f)
                if "risk_per_trade_pct" in shared:
                    self.cfg["risk_per_trade_pct"] = shared["risk_per_trade_pct"]
            except Exception as e:
                self.logger.warning(f"Shared config load error: {e}")


    def _setup_logging(self):
        os.makedirs(self.cfg["log_dir"], exist_ok=True)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
                            handlers=[logging.FileHandler(f"{self.cfg['log_dir']}/{self.cfg['bot_name']}.log"), logging.StreamHandler()])
        return logging.getLogger(self.cfg["bot_name"])

    def _init_clients(self):
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient
            self.trading_client = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET"), paper=True)
            self.data_client = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET"))
        except Exception as e:
            self.logger.warning(f"Client init: {e}")

    def get_equity(self):
        try:
            return float(self.trading_client.get_account().equity)
        except:
            return self.equity

    def _load_state(self):
        try:
            if os.path.exists(self.cfg["state_file"]):
                with open(self.cfg["state_file"]) as f:
                    for k, v in json.load(f).items():
                        self.virtual_trades[k] = VirtualTrade(**v)
        except:
            pass

    def _save_state(self):
        with open(self.cfg["state_file"], "w") as f:
            json.dump({k: asdict(v) for k, v in self.virtual_trades.items()}, f, indent=2)

    def is_trading_paused(self):
        if (self.peak_equity - self.equity) / self.peak_equity > self.cfg["max_drawdown_pause"]:
            self.logger.warning("Drawdown circuit breaker triggered")
            return True
        return False

    def fetch_bars(self, symbol, limit=200):
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, limit=limit)
            return self.data_client.get_stock_bars(req).df.reset_index().set_index("timestamp")
        except:
            return None

    def calculate_atr(self, df, p=14):
        tr = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()], 1).max(1)
        return tr.rolling(p).mean()

    def classify_regime(self, df):
        if len(df) < 50:
            return "unknown"
        adx = self.calculate_adx(df)
        bb_width = (df['close'].rolling(20).std() * 2) / df['close'].rolling(20).mean()
        if adx.iloc[-1] > self.params["adx_trend_threshold"] and bb_width.iloc[-1] > 0.08:
            return "trending"
        return "ranging"

    def calculate_adx(self, df, period=14):
        high = df['high']
        low = df['low']
        close = df['close']
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(period).mean()
        return adx

    def donchian_box(self, df, lb=30):
        return {
            "high": df['high'].rolling(lb).max().iloc[-1],
            "low": df['low'].rolling(lb).min().iloc[-1]
        }

    def calculate_position_size(self, risk_dist: float) -> float:
        equity = self.get_equity()
        base_risk = self.cfg.get("risk_per_trade_pct", 0.008) * self.cfg.get("kelly_fraction", 0.6)
        size = (equity * base_risk) / risk_dist if risk_dist > 0 else 0
        return max(1, int(size))

    def check_entry(self, df, symbol):
        if len(df) < 100 or self.is_trading_paused():
            return None

        regime = self.classify_regime(df)
        price = df['close'].iloc[-1]
        ema8 = df['close'].ewm(span=self.params["ema_short"], adjust=False).mean().iloc[-1]
        atr = self.calculate_atr(df).iloc[-1]
        sector = self.symbol_to_sector.get(symbol, "Unknown")

        if regime == "trending":
            if price > ema8:
                return {"side": "long", "entry": price, "atr": atr, "type": "trend", "sector": sector}
            elif price < ema8:
                return {"side": "short", "entry": price, "atr": atr, "type": "trend", "sector": sector}

        elif regime == "ranging":
            box = self.donchian_box(df)
            if price <= box["low"] * 1.005:
                return {"side": "long", "entry": price, "atr": atr, "type": "mean_reversion", "sector": sector}
            elif price >= box["high"] * 0.995:
                return {"side": "short", "entry": price, "atr": atr, "type": "mean_reversion", "sector": sector}

        return None

    def open_trade(self, sym, sig):
        sector = sig.get("sector", "Unknown")
        if self.sector_exposure[sector] >= self.cfg["max_trades_per_sector"]:
            return
        if len(self.virtual_trades) >= self.cfg["max_concurrent_trades"]:
            return
        if self.is_trading_paused():
            return

        rd = self.params["atr_mult"] * sig["atr"]
        size = self.calculate_position_size(rd)
        if size < 1:
            return

        sl = sig["entry"] - rd if sig["side"] == "long" else sig["entry"] + rd
        t = VirtualTrade(sym, sig["side"], sig["entry"], datetime.now().isoformat(), sl, sig["atr"], rd, size, sector=sector)
        self.virtual_trades[sym] = t
        self.sector_exposure[sector] += 1
        self._save_state()
        self.logger.info(f"INSTITUTIONAL OPEN {sig['side'].upper()} {sym} @ {sig['entry']:.2f} | {sig['type']} | Sector: {sector} | Size: {size}")

    def close_trade(self, sym, price, reason):
        t = self.virtual_trades.pop(sym)
        pnl = (price - t.entry_price) * (1 if t.side == "long" else -1) * t.position_size
        self.equity += pnl
        self.recent_pnls.append(pnl)
        if len(self.recent_pnls) > 20:
            self.recent_pnls.pop(0)
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        self.sector_exposure[t.sector] = max(0, self.sector_exposure[t.sector] - 1)
        self._save_state()
        self.logger.info(f"INSTITUTIONAL CLOSE {t.side.upper()} {sym} @ {price:.2f} | PnL ${pnl:+.2f} | {reason}")

    def run(self):
        self.logger.info("Institutional Sector Swing Bot Starting")
        while True:
            try:
                for sym in self.symbols:
                    if sym in self.virtual_trades:
                        df = self.fetch_bars(sym)
                        if df is not None:
                            price = df['close'].iloc[-1]
                            t = self.virtual_trades[sym]
                            if (t.side == "long" and price <= t.sl_price) or (t.side == "short" and price >= t.sl_price):
                                self.close_trade(sym, price, "stop_loss")
                        continue

                    if len(self.virtual_trades) >= self.cfg["max_concurrent_trades"]:
                        continue

                    df = self.fetch_bars(sym)
                    if df is not None and len(df) > 100:
                        sig = self.check_entry(df, sym)
                        if sig:
                            self.open_trade(sym, sig)

                self._save_state()
                time.sleep(self.cfg["poll_interval_sec"])
            except KeyboardInterrupt:
                break
            except Exception as e:
                self.logger.error(str(e))
                time.sleep(60)

if __name__ == "__main__":
    InstitutionalSectorSwingBot(CONFIG).run()
    def _load_shared_config(self):
        config_path = Path("trading_config.json")
        if config_path.exists():
            try:
                with open(config_path) as f:
                    shared = json.load(f)
                if "risk_per_trade_pct" in shared:
                    self.cfg["risk_per_trade_pct"] = shared["risk_per_trade_pct"]
            except Exception as e:
                self.logger.warning(f"Shared config load error: {e}")

    def check_exits(self, sym, price, df):
        t = self.virtual_trades[sym]
        t.bars_held += 1
        rr = abs(price - t.entry_price) / t.risk_dist if t.risk_dist > 0 else 0

        # Determine regime from trade type
        is_trending = getattr(t, "trade_type", "trend") == "trend"

        # === Hard Stop Loss (always active) ===
        if (t.side == "long" and price <= t.sl_price) or (t.side == "short" and price >= t.sl_price):
            return "hard_stop"

        # === Time Stop ===
        if t.bars_held > self.params.get("max_hold_bars", 180):
            return "time_stop"

        # === Adaptive Take Profit & Trailing ===
        if is_trending:
            # Trending → Let it run
            target_rr = self.params.get("target_rr", 2.5)
            trail_mult = 1.8
        else:
            # Ranging / Mean Reversion → Tighter
            target_rr = 1.8
            trail_mult = 1.0

        if rr >= target_rr:
            return f"tp_{target_rr}"

        # Breakeven + Adaptive Trailing
        if rr >= 1.0 and not hasattr(t, "breakeven_moved"):
            t.sl_price = t.entry_price
            t.breakeven_moved = True

        if rr >= 1.2:
            trail = self.calculate_atr(df).iloc[-1] * trail_mult
            if t.side == "long":
                t.sl_price = max(t.sl_price, price - trail)
            else:
                t.sl_price = min(t.sl_price, price + trail)

        # Regime change exit (for trending trades)
        if is_trending and hasattr(self, "classify_regime"):
            current_regime = self.classify_regime(df)
            if current_regime == "ranging":
                return "regime_change_exit"

        return None

    def calculate_position_size(self, risk_dist: float, regime: str = "unknown") -> float:
        equity = self.get_equity()
        base_risk = self.cfg.get("risk_per_trade_pct", 0.008) * self.cfg.get("kelly_fraction", 0.7)

        # Trend strength multiplier
        if regime == "trending":
            trend_mult = 1.25          # Stronger trend = bigger size
        elif regime == "ranging":
            trend_mult = 0.65          # Ranging = smaller size
        else:
            trend_mult = 1.0

        # Portfolio risk cap
        current_exposure = sum(t.position_size * t.risk_dist for t in self.virtual_trades.values())
        portfolio_risk_used = current_exposure / equity if equity > 0 else 0

        if portfolio_risk_used > self.cfg.get("max_portfolio_risk", 0.05):
            trend_mult *= 0.5

        final_risk = base_risk * trend_mult
        final_risk = min(final_risk, 0.02)  # Hard cap per trade

        risk_amount = equity * final_risk
        size = risk_amount / risk_dist if risk_dist > 0 else 0

        return max(1, int(size))

    def check_performance(self):
        if len(self.recent_pnls) < 10:
            return "normal"

        wins = [p for p in self.recent_pnls[-10:] if p > 0]
        win_rate = len(wins) / 10
        total_pnl = sum(self.recent_pnls[-10:])

        if win_rate < 0.4 or total_pnl < 0:
            return "poor"
        return "normal"

    def handle_pause(self):
        pause_file = Path("PAUSE_TRADING")
        if pause_file.exists():
            try:
                with open(pause_file) as f:
                    data = json.load(f)
                if datetime.now() < datetime.fromisoformat(data["until"]):
                    return True  # Still paused
            except:
                pass
        return False

    def trigger_pause(self, duration_minutes):
        pause_file = Path("PAUSE_TRADING")
        until = datetime.now() + timedelta(minutes=duration_minutes)
        with open(pause_file, "w") as f:
            json.dump({"until": until.isoformat(), "reason": "poor_performance"}, f)
        self.logger.warning(f"PAUSE TRIGGERED for {duration_minutes} minutes due to poor performance")

    def check_and_pause(self):
        if self.handle_pause():
            return True

        performance = self.check_performance()
        if performance == "poor":
            # Count how many times we've paused recently
            pause_count = getattr(self, "pause_count", 0) + 1
            self.pause_count = pause_count

            if pause_count == 1:
                self.trigger_pause(15)
            elif pause_count == 2:
                self.trigger_pause(480)  # 8 hours
            else:
                self.trigger_pause(999999)  # Until manually fixed
            return True
        return False
    def minervini_entry(self, df):
        """Mark Minervini style entry - Volatility Contraction + Pivot Breakout"""
        if len(df) < 60:
            return None

        price = df['close'].iloc[-1]
        vol = df['volume'].iloc[-1]
        avg_vol = df['volume'].rolling(20).mean().iloc[-1]

        # Volatility Contraction (tightening range)
        recent_high = df['high'].rolling(10).max().iloc[-1]
        recent_low = df['low'].rolling(10).min().iloc[-1]
        contraction = (recent_high - recent_low) / recent_low < 0.08  # Tight range

        # Pivot Breakout
        pivot_high = df['high'].rolling(10).max().iloc[-2]
        breakout = price > pivot_high * 1.005

        # Volume surge
        volume_ok = vol > avg_vol * 1.5

        if contraction and breakout and volume_ok:
            side = "long" if price > df['close'].rolling(20).mean().iloc[-1] else "short"
            atr = self.calculate_atr(df).iloc[-1]
            return {"side": side, "entry": price, "atr": atr, "type": "minervini_vcp"}

        return None

    def minervini_stop_logic(self, t, price, df):
        """Mark Minervini strict stop rules"""
        atr = self.calculate_atr(df).iloc[-1]

        # Initial tight stop protection
        if t.bars_held < 5:
            if t.side == "long" and price < t.entry_price * 0.98:
                return "minervini_tight_stop"
            if t.side == "short" and price > t.entry_price * 1.02:
                return "minervini_tight_stop"

        # Structure break stop
        recent_low = df['low'].rolling(10).min().iloc[-1]
        recent_high = df['high'].rolling(10).max().iloc[-1]

        if t.side == "long" and price < recent_low:
            return "structure_break"
        if t.side == "short" and price > recent_high:
            return "structure_break"

        # Volatility trailing after breakeven
        if hasattr(t, "breakeven_moved"):
            trail = atr * 1.8
            if t.side == "long":
                t.sl_price = max(t.sl_price, price - trail)
            else:
                t.sl_price = min(t.sl_price, price + trail)

        return None
    def volume_confirmation(self, df, threshold=1.5):
        """Strong volume confirmation (key Velez/Minervini filter)"""
        if len(df) < 30:
            return False

        current_vol = df['volume'].iloc[-1]
        avg_vol = df['volume'].rolling(20).mean().iloc[-1]

        return current_vol > avg_vol * threshold
    def relative_volume_rank(self, df, lookback=20):
        """
        Relative Volume Rank (0-100)
        100 = highest volume in last 20 bars
        """
        if len(df) < lookback + 5:
            return 50  # Neutral if not enough data

        current_vol = df['volume'].iloc[-1]
        recent_vols = df['volume'].iloc[-lookback-1:-1]

        rank = (recent_vols < current_vol).sum() / len(recent_vols) * 100
        return round(rank, 1)

    def is_high_relative_volume(self, df, min_rank=70):
        """Returns True if current volume is in top 30% of recent bars"""
        rank = self.relative_volume_rank(df)
        return rank >= min_rank
    def calculate_volume_profile(self, df, num_bins=30):
        """Simple Volume Profile - returns POC, Value Area High/Low"""
        if len(df) < 50:
            return None

        price_min = df['low'].min()
        price_max = df['high'].max()
        bin_size = (price_max - price_min) / num_bins

        # Volume by price bin
        bins = pd.cut(df['close'], bins=num_bins)
        vol_profile = df.groupby(bins)['volume'].sum()

        poc_price = vol_profile.idxmax().mid  # Point of Control

        # Value Area (70% of total volume)
        total_vol = vol_profile.sum()
        sorted_vol = vol_profile.sort_values(ascending=False)
        cumulative = sorted_vol.cumsum() / total_vol
        value_area = sorted_vol[cumulative <= 0.7]

        vah = value_area.index.max().mid
        val = value_area.index.min().mid

        return {
            "poc": poc_price,
            "vah": vah,
            "val": val,
            "profile": vol_profile
        }

    def is_near_value_area(self, df, price, tolerance=0.005):
        """Check if price is near Value Area or POC"""
        vp = self.calculate_volume_profile(df)
        if not vp:
            return False

        near_poc = abs(price - vp["poc"]) / vp["poc"] < tolerance
        near_val = abs(price - vp["val"]) / vp["val"] < tolerance
        near_vah = abs(price - vp["vah"]) / vp["vah"] < tolerance

        return near_poc or near_val or near_vah
    def visible_range_volume_profile(self, df, num_bins=50):
        """
        Visible Range Volume Profile
        Calculates volume at each price level across the entire df range
        """
        if len(df) < 30:
            return None

        price_min = df['low'].min()
        price_max = df['high'].max()
        if price_max == price_min:
            return None

        bin_size = (price_max - price_min) / num_bins
        price_bins = pd.cut(df['close'], bins=num_bins, labels=False)

        # Volume per bin
        vol_profile = df.groupby(price_bins)['volume'].sum()

        # Point of Control (highest volume price level)
        poc_bin = vol_profile.idxmax()
        poc_price = price_min + (poc_bin + 0.5) * bin_size

        # Value Area (70% of total volume)
        total_vol = vol_profile.sum()
        sorted_profile = vol_profile.sort_values(ascending=False)
        cumulative = sorted_profile.cumsum() / total_vol
        value_area_bins = sorted_profile[cumulative <= 0.70]

        if value_area_bins.empty:
            vah = val = poc_price
        else:
            vah_bin = value_area_bins.index.max()
            val_bin = value_area_bins.index.min()
            vah = price_min + (vah_bin + 1) * bin_size
            val = price_min + val_bin * bin_size

        return {
            "poc": round(poc_price, 4),
            "vah": round(vah, 4),
            "val": round(val, 4),
            "total_volume": int(total_vol)
        }

    def is_near_poc(self, df, price, tolerance=0.008):
        """Price near Point of Control"""
        vp = self.visible_range_volume_profile(df)
        if not vp:
            return False
        return abs(price - vp["poc"]) / vp["poc"] < tolerance

    def is_in_value_area(self, df, price):
        """Price inside Value Area"""
        vp = self.visible_range_volume_profile(df)
        if not vp:
            return False
        return vp["val"] <= price <= vp["vah"]

    def raschke_3bar_play(self, df):
        """Linda Raschke 3-Bar Play"""
        if len(df) < 10:
            return None
        # Simple 3-bar momentum pattern
        bar1 = df['close'].iloc[-3] > df['open'].iloc[-3]
        bar2 = df['close'].iloc[-2] > df['open'].iloc[-2]
        bar3 = df['close'].iloc[-1] > df['open'].iloc[-1]
        if bar1 and bar2 and bar3 and df['volume'].iloc[-1] > df['volume'].rolling(10).mean().iloc[-1]:
            return {"side": "long", "type": "raschke_3bar"}
        return None

    def anchored_vwap(self, df, anchor_bar=0):
        """Brian Shannon Anchored VWAP"""
        if len(df) < 20:
            return df['close'].iloc[-1]
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        vwap = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
        return vwap.iloc[-1]

    def shannon_entry(self, df):
        """Anchored VWAP + Volume entry"""
        vwap = self.anchored_vwap(df)
        price = df['close'].iloc[-1]
        if abs(price - vwap) / vwap < 0.005 and self.is_high_relative_volume(df):
            side = "long" if price > vwap else "short"
            return {"side": side, "type": "shannon_vwap"}
        return None

    def dynamic_trailing_stop(self, t, price, df):
        """Advanced dynamic trailing stop"""
        atr = self.calculate_atr(df).iloc[-1]
        bars_held = getattr(t, 'bars_held', 0)
        rr = abs(price - t.entry_price) / t.risk_dist if t.risk_dist > 0 else 0

        # Base trail
        trail = atr * 1.5

        # Regime adjustment
        if hasattr(self, 'classify_regime'):
            regime = self.classify_regime(df)
            if regime == "ranging":
                trail *= 0.7  # Tighter in ranging
            else:
                trail *= 1.3  # Wider in trending

        # Profit scaling (tighten as RR increases)
        if rr > 2.0:
            trail *= 0.6
        elif rr > 1.0:
            trail *= 0.85

        # Time-based tightening
        if bars_held > 30:
            trail *= 0.8

        # Apply trail
        if t.side == "long":
            new_sl = price - trail
            t.sl_price = max(t.sl_price, new_sl)
        else:
            new_sl = price + trail
            t.sl_price = min(t.sl_price, new_sl)

        return None

    def al_brooks_2nd_entry(self, df):
        """Al Brooks 2nd Entry Long/Short"""
        if len(df) < 15:
            return None

        # Simple 2nd entry logic
        # Look for pullback then 2nd push in trend direction
        close = df['close']
        high = df['high']
        low = df['low']

        # 2nd Entry Long (bullish)
        if (close.iloc[-3] < close.iloc[-2] and
            close.iloc[-2] < close.iloc[-1] and
            low.iloc[-2] > low.iloc[-3]):  # Higher low
            return {"side": "long", "type": "al_brooks_2nd_entry"}

        # 2nd Entry Short (bearish)
        if (close.iloc[-3] > close.iloc[-2] and
            close.iloc[-2] > close.iloc[-1] and
            high.iloc[-2] < high.iloc[-3]):  # Lower high
            return {"side": "short", "type": "al_brooks_2nd_entry"}

        return None

    def failed_2nd_entry(self, df):
        """Al Brooks Failed 2nd Entry (strong reversal)"""
        if len(df) < 15:
            return None

        close = df['close']
        # If 2nd entry fails quickly → strong reversal
        if self.al_brooks_2nd_entry(df) and close.iloc[-1] < close.iloc[-2]:
            return {"side": "short", "type": "al_brooks_failed_2nd"}
        if self.al_brooks_2nd_entry(df) and close.iloc[-1] > close.iloc[-2]:
            return {"side": "long", "type": "al_brooks_failed_2nd"}

        return None

    def count_bars(self, df, direction="up", max_bars=8):
        """Simple bar counting for Al Brooks style patterns"""
        if len(df) < max_bars:
            return 0

        count = 0
        if direction == "up":
            for i in range(1, max_bars):
                if df['close'].iloc[-i] > df['close'].iloc[-i-1]:
                    count += 1
                else:
                    break
        else:  # down
            for i in range(1, max_bars):
                if df['close'].iloc[-i] < df['close'].iloc[-i-1]:
                    count += 1
                else:
                    break
        return count

    def bar_count_entry(self, df):
        """Al Brooks style bar counting entry"""
        up_count = self.count_bars(df, "up", 5)
        down_count = self.count_bars(df, "down", 5)

        # 3-push pattern or exhaustion
        if up_count >= 3 and self.is_high_relative_volume(df):
            return {"side": "short", "type": "al_brooks_exhaustion_up"}
        if down_count >= 3 and self.is_high_relative_volume(df):
            return {"side": "long", "type": "al_brooks_exhaustion_down"}

        return None

    def one_two_three_pattern(self, df):
        """Classic 1-2-3 reversal pattern detection"""
        if len(df) < 15:
            return None

        close = df['close']
        high = df['high']
        low = df['low']

        # 1-2-3 Long (bullish reversal)
        if (low.iloc[-3] < low.iloc[-2] and          # Point 1 low
            high.iloc[-2] > high.iloc[-3] and        # Point 2 higher high
            low.iloc[-1] > low.iloc[-2] and          # Point 3 higher low
            close.iloc[-1] > close.iloc[-2]):        # Break above point 2
            return {"side": "long", "type": "1-2-3_long"}

        # 1-2-3 Short (bearish reversal)
        if (high.iloc[-3] > high.iloc[-2] and        # Point 1 high
            low.iloc[-2] < low.iloc[-3] and          # Point 2 lower low
            high.iloc[-1] < high.iloc[-2] and        # Point 3 lower high
            close.iloc[-1] < close.iloc[-2]):        # Break below point 2
            return {"side": "short", "type": "1-2-3_short"}

        return None

    def wyckoff_accumulation(self, df):
        """Basic Wyckoff Accumulation detection"""
        if len(df) < 40:
            return None

        close = df['close']
        volume = df['volume']
        low = df['low']
        high = df['high']

        # Simple Selling Climax detection
        recent_low = low.iloc[-20:].min()
        current_low = low.iloc[-1]
        vol_spike = volume.iloc[-1] > volume.rolling(20).mean().iloc[-1] * 2.5

        if current_low < recent_low * 0.97 and vol_spike:
            return {"phase": "Selling Climax", "type": "wyckoff_sc"}

        # Spring detection (shakeout)
        if current_low < recent_low and volume.iloc[-1] < volume.rolling(10).mean().iloc[-1]:
            return {"phase": "Spring", "type": "wyckoff_spring"}

        # Sign of Strength (strong move up)
        if close.iloc[-1] > close.iloc[-5] * 1.03 and volume.iloc[-1] > volume.rolling(10).mean().iloc[-1]:
            return {"phase": "Sign of Strength", "type": "wyckoff_sos"}

        return None

    def detect_lps(self, df):
        """Wyckoff Last Point of Support (LPS) detection"""
        if len(df) < 40:
            return None

        low = df['low']
        vol = df['volume']
        close = df['close']

        # Look for higher low on decreasing volume after a spring or test
        recent_low = low.iloc[-10:].min()
        previous_low = low.iloc[-30:-10].min()

        vol_decline = vol.iloc[-5:].mean() < vol.iloc[-30:-5].mean() * 0.75
        higher_low = recent_low > previous_low * 1.005

        if higher_low and vol_decline and close.iloc[-1] > close.iloc[-5]:
            return {"phase": "LPS", "type": "wyckoff_lps", "side": "long"}

        return None

    def toby_crabel_orb(self, df):
        """Toby Crabel Opening Range Breakout"""
        if len(df) < 10:
            return None
        open_range_high = df['high'].iloc[:5].max()
        open_range_low = df['low'].iloc[:5].min()
        price = df['close'].iloc[-1]
        if price > open_range_high * 1.005 and self.is_high_relative_volume(df):
            return {"side": "long", "type": "crabel_orb"}
        if price < open_range_low * 0.995 and self.is_high_relative_volume(df):
            return {"side": "short", "type": "crabel_orb"}
        return None

    def al_brooks_bar_by_bar(self, df):
        """Expanded Al Brooks bar-by-bar logic"""
        if len(df) < 10:
            return None
        # Failed 2nd entry or inside bar reversal
        if self.failed_2nd_entry(df):
            return self.failed_2nd_entry(df)
        return None

    def shannon_vwap_strategy(self, df, anchor_type="session"):
        """Brian Shannon style VWAP strategies"""
        if len(df) < 30:
            return None

        price = df['close'].iloc[-1]
        vwap = self.anchored_vwap(df)

        # VWAP Magnet + Trend
        distance_to_vwap = abs(price - vwap) / vwap

        if distance_to_vwap < 0.004 and self.is_high_relative_volume(df):
            # Price near VWAP with volume → potential continuation
            trend = "up" if price > df['close'].rolling(20).mean().iloc[-1] else "down"
            side = "long" if trend == "up" else "short"
            return {"side": side, "type": "shannon_vwap_magnet"}

        # VWAP Rejection (strong move away from VWAP)
        if distance_to_vwap > 0.015 and self.is_high_relative_volume(df):
            # Strong rejection from VWAP
            side = "short" if price < vwap else "long"
            return {"side": side, "type": "shannon_vwap_rejection"}

        return None

    def anchored_vwap(self, df, anchor="session"):
        """Multiple Anchored VWAPs (Brian Shannon style)"""
        if len(df) < 20:
            return df['close'].iloc[-1]

        typical_price = (df['high'] + df['low'] + df['close']) / 3

        if anchor == "session":
            # Standard session VWAP
            vwap = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
            return vwap.iloc[-1]

        elif anchor == "previous_day_high":
            # Anchor from previous day high
            idx = df['high'].idxmax()
            if idx == 0:
                return df['close'].iloc[-1]
            typical_price = (df['high'] + df['low'] + df['close']) / 3
            vwap = (typical_price.iloc[idx:] * df['volume'].iloc[idx:]).cumsum() / df['volume'].iloc[idx:].cumsum()
            return vwap.iloc[-1]

        elif anchor == "swing_low":
            # Anchor from recent swing low
            swing_low_idx = df['low'].rolling(10).min().idxmin()
            if swing_low_idx == 0:
                return df['close'].iloc[-1]
            typical_price = (df['high'] + df['low'] + df['close']) / 3
            vwap = (typical_price.iloc[swing_low_idx:] * df['volume'].iloc[swing_low_idx:]).cumsum() / df['volume'].iloc[swing_low_idx:].cumsum()
            return vwap.iloc[-1]

        return df['close'].iloc[-1]

    def multi_vwap_confluence(self, df, price):
        """Check confluence between multiple VWAPs"""
        vwap_session = self.anchored_vwap(df, "session")
        vwap_prev_high = self.anchored_vwap(df, "previous_day_high")
        vwap_swing = self.anchored_vwap(df, "swing_low")

        near_session = abs(price - vwap_session) / vwap_session < 0.005
        near_prev = abs(price - vwap_prev_high) / vwap_prev_high < 0.008
        near_swing = abs(price - vwap_swing) / vwap_swing < 0.008

        confluence_count = sum([near_session, near_prev, near_swing])
        return confluence_count >= 2

    def ross_cameron_momentum(self, df):
        """Ross Cameron momentum breakout"""
        if len(df) < 15:
            return None
        if (df['close'].iloc[-1] > df['high'].rolling(5).max().iloc[-2] and
            self.is_high_relative_volume(df)):
            return {"side": "long", "type": "ross_momentum"}
        return None

    def mean_reversion_signal(self, df):
        """Mean Reversion - Only in low trend"""
        if len(df) < 50:
            return None
        rsi = pandas_ta.rsi(df['close'], length=14).iloc[-1]
        bb_lower = pandas_ta.bbands(df['close'], length=20).iloc[-1]['BBL_20_2.0']
        adx = pandas_ta.adx(df['high'], df['low'], df['close'], length=14).iloc[-1]['ADX_14']

        if adx < 23 and df['close'].iloc[-1] <= bb_lower * 1.005 and rsi < 33:
            return {"side": "long", "type": "mean_reversion", "confidence": "high"}
        return None

    def mean_reversion_signal(self, df):
        """Hybrid Mean Reversion - only in low trend"""
        if len(df) < 50:
            return None
        # Use pandas_ta if available, fallback simple calculation
        try:
            rsi = pandas_ta.rsi(df['close'], length=14).iloc[-1]
            bb = pandas_ta.bbands(df['close'], length=20).iloc[-1]
            adx = pandas_ta.adx(df['high'], df['low'], df['close'], length=14).iloc[-1]['ADX_14']
        except:
            rsi = 50  # fallback
            adx = 25
            bb = {'BBL_20_2.0': df['close'].mean()}

        if adx < 23 and df['close'].iloc[-1] <= bb['BBL_20_2.0'] * 1.005 and rsi < 33:
            return {"side": "long", "type": "mean_reversion_hybrid"}
        return None
