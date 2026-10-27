"""Kathy Lien, *Day Trading and Swing Trading the Currency Market* (Wiley), 'The Fader'.
BOOK rules (ch. Technical Trading Strategies; reprinted at TradeWins):
 1. 14-period ADX (daily) < 35 (ideally falling - optional, not coded).
 2. Long: price breaks below the previous day's low by >= 15 pips, then
 3. buy stop 15 pips above the previous day's high.
 4. Initial stop no more than 30 pips away (we use exactly 30).
 5. Take profit at double the risk, i.e. 60 pips.  Short is the mirror image.
DEFAULT: entry orders good for the trading day only (OANDA GFD = 17:00 NY)."""
from .base import Strategy, Order
from ..indicators import adx
from ..instruments import pip

class LienFader(Strategy):
    sid = "lien-fader"; expert = "Kathy Lien"; style = "mean reversion (false-breakout fade)"
    book = "Day Trading and Swing Trading the Currency Market (Wiley, 2nd/3rd ed.)"
    warmup = 40
    def plan(self, hist, pair, state):
        if len(hist) < self.warmup: return []
        a = adx(hist.iloc[-120:], 14).iloc[-1]
        if not (a < 35): return []
        p = pip(pair); ph, pl = hist["high"].iloc[-1], hist["low"].iloc[-1]
        return [Order(+1, price=ph + 15 * p, arm=pl - 15 * p, sl_dist=30 * p, tp_dist=60 * p, meta={"adx": round(float(a), 1)}),
                Order(-1, price=pl - 15 * p, arm=ph + 15 * p, sl_dist=30 * p, tp_dist=60 * p, meta={"adx": round(float(a), 1)})]
