"""Larry Williams, *Long-Term Secrets to Short-Term Trading* (Wiley 1999/2011), ch. 4 "Volatility
Breakouts - The Momentum Breakthrough".
BOOK (2nd ed. p.61-62, quoted): "take today's range adding a portion of it to tomorrow's opening ...
A protective stop of $1,500, or 50 percent of the previous day's range, subtracted from our entry,
is used as our protective stop, while our exit is the Bail Out, or the first profitable opening".
DEFAULT / uncertainty:
 * The book does not fix "a portion"; we use 60% of the previous day's range, both directions
   (buy stop open+0.6R, sell stop open-0.6R; first fill cancels the other).
 * 'Open' = FX daily open at 17:00 NY. Stop = 50% of the previous day's range from entry (book).
 * Exit = bailout: close at the first daily open (17:00 NY) at which the trade shows a profit (book).
   No take-profit order - the book has none; the stop lives at OANDA."""
from .base import Strategy, Order, Manage

class WilliamsVBO(Strategy):
    sid = "williams-vbo"; expert = "Larry Williams"; style = "volatility breakout off the open"
    book = "Long-Term Secrets to Short-Term Trading (ch. 4)"
    warmup = 2; PCT = 0.6
    def plan(self, hist, pair, state):
        if len(hist) < self.warmup: return []
        r = float(hist["high"].iloc[-1] - hist["low"].iloc[-1])
        if not r > 0: return []
        m = {"prev_range": r}
        return [Order(+1, from_open=self.PCT * r, sl_dist=0.5 * r, meta=dict(m)),
                Order(-1, from_open=-self.PCT * r, sl_dist=0.5 * r, meta=dict(m))]
    def manage(self, pos, hist, pair, state):
        new_open = float(hist["close"].iloc[-1])   # FX: the 17:00 NY close is the next day's open
        if (new_open - pos.entry) * pos.side > 0:
            return Manage(close=True, reason="bailout: first profitable open")
        return Manage()
