import math
import numpy as np
import pandas as pd
import pytest
from fxexp import indicators as ind
from fxexp.data import trade_date, to_daily
from fxexp.sizing import units_for_risk, quote_to_usd
from fxexp.instruments import pip, spread
from tests.helpers import daily, flat_daily

def test_trade_date_boundary_dst_and_standard():
    s = pd.Series(pd.to_datetime(["2024-07-10 20:59", "2024-07-10 21:00", "2024-01-10 21:59", "2024-01-10 22:00"], utc=True))
    d = trade_date(s).tolist()
    assert [str(x) for x in d] == ["2024-07-10", "2024-07-11", "2024-01-10", "2024-01-11"]

def test_sunday_open_belongs_to_monday():
    s = pd.Series(pd.to_datetime(["2024-01-07 22:00"], utc=True))  # Sunday 17:00 NY
    assert str(trade_date(s).iloc[0]) == "2024-01-08"

def test_to_daily_aggregates_ohlc():
    t = pd.date_range("2024-01-07 22:00", periods=6, freq="4h", tz="UTC")
    df = pd.DataFrame({"time": t, "open": [1, 2, 3, 4, 5, 6], "high": [2, 9, 4, 5, 6, 7], "low": [0.5, 1, 2, 3, 4, 5], "close": [2, 3, 4, 5, 6, 6.5]})
    d = to_daily(df)
    assert len(d) == 1 and d.iloc[0][["open", "high", "low", "close"]].tolist() == [1, 9, 0.5, 6.5]

def test_wilder_seed_and_recursion():
    s = pd.Series([1.0] * 5 + [2.0])
    w = ind.wilder(s, 5)
    assert math.isnan(w.iloc[3]) and w.iloc[4] == 1.0 and abs(w.iloc[5] - 1.2) < 1e-12

def test_adx_bounds_and_trend():
    up = daily([(1 + i * .01, 1 + i * .01 + .008, 1 + i * .01 - .002, 1 + i * .01 + .006) for i in range(80)])
    a = ind.adx(up, 14).dropna()
    assert (a >= 0).all() and (a <= 100).all() and a.iloc[-1] > 50

def test_bollinger_population_sd():
    c = pd.Series([1.0, 2.0, 3.0, 4.0]); lo, mid, up = ind.bollinger(c, 4, 2)
    assert abs(up.iloc[-1] - (2.5 + 2 * np.std([1, 2, 3, 4]))) < 1e-12

def test_parabolic_long_never_above_lows():
    sar, ep, af = ind.parabolic_step(1.0, 1.2, 0.02, True, 1.25, 1.003, 1.21, 1.002)
    assert sar <= 1.002 and ep == 1.25 and abs(af - 0.04) < 1e-12

def test_turtle_n_equals_wilder_tr():
    d = flat_daily(30, rng=0.01)
    assert abs(ind.turtle_n(d).iloc[-1] - 0.01) < 1e-12

def test_sizing_usd_quote():
    u = units_for_risk("EUR_USD", 1.1000, 1.0970, 100_000, 0.5, {"EUR_USD": 1.1}, max_margin_frac=1.0)
    assert u == 166_666

def test_sizing_jpy_and_cross():
    p = {"USD_JPY": 150.0, "EUR_JPY": 160.0, "EUR_USD": 1.0667}
    u = units_for_risk("USD_JPY", 150.0, 149.7, 100_000, 0.5, p, max_margin_frac=1.0)
    assert abs(u * 0.3 / 150.0 - 500) < 1
    u2 = units_for_risk("EUR_JPY", 160.0, 159.5, 100_000, 0.5, p, max_margin_frac=1.0)
    assert abs(u2 * 0.5 / 150.0 - 500) < 1

def test_sizing_margin_cap():
    u = units_for_risk("EUR_USD", 1.1, 1.0999, 100_000, 1.0, {"EUR_USD": 1.1}, margin_rate=0.02, max_margin_frac=0.25)
    assert u == int(0.25 * 100_000 / (0.02 * 1.1))

def test_sizing_zero_distance():
    assert units_for_risk("EUR_USD", 1.1, 1.1, 1e5, 0.5, {"EUR_USD": 1.1}) == 0

def test_pip_sizes():
    assert pip("USD_JPY") == 0.01 and pip("EUR_USD") == 0.0001 and spread("GBP_JPY") == pytest.approx(0.03)
