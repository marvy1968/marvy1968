"""Kathy Lien, *Day Trading and Swing Trading the Currency Market* (Wiley; 2005 1st ed. ch. 8 p.103-108,
3rd ed. ch. 10 "Technical Trading Strategy: Fading the Double Zeros").  (Also co-taught with Boris
Schlossberg at BKForex; *Millionaire Traders* is an interview book with no codable rules.)
BOOK rules (long; short is the mirror):
 1. Pair trading well below its 20-period SMA on a 10- or 15-minute chart (we use 15-minute).
 2. Enter long several pips below the figure ("00" level), no more than 10 pips (book example: 5).
 3. Initial stop no more than 20 pips below entry (we use exactly 20).
 4. At a profit of 2x risk close half and move the stop on the rest to breakeven, then trail.
DEFAULT / interpretation (not numbers from the book):
 * "well below" = last 15-min close at least 10 pips beyond the 20-SMA; "approaching" = figure within
   3-30 pips of the last close.  Entry = resting LIMIT 5 pips beyond the figure, valid 60 minutes.
 * One ticket per signal (no half-closing on a FIFO account): take-profit at 2R = 40 pips, which is
   the book's first target; the trailed second half is not modelled.
 * JPY pairs: the figure is the whole yen (e.g. 150.00)."""
import math
from .base import Strategy, Order
from ._bars import resample
from ..instruments import pip

class LienDoubleZeros(Strategy):
    sid = "lien-zeros"; expert = "Kathy Lien (& Boris Schlossberg)"; style = "intraday fade at round numbers"
    book = "Day Trading and Swing Trading the Currency Market - 'Fading the Double Zeros'"
    warmup = 1; intraday = True; max_per_day = 3
    def signal(self, hist, bars, pair, state, now):
        m15 = resample(bars, 15)
        if len(m15) < 21: return []
        last_t = str(m15["time"].iloc[-1])
        if state.get("last_bar") == last_t: return []
        state["last_bar"] = last_t
        p = pip(pair); fig = 100 * p
        c = float(m15["close"].iloc[-1]); sma = float(m15["close"].iloc[-20:].mean())
        meta = {"sma20": round(sma, 5), "close": c}
        if c < sma - 10 * p:
            f = math.floor(c / fig + 1e-9) * fig
            if 3 * p <= c - f <= 30 * p:
                e = f - 5 * p
                return [Order(+1, kind="limit", price=e, sl=e - 20 * p, tp=e + 40 * p, gtd_minutes=60, meta={**meta, "figure": round(f, 3)})]
        elif c > sma + 10 * p:
            f = math.ceil(c / fig - 1e-9) * fig
            if 3 * p <= f - c <= 30 * p:
                e = f + 5 * p
                return [Order(-1, kind="limit", price=e, sl=e + 20 * p, tp=e - 40 * p, gtd_minutes=60, meta={**meta, "figure": round(f, 3)})]
        return []
