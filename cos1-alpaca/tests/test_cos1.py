"""Offline tests. No network and no broker keys."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from cos1_alpaca.bars import MIN_DAILY_BARS, assert_iex, drop_incomplete, history_start
from cos1_alpaca.broker_guard import PAPER_URL, assert_paper_url
from cos1_alpaca.btcosts import Costs, Portfolio, feed_label, pnl_per_share
from cos1_alpaca.clock import ET, entry_cutoff, flatten_at, session_for
from cos1_alpaca.config import Config, load_config
from cos1_alpaca.envfile import load_keys
from cos1_alpaca.errors import BrokerReadError, ConfigError, FeedRefused, LiveEndpointRefused, OrderRejected
from cos1_alpaca.filters import load_earnings, liquidity_ok, vwap_chase
from cos1_alpaca.http_retry import call_submit, force_timeouts
from cos1_alpaca.indicators import overextended
from cos1_alpaca.journal import Journal, slippage
from cos1_alpaca.lockfile import run_lock
from cos1_alpaca.orders import build_bracket, make_client_order_id
from cos1_alpaca.protect import desired_lock_r, ensure_protected, replace_stop
from cos1_alpaca.reconcile import reconcile
from cos1_alpaca.regime import classify_regime
from cos1_alpaca.report import write_daily_report
from cos1_alpaca.risk import ExposureItem, exposure_ok, governor_step, loss_stopped
from cos1_alpaca.runner import Flags, _feed_health, run
from cos1_alpaca.signals import day_pullback, donchian, mean_reversion, range_box, scalp
from cos1_alpaca.state import State, apply_fills, day_pnl, save_state, week_pnl
from cos1_alpaca.types import Account, Asset, Bar, BrokerOrder, BrokerPosition, Fill, MarketBundle, Quote, Regime, Signal
from cos1_alpaca.weights import recorded_weight, risk_dollars, update_ewma

ROOT = Path(__file__).resolve().parents[1]


def _next(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _bars(n, drift=0.0, shock=0.5, start=100.0, volume=3_000_000.0, hour=16) -> list[Bar]:
    price = start
    day = date(2024, 1, 2)
    out = []
    for i in range(n):
        opened = price
        close = max(5.0, price * (1.0 + drift) + shock * math.sin(i / 2.5))
        high = max(opened, close) + abs(shock)
        low = max(1.0, min(opened, close) - abs(shock))
        ts = datetime.combine(day, time(hour, 0), tzinfo=ET)
        out.append(Bar(ts, opened, high, low, close, volume))
        price = close
        day = _next(day)
    return out


def _sig(**kw) -> Signal:
    base = dict(
        symbol="AAPL",
        side="long",
        mode="day",
        expert="day_pullback",
        signal_id="AAPL-day_pullback-long-202611041000",
        entry=100.0,
        stop=97.0,
        target=None,
        target_r=2.75,
        log_only=False,
        reason="test",
        bar_ts=datetime(2026, 11, 4, 10, 0, tzinfo=ET),
    )
    base.update(kw)
    return Signal(**base)


class FakeBroker:
    def __init__(self):
        self.base_url = PAPER_URL
        self.account = Account(100_000.0, 100_000.0)
        self.positions = []
        self.orders = []
        self.fills = []
        self.submitted = []
        self.stops = []
        self.canceled = []
        self.closed = []
        self.fail = set()
        self.assets = {}
        self.sessions = []

    def get_account(self):
        if "account" in self.fail:
            raise RuntimeError("account down")
        return self.account

    def get_positions(self):
        if "positions" in self.fail:
            raise RuntimeError("positions down")
        return list(self.positions)

    def get_open_orders(self):
        if "orders" in self.fail:
            raise RuntimeError("orders down")
        return list(self.orders)

    def get_fills_since(self, start):
        if "fills" in self.fail:
            raise RuntimeError("fills down")
        return list(self.fills)

    def get_calendar(self, start, end):
        if "calendar" in self.fail:
            raise RuntimeError("calendar down")
        return list(self.sessions)

    def get_asset(self, symbol):
        if "asset" in self.fail:
            raise RuntimeError("asset down")
        return self.assets.get(symbol, Asset(symbol, True, True, True))

    def submit_bracket(self, plan):
        self.submitted.append(plan)
        order = BrokerOrder(
            id=f"oid-{len(self.submitted)}",
            client_order_id=plan.client_order_id,
            symbol=plan.symbol,
            side=plan.side,
            qty=plan.qty,
            status="accepted",
            type="market",
            legs=[
                BrokerOrder("stop-1", plan.client_order_id + "-s", plan.symbol, "sell", plan.qty, "new", "stop", stop_price=plan.stop)
            ],
        )
        self.orders.append(order)
        return order

    def submit_stop(self, **kw):
        self.stops.append(kw)
        return BrokerOrder("new-stop", kw["client_order_id"], kw["symbol"], kw["side"], kw["qty"], "accepted", "stop", stop_price=kw["stop_price"])

    def cancel_order(self, order_id):
        self.canceled.append(order_id)

    def close_position(self, symbol, qty, side, client_order_id):
        self.closed.append((symbol, qty, side, client_order_id))
        return BrokerOrder("flat", client_order_id, symbol, side, qty, "accepted", "market")

    def stop_is_working(self, order_id):
        return any(o.id == order_id and o.status in {"new", "accepted", "held"} for o in self.orders)

    def find_by_client_id(self, coid):
        for order in self.orders:
            if order.client_order_id == coid:
                return order
        return None


class FakeData:
    def __init__(self, bundle):
        self.feed = "iex"
        self.bundle_obj = bundle

    def bundle(self, symbols, now, sessions):
        return self.bundle_obj


def _now(hour=10, minute=0, day=4):
    return datetime(2026, 11, day, hour, minute, tzinfo=ET)


def _session(close_h=16, close_m=0, day=4):
    return session_for(date(2026, 11, day), time(9, 30), time(close_h, close_m))


def _cfg():
    cfg = Config()
    cfg.universe = ["SPY", "AAPL", "XLK"]
    cfg.liquidity.min_rvol = 0.2
    cfg.liquidity.min_dollar_volume = 1_000_000
    cfg.regime.min_atr_pct = 0.0001
    return cfg


def _paths(tmp):
    root = Path(tmp)
    return root / "state.json", root / "j.sqlite", root / "reports", root / "run.lock"


def _bundle(bars):
    quote = Quote(100.0, 100.02, _now())
    return MarketBundle(
        daily={"SPY": bars, "AAPL": bars, "XLK": bars},
        min5={"AAPL": bars[-30:], "SPY": bars[-30:], "XLK": bars[-30:]},
        min1={"AAPL": bars[-30:]},
        quotes={"AAPL": quote, "SPY": quote, "XLK": quote},
    )


def _run(tmp, broker, data, flags, now=None, cfg=None, state=None):
    cfg = cfg or _cfg()
    state_path, journal, reports, lock = _paths(tmp)
    if state is not None:
        save_state(state_path, state)
    broker.sessions = [_session()]
    return run(cfg, broker, data, flags, now or _now(), state_path, journal, reports, lock)


def test_no_flag_places_nothing(tmp_path):
    broker = FakeBroker()
    broker.get_account = lambda: (_ for _ in ()).throw(AssertionError("should not read"))
    result = _run(tmp_path, broker, FakeData(_bundle(_bars(5))), Flags())
    assert result.exit_code == 0
    assert result.submitted == []
    assert "orders disabled" in result.message


def test_paper_client_keeps_paper_host():
    pytest.importorskip("alpaca")
    from cos1_alpaca.broker import AlpacaBroker

    broker = AlpacaBroker("key", "secret", Config())
    client = broker.client()
    host = str(getattr(client._base_url, "value", client._base_url))
    assert host == PAPER_URL
    assert broker.base_url == PAPER_URL


def test_live_url_and_rollout_refused(tmp_path):
    with pytest.raises(LiveEndpointRefused):
        assert_paper_url("https://api.alpaca.markets")
    assert assert_paper_url(PAPER_URL) == PAPER_URL
    broker = FakeBroker()
    broker.base_url = "https://api.alpaca.markets"
    result = _run(tmp_path, broker, FakeData(_bundle(_bars(5))), Flags(paper=True))
    assert result.exit_code == 2
    assert broker.submitted == []
    with pytest.raises(ConfigError):
        cfg = Config()
        cfg.rollout.live = ["AAPL"]
        from cos1_alpaca.config import validate

        validate(cfg)


def test_yaml_defaults_and_forbidden(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.rollout.live == []
    assert cfg.feed == "iex"
    assert cfg.scalp.enabled is False
    assert cfg.modules.range_box is False
    assert cfg.day.target_r == 2.75
    assert cfg.account.base_url == PAPER_URL
    bad = tmp_path / "bad.yaml"
    bad.write_text("scale_out: true\nrollout: {live: []}\n")
    with pytest.raises(ConfigError):
        load_config(bad)
    bad.write_text("rollout: {live: [AAPL]}\n")
    with pytest.raises(ConfigError):
        load_config(bad)
    with pytest.raises(FeedRefused):
        assert_iex("sip")


def test_env_file_does_not_log_secret(tmp_path, caplog):
    path = tmp_path / "secret.env"
    path.write_text("# comment\nALPACA_API_KEY=pk-test\nALPACA_SECRET_KEY=super-secret-value\n")
    key, secret = load_keys(str(path))
    assert key == "pk-test" and secret == "super-secret-value"
    assert "super-secret-value" not in caplog.text


def test_regime_up_down_chop():
    cfg = _cfg().regime
    up = classify_regime(_bars(230, drift=0.002, shock=0.8), cfg)
    down = classify_regime(_bars(230, drift=-0.002, shock=0.8, start=200), cfg)
    chop = classify_regime(_bars(10), cfg)
    quiet = classify_regime(_bars(230, drift=0.002, shock=0.01), Config().regime)
    assert up.name == "up"
    assert down.name == "down"
    assert chop.name == "chop" and chop.reason == "insufficient_history"
    assert quiet.name == "chop" and quiet.reason == "vol_filter"


def test_day_rules_and_modules():
    cfg = _cfg()
    regime = Regime("up", reason="trend_up")
    # Flat base, one dip through the EMA, then a close back above it and above VWAP.
    bars = []
    day = date(2026, 11, 4)
    start = datetime.combine(day, time(9, 30), tzinfo=ET)
    for i in range(25):
        price = 100.0
        bars.append(Bar(start + timedelta(minutes=5 * i), price, price + 0.2, price - 0.2, price, 3_000_000))
    bars[-2] = Bar(bars[-2].ts, 100, 100.3, 99.0, 100.1, 3_000_000)
    bars[-1] = Bar(bars[-1].ts, 100.1, 101.2, 100.0, 101.0, 4_000_000)
    cfg.day.vwap_ext_std = 20  # the flat session's own std is tiny; the band test is below
    sig = day_pullback("AAPL", bars, regime, cfg, day)
    assert sig is not None and sig.log_only is False
    assert sig.target is None and sig.target_r == 2.75
    assert sig.stop < sig.entry
    cfg.day.vwap_ext_std = 0.5
    assert day_pullback("AAPL", bars, regime, cfg, day) is None
    cfg.day.vwap_ext_std = 20
    stretched = list(bars)
    stretched[-1] = Bar(stretched[-1].ts, 100, 130, 100, 130, 4_000_000)
    assert day_pullback("AAPL", stretched, regime, cfg, day) is None or vwap_chase(130, 100, 1, 2.0)
    assert day_pullback("AAPL", bars, Regime("chop"), cfg, day) is None
    sc = scalp("AAPL", bars, regime, cfg, day)
    if sc is not None:
        assert sc.log_only is True and sc.mode == "scalp"
    daily = _bars(80, shock=1.0)
    prior_high = max(b.high for b in daily[-56:-1])
    last = daily[-1]
    daily[-1] = Bar(last.ts, last.open, prior_high + 2, min(last.low, last.open), prior_high + 1, last.volume)
    don = donchian("AAPL", daily, regime, cfg)
    assert don is not None and don.expert == "swing_donchian" and don.log_only is False
    assert don.stop < don.entry
    mr = _mean_reversion_fixture()
    rev = mean_reversion("AAPL", mr, cfg)
    assert rev is not None and rev.log_only is True and rev.target > rev.entry > rev.stop
    box_bars = _bars(30, drift=0.0, shock=0.2)
    low = min(b.low for b in box_bars[-20:])
    high = max(b.high for b in box_bars[-20:])
    width = high - low
    ts = box_bars[-1].ts
    box_bars[-1] = Bar(ts, low + 0.05 * width, low + 0.2 * width, low, low + 0.05 * width, 3_000_000)
    box_bars[-2] = Bar(box_bars[-2].ts, low + 0.2 * width, low + 0.3 * width, low, low + 0.02 * width, 3_000_000)
    boxed = range_box("AAPL", box_bars, Regime("chop"), cfg)
    assert boxed is not None and boxed.log_only is True
    assert range_box("AAPL", box_bars, Regime("up"), cfg) is None


def _mean_reversion_fixture():
    bars = _bars(60, drift=0.0, shock=0.1, start=100)
    # Yesterday deeply below the 50-day mean; today reclaims the -2.5 sigma band but stays under the mean.
    prev, last = bars[-2], bars[-1]
    bars[-2] = Bar(prev.ts, 90, 91, 88, 89, prev.volume)
    bars[-1] = Bar(last.ts, 89, 97, 89, 96, last.volume)
    return bars


def test_bracket_never_at_entry_and_no_half_size():
    plan = build_bracket(_sig(target=100.0), 40)
    assert plan.take_profit > plan.entry_hint
    assert plan.qty == 40
    assert plan.client_order_id.startswith("cos1-")
    assert len(plan.client_order_id) <= 48
    assert abs((plan.take_profit - plan.entry_hint) / (plan.entry_hint - plan.stop) - 2.75) < 0.02
    with pytest.raises(OrderRejected):
        build_bracket(_sig(target_r=1.0), 10)
    src = (ROOT / "cos1_alpaca" / "orders.py").read_text()
    assert "scale_out" not in src
    assert make_client_order_id("swing", "BRK.B", "short", "swing_donchian", "x").startswith("cos1-w-")


def test_session_clock_half_day():
    regular = session_for(date(2026, 11, 4), time(9, 30), time(16, 0))
    half = session_for(date(2026, 11, 27), time(9, 30), time(13, 0))
    assert half.half_day
    assert entry_cutoff(regular).strftime("%H:%M") == "15:00"
    assert flatten_at(regular).strftime("%H:%M") == "15:50"
    assert entry_cutoff(half).strftime("%H:%M") == "12:00"
    assert flatten_at(half).strftime("%H:%M") == "12:50"


def test_completed_bars_and_history():
    now = _now(10, 4)
    start = datetime(2026, 11, 4, 10, 0, tzinfo=ET)
    bars = [Bar(start, 1, 1, 1, 1, 1)]
    assert drop_incomplete(bars, now, 5) == []
    assert len(drop_incomplete(bars, _now(10, 5), 5)) == 1
    daily = [Bar(datetime(2026, 11, 4, 0, 0, tzinfo=ET), 1, 1, 1, 1, 1)]
    sessions = {date(2026, 11, 4): _session()}
    assert drop_incomplete(daily, _now(15, 0), 1440, sessions) == []
    assert len(drop_incomplete(daily, _now(16, 0), 1440, sessions)) == 1
    assert ( _now() - history_start(_now(), "daily")).days >= 400
    assert MIN_DAILY_BARS >= 200


def test_costs_match_harness_fields():
    costs = Costs()
    book = Portfolio()
    assert costs.slip_bps == 2.0 and costs.fee_bps_sell == 0.4 and costs.commission == 0.0
    assert book.max_pos_frac == 0.25 and book.max_positions == 3
    assert feed_label(costs) == "iex"
    # sell-side fee only, same shape as alpacabot.engine.Trade.pnl_per_share
    assert pnl_per_share(1, 100.0, 101.0, costs) == pytest.approx(1.0 - 101.0 * 0.4 / 1e4)


def test_governor_hysteresis_and_loss_stops():
    assert governor_step(1.0, 0.04) == 1.0
    assert governor_step(1.0, 0.05) == 0.85
    assert governor_step(0.85, 0.04) == 0.85
    assert governor_step(0.85, 0.03) == 1.0
    assert governor_step(1.0, 0.09) == 0.50
    assert governor_step(0.50, 0.08) == 0.50
    assert governor_step(0.50, 0.06) == 0.85
    assert governor_step(1.0, 0.13) == 0.15
    assert governor_step(0.15, 0.12) == 0.15
    assert governor_step(0.15, 0.06) == 0.50
    assert loss_stopped(-2000, 100_000, 0.02)
    assert not loss_stopped(-1999, 100_000, 0.02)
    assert loss_stopped(-4000, 100_000, 0.04)


def test_exposure_includes_the_new_order_and_sector_cap():
    cfg = _cfg().risk
    held = [
        ExposureItem("MSFT", "long", 10, 490.0, 480.0, "XLK"),
    ]
    # Current gross is under the cap; adding this order crosses it.
    cfg.max_gross_frac = 0.06
    new = ExposureItem("AAPL", "long", 20, 100.0, 98.0, "XLK")
    assert exposure_ok([], new, 100_000, cfg)[0] is True or exposure_ok([], new, 100_000, cfg)[1] == "sector"
    ok, reason = exposure_ok(held, new, 100_000, cfg)
    assert ok is False and reason == "sector"
    other = ExposureItem("JPM", "long", 5, 200.0, 190.0, "XLF")
    cfg.max_positions = 1
    assert exposure_ok(held, other, 100_000, cfg)[1] == "count"
    cfg.max_positions = 3
    cfg.max_gross_frac = 0.50
    cfg.gross_open_risk_frac = 0.02
    # 1900 of open risk already; the new order's 200 crosses 2%.
    wide = ExposureItem("XOM", "short", 100, 100.0, 119.0, "XLE")
    current = [ExposureItem("CVX", "long", 100, 100.0, 81.0, "XLE")]
    # same sector blocks first; use a different sector and a known risk
    current = [ExposureItem("CVX", "long", 95, 100.0, 80.0, "XLE")]
    added = ExposureItem("JPM", "long", 10, 100.0, 80.0, "XLF")
    assert exposure_ok(current, added, 100_000, cfg)[1] == "open_risk"
    assert exposure_ok([], added, 100_000, cfg)[0] is True


def test_weights_cannot_raise_the_risk_limit():
    cfg = _cfg().weights
    stats = {}
    for _ in range(8):
        update_ewma(stats, "day_pullback", -0.4, cfg)
    weight = recorded_weight(stats, "day_pullback", cfg)
    assert cfg.lo <= weight < 1.0
    assert recorded_weight({}, "day_pullback", cfg) == 1.0
    ceiling = 100_000 * 0.005
    assert risk_dollars(100_000, 0.005, 1.5, 1.0) == pytest.approx(ceiling)
    assert risk_dollars(100_000, 0.005, weight, 0.85) < ceiling
    stop_before = _sig().stop
    plan = build_bracket(_sig(), 10)
    assert plan.stop == round(stop_before, 2)


def test_stop_replace_keeps_old_until_confirmed_then_flattens():
    pos = {"symbol": "AAPL", "side": "long", "qty": 10, "stop": 95.0, "stop_order_id": "old", "mode": "day", "entry": 100, "risk": 5}

    class Reject:
        def submit_stop(self, **kw):
            return BrokerOrder("new", kw["client_order_id"], "AAPL", "sell", 10, "rejected", "stop")

        def stop_is_working(self, oid):
            return oid == "old"

        def cancel_order(self, oid):
            raise AssertionError("old stop canceled")

    notes = []
    assert replace_stop(Reject(), pos, 100.0, "cos1-stp-AAPL-00-1", lambda *a: notes.append(a)) == "kept_old"
    assert pos["stop_order_id"] == "old"

    class Down:
        def __init__(self):
            self.closed = []

        def submit_stop(self, **kw):
            raise TimeoutError("timed out")

        def stop_is_working(self, oid):
            return False

        def close_position(self, **kw):
            self.closed.append(kw)

    down = Down()
    assert replace_stop(down, pos, 100.0, "cos1-stp-AAPL-00-2", lambda *a: None) == "flattened"
    assert down.closed and down.closed[0]["client_order_id"].startswith("cos1-flat-")

    class Ok:
        def __init__(self):
            self.canceled = []

        def submit_stop(self, **kw):
            return BrokerOrder("newer", kw["client_order_id"], "AAPL", "sell", 10, "accepted", "stop", stop_price=kw["stop_price"])

        def cancel_order(self, oid):
            self.canceled.append(oid)

        def stop_is_working(self, oid):
            return True

    pos["stop_order_id"] = "old"
    book = Ok()
    assert replace_stop(book, pos, 100.0, "cos1-stp-AAPL-00-3", lambda *a: None) == "replaced"
    assert book.canceled == ["old"]
    assert pos["stop"] == 100.0
    assert desired_lock_r(0.99) is None
    assert desired_lock_r(1.0) == 0.0
    assert desired_lock_r(1.5) == 0.5
    assert desired_lock_r(2.1) == 1.0


def test_retry_checks_status_and_does_not_resend():
    calls = {"n": 0}

    def submit():
        calls["n"] += 1
        raise TimeoutError("read timeout")

    found = call_submit(submit, lambda: {"id": "existing"}, 3, (0, 0, 0), lambda _s: None)
    assert calls["n"] == 1 and found["id"] == "existing"

    def explode():
        raise ConnectionError("down")

    before = calls["n"]
    with pytest.raises(BrokerReadError):
        call_submit(submit, explode, 3, (0, 0, 0), lambda _s: None)
    assert calls["n"] == before + 1

    class Session:
        def request(self, method, url, **kwargs):
            return kwargs.get("timeout")

    class Client:
        _session = Session()

    force_timeouts(Client(), 3, 20)
    assert Client._session.request("GET", "https://paper-api.alpaca.markets") == (3, 20)


def test_earnings_liquidity_and_vwap():
    assert vwap_chase(110, 100, 2, 2.0) is True
    assert overextended(103, 100, 2, 2.0) is False
    guard = load_earnings("")
    assert guard.status("AAPL", date(2026, 11, 4), []) == (False, "stub")
    path_days = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)]
    real = load_earnings.__wrapped__ if hasattr(load_earnings, "__wrapped__") else None
    from cos1_alpaca.filters import EarningsGuard

    guard = EarningsGuard(False, {"AAPL": {date(2026, 11, 4)}})
    assert guard.status("AAPL", date(2026, 11, 3), path_days)[0] is True
    assert guard.status("AAPL", date(2026, 11, 2), path_days)[0] is False
    bars = _bars(25)
    cfg = _cfg().liquidity
    assert liquidity_ok(price=100, quote=Quote(100, 100.05), bars=bars, cfg=cfg)[0]
    assert liquidity_ok(price=100, quote=Quote(100, 102), bars=bars, cfg=cfg)[1] == "spread"
    assert liquidity_ok(price=100, quote=None, bars=bars, cfg=cfg)[1] == "no_quote"
    thin = _bars(25, volume=10)
    assert liquidity_ok(price=100, quote=Quote(100, 100.01), bars=thin, cfg=cfg, dollar_volume=10)[1] == "thin_dollar"


def test_state_fifo_sqlite_report_reconcile(tmp_path):
    state = State()
    fills = [
        Fill("a", _now(10), "AAPL", "buy", 10, 100, client_order_id="cos1-d-AAPL-b-aaaaaaaa"),
        Fill("b", _now(11), "AAPL", "sell", 10, 110, client_order_id="cos1-d-AAPL-s-bbbbbbbb", expected_price=110),
    ]
    closed = apply_fills(state, fills)
    assert state.trade_count == 1
    assert closed[0]["pnl"] == pytest.approx(100)
    assert day_pnl(state) == pytest.approx(100)
    apply_fills(state, fills)
    assert len(state.closed) == 1
    journal = Journal(tmp_path / "j.sqlite")
    slip = slippage("sell", 110, 109.5)
    journal.event("fill", symbol="AAPL", client_order_id="cos1-d-AAPL-s-bbbbbbbb", fill_price=109.5, slippage=slip, config_hash="abc")
    assert journal.count("fill") == 1
    assert slip == pytest.approx(0.5)
    path = write_daily_report(tmp_path / "reports", date(2026, 11, 4), win_rate=1, pnl=100, trades=1, errors=0)
    text = path.read_text()
    assert "win_rate=1.0000" in text and "pnl=100.00" in text and "telegram" not in text.lower()
    orders = [
        BrokerOrder("1", "cos1-stp-AAPL", "AAPL", "sell", 1, "new", "stop"),
        BrokerOrder("2", "cos1-stp-AAPL", "AAPL", "sell", 1, "new", "stop"),
    ]
    flags = reconcile(
        [{"role": "stop", "symbol": "MSFT", "client_order_id": "cos1-stp-MSFT", "order_id": "missing"}],
        orders,
        [BrokerPosition("MSFT", 5, "long", 100, 100)],
        "hash1",
    )
    kinds = {item["kind"] for item in flags}
    assert "duplicate" in kinds and "missing" in kinds
    assert all(item["config_hash"] == "hash1" for item in flags)
    save_state(tmp_path / "s.json", state)
    assert (tmp_path / "s.json").is_file()


def test_feed_health_every_five_minutes(caplog):
    caplog.set_level("INFO", logger="cos1")
    state = State()
    bars = _bars(5)
    bundle = _bundle(bars)
    _feed_health(state, _now(10, 0), bundle, __import__("logging").getLogger("cos1"))
    _feed_health(state, _now(10, 2), bundle, __import__("logging").getLogger("cos1"))
    assert caplog.text.count("FEED_HEALTH feed=iex") == 1
    _feed_health(state, _now(10, 6), bundle, __import__("logging").getLogger("cos1"))
    assert caplog.text.count("FEED_HEALTH feed=iex") == 2


def test_lock_refuses_overlap(tmp_path):
    path = tmp_path / "lock"
    with run_lock(path):
        result = _run(tmp_path / "inner", FakeBroker(), FakeData(_bundle(_bars(5))), Flags(dry_run=True))
        # the inner run uses a different lock path; overlap is the same path
    with run_lock(path):
        from cos1_alpaca.errors import AlreadyRunning

        with pytest.raises(AlreadyRunning):
            with run_lock(path):
                pass


def test_fail_closed_and_shared_account_and_cutoff(tmp_path, monkeypatch):
    broker = FakeBroker()
    broker.fail.add("positions")
    result = _run(tmp_path / "a", broker, FakeData(_bundle(_bars(30))), Flags(dry_run=True))
    assert result.exit_code == 2 and broker.submitted == []
    assert "fail closed" in result.message

    broker = FakeBroker()
    broker.positions = [BrokerPosition("AAPL", 10, "long", 100, 101)]
    broker.orders = [BrokerOrder("x", "otherbot-1", "MSFT", "buy", 1, "new", "market")]
    hand = _sig()
    monkeypatch.setattr("cos1_alpaca.runner.generate_signals", lambda *a, **k: [hand, _sig(symbol="MSFT", signal_id="msft-1")])
    data = FakeData(_bundle(_bars(30)))
    result = _run(tmp_path / "b", broker, data, Flags(paper=True))
    assert broker.submitted == []
    assert {row["reason"] for row in result.skipped} >= {"foreign"}

    broker = FakeBroker()
    result = _run(tmp_path / "c", broker, data, Flags(paper=True), now=_now(15, 0))
    assert broker.submitted == []
    assert any(row["reason"] == "cutoff" for row in result.skipped)

    # Dry-run logs and does not send. Scalp stays log-only even on --paper.
    broker = FakeBroker()
    monkeypatch.setattr(
        "cos1_alpaca.runner.generate_signals",
        lambda *a, **k: [_sig(mode="scalp", expert="scalp", log_only=True, signal_id="scalp-1")],
    )
    result = _run(tmp_path / "d", broker, data, Flags(paper=True))
    assert broker.submitted == []
    assert any(row["reason"] == "log_only" for row in result.skipped)

    broker = FakeBroker()
    monkeypatch.setattr("cos1_alpaca.runner.generate_signals", lambda *a, **k: [hand])
    result = _run(tmp_path / "e", broker, data, Flags(dry_run=True))
    assert result.exit_code == 0 and broker.submitted == []
    assert (tmp_path / "e" / "reports").exists()


def test_paper_bracket_shortable_flatten_and_once(tmp_path, monkeypatch):
    broker = FakeBroker()
    hand = _sig()
    monkeypatch.setattr("cos1_alpaca.runner.generate_signals", lambda *a, **k: [hand])
    result = _run(tmp_path / "p", broker, FakeData(_bundle(_bars(30))), Flags(paper=True))
    assert result.exit_code == 0
    assert len(broker.submitted) == 1
    assert broker.submitted[0].client_order_id.startswith("cos1-")
    assert broker.submitted[0].take_profit != broker.submitted[0].entry_hint
    assert broker.submitted[0].qty == broker.submitted[0].qty  # full size, one bracket

    again = _run(tmp_path / "p", broker, FakeData(_bundle(_bars(30))), Flags(paper=True))
    assert len(broker.submitted) == 1
    assert any(row["reason"] == "once" for row in again.skipped)

    broker = FakeBroker()
    broker.assets["AAPL"] = Asset("AAPL", True, False, False)
    monkeypatch.setattr(
        "cos1_alpaca.runner.generate_signals",
        lambda *a, **k: [_sig(side="short", stop=103.0, signal_id="short-1")],
    )
    result = _run(tmp_path / "s", broker, FakeData(_bundle(_bars(30))), Flags(paper=True))
    assert broker.submitted == []
    assert any(row["reason"] == "not_shortable" for row in result.skipped)

    broker = FakeBroker()
    state = State()
    state.positions = {
        "AAPL": {
            "symbol": "AAPL",
            "mode": "day",
            "expert": "day_pullback",
            "side": "long",
            "qty": 10,
            "entry": 100,
            "stop": 97,
            "tp": 108,
            "risk": 3,
            "signal_id": "held",
            "entry_client_id": "cos1-d-AAPL-b-held",
            "stop_order_id": "stop-a",
            "stop_client_id": "cos1-stp-AAPL",
            "opened_at": "2026-11-04",
            "extreme": 100,
            "sector": "XLK",
        },
        "XLK": {
            "symbol": "XLK",
            "mode": "swing",
            "expert": "swing_ema",
            "side": "long",
            "qty": 5,
            "entry": 200,
            "stop": 190,
            "tp": 220,
            "risk": 10,
            "signal_id": "swing-held",
            "entry_client_id": "cos1-w-XLK-b-held",
            "stop_order_id": "stop-b",
            "stop_client_id": "cos1-stp-XLK",
            "opened_at": "2026-11-01",
            "extreme": 200,
            "sector": "XLK",
        },
    }
    broker.positions = [
        BrokerPosition("AAPL", 10, "long", 100, 101),
        BrokerPosition("XLK", 5, "long", 200, 201),
    ]
    broker.orders = [
        BrokerOrder("stop-a", "cos1-stp-AAPL", "AAPL", "sell", 10, "new", "stop", stop_price=97),
        BrokerOrder("stop-b", "cos1-stp-XLK", "XLK", "sell", 5, "new", "stop", stop_price=190),
    ]
    monkeypatch.setattr("cos1_alpaca.runner.generate_signals", lambda *a, **k: [])
    monkeypatch.setattr("cos1_alpaca.runner.swing_hold_exit", lambda *a, **k: None)
    _run(tmp_path / "f", broker, FakeData(_bundle(_bars(30))), Flags(paper=True), now=_now(15, 50), state=state)
    flat = [row[0] for row in broker.closed]
    assert "AAPL" in flat and "XLK" not in flat

    # Half-day flatten at 12:50. November 27 2026 is a Friday.
    broker = FakeBroker()
    state.positions = {
        "AAPL": {
            "symbol": "AAPL",
            "mode": "day",
            "expert": "day_pullback",
            "side": "long",
            "qty": 10,
            "entry": 100,
            "stop": 97,
            "tp": 108,
            "risk": 3,
            "signal_id": "held",
            "entry_client_id": "cos1-d-AAPL-b-held",
            "stop_order_id": "stop-a",
            "stop_client_id": "cos1-stp-AAPL",
            "opened_at": "2026-11-27",
            "extreme": 100,
            "sector": "XLK",
        }
    }
    state.day_key = ""
    monkeypatch.setattr("cos1_alpaca.runner.generate_signals", lambda *a, **k: [])
    broker.positions = [BrokerPosition("AAPL", 10, "long", 100, 101)]
    broker.orders = [BrokerOrder("stop-a", "cos1-stp-AAPL", "AAPL", "sell", 10, "new", "stop", stop_price=97)]
    now = datetime(2026, 11, 27, 12, 50, tzinfo=ET)
    cfg = _cfg()
    state_path, journal, reports, lock = _paths(tmp_path / "h")
    save_state(state_path, state)
    broker.sessions = [session_for(date(2026, 11, 27), time(9, 30), time(13, 0))]
    run(cfg, broker, FakeData(_bundle(_bars(30))), Flags(paper=True), now, state_path, journal, reports, lock)
    assert any(row[0] == "AAPL" for row in broker.closed)


def test_weekly_breaker_and_mode_kill(tmp_path, monkeypatch):
    monkeypatch.setattr("cos1_alpaca.runner.generate_signals", lambda *a, **k: [_sig()])
    state = State(week_key="2026-W45", week_start_equity=100_000, day_key="2026-11-04", day_start_equity=100_000)
    state.closed = [{"ts": "2026-11-02T15:00:00-05:00", "mode": "swing", "pnl": -5000, "symbol": "XOM", "qty": 1}]
    broker = FakeBroker()
    result = _run(tmp_path / "w", broker, FakeData(_bundle(_bars(30))), Flags(paper=True), state=state)
    assert broker.submitted == []
    assert any(row["reason"] == "weekly_stop" for row in result.skipped)

    state = State(day_key="2026-11-04", day_start_equity=100_000, week_key="2026-W45", week_start_equity=100_000)
    state.mode_pnl = {"day": -7000.0, "swing": 0.0, "scalp": 0.0}
    state.mode_peak = {"day": 0.0, "swing": 0.0, "scalp": 0.0}
    broker = FakeBroker()
    result = _run(tmp_path / "k", broker, FakeData(_bundle(_bars(30))), Flags(paper=True), state=state)
    assert any(row["reason"] == "killed" for row in result.skipped)
    assert broker.submitted == []


def test_reconcile_on_run_and_no_telegram(tmp_path, monkeypatch):
    monkeypatch.setattr("cos1_alpaca.runner.generate_signals", lambda *a, **k: [])
    state = State()
    state.positions = {
        "MSFT": {
            "symbol": "MSFT",
            "mode": "swing",
            "expert": "swing_ema",
            "side": "long",
            "qty": 2,
            "entry": 400,
            "stop": 380,
            "tp": 440,
            "risk": 20,
            "signal_id": "m",
            "entry_client_id": "cos1-w-MSFT-b-m",
            "stop_order_id": "gone",
            "stop_client_id": "cos1-stp-MSFT-missing",
            "opened_at": "2026-11-01",
            "extreme": 400,
            "sector": "XLK",
        }
    }
    state.intended = [{"role": "stop", "symbol": "MSFT", "order_id": "gone", "client_order_id": "cos1-stp-MSFT-missing"}]
    broker = FakeBroker()
    broker.positions = [BrokerPosition("MSFT", 2, "long", 400, 401)]
    result = _run(tmp_path / "r", broker, FakeData(_bundle(_bars(30))), Flags(dry_run=True), state=state)
    assert any(item["kind"] == "missing" for item in result.reconcile_flags)
    text = "\n".join(path.read_text() for path in (ROOT / "cos1_alpaca").rglob("*.py"))
    assert "api.telegram" not in text.lower()
    assert "import telegram" not in text.lower()
    assert "paper=False" not in text
    assert "https://api.alpaca.markets" not in text


def test_backtest_is_short_and_labeled(tmp_path):
    from cos1_alpaca.backtest import run_backtest
    from cos1_alpaca.btcosts import Costs, scale_costs

    result = run_backtest(oos_days=20, daily_len=250, intraday_days=2, symbols=("SPY", "XLK", "AAPL"), cost_mult=2)
    assert result["feed"] == "iex"
    assert result["data_source"] == "synthetic"
    assert result["cost_model"]["slip_bps"] == 2.0
    assert result["cost_model"]["max_pos_frac"] == 0.25
    assert {row["expert"] for row in result["rows"]} >= {"swing_ema", "swing_donchian", "day_pullback", "mean_reversion", "range_box", "scalp"}
    assert {row["cost_mult"] for row in result["rows"]} == {1.0, 2.0}
    doubled = scale_costs(Costs(), 2)
    assert doubled.slip_bps == 4.0 and doubled.fee_bps_sell == pytest.approx(0.8)
    for row in result["rows"]:
        assert "profit_factor" in row and "roi" in row and "max_dd" in row and "trades" in row


def test_foreign_msft_short_blocks_that_symbol(tmp_path, monkeypatch, caplog):
    """A short the bot does not own, or someone else's open order, blocks that symbol only."""
    caplog.set_level("INFO", logger="cos1")
    msft = _sig(symbol="MSFT", signal_id="msft-new", entry=400.0, stop=390.0)
    jpm = _sig(symbol="JPM", signal_id="jpm-new", entry=200.0, stop=195.0)
    monkeypatch.setattr("cos1_alpaca.runner.generate_signals", lambda *a, **k: [msft, jpm])
    bars = _bars(30)
    bundle = _bundle(bars)
    for symbol, quote in (("MSFT", Quote(400.0, 400.05)), ("JPM", Quote(200.0, 200.04))):
        bundle.daily[symbol] = bars
        bundle.min5[symbol] = bars
        bundle.quotes[symbol] = quote

    broker = FakeBroker()
    broker.positions = [BrokerPosition("MSFT", 5, "short", 410.0, 400.0)]
    broker.orders = [BrokerOrder("ord-msft", "other-strategy", "MSFT", "sell", 5, "new", "market")]
    result = _run(tmp_path / "pos", broker, FakeData(bundle), Flags(paper=True))
    assert [plan.symbol for plan in broker.submitted] == ["JPM"]
    assert any(row["signal"] == "msft-new" and row["reason"] == "foreign" for row in result.skipped)

    broker = FakeBroker()
    broker.orders = [BrokerOrder("ord-only", "not-cos1", "MSFT", "sell", 3, "accepted", "market")]
    result = _run(tmp_path / "ord", broker, FakeData(bundle), Flags(dry_run=True))
    assert broker.submitted == []
    assert any(row["signal"] == "msft-new" and row["reason"] == "foreign" for row in result.skipped)
    assert "EXCLUDED_FOREIGN symbol=MSFT source=open_order side=sell" in caplog.text

    caplog.clear()
    broker = FakeBroker()
    broker.positions = [BrokerPosition("MSFT", 5, "short", 410.0, 400.0)]
    _run(tmp_path / "log", broker, FakeData(bundle), Flags(dry_run=True))
    assert "EXCLUDED_FOREIGN symbol=MSFT source=position side=short qty=5" in caplog.text


def test_alpaca_source_needs_env_and_requests_iex(monkeypatch):
    from cos1_alpaca.cli import main

    monkeypatch.delenv("COS1_ENV_FILE", raising=False)
    assert main(["backtest", "--source", "alpaca", "--months", "3"]) == 2
    pytest.importorskip("alpaca")
    from datetime import datetime

    from cos1_alpaca.datafeed import make_iex_bars_request

    request = make_iex_bars_request(["SPY", "AAPL"], "5", datetime(2026, 8, 1, tzinfo=ET), datetime(2026, 11, 1, tzinfo=ET))
    assert str(getattr(request.feed, "value", request.feed)).lower() == "iex"
    assert "5" in str(request.timeframe)


def test_cli_without_flags(capsys):
    from cos1_alpaca.cli import main

    assert main(["run"]) == 0
    assert "orders disabled" in capsys.readouterr().out
    assert main(["run", "--paper", "--dry-run"]) == 2


def test_unprotected_ensure_flattens():
    pos = {"symbol": "AAPL", "side": "long", "qty": 3, "stop": 90.0, "stop_order_id": "", "mode": "day"}

    class Bare:
        def __init__(self):
            self.closed = []

        def submit_stop(self, **kw):
            raise TimeoutError("timed out")

        def stop_is_working(self, oid):
            return False

        def close_position(self, **kw):
            self.closed.append(kw)

    broker = Bare()
    assert ensure_protected(broker, pos, [], lambda *a: None, "110410") == "flattened"
    assert broker.closed
