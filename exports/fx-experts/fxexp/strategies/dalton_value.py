"""James F. Dalton (with E. Jones & R. Dalton), *Mind Over Markets* (updated ed., Wiley 2013).
BOOK: value area = price region holding 70% of the day's volume, built outward from the
highest-volume price by comparing the two prices above vs the two below (Appendix 1).
Value-Area Rule (p.279-280): if the market opens outside the previous day's value area and then
trades inside it for two half-hour periods (acceptance), it is likely to trade through the entire
value area. Dalton warns the rule alone is "little better than a flip of a coin" without context.
DEFAULT / interpretation:
 * FX day = 17:00-17:00 NY; profile from the previous day's OANDA M5 candles with tick volume spread
   evenly across each candle's range, 1-pip price bins.
 * Acceptance = two consecutive completed 30-minute periods closing inside the value area, before
   12:00 NY.  Enter at market toward the far edge: take-profit = opposite value-area edge.
 * Stop = 1 pip beyond today's extreme outside value (the failed side); skip if risk > reward or
   the target is < 5 pips away.  No context filters (Dalton's caveat).  Day trade."""
import numpy as np
from datetime import timedelta
from zoneinfo import ZoneInfo
from .base import Strategy, Order
from ._bars import resample
from ..instruments import pip
NY = ZoneInfo("America/New_York")

def volume_profile(bars, p):
    """Returns (prices array, volume array) at 1-pip bins."""
    lo = int(round(float(bars["low"].min()) / p)); hi = int(round(float(bars["high"].max()) / p))
    vol = np.zeros(hi - lo + 1)
    for l, h, v in zip(bars["low"], bars["high"], bars["volume"]):
        a, b = int(round(l / p)) - lo, int(round(h / p)) - lo
        vol[a:b + 1] += (v if v > 0 else 1) / (b - a + 1)
    return (np.arange(lo, hi + 1) * p), vol

def value_area(prices, vol, frac=0.70):
    """Dalton Appendix 1: start at the POC, add the larger of the two-price sums above/below."""
    poc = int(np.argmax(vol)); lo = hi = poc; acc = vol[poc]; tot = vol.sum(); n = len(vol)
    while acc < frac * tot and (lo > 0 or hi < n - 1):
        up = vol[hi + 1:hi + 3].sum() if hi < n - 1 else -1
        dn = vol[max(lo - 2, 0):lo].sum() if lo > 0 else -1
        if up >= dn: acc += up; hi = min(hi + 2, n - 1)
        else: acc += dn; lo = max(lo - 2, 0)
    return float(prices[lo]), float(prices[hi]), float(prices[poc])

class DaltonValueArea(Strategy):
    sid = "dalton-value"; expert = "James F. Dalton"; style = "market profile: value-area rule"
    book = "Mind Over Markets (updated ed. 2013)"
    warmup = 1; intraday = True; max_per_day = 1
    def signal(self, hist, bars, pair, state, now):
        n = now.astimezone(NY); today = (n + timedelta(hours=7)).date()
        if 12 <= n.hour < 17: return []
        before = sorted(d for d in set(bars["td"]) if d < today)
        if not before: return []
        prev = bars[bars["td"] == before[-1]]; cur = bars[bars["td"] == today]
        if len(prev) < 48 or len(cur) < 6: return []
        p = pip(pair)
        val, vah, poc = value_area(*volume_profile(prev, p))
        o = float(cur["open"].iloc[0])
        m30 = resample(cur, 30)
        if len(m30) < 2: return []
        last2 = m30["close"].iloc[-2:]
        if not ((last2 >= val) & (last2 <= vah)).all(): return []
        c = float(cur["close"].iloc[-1]); meta = {"VAL": val, "VAH": vah, "POC": poc, "open": o}
        if o < val:
            sl = float(cur["low"].min()) - p; tp = vah
            if tp - c >= max(c - sl, 5 * p) and c > sl:
                return [Order(+1, kind="market", sl=sl, tp=tp, close_eod=True, meta=meta)]
        elif o > vah:
            sl = float(cur["high"].max()) + p; tp = val
            if c - tp >= max(sl - c, 5 * p) and sl > c:
                return [Order(-1, kind="market", sl=sl, tp=tp, close_eod=True, meta=meta)]
        return []
