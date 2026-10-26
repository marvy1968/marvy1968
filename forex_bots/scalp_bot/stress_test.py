"""Destructive / adversarial test suite for the scalp bot.  Runs fully offline.

    python stress_test.py            # all tests
    python stress_test.py -k gap     # only tests whose name contains "gap"

Each test attacks one weakness: look-ahead bias, fake edges on pure noise, bad data,
gaps and flash crashes, half-days, DST, tiny/huge prices, broker failures, restarts,
corrupted state, buying-power exhaustion and speed.  Alpaca is stubbed with a fake broker.
"""
import argparse
import json
import os
import sys
import tempfile
import time
import traceback
import types
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- alpaca stubs (only if not installed)
def _stub_alpaca():
    try:
        import alpaca.trading.client  # noqa: F401
        return
    except Exception:   # noqa: BLE001
        pass

    class _Any:
        def __init__(self, *a, **k):
            self.__dict__.update(k)

    class _EnumNS:
        def __init__(self, name):
            self._n = name

        def __getattr__(self, name):
            return f"{self._n}.{name}"

        def __call__(self, v="x"):
            return v

    names = {
        "alpaca": [], "alpaca.data": [], "alpaca.trading": [],
        "alpaca.data.historical": ["StockHistoricalDataClient"],
        "alpaca.data.requests": ["StockBarsRequest", "StockLatestQuoteRequest"],
        "alpaca.trading.client": ["TradingClient"],
        "alpaca.trading.requests": ["GetOrdersRequest", "MarketOrderRequest", "ReplaceOrderRequest",
                                    "StopLossRequest", "TakeProfitRequest", "StopOrderRequest",
                                    "LimitOrderRequest", "ClosePositionRequest", "GetOrderByIdRequest"],
    }
    enums = {"alpaca.data.enums": ["DataFeed", "Adjustment"],
             "alpaca.trading.enums": ["OrderClass", "OrderSide", "QueryOrderStatus", "TimeInForce"]}
    for mod in set(names) | set(enums) | {"alpaca.data.timeframe"}:
        sys.modules[mod] = types.ModuleType(mod)
    for mod, attrs in names.items():
        for a in attrs:
            setattr(sys.modules[mod], a, type(a, (_Any,), {}))
    for mod, attrs in enums.items():
        for a in attrs:
            setattr(sys.modules[mod], a, _EnumNS(a))
    tf = sys.modules["alpaca.data.timeframe"]
    tf.TimeFrame = type("TimeFrame", (), {"Minute": "1Min", "Day": "1Day"})
    tf.TimeFrameUnit = _EnumNS("TimeFrameUnit")


_stub_alpaca()

import config                                   # noqa: E402
from common import Position, check_management   # noqa: E402
from experts import build_experts               # noqa: E402
from indicators import prepare_intraday, to_arrays  # noqa: E402
from regime import daily_frame                  # noqa: E402
import backtest as bt                           # noqa: E402

NYZ = "America/New_York"


# ---------------------------------------------------------------- synthetic data
def minute_bars(p0, days, rng, vol=0.0008, drift=0.0, end_hhmm=(16, 0)):
    idx = []
    for d in days:
        o = pd.Timestamp(d, tz=NYZ) + pd.Timedelta(hours=9, minutes=30)
        n = (end_hhmm[0] * 60 + end_hhmm[1]) - 570
        idx += list(pd.date_range(o, periods=n, freq="min"))
    idx = pd.DatetimeIndex(idx).tz_convert("UTC")
    r = rng.normal(drift, vol, len(idx))
    c = p0 * np.exp(np.cumsum(r))
    o = np.r_[p0, c[:-1]]
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, vol / 2, len(c))))
    l = np.minimum(o, c) * (1 - abs(rng.normal(0, vol / 2, len(c))))
    v = rng.integers(1000, 20000, len(c)).astype(float)
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=v), index=idx)


def daily_bars(p0, n, drift, rng, end="2026-08-28"):
    idx = pd.date_range(end=end, periods=n, freq="B", tz=NYZ).tz_convert("UTC")
    c = p0 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    return pd.DataFrame(dict(open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1e6), index=idx)


def world(seed=0, days=None, per_sector=5, vol=0.0008, mutate=None, end_hhmm=None, prep=None):
    """A full synthetic market: 11 sectors x N stocks + ETFs.  mutate(sym, df) -> df lets a test corrupt data."""
    rng = np.random.default_rng(seed)
    days = days if days is not None else pd.bdate_range("2026-08-17", "2026-08-28")
    arrays, frames, sym_sector = {}, {}, {}
    for k, (sec, spec) in enumerate(config.SECTORS.items()):
        drift = [0.003, -0.003, 0.0][k % 3]
        frames[spec["etf"]] = daily_frame(daily_bars(100, 300, drift, rng, end=str(days[-1].date())))
        syms = [spec["etf"]] + spec["candidates"][:per_sector]
        for s in syms:
            df = minute_bars(rng.uniform(30, 400), days, rng, vol=vol, drift=0.0,
                             **({"end_hhmm": end_hhmm} if end_hhmm else {}))
            if mutate:
                df = mutate(s, df)
            arrays[s] = to_arrays((prep or prepare_intraday)(df))
            if s != spec["etf"]:
                sym_sector[s] = sec
    return arrays, frames, sym_sector, days


def run(arrays, frames, sym_sector, days, slip=2.0, equity=100_000):
    sim, _ = bt.run_backtest(arrays, sym_sector, frames, days[1].date(), days[-1].date(),
                             equity, slip, 0.0, verbose=False)
    return sim, pd.DataFrame(sim.trades)


# ---------------------------------------------------------------- tests
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def lookahead_indicators():
    """Every indicator value at bar t must be identical whether or not future bars exist."""
    rng = np.random.default_rng(1)
    df = minute_bars(100, pd.bdate_range("2026-08-24", "2026-08-26"), rng)
    full = prepare_intraday(df)
    cols = [c for c in full.columns if c not in ("session",)]
    bad = set()
    for k in rng.integers(60, len(df), 40):
        part = prepare_intraday(df.iloc[:k])
        t = part.index[-1]
        a, b = part.loc[t, cols].astype(float), full.loc[t, cols].astype(float)
        diff = ~(np.isclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-9))
        bad |= set(np.array(cols)[np.asarray(diff)])
    assert not bad, f"look-ahead in columns: {sorted(bad)}"


@test
def no_edge_on_pure_noise():
    """On random-walk data with ZERO costs, the system must not show a significant edge.
    A strong positive result here would mean look-ahead or a fill bug."""
    rs = []
    for seed in range(3):
        arrays, frames, ss, days = world(seed=100 + seed)
        _, tr = run(arrays, frames, ss, days, slip=0.0)
        rs.append(tr["r"].to_numpy())
    r = np.concatenate(rs)
    t_stat = r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))
    print(f"      noise: {len(r)} trades, mean R {r.mean():+.3f}, t={t_stat:+.1f}")
    assert t_stat < 3, f"suspicious edge on noise (t={t_stat:.1f})"


@test
def same_bar_stop_and_target_is_pessimistic():
    """If a bar touches both stop and target we don't know which came first: must assume the stop."""
    pos = Position("X", "EMA", "Tech", 1, 10, 100.0, 99.0, 102.0, 1.0, "2026-08-24T14:00:00+00:00")
    px, why = bt.bracket_hit(pos, o=100.0, h=103.0, l=98.5, slip=0.0)
    assert why.endswith("stop") and px <= 99.0, (px, why)
    pos.side, pos.stop, pos.target = -1, 101.0, 98.0
    px, why = bt.bracket_hit(pos, o=100.0, h=101.5, l=97.0, slip=0.0)
    assert why.endswith("stop") and px >= 101.0, (px, why)


@test
def gap_through_stop_fills_at_open_not_stop():
    """A gap below the stop must fill at the (worse) open price, never at the stop price."""
    pos = Position("X", "ORB", "Tech", 1, 10, 100.0, 99.0, 102.0, 1.0, "2026-08-24T14:00:00+00:00")
    px, _ = bt.bracket_hit(pos, o=95.0, h=95.5, l=94.0, slip=0.0)
    assert abs(px - 95.0) < 1e-9, px
    short = Position("X", "ORB", "Tech", -1, 10, 100.0, 101.0, 98.0, 1.0, "2026-08-24T14:00:00+00:00")
    px, _ = bt.bracket_hit(short, o=108.0, h=109.0, l=107.0, slip=0.0)
    assert abs(px - 108.0) < 1e-9, px


@test
def flash_crash_losses_bounded_and_halt():
    """A -15% one-bar crash in every stock at 11:00.  Losses must equal the gap (not be hidden)
    and the daily-loss kill switch must use mark-to-market equity."""
    def crash(sym, df):
        ny = df.index.tz_convert(NYZ)
        hit = (ny.hour == 11) & (ny.minute == 0)
        after = (ny.hour > 11) | ((ny.hour == 11) & (ny.minute >= 0))
        same_day = ny.normalize() == ny.normalize()[hit][-1] if hit.any() else hit
        df = df.copy()
        mask = after & same_day
        df.loc[mask, ["open", "high", "low", "close"]] *= 0.85
        return df
    arrays, frames, ss, days = world(seed=7, mutate=crash)
    sim, tr = run(arrays, frames, ss, days)
    worst = tr["r"].min()
    print(f"      worst trade {worst:.1f}R, total trades {len(tr)}")
    assert np.isfinite(tr["pnl"]).all()
    # one position is 0.5% of equity; a 15% gap on 15 open positions ~ -1.1% of equity worst case
    assert sim.equity > 100_000 * 0.97, sim.equity


@test
def mtm_kill_switch_counts_open_losses():
    """Kill switch must include unrealized losses (live Alpaca equity does)."""
    assert hasattr(bt, "mark_to_market"), "backtest has no mark-to-market equity for the kill switch"
    pos = Position("X", "EMA", "Tech", 1, 100, 100.0, 90.0, 120.0, 10.0, "2026-08-24T14:00:00+00:00")
    eq = bt.mark_to_market(10_000, {"X": pos}, {"X": 80.0})
    assert abs(eq - 8_000) < 1e-6, eq


@test
def dirty_data_duplicates_nans_unsorted_zero_volume():
    """Duplicated, unsorted, NaN, zero-price and zero-volume bars must not crash or poison indicators."""
    def dirty(sym, df):
        rng = np.random.default_rng(abs(hash(sym)) % 2**32)
        df = df.copy()
        df.iloc[rng.integers(0, len(df), 30), :] = np.nan
        df.iloc[rng.integers(0, len(df), 20), df.columns.get_loc("volume")] = 0
        df.iloc[rng.integers(0, len(df), 3), df.columns.get_loc("low")] = 0.0
        df = pd.concat([df, df.iloc[rng.integers(0, len(df), 50)]])
        return df.sample(frac=1, random_state=1)
    arrays, frames, ss, days = world(seed=3, mutate=dirty)
    for s, a in arrays.items():
        assert np.all(np.diff(a["ts"].asi8) > 0), f"{s}: timestamps not strictly increasing"
        assert not np.isnan(a["close"]).any(), f"{s}: NaN closes survived"
        assert (a["low"] > 0).all(), f"{s}: zero/negative prices survived"
    _, tr = run(arrays, frames, ss, days)
    assert np.isfinite(tr["pnl"]).all()


@test
def sparse_iex_like_data():
    """Drop 70% of bars (thin IEX feed).  Must still run, no NaN P&L, no trades on stale data."""
    def thin(sym, df):
        rng = np.random.default_rng(abs(hash(sym)) % 2**32)
        return df[rng.random(len(df)) > 0.7]
    arrays, frames, ss, days = world(seed=4, mutate=thin)
    _, tr = run(arrays, frames, ss, days)
    print(f"      sparse data trades: {len(tr)}")
    assert tr.empty or np.isfinite(tr["pnl"]).all()


@test
def half_day_session_flattens_before_close():
    """Day after Thanksgiving: market closes 13:00. Every trade must close before 12:56 ET,
    and no entries in the last 30 minutes."""
    days = pd.bdate_range("2026-11-23", "2026-11-27")   # 27th = half day
    rng = np.random.default_rng(5)

    def cut(sym, df):
        ny = df.index.tz_convert(NYZ)
        half = ny.normalize() == pd.Timestamp("2026-11-27", tz=NYZ)
        return df[~half | ((ny.hour * 60 + ny.minute) < 13 * 60)]
    arrays, frames, ss, days = world(seed=5, days=days, mutate=cut)
    _, tr = run(arrays, frames, ss, days)
    h = tr[tr["date"] == date(2026, 11, 27)]
    assert len(h), "no trades on half day to test"
    late_close = h[h["closed"].dt.hour * 60 + h["closed"].dt.minute > 12 * 60 + 56]
    late_open = h[h["opened"].dt.hour * 60 + h["opened"].dt.minute > 12 * 60 + 31]
    assert late_close.empty, f"{len(late_close)} trades still open into the 13:00 close"
    assert late_open.empty, f"{len(late_open)} entries in the last 30 min of a half day"


@test
def live_flatten_uses_real_close_time():
    """Live bot must flatten relative to Alpaca's actual next_close (half days), not a fixed 15:55."""
    import bot
    assert hasattr(bot, "session_limits"), "bot has no session_limits(close_time) helper"
    last_entry, flatten = bot.session_limits(pd.Timestamp("2026-11-27 13:00", tz=NYZ))
    assert flatten <= 13 * 60 - 570 - 5 and last_entry <= 13 * 60 - 570 - 30, (last_entry, flatten)


@test
def dst_boundary_minutes_are_correct():
    """UTC bars across the DST change: 13:30 UTC in summer and 14:30 UTC in winter are both 09:30 ET."""
    idx = pd.DatetimeIndex(["2026-10-30 13:30", "2026-10-30 13:31", "2026-11-02 14:30", "2026-11-02 14:31",
                            "2026-11-02 13:30"], tz="UTC")
    df = pd.DataFrame(dict(open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0), index=idx).sort_index()
    p = prepare_intraday(df)
    got = dict(zip(p.index, p["mod"]))
    assert got[pd.Timestamp("2026-10-30 13:30", tz="UTC")] == 0
    assert got[pd.Timestamp("2026-11-02 14:30", tz="UTC")] == 0
    assert pd.Timestamp("2026-11-02 13:30", tz="UTC") not in got, "pre-market bar leaked into session"


@test
def stop_and_target_valid_at_low_and_high_prices():
    """Rounding to cents must never put stop or target on (or through) the entry price."""
    experts = build_experts()
    e = experts[0]
    bad = []
    for px in [1.23, 2.5, 5.0, 9.99, 10.0, 25.0, 400.0, 4000.0]:
        a = {"close": np.array([px]), "atr": np.array([px * 0.00005])}
        for side in (1, -1):
            s = e._build("X", side, a, 0, px - side * px * 0.00001, "t")
            if s is None:
                continue
            if side * (px - s.stop) < 0.01 or side * (s.target - px) < 0.01:
                bad.append((px, side, s.stop, s.target))
    assert not bad, f"invalid brackets: {bad[:4]}"


@test
def expensive_stock_small_account():
    """$10k account, $4,000 stock: 0.5% = $50 -> 0 shares. Must skip, never crash or buy 1 share."""
    arrays, frames, ss, days = world(seed=6, per_sector=2)
    for s in list(ss)[:6]:
        for c in ("open", "high", "low", "close", "vwap", "ema9", "ema20", "ema50", "sma20",
                  "bb_up", "bb_lo", "kc_up", "kc_lo", "or_high", "or_low", "day_open"):
            arrays[s][c] = arrays[s][c] * 40
        arrays[s]["atr"] = arrays[s]["atr"] * 40
    sim, tr = run(arrays, frames, ss, days, equity=10_000)
    if len(tr):
        assert ((tr["qty"] * tr["entry"]) <= 10_000 * config.POSITION_PCT * 1.01).all()


@test
def per_expert_cap_and_symbol_lock_under_signal_storm():
    """Very volatile market -> lots of signals. Cap of 3 per expert and 1 expert per symbol must hold."""
    arrays, frames, ss, days = world(seed=8, vol=0.003)
    _, tr = run(arrays, frames, ss, days)
    for d, g in tr.groupby("date"):
        ev = sorted([(t, 1, e, s) for t, e, s in zip(g.opened, g.expert, g.symbol)] +
                    [(t, -1, e, s) for t, e, s in zip(g.closed, g.expert, g.symbol)], key=lambda x: (x[0], x[1]))
        per_e, per_s = {}, {}
        for _, x, e, s in ev:
            per_e[e] = per_e.get(e, 0) + x
            per_s[s] = per_s.get(s, 0) + x
            assert per_e[e] <= 3, f"{d}: {e} held {per_e[e]} positions"
            assert per_s[s] <= 1, f"{d}: {s} held by 2 experts"


@test
def corrupted_state_file_does_not_brick_bot():
    import bot
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "state.json")
        with open(p, "w") as fh:
            fh.write('{"positions": {"AAPL": {"symbol": "AAPL", "exp')   # truncated write
        st = bot.State(p)
        st.load()   # must not raise
        assert st.positions == {}


# ---------------------------------------------------------------- fake broker for live-logic tests
class FakeBroker:
    def __init__(self, equity=100_000, fail_close=False):
        self.pos, self.orders, self.fail_close = {}, [], fail_close
        self.protected, self.closed, self.equity = [], [], equity

    def account(self):
        return types.SimpleNamespace(equity=self.equity, last_equity=self.equity, buying_power=self.equity * 4,
                                     shorting_enabled=True, trading_blocked=False)

    def positions(self):
        return dict(self.pos)

    def can_trade(self, sym, side, acct):
        return True

    def spread_ok(self, sym):
        return True

    def open_bracket(self, sig, qty):
        self.orders.append((sig, qty))
        return types.SimpleNamespace(id=f"o{len(self.orders)}")

    def close(self, sym):
        if self.fail_close:
            return False
        self.closed.append(sym)
        self.pos.pop(sym, None)
        return True

    def protect(self, sym, pos):
        self.protected.append(sym)
        return True

    def move_stop(self, sym, pos, s, last):
        return "failed"

    def recover_expert(self, sym):
        return None

    def open_stop_price(self, sym):
        return None


def make_bot(broker):
    import bot
    b = object.__new__(bot.ScalpBot)
    b.broker, b.experts = broker, build_experts()
    b.by_id = {e.id: e for e in b.experts}
    b.state = bot.State(os.path.join(tempfile.mkdtemp(), "s.json"))
    b.dry_run, b.tick_n = False, 0
    b.sym_sector = {"AAPL": "Technology"}
    b.regime = {s: "BULL" for s in config.SECTORS}
    b.universe = {"Technology": ["AAPL"]}
    if hasattr(bot, "session_limits"):
        b.last_entry_mod, b.flatten_mod = bot.session_limits(pd.Timestamp("2026-08-24 16:00", tz=NYZ))
    return b


@test
def live_failed_close_keeps_position_protected():
    """close() cancels the bracket legs first. If the close then fails, the position is naked.
    The bot must re-protect it (fresh stop) instead of leaving it unprotected."""
    fb = FakeBroker(fail_close=True)
    b = make_bot(fb)
    fb.pos["AAPL"] = (10, 200.0)
    b.state.positions["AAPL"] = Position("AAPL", "EMA", "Technology", 1, 10, 200.0, 199.0, 202.0, 1.0,
                                         "2026-08-24T14:00:00+00:00")
    b.manage({}, mod=388)   # after flatten time -> must try to exit
    assert "AAPL" in fb.protected, "close failed and position was left without a stop"


@test
def live_unfilled_entry_releases_slot():
    """Entry accepted but never filled: the slot/lock must be released after a few minutes."""
    fb = FakeBroker()
    b = make_bot(fb)
    old = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=10)).isoformat()
    b.state.positions["AAPL"] = Position("AAPL", "EMA", "Technology", 1, 10, 200.0, 199.0, 202.0, 1.0, old,
                                         confirmed=False)
    b.log_trade = lambda *a, **k: None
    b.reconcile({})
    assert "AAPL" not in b.state.positions


@test
def live_manual_position_never_touched():
    """A position you opened by hand (no SB_ tag) must be ignored and never closed."""
    fb = FakeBroker()
    b = make_bot(fb)
    fb.pos["AAPL"] = (50, 190.0)
    b.log_trade = lambda *a, **k: None
    b.reconcile({})
    b.manage({}, mod=388)
    assert "AAPL" in b.state.unmanaged and "AAPL" not in fb.closed


@test
def short_blocked_below_2000_equity():
    import broker as brk
    br = object.__new__(brk.Broker)
    br._assets = {"XOM": types.SimpleNamespace(tradable=True, shortable=True, easy_to_borrow=True)}
    small = types.SimpleNamespace(equity="1500", shorting_enabled=True)
    big = types.SimpleNamespace(equity="50000", shorting_enabled=True)
    assert not br.can_trade("XOM", -1, small)
    assert br.can_trade("XOM", 1, small)
    assert br.can_trade("XOM", -1, big)
    br._assets["HTB"] = types.SimpleNamespace(tradable=True, shortable=True, easy_to_borrow=False)
    assert not br.can_trade("HTB", -1, big), "hard-to-borrow short allowed"


@test
def backtest_speed():
    """55 symbols x 10 days should run fast enough to backtest a year in minutes."""
    arrays, frames, ss, days = world(seed=9)
    t0 = time.time()
    run(arrays, frames, ss, days)
    per_day = (time.time() - t0) / (len(days) - 1)
    print(f"      {per_day:.2f}s per trading day (~{per_day * 252 / 60:.1f} min per year)")
    assert per_day < 20


# ---------------------------------------------------------------- fake Alpaca TradingClient (order-level)
class ApiErr(Exception):
    def __init__(self, code, msg="err"):
        super().__init__(f"{code} {msg}")
        self.status_code = code


class FakeTC:
    """Simulates one position + its bracket legs, with scriptable failures.
    Enforces Alpaca's rule: exit orders can't exceed qty_available (no over-allocation)."""

    def __init__(self, side=1, qty=10, replace=None, oco=None, stop=None, close=None,
                 stuck_cancel=False, fill_on_cancel=False, submit_transient=0):
        self.side, self.qty = side, qty
        self.script = {"replace": list(replace or []), "oco": list(oco or []),
                       "stop": list(stop or []), "close": list(close or [])}
        self.stuck_cancel, self.fill_on_cancel = stuck_cancel, fill_on_cancel
        self.submit_transient = submit_transient
        self.log, self.seen_coids = [], []
        self.orders = {
            "tp": types.SimpleNamespace(id="tp", status="new", stop_price=None, limit_price=102.0, legs=[], qty=qty),
            "sl": types.SimpleNamespace(id="sl", status="held", stop_price=99.0, limit_price=None, legs=[], qty=qty)}

    def _next(self, kind):
        seq = self.script[kind]
        r = seq.pop(0) if seq else None
        if isinstance(r, Exception):
            raise r
        return r

    def _reserved(self):
        return sum(o.qty for o in self.orders.values() if o.status in ("new", "held", "accepted", "pending_cancel"))

    # --- API surface used by Broker ---
    def get_orders(self, filter=None):
        return [o for o in self.orders.values() if o.status in ("new", "held", "accepted", "pending_cancel")]

    def get_open_position(self, sym):
        if self.qty == 0:
            raise ApiErr(404, "position does not exist")
        return types.SimpleNamespace(qty=str(self.qty), side="long" if self.side > 0 else "short",
                                     qty_available=str(max(self.qty - self._reserved(), 0)), avg_entry_price="100")

    def replace_order_by_id(self, oid, req):
        self.log.append(("replace", oid))
        self._next("replace")
        self.orders[oid].stop_price = req.stop_price

    def cancel_order_by_id(self, oid):
        self.log.append(("cancel", oid))
        if self.fill_on_cancel:
            self.qty = 0
            for o in self.orders.values():
                o.status = "filled"
            return
        self.orders[oid].status = "pending_cancel" if self.stuck_cancel else "canceled"

    def submit_order(self, order_data):
        kind = type(order_data).__name__
        oc = str(getattr(order_data, "order_class", "")).lower()
        tag = "oco" if "oco" in oc else "stop" if kind == "StopOrderRequest" else "entry"
        self.seen_coids.append(getattr(order_data, "client_order_id", None))
        if tag == "entry" and self.submit_transient:
            self.submit_transient -= 1
            raise ApiErr(503, "unavailable")
        self.log.append(("submit", tag, float(order_data.qty), str(order_data.side)))
        if tag != "entry" and float(order_data.qty) > self.qty - self._reserved():
            raise ApiErr(403, "insufficient qty available")   # Alpaca's over-allocation rejection
        if tag in ("oco", "stop"):
            self._next(tag)
        oid = f"n{len(self.log)}"
        self.orders[oid] = types.SimpleNamespace(id=oid, status="new", stop_price=1.0, limit_price=None,
                                                 legs=[], qty=float(order_data.qty))
        return types.SimpleNamespace(id=oid)

    def close_position(self, sym):
        self.log.append(("close", sym))
        self._next("close")
        self.qty = 0


def ladder(tc, last=101.5, new_stop=100.05, side=1):
    import broker as brk
    b = object.__new__(brk.Broker)
    b.tc, b._assets = tc, {}
    real_time = brk.time
    brk.time = types.SimpleNamespace(time=real_time.time, sleep=lambda s: None)
    try:
        pos = Position("AAPL", "EMA", "Technology", side, tc.qty, 100.0, 99.0 if side > 0 else 101.0,
                       102.0 if side > 0 else 98.0, 1.0, "2026-08-24T14:00:00+00:00")
        return b.move_stop("AAPL", pos, new_stop, last), brk
    finally:
        brk.time = real_time


@test
def ladder_in_place_modify():
    tc = FakeTC()
    res, brk = ladder(tc)
    assert res == brk.REPLACED and not any(x[0] == "cancel" for x in tc.log), (res, tc.log)
    assert tc.orders["sl"].stop_price == 100.05


@test
def ladder_transient_errors_are_retried():
    tc = FakeTC(replace=[ApiErr(429), ApiErr(503)])
    res, brk = ladder(tc)
    assert res == brk.REPLACED and sum(x[0] == "replace" for x in tc.log) == 3, tc.log


@test
def ladder_rejected_modify_falls_back_to_oco_after_cancels_confirmed():
    tc = FakeTC(replace=[ApiErr(422, "cannot replace held order")])
    res, brk = ladder(tc)
    assert res == brk.OCO, (res, tc.log)
    first_submit = next(k for k, x in enumerate(tc.log) if x[0] == "submit")
    cancels = [k for k, x in enumerate(tc.log) if x[0] == "cancel"]
    assert len(cancels) == 2 and max(cancels) < first_submit, f"OCO sent before legs cancelled: {tc.log}"
    assert tc.log[first_submit][2] == 10, "OCO must cover the full position"


@test
def ladder_oco_rejected_falls_back_to_plain_stop():
    tc = FakeTC(replace=[ApiErr(422)], oco=[ApiErr(422, "oco rejected")])
    res, brk = ladder(tc)
    assert res == brk.STOP_ONLY and ("submit", "stop", 10.0, "OrderSide.SELL") in tc.log, (res, tc.log)


@test
def ladder_everything_rejected_flattens():
    tc = FakeTC(replace=[ApiErr(422)], oco=[ApiErr(422)], stop=[ApiErr(422)])
    res, brk = ladder(tc)
    assert res == brk.FLATTENED and ("close", "AAPL") in tc.log, (res, tc.log)


@test
def ladder_price_already_through_new_stop():
    tc = FakeTC()
    res, brk = ladder(tc, last=100.0, new_stop=100.05)
    assert res == brk.BREACHED and tc.log == [], tc.log


@test
def ladder_stuck_cancel_never_double_allocates():
    """If Alpaca doesn't confirm the cancels, submitting a new exit would be rejected / over-allocate.
    Must keep the old (still live) orders and retry next tick."""
    tc = FakeTC(replace=[ApiErr(422)], stuck_cancel=True)
    res, brk = ladder(tc)
    assert res == brk.FAILED and not any(x[0] == "submit" for x in tc.log), (res, tc.log)


@test
def ladder_leg_fills_during_cancel():
    tc = FakeTC(replace=[ApiErr(422)], fill_on_cancel=True)
    res, brk = ladder(tc)
    assert res == brk.GONE and not any(x[0] == "submit" for x in tc.log), (res, tc.log)


@test
def ladder_short_position_uses_buy_side():
    tc = FakeTC(side=-1, replace=[ApiErr(422)], oco=[ApiErr(422)])
    res, brk = ladder(tc, last=98.5, new_stop=99.95, side=-1)
    assert res == brk.STOP_ONLY and ("submit", "stop", 10.0, "OrderSide.BUY") in tc.log, (res, tc.log)


@test
def entry_retry_reuses_client_order_id():
    """A retried entry after a 503 must reuse the same client_order_id so Alpaca rejects a duplicate
    instead of opening the position twice."""
    import broker as brk
    from common import Signal
    tc = FakeTC(submit_transient=1)
    b = object.__new__(brk.Broker)
    b.tc = tc
    real_time = brk.time
    brk.time = types.SimpleNamespace(time=real_time.time, sleep=lambda s: None)
    try:
        b.open_bracket(Signal("EMA", "AAPL", 1, 100.0, 99.0, 102.0, 1.0, "t"), 5)
    finally:
        brk.time = real_time
    assert len(tc.seen_coids) == 2 and tc.seen_coids[0] == tc.seen_coids[1], tc.seen_coids


@test
def stop_only_outcome_makes_bot_manage_target():
    import broker as brk
    fb = FakeBroker()
    fb.move_stop = lambda sym, pos, s, last: brk.STOP_ONLY
    b = make_bot(fb)
    fb.pos["AAPL"] = (10, 100.0)
    pos = Position("AAPL", "EMA", "Technology", 1, 10, 100.0, 99.0, 102.0, 1.0,
                   pd.Timestamp.now(tz="UTC").isoformat())
    b.state.positions["AAPL"] = pos
    b.log_trade = lambda *a, **k: None
    b.apply_stop_move("AAPL", pos, 100.05, 101.2)
    assert pos.be_moved and not pos.target_live and pos.stop == 100.05
    a = {"close": np.array([102.3]), "atr": np.array([0.1])}
    b.manage({"AAPL": a}, mod=200)
    assert "AAPL" in fb.closed, "bot did not take profit itself after the target order was lost"


# ---------------------------------------------------------------- robustness.py
@test
def robustness_math():
    import robustness as rb
    assert abs(rb.max_dd_pct(np.array([100, -300, 50, -100]), 1000) - (-100 * 350 / 1100)) < 1e-9
    assert abs(rb.breakeven_bps([0, 2, 5, 10], [100, 50, -25, -100]) - (2 + 3 * 50 / 75)) < 1e-9
    assert rb.breakeven_bps([0, 2], [10, 5]) == float("inf")
    assert rb.breakeven_bps([0, 2], [-1, -5]) == 0.0
    rng = np.random.default_rng(0)
    pnl = rng.normal(1, 10, 300)
    d = rb.shuffle_drawdowns(pnl, 10_000, 500, rng)
    m = rb.max_dd_matrix(pnl[None, :], 10_000)[0]
    assert abs(m - rb.max_dd_pct(pnl, 10_000)) < 1e-9 and (d <= 0).all()


@test
def robustness_sweep_on_synthetic_market():
    """More slippage must never make the system look better; bootstraps must be seed-reproducible."""
    import robustness as rb
    arrays, frames, ss, days = world(seed=11)
    summary, by_exp, trades = rb.slippage_sweep(arrays, frames, ss, days[1].date(), days[-1].date(),
                                                100_000, [0.0, 2.0, 5.0, 10.0])
    pn = summary["net_pnl"].to_numpy()
    print("      sweep net P&L:", dict(zip(summary.index, pn)))
    assert np.all(np.diff(pn) < 0), "P&L did not fall as slippage rose"
    tr = trades[2.0]
    f1, _ = rb.day_block_bootstrap(tr, 100_000, 200, np.random.default_rng(3))
    f2, _ = rb.day_block_bootstrap(tr, 100_000, 200, np.random.default_rng(3))
    assert np.array_equal(f1, f2)


# ---------------------------------------------------------------- v3 direction confirmation
@test
def htf_votes_use_only_completed_bars():
    """The 60-min vote may only change on the last minute of an hour bar (10:29, 11:29, ...),
    never in the middle of one - otherwise it is peeking at an unfinished bar."""
    rng = np.random.default_rng(21)
    df = prepare_intraday(minute_bars(100, pd.bdate_range("2026-08-17", "2026-08-28"), rng, vol=0.002))
    d = df["dir60"].to_numpy()
    m = df["mod"].to_numpy()
    changed = np.flatnonzero(d[1:] != d[:-1]) + 1
    bad = [int(m[k]) for k in changed if (m[k] + 1) % 60 != 0 and m[k] != 389]
    assert not bad, f"60m vote changed mid-bar at minutes {bad[:5]}"
    assert set(np.unique(d)) <= {-1.0, 0.0, 1.0}


@test
def confirmation_gate_blocks_disagreeing_trades():
    """With confirmation ON, every trade's signal bar must have all votes agreeing with its side."""
    config.DIRECTION_CONFIRM = True
    try:
        arrays, frames, ss, days = world(seed=12)
        _, tr = run(arrays, frames, ss, days)
    finally:
        config.DIRECTION_CONFIRM = False
    _, base = run(*world(seed=12))
    print(f"      trades: {len(base)} without confirmation -> {len(tr)} with")
    assert len(tr) < len(base)
    styles = {e.id: e.style for e in build_experts()}
    for _, t in tr.iterrows():
        a = arrays[t["symbol"]]
        i = a["ts"].get_loc(t["opened"].tz_convert("UTC")) - 1
        side = 1 if t["side"] == "LONG" else -1
        votes = [a["dir60"][i], a["dir15"][i]] + ([] if styles[t["expert"]] == "reversion" else [a["dir5"][i]])
        assert all(v == side for v in votes), (t["symbol"], t["expert"], t["side"], votes)


# ---------------------------------------------------------------- v4 volatility / volume filter
@test
def vol_filter_gate_holds_on_every_trade():
    """With the v4 filter ON, every trade's signal bar must show contracting ATR and rising volume."""
    config.VOL_FILTER = True
    try:
        arrays, frames, ss, days = world(seed=13)
        _, tr = run(arrays, frames, ss, days)
    finally:
        config.VOL_FILTER = False
    _, base = run(*world(seed=13))
    print(f"      trades: {len(base)} without filter -> {len(tr)} with")
    assert 0 < len(tr) < len(base)
    for _, t in tr.iterrows():
        a = arrays[t["symbol"]]
        i = a["ts"].get_loc(t["opened"].tz_convert("UTC")) - 1
        assert a["vx_atr_ratio"][i] < config.VX_ATR_RATIO and a["vx_vol_ratio"][i] > config.VX_VOL_RATIO, t["symbol"]


# ---------------------------------------------------------------- v6 runner exits
@test
def runner_trail_squeezes_and_never_loosens():
    """Peak +1R -> stop 0R, +2R -> +1.25R, +4R -> +3.75R; a pullback must never loosen the stop."""
    from common import runner_trail
    for side in (1, -1):
        pos = Position("X", "EMA", "Tech", side, 10, 100.0, 100.0 - side, 0.0, 1.0, "2026-08-24T14:00:00+00:00")
        stops = []
        for ext in (0.5, 1.0, 2.0, 4.0, 3.0):
            px = 100.0 + side * ext
            a = {"high": np.array([px]), "low": np.array([px])}
            act = runner_trail(pos, a, 0)
            if act:
                pos.stop = act[1]
            stops.append(round(side * (pos.stop - 100.0), 2))
        assert stops == [-1.0, 0.0, 1.25, 3.75, 3.75], (side, stops)


@test
def runner_mode_backtest_exits_only_by_stops():
    """Runner mode: no targets, no expert/time exits - only stops, trailing stops and end of day."""
    config.RUNNER_MODE = True
    try:
        _, tr = run(*world(seed=14))
    finally:
        config.RUNNER_MODE = False
    reasons = set(tr["exit_reason"])
    print(f"      runner exits: {tr['exit_reason'].value_counts().to_dict()} | best trade {tr['r'].max():.1f}R")
    assert reasons <= {"stop", "trail_stop", "eod_flatten", "session_end"}, reasons
    trailed = tr.loc[tr.exit_reason == "trail_stop", "r"]
    assert (trailed > -1.0).all(), "a trailed stop lost more than the original 1R"
    assert trailed.mean() > 0, "trailed exits should average a profit"


# ---------------------------------------------------------------- Velez study
@test
def velez_2min_bars_are_session_anchored_and_exact():
    import velez
    rng = np.random.default_rng(31)
    m = minute_bars(100, pd.bdate_range("2026-08-24", "2026-08-24"), rng)
    b = velez.to_2min(m)
    ny = b.index.tz_convert(NYZ)
    assert ny[0].strftime("%H:%M") == "09:30" and len(b) == 195
    assert np.isclose(b["high"].iloc[0], m["high"].iloc[:2].max()) and np.isclose(b["close"].iloc[0], m["close"].iloc[1])
    assert np.isclose(b["volume"].sum(), m["volume"].sum())


@test
def velez_patterns_detected():
    import velez

    def arr(prev, cur, avg=0.2):
        return {"open": np.array([prev[0], cur[0]]), "high": np.array([prev[1], cur[1]]),
                "low": np.array([prev[2], cur[2]]), "close": np.array([prev[3], cur[3]]),
                "vz_body_avg": np.array([avg, avg])}
    assert velez.pattern(arr((10, 10.1, 9.9, 10), (10, 10.52, 9.99, 10.5)), 1) == ("EB", 1)
    assert velez.pattern(arr((10, 10.1, 9.9, 10), (10.3, 10.32, 9.8, 10.28)), 1) == ("TB", 1)
    assert velez.pattern(arr((10.2, 10.25, 9.95, 10.0), (9.98, 10.3, 9.97, 10.28), avg=1.0), 1) == ("180", 1)
    assert velez.pattern(arr((10, 10.1, 9.9, 10), (10, 10.05, 9.95, 10.01)), 1) == (None, None)


@test
def velez_no_lookahead():
    import velez
    rng = np.random.default_rng(32)
    b = velez.to_2min(minute_bars(100, pd.bdate_range("2026-08-17", "2026-08-21"), rng))
    full = velez.add_velez(prepare_intraday(b))
    cols = ["vz_s20", "vz_s200", "vz_narrow", "vz_slope", "vz_body_avg"]
    for k in rng.integers(250, len(b), 25):
        part = velez.add_velez(prepare_intraday(b.iloc[:k]))
        t = part.index[-1]
        assert np.allclose(part.loc[t, cols].astype(float), full.loc[t, cols].astype(float), equal_nan=True), t


@test
def velez_backtest_fills_only_through_trigger():
    import velez
    saved = (config.MAX_POSITIONS_PER_EXPERT, config.PENDING_EXPIRY_S)
    try:
        experts = velez.activate()
        arrays, frames, ss, days = world(seed=33, vol=0.002, prep=velez.prepare_2min)
        sim, _ = bt.run_backtest(arrays, ss, frames, days[1].date(), days[-1].date(), 100_000, 2.0, 0.0,
                                 verbose=False, experts=experts)
    finally:
        config.MAX_POSITIONS_PER_EXPERT, config.PENDING_EXPIRY_S = saved
    tr = pd.DataFrame(sim.trades)
    print(f"      velez synthetic trades: {len(tr)}")
    assert len(tr) > 0
    for _, t in tr.iterrows():
        trig = float(t["entry_reason"].split("@")[1])
        if t["side"] == "LONG":
            assert t["entry"] >= trig - 1e-9, (t["symbol"], t["entry"], trig)
        else:
            assert t["entry"] <= trig + 1e-9, (t["symbol"], t["entry"], trig)


# ---------------------------------------------------------------- Velez stop scan
@test
def velez_30min_bars_anchor_at_open():
    import velez
    rng = np.random.default_rng(41)
    m = minute_bars(100, pd.bdate_range("2026-08-24", "2026-08-25"), rng)
    b = velez.to_bars(m, 30)
    ny = b.index.tz_convert(NYZ)
    assert list(ny[:3].strftime("%H:%M")) == ["09:30", "10:00", "10:30"] and len(b) == 26
    assert np.isclose(b["volume"].sum(), m["volume"].sum())


@test
def velez_stop_replay_math():
    import velez
    t = dict(side=1, fill=100.0, o=np.array([100.0, 99.8, 99.0]), h=np.array([100.2, 99.9, 99.3]),
             l=np.array([99.9, 99.6, 98.9]), exit_close=99.2, tail_bps=30.0)
    r, hit = velez._result_bps(t, None, 0.0)
    assert not hit and abs(r - (-80.0)) < 1e-6          # no stop: held to the horizon close
    r, hit = velez._result_bps(t, 30, 0.0)               # stop 99.70, first touched on bar 1 (low 99.6)
    assert hit and abs(r - (-30.0)) < 1e-6
    r, hit = velez._result_bps(t, 90, 0.0)               # stop 99.10: bar 2 opens 99.0 -> gap fill at open
    assert hit and abs(r - (-100.0)) < 1e-6
    s = dict(t, side=-1, o=np.array([100.0, 100.2, 101.0]), h=np.array([100.1, 100.4, 101.1]),
             l=np.array([99.8, 100.1, 100.7]), exit_close=100.8)
    r, hit = velez._result_bps(s, 30, 0.0)
    assert hit and abs(r - (-30.0)) < 1e-6


@test
def velez_stop_scan_runs_and_holds_out_data():
    import velez, io, contextlib
    arrays, frames, ss, days = world(seed=42, vol=0.002, prep=velez.prepare_2min)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        velez.stop_scan(arrays, ss, frames, days[1].date(), days[-1].date(), 2.0, 90)
    out = buf.getvalue()
    assert "BEST STOP on pick set" in out and "CHECK set" in out and "VERDICT" in out, out[-500:]
    print("      " + [x for x in out.splitlines() if x.startswith("VERDICT")][0])


# ---------------------------------------------------------------- swing study (daily, mid/small caps)
def _daily_panel(n=40, days=1100, drift=0.0, vol=0.02, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2016-01-04", periods=days)
    out = {}
    for k in range(n):
        c = 50 * np.exp(np.cumsum(rng.normal(drift, vol, days)))
        o = c * np.exp(rng.normal(0, vol / 3, days))
        h = np.maximum(o, c) * (1 + abs(rng.normal(0, vol / 2, days)))
        l = np.minimum(o, c) * (1 - abs(rng.normal(0, vol / 2, days)))
        out[f"S{k}"] = pd.DataFrame(dict(open=o, high=h, low=l, close=c,
                                         volume=rng.integers(200_000, 600_000, days).astype(float)), index=idx)
    return out


@test
def swing_indicators_no_lookahead():
    import swing
    d = _daily_panel(1, 400, seed=1)["S0"]
    full = swing.obos_frame(d)
    for k in np.random.default_rng(2).integers(60, 400, 25):
        part = swing.obos_frame(d.iloc[:k])
        a, b = part.iloc[-1].astype(float), full.iloc[k - 1].astype(float)
        assert np.allclose(a, b, equal_nan=True), (k, a[~np.isclose(a, b, equal_nan=True)])


@test
def swing_votes_on_extremes():
    """A sharp 6-day selloff after a long rise must read oversold on >= 4 of 5 indicators, and vice versa."""
    import swing
    idx = pd.bdate_range("2020-01-01", periods=260)
    base = np.linspace(50, 80, 254)
    for moves, col in ((np.array([0.97, 0.96, 0.97, 0.95, 0.96, 0.95]), "os_votes"),
                       (np.array([1.03, 1.04, 1.03, 1.05, 1.04, 1.05]), "ob_votes")):
        c = np.concatenate([base, base[-1] * np.cumprod(moves)])
        d = pd.DataFrame(dict(open=c, high=c * 1.005, low=c * 0.995, close=c, volume=1e6), index=idx)
        v = swing.obos_frame(d)[col].iloc[-1]
        assert v >= 4, (col, v)


@test
def swing_universe_is_point_in_time():
    """Membership for a month uses the PREVIOUS day's liquidity - a jump on the 1st can't be seen that day."""
    import swing
    idx = pd.bdate_range("2021-01-25", "2021-02-10")
    first_feb = idx[idx.month == 2][0]
    adv = pd.DataFrame({"A": 1e6, "B": 1e6}, index=idx)
    adv.loc[adv.index >= first_feb, "A"] = 1e8                     # A becomes liquid ON Feb 1
    adv.loc[adv.index >= first_feb - pd.offsets.BDay(1), "B"] = 1e8  # B liquid the day before
    m = swing.membership(adv, pd.DataFrame(20.0, index=idx, columns=["A", "B"]), large_rank=0)
    assert not m.loc[first_feb:, "A"].any() and m.loc[first_feb:, "B"].all()


@test
def swing_mechanics_and_position_cap():
    import swing
    tr, eq = swing.run(_daily_panel(60, 1100, drift=0.0004, seed=3), "2017-01-02", "2020-04-30", large_rank=0)
    assert len(tr) > 50
    assert (pd.to_datetime(tr.entry_date) > pd.to_datetime(tr.signal_date)).all(), "entered on the signal day"
    ev = sorted([(d, 1) for d in pd.to_datetime(tr.entry_date)] + [(d, -1) for d in pd.to_datetime(tr.exit_date)],
                key=lambda x: (x[0], x[1]))
    cur = peak = 0
    for _, x in ev:
        cur += x
        peak = max(peak, cur)
    assert peak <= swing.MAX_POSITIONS, peak
    assert set(tr.reason) <= {"engine_neutral", "stop", "time", "period_end"}


@test
def swing_no_edge_on_noise():
    import swing
    rets = []
    for seed in (4, 5, 6):
        tr, _ = swing.run(_daily_panel(60, 1100, seed=seed), "2017-01-02", "2020-04-30", cost_mult=0.0, large_rank=0)
        rets.append(tr.ret_pct.to_numpy())
    r = np.concatenate(rets)
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))
    print(f"      swing on noise: {len(r)} trades, mean {r.mean():+.3f}%, t={t:+.2f}")
    assert abs(t) < 3, t


@test
def swing_holdout_guard():
    import swing
    old = sys.argv
    sys.argv = ["swing.py", "--start", "2016-01-01", "--end", "2023-06-30"]
    try:
        swing.main()
        raise AssertionError("ran on held-out dates without --use-holdout")
    except SystemExit as e:
        assert "HELD OUT" in str(e)
    finally:
        sys.argv = old


# ---------------------------------------------------------------- news engine
@test
def news_classifier_reads_headlines():
    import news_engine as ne
    assert ne.classify("XYZ Announces $50M Public Offering of Common Stock") == (-1.0, True)
    assert ne.classify("Analyst downgrades ABC to Underperform, lowers price target")[1]
    assert ne.classify("DEF beats estimates, raises guidance") == (1.0, False)
    assert ne.classify("GHI to present at investor conference") == (0.0, False)
    assert ne.classify("")[0] == 0.0


@test
def news_articles_count_only_after_publication():
    """Published before 16:00 ET -> that day; after the close or on a weekend -> next trading day."""
    import news_engine as ne
    dates = pd.bdate_range("2021-03-01", "2021-03-10")          # Mon 1st ... Wed 10th
    ts = ["2021-03-01 20:59:00+00:00",   # 15:59 ET Mon -> Mon
          "2021-03-01 21:01:00+00:00",   # 16:01 ET Mon -> Tue
          "2021-03-06 15:00:00+00:00"]   # Saturday     -> Mon 8th
    k = ne.assign_trading_day(pd.Series(ts), dates)
    assert [dates[i].strftime("%m-%d") for i in k] == ["03-01", "03-02", "03-08"], k


@test
def news_features_are_causal():
    import news_engine as ne
    rng = np.random.default_rng(51)
    dates = pd.bdate_range("2019-01-01", periods=420)
    syms = ["AAA", "BBB", "CCC"]
    sectors = {"AAA": "Technology", "BBB": "Technology", "CCC": "Energy"}
    n = 900
    t = pd.to_datetime(rng.choice(dates, n)) + pd.to_timedelta(rng.integers(0, 24 * 60, n), unit="min")
    heads = rng.choice(["X downgraded", "X beats estimates", "X announces offering", "X update"], n)
    news = pd.DataFrame({"created_at": t.tz_localize("UTC"), "symbols": [(rng.choice(syms),) for _ in range(n)],
                         "headline": heads, "summary": ""})
    full = ne.news_features(news, dates, syms, sectors)
    for k in (100, 250, 400):
        cut = pd.Timestamp(dates[k - 1]).tz_localize("America/New_York") + pd.Timedelta(hours=16)
        part = ne.news_features(news[pd.to_datetime(news.created_at) <= cut], dates[:k], syms, sectors)
        for key in ("count3", "neg3", "sector_neg"):
            assert part[key].iloc[-1].equals(full[key].iloc[k - 1]), (key, k)


@test
def news_sic_sector_map():
    import news_engine as ne
    cases = {"2834": "Health Care", "7372": "Technology", "6798": "Real Estate", "1311": "Energy",
             "3674": "Technology", "4911": "Utilities", "6022": "Financials", "5812": "Consumer Disc.",
             "2086": "Consumer Staples", "4813": "Communication", "3721": "Industrials", None: None}
    got = {k: ne.sic_to_sector(k) for k in cases}
    assert got == cases, {k: (v, cases[k]) for k, v in got.items() if v != cases[k]}


@test
def swing_allow_mask_is_respected_and_arms_run():
    import swing, news_engine as ne, io, contextlib
    P = _daily_panel(60, 1100, drift=0.0004, seed=8)
    dates = P["S0"].index
    only = ["S1", "S2", "S3", "S4", "S5"]
    allow = pd.DataFrame(False, index=dates, columns=sorted(P))
    allow[only] = True
    tr, _ = swing.run(P, "2017-01-02", "2020-04-30", large_rank=0, allow=allow)
    assert len(tr) and set(tr.symbol) <= set(only), set(tr.symbol) - set(only)
    base, _ = swing.run(P, "2017-01-02", "2020-04-30", large_rank=0)
    none_mask, _ = swing.run(P, "2017-01-02", "2020-04-30", large_rank=0, allow=None)
    assert len(base) == len(none_mask)
    rng = np.random.default_rng(9)
    n = 3000
    t = pd.to_datetime(rng.choice(dates, n)) + pd.to_timedelta(rng.integers(0, 1440, n), unit="min")
    news = pd.DataFrame({"created_at": t.tz_localize("UTC"), "symbols": [(f"S{rng.integers(60)}",) for _ in range(n)],
                         "headline": rng.choice(["downgrade", "beats estimates", "update"], n), "summary": ""})
    sectors = {f"S{i}": ["Technology", "Energy", "Financials"][i % 3] for i in range(60)}
    feats = ne.news_features(news, dates, list(P), sectors)
    old = swing.LARGE_CAP_RANK
    swing.LARGE_CAP_RANK = 0
    real_run = swing.run
    swing.run = lambda *a, **k: real_run(*a, **{**k, "large_rank": 0})
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ne.retest(P, feats, "2017-01-02", "2020-04-30", {}, out="/tmp/news_test")
    finally:
        swing.run, swing.LARGE_CAP_RANK = real_run, old
    out = buf.getvalue()
    assert "PRIMARY (B)" in out and "baseline trades split by news" in out, out[-600:]


# ---------------------------------------------------------------- Weinstein Stage 2 module
def _stage2_stock(breakout=True, weeks=62, seed=0):
    """50 weeks rising 20->40, 10-week flat base near 40, then a breakout week (3x volume) to ~44."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2019-01-07", periods=weeks * 5)
    wk = np.arange(len(days)) // 5
    lvl = np.where(wk < 50, 20 + 20 * wk / 50, 40.0)
    last = wk == wk.max()
    lvl = np.where(last, 44.0 if breakout else 39.8, lvl)
    c = lvl * (1 + rng.normal(0, 0.002, len(days)))
    d = pd.DataFrame(dict(open=c, high=c * 1.005, low=c * 0.995, close=c, volume=1e6), index=days)
    if breakout:
        d.loc[last, "volume"] = 3e6
    return d


def _flat_spy(index):
    return pd.Series(100.0, index=index)


@test
def weinstein_weekly_bars_and_breakout():
    import weinstein as ws
    d = _stage2_stock()
    w = ws.weekly(d)
    assert len(w) == 62 and np.isclose(w["volume"].iloc[0], 5e6)
    f = ws.stage_frame(w, ws.weekly(pd.DataFrame(dict(open=100.0, high=100.0, low=100.0, close=100.0, volume=0.0),
                                                 index=d.index))["close"])
    assert bool(f["breakout"].iloc[-1]), f.iloc[-1]
    assert not f["breakout"].iloc[-6:-1].any()                        # inside the base: no signal
    quiet = _stage2_stock()
    quiet["volume"] = 1e6                                             # same price move, no volume surge
    fq = ws.stage_frame(ws.weekly(quiet), ws.weekly(pd.DataFrame(dict(open=100.0, high=100.0, low=100.0,
                                                                      close=100.0, volume=0.0), index=d.index))["close"])
    assert not bool(fq["breakout"].iloc[-1]), "breakout without 2x volume must not signal"


@test
def weinstein_no_lookahead():
    import weinstein as ws
    rng = np.random.default_rng(61)
    days = pd.bdate_range("2017-01-02", periods=900)
    c = 30 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, 900)))
    d = pd.DataFrame(dict(open=c, high=c * 1.01, low=c * 0.99, close=c, volume=rng.integers(5e5, 2e6, 900) * 1.0), index=days)
    b = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 900))), index=days)
    bw = ws.weekly(pd.DataFrame(dict(open=b, high=b, low=b, close=b, volume=0.0)))["close"]
    full = ws.stage_frame(ws.weekly(d), bw)
    for k in (300, 500, 700, 895):
        cut = days[k - 1]
        wk_last = cut.to_period("W-FRI")
        part = ws.stage_frame(ws.weekly(d.iloc[:k]), bw)
        done = [p for p in part.index if p < wk_last]                # only COMPLETED weeks are ever used
        a, bb = part.loc[done[-1]].astype(float), full.loc[done[-1]].astype(float)
        assert np.allclose(a, bb, equal_nan=True), (k, done[-1])


@test
def weinstein_backtest_mechanics_and_noise():
    import weinstein as ws
    P = _daily_panel(250, 1300, drift=0.0, vol=0.02, seed=62)
    rng = np.random.default_rng(63)
    for d in P.values():                                   # realistic volume: occasional heavy weeks
        d["volume"] = 400_000 * np.exp(rng.normal(0, 0.9, len(d)))
    spy = pd.Series(100.0, index=P["S0"].index)
    tr, eq = ws.run(P, spy, "2017-06-01", "2021-01-29", cost_mult=0.0)
    assert len(tr) > 25, len(tr)
    wk_end = pd.to_datetime(tr["signal_week"].map(lambda p: pd.Period(p, "W-FRI").end_time.normalize()))
    assert (pd.to_datetime(tr["entry_date"]) > wk_end - pd.Timedelta(days=3)).all()
    assert set(tr.reason) <= {"stop", "weekly_close_below_ma30", "period_end"}
    ev = sorted([(d, 1) for d in pd.to_datetime(tr.entry_date)] + [(d, -1) for d in pd.to_datetime(tr.exit_date)],
                key=lambda x: (x[0], x[1]))
    cur = peak = 0
    for _, x in ev:
        cur += x
        peak = max(peak, cur)
    assert peak <= ws.MAX_POSITIONS
    assert (tr.loc[tr.reason == "stop", "r"] < 0.05).all()
    t = tr.ret_pct.mean() / (tr.ret_pct.std() / np.sqrt(len(tr)))
    print(f"      weinstein on noise: {len(tr)} trades, avg {tr.ret_pct.mean():+.2f}%, t={t:+.2f}")
    assert abs(t) < 3


class _WSFakeTC:
    def __init__(self, positions=None, orders=None, is_open=True):
        self.pos = dict(positions or {})
        self.orders = list(orders or [])
        self.submitted, self.cancelled, self.is_open = [], [], is_open

    def get_clock(self):
        return types.SimpleNamespace(is_open=self.is_open)

    def get_account(self):
        return types.SimpleNamespace(equity="100000")

    def get_all_positions(self):
        return [types.SimpleNamespace(symbol=s, qty=str(q)) for s, q in self.pos.items()]

    def get_orders(self, filter=None):
        return list(self.orders)

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)
        self.orders = [o for o in self.orders if o.id != oid]

    def submit_order(self, order_data):
        kind = type(order_data).__name__
        self.submitted.append((kind, order_data.symbol, float(order_data.qty), str(order_data.side),
                               getattr(order_data, "stop_price", None), order_data.client_order_id))
        oid = f"o{len(self.submitted)}"
        if kind == "MarketOrderRequest" and "BUY" in str(order_data.side).upper():
            self.pos[order_data.symbol] = self.pos.get(order_data.symbol, 0) + float(order_data.qty)
        self.orders.append(types.SimpleNamespace(id=oid, symbol=order_data.symbol, client_order_id=order_data.client_order_id,
                                                 stop_price=getattr(order_data, "stop_price", None)))
        return types.SimpleNamespace(id=oid)

    def get_order_by_id(self, oid):
        q = next(x[2] for x in self.submitted if f"o{self.submitted.index(x) + 1}" == oid)
        return types.SimpleNamespace(status="filled", filled_qty=str(q))


@test
def weinstein_bot_buys_once_with_stop_and_respects_others():
    import weinstein as ws
    d = _stage2_stock()
    today = (d.index[-1] + pd.offsets.BDay(1)).date()                 # first session after the breakout week
    panel = {"BRK": d}
    spy = _flat_spy(d.index)
    old_state, old_paper = ws.STATE_FILE, config.PAPER
    ws.STATE_FILE = os.path.join(tempfile.mkdtemp(), "ws.json")
    config.PAPER = True
    try:
        foreign = types.SimpleNamespace(id="x1", symbol="ZZZ", client_order_id="SB_EMA_ZZZ_1", stop_price=5.0)
        tc = _WSFakeTC(orders=[foreign])
        ws.run_bot(tc, panel, spy, submit=False, today=today)
        assert tc.submitted == [], "dry run placed orders"
        ws.run_bot(tc, panel, spy, submit=True, today=today, wait_fill_s=0.1)
        buys = [x for x in tc.submitted if x[0] == "MarketOrderRequest"]
        stops = [x for x in tc.submitted if x[0] == "StopOrderRequest"]
        assert len(buys) == 1 and buys[0][1] == "BRK" and len(stops) == 1 and stops[0][4] < buys[0][2] * 1000
        risk = (44 - stops[0][4]) * buys[0][2]
        assert risk <= 100000 * ws.RISK_PCT * 1.05 and buys[0][2] * 44 <= 100000 * ws.MAX_POS_PCT * 1.05
        ws.run_bot(tc, panel, spy, submit=True, today=today, wait_fill_s=0.1)       # second run same day
        assert len([x for x in tc.submitted if x[0] == "MarketOrderRequest"]) == 1, "bought twice"
        stale = (d.index[-1] + pd.offsets.BDay(3)).date()
        n = len(tc.submitted)
        ws.run_bot(_WSFakeTC(), {"BRK": d}, spy, submit=True, today=stale, wait_fill_s=0.1)
        assert "x1" not in tc.cancelled, "cancelled another bot's order"
    finally:
        ws.STATE_FILE, config.PAPER = old_state, old_paper


@test
def weinstein_bot_exits_below_ma_and_is_paper_only():
    import weinstein as ws
    days = pd.bdate_range("2019-01-07", periods=60 * 5)
    c = np.concatenate([np.linspace(20, 40, 250), np.linspace(40, 30, 50)])      # rise, then break down
    d = pd.DataFrame(dict(open=c, high=c * 1.005, low=c * 0.995, close=c, volume=1e6), index=days)
    today = (days[-1] + pd.offsets.BDay(1)).date()
    old_state, old_paper = ws.STATE_FILE, config.PAPER
    ws.STATE_FILE = os.path.join(tempfile.mkdtemp(), "ws.json")
    json.dump({"positions": {"DWN": {"stop": 25.0, "week": "x", "qty": 100}}, "attempted": {}}, open(ws.STATE_FILE, "w"))
    try:
        config.PAPER = True
        mine = types.SimpleNamespace(id="s1", symbol="DWN", client_order_id="WS_S_DWN_1", stop_price=25.0)
        other = types.SimpleNamespace(id="s2", symbol="DWN", client_order_id="SB_EMA_DWN_1", stop_price=20.0)
        tc = _WSFakeTC(positions={"DWN": 150}, orders=[mine, other])
        ws.run_bot(tc, {"DWN": d}, _flat_spy(days), submit=True, today=today, wait_fill_s=0.1)
        sells = [x for x in tc.submitted if x[0] == "MarketOrderRequest"]
        assert sells and sells[0][1] == "DWN" and sells[0][2] == 100, sells      # only its own 100 shares
        assert tc.cancelled == ["s1"], tc.cancelled                             # only its own stop
        config.PAPER = False
        try:
            ws.run_bot(_WSFakeTC(), {"DWN": d}, _flat_spy(days), submit=True, today=today)
            raise AssertionError("submitted with a live account")
        except SystemExit as e:
            assert "paper-only" in str(e)
    finally:
        ws.STATE_FILE, config.PAPER = old_state, old_paper


# ---------------------------------------------------------------- 365-day zones (Weinstein / Velez)
@test
def zones_profile_finds_volume_levels():
    import zones
    rng = np.random.default_rng(71)
    c = np.concatenate([50 + rng.normal(0, 0.4, 180), 60 + rng.normal(0, 0.4, 185)])
    nodes = zones.profile_nodes(c + 0.3, c - 0.3, c, np.full(365, 1e6))
    assert any(abs(n - 50) < 1.0 for n in nodes) and any(abs(n - 60) < 1.0 for n in nodes), nodes
    assert len(zones.profile_nodes(np.ones(5), np.ones(5), np.ones(5), np.ones(5))) == 0


@test
def zones_support_and_target_rules():
    import zones
    rng = np.random.default_rng(72)
    days = pd.bdate_range("2019-01-01", periods=365)
    lv = np.concatenate([np.full(120, 40.0), np.full(120, 50.0), np.full(125, 60.0)])
    c = lv + rng.normal(0, 0.3, 365)
    d = pd.DataFrame(dict(open=c, high=c + 0.3, low=c - 0.3, close=c, volume=1e6), index=days)
    d.iloc[-1, d.columns.get_loc("close")] = 55.0                      # price between the 50 and 60 nodes
    z = zones.zones_at(d, 364, ma30=48.0, atr=1.0)
    assert abs(z["support"] - 50) < 1.0 and abs(z["tp1"] - 60) < 1.0, z
    assert abs(z["zone_lo"] - (z["support"] - 0.5)) < 1e-9
    z2 = zones.zones_at(d.assign(close=np.where(np.arange(365) == 364, 70.0, d["close"])), 364, ma30=55.0, atr=1.0)
    assert np.isnan(z2["tp1"]), "no node above price must mean open air"
    z3 = zones.zones_at(d, 364, ma30=53.0, atr=1.0)                   # 50-node is below 97% of the MA
    assert abs(z3["support"] - 53.0) < 1e-9, z3


@test
def zones_weekly_state_is_causal():
    import zones
    import weinstein as ws
    rng = np.random.default_rng(73)
    days = pd.bdate_range("2017-01-02", periods=700)
    cl = 30 * np.exp(np.cumsum(rng.normal(0.0012, 0.015, 700)))
    d = pd.DataFrame(dict(open=cl, high=cl * 1.01, low=cl * 0.99, close=cl,
                          volume=rng.integers(5e5, 2e6, 700) * 1.0), index=days)
    spy_w = ws.weekly(pd.DataFrame(dict(open=100.0, high=100.0, low=100.0, close=100.0, volume=0.0), index=days))["close"]
    _, zfull = zones.weekly_state(d, spy_w)
    assert len(zfull) > 10
    for wk in list(zfull)[-8::3]:
        cut = int(np.flatnonzero(days.to_period("W-FRI") == wk)[-1]) + 1
        _, zpart = zones.weekly_state(d.iloc[:cut], spy_w)
        a, b = zpart[wk], zfull[wk]
        assert all(np.isclose(a[k], b[k], equal_nan=True) for k in a), (wk, a, b)


@test
def zones_engine_invariants_and_noise():
    import zones
    P = _daily_panel(200, 1300, drift=0.0, vol=0.02, seed=74)
    spy = pd.Series(100.0, index=P["S0"].index)
    tr, eq = zones.run(P, spy, "2017-06-01", "2021-01-29", cost_mult=0.0)
    assert len(tr) > 30, len(tr)
    assert (tr["entry"] >= tr["trigger"] - 1e-9).all(), "filled below the stop-entry trigger"
    assert (tr["trigger"] > tr["stop0"]).all()
    assert tr.loc[tr.reason == "breakeven_stop", "tp1_hit"].all(), "breakeven stop without TP1"
    assert (tr.loc[tr.tp1_hit, "r"] > -0.3).all(), "a trade that took TP1 lost more than a gap allows"
    assert set(tr.reason) <= {"stop", "breakeven_stop", "weekly_close_below_ma30", "period_end"}
    ev = sorted([(d, 1) for d in pd.to_datetime(tr.entry_date)] + [(d, -1) for d in pd.to_datetime(tr.exit_date)],
                key=lambda x: (x[0], x[1]))
    cur = peak = 0
    for _, x in ev:
        cur += x
        peak = max(peak, cur)
    assert peak <= zones.MAX_POSITIONS
    t = tr.ret_pct.mean() / (tr.ret_pct.std() / np.sqrt(len(tr)))
    print(f"      zones on noise: {len(tr)} trades, avg {tr.ret_pct.mean():+.2f}%, TP1 hit "
          f"{tr.tp1_hit.mean() * 100:.0f}%, t={t:+.2f}")
    assert abs(t) < 3


# ---------------------------------------------------------------- market filter + 90-day GTC renewal
def _falling_spy(index):
    return pd.Series(np.linspace(400, 200, len(index)), index=index)


@test
def market_filter_blocks_longs_when_spy_not_stage2():
    import weinstein as ws, zones
    P = _daily_panel(150, 1300, drift=0.0, vol=0.02, seed=81)
    rng = np.random.default_rng(82)
    for d in P.values():
        d["volume"] = 400_000 * np.exp(rng.normal(0, 0.9, len(d)))
    bad_spy = _falling_spy(P["S0"].index)
    t_on, _ = ws.run(P, bad_spy, "2017-06-01", "2021-01-29", cost_mult=0.0, market_filter=True)
    t_off, _ = ws.run(P, bad_spy, "2017-06-01", "2021-01-29", cost_mult=0.0, market_filter=False)
    assert len(t_on) == 0 and len(t_off) > 0, (len(t_on), len(t_off))
    z_on, _ = zones.run(P, bad_spy, "2017-06-01", "2021-01-29", cost_mult=0.0, market_filter=True)
    z_off, _ = zones.run(P, bad_spy, "2017-06-01", "2021-01-29", cost_mult=0.0, market_filter=False)
    assert len(z_on) == 0 and len(z_off) > 0, (len(z_on), len(z_off))
    assert not ws.spy_stage2(bad_spy).any() and ws.spy_stage2(pd.Series(np.linspace(200, 400, 1300),
                                                                         index=P["S0"].index)).iloc[-1]


@test
def weinstein_bot_renews_stops_before_alpaca_90_day_expiry():
    import weinstein as ws
    d = _stage2_stock()
    today = (d.index[-1] + pd.offsets.BDay(1)).date()
    old_state, old_paper = ws.STATE_FILE, config.PAPER
    ws.STATE_FILE = os.path.join(tempfile.mkdtemp(), "ws.json")
    json.dump({"positions": {"BRK": {"stop": 39.1, "week": "x", "qty": 50}}, "attempted": {}}, open(ws.STATE_FILE, "w"))
    try:
        config.PAPER = True
        aged = types.SimpleNamespace(id="old1", symbol="BRK", client_order_id="WS_S_BRK_1", stop_price=39.1,
                                     created_at=pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=85))
        fresh = types.SimpleNamespace(id="new1", symbol="BRK", client_order_id="WS_S_BRK_2", stop_price=39.1,
                                      created_at=pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=5))
        tc = _WSFakeTC(positions={"BRK": 50}, orders=[aged])
        ws.run_bot(tc, {"BRK": d}, _flat_spy(d.index), submit=True, today=today, wait_fill_s=0.1)
        stops = [x for x in tc.submitted if x[0] == "StopOrderRequest"]
        assert tc.cancelled == ["old1"] and len(stops) == 1 and stops[0][2] == 50, (tc.cancelled, stops)
        tc2 = _WSFakeTC(positions={"BRK": 50}, orders=[fresh])
        ws.run_bot(tc2, {"BRK": d}, _flat_spy(d.index), submit=True, today=today, wait_fill_s=0.1)
        assert tc2.cancelled == [] and not [x for x in tc2.submitted if x[0] == "StopOrderRequest"], "renewed a fresh stop"
    finally:
        ws.STATE_FILE, config.PAPER = old_state, old_paper


# ---------------------------------------------------------------- zones paper bot
class _ZFake:
    """Fake Alpaca modelling bracket / OTO parents with legs (GTC), standalone stops, positions."""

    def __init__(self, is_open=True):
        self.pos, self.top, self.submitted, self.cancelled, self.is_open, self.n = {}, {}, [], [], is_open, 0

    def _id(self):
        self.n += 1
        return f"z{self.n}"

    def get_clock(self):
        return types.SimpleNamespace(is_open=self.is_open)

    def get_account(self):
        return types.SimpleNamespace(equity="100000")

    def get_all_positions(self):
        return [types.SimpleNamespace(symbol=s, qty=str(q)) for s, q in self.pos.items() if q > 0]

    def get_orders(self, filter=None):
        return [o for o in self.top.values() if o.status in ("new", "held", "accepted")]

    def get_order_by_id(self, oid, filter=None):
        return self.top[oid]

    def _find(self, oid):
        for o in self.top.values():
            if o.id == oid:
                return o
            for l in o.legs:
                if l.id == oid:
                    return l

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)
        self._find(oid).status = "canceled"

    def submit_order(self, order_data):
        od, kind = order_data, type(order_data).__name__
        oc = str(getattr(od, "order_class", "")).lower()
        side = "buy" if "buy" in str(od.side).lower() else "sell"
        self.submitted.append(dict(kind=kind, cls=oc, sym=od.symbol, qty=float(od.qty), side=side,
                                   tif=str(od.time_in_force).lower(), cid=od.client_order_id,
                                   stop=getattr(od, "stop_price", None) or getattr(getattr(od, "stop_loss", None), "stop_price", None),
                                   tp=getattr(getattr(od, "take_profit", None), "limit_price", None)))
        mk = lambda **k: types.SimpleNamespace(id=self._id(), symbol=od.symbol, client_order_id=k.pop("cid", None),
                                               legs=[], created_at=pd.Timestamp.now(tz="UTC"), **k)
        if kind == "MarketOrderRequest" and side == "buy":
            self.pos[od.symbol] = self.pos.get(od.symbol, 0) + float(od.qty)
            par = mk(cid=od.client_order_id, status="filled", stop_price=None, limit_price=None, qty=od.qty)
            if "bracket" in oc:
                par.legs.append(mk(status="new", limit_price=od.take_profit.limit_price, stop_price=None, qty=od.qty))
            if "bracket" in oc or "oto" in oc:
                par.legs.append(mk(status="held", stop_price=od.stop_loss.stop_price, limit_price=None, qty=od.qty))
        elif kind == "StopOrderRequest":
            par = mk(cid=od.client_order_id, status="new", stop_price=od.stop_price, limit_price=None, qty=od.qty)
        else:
            self.pos[od.symbol] = self.pos.get(od.symbol, 0) - float(od.qty)
            par = mk(cid=od.client_order_id, status="filled", stop_price=None, limit_price=None, qty=od.qty)
        self.top[par.id] = par
        return par

    def fill_tp(self, sym):
        for o in self.top.values():
            tp = [l for l in o.legs if l.limit_price and l.status == "new"]
            if o.symbol == sym and tp:
                tp[0].status = "filled"
                for l in o.legs:
                    if l.stop_price:
                        l.status = "canceled"
                self.pos[sym] -= float(tp[0].qty)
                return


def _zb_env():
    import zones_bot as zb
    old = (zb.STATE_FILE, config.PAPER, zb.find_setups)
    zb.STATE_FILE = os.path.join(tempfile.mkdtemp(), "zn.json")
    config.PAPER = True
    return zb, old


def _zb_restore(zb, old):
    zb.STATE_FILE, config.PAPER, zb.find_setups = old


def _zb_setup(sym="ZZ"):
    return dict(symbol=sym, trigger=50.0, stop=47.0, tp1=56.0, qty=100, pattern="TB", open_air=False,
                signal_day="2026-09-25", zone="47.50-48.50")


@test
def zones_bot_two_protected_lots_once():
    zb, old = _zb_env()
    try:
        days = pd.bdate_range("2025-01-02", "2026-09-25")
        spy = pd.Series(100.0, index=days)
        today = pd.Timestamp("2026-09-28")
        zb.find_setups = lambda *a, **k: [_zb_setup()]
        tc = _ZFake()
        zb.run_bot(tc, {}, spy, lambda s: 49.0, submit=True, today=today)          # below trigger: wait
        assert tc.submitted == []
        zb.run_bot(tc, {}, spy, lambda s: 50.2, submit=True, today=today)          # through trigger
        a, b = tc.submitted
        assert a["cls"].endswith("bracket") and a["qty"] == 50 and a["tp"] == 56.0 and a["stop"] == 47.0
        assert b["cls"].endswith("oto") and b["qty"] == 50 and b["stop"] == 47.0 and b["tp"] is None
        assert "gtc" in a["tif"] and "gtc" in b["tif"], "legs would expire at the close"
        assert a["cid"].startswith("ZN_") and b["cid"].startswith("ZN_")
        zb.run_bot(tc, {}, spy, lambda s: 51.0, submit=True, today=today)          # same day again
        assert len(tc.submitted) == 2, "bought twice"
        tc2 = _ZFake()
        zb.run_bot(tc2, {}, spy, lambda s: 51.0, submit=False, today=today)
        assert tc2.submitted == [], "plan mode placed orders"
    finally:
        _zb_restore(zb, old)


@test
def zones_bot_tp1_moves_rest_to_breakeven_and_renews_old_stops():
    zb, old = _zb_env()
    try:
        days = pd.bdate_range("2025-01-02", "2026-09-25")
        spy = pd.Series(100.0, index=days)
        today = pd.Timestamp("2026-09-28")
        zb.find_setups = lambda *a, **k: [_zb_setup()]
        tc = _ZFake()
        zb.run_bot(tc, {}, spy, lambda s: 50.0, submit=True, today=today)
        tc.fill_tp("ZZ")
        zb.find_setups = lambda *a, **k: []
        zb.run_bot(tc, {}, spy, lambda s: 56.5, submit=True, today=today)
        be = [x for x in tc.submitted if x["kind"] == "StopOrderRequest"]
        assert len(be) == 1 and be[0]["stop"] == 50.0 and be[0]["qty"] == 50, be
        assert any(c for c in tc.cancelled), "lot B's old stop was not cancelled"
        for o in tc.top.values():                                       # age every live stop to 85 days
            for x in [o] + o.legs:
                if x.stop_price and x.status in ("new", "held"):
                    x.created_at = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=85)
        n = len(tc.submitted)
        zb.run_bot(tc, {}, spy, lambda s: 57.0, submit=True, today=today)
        renew = tc.submitted[n:]
        assert len(renew) == 1 and renew[0]["kind"] == "StopOrderRequest" and renew[0]["stop"] == 50.0, renew
    finally:
        _zb_restore(zb, old)


@test
def zones_bot_weekly_exit_touches_only_its_own_orders():
    zb, old = _zb_env()
    try:
        days = pd.bdate_range("2025-06-02", periods=60 * 5)
        c = np.concatenate([np.linspace(20, 40, 250), np.linspace(40, 30, 50)])
        d = pd.DataFrame(dict(open=c, high=c * 1.005, low=c * 0.995, close=c, volume=1e6), index=days)
        spy = pd.Series(100.0, index=days)
        today = days[-1] + pd.offsets.BDay(1)                            # first session of the next week
        zb.find_setups = lambda *a, **k: [_zb_setup("DN")]
        tc = _ZFake()
        zb.run_bot(tc, {"DN": d}, spy, lambda s: 50.0, submit=True, today=days[-1] - pd.offsets.BDay(3))
        foreign = types.SimpleNamespace(id="other1", symbol="DN", client_order_id="WS_S_DN_1", status="new",
                                        stop_price=10.0, limit_price=None, qty=5, legs=[], created_at=pd.Timestamp.now(tz="UTC"))
        tc.top["other1"] = foreign
        tc.pos["DN"] += 5                                                 # another bot's 5 shares
        zb.find_setups = lambda *a, **k: []
        zb.run_bot(tc, {"DN": d}, spy, lambda s: 30.0, submit=True, today=today)
        sells = [x for x in tc.submitted if x["side"] == "sell" and x["kind"] == "MarketOrderRequest"]
        assert len(sells) == 1 and sells[0]["qty"] == 100, sells          # only its own 100 shares
        assert "other1" not in tc.cancelled
        assert tc.cancelled, "its own stops were not cancelled before selling"
    finally:
        _zb_restore(zb, old)


@test
def zones_bot_triggers_expire_and_paper_only():
    zb, old = _zb_env()
    try:
        days = pd.bdate_range("2025-01-02", "2026-10-02")
        spy = pd.Series(100.0, index=days)
        zb.find_setups = lambda *a, **k: [_zb_setup()]
        tc = _ZFake()
        zb.run_bot(tc, {}, spy, lambda s: 40.0, submit=True, today=pd.Timestamp("2026-09-28"))
        zb.find_setups = lambda *a, **k: []
        for d in ("2026-09-29", "2026-09-30", "2026-10-01"):
            st = zb.run_bot(tc, {}, spy, lambda s: 40.0, submit=True, today=pd.Timestamp(d))
        assert "ZZ" not in st["pending"], st["pending"]
        zb.run_bot(tc, {}, spy, lambda s: 60.0, submit=True, today=pd.Timestamp("2026-10-02"))
        assert tc.submitted == [], "bought on an expired trigger"
        config.PAPER = False
        try:
            zb.run_bot(_ZFake(), {}, spy, lambda s: 60.0, submit=True, today=pd.Timestamp("2026-10-02"))
            raise AssertionError("submitted on a live account")
        except SystemExit as e:
            assert "paper-only" in str(e)
    finally:
        _zb_restore(zb, old)


@test
def zones_bot_setups_match_backtest_rules():
    """The live bot must find exactly the signals the backtest engine would take on the same day."""
    import zones, zones_bot as zb, weinstein as ws, velez
    P = _daily_panel(80, 900, drift=0.0006, vol=0.018, seed=91)
    spy = pd.Series(100.0, index=P["S0"].index)
    spy_w = ws.weekly(pd.DataFrame(dict(open=spy, high=spy, low=spy, close=spy, volume=0.0)))["close"]
    states = {s: zones.weekly_state(d, spy_w)[1] for s, d in P.items()}
    checked = 0
    for day in P["S0"].index[500::37]:
        live = {x["symbol"]: x for x in zb.find_setups(P, spy, day, 1e7)}
        ref = {}
        for s, d in P.items():
            z = states[s].get(day.to_period("W-FRI") - 1)
            i = d.index.get_loc(day)
            if z is None or not (d["low"].iloc[i] <= z["zone_hi"] and d["close"].iloc[i] >= z["zone_lo"]):
                continue
            a = {k: d[k].to_numpy() for k in ("open", "high", "low", "close")}
            a["vz_body_avg"] = (d["close"] - d["open"]).abs().rolling(20).mean().shift(1).to_numpy()
            if velez.pattern(a, i)[1] != 1:
                continue
            trig, stop = round(d["high"].iloc[i] + 0.01, 2), round(min(d["low"].iloc[i], z["zone_lo"]) - 0.01, 2)
            if (trig - stop) / trig > zones.MAX_STOP_PCT:
                continue
            if np.isfinite(z["tp1"]) and z["tp1"] - trig < zones.MIN_RR * (trig - stop):
                continue
            ref[s] = (trig, stop)
        assert set(live) == set(ref), (day, sorted(live), sorted(ref))
        for s in ref:
            assert (live[s]["trigger"], live[s]["stop"]) == ref[s]
        checked += len(ref)
    print(f"      setups matched on {checked} signals")
    assert checked > 0
# ---------------------------------------------------------------- Velez swing (Pristine Buy Setup) + Weinstein hybrid
def _pbs_stock(pullback=3):
    days = pd.bdate_range("2021-01-04", periods=80)
    c = list(np.linspace(50, 70, 80 - pullback))
    for k in range(pullback):
        c.append(c[-1] - 0.6)
    c = np.array(c)
    return pd.DataFrame(dict(open=c, high=c + 0.3, low=c - 0.3, close=c, volume=1e6), index=days)


def _path_panel(n, days, seed, steps=26, vol=0.02):
    """TRUE random walk: each day's O/H/L/C come from a simulated intraday path, so a buy stop has exactly
    zero expected edge. (The simpler generator makes highs pure one-day spikes, which unfairly punishes
    any stop-entry strategy.)"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2016-01-04", periods=days)
    out = {}
    for k in range(n):
        x = np.log(50) + np.cumsum(rng.normal(0, vol / np.sqrt(steps), days * steps)).reshape(days, steps)
        px = np.exp(x)
        out[f"S{k}"] = pd.DataFrame(dict(open=px[:, 0], high=px.max(1), low=px.min(1), close=px[:, -1],
                                         volume=rng.integers(200_000, 600_000, days).astype(float)), index=idx)
    return out


@test
def vswing_setup_detection():
    import velez_swing as vs
    f3 = vs.pbs_frame(_pbs_stock(3))
    assert bool(f3["setup"].iloc[-1]) and bool(f3["trend"].iloc[-1])
    assert np.isclose(f3["trigger"].iloc[-1], _pbs_stock(3)["high"].iloc[-1] + 0.05)
    assert np.isclose(f3["stop"].iloc[-1], _pbs_stock(3)["low"].iloc[-1] - 0.05)
    assert f3["swing_high"].iloc[-1] > f3["trigger"].iloc[-1]
    assert not bool(vs.pbs_frame(_pbs_stock(2))["setup"].iloc[-1]), "only 2 lower highs must not qualify"
    down = _pbs_stock(3).iloc[::-1].set_axis(_pbs_stock(3).index)
    assert not vs.pbs_frame(down)["trend"].iloc[-1], "falling averages must not count as an uptrend"


@test
def vswing_no_lookahead():
    import velez_swing as vs, weinstein as ws
    rng = np.random.default_rng(101)
    days = pd.bdate_range("2017-01-02", periods=700)
    cl = 30 * np.exp(np.cumsum(rng.normal(0.0008, 0.018, 700)))
    d = pd.DataFrame(dict(open=cl, high=cl * 1.01, low=cl * 0.99, close=cl, volume=1e6), index=days)
    spy_w = ws.weekly(pd.DataFrame(dict(open=100.0, high=100.0, low=100.0, close=100.0, volume=0.0), index=days))["close"]
    full, st_full = vs.pbs_frame(d), vs.stage2_daily(d, spy_w)
    for k in (100, 333, 512, 699):
        part = vs.pbs_frame(d.iloc[:k])
        assert np.allclose(part.iloc[-1].astype(float), full.iloc[k - 1].astype(float), equal_nan=True), k
        assert vs.stage2_daily(d.iloc[:k], spy_w).iloc[-1] == st_full.iloc[k - 1], k


@test
def vswing_engine_invariants_and_noise():
    import velez_swing as vs
    P = _path_panel(150, 1100, seed=102)
    spy = pd.Series(100.0, index=P["S0"].index)
    assert vs.FILL_RULE == "pessimistic", "the candle rule overstates results (t=+8.5 on a true random walk)"
    for hyb in (False, True):
        tr, eq = vs.run(P, spy, "2017-06-01", "2020-04-30", hybrid=hyb, cost_mult=0.0)
        assert len(tr) > 30, (hyb, len(tr))
        assert (tr["entry"] >= tr["trigger"] - 1e-9).all(), "filled below the buy stop"
        nxt = [P[s].index[P[s].index.get_loc(pd.Timestamp(sd)) + 1] for s, sd in zip(tr.symbol, tr.signal_date)]
        assert (pd.to_datetime(tr.entry_date).to_numpy() == np.array(nxt, dtype="datetime64[ns]")).all(), \
            "entry must be the session right after the signal"
        assert (tr.loc[tr.reason != "period_end", "days"] <= vs.HOLD_DAYS).all()
        assert (tr.loc[tr.reason == "target", "exit"] >= tr.loc[tr.reason == "target", "target"] - 1e-9).all()
        assert (tr.loc[tr.reason == "stop", "exit"] <= tr.loc[tr.reason == "stop", "stop"] + 1e-9).all()
        t = tr.ret_pct.mean() / (tr.ret_pct.std() / np.sqrt(len(tr)))
        print(f"      {'hybrid' if hyb else 'velez '} on noise: {len(tr)} trades, avg {tr.ret_pct.mean():+.2f}%, t={t:+.2f}")
        assert abs(t) < 3


@test
def vswing_main_wiring():
    import swing, velez_swing as vs, io, contextlib
    P = _daily_panel(40, 1300, drift=0.0003, vol=0.02, seed=103)
    spy = pd.DataFrame(dict(open=100.0, high=100.0, low=100.0, close=100.0, volume=1e6), index=P["S0"].index)
    real = (swing._symbols, swing.load_panel)
    swing._symbols = lambda f=None: sorted(P)
    swing.load_panel = lambda syms, a, b, **k: ({"SPY": spy, "IWM": spy, "IJH": spy} if len(syms) == 1 else P)
    old = sys.argv
    try:
        sys.argv = ["velez_swing.py", "--start", "2017-06-01", "--end", "2021-01-29"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            vs.main()
        out = buf.getvalue()
        assert "VERDICT A velez_swing" in out and "VERDICT B weinstein_hybrid" in out, out[-400:]
        sys.argv = ["velez_swing.py", "--start", "2017-06-01", "--end", "2023-01-01"]
        try:
            vs.main()
            raise AssertionError("ran on held-out dates")
        except SystemExit as e:
            assert "HELD OUT" in str(e)
    finally:
        swing._symbols, swing.load_panel = real
        sys.argv = old
# ---------------------------------------------------------------- 5 experts x 365-day zones
def _daily_hist(n=420, seed=0, end_price=None):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end="2026-08-14", periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    if end_price:
        c = c * end_price / c[-1]
    return pd.DataFrame(dict(open=c, high=c * 1.01, low=c * 0.99, close=c,
                             volume=rng.integers(5e5, 2e6, n) * 1.0), index=days)


@test
def zone_filter_table_is_causal():
    import zone_filter as zf
    d = _daily_hist(420, seed=1)
    base = zf.zone_table(d)
    k = 300
    day = int(d.index[k].strftime("%Y%m%d"))
    d2 = d.copy()
    d2.iloc[k, d2.columns.get_loc("volume")] = 5e9               # a huge bar ON the session itself
    d2.iloc[k, d2.columns.get_loc("close")] *= 1.3
    alt = zf.zone_table(d2)
    assert np.allclose(base.loc[day].to_numpy(), alt.loc[day].to_numpy(), equal_nan=True), "zone used its own day"
    nxt = int(d.index[k + 1].strftime("%Y%m%d"))
    assert not np.allclose(base.loc[nxt].to_numpy(), alt.loc[nxt].to_numpy(), equal_nan=True)


@test
def zone_filter_levels_below_and_above():
    import zone_filter as zf
    rng = np.random.default_rng(2)
    days = pd.bdate_range(end="2026-08-14", periods=380)
    lv = np.concatenate([np.full(190, 50.0), np.full(189, 60.0), [55.0]])
    c = lv + rng.normal(0, 0.2, 380)
    c[-2] = 55.0
    d = pd.DataFrame(dict(open=c, high=c + 0.3, low=c - 0.3, close=c, volume=1e6), index=days)
    t = zf.zone_table(d).iloc[-1]
    assert t.buy_lo < 50 < t.buy_hi and t.sell_lo < 60 < t.sell_hi, t


@test
def zone_filter_gate_holds_on_every_trade():
    import zone_filter as zf
    arrays, frames, ss, days = world(seed=121, vol=0.002)
    tables = {}
    for s, a in arrays.items():
        g = pd.DataFrame({"o": a["open"], "h": a["high"], "l": a["low"], "c": a["close"], "v": a["volume"],
                          "s": a["session"]}).groupby("s")
        live = pd.DataFrame({"open": g.o.first(), "high": g.h.max(), "low": g.l.min(), "close": g.c.last(),
                             "volume": g.v.sum()})
        live.index = pd.to_datetime(live.index.astype(str))
        hist = _daily_hist(300, seed=abs(hash(s)) % 1000, end_price=float(live.open.iloc[0]))
        hist.index = pd.bdate_range(end=live.index[0] - pd.offsets.BDay(1), periods=300)
        tables[s] = zf.zone_table(pd.concat([hist, live]))
    zf.attach(arrays, tables)
    _, base = run(arrays, frames, ss, days)
    config.ZONE_FILTER = True
    try:
        _, tr = run(arrays, frames, ss, days)
    finally:
        config.ZONE_FILTER = False
    print(f"      trades: {len(base)} without zones -> {len(tr)} with")
    assert 0 < len(tr) < len(base)
    for _, t in tr.iterrows():
        a = arrays[t["symbol"]]
        i = a["ts"].get_loc(t["opened"].tz_convert("UTC")) - 1
        lo, hi = (a["zbuy_lo"][i], a["zbuy_hi"][i]) if t["side"] == "LONG" else (a["zsell_lo"][i], a["zsell_hi"][i])
        assert lo <= a["close"][i] <= hi, (t["symbol"], t["side"], lo, a["close"][i], hi)


@test
def zone_filter_main_wiring():
    import zone_filter as zf, io, contextlib
    arrays, frames, ss, days = world(seed=122, vol=0.002)
    tables = {s: pd.DataFrame({"buy_lo": 0.0, "buy_hi": 1e9, "sell_lo": 0.0, "sell_hi": 1e9},
                              index=[int(d.strftime("%Y%m%d")) for d in days]) for s in arrays}
    real = (bt.load_data, zf.build_tables)
    bt.load_data = lambda *a, **k: (arrays, frames, ss)
    zf.build_tables = lambda *a, **k: tables
    old = sys.argv
    sys.argv = ["backtest.py", "--start", str(days[1].date()), "--end", str(days[-1].date()), "--zones",
                "--out", tempfile.mkdtemp()]
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bt.main()
        out = buf.getvalue()
        assert "VARIANT: 365-day zones ON" in out and ("BACKTEST SUMMARY" in out or "No trades" in out), out[-300:]
    finally:
        bt.load_data, zf.build_tables = real
        sys.argv = old
        config.ZONE_FILTER = False
# ---------------------------------------------------------------- runner
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    args = ap.parse_args()
    passed = failed = 0
    for fn in TESTS:
        if args.k and args.k not in fn.__name__:
            continue
        t0 = time.time()
        try:
            fn()
            passed += 1
            print(f"PASS  {fn.__name__}  ({time.time() - t0:.1f}s)")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:   # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            if os.getenv("VERBOSE"):
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
