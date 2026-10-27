"""Al Brooks, *Trading Price Action Trading Ranges* (Wiley 2012).
BOOK principles (Brooks: "most breakout attempts from a trading range fail"; "buy low, sell high";
enter on a stop beyond a reversal signal bar, protective stop beyond the signal bar, scalp toward
the middle of the range with reward at least as large as risk).  Brooks trades discretionally on
5-minute charts; the numbers below are OUR mechanical translation (flagged DEFAULT):
 * Chart: 1-hour bars built from OANDA M5 candles.  Trading range = the prior 24 bars: width <= 8x the
   average bar range, 20-EMA roughly flat (moved <= 30% of the range width) and price crossed the
   20-EMA at least 4 times (two-sided trading).
 * Failed upside breakout: the last completed bar trades above the range high but closes back inside
   and is a bear bar (close < open) -> SELL STOP 1 pip below that bar's low, stop 1 pip above its
   high, take-profit at the range midpoint; skipped unless reward >= risk.  Mirror for longs.
 * Entry order valid 60 minutes (the next bar); day trade, flat before 17:00 NY."""
from .base import Strategy, Order
from ._bars import resample
from ..indicators import ema
from ..instruments import pip

class BrooksRangeFade(Strategy):
    sid = "brooks-range-fade"; expert = "Al Brooks"; style = "fade failed breakouts of trading ranges"
    book = "Trading Price Action Trading Ranges (2012)"
    warmup = 1; intraday = True; max_per_day = 2
    N = 24
    def signal(self, hist, bars, pair, state, now):
        h1 = resample(bars, 60)
        if len(h1) < self.N + 21: return []
        t = str(h1["time"].iloc[-1])
        if state.get("last_bar") == t: return []
        state["last_bar"] = t
        p = pip(pair); e = ema(h1["close"], 20)
        rg = h1.iloc[-self.N - 1:-1]; er = e.iloc[-self.N - 1:-1]
        rh, rl = float(rg["high"].max()), float(rg["low"].min()); w = rh - rl
        avgbar = float((rg["high"] - rg["low"]).mean())
        side_ = (rg["close"] - er) > 0
        crosses = int((side_ != side_.shift()).iloc[1:].sum())
        flat = abs(float(er.iloc[-1] - er.iloc[0])) <= 0.3 * w
        if not (w > 0 and w <= 8 * avgbar and flat and crosses >= 4): return []
        b = h1.iloc[-1]; mid = (rh + rl) / 2
        meta = {"range_hi": rh, "range_lo": rl, "crosses": crosses}
        if b.high > rh and b.close < rh and b.close < b.open:
            en, sl = float(b.low) - p, float(b.high) + p
            if en - mid >= sl - en > 0:
                return [Order(-1, price=en, sl=sl, tp=mid, gtd_minutes=60, close_eod=True, meta=meta)]
        if b.low < rl and b.close > rl and b.close > b.open:
            en, sl = float(b.high) + p, float(b.low) - p
            if mid - en >= en - sl > 0:
                return [Order(+1, price=en, sl=sl, tp=mid, gtd_minutes=60, close_eod=True, meta=meta)]
        return []
