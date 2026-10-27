"""John Bollinger, *Bollinger on Bollinger Bands* (McGraw-Hill 2001), ch. 16 'Method I: Volatility
Breakout'.  BOOK/SUMMARY: a Squeeze is BandWidth (20-day, 2 sd) at its lowest level in six months
(~125 sessions); 'wait for a Squeeze and go with the first breakout' - a close outside a band.
Exit: Parabolic SAR whose initial stop is 'just below the range of the breakout formation' and is
incremented each day (the book's conservative exit; a tag of the opposite band is the alternative).
DEFAULT: breakout must come within 20 sessions (one band period) of the last squeeze reading;
formation = squeeze bar..breakout bar, at least 5 bars; SAR 0.02/0.02/0.20 (Wilder);
entry at the next day's open (market).  Volume confirmation (II/AD/MFI) not used: FX has no
central volume.  No fixed take-profit in the book."""
from .base import Strategy, Order, Manage
from ..indicators import bollinger, parabolic_step
from ..instruments import pip

class BollingerSqueeze(Strategy):
    sid = "bollinger-squeeze"; expert = "John Bollinger"; style = "breakout (volatility expansion)"
    book = "Bollinger on Bollinger Bands (2001)"
    warmup = 150; LOOK = 125; WINDOW = 20
    def plan(self, hist, pair, state):
        if len(hist) < self.warmup: return []
        h = hist.iloc[-(self.LOOK + self.WINDOW + 25):]
        lo, mid, up = bollinger(h["close"]); bw = (up - lo) / mid
        sq = bw <= bw.rolling(self.LOOK).min() + 1e-15
        c = h["close"]; n = len(h)
        recent = [i for i in range(n - 1 - self.WINDOW, n - 1) if sq.iloc[i]]
        if not recent: return []
        s = recent[-1]
        outside = [(c.iloc[i] > up.iloc[i]) or (c.iloc[i] < lo.iloc[i]) for i in range(s + 1, n)]
        if not outside or not outside[-1] or any(outside[:-1]): return []   # first breakout only, on the last bar
        f0 = min(s, n - 5); t = pip(pair)
        if c.iloc[-1] > up.iloc[-1]:
            sl = float(h["low"].iloc[f0:].min()) - t
            return [Order(+1, kind="market", sl=sl, meta={"sar": sl, "ep": float(h["high"].iloc[-1]), "af": 0.02})]
        sl = float(h["high"].iloc[f0:].max()) + t
        return [Order(-1, kind="market", sl=sl, meta={"sar": sl, "ep": float(h["low"].iloc[-1]), "af": 0.02})]
    def manage(self, pos, hist, pair, state):
        m = pos.meta; hi, lo = float(hist["high"].iloc[-1]), float(hist["low"].iloc[-1])
        if "sar" not in m:  # state lost: restart the Parabolic from the current broker stop
            m.update(sar=pos.sl, ep=hi if pos.side == 1 else lo, af=0.02)
        phi, plo = float(hist["high"].iloc[-2]), float(hist["low"].iloc[-2])
        sar, ep, af = parabolic_step(m["sar"], m["ep"], m["af"], pos.side == 1, hi, lo, phi, plo)
        m.update(sar=sar, ep=ep, af=af)
        new = max(pos.sl, sar) if pos.side == 1 else min(pos.sl, sar)
        return Manage(new_sl=new)
