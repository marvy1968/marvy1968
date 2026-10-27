"""Van K. Tharp, *Trade Your Way to Financial Freedom* (McGraw-Hill, 1999/2007), "Trying to beat
random entry" (Tharp & Tom Basso) and the position-sizing chapter.
BOOK rules: always in the market, direction by coin flip; volatility = 10-day exponential moving
average of the average true range; trailing stop 3x volatility from the close, only moved in the
trade's favour; re-enter (new coin flip) after each exit; size so a stop-out risks 1% of equity.
DEFAULT / deviations:
 * Risk 0.5% per trade (the fx-experts account-wide setting) instead of Tharp's 1%.
 * The coin flip is a deterministic hash of pair+date (reproducible, not predictable from price).
 * Re-entry happens at the next daily run (17:15 NY) after a stop-out, not the same instant.
 * Markets: six FX crosses not traded by the other systems (EUR_GBP, EUR_CHF, AUD_JPY, CAD_JPY,
   AUD_NZD, EUR_AUD) so an always-in system does not block them on this single FIFO account.
 * No take-profit: the book's only exit is the trailing stop (kept at OANDA, ratcheted daily)."""
import hashlib
from .base import Strategy, Order, Manage
from ..indicators import tharp_vol
from ..instruments import EXTRA_PAIRS

def coin(pair, day):
    return 1 if hashlib.sha256(f"{pair}|{day}".encode()).digest()[0] % 2 == 0 else -1

class TharpRandom(Strategy):
    sid = "tharp-random"; expert = "Van K. Tharp"; style = "random entry + 3x volatility trailing stop"
    book = "Trade Your Way to Financial Freedom (2nd ed. 2007)"
    warmup = 15; pairs = EXTRA_PAIRS
    def plan(self, hist, pair, state):
        if len(hist) < self.warmup: return []
        v = float(tharp_vol(hist).iloc[-1])
        if not v > 0: return []
        side = coin(pair, str(hist.index[-1].date()))
        return [Order(side, kind="market", sl_dist=3 * v, meta={"vol": v})]
    def manage(self, pos, hist, pair, state):
        if pos.sl is None: return Manage()
        v = float(tharp_vol(hist).iloc[-1]); c = float(hist["close"].iloc[-1])
        new = c - pos.side * 3 * v
        return Manage(new_sl=max(pos.sl, new) if pos.side == 1 else min(pos.sl, new))
