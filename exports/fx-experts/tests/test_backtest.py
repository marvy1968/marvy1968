import pandas as pd
import pytest
from fxexp.backtest import Sim, nights_between, stats
from fxexp.strategies.base import Strategy, Order
from fxexp.instruments import spread, SLIP_PIPS, FIN_PIPS_PER_NIGHT
from tests.helpers import flat_daily, h4_from_daily

class OneShot(Strategy):
    sid = "t"; warmup = 1
    def __init__(self, orders_on, orders): self.on, self.orders, self.done = orders_on, orders, False
    def plan(self, hist, pair, state):
        if len(hist) == self.on and not self.done:
            self.done = True; return self.orders()
        return []

def run(strat, d, paths):
    h4 = h4_from_daily(d, paths); from fxexp.data import to_daily
    return Sim(strat, "EUR_USD", h4, to_daily(h4)).run()

S = spread("EUR_USD"); SL = SLIP_PIPS * 0.0001

def test_stop_entry_then_target():
    d = flat_daily(6)
    paths = {3: [(1.1000, 1.1100, 1.0990, 1.1090)] + [(1.109, 1.109, 1.109, 1.109)] * 5}
    tr = run(OneShot(3, lambda: [Order(1, price=1.1010, sl_dist=0.0030, tp_dist=0.0060)]), d, paths)
    assert len(tr) == 1 and tr[0].reason == "target"
    assert tr[0].entry == pytest.approx(1.1010 + SL)   # ask-triggered fill at level + slippage
    assert tr[0].r == pytest.approx(0.0060 / 0.0030, rel=1e-6)

def test_bearish_bar_hits_stop_before_target():
    d = flat_daily(6)
    # bearish bar: O->H->L->C. entry at 1.1010 on the way up, high 1.1060 (< tp), then low 1.0960 (< stop)
    paths = {3: [(1.1000, 1.1060, 1.0960, 1.0970)] + [(1.097, 1.097, 1.097, 1.097)] * 5}
    tr = run(OneShot(3, lambda: [Order(1, price=1.1010, sl_dist=0.0030, tp_dist=0.0060)]), d, paths)
    assert tr[0].reason == "stop" and tr[0].r < -1.0

def test_arm_then_trigger_and_oco():
    d = flat_daily(6)
    paths = {3: [(1.1000, 1.1000, 1.0980, 1.0985), (1.0985, 1.1050, 1.0985, 1.1045)] + [(1.1045,) * 4] * 4}
    mk = lambda: [Order(1, price=1.1030, arm=1.0985, sl_dist=0.003, tp_dist=0.006), Order(-1, price=1.0970, arm=1.1040, sl_dist=0.003, tp_dist=0.006)]
    tr = run(OneShot(3, mk), d, paths)
    assert len(tr) == 1 and tr[0].side == 1

def test_unarmed_order_never_fills():
    d = flat_daily(6)
    paths = {3: [(1.1000, 1.1050, 1.0995, 1.1045)] + [(1.1045,) * 4] * 5}
    tr = run(OneShot(3, lambda: [Order(1, price=1.1030, arm=1.0985, sl_dist=0.003, tp_dist=0.006)]), d, paths)
    assert tr == []

def test_gap_fill_at_open_and_close_eod():
    d = flat_daily(6)
    paths = {3: [(1.1050, 1.1060, 1.1040, 1.1055)] + [(1.1055,) * 4] * 5}
    tr = run(OneShot(3, lambda: [Order(1, price=1.1010, sl=1.0990, close_eod=True)]), d, paths)
    assert tr[0].entry == pytest.approx(1.1050 + S / 2 + SL) and tr[0].reason == "eod"

def test_daylow_stop_uses_low_before_fill():
    d = flat_daily(6)
    paths = {3: [(1.1000, 1.1000, 1.0950, 1.0955), (1.0955, 1.0990, 1.0955, 1.0985)] + [(1.0985,) * 4] * 4}
    tr = run(OneShot(3, lambda: [Order(1, price=1.0970, arm=1.0960, sl_daylow=True, close_eod=True)]), d, paths)
    assert tr[0].sl0 == pytest.approx(1.0950 - S / 2 - 0.0001)

def test_financing_nights_wednesday_triple():
    dates = list(pd.bdate_range("2024-01-08", periods=5))  # Mon..Fri
    assert nights_between(dates, 0, 4) == 1 + 1 + 3 + 1
    assert nights_between(dates, 0, 0) == 0

def test_stats_pf_and_dd():
    s = stats([1, -1, 2, -1, -1])
    assert s["trades"] == 5 and s["pf"] == pytest.approx(1.0) and s["maxdd_r"] == pytest.approx(2.0)
