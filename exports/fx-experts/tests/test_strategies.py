import numpy as np
import pandas as pd
from fxexp.strategies import LienFader, FaithTurtle, RaschkeSoup, BollingerSqueeze, CrabelNR4
from fxexp.strategies.faith_turtle import _hypo_update
from fxexp.indicators import turtle_n
from tests.helpers import daily, flat_daily, choppy_daily

def test_fader_levels_when_adx_low():
    d = choppy_daily(60)
    o = LienFader().plan(d, "EUR_USD", {})
    assert len(o) == 2
    lo = [x for x in o if x.side == 1][0]; sh = [x for x in o if x.side == -1][0]
    assert abs(lo.price - (1.1025 + 0.0015)) < 1e-9 and abs(lo.arm - (1.0975 - 0.0015)) < 1e-9
    assert abs(lo.sl_dist - 0.0030) < 1e-12 and abs(lo.tp_dist - 0.0060) < 1e-12
    assert abs(sh.price - (1.0975 - 0.0015)) < 1e-9 and abs(sh.arm - (1.1025 + 0.0015)) < 1e-9

def test_fader_skips_trending_market():
    d = daily([(1 + i * .01, 1 + i * .01 + .008, 1 + i * .01 - .002, 1 + i * .01 + .006) for i in range(80)])
    assert LienFader().plan(d, "EUR_USD", {}) == []

def test_fader_jpy_pips():
    d = choppy_daily(60, px=150.0, rng=0.5)
    lo = [x for x in LienFader().plan(d, "USD_JPY", {}) if x.side == 1][0]
    assert abs(lo.sl_dist - 0.30) < 1e-9

def test_turtle_20_day_levels_and_2n_stop():
    d = flat_daily(70, rng=0.0100)
    o = FaithTurtle().plan(d, "EUR_USD", {})
    lo = [x for x in o if x.side == 1][0]
    assert abs(lo.price - (1.1050 + 0.0001)) < 1e-9 and abs(lo.sl_dist - 0.02) < 1e-9 and lo.meta["lookback"] == 20

def test_turtle_skips_after_winning_breakout_uses_55():
    rows = [(1.10, 1.105, 1.095, 1.10)] * 60
    # breakout up then strong trend then 10-day low exit at profit -> last breakout = winner
    px = 1.10
    for i in range(25):
        px += 0.004; rows.append((px - 0.002, px + 0.003, px - 0.003, px))
    for i in range(15):
        px -= 0.002; rows.append((px + 0.001, px + 0.002, px - 0.004, px))
    d = daily(rows)
    st = {}
    winner, open_ = _hypo_update(d, turtle_n(d), st)
    assert winner is True or winner == True
    o = FaithTurtle().plan(d, "EUR_USD", {})
    assert o and o[0].meta["lookback"] == 55

def test_turtle_manage_trails_to_10day_low():
    from fxexp.strategies.base import Position
    d = flat_daily(30, rng=0.0100)
    p = Position(1, 1.10, 1.08, None, 0.02, 20, None)
    m = FaithTurtle().manage(p, d, "EUR_USD", {})
    assert abs(m.new_sl - (1.0950 - 0.0001)) < 1e-9

def test_soup_requires_old_prior_low():
    rows = [(1.10, 1.105, 1.095, 1.10)] * 30
    rows[10] = (1.10, 1.105, 1.080, 1.10)  # 20-day low long ago
    d = daily(rows)
    o = RaschkeSoup().plan(d, "EUR_USD", {})
    lo = [x for x in o if x.side == 1][0]
    assert abs(lo.price - (1.080 + 0.0005)) < 1e-9 and abs(lo.arm - (1.080 - 0.0001)) < 1e-9 and lo.sl_daylow
    rows[-2] = (1.10, 1.105, 1.079, 1.10)  # new 20-day low only 2 sessions ago -> no long setup
    o2 = RaschkeSoup().plan(daily(rows), "EUR_USD", {})
    assert not [x for x in o2 if x.side == 1]

def test_soup_time_exit_and_reentry():
    from fxexp.strategies.base import Position
    d = flat_daily(40); s = RaschkeSoup()
    p = Position(1, 1.10, 1.09, None, .01, len(d) - 6, None, meta={"level": 1.10})
    assert s.manage(p, d, "EUR_USD", {}).close
    r = s.after_stop(p, 1, "EUR_USD", {})
    assert r and r.price == 1.10 and r.expires_days == 1 and r.meta["reentry"]
    p.meta["reentry"] = True
    assert s.after_stop(p, 1, "EUR_USD", {}) is None
    assert s.after_stop(Position(1, 1.1, 1.09, None, .01, 0, None, meta={"level": 1.1}), 3, "EUR_USD", {}) is None

def test_bollinger_first_breakout_after_squeeze():
    rng = np.random.default_rng(1)
    c = list(1.10 + rng.normal(0, 0.004, 150).cumsum() * 0.2)
    c += [c[-1]] * 1 + [c[-1] + rng.normal(0, 0.00005) for _ in range(20)]
    rows = [(x, x + 0.001, x - 0.001, x) for x in c]
    last = c[-1] + 0.01; rows.append((c[-1], last + 0.001, c[-1] - 0.0005, last))
    d = daily(rows)
    o = BollingerSqueeze().plan(d, "EUR_USD", {})
    assert len(o) == 1 and o[0].side == 1 and o[0].kind == "market" and o[0].sl < c[-1]
    rows.append((last, last + 0.012, last, last + 0.011))   # second close outside: not 'first' breakout
    assert BollingerSqueeze().plan(daily(rows), "EUR_USD", {}) == []

def test_crabel_idnr4_and_stretch():
    rows = [(1.10, 1.1040, 1.0960, 1.10)] * 14
    rows.append((1.10, 1.1020, 1.0985, 1.10))  # inside + narrowest of 4
    d = daily(rows)
    o = CrabelNR4().plan(d, "EUR_USD", {})
    st = np.mean([min(r[1] - r[0], r[0] - r[2]) for r in rows[-10:]])
    assert len(o) == 2 and abs(o[0].from_open - st) < 1e-12 and o[0].sl_from_open == -o[0].from_open and o[0].close_eod
    rows[-1] = (1.10, 1.1045, 1.0985, 1.10)  # not inside
    assert CrabelNR4().plan(daily(rows), "EUR_USD", {}) == []

def test_bollinger_manage_recovers_without_meta():
    from fxexp.strategies.base import Position
    d = flat_daily(30)
    p = Position(1, 1.10, 1.09, None, .01, 20, None, meta={})
    m = BollingerSqueeze().manage(p, d, "EUR_USD", {})
    assert m.new_sl >= 1.09 and "sar" in p.meta

def test_soup_no_reentry_without_level():
    from fxexp.strategies.base import Position
    assert RaschkeSoup().after_stop(Position(1, 1.1, 1.09, None, .01, 0, None, meta={}), 1, "EUR_USD", {}) is None
