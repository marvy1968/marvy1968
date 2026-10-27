"""Curtis Faith, *Way of the Turtle* (McGraw-Hill 2007) / 'The Original Turtle Trading Rules'.
BOOK (System 1): enter when price exceeds the 20-day high/low by one tick; skip the signal if the
last 20-day breakout (either direction, taken or not) was a winner - a breakout counts as losing if
price moved 2N against it before a profitable 10-day exit; if skipped, still take the 55-day
'failsafe' breakout.  Stop 2N from entry.  Exit at a 10-day low (long) / 10-day high (short).
N = (19*PDN + TR)/20.
DEFAULT/deviations: one unit only (no pyramiding at 1/2 N), sized to the account risk % instead of
the Turtles' 1%-of-equity-per-N unit; 1 tick = 1 pip (historical currency-futures tick)."""
import math
from .base import Strategy, Order, Manage
from ..indicators import turtle_n
from ..instruments import pip

def _hypo_update(hist, n_series, st):
    """Incrementally simulate hypothetical System-1 trades on every 20-day breakout (daily
    resolution) to know whether the *last* breakout was a winner. st persists between calls."""
    H, L, C, O = hist["high"].to_numpy(), hist["low"].to_numpy(), hist["close"].to_numpy(), hist["open"].to_numpy()
    N = n_series.to_numpy()
    i = st.get("i", 21)
    tr = st.get("trade")
    while i < len(hist):
        if tr is None:
            h20, l20 = H[i - 20:i].max(), L[i - 20:i].min()
            if not math.isnan(N[i - 1]):
                if H[i] > h20:
                    e = max(O[i], h20); tr = {"side": 1, "entry": e, "stop": e - 2 * N[i - 1]}
                elif L[i] < l20:
                    e = min(O[i], l20); tr = {"side": -1, "entry": e, "stop": e + 2 * N[i - 1]}
                if tr is not None:  # same-day stop check (conservative)
                    if (tr["side"] == 1 and L[i] <= tr["stop"]) or (tr["side"] == -1 and H[i] >= tr["stop"]):
                        st["last_winner"] = False; tr = None
            i += 1; continue
        s = tr["side"]; l10, h10 = L[i - 10:i].min(), H[i - 10:i].max()
        if s == 1:
            if L[i] <= tr["stop"]: st["last_winner"] = False; tr = None
            elif L[i] < l10: st["last_winner"] = min(O[i], l10) > tr["entry"]; tr = None
        else:
            if H[i] >= tr["stop"]: st["last_winner"] = False; tr = None
            elif H[i] > h10: st["last_winner"] = max(O[i], h10) < tr["entry"]; tr = None
        i += 1
    st["i"], st["trade"] = i, tr
    return st.get("last_winner", False), tr is not None

class FaithTurtle(Strategy):
    sid = "faith-turtle"; expert = "Curtis Faith"; style = "breakout (trend following)"
    book = "Way of the Turtle (2007) / The Original Turtle Trading Rules"
    warmup = 60
    def plan(self, hist, pair, state):
        if len(hist) < self.warmup: return []
        if state.get("_first") != str(hist.index[0]) or state.get("_len", 0) > len(hist):
            state.clear()  # live runs pass a rolling window: recompute from scratch
        state["_first"], state["_len"] = str(hist.index[0]), len(hist)
        n = turtle_n(hist)
        last_winner, hypo_open = _hypo_update(hist, n, state.setdefault("hypo", {}))
        N = float(n.iloc[-1]); t = pip(pair)
        if not N > 0: return []
        meta = {"N": N, "last_winner": last_winner}
        if not last_winner and not hypo_open:
            hi, lo, lb = hist["high"].iloc[-20:].max(), hist["low"].iloc[-20:].min(), 20
        else:
            hi, lo, lb = hist["high"].iloc[-55:].max(), hist["low"].iloc[-55:].min(), 55
        meta["lookback"] = lb
        return [Order(+1, price=hi + t, sl_dist=2 * N, meta=dict(meta)),
                Order(-1, price=lo - t, sl_dist=2 * N, meta=dict(meta))]
    def manage(self, pos, hist, pair, state):
        t = pip(pair)
        if pos.side == 1:
            return Manage(new_sl=max(pos.sl, hist["low"].iloc[-10:].min() - t))
        return Manage(new_sl=min(pos.sl, hist["high"].iloc[-10:].max() + t))
