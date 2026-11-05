#!/usr/bin/env python3
"""Stock Bot — top-10 experts + top-5 hedge-fund principles overlay.
Three sleeves in one runner: scalp (<=30 min), day-trade (10:00-15:45 ET,
flatten 15:50), swing (daily, 2R TP).

PAPER ONLY. --live ALWAYS refuses with exit code 2. With NO flag the bot
does nothing (exit 0). --paper trades paper only; --dry-run previews
with REAL Alpaca bars when keys are present (synthetic demo bars only
when no keys are present, clearly labelled SYNTHETIC).

Version 0.2.0 — Claude fix list, 2026-11-05 (all paper-only):
- REAL EXITS: every entry is submitted as a bracket order (entry + stop
  + target legs) with its own client_order_id. Fills and open positions
  are tracked between runs in state.json. Breakeven move (+1R), virtual
  stair trail (replaces the stop leg), max hold time, swing 2R/EMA exits
  and the 15:50 ET day flatten are enforced every run.
- SAVED STATE: state.json (local only, never in git) holds daily P&L
  (realized, reconstructed from broker order fills), trade count, and
  every Muse1 position with its order ids. A lock file stops overlapping
  runs. Repeat/opposite orders on a held stock are blocked; open orders
  are checked before any new order.
- STOP ON ERRORS: a failed positions/account read skips the run instead
  of assuming the account is empty. The exposure check counts the new
  order's size and direction. Shorts are checked shortable first. API
  calls retry with backoff; after any order-submission error the order
  status is verified by client_order_id before deciding what happened.
  (alpaca-py 0.44.0 exposes no per-call HTTP timeout, so the timeout
  is retry + verification, not a hard socket timeout — documented.)
- DATA: feed=iex explicitly, start/end bar requests (no bare limit=100),
  completed bars only, market calendar (Alpaca get_calendar, static
  fallback) for holidays/half-days, swing signals deduped in state.
- STRATEGY (experts.py): Crabel enters only AFTER the breakout bar
  closes beyond the narrow bar; Brooks/Dalton require reward:risk >= 1.5
  on the actual stop distance; Ponsi EMA timing fixed (prior close vs
  prior bar's EMA10); Bollinger squeeze now compares bandwidth on the
  same 20-bar window. Costs are checked against the ACTUAL risk dollars
  (qty x stop distance after the notional cap), not the intended risk.
- ROLLOUT: ONE expert in log-only mode (raschke_soup — the only expert
  with a real-data record on two engines); no expert places real orders
  yet. Backtest with realistic costs is still owed.
- ACCOUNT: this bot MUST run on its own Alpaca paper account, separate
  from Brain. The user/Claude must create that paper account and put
  its keys in the VM .env — Muse cannot create it.

UNVALIDATED: no out-of-sample backtest yet. Do NOT run it on the same
Alpaca paper account as the existing bots. Do NOT deploy it live.
"""
import argparse
import fcntl
import json
import math
import os
import random
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import yaml  # noqa: E402

import experts  # noqa: E402
from experts import scan, SLEEVE_EXPERTS  # noqa: E402
from hedge_fund_overlay import (  # noqa: E402
    vol_scaled_size, costs, check_portfolio_limits, stress_test,
    exit_policy, combine_signals,
)
from risk import RiskState  # noqa: E402

BOT_NAME = "muse1/alpaca"
VERSION = "0.2.0-claude-fixes (paper only, unvalidated)"
STATE_FILE = HERE / "state.json"
LOCK_FILE = HERE / "bot.lock"
ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------- config

def load_config(path=None):
    path = Path(path) if path else HERE / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)

def load_dotenv():
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# ---------------------------------------------------------------- market calendar
# NYSE holidays / early closes. PRIMARY source is Alpaca get_calendar();
# this static list is only the fallback when that call fails. Verify
# against the NYSE calendar when a new year starts.

US_HOLIDAYS = {
    2026: [(1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 3),
           (9, 7), (11, 26), (12, 25)],
    2027: [(1, 1), (1, 18), (2, 15), (3, 26), (5, 31), (6, 18), (7, 5),
           (9, 6), (11, 25), (12, 24)],
}
# Early closes (13:00 ET). 2026: day after Thanksgiving, Christmas Eve.
US_HALF_DAYS = {
    2026: [(11, 27), (12, 24)],
    2027: [(11, 26)],
}

def _static_market_status(today):
    """(status, flatten_et) from the static calendar. status: open|holiday|half_day."""
    if today.weekday() >= 5:
        return "holiday", None
    y = today.year
    if (today.month, today.day) in US_HOLIDAYS.get(y, []):
        return "holiday", None
    if (today.month, today.day) in US_HALF_DAYS.get(y, []):
        return "half_day", "13:00"
    return "open", None

def market_status(broker, today):
    """Market status for `today` (a date, ET). Tries Alpaca get_calendar()
    first; falls back to the static calendar. Returns
    (status, flatten_et, source) with status open|holiday|half_day."""
    try:
        cal = broker.get_calendar(today, today)
        if not cal:
            return "holiday", None, "alpaca-calendar"
        day = cal[0]
        close_t = getattr(day, "close", None)
        # Alpaca Calendar.close is a time; 13:00 means an early close.
        if close_t is not None and getattr(close_t, "hour", 16) < 16:
            return "half_day", "13:00", "alpaca-calendar"
        return "open", None, "alpaca-calendar"
    except Exception:
        status, flat = _static_market_status(today)
        return status, flat, "static-fallback"

# ---------------------------------------------------------------- state file
# state.json lives in the bot folder, is NEVER committed to git
# (.gitignore), and is the only memory between run-once invocations:
# daily realized P&L, trade count, Muse1 positions with order ids,
# swing signals already acted on. Positions are keyed by symbol.

def default_state():
    return {"version": 1, "bot": BOT_NAME, "day": date.today().isoformat(),
            "trades_today": 0, "day_pnl_dollars": 0.0, "day_pnl_r": 0.0,
            "positions": {}, "swing_signals_seen": {}, "notes": []}

def load_state():
    """Returns (state, corrupt). A corrupt/unreadable state file is
    reported, not silently replaced — paper trading refuses on it."""
    if not STATE_FILE.exists():
        return default_state(), False
    try:
        st = json.loads(STATE_FILE.read_text())
        if not isinstance(st, dict) or "positions" not in st:
            return default_state(), True
        st.setdefault("swing_signals_seen", {})
        st.setdefault("trades_today", 0)
        st.setdefault("day_pnl_dollars", 0.0)
        st.setdefault("day_pnl_r", 0.0)
        st.setdefault("notes", [])
        return st, False
    except Exception:
        return default_state(), True

def save_state(state):
    """Atomic save: write to temp, then replace. Dry runs never call this."""
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.replace(tmp, STATE_FILE)

def rollover_state(state):
    """New day: reset the day counters, keep open positions (they carry
    over — e.g. the MSFT short), clear yesterday's swing-signal keys."""
    today = date.today().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["trades_today"] = 0
        state["day_pnl_dollars"] = 0.0
        state["day_pnl_r"] = 0.0
        state["swing_signals_seen"] = {}
        state.setdefault("notes", []).append(f"rollover to {today}")
    return state

# ---------------------------------------------------------------- run lock
# One run at a time. A second invocation while the lock is held exits
# immediately without trading (exit 0, "lock busy" message).

@contextmanager
def run_lock():
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        yield None
        return
    try:
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

# ---------------------------------------------------------------- demo bars (dry-run / no keys)

def synthetic_bars(symbol, n=60, seed=42, timeframe="5min"):
    """Deterministic demo bars so --dry-run can exercise the experts with
    no keys and no network. Labelled SYNTHETIC everywhere it is used.
    A seeded random walk with a mild trend and a squeeze — not real data.
    Bars carry a "t" timestamp (completed bars ending now) so the SPY
    filter and completed-bar logic work the same as on real data."""
    rng = random.Random(seed + sum(ord(ch) for ch in symbol))
    bars = []
    price = 100.0 + rng.random() * 200
    now = datetime.now(timezone.utc)
    step = timedelta(minutes=5) if timeframe == "5min" else timedelta(days=1)
    start = now - step * n
    for i in range(n):
        drift = 0.15 if i > 40 else 0.0
        o = price
        c = o + rng.gauss(drift, 1.0)
        h = max(o, c) + rng.random() * 0.5
        l = min(o, c) - rng.random() * 0.5
        bars.append({"t": start + step * (i + 1), "o": round(o, 2),
                     "h": round(h, 2), "l": round(l, 2),
                     "c": round(c, 2), "v": 1000 + rng.randint(0, 5000)})
        price = c
    return bars

# ---------------------------------------------------------------- brokers

def _retry(fn, what, tries=3):
    """Retry an API call with backoff. On final failure raise RuntimeError.
    NOTE: alpaca-py 0.44.0 exposes no per-call HTTP timeout on either
    client, so a hung call is bounded by retries + the verification
    steps in the order paths, not by a socket timeout. Documented."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - retried, then re-raised
            last = e
            if i < tries - 1:
                time.sleep(2 ** i)
    raise RuntimeError(f"Alpaca call failed ({what}) after {tries} tries: {last}")

class PaperBrokerStub:
    """No-keys paper broker: records what WOULD be ordered, places nothing.
    Used for --dry-run with no keys. Same interface as AlpacaBroker."""
    def __init__(self, equity=100000.0):
        self.equity = equity
        self.would_orders = []

    def account(self):
        return None

    def positions(self):
        return {}

    def open_orders(self, symbols=None):
        return []

    def get_order(self, order_id):
        return None

    def get_order_by_client_id(self, client_order_id):
        return None

    def shortable(self, symbol):
        return True

    def get_clock(self):
        return None

    def get_calendar(self, start, end):
        return []

    def submit_bracket(self, symbol, side, qty, stop_price, target_price,
                       sleeve, expert, tif="DAY", client_order_id=None):
        order = {"symbol": symbol, "side": side, "qty": qty,
                 "stop_price": stop_price, "target_price": target_price,
                 "sleeve": sleeve, "expert": expert,
                 "client_order_id": client_order_id or "stub",
                 "status": "WOULD_PLACE_BRACKET (stub, no keys / dry-run)"}
        self.would_orders.append(order)
        return order

    def replace_stop(self, order_id, new_stop_price):
        return False, "stub: no real order to replace"

    def cancel_order(self, order_id):
        return False, "stub: no real order to cancel"

    def close_market(self, symbol, qty, order_side):
        return {"symbol": symbol, "qty": qty, "side": order_side,
                "status": "WOULD_CLOSE (stub)"}

class AlpacaBroker:
    """Real Alpaca paper broker, only constructed when keys are present
    and --paper was given (or --dry-run with keys). Live is blocked
    before this is ever built."""
    def __init__(self, key, secret):
        from alpaca.trading.client import TradingClient
        self.client = TradingClient(key, secret, paper=True)

    def account(self):
        return _retry(lambda: self.client.get_account(), "get_account")

    def equity(self):
        return float(self.account().equity)

    def positions(self):
        return {p.symbol: p for p in
                _retry(lambda: self.client.get_all_positions(), "get_all_positions")}

    def open_orders(self, symbols=None):
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN,
                               symbols=symbols) if symbols else \
            GetOrdersRequest(status=QueryOrderStatus.OPEN)
        return _retry(lambda: self.client.get_orders(req), "get_orders(open)")

    def closed_orders(self, limit=50):
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=limit)
        return _retry(lambda: self.client.get_orders(req), "get_orders(closed)")

    def get_order(self, order_id):
        return _retry(lambda: self.client.get_order_by_id(order_id),
                      f"get_order({order_id})")

    def get_order_by_client_id(self, client_order_id):
        return _retry(lambda: self.client.get_order_by_client_id(client_order_id),
                      f"get_order_by_client_id({client_order_id})")

    def shortable(self, symbol):
        """Fail-closed for shorts: if we cannot verify, do not short."""
        try:
            asset = _retry(lambda: self.client.get_asset(symbol),
                           f"get_asset({symbol})")
            return bool(asset.shortable)
        except Exception:
            return False

    def get_clock(self):
        return _retry(lambda: self.client.get_clock(), "get_clock")

    def get_calendar(self, start, end):
        # alpaca-py 0.44.0: get_calendar() takes a GetCalendarRequest,
        # NOT start/end kwargs (those raise TypeError).
        from alpaca.trading.requests import GetCalendarRequest
        return _retry(lambda: self.client.get_calendar(
            GetCalendarRequest(start=start, end=end)), "get_calendar")

    def submit_bracket(self, symbol, side, qty, stop_price, target_price,
                       sleeve, expert, tif="DAY", client_order_id=None):
        """Entry + stop + target as ONE bracket order. If the submit call
        itself fails (e.g. timeout), the order status is verified by
        client_order_id before deciding what happened — never assume an
        order did or did not go through."""
        from alpaca.trading.requests import (MarketOrderRequest,
                                            TakeProfitRequest, StopLossRequest)
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
        cid = client_order_id or (
            f"muse1-{date.today().isoformat()}-{symbol}-{sleeve}-"
            f"{uuid.uuid4().hex[:8]}")
        req = MarketOrderRequest(
            symbol=symbol, qty=int(qty),
            side=OrderSide.BUY if side == 1 else OrderSide.SELL,
            time_in_force=TimeInForce.DAY if tif == "DAY" else TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(float(target_price), 2)),
            stop_loss=StopLossRequest(stop_price=round(float(stop_price), 2)),
            client_order_id=cid)
        try:
            order = _retry(lambda: self.client.submit_order(req),
                           "submit_order(bracket)", tries=2)
        except Exception as e:  # noqa: BLE001 - verified below, then re-raised
            existing = None
            try:
                existing = self.client.get_order_by_client_id(cid)
            except Exception:
                existing = None
            if existing is not None:
                return {"symbol": symbol, "side": side, "qty": qty,
                        "stop_price": stop_price, "target_price": target_price,
                        "sleeve": sleeve, "expert": expert,
                        "client_order_id": cid,
                        "status": "SUBMITTED_PAPER (verified after error)",
                        "order_id": str(existing.id),
                        "verified_after_error": True}
            raise RuntimeError(
                f"bracket submit failed and no order found for {cid}: {e}")
        legs = getattr(order, "legs", None) or []
        stop_id = target_id = None
        for leg in legs:
            leg_type = str(getattr(leg, "order_type", "")).lower()
            if "stop" in leg_type:
                stop_id = str(leg.id)
            elif "limit" in leg_type:
                target_id = str(leg.id)
        return {"symbol": symbol, "side": side, "qty": qty,
                "stop_price": stop_price, "target_price": target_price,
                "sleeve": sleeve, "expert": expert, "client_order_id": cid,
                "status": "SUBMITTED_PAPER", "order_id": str(order.id),
                "stop_order_id": stop_id, "target_order_id": target_id}

    def replace_stop(self, order_id, new_stop_price):
        """Move a stop leg to a new price (breakeven / trail). Returns
        (ok, info). The caller falls back to cancel + new stop on failure."""
        from alpaca.trading.requests import ReplaceOrderRequest
        try:
            replaced = _retry(
                lambda: self.client.replace_order_by_id(
                    order_id, ReplaceOrderRequest(stop_price=round(float(new_stop_price), 2))),
                f"replace_order({order_id})", tries=2)
            return True, str(replaced.id)
        except Exception as e:  # noqa: BLE001 - caller falls back
            return False, str(e)

    def cancel_order(self, order_id):
        """Cancel and verify the order is actually cancelled."""
        try:
            _retry(lambda: self.client.cancel_order_by_id(order_id),
                   f"cancel_order({order_id})", tries=2)
        except Exception as e:  # noqa: BLE001 - verified below
            pass
        for _ in range(3):
            try:
                o = self.client.get_order_by_id(order_id)
                if str(o.status).upper().endswith("CANCELED"):
                    return True, "canceled"
            except Exception:
                return True, "order no longer found (assumed canceled)"
            time.sleep(1)
        return False, "cancel not confirmed"

    def close_market(self, symbol, qty, order_side):
        """Close a position with a market order (legs must be cancelled
        first by the caller). Verifies by client_order_id on error."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        cid = (f"muse1-{date.today().isoformat()}-{symbol}-close-"
               f"{uuid.uuid4().hex[:8]}")
        req = MarketOrderRequest(
            symbol=symbol, qty=int(qty),
            side=OrderSide.BUY if order_side == 1 else OrderSide.SELL,
            time_in_force=TimeInForce.DAY, client_order_id=cid)
        try:
            order = _retry(lambda: self.client.submit_order(req),
                           "submit_order(close)", tries=2)
        except Exception as e:  # noqa: BLE001 - verified below
            try:
                existing = self.client.get_order_by_client_id(cid)
                if existing is not None:
                    return {"symbol": symbol, "qty": qty, "side": order_side,
                            "status": "SUBMITTED_PAPER (verified after error)",
                            "order_id": str(existing.id),
                            "client_order_id": cid}
            except Exception:
                pass
            raise RuntimeError(f"close order failed and not found for {cid}: {e}")
        return {"symbol": symbol, "qty": qty, "side": order_side,
                "status": "SUBMITTED_PAPER", "order_id": str(order.id),
                "client_order_id": cid}

# ---------------------------------------------------------------- SPY filter
# Owner rule 2026-11-05: if the S&P 500 is up, trade longs only;
# if it is down, trade shorts only.
# vs_open: SPY last close vs today's open.
#   For 5-min bars: today's session bars only (the first bar of the day
#   is the open — NOT bars[0] of a multi-day fetch). For daily bars:
#   the last COMPLETED daily bar's open vs close.
# sma20_regime: previous daily close vs rising/falling SMA20 (daily bars
#   only; falls back to vs_open on 5-min bars).

def _today_bars(bars, timeframe):
    if timeframe != "5min":
        return bars
    today = datetime.now(ET).date()
    tb = [b for b in bars
          if b.get("t") is not None
          and b["t"].astimezone(ET).date() == today]
    return tb if tb else bars

def spy_side(spy_bars, mode="vs_open", timeframe="5min"):
    if not spy_bars or len(spy_bars) < 2:
        return 0
    if mode == "sma20_regime":
        closes = [b["c"] for b in spy_bars]
        if len(closes) < 22:
            return spy_side(spy_bars, "vs_open", timeframe)
        sma20 = sum(closes[-20:]) / 20
        sma20_prev = sum(closes[-21:-1]) / 20
        prev_close = closes[-2]
        if prev_close > sma20 and sma20 > sma20_prev:
            return 1
        if prev_close < sma20 and sma20 < sma20_prev:
            return -1
        return 0
    # vs_open (default)
    bars = _today_bars(spy_bars, timeframe)
    if len(bars) < 2:
        return 0
    o = float(bars[0]["o"])
    c = float(bars[-1]["c"])
    if c > o:
        return 1
    if c < o:
        return -1
    return 0

def spy_flat_pct(spy_bars, timeframe="5min"):
    """|last close - today's open| / open, in percent. None if no bars."""
    if not spy_bars or len(spy_bars) < 2:
        return None
    bars = _today_bars(spy_bars, timeframe)
    if len(bars) < 2:
        return None
    o = float(bars[0]["o"])
    c = float(bars[-1]["c"])
    return abs(c - o) / o * 100.0 if o else None

def in_et_window(window="10:00-15:45", now=None):
    """True if `now` (default: now, US/Eastern) is inside window_et.
    Window is HH:MM-HH:MM Eastern, day/scalp sleeves only."""
    try:
        start_s, end_s = window.split("-")
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
    except Exception:
        return True
    now = now or datetime.now(ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    now_et = now.astimezone(ET)
    t = (now_et.hour, now_et.minute)
    return (sh, sm) <= t < (eh, em)

def _parse_hhmm(s):
    h, m = map(int, s.split(":"))
    return h, m

def available_equity(equity, open_positions):
    """Total AVAILABLE equity: account equity minus notional already
    committed to open positions. Sizing is computed on this, not on the
    full account equity, so a new trade does not size as if money already
    in other trades were still free."""
    committed = sum(abs(float(p.get("notional", 0.0))) for p in open_positions)
    return max(float(equity) - committed, 0.0)

# ---------------------------------------------------------------- virtual trail + stop
# Last night's logic (Trend Stock / Velez, as refined 2026-11-02):
# - Hard stop = -1R from the ACTUAL fill entry, where 1R is the sized
#   stop distance (signal entry to signal stop). If the fill is better
#   than the signal entry, the stop moves with it — the risk stays 1R,
#   it does not inflate because the fill was better.
# - Virtual stair trail: once the trade has moved +0.5R in your favour,
#   the stop trails the peak by 0.5R, stepping up every +0.5R
#   (trail = peak_R - 0.5R). No fixed target caps the runner.
# - For NEW positions the trail/stop are REAL broker orders (bracket
#   legs, replaced as the trail advances). The virtual math below is
#   still used to decide WHEN to move them, and to monitor the
#   pre-existing protected MSFT short, whose stop order is parent-placed
#   and must never be cancelled or replaced by this bot.
# For a short, "favourable" is price falling; peak = lowest price seen.

def virtual_stop_levels(entry, one_r, side, peak_price=None):
    """Returns (hard_stop_price, trail_stop_price or None).
    peak_price: most favourable price seen so far (low for shorts,
    high for longs). If the trade never reached +0.5R, trail is None."""
    if side == 1:  # long
        hard = entry - one_r
        if peak_price is None:
            return hard, None
        peak_r = (peak_price - entry) / one_r
        if peak_r < 0.5:
            return hard, None
        trail = peak_price - 0.5 * one_r
        return hard, max(trail, hard)
    else:  # short
        hard = entry + one_r
        if peak_price is None:
            return hard, None
        peak_r = (entry - peak_price) / one_r
        if peak_r < 0.5:
            return hard, None
        trail = peak_price + 0.5 * one_r
        return hard, min(trail, hard)

def virtual_exit_check(entry, one_r, side, current_price, peak_price=None):
    """Returns (should_exit, reason, hard_stop, trail_stop).
    Exit when price crosses the hard stop or the active trail."""
    hard, trail = virtual_stop_levels(entry, one_r, side, peak_price)
    if side == 1:
        if current_price <= hard:
            return True, f"hard stop hit at ${hard:.2f} (-1R)", hard, trail
        if trail is not None and current_price <= trail:
            return True, f"virtual trail hit at ${trail:.2f}", hard, trail
    else:
        if current_price >= hard:
            return True, f"hard stop hit at ${hard:.2f} (-1R)", hard, trail
        if trail is not None and current_price >= trail:
            return True, f"virtual trail hit at ${trail:.2f}", hard, trail
    return False, "holding", hard, trail

def swing_should_carry(bars, side):
    """Swing carryover: carry to the next day if warranted — price still
    on the right side of EMA20 and the trade has not hit stop/TP.
    Returns True to carry, False to exit at the next open."""
    from experts import ema
    closes = [float(b["c"]) for b in bars]
    e20 = ema(closes, 20)
    if e20 is None or not closes:
        return False
    last = closes[-1]
    return last > e20 if side == 1 else last < e20

# ---------------------------------------------------------------- data

def fetch_bars(symbol, timeframe, broker, use_real, cfg=None):
    """Returns (bars, source). Bars are dicts {t, o, h, l, c, v},
    oldest first, COMPLETED bars only (a bar whose interval has not
    finished yet is dropped). Real bars use feed=iex explicitly and a
    start/end window — never a bare limit=100 with no start, which
    returned stale bars in the past."""
    if not use_real:
        return synthetic_bars(symbol, timeframe=timeframe), "SYNTHETIC"
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.data.enums import DataFeed
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_SECRET_KEY")
    client = StockHistoricalDataClient(key, secret)
    data_cfg = (cfg or {}).get("data", {}) or {}
    now = datetime.now(timezone.utc)
    if timeframe == "5min":
        tf = TimeFrame(5, TimeFrameUnit.Minute)
        days = int(data_cfg.get("history_days_5min", 5))
        delta = timedelta(minutes=5)
    else:
        tf = TimeFrame(1, TimeFrameUnit.Day)
        days = int(data_cfg.get("history_days_daily", 90))
        delta = timedelta(days=1)
    start = now - timedelta(days=days)
    req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf,
                           start=start, end=now, limit=2000,
                           feed=DataFeed.IEX)
    df = _retry(lambda: client.get_stock_bars(req), f"get_stock_bars({symbol})").df
    out = []
    if df is not None and len(df):
        df = df.reset_index()
        for _, r in df.iterrows():
            ts = r["timestamp"]
            ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts + delta > now:
                continue  # incomplete bar — the current interval is not done
            out.append({"t": ts, "o": float(r["open"]), "h": float(r["high"]),
                        "l": float(r["low"]), "c": float(r["close"]),
                        "v": float(r["volume"])})
    out.sort(key=lambda b: b["t"])
    return out, "ALPACA"

# ---------------------------------------------------------------- state reconciliation
# Realized P&L is reconstructed from the BROKER's own order fills
# (alpaca-py 0.44.0 has no account-activities endpoint, so fills are
# read from closed orders). Only Muse1's tracked positions count toward
# the daily loss cap — other bots' orders on a shared account are not
# Muse1's P&L. Once the bot runs on its own paper account (item 6),
# every order on the account is Muse1's.

def _order_status(o):
    return str(getattr(o, "status", "")).upper()

def _filled(o):
    try:
        return float(getattr(o, "filled_qty", 0) or 0), float(getattr(o, "filled_avg_price", 0) or 0)
    except Exception:
        return 0.0, 0.0

def reconcile_state(state, broker_positions, broker, notes):
    """Sync state positions with the broker. Fills update entry/qty/1R;
    closed positions realize P&L from the closing order's fill and are
    removed; a position that vanished with no closing fill is flagged
    CLOSED-UNKNOWN (removed, NOT counted in day P&L). Returns nothing;
    mutates state and appends to notes."""
    for sym, pos in list(state.get("positions", {}).items()):
        bp = broker_positions.get(sym)
        if bp is not None:
            # Entry fill check (first run after submission).
            if pos.get("entry_order_id") and not pos.get("entry_filled"):
                try:
                    o = broker.get_order(pos["entry_order_id"])
                    fq, fa = _filled(o)
                    if "FILLED" in _order_status(o) and fq > 0 and fa > 0:
                        pos["entry"] = fa
                        pos["qty"] = int(fq)
                        pos["one_r"] = abs(fa - pos["stop_price"])
                        pos["entry_filled"] = True
                        notes.append(f"{sym}: entry filled {int(fq)} @ ${fa:.2f}")
                    elif any(s in _order_status(o) for s in ("CANCELED", "REJECTED", "EXPIRED")):
                        notes.append(f"{sym}: entry order {_order_status(o)} — dropping tracked position")
                        del state["positions"][sym]
                except Exception as e:  # noqa: BLE001 - noted, position kept
                    notes.append(f"{sym}: could not verify entry order: {e}")
            continue
        # Position is gone from the broker: find the closing fill.
        realized = None
        for key in ("target_order_id", "stop_order_id", "close_order_id"):
            oid = pos.get(key)
            if not oid:
                continue
            try:
                o = broker.get_order(oid)
            except Exception:
                continue
            fq, fa = _filled(o)
            if "FILLED" in _order_status(o) and fq > 0 and fa > 0:
                realized = (fa - pos["entry"]) * fq * pos["side"]
                notes.append(f"{sym}: closed via {key} @ ${fa:.2f}, realized ${realized:.2f}")
                break
        if realized is not None:
            state["day_pnl_dollars"] = round(state.get("day_pnl_dollars", 0.0) + realized, 2)
            risk_dollars = pos["one_r"] * pos["qty"]
            if risk_dollars > 0:
                state["day_pnl_r"] = round(state.get("day_pnl_r", 0.0) + realized / risk_dollars, 3)
            del state["positions"][sym]
        else:
            notes.append(f"{sym}: position gone from broker, no closing fill "
                         f"found — CLOSED-UNKNOWN, removed from tracking, NOT "
                         f"counted in day P&L")
            del state["positions"][sym]

def adopt_muse1_positions(state, broker_positions, broker, notes):
    """Safety net: if the state file was lost, adopt broker positions
    whose filled entry orders carry a muse1- client_order_id, so the bot
    does not trade against its own positions as if they did not exist."""
    for sym, bp in broker_positions.items():
        if sym in state["positions"]:
            continue
        try:
            orders = broker.closed_orders(limit=20)
        except Exception:
            return
        for o in orders:
            cid = str(getattr(o, "client_order_id", "") or "")
            if not cid.startswith("muse1-"):
                continue
            if str(getattr(o, "symbol", "")) != sym:
                continue
            fq, fa = _filled(o)
            if "FILLED" not in _order_status(o) or fq <= 0 or fa <= 0:
                continue
            qty = int(abs(float(getattr(bp, "qty", 0) or 0)))
            if qty <= 0 or qty != int(fq):
                continue
            side = 1 if str(getattr(bp, "side", "long")).lower().startswith("l") else -1
            state["positions"][sym] = {
                "symbol": sym, "side": side, "qty": qty, "entry": fa,
                "one_r": 0.0, "stop_price": 0.0, "target_price": None,
                "sleeve": "unknown", "expert": "adopted",
                "entry_order_id": str(o.id), "entry_filled": True,
                "peak": fa, "bars_held": 0, "adopted": True,
                "entry_time": datetime.now(ET).isoformat(),
            }
            notes.append(f"{sym}: adopted broker position into state "
                         f"(muse1 order {cid}, entry ${fa:.2f})")
            break

# ---------------------------------------------------------------- position management
# Runs BEFORE any new scan, every run. Enforces, for Muse1 positions:
# breakeven move (+1R), stair trail (replaces the stop leg), signal
# decay, max hold (scalp 30 min), swing 2R/EMA exits, and the 15:50 ET
# day flatten. PROTECTED pre-existing positions (the MSFT short) are
# monitored only — their orders are never cancelled or replaced.

def _parse_time(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def manage_positions(cfg, broker, state, dry_run, now_et, flatten_et, notes):
    """Returns a list of action strings. In dry-run mode nothing is
    submitted, replaced or cancelled — actions are reported as WOULD."""
    actions = []
    if not state.get("positions"):
        return actions
    overlay = cfg.get("hedge_fund_overlay", {}) or {}
    be_after = float(overlay.get("breakeven_after_r", 1.0))
    time_exit_bars = int(overlay.get("time_exit_bars", 12))
    fh, fm = _parse_hhmm(flatten_et)
    flatten_dt = now_et.replace(hour=fh, minute=fm, second=0, microsecond=0)
    try:
        broker_pos = broker.positions()
    except Exception as e:  # noqa: BLE001 - fail-closed: no management on bad read
        raise RuntimeError(f"manage_positions: broker positions read failed: {e}")

    def _close(sym, pos, reason):
        """Cancel the bracket legs, then close at market. In dry-run,
        report only. Returns True if a close was submitted (or would be)."""
        if dry_run:
            actions.append(f"{sym}: WOULD CLOSE ({reason})")
            return True
        # Cancel legs first so the close cannot double-trade with a leg.
        for key in ("stop_order_id", "target_order_id"):
            oid = pos.get(key)
            if not oid:
                continue
            ok, info = broker.cancel_order(oid)
            if not ok:
                actions.append(f"{sym}: ABORT close ({reason}) — could not "
                               f"cancel {key} {oid}: {info}")
                return False
        close_side = -pos["side"]  # order side that closes the position
        try:
            res = broker.close_market(sym, pos["qty"], close_side)
        except Exception as e:  # noqa: BLE001 - reported, position kept
            actions.append(f"{sym}: close order FAILED ({reason}): {e}")
            return False
        pos["close_order_id"] = res.get("order_id")
        pos["close_pending"] = True
        actions.append(f"{sym}: CLOSED ({reason}) — market order "
                       f"{res.get('order_id')} submitted, realized P&L on fill")
        return True

    def _move_stop(sym, pos, new_stop, reason):
        """Replace the stop leg. On replace failure, fall back to cancel +
        a new standalone stop order. Dry-run reports only."""
        if dry_run:
            actions.append(f"{sym}: WOULD move stop to ${new_stop:.2f} ({reason})")
            return True
        oid = pos.get("stop_order_id")
        if not oid:
            actions.append(f"{sym}: cannot move stop ({reason}) — no stop order id")
            return False
        ok, info = broker.replace_stop(oid, new_stop)
        if ok:
            pos["stop_price"] = round(float(new_stop), 2)
            actions.append(f"{sym}: stop moved to ${new_stop:.2f} ({reason})")
            return True
        # Fallback: cancel the leg, then place a standalone stop.
        ok2, info2 = broker.cancel_order(oid)
        if not ok2:
            actions.append(f"{sym}: stop move FAILED ({reason}) — replace "
                           f"failed ({info}) and cancel failed ({info2})")
            return False
        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req = StopOrderRequest(symbol=sym, qty=int(pos["qty"]),
                                   side=OrderSide.BUY if pos["side"] == -1 else OrderSide.SELL,
                                   time_in_force=TimeInForce.GTC,
                                   stop_price=round(float(new_stop), 2))
            order = broker.client.submit_order(req)
            pos["stop_order_id"] = str(order.id)
            pos["stop_price"] = round(float(new_stop), 2)
            actions.append(f"{sym}: stop moved to ${new_stop:.2f} via cancel + "
                           f"new stop order ({reason})")
            return True
        except Exception as e:  # noqa: BLE001 - reported, position kept
            actions.append(f"{sym}: stop move FAILED ({reason}) — new stop "
                           f"order failed: {e}. Position is UNPROTECTED.")
            return False

    for sym, pos in list(state["positions"].items()):
        bp = broker_pos.get(sym)
        if bp is None:
            continue  # reconciliation already handled the disappearance
        cur = float(getattr(bp, "current_price", pos["entry"]) or pos["entry"])
        entry, one_r, side = pos["entry"], pos["one_r"], pos["side"]
        if one_r <= 0:
            actions.append(f"{sym}: one_r is 0 (adopted position, no stop "
                           f"known) — management skipped, manual review needed")
            continue
        # Peak / bars held.
        peak = pos.get("peak", entry)
        peak = min(peak, cur) if side == -1 else max(peak, cur)
        pos["peak"] = peak
        peak_r = (entry - peak) / one_r if side == -1 else (peak - entry) / one_r
        pos["peak_r"] = round(peak_r, 3)
        et = _parse_time(pos.get("entry_time", "")) or now_et
        if et.tzinfo is None:
            et = et.replace(tzinfo=ET)
        minutes_held = max(0.0, (now_et - et.astimezone(ET)).total_seconds() / 60.0)
        pos["bars_held"] = int(minutes_held // 5) if pos.get("sleeve") != "swing" else int(minutes_held // (60 * 24))
        hard, trail = virtual_stop_levels(entry, one_r, side, peak)
        pos["trail_stop"] = trail

        # --- protected pre-existing position: monitor only, never touch.
        if pos.get("protected"):
            should_exit, reason, _, _ = virtual_exit_check(entry, one_r, side, cur, peak)
            if should_exit:
                actions.append(f"{sym} PROTECTED: {reason} at ${cur:.2f} — "
                               f"MANUAL ACTION REQUIRED (stop order is "
                               f"parent-placed; bot will not cancel/replace it)")
            else:
                actions.append(f"{sym} PROTECTED: holding, cur ${cur:.2f}, "
                               f"trail ${trail}, hard stop ${hard:.2f}, "
                               f"peak +{peak_r:.2f}R")
            continue

        # --- safety net: price past the LIVE stop leg and still open.
        live_stop = pos.get("stop_price")
        if live_stop and ((side == -1 and cur >= live_stop) or (side == 1 and cur <= live_stop)):
            _close(sym, pos, f"price ${cur:.2f} past live stop ${live_stop:.2f} (broker leg missed)")
            continue
        # --- flatten day/scalp at flatten_et.
        if pos.get("sleeve") in ("scalp", "day") and now_et >= flatten_dt:
            _close(sym, pos, f"flatten {flatten_et} ET")
            continue
        # --- max hold: scalp 30 min (config intended_hold_minutes).
        if pos.get("sleeve") == "scalp":
            max_hold = int(cfg["sleeves"]["scalp"].get("intended_hold_minutes", 30))
            if minutes_held > max_hold:
                _close(sym, pos, f"max hold {max_hold} min exceeded ({minutes_held:.0f} min)")
                continue
        # --- swing: max hold days + EMA20 break.
        if pos.get("sleeve") == "swing":
            max_days = int(cfg["sleeves"]["swing"].get("max_hold_days", 10))
            if minutes_held > max_days * 60 * 24:
                _close(sym, pos, f"swing max hold {max_days} days exceeded")
                continue
            try:
                dbars, _ = fetch_bars(sym, "daily", broker, True, cfg)
                if dbars and not swing_should_carry(dbars, side):
                    _close(sym, pos, "swing EMA20 break (price crossed to the wrong side)")
                    continue
            except Exception as e:  # noqa: BLE001 - EMA check skipped, other rules still run
                notes.append(f"{sym}: swing EMA check skipped: {e}")
        # --- signal decay: no progress past +0.5R in time_exit_bars bars.
        if peak_r < 0.5 and pos["bars_held"] >= time_exit_bars:
            _close(sym, pos, f"signal decay (no progress past +0.5R in {time_exit_bars} bars)")
            continue
        # --- breakeven move after +1R.
        if peak_r >= be_after and not pos.get("be_moved") and live_stop != entry:
            if _move_stop(sym, pos, entry, f"breakeven after +{peak_r:.2f}R"):
                pos["be_moved"] = True
        # --- stair trail: tighten the stop to the trail when it is better.
        if trail is not None and live_stop is not None:
            tighter = (side == -1 and trail < live_stop) or (side == 1 and trail > live_stop)
            if tighter:
                _move_stop(sym, pos, trail, f"stair trail (peak +{peak_r:.2f}R)")
    return actions

# ---------------------------------------------------------------- runner

def run_once(cfg, broker, dry_run, use_real, equity, state, now_et, flatten_et):
    """One scan over the watchlist, all three sleeves. Returns a report
    dict. Raises RuntimeError on a fail-closed broker read failure
    (main() maps that to exit 3 — the run is skipped, nothing traded,
    state untouched)."""
    # Strategy threshold from config (frozen).
    experts.MIN_REWARD_RISK = float((cfg.get("strategy", {}) or {}).get("min_reward_risk", 1.5))
    rollout = cfg.get("rollout", {}) or {}
    live_experts = set(rollout.get("live", []) or [])
    log_only_experts = set(rollout.get("log_only", []) or [])
    risk = RiskState(
        equity=equity,
        daily_loss_cap_pct=cfg["risk"]["daily_loss_cap_pct"],
        max_open_day_scalp=cfg["risk"]["max_open_day_scalp"],
        max_open_swing=cfg["risk"]["max_open_swing"],
        max_new_day_per_day=cfg["risk"]["max_new_day_per_day"],
    )
    sectors = cfg.get("sectors", {})
    overlay = cfg.get("hedge_fund_overlay", {})
    policy = exit_policy(tp_r=overlay.get("take_profit_r_day", 2.5),
                         breakeven_after_r=overlay.get("breakeven_after_r", 1.0))
    report = {"mode": "DRY_RUN" if dry_run else "PAPER",
              "data": "SYNTHETIC" if not use_real else "ALPACA",
              "policy": policy, "signals": [], "log_only_signals": [],
              "would_orders": [], "orders": [], "skipped": [],
              "management": [], "notes": []}
    notes = report["notes"]

    # --- 1. Broker reads (fail-closed: any failure skips the whole run).
    try:
        broker_positions = broker.positions()
        broker_open_orders = broker.open_orders()
        acct_equity = broker.equity() if use_real else equity
    except Exception as e:  # noqa: BLE001 - fail-closed
        raise RuntimeError(f"broker read failed ({e}) — run skipped, "
                           f"account NOT assumed empty")
    equity = float(acct_equity)
    report["account_equity"] = round(equity, 2)

    # --- 2. Reconcile state with the broker (fills, closes, realized P&L).
    reconcile_state(state, broker_positions, broker, notes)
    adopt_muse1_positions(state, broker_positions, broker, notes)
    open_positions = []  # for sizing + portfolio-limit checks
    for sym, pos in broker_positions.items():
        mv = float(getattr(pos, "market_value", 0.0) or 0.0)
        side = 1 if str(getattr(pos, "side", "long")).lower().startswith("l") else -1
        # Risk dollars come from the state record when the position is
        # tracked (otherwise the stress test would read $0 for it).
        tracked = state.get("positions", {}).get(sym, {})
        open_positions.append({"symbol": sym, "sector": sectors.get(sym, "unknown"),
                               "side": side, "notional": abs(mv),
                               "risk_dollars": round(tracked.get("qty", 0)
                                                   * tracked.get("one_r", 0.0), 2),
                               "source": "broker"})
    for sym, pos in state.get("positions", {}).items():
        if sym not in broker_positions:
            open_positions.append({"symbol": sym, "sector": sectors.get(sym, "unknown"),
                                   "side": pos["side"],
                                   "notional": abs(pos["qty"] * pos["entry"]),
                                   "risk_dollars": round(pos["qty"] * pos["one_r"], 2),
                                   "source": "state"})
    report["available_equity_start"] = round(available_equity(equity, open_positions), 2)
    report["state_day_pnl_dollars"] = round(state.get("day_pnl_dollars", 0.0), 2)
    report["state_day_pnl_r"] = round(state.get("day_pnl_r", 0.0), 3)

    # --- 3. Seed RiskState from persisted state; daily loss cap is REALIZED P&L.
    open_ds = sum(1 for p in state.get("positions", {}).values() if p.get("sleeve") in ("scalp", "day"))
    open_sw = sum(1 for p in state.get("positions", {}).values() if p.get("sleeve") == "swing")
    risk.seed(state.get("day_pnl_dollars", 0.0), state.get("trades_today", 0), open_ds, open_sw,
              day_pnl_r=state.get("day_pnl_r", 0.0))
    cap_hit = risk.cap_hit()
    if cap_hit:
        notes.append(f"daily loss cap hit (realized ${state.get('day_pnl_dollars', 0.0):.2f} "
                     f"<= -${risk.daily_loss_cap_dollars():.2f}) — no new trades")

    # --- 4. Manage existing positions (exits run even when the cap is hit).
    report["management"] = manage_positions(cfg, broker, state, dry_run, now_et, flatten_et, notes)

    # --- 5. New signals.
    spy_cfg = cfg.get("spy_filter", {})
    spy_enabled = bool(spy_cfg.get("enabled", False))
    spy_mode = spy_cfg.get("mode", "vs_open")
    if cap_hit:
        report["skipped"].append(("--", "all", "all", "daily loss cap hit — no new trades"))
    else:
        for sleeve in ("scalp", "day", "swing"):
            sleeve_cfg = cfg["sleeves"][sleeve]
            if not sleeve_cfg.get("enabled", True):
                continue
            if sleeve in ("scalp", "day"):
                window = sleeve_cfg.get("window_et", "10:00-15:45")
                if not in_et_window(window, now_et):
                    report["skipped"].append(
                        ("--", sleeve, "all",
                         f"outside window {window} ET, no new {sleeve} trades"))
                    continue
            timeframe = sleeve_cfg["timeframe"]
            risk_pct = sleeve_cfg["risk_pct"]
            spy = 0
            flat_mode = False
            range_experts = set(spy_cfg.get("range_experts", []) or [])
            if spy_enabled:
                spy_bars, _src = fetch_bars("SPY", timeframe, broker, use_real, cfg)
                spy = spy_side(spy_bars, spy_mode, timeframe)
                flat_pct = spy_flat_pct(spy_bars, timeframe)
                threshold = float(spy_cfg.get("flat_threshold_pct", 0.15))
                flat_mode = bool(
                    spy_cfg.get("flat_mode") == "range_scalp"
                    and flat_pct is not None and flat_pct < threshold)
                report.setdefault("spy_filter", {})[sleeve] = {
                    "mode": spy_mode,
                    "side": "range_only" if flat_mode else "long_only" if spy == 1 else "short_only" if spy == -1 else "both",
                    "flat_pct": round(flat_pct, 3) if flat_pct is not None else None,
                    "flat_mode": flat_mode,
                }
                if flat_mode and sleeve == "swing":
                    report["skipped"].append(
                        ("SPY", sleeve, "all",
                         f"spy_filter: SPY flat ({flat_pct:.3f}% < {threshold}%), "
                         f"range/scalp only, swing off"))
                    continue
                # Staleness warning: newest completed 5-min bar older than threshold.
                if timeframe == "5min" and spy_bars:
                    newest = spy_bars[-1].get("t")
                    if newest is not None:
                        age_min = (datetime.now(timezone.utc) - newest).total_seconds() / 60.0
                        stale_after = int((cfg.get("data", {}) or {}).get("stale_bar_minutes", 30))
                        if age_min > stale_after:
                            notes.append(f"STALE DATA: newest 5-min SPY bar is "
                                         f"{age_min:.0f} min old (>{stale_after} min) — "
                                         f"entries are priced off old bars")
            for symbol in cfg["watchlist"]:
                bars, source = fetch_bars(symbol, timeframe, broker, use_real, cfg)
                report["data"] = source
                if len(bars) < 51:
                    continue
                scan_bars = bars[-300:]  # experts need at most 51 bars
                signals = scan(scan_bars, sleeve_cfg["experts"])
                signals = combine_signals(signals)
                # Signal date for swing dedupe: last completed daily bar's date.
                signal_date = None
                if sleeve == "swing" and scan_bars:
                    t = scan_bars[-1].get("t")
                    signal_date = t.astimezone(ET).date().isoformat() if t else date.today().isoformat()
                for sig in signals:
                    # --- rollout gate: log-only, live, or off.
                    if sig["expert"] in live_experts:
                        pass  # trades for real
                    elif sig["expert"] in log_only_experts:
                        report["log_only_signals"].append(
                            {**sig, "symbol": symbol, "sleeve": sleeve})
                        continue
                    else:
                        report["skipped"].append(
                            (symbol, sleeve, sig["expert"],
                             "not in rollout (log-only phase: raschke_soup only)"))
                        continue
                    if flat_mode and sig["expert"] not in range_experts:
                        report["skipped"].append(
                            (symbol, sleeve, sig["expert"],
                             "spy_filter: SPY flat, range experts only "
                             f"({sig['expert']} is trend/breakout)"))
                        continue
                    if spy_enabled and not flat_mode and spy != 0 and sig["side"] != spy:
                        report["skipped"].append(
                            (symbol, sleeve, sig["expert"],
                             f"spy_filter: SPY {'up' if spy == 1 else 'down'} "
                             f"({spy_mode}), {'longs' if spy == 1 else 'shorts'} only"))
                        continue
                    # --- no duplicates: already holding this stock (Muse1),
                    # or an open order pending for it.
                    if symbol in state.get("positions", {}):
                        report["skipped"].append(
                            (symbol, sleeve, sig["expert"],
                             "already holds this stock (Muse1 position)"))
                        continue
                    if any(str(getattr(o, "symbol", "")) == symbol for o in broker_open_orders):
                        report["skipped"].append(
                            (symbol, sleeve, sig["expert"],
                             "open order already pending for this stock"))
                        continue
                    # --- shortable check (fail-closed for shorts).
                    if sig["side"] == -1 and not broker.shortable(symbol):
                        report["skipped"].append(
                            (symbol, sleeve, sig["expert"],
                             "not shortable (or could not verify) — no short"))
                        continue
                    # --- swing dedupe: one entry per signal.
                    if sleeve == "swing":
                        key = f"{symbol}:{sig['expert']}:{signal_date}"
                        if key in state.get("swing_signals_seen", {}):
                            report["skipped"].append(
                                (symbol, sleeve, sig["expert"],
                                 f"swing signal already acted on ({signal_date})"))
                            continue
                    entry = sig["entry"]
                    stop_dist = abs(entry - sig["stop"])
                    if stop_dist <= 0:
                        continue
                    # Sizing on AVAILABLE equity, not full account equity.
                    avail = available_equity(equity, open_positions)
                    qty, notional = vol_scaled_size(
                        avail, risk_pct, stop_dist, entry,
                        max_notional_pct=cfg["risk"]["max_notional_pct"])
                    if qty <= 0:
                        report["skipped"].append((symbol, sleeve, sig["expert"], "size 0 after vol scaling"))
                        continue
                    # Costs on the ACTUAL risk dollars (qty x stop distance
                    # after the notional cap), not the intended risk.
                    risk_dollars = qty * stop_dist
                    trade_cost = costs(entry, qty,
                                       bps_per_side=overlay.get("costs_bps_per_side", 3.0),
                                       sec_fee_bps=overlay.get("sec_fee_bps", 0.1))
                    round_trip = trade_cost * 2
                    if round_trip >= risk_dollars:
                        report["skipped"].append(
                            (symbol, sleeve, sig["expert"],
                             f"round-trip cost ${round_trip:.2f} >= actual 1R risk ${risk_dollars:.2f}"))
                        continue
                    tp_r = sig["tp_r"] if sig["tp_r"] is not None else policy["tp_r"]
                    if tp_r is not None and tp_r * risk_dollars - round_trip <= 0:
                        report["skipped"].append(
                            (symbol, sleeve, sig["expert"],
                             "no edge after costs at target"))
                        continue
                    # Target price for the bracket leg (None = no fixed TP;
                    # the position then runs on stop / trail / decay only).
                    if tp_r is None:
                        target_price = None
                    elif sig["side"] == 1:
                        target_price = entry + tp_r * stop_dist
                    else:
                        target_price = entry - tp_r * stop_dist
                    sector = sectors.get(symbol, "unknown")
                    ok, reason = check_portfolio_limits(
                        open_positions, symbol, sector, sig["side"], sectors,
                        max_open=cfg["risk"]["max_open_day_scalp"] if sleeve != "swing" else cfg["risk"]["max_open_swing"],
                        max_per_sector=cfg["risk"]["max_per_sector"],
                        max_net_exposure_pct=cfg["risk"]["max_net_exposure_pct"],
                        equity=equity, new_notional=notional)
                    if not ok:
                        report["skipped"].append((symbol, sleeve, sig["expert"], reason))
                        continue
                    # Hard total cap: up to 5 open trades at once, all sleeves.
                    total_cap = int(cfg["risk"].get("max_open_total", 5))
                    if len(open_positions) >= total_cap:
                        report["skipped"].append((symbol, sleeve, sig["expert"], f"total max open {total_cap} reached"))
                        continue
                    ok, reason = risk.can_open(sleeve)
                    if not ok:
                        report["skipped"].append((symbol, sleeve, sig["expert"], reason))
                        continue
                    sig_out = {**sig, "symbol": symbol, "sleeve": sleeve,
                               "qty": qty, "notional": round(notional, 2),
                               "available_equity": round(avail, 2),
                               "cost_one_side": round(trade_cost, 2),
                               "risk_dollars": round(risk_dollars, 2),
                               "tp_r": tp_r, "target_price": target_price}
                    if sleeve == "swing":
                        sig_out["carryover"] = swing_should_carry(scan_bars, sig["side"])
                    report["signals"].append(sig_out)
                    risk.record_open(sleeve)
                    open_positions.append({"symbol": symbol, "sector": sector,
                                           "side": sig["side"], "notional": notional,
                                           "risk_dollars": round(risk_dollars, 2)})
                    if dry_run or not use_real:
                        placed = broker.submit_bracket(
                            symbol, sig["side"], qty, sig["stop"],
                            target_price if target_price is not None else entry,
                            sleeve, sig["expert"])
                        report["would_orders"].append(placed)
                    else:
                        placed = broker.submit_bracket(
                            symbol, sig["side"], qty, sig["stop"],
                            target_price if target_price is not None else entry,
                            sleeve, sig["expert"],
                            tif="GTC" if sleeve == "swing" else "DAY")
                        report["orders"].append(placed)
                        # Track the new position in state (paper only).
                        state["positions"][symbol] = {
                            "symbol": symbol, "side": sig["side"], "qty": qty,
                            "entry": entry, "one_r": stop_dist,
                            "stop_price": sig["stop"], "target_price": target_price,
                            "sleeve": sleeve, "expert": sig["expert"],
                            "entry_order_id": placed.get("order_id"),
                            "stop_order_id": placed.get("stop_order_id"),
                            "target_order_id": placed.get("target_order_id"),
                            "client_order_id": placed.get("client_order_id"),
                            "entry_filled": False, "peak": entry, "peak_r": 0.0,
                            "bars_held": 0, "be_moved": False,
                            "entry_time": now_et.isoformat(),
                        }
                        state["trades_today"] = state.get("trades_today", 0) + 1
                        if sleeve == "swing":
                            state.setdefault("swing_signals_seen", {})[
                                f"{symbol}:{sig['expert']}:{signal_date}"] = True
    worst, worst_pct = stress_test(open_positions, equity)
    report["risk"] = risk.summary()
    report["stress_worst_case_loss"] = round(worst, 2)
    report["stress_worst_case_pct"] = round(worst_pct, 2)
    return report

def main(argv=None):
    p = argparse.ArgumentParser(
        description="Stock bot: top-10 experts + hedge-fund overlay. PAPER ONLY.")
    p.add_argument("--paper", action="store_true", help="paper trading (keys via env/.env)")
    p.add_argument("--dry-run", action="store_true", help="preview: real Alpaca bars if keys are present, synthetic demo bars if not. Places no orders.")
    p.add_argument("--live", action="store_true", help="BLOCKED: live trading is never enabled")
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument("--equity", type=float, default=100000.0, help="paper equity for sizing when no keys (default 100000)")
    args = p.parse_args(argv)

    if args.live:
        print("LIVE TRADING IS BLOCKED for this bot. Use --paper or --dry-run. "
              "This bot is unvalidated and paper-only.", file=sys.stderr)
        return 2

    # No flag = do nothing. --paper is required to trade.
    if not args.paper and not args.dry_run:
        print(f"{BOT_NAME} {VERSION}")
        print("No --paper or --dry-run given: doing nothing (no scan, no orders).")
        print("  --dry-run   preview with real Alpaca bars (or synthetic if no keys)")
        print("  --paper     trade paper (keys via env/.env)")
        print("  --live      always refused (exit 2)")
        return 0

    load_dotenv()
    cfg = load_config(args.config)
    if cfg.get("allow_live"):
        print("config allow_live must stay false", file=sys.stderr)
        return 2

    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_SECRET_KEY")
    use_real = bool(key and secret)
    dry = args.dry_run
    if args.paper and not dry and not use_real:
        print("No Alpaca keys found (ALPACA_API_KEY / ALPACA_API_SECRET). "
              "--paper cannot trade without keys. Use --dry-run to preview "
              "with synthetic demo bars.", file=sys.stderr)
        return 0
    if dry and not use_real:
        print("DRY RUN — no orders will be placed. Data: SYNTHETIC demo bars (no keys).")
    elif dry:
        print("DRY RUN — no orders will be placed. Data: REAL Alpaca bars (feed=iex).")
    else:
        print("PAPER TRADING — real bracket orders will be submitted to the Alpaca paper account.")

    broker = AlpacaBroker(key, secret) if use_real else PaperBrokerStub(args.equity)
    equity = broker.equity() if use_real else args.equity
    now_et = datetime.now(ET)

    # Market calendar (real runs only): skip holidays, early-close flatten.
    flatten_et = str(cfg["sleeves"]["day"].get("flatten_et", "15:50"))
    if use_real:
        try:
            status, flat, source = market_status(broker, now_et.date())
        except Exception as e:  # noqa: BLE001 - calendar failure is not fatal
            status, flat, source = "open", None, f"calendar error ({e}), assuming open"
        print(f"market calendar [{source}]: {status}"
              + (f", early close, flatten {flat} ET" if status == "half_day" else ""))
        if status == "holiday":
            print("Market closed today (holiday/weekend) — doing nothing.")
            return 0
        if status == "half_day" and flat:
            flatten_et = flat

    state, corrupt = load_state()
    if corrupt and not dry:
        print("state.json is corrupt or unreadable — refusing to trade. "
              "Fix or delete it (it will be recreated) and re-run.", file=sys.stderr)
        return 3
    if corrupt:
        print("WARNING: state.json is corrupt — dry run proceeds without it.")
        state = default_state()
    state = rollover_state(state)

    print(f"{BOT_NAME} {VERSION}")
    print(f"mode={'DRY_RUN' if dry else 'PAPER'} data={'ALPACA' if use_real else 'SYNTHETIC'} "
          f"equity=${equity:,.0f} time={datetime.now(timezone.utc).isoformat()} "
          f"flatten={flatten_et} ET")

    def _execute():
        report = run_once(cfg, broker, dry_run=dry, use_real=use_real,
                          equity=equity, state=state, now_et=now_et,
                          flatten_et=flatten_et)
        if not dry:
            save_state(state)
        return report

    if dry:
        try:
            report = _execute()
        except RuntimeError as e:
            print(f"FATAL (dry run): {e}", file=sys.stderr)
            return 3
    else:
        with run_lock() as fd:
            if fd is None:
                print("Another run holds the lock — skipping this run (no trades, no state change).")
                return 0
            try:
                report = _execute()
            except RuntimeError as e:
                print(f"FATAL: {e}", file=sys.stderr)
                return 3

    if report.get("spy_filter"):
        for sleeve, f in report["spy_filter"].items():
            flat = f" FLAT {f.get('flat_pct')}% -> range/scalp only" if f.get("flat_mode") else ""
            print(f"SPY filter [{sleeve}]: mode={f['mode']} side={f['side']}{flat}")
    for n in report.get("notes", []):
        print(f"  note: {n}")
    for a in report.get("management", []):
        print(f"  manage: {a}")
    print(f"signals found: {len(report['signals'])} "
          f"(log-only: {len(report['log_only_signals'])})")
    for s in report["signals"]:
        side = "LONG" if s["side"] == 1 else "SHORT"
        print(f"  [{s['sleeve']}] {s['symbol']} {side} {s['expert']} "
              f"entry ${s['entry']:.2f} stop ${s['stop']:.2f} qty {s['qty']} "
              f"notional ${s['notional']:,.0f} tp {s['tp_r']}R exit {s['exit']} "
              f"risk ${s['risk_dollars']:.2f} ({s['reason']})")
    for s in report["log_only_signals"]:
        side = "LONG" if s["side"] == 1 else "SHORT"
        print(f"  LOG-ONLY [{s['sleeve']}] {s['symbol']} {side} {s['expert']} "
              f"entry ${s['entry']:.2f} stop ${s['stop']:.2f} ({s['reason']})")
    for o in report["would_orders"]:
        print(f"  WOULD PLACE BRACKET (no order): {o['sleeve']} {o['symbol']} "
              f"{'LONG' if o['side'] == 1 else 'SHORT'} x{o['qty']} "
              f"stop ${o['stop_price']:.2f} target ${o['target_price']:.2f} "
              f"via {o['expert']} — {o['status']}")
    for o in report["orders"]:
        print(f"  PAPER BRACKET ORDER: {o}")
    for sk in report["skipped"]:
        print(f"  skipped: {sk[0]} {sk[1]} {sk[2]} — {sk[3]}")
    print(f"risk: {report['risk']}")
    print(f"stress test (all stops hit at once): -${report['stress_worst_case_loss']:,.2f} "
          f"({report['stress_worst_case_pct']:.2f}% of equity)")
    print("Done. No live trading. This bot is UNVALIDATED — backtest + paper forward test required before any live use.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
