import json
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import pytest
from fxexp.strategies import (BY_SID, NEW6, LienDoubleZeros, WilliamsVBO, PonsiTrend, BrooksRangeFade,
                              TharpRandom, DaltonValueArea)
from fxexp.strategies._bars import resample
from fxexp.strategies.dalton_value import volume_profile, value_area
from fxexp.strategies.tharp_random import coin
from fxexp.strategies.base import Position
from fxexp.live import Runner, order_body, trading_day
from fxexp.instruments import EXTRA_PAIRS, PAIRS
from tests.helpers import daily, flat_daily
NY = ZoneInfo("America/New_York")

def ny(y, m, d, h, mi=0): return datetime(y, m, d, h, mi, tzinfo=NY).astimezone(timezone.utc)

def m5(rows, start):
    """rows: list of (o,h,l,c[,vol]); start: aware datetime of the first bar."""
    out = []
    for k, r in enumerate(rows):
        o, h, l, c = r[:4]; v = r[4] if len(r) > 4 else 10
        t = pd.Timestamp(start + timedelta(minutes=5 * k)).tz_convert("UTC")
        out.append({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
    df = pd.DataFrame(out); df["td"] = [trading_day(t.to_pydatetime()) for t in df["time"]]
    return df

def pos(side, entry, sl, meta=None): return Position(side, entry, sl, None, 0, 0, None, meta=meta or {})

# ---------------------------------------------------------------- registry / switches
def test_eleven_systems_registered_with_unique_cid_prefixes():
    assert len(BY_SID) == 11 and set(NEW6) <= set(BY_SID)
    assert len({s[:6] for s in BY_SID}) == 11

def test_resample_drops_incomplete_bucket():
    b = m5([(1, 1, 1, 1)] * 14, datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc))   # 70 minutes
    h = resample(b, 60)
    assert len(h) == 1 and h["volume"].iloc[0] == 120

# ---------------------------------------------------------------- Lien double zeros
def test_lien_zeros_buy_limit_below_figure_when_well_below_sma():
    rows = [(1.1080, 1.1082, 1.1078, 1.1080)] * 3 * 25 + [(1.1015, 1.1016, 1.1010, 1.1012)] * 3
    b = m5(rows, datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc))
    o = LienDoubleZeros().signal(None, b, "EUR_USD", {}, ny(2026, 10, 5, 3))
    assert len(o) == 1 and o[0].side == 1 and o[0].kind == "limit"
    assert o[0].price == pytest.approx(1.0995) and o[0].sl == pytest.approx(1.0975) and o[0].tp == pytest.approx(1.1035)

def test_lien_zeros_short_and_jpy_figure():
    rows = [(149.20, 149.22, 149.18, 149.20)] * 3 * 25 + [(149.85, 149.86, 149.80, 149.82)] * 3
    b = m5(rows, datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc))
    o = LienDoubleZeros().signal(None, b, "USD_JPY", {}, ny(2026, 10, 5, 3))
    assert o[0].side == -1 and o[0].price == pytest.approx(150.05) and o[0].sl == pytest.approx(150.25) and o[0].tp == pytest.approx(149.65)

def test_lien_zeros_no_signal_near_sma_or_same_bar_twice():
    b = m5([(1.1012, 1.1013, 1.1011, 1.1012)] * 90, datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc))
    assert LienDoubleZeros().signal(None, b, "EUR_USD", {}, ny(2026, 10, 5, 3)) == []
    rows = [(1.1080, 1.1082, 1.1078, 1.1080)] * 75 + [(1.1015, 1.1016, 1.1010, 1.1012)] * 3
    b2 = m5(rows, datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)); st = {}; s = LienDoubleZeros()
    assert s.signal(None, b2, "EUR_USD", st, None) and s.signal(None, b2, "EUR_USD", st, None) == []

# ---------------------------------------------------------------- Williams
def test_williams_levels_and_bailout():
    d = daily([(1.10, 1.105, 1.095, 1.10)] * 5 + [(1.10, 1.1060, 1.0960, 1.1000)])
    o = WilliamsVBO().plan(d, "EUR_USD", {})
    lo = [x for x in o if x.side == 1][0]; sh = [x for x in o if x.side == -1][0]
    assert lo.from_open == pytest.approx(0.0060) and lo.sl_dist == pytest.approx(0.0050) and lo.tp is None and lo.tp_dist is None
    assert sh.from_open == pytest.approx(-0.0060)
    assert WilliamsVBO().manage(pos(1, 1.0990, 1.09), d, "EUR_USD", {}).close        # close 1.1000 > entry
    assert not WilliamsVBO().manage(pos(1, 1.1010, 1.09), d, "EUR_USD", {}).close
    assert WilliamsVBO().manage(pos(-1, 1.1010, 1.11), d, "EUR_USD", {}).close

# ---------------------------------------------------------------- Ponsi
def trend_daily(n=260, step=0.001):
    rows = []; px = 1.0
    for i in range(n): px += step; rows.append((px - step / 2, px + 0.002, px - 0.002, px))
    return daily(rows, start="2025-01-01")

def test_ponsi_long_limit_at_ema10_with_half_atr_stop():
    d = trend_daily()
    o = PonsiTrend().plan(d, "EUR_USD", {})
    assert len(o) == 1 and o[0].side == 1 and o[0].kind == "limit"
    assert o[0].sl == pytest.approx(o[0].price - 0.5 * o[0].meta["atr"]) and o[0].tp is None

def test_ponsi_short_and_no_signal_in_flat_market():
    d = trend_daily(step=-0.001)
    assert PonsiTrend().plan(d, "EUR_USD", {})[0].side == -1
    assert PonsiTrend().plan(flat_daily(260), "EUR_USD", {}) == []

def test_ponsi_trailing_never_loosens_and_breakeven_at_1r():
    d = trend_daily(); s = PonsiTrend()
    o = s.plan(d, "EUR_USD", {})[0]
    m = s.manage(pos(1, 1.20, 1.10, {"risk": 0.003}), d, "EUR_USD", {})
    assert m.new_sl >= 1.20                                       # close far above entry+1R -> >= breakeven
    m2 = s.manage(pos(1, 2.0, 1.99, {"risk": 0.003}), d, "EUR_USD", {})
    assert m2.new_sl == pytest.approx(1.99)                      # never lowered

# ---------------------------------------------------------------- Brooks
def range_then_failed_breakout(bear=True):
    rows = []
    for h in range(48):                                         # oscillating hourly range 1.1000-1.1040
        up = h % 2 == 0
        o, c = (1.1005, 1.1095) if up else (1.1095, 1.1005)
        rows += [(o, max(o, c) + 0.0003, min(o, c) - 0.0003, c)] + [(c, c + 0.0001, c - 0.0001, c)] * 11
    if bear:  # spike above the range high (1.1098) then close back inside as a bear bar
        rows += [(1.1095, 1.1106, 1.1093, 1.1100)] + [(1.1100, 1.1101, 1.1085, 1.1087)] * 11
    else:
        rows += [(1.1005, 1.1007, 1.0994, 1.1000)] + [(1.1000, 1.1015, 1.0999, 1.1013)] * 11
    return m5(rows, datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc))

def test_brooks_sells_failed_upside_breakout_to_midpoint():
    o = BrooksRangeFade().signal(None, range_then_failed_breakout(True), "EUR_USD", {}, None)
    assert len(o) == 1 and o[0].side == -1 and o[0].kind == "stop" and o[0].close_eod
    assert o[0].price == pytest.approx(1.1085 - 0.0001) and o[0].sl == pytest.approx(1.1106 + 0.0001)
    assert o[0].tp == pytest.approx((1.1098 + 1.1002) / 2)

def test_brooks_buys_failed_downside_breakout():
    o = BrooksRangeFade().signal(None, range_then_failed_breakout(False), "EUR_USD", {}, None)
    assert len(o) == 1 and o[0].side == 1 and o[0].tp > o[0].price > o[0].sl

def test_brooks_no_signal_in_trend():
    rows = [(1.1 + k * 0.0002, 1.1 + k * 0.0002 + 0.0002, 1.1 + k * 0.0002 - 0.0001, 1.1 + k * 0.0002 + 0.0001) for k in range(600)]
    assert BrooksRangeFade().signal(None, m5(rows, datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)), "EUR_USD", {}, None) == []

# ---------------------------------------------------------------- Tharp
def test_tharp_market_entry_3x_vol_and_own_pairs():
    d = flat_daily(40, rng=0.0060)
    o = TharpRandom().plan(d, "EUR_GBP", {})[0]
    assert o.kind == "market" and o.sl_dist == pytest.approx(3 * 0.0060, rel=1e-6) and o.tp is None
    assert o.side == coin("EUR_GBP", str(d.index[-1].date()))
    assert TharpRandom.pairs == EXTRA_PAIRS and not set(EXTRA_PAIRS) & set(PAIRS)

def test_tharp_coin_is_roughly_fair():
    flips = [coin("EUR_GBP", str(date(2020, 1, 1) + timedelta(days=i))) for i in range(2000)]
    assert 0.45 < flips.count(1) / 2000 < 0.55

def test_tharp_trailing_only_tightens():
    d = flat_daily(40, rng=0.0060)
    assert TharpRandom().manage(pos(1, 1.1, 1.05), d, "EUR_GBP", {}).new_sl == pytest.approx(1.1 - 0.018, rel=1e-6)
    assert TharpRandom().manage(pos(1, 1.1, 1.095), d, "EUR_GBP", {}).new_sl == pytest.approx(1.095)
    assert TharpRandom().manage(pos(-1, 1.1, 1.15), d, "EUR_GBP", {}).new_sl == pytest.approx(1.1 + 0.018, rel=1e-6)

# ---------------------------------------------------------------- Dalton
def test_value_area_dual_price_rule():
    prices = np.arange(10) * 1.0
    vol = np.array([1, 1, 2, 5, 9, 20, 8, 4, 1, 1], float)
    val, vah, poc = value_area(prices, vol)
    assert poc == 5.0 and val <= 4.0 and vah >= 6.0
    assert vol[int(val):int(vah) + 1].sum() >= 0.7 * vol.sum()

def test_volume_profile_spreads_volume():
    b = pd.DataFrame({"low": [1.1000], "high": [1.1004], "volume": [10]})
    px, v = volume_profile(b, 0.0001)
    assert len(v) == 5 and v.sum() == pytest.approx(10)

def dalton_bars(open_below=True):
    prev_start = datetime(2026, 10, 5, 17, 0, tzinfo=NY) - timedelta(days=1)      # Mon td = Sun 17:00
    prev = [(1.1000, 1.1010, 1.0990, 1.1000, 50)] * 150 + [(1.1000, 1.1000, 1.1000, 1.1000, 100)] * 50 + [(1.1000, 1.1040, 1.0960, 1.1000, 1)] * 88
    pb = m5(prev, prev_start); val, vah, _ = value_area(*volume_profile(pb, 0.0001))
    assert vah - val >= 0.00099
    cur_start = datetime(2026, 10, 5, 17, 0, tzinfo=NY)                           # Tue td
    if open_below:
        x = val - 0.0004; y = val + 0.0001
        cur = [(x, x + 0.0001, x - 0.0001, x)] * 6 + [(y, y + 0.00005, y - 0.00005, y)] * 12
    else:
        x = vah + 0.0004; y = vah - 0.0001
        cur = [(x, x + 0.0001, x - 0.0001, x)] * 6 + [(y, y + 0.00005, y - 0.00005, y)] * 12
    return pd.concat([pb, m5(cur, cur_start)], ignore_index=True), val, vah

def test_dalton_value_area_rule_long():
    b, val, vah = dalton_bars(True)
    o = DaltonValueArea().signal(None, b, "EUR_USD", {}, ny(2026, 10, 5, 18, 35))
    assert len(o) == 1 and o[0].side == 1 and o[0].kind == "market" and o[0].close_eod
    assert o[0].sl == pytest.approx(val - 0.0005 - 0.0001) and o[0].tp == pytest.approx(vah)

def test_dalton_short_and_quiet_hours():
    b, val, vah = dalton_bars(False)
    o = DaltonValueArea().signal(None, b, "EUR_USD", {}, ny(2026, 10, 5, 18, 35))
    assert len(o) == 1 and o[0].side == -1 and o[0].tp == pytest.approx(val) and o[0].sl == pytest.approx(vah + 0.0006)
    assert DaltonValueArea().signal(None, b, "EUR_USD", {}, ny(2026, 10, 6, 13, 0)) == []

def test_dalton_no_trade_when_open_inside_value():
    b, val, vah = dalton_bars(True)
    b.loc[b["td"] == b["td"].max(), ["open", "high", "low", "close"]] = (val + vah) / 2
    assert DaltonValueArea().signal(None, b, "EUR_USD", {}, ny(2026, 10, 5, 18, 35)) == []
# ---------------------------------------------------------------- runner integration
from tests.test_live import FakeCli, flat

class BarsCli(FakeCli):
    def __init__(self, d, bars, prices):
        super().__init__(d, intraday=(1.1, 1.09, 1.11), prices=prices); self.bars = bars
    def candles(self, pair, gran, count=None, frm=None, price="M"):
        if gran == "M5" and count and not frm and self.bars is not None:
            df = self.bars.drop(columns=["td"]).copy(); df["complete"] = True; return df
        return super().candles(pair, gran, count, frm, price)

def test_runner_places_intraday_limit_with_short_gtd(tmp_path):
    rows = [(1.1080, 1.1082, 1.1078, 1.1080)] * 75 + [(1.1015, 1.1016, 1.1010, 1.1012)] * 3
    bars = m5(rows, datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc))
    cli = BarsCli(flat(), bars, {"EUR_USD": (1.10113, 1.10127, True)})
    now = ny(2026, 10, 5, 3, 0)
    Runner({"systems": ["lien-zeros"], "pairs": ["EUR_USD"], "risk_pct": 0.25}, {"lien-zeros": cli}, str(tmp_path / "s.json"), now=now, log=lambda m: None).run()
    c = [w[1] for w in cli.writes if w[0] == "create"]
    assert len(c) == 1 and c[0]["type"] == "LIMIT" and c[0]["price"] == "1.09950"
    assert c[0]["stopLossOnFill"]["price"] == "1.09750" and c[0]["takeProfitOnFill"]["price"] == "1.10350"
    gtd = pd.Timestamp(c[0]["gtdTime"])
    assert gtd == pd.Timestamp(now + timedelta(minutes=60))
    units = int(c[0]["units"]); assert units == pytest.approx(100000 * 0.0025 / 0.0020, abs=1)   # 0.25% of NAV over 20 pips
    # second run: nothing new while the order is live
    cli.writes.clear()
    Runner({"systems": ["lien-zeros"], "pairs": ["EUR_USD"], "risk_pct": 0.25}, {"lien-zeros": cli}, str(tmp_path / "s.json"), now=now + timedelta(minutes=5), log=lambda m: None).run()
    assert not [w for w in cli.writes if w[0] == "create"]

def test_runner_tharp_market_order_on_own_pairs_with_distance_stop(tmp_path):
    prices = {p: (1.0, 1.0001, True) for p in ["GBP_USD", "EUR_USD", "AUD_USD", "NZD_USD"]}
    prices.update({"USD_JPY": (150.0, 150.01, True), "USD_CHF": (0.8, 0.8001, True), "USD_CAD": (1.37, 1.3701, True),
                   "EUR_GBP": (0.86990, 0.87005, True)})
    cli = FakeCli(flat(), prices=prices)
    cfg = {"systems": ["tharp-random"], "pairs": ["EUR_USD"], "risk_pct": 0.25, "system_pairs": {"tharp-random": ["EUR_GBP"]}}
    Runner(cfg, {"tharp-random": cli}, str(tmp_path / "s.json"), now=ny(2026, 10, 4, 18), log=lambda m: None).run()
    c = [w[1] for w in cli.writes if w[0] == "create"]
    assert len(c) == 1 and c[0]["instrument"] == "EUR_GBP" and c[0]["type"] == "MARKET" and "distance" in c[0]["stopLossOnFill"]
    assert "takeProfitOnFill" not in c[0]

def test_order_body_limit():
    from fxexp.strategies.base import Order
    b = order_body("EUR_USD", Order(1, kind="limit", price=1.0995, sl=1.0975, tp=1.1035), 100, 1.0995, 1.0975, None, 1.1035,
                   ny(2026, 10, 5, 4), "c", "lien-zeros", 0.00014)
    assert b["type"] == "LIMIT" and b["timeInForce"] == "GTD" and "priceBound" not in b and b["stopLossOnFill"]["price"] == "1.09750"

def test_config_all_eleven_on_at_quarter_percent():
    c = json.load(open("config/config.json"))
    assert set(c["systems"]) == set(BY_SID) and c["risk_pct"] == 0.25

def test_runner_defers_new_orders_when_margin_used_high(tmp_path):
    rows = [(1.1080, 1.1082, 1.1078, 1.1080)] * 75 + [(1.1015, 1.1016, 1.1010, 1.1012)] * 3
    bars = m5(rows, datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc))
    cli = BarsCli(flat(), bars, {"EUR_USD": (1.10113, 1.10127, True)})
    cli.summary = lambda: {"NAV": "100000", "marginUsed": "60000"}
    Runner({"systems": ["lien-zeros"], "pairs": ["EUR_USD"], "risk_pct": 0.25}, {"lien-zeros": cli}, str(tmp_path / "s.json"),
           now=ny(2026, 10, 5, 3, 0), log=lambda m: None).run()
    assert not [w for w in cli.writes if w[0] == "create"]
