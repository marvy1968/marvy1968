"""Toby Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (Traders
Press, 1990).  Studied currency futures (Swiss franc, D-mark) among other markets.
BOOK/SUMMARY: volatility contraction precedes expansion; after an ID/NR4 (inside day that is also
the narrowest range of 4 days) the opening-range breakout tends to produce trend days.  Entry by
stop orders a predetermined 'stretch' above/below the open (stretch = 10-day average of the
smaller of high-open and open-low); the opposite stop is the protective stop; exit on the close.
FX translation: 'open' = 17:00 NY daily open; 'close' = next 17:00 NY.  No take-profit in the
book (day trade closed at the end of the day)."""
import numpy as np
from .base import Strategy, Order

class CrabelNR4(Strategy):
    sid = "crabel-idnr4"; expert = "Toby Crabel"; style = "breakout (opening range / contraction)"
    book = "Day Trading with Short Term Price Patterns and Opening Range Breakout (1990)"
    warmup = 15
    def plan(self, hist, pair, state):
        if len(hist) < self.warmup: return []
        H, L, O = hist["high"].to_numpy(), hist["low"].to_numpy(), hist["open"].to_numpy()
        rng = H - L
        inside = H[-1] <= H[-2] and L[-1] >= L[-2] and rng[-1] < rng[-2]
        nr4 = rng[-1] <= rng[-4:].min()
        if not (inside and nr4): return []
        stretch = float(np.mean(np.minimum(H[-10:] - O[-10:], O[-10:] - L[-10:])))
        if not stretch > 0: return []
        return [Order(+1, from_open=stretch, sl_from_open=-stretch, close_eod=True, meta={"stretch": stretch}),
                Order(-1, from_open=-stretch, sl_from_open=stretch, close_eod=True, meta={"stretch": stretch})]
