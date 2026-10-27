"""Linda Bradford Raschke & Laurence Connors, *Street Smarts* (M. Gordon Publishing, 1995), ch. 4
'Turtle Soup'.  BOOK (as reprinted by TradingMarkets/Connors Group):
 1. Today makes a new 20-day low.  2. The previous 20-day low occurred at least four sessions earlier.
 3. After the market falls below the previous 20-day low, buy stop 5-10 ticks above that prior low,
    good for today only.  4. Initial stop one tick under today's low.  Shorts are the mirror image.
 Chapter text: trail the stop as the trade becomes profitable; re-enter at the original entry
 level if stopped out on day 1 or 2.  Turtle Soup Plus One text: 'take profits within two to six
 days and trail stops'.
DEFAULT: tick = 1 pip; buffer = 5 pips (book's lower bound); trail = prior day's low/high from the
first close; time exit at the close of trading day 6; one re-entry."""
from .base import Strategy, Order, Manage
from ..instruments import pip

class RaschkeSoup(Strategy):
    sid = "raschke-soup"; expert = "Linda Raschke (with Larry Connors)"; style = "mean reversion (failed-breakout fade)"
    book = "Street Smarts: High Probability Short-Term Trading Strategies (1995)"
    warmup = 25
    MAX_DAYS = 6
    def plan(self, hist, pair, state):
        if len(hist) < self.warmup: return []
        t = pip(pair); w = hist.iloc[-20:]; n = len(hist); out = []
        lo_pos = n - 20 + int(w["low"].to_numpy().argmin()); hi_pos = n - 20 + int(w["high"].to_numpy().argmax())
        l20, h20 = float(w["low"].min()), float(w["high"].max())
        if n - lo_pos >= 4:   # today (index n) is >= 4 sessions after the prior 20-day low
            out.append(Order(+1, price=l20 + 5 * t, arm=l20 - t, sl_daylow=True, meta={"level": l20 + 5 * t}))
        if n - hi_pos >= 4:
            out.append(Order(-1, price=h20 - 5 * t, arm=h20 + t, sl_daylow=True, meta={"level": h20 - 5 * t}))
        return out
    def manage(self, pos, hist, pair, state):
        t = pip(pair); k = len(hist) - pos.entry_di   # trading days held incl. entry day
        if k >= self.MAX_DAYS: return Manage(close=True, reason="time(6d)")
        if pos.side == 1: return Manage(new_sl=max(pos.sl, hist["low"].iloc[-1] - t))
        return Manage(new_sl=min(pos.sl, hist["high"].iloc[-1] + t))
    def after_stop(self, pos, day_k, pair, state):
        if day_k > 2 or pos.meta.get("reentry") or "level" not in pos.meta: return None
        return Order(pos.side, price=pos.meta["level"], sl_daylow=True,
                     expires_days=(1 if day_k == 1 else 0), meta={"level": pos.meta["level"], "reentry": True})
