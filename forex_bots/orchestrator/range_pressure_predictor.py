#!/usr/bin/env python3
import os, sys, json, logging
from datetime import datetime, timezone
import numpy as np
import pandas as pd

class RangePressurePredictor:
    def __init__(self, symbol, df_5m, df_1h=None, df_daily=None):
        self.symbol = symbol
        self.df_5m = df_5m.copy()
        self.df_1h = df_1h.copy() if df_1h is not None else None
        self.df_daily = df_daily.copy() if df_daily is not None else None
        
    @staticmethod
    def calculate_atr(df, period=14):
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        tr = np.maximum(high[1:] - low[1:], np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
        tr = np.insert(tr, 0, high[0] - low[0])
        return pd.Series(tr, index=df.index).rolling(period).mean().values

    def calculate_range_quality(self, lookback=24):
        df = self.df_5m.tail(lookback)
        if len(df) < lookback:
            return 0.0, 0.0, 0.0, 0.0

        close = df['close'].values
        high = df['high'].values
        low = df['low'].values

        range_high = float(np.max(high))
        range_low = float(np.min(low))
        range_span = range_high - range_low

        if range_span <= 0 or close[-1] <= 0:
            return 0.0, 0.0, 0.0, 0.0

        atr_5m = self.calculate_atr(self.df_5m, 14)
        curr_atr = atr_5m[-1]
        hist_atr_mean = float(np.mean(atr_5m[-100:])) if len(atr_5m) >= 100 else curr_atr
        atr_compression = max(0.0, min(1.0, 1.0 - (curr_atr / (hist_atr_mean + 1e-8))))

        sma20 = float(pd.Series(close).rolling(20).mean().values[-1])
        std20 = float(pd.Series(close).rolling(20).std().values[-1])
        bb_width = (4.0 * std20) / (sma20 + 1e-8)
        bb_compression = max(0.0, min(1.0, 1.0 - (bb_width * 12.0)))

        upper_touches = int(np.sum(high >= (range_high - 0.12 * range_span)))
        lower_touches = int(np.sum(low <= (range_low + 0.12 * range_span)))
        touch_score = min(1.0, (upper_touches + lower_touches) / 6.0)

        x = np.arange(len(close))
        r_sq = float(np.corrcoef(x, close)[0, 1] ** 2) if np.std(close) > 0 else 0.0
        drift_score = max(0.0, 1.0 - r_sq)

        quality_score = float(round((0.30 * touch_score + 0.30 * drift_score + 0.20 * atr_compression + 0.20 * bb_compression) * 100.0, 1))
        compression_score = float(round((0.50 * atr_compression + 0.50 * bb_compression) * 100.0, 1))

        return quality_score, compression_score, range_low, range_high

    def score_directional_pressure(self, range_low, range_high):
        df = self.df_5m.tail(30)
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        vol = df['volume'].values if 'volume' in df.columns else np.ones(len(df))

        htf_vector = 0.0
        if self.df_1h is not None and len(self.df_1h) >= 50:
            c_1h = self.df_1h['close'].values
            ema20 = float(pd.Series(c_1h).ewm(span=20).mean().values[-1])
            ema50 = float(pd.Series(c_1h).ewm(span=50).mean().values[-1])
            if c_1h[-1] > ema20 > ema50: htf_vector = 1.0
            elif c_1h[-1] < ema20 < ema50: htf_vector = -1.0

        clv = float(np.mean(((close - low) - (high - close)) / (high - low + 1e-8)))

        price_diff = np.diff(close, prepend=close[0])
        up_vol = float(np.sum(vol[price_diff > 0]))
        down_vol = float(np.sum(vol[price_diff < 0]))
        vol_delta = (up_vol - down_vol) / (up_vol + down_vol + 1e-8)

        low_slope = float(np.polyfit(np.arange(10), low[-10:], 1)[0])
        high_slope = float(np.polyfit(np.arange(10), high[-10:], 1)[0])
        geom_vector = 0.0
        if low_slope > 0 and abs(high_slope) < low_slope: geom_vector = 0.80
        elif high_slope < 0 and abs(low_slope) < abs(high_slope): geom_vector = -0.80

        range_span = range_high - range_low + 1e-8
        bins = np.linspace(range_low, range_high, 10)
        hist, bin_edges = np.histogram(close, bins=bins, weights=vol)
        poc_bin_idx = int(np.argmax(hist))
        poc_price = (bin_edges[poc_bin_idx] + bin_edges[poc_bin_idx + 1]) / 2.0
        poc_rel_pos = (poc_price - range_low) / range_span
        poc_vector = (poc_rel_pos - 0.5) * 2.0

        sma20_seq = pd.Series(close).rolling(10).mean().values
        dev_seq = close - sma20_seq
        dev_clean = dev_seq[~np.isnan(dev_seq)]
        mom_slope = float(np.polyfit(np.arange(len(dev_clean)), dev_clean, 1)[0]) if len(dev_clean) >= 5 else 0.0
        mom_vector = max(-1.0, min(1.0, mom_slope * 5.0))

        rs_vector = 0.0
        if self.df_daily is not None and len(self.df_daily) >= 5:
            d_close = self.df_daily['close'].values
            ret_5d = (d_close[-1] - d_close[-5]) / d_close[-5]
            rs_vector = max(-1.0, min(1.0, ret_5d * 20.0))

        composite_vector = (
            (0.25 * htf_vector) + (0.20 * vol_delta) + (0.15 * clv) +
            (0.15 * geom_vector) + (0.10 * poc_vector) + (0.10 * mom_vector) + (0.05 * rs_vector)
        )
        return float(round(max(-1.0, min(1.0, composite_vector)), 3))

    def calculate_multinomial_probabilities(self, compression_score, pressure_vector):
        if compression_score < 45.0:
            p_hold = 0.75
            p_up = round(0.125 + (pressure_vector * 0.10), 2)
            p_down = round(1.0 - p_hold - p_up, 2)
            return p_up, p_down, p_hold

        expansion_prob = min(0.88, 0.35 + (compression_score / 180.0))
        p_hold = round(1.0 - expansion_prob, 2)

        up_weight = float(np.exp(pressure_vector * 2.2))
        down_weight = float(np.exp(-pressure_vector * 2.2))
        total_w = up_weight + down_weight

        p_up = round(expansion_prob * (up_weight / total_w), 2)
        p_down = round(expansion_prob * (down_weight / total_w), 2)

        return p_up, p_down, p_hold

    def generate_advisory_contract(self):
        atr_5m = float(self.calculate_atr(self.df_5m, 14)[-1])
        quality_score, compression_score, range_low, range_high = self.calculate_range_quality()

        if range_low == 0.0 or range_high == 0.0: return None

        pressure_vector = self.score_directional_pressure(range_low, range_high)
        p_up, p_down, p_hold = self.calculate_multinomial_probabilities(compression_score, pressure_vector)

        range_span = range_high - range_low
        buffer = max(0.04 * range_span, 0.20 * atr_5m)

        virtual_long_trigger = round(range_high + buffer, 2)
        virtual_short_trigger = round(range_low - buffer, 2)

        upside_t1 = round(virtual_long_trigger + range_span, 2)
        upside_t2 = round(virtual_long_trigger + (1.6 * range_span), 2)
        downside_t1 = round(virtual_short_trigger - range_span, 2)
        downside_t2 = round(virtual_short_trigger - (1.6 * range_span), 2)

        invalidation_long = round(range_high - (0.30 * range_span), 2)
        invalidation_short = round(range_low + (0.30 * range_span), 2)

        regime = "TIGHT_COMPRESSION" if compression_score >= 68.0 and quality_score >= 60.0 else ("VALID_RANGE" if quality_score >= 45.0 else "LOOSE_CHOP")

        return {
            "symbol": self.symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "regime": regime,
            "range_low": round(range_low, 2),
            "range_high": round(range_high, 2),
            "range_quality_score": quality_score,
            "compression_score": compression_score,
            "directional_pressure_vector": pressure_vector,
            "probability_up": p_up,
            "probability_down": p_down,
            "probability_hold": p_hold,
            "virtual_long_trigger": virtual_long_trigger,
            "virtual_short_trigger": virtual_short_trigger,
            "upside_targets": [upside_t1, upside_t2],
            "downside_targets": [downside_t1, downside_t2],
            "invalidation_long": invalidation_long,
            "invalidation_short": invalidation_short,
            "false_break_risk": round(max(0.10, 1.0 - (quality_score / 100.0)), 2),
            "execution_allowed": False,
            "advisory_only": True
        }
