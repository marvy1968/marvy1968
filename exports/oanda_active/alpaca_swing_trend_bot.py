#!/usr/bin/env python3
"""
Alpaca Swing Trend Bot (Final Clean Version)
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
from typing import Dict, Optional

CONFIG = {
    "bot_name": "SwingTrend_Final_v2",
    "max_concurrent_trades": 6,
    "risk_per_trade_pct": 0.01,
    "poll_interval_sec": 300,
    "log_dir": "./logs",
    "state_file": "./state/swing_trades.json",
    "params": {
        "box_lookback": 40,
        "adx_range_thresh": 22,
        "entry_zone_pct": 0.12,
        "rsi_oversold": 35,
        "rsi_overbought": 65,
        "sl_atr_mult": 1.5,
        "target_rr": 2.0,
        "max_hold_bars": 120,
        "use_macd_confirmation": True,
    }
}

SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"]

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
    bars_held: int = 0
    peak_price: float = 0.0
    scaled: bool = False

class AlpacaSwingTrendBot:
    def __init__(self, config):
        self.cfg = config
        self.params = config["params"]
        self.virtual_trades: Dict[str, VirtualTrade] = {}
        self.equity = 100000.0
        self.recent_pnls = []
        self.peak_equity = 100000.0
        self.logger = self._setup_logging()
        self._load_shared_config()
        self._load_state()
        self._init_clients()
        self.symbols = SYMBOLS

    def _setup_logging(self):
        os.makedirs(self.cfg["log_dir"], exist_ok=True)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
                            handlers=[logging.FileHandler(f"{self.cfg['log_dir']}/{self.cfg['bot_name']}.log"), logging.StreamHandler()])
        return logging.getLogger(self.cfg["bot_name"])


    def _load_shared_config(self):
        """Load shared settings from trading_config.json if available. Explicit handling for missing file."""
        config_path = Path("trading_config.json")
        if not config_path.exists():
            if hasattr(self, "logger"):
                self.logger.info("trading_config.json not found. Using default settings.")
            return

        try:
            with open(config_path) as f:
                shared = json.load(f)

            updated = False
            if "risk_per_trade_pct" in shared:
                self.cfg["risk_per_trade_pct"] = shared["risk_per_trade_pct"]
                updated = True
            if "max_concurrent_trades" in shared:
                bot_name = self.cfg.get("bot_name", "").lower()
                for key in ["swing", "daytrade", "crypto"]:
                    if key in bot_name and key in shared["max_concurrent_trades"]:
                        self.cfg["max_concurrent_trades"] = shared["max_concurrent_trades"][key]
                        updated = True
                        break
            if hasattr(self, "logger"):
                self.logger.info(f"Loaded shared config (updated: {updated})")
        except Exception as e:
            if hasattr(self, "logger"):
                self.logger.error(f"Failed to load trading_config.json: {e}. Using defaults.")
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
        pause_file = Path("PAUSE_TRADING")
        if not pause_file.exists():
            return False
        try:
            with open(pause_file) as f:
                data = json.load(f)
                return datetime.now() < datetime.fromisoformat(data["pause_until"])
        except:
            return False

    def fetch_daily(self, symbol, limit=200):
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, limit=limit)
            return self.data_client.get_stock_bars(req).df.reset_index().set_index("timestamp")
        except:
            return None

    def fetch_bars(self, symbol, limit=100):
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, limit=limit)
            return self.data_client.get_stock_bars(req).df.reset_index().set_index("timestamp")
        except:
            return None

    def rsi(self, s, p=14):
        d = s.diff()
        g = d.where(d > 0, 0).rolling(p).mean()
        l = (-d.where(d < 0, 0)).rolling(p).mean()
        return (100 - 100 / (1 + g / l.replace(0, np.nan))).fillna(50)

    def calculate_atr(self, df, p=14):
        tr = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()], 1).max(1)
        return tr.rolling(p).mean()

    def macd(self, df):
        ef = df.close.ewm(12).mean()
        es = df.close.ewm(26).mean()
        ml = ef - es
        return ml, ml.ewm(9).mean()

    def vwap(self, df):
        typical = (df.high + df.low + df.close) / 3
        return (typical * df.volume).cumsum() / df.volume.cumsum()

    def relative_volume(self, df, period=20):
        return df.volume / df.volume.rolling(period).mean()

    def ema(self, series, period=9):
        return series.ewm(span=period, adjust=False).mean()

    def calculate_position_size(self, risk_dist: float) -> float:
        equity = self.get_equity()
        base_risk = self.cfg.get("risk_per_trade_pct", 0.01)

        vol_mult = 1.0
        try:
            df = self.fetch_daily(self.symbols[0], limit=25)
            if df is not None and len(df) > 10:
                current_atr = self.calculate_atr(df).iloc[-1]
                avg_atr = self.calculate_atr(df).rolling(15).mean().iloc[-1]
                if current_atr > avg_atr * 1.4:
                    vol_mult = 0.75
        except:
            pass

        perf_mult = 1.0
        if len(self.recent_pnls) >= 5:
            wr = sum(1 for p in self.recent_pnls[-5:] if p > 0) / 5
            if wr >= 0.7: perf_mult = 1.1
            elif wr <= 0.3: perf_mult = 0.8

        dd_mult = 1.0
        if equity < self.peak_equity:
            dd = (self.peak_equity - equity) / self.peak_equity
            if dd > 0.08: dd_mult = 0.65
            elif dd > 0.04: dd_mult = 0.85

        final_risk = base_risk * vol_mult * perf_mult * dd_mult
        final_risk = min(final_risk, 0.012)
        risk_amount = equity * final_risk
        size = risk_amount / risk_dist if risk_dist > 0 else 0
        return max(1, int(size))

    def detect_stage(self, df):
        price = df.close.iloc[-1]
        ema50 = df.close.ewm(span=50, adjust=False).mean().iloc[-1]
        ema200 = df.close.ewm(span=200, adjust=False).mean().iloc[-1]
        if price > ema50 > ema200:
            return "stage2_early"
        elif price < ema50 < ema200:
            return "stage4_early"
        return "other"

    def check_entry(self, df_daily: pd.DataFrame, df_hourly: pd.DataFrame = None):
        stage = self.detect_stage(df_daily)
        if stage not in ["stage2_early", "stage4_early"]:
            return None
        if self.is_trading_paused():
            return None

        df = df_hourly if df_hourly is not None else df_daily
        price = df['close'].iloc[-1]
        rsi_val = self.rsi(df['close']).iloc[-1] if hasattr(self, 'rsi') else 50
        atr = self.calculate_atr(df).iloc[-1]
        ml, sl = self.macd(df)
        rel_vol = self.relative_volume(df).iloc[-1] if hasattr(self, 'relative_volume') else 1.0
        vwap_val = self.vwap(df).iloc[-1] if hasattr(self, 'vwap') else price
        ema9 = self.ema(df['close'], 9).iloc[-1] if hasattr(self, 'ema') else price

        if stage == "stage2_early":
            breakout = price > df['high'].rolling(10).max().iloc[-2] * 1.005 and rel_vol > 1.2
            pullback = (price > vwap_val) and (price > ema9) and (rsi_val < 45)
            momentum = (ml.iloc[-1] > sl.iloc[-1] and ml.iloc[-2] <= sl.iloc[-2])
            if (breakout or pullback) and momentum:
                return {"side": "long", "entry": price, "atr": atr, "stage": stage}

        if stage == "stage4_early":
            breakout = price < df['low'].rolling(10).min().iloc[-2] * 0.995 and rel_vol > 1.2
            pullback = (price < vwap_val) and (price < ema9) and (rsi_val > 55)
            momentum = (ml.iloc[-1] < sl.iloc[-1] and ml.iloc[-2] >= sl.iloc[-2])
            if (breakout or pullback) and momentum:
                return {"side": "short", "entry": price, "atr": atr, "stage": stage}

        return None

    def check_exits(self, sym, price, df):
        t = self.virtual_trades[sym]
        t.bars_held += 1
        rr = abs(price - t.entry_price) / t.risk_dist if t.risk_dist > 0 else 0
        ml, sl = self.macd(df)
        atr_now = self.calculate_atr(df).iloc[-1]

        if (t.side == "long" and price <= t.sl_price) or (t.side == "short" and price >= t.sl_price):
            return "virtual_sl"
        if t.bars_held > self.params["max_hold_bars"]:
            return "time_stop"
        if rr >= self.params["target_rr"]:
            return f"tp_{self.params['target_rr']}"
        if rr >= 1.8 and not t.scaled:
            return "scale_out_50"
        if t.side == "long" and ml.iloc[-1] < sl.iloc[-1] and ml.iloc[-2] >= sl.iloc[-2]:
            return "reversal_exit"
        if t.side == "short" and ml.iloc[-1] > sl.iloc[-1] and ml.iloc[-2] <= sl.iloc[-2]:
            return "reversal_exit"
        return None

    def open_trade(self, sym, sig):
        if len(self.virtual_trades) >= self.cfg["max_concurrent_trades"]:
            return
        if self.is_trading_paused():
            return
        rd = self.params["sl_atr_mult"] * sig["atr"]
        size = self.calculate_position_size(rd)
        if size < 1:
            return
        sl = sig["entry"] - rd if sig["side"] == "long" else sig["entry"] + rd
        t = VirtualTrade(sym, sig["side"], sig["entry"], datetime.now().isoformat(), sl, sig["atr"], rd, size)
        self.virtual_trades[sym] = t
        self._save_state()
        self.logger.info(f"SWING OPEN {sig['side'].upper()} {sym} @ {sig['entry']:.2f} | Size: {size}")

    def close_trade(self, sym, price, reason):
        t = self.virtual_trades.pop(sym)
        pnl = (price - t.entry_price) * (1 if t.side == "long" else -1) * t.position_size
        self.equity += pnl
        self.recent_pnls.append(pnl)
        if len(self.recent_pnls) > 20:
            self.recent_pnls.pop(0)
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        self._save_state()
        self.logger.info(f"SWING CLOSE {t.side.upper()} {sym} @ {price:.2f} | PnL ${pnl:+.2f} | {reason}")

    def run(self):
        self.logger.info("Swing Trend Bot Starting")
        while True:
            try:
                for sym in self.symbols:
                    if sym in self.virtual_trades:
                        df = self.fetch_bars(sym)
                        if df is not None:
                            r = self.check_exits(sym, df.close.iloc[-1], df)
                            if r:
                                self.close_trade(sym, df.close.iloc[-1], r)
                        continue
                    if len(self.virtual_trades) >= self.cfg["max_concurrent_trades"]:
                        continue
                    df_daily = self.fetch_daily(sym, limit=200)
                    df_hourly = self.fetch_bars(sym, limit=100)
                    if df_daily is not None:
                        sig = self.check_entry(df_daily, df_hourly)
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
    AlpacaSwingTrendBot(CONFIG).run()
