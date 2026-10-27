"""Ed Ponsi, *Forex Patterns and Probabilities* (Wiley 2007), ch. 9 trend strategy (daily chart).
BOOK rules (via chapter summaries: thepatternsite.com Forex review, desiretotrade.com):
 1. Trend filter: 10, 20, 50 and 200-day EMAs in order (10>20>50>200 for longs).
 2. At least 10 daily closes above the 10-day EMA (below, for shorts).
 3. Entry on a pullback to the 10-day EMA.
 4. Stop = 10-day EMA minus 1/2 of the daily ATR; trailed every daily close, never loosened.
 5. Exit part at a profit equal to the risk and move the stop to breakeven; trail the rest.
DEFAULT / interpretation:
 * Entry = LIMIT at today's 10-EMA (good for the trading day). ATR = 14-day Wilder ATR (period not
   confirmed from the book).
 * One ticket (FIFO account): no take-profit; once a daily close is >= 1R in profit the stop goes to
   breakeven (book step 5 without the partial exit), then keeps trailing at 10-EMA -/+ 0.5 ATR."""
from .base import Strategy, Order, Manage
from ..indicators import ema, atr

class PonsiTrend(Strategy):
    sid = "ponsi-trend"; expert = "Ed Ponsi"; style = "trend pullback to 10-EMA"
    book = "Forex Patterns and Probabilities (2007)"
    warmup = 220
    def _ind(self, hist):
        c = hist["close"]
        return c, ema(c, 10), ema(c, 20), ema(c, 50), ema(c, 200), atr(hist, 14)
    def plan(self, hist, pair, state):
        if len(hist) < self.warmup: return []
        c, e10, e20, e50, e200, a = self._ind(hist)
        E, A = float(e10.iloc[-1]), float(a.iloc[-1])
        if not A > 0: return []
        up = e10.iloc[-1] > e20.iloc[-1] > e50.iloc[-1] > e200.iloc[-1] and (c.iloc[-10:] > e10.iloc[-10:]).all()
        dn = e10.iloc[-1] < e20.iloc[-1] < e50.iloc[-1] < e200.iloc[-1] and (c.iloc[-10:] < e10.iloc[-10:]).all()
        m = {"ema10": E, "atr": A, "risk": 0.5 * A}
        if up: return [Order(+1, kind="limit", price=E, sl=E - 0.5 * A, meta=m)]
        if dn: return [Order(-1, kind="limit", price=E, sl=E + 0.5 * A, meta=m)]
        return []
    def manage(self, pos, hist, pair, state):
        c, e10, *_, a = self._ind(hist)
        new = float(e10.iloc[-1]) - pos.side * 0.5 * float(a.iloc[-1])
        risk = pos.meta.get("risk")
        if risk and (float(c.iloc[-1]) - pos.entry) * pos.side >= risk:
            new = max(new, pos.entry) if pos.side == 1 else min(new, pos.entry)
        if pos.sl is None: return Manage()
        return Manage(new_sl=max(pos.sl, new) if pos.side == 1 else min(pos.sl, new))
