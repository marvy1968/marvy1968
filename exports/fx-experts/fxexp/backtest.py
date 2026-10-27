"""Event backtester on real OANDA H4 candles (mid) with an intrabar path model.

Path model: bullish bar O->L->H->C, bearish bar O->H->L->C.  Orders trigger on the correct side of
the quote (long entry/short stop on ask, short entry/long stop on bid) using a typical spread; stop
fills get extra slippage; fills at the 17:00 NY open use a widened spread.  Financing is charged
per rollover held (x3 for the Wednesday rollover).  No parameters are fitted to the data."""
import math
from dataclasses import dataclass, asdict
import pandas as pd
from .data import load_csv, to_daily, trade_date
from .instruments import pip, spread, ROLL_MULT, SLIP_PIPS, FIN_PIPS_PER_NIGHT
from .strategies.base import Position

@dataclass
class Trade:
    system: str; pair: str; side: int; entry_time: str; exit_time: str
    entry: float; exit: float; sl0: float; risk: float; r: float; reason: str; nights: int; meta: str

def nights_between(dates, i0, i1):
    n = 0
    for k in range(i0 + 1, i1 + 1):
        n += 3 if dates[k].weekday() == 3 else 1
    return n

class Sim:
    def __init__(self, strat, pair, h4, daily):
        self.s, self.pair, self.daily = strat, pair, daily
        self.dates = list(daily.index)
        h4 = h4.copy(); h4["date"] = pd.to_datetime(trade_date(h4["time"]))
        self.bars = {d: g for d, g in h4.groupby("date")}
        self.sp = spread(pair); self.slip = SLIP_PIPS * pip(pair); self.t = pip(pair)
        self.state, self.pos, self.pending, self.trades = {}, None, [], []

    # ---- helpers -------------------------------------------------------
    def _open_pos(self, o, fill, when, di, day_lo, day_hi, day_open):
        side = o.side
        if o.sl is not None: sl = o.sl
        elif o.sl_from_open is not None: sl = day_open + o.sl_from_open
        elif o.sl_dist is not None: sl = fill - side * o.sl_dist
        elif o.sl_daylow: sl = (day_lo - self.sp / 2 - self.t) if side == 1 else (day_hi + self.sp / 2 + self.t)
        else: raise ValueError("order without stop")
        if (fill - sl) * side <= 0: return None      # stop already through: skip
        tp = o.tp if o.tp is not None else (fill + side * o.tp_dist if o.tp_dist else None)
        self.pos = Position(side, fill, sl, tp, abs(fill - sl), di, when, o.close_eod, dict(o.meta, sl0=sl))
        self.pending = []                              # OCO: one trade per pair per system
        return self.pos

    def _close(self, px, when, di, reason):
        p = self.pos
        nts = nights_between(self.dates, p.entry_di, di)
        fin = nts * FIN_PIPS_PER_NIGHT * self.t
        r = (p.side * (px - p.entry) - fin) / p.risk
        self.trades.append(Trade(self.s.sid, self.pair, p.side, str(p.entry_time), str(when), p.entry, px,
                                 p.meta["sl0"], p.risk, r, reason, nts,
                                 ";".join(f"{k}={v:.5g}" if isinstance(v, float) else f"{k}={v}" for k, v in p.meta.items() if k not in ("sl0",))))
        self.pos = None

    def _triggers(self, day_open):
        """Return list of (mid_level, direction, kind, obj). direction +1 fires on up-moves."""
        out, h = [], self.sp / 2
        p = self.pos
        if p is not None:
            out.append((p.sl + h if p.side == 1 else p.sl - h, -p.side, "sl", None))
            if p.tp is not None:
                out.append((p.tp + h if p.side == 1 else p.tp - h, p.side, "tp", None))
            return out
        for o in self.pending:
            if o.kind != "stop": continue
            if o.arm is not None and not o.armed:
                out.append((o.arm, -o.side, "arm", o))
                continue
            lvl = o.price if o.price is not None else day_open + o.from_open
            out.append((lvl - h if o.side == 1 else lvl + h, o.side, "entry", o))
        return out

    def _walk_bar(self, bar, di, day_open, ext):
        o_, h_, l_, c_ = bar.open, bar.high, bar.low, bar.close
        pts = [o_, l_, h_, c_] if c_ >= o_ else [o_, h_, l_, c_]
        when = bar.time; a = pts[0]; first = True
        for b in pts[1:] + [None]:
            # gap check at bar open (first point only) then segment a->b
            for _guard in range(20):
                trig = self._triggers(day_open)
                hit = None
                if first:
                    for lvl, d, kind, obj in trig:
                        if (d == 1 and a >= lvl) or (d == -1 and a <= lvl): hit = (lvl, d, kind, obj, a); break
                if hit is None and b is not None:
                    cands = [(lvl, d, kind, obj) for lvl, d, kind, obj in trig
                             if (d == 1 and b > a and a <= lvl <= b) or (d == -1 and b < a and b <= lvl <= a)]
                    if cands:
                        lvl, d, kind, obj = min(cands, key=lambda x: abs(x[0] - a)); hit = (lvl, d, kind, obj, lvl)
                if hit is None: break
                lvl, d, kind, obj, px = hit
                ext[0] = min(ext[0], px); ext[1] = max(ext[1], px)
                h = self.sp / 2
                if kind == "arm":
                    obj.armed = True
                elif kind == "entry":
                    fill = (px + h + self.slip) if obj.side == 1 else (px - h - self.slip)
                    self._open_pos(obj, fill, when, di, ext[0], ext[1], day_open)
                elif kind == "sl":
                    p = self.pos; fill = (px - h - self.slip) if p.side == 1 else (px + h + self.slip)
                    k = di - p.entry_di + 1; old = p
                    self._close(fill, when, di, "stop")
                    ro = self.s.after_stop(old, k, self.pair, self.state)
                    if ro is not None: ro.meta["_exp"] = di + ro.expires_days; self.pending = [ro]
                elif kind == "tp":
                    p = self.pos; fill = (px - h) if p.side == 1 else (px + h)
                    self._close(fill, when, di, "target")
                a = px
            first = False
            if b is None: break
            ext[0] = min(ext[0], b); ext[1] = max(ext[1], b)
            a = b

    def run(self, start=None, end=None):
        d = self.daily
        for di in range(self.s.warmup, len(self.dates)):
            date = self.dates[di]
            if (start is not None and date < start) or (end is not None and date > end): continue
            bars = self.bars.get(date)
            if bars is None or bars.empty: continue
            self.pending = [o for o in self.pending if o.meta.get("_exp", -1) >= di]
            if self.pos is None:
                for o in self.s.plan(d.iloc[:di], self.pair, self.state):
                    o.meta["_exp"] = di + o.expires_days; self.pending.append(o)
            day_open = float(bars.iloc[0].open); ext = [day_open, day_open]
            # market orders fill at the day's first price with a widened rollover spread
            for o in [o for o in self.pending if o.kind == "market"]:
                if self.pos is None:
                    h = self.sp * ROLL_MULT / 2
                    fill = day_open + h + self.slip if o.side == 1 else day_open - h - self.slip
                    self._open_pos(o, fill, bars.iloc[0].time, di, day_open, day_open, day_open)
            self.pending = [o for o in self.pending if o.kind != "market"]
            for bar in bars.itertuples():
                self._walk_bar(bar, di, day_open, ext)
            last = bars.iloc[-1]; h = self.sp / 2
            if self.pos is not None:
                p = self.pos
                if p.close_eod:
                    self._close(last.close - h - self.slip if p.side == 1 else last.close + h + self.slip, last.time, di, "eod")
                else:
                    m = self.s.manage(p, d.iloc[:di + 1], self.pair, self.state)
                    if m.close:
                        self._close(last.close - h - self.slip if p.side == 1 else last.close + h + self.slip, last.time, di, m.reason or "exit")
                    elif m.new_sl is not None:
                        p.sl = m.new_sl
        return self.trades

def stats(rs):
    rs = list(rs); n = len(rs)
    if n == 0: return dict(trades=0, win=0.0, pf=float("nan"), net_r=0.0, avg_r=0.0, maxdd_r=0.0)
    w = [r for r in rs if r > 0]; l = [r for r in rs if r <= 0]
    pf = sum(w) / -sum(l) if l and sum(l) < 0 else float("inf")
    eq = peak = dd = 0.0
    for r in rs:
        eq += r; peak = max(peak, eq); dd = max(dd, peak - eq)
    return dict(trades=n, win=100.0 * len(w) / n, pf=pf, net_r=sum(rs), avg_r=sum(rs) / n, maxdd_r=dd)
