"""Execution safety layer. The same Strategy.signals() that the backtester uses decides what to do live, so the math is written once.

Patterns (each has a test):
  spread_ok          halt a market order when the live bid-ask spread is wider than a threshold
  CircuitBreaker     equity drawdown on the day beyond a limit -> flatten everything and lock new entries for 24 hours
  stale_positions    time-based exit: positions that have reached neither target nor stop within N minutes are closed
  position_size      volatility-normalized sizing: shares = risk dollars / stop distance, capped by notional
  EntryWindow        no entries in the opening minutes (spread anomalies), none near the close; flat before the bell
  retry_call         exponential backoff with jitter on rate limits (429), 5xx and network errors, then a clean failure
  setup_logging      size-capped rotating log files so a daemon can run for months without filling the disk
  live_decision      the signal (if any) that the latest completed bar produces, via the SAME strategy object as the backtest

PAPER ONLY: AlpacaBroker always builds the client with paper=True. There is no switch for live trading in this package.
"""

import logging
import logging.handlers
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

log = logging.getLogger("alpacabot")


# ------------------------------------------------------------------ logging
def setup_logging(path: str | Path, max_bytes: int = 10_000_000, backups: int = 5, level: int = logging.INFO) -> logging.Logger:
    """Rotating file log: at most (backups + 1) x max_bytes on disk (default 60 MB)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(level)
    log.handlers = [h for h in log.handlers if not isinstance(h, logging.handlers.RotatingFileHandler)] + [handler]
    return log


# ------------------------------------------------------------------ retries
class RetryableError(Exception):
    pass


def _retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    text = str(exc).lower()
    return isinstance(exc, (ConnectionError, TimeoutError, RetryableError)) or any(k in text for k in ("429", "rate limit", "timed out", "connection"))


def retry_call(fn, *args, retries: int = 4, base_delay: float = 0.5, max_delay: float = 20.0, sleep=time.sleep, **kwargs):
    """Call fn; on a retryable failure wait base*2^n (+ jitter) and try again, up to `retries` retries. Non-retryable errors raise at once."""
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if attempt == retries or not _retryable(exc):
                raise
            delay = min(max_delay, base_delay * 2 ** attempt) * (0.75 + 0.5 * random.random())
            log.warning("retry %d/%d after %.1fs: %r", attempt + 1, retries, delay, exc)
            sleep(delay)


# ------------------------------------------------------------------ pre-trade checks
def spread_ok(bid: float, ask: float, max_abs: float = 0.05, max_pct: float | None = None) -> bool:
    """True when the quote is sane and tight. Stocks: max_abs dollars (default 5 cents). Crypto: pass max_pct (e.g. 0.0005 = 0.05%)."""
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return False
    spread = ask - bid
    if max_pct is not None:
        return spread / ((ask + bid) / 2) <= max_pct
    return spread <= max_abs


def position_size(equity: float, risk_pct: float, entry: float, stop_dist: float, max_pos_frac: float = 0.25) -> int:
    """Volatility-normalized: the same dollars at risk on every trade; wide (volatile) stops get fewer shares."""
    if stop_dist <= 0 or entry <= 0:
        return 0
    return max(int(min(equity * risk_pct / stop_dist, equity * max_pos_frac / entry)), 0)


@dataclass
class EntryWindow:
    """Regular-hours session rules (Eastern time)."""
    no_entry_before: dtime = dtime(9, 45)
    no_entry_after: dtime = dtime(15, 35)     # the last decision bar is 15:30-15:35 (its close is when the order goes in), as in the backtest
    flatten_at: dtime = dtime(15, 50)          # day trades and scalps are flat by 3:50 pm ET; only swing trades hold overnight

    session_close: datetime | None = None    # today's real close from Alpaca's clock (early-close days: 1:00 pm)

    def _early(self, now_et: datetime):
        c = self.session_close
        return c if c is not None and c.date() == now_et.date() and c.time() < dtime(16, 0) else None

    def entries_allowed(self, now_et: datetime) -> bool:
        c = self._early(now_et)
        if c is not None and now_et >= c - timedelta(minutes=25):          # same 25-minute gap as 3:35 -> 4:00
            return False
        return now_et.weekday() < 5 and self.no_entry_before <= now_et.time() <= self.no_entry_after

    def must_flatten(self, now_et: datetime) -> bool:
        c = self._early(now_et)
        if c is not None and now_et >= c - timedelta(minutes=10):          # same 10 minutes as 3:50 -> 4:00
            return True
        return now_et.time() >= self.flatten_at


@dataclass
class CircuitBreaker:
    """Peak-to-trough guard for the day: below -limit of the day's opening equity -> flatten and lock new entries for lock_hours."""
    limit: float = 0.02
    lock_hours: float = 24.0
    day: object = None
    day_start_equity: float = 0.0
    locked_until: datetime | None = None

    def update(self, equity: float, now: datetime) -> bool:
        """Feed the live equity; returns True when the breaker trips on this call (the caller must flatten)."""
        if self.day != now.date():
            self.day, self.day_start_equity = now.date(), equity
        if self.locked_until and now < self.locked_until:
            return False
        if self.day_start_equity > 0 and equity <= self.day_start_equity * (1 - self.limit):
            self.locked_until = now + timedelta(hours=self.lock_hours)
            return True
        return False

    def entries_allowed(self, now: datetime) -> bool:
        return not (self.locked_until and now < self.locked_until)


@dataclass
class ProfitLock:
    """Keeps a good day from being handed back. Once the day's gain has reached arm (fraction of the day's opening equity), a fall to
    keep x the best gain seen today flattens everything and stops new entries until tomorrow (e.g. up $400 at the high, back to +$200:
    flatten, the day ends +$200). A risk rule chosen by the owner, not a backtested edge: it also cuts days that dip and recover."""
    arm: float = 0.005
    keep: float = 0.5
    day: object = None
    start: float = 0.0
    peak_gain: float = 0.0
    locked_day: object = None

    def update(self, equity: float, now: datetime) -> bool:
        if self.day != now.date():
            self.day, self.start, self.peak_gain = now.date(), equity, 0.0
        if self.locked_day == now.date() or self.start <= 0:
            return False
        gain = equity - self.start
        self.peak_gain = max(self.peak_gain, gain)
        if self.peak_gain >= self.arm * self.start and gain <= self.keep * self.peak_gain:
            self.locked_day = now.date()
            return True
        return False

    def entries_allowed(self, now: datetime) -> bool:
        return self.locked_day != now.date()


def stale_positions(open_positions: dict[str, datetime], now: datetime, max_minutes: float = 45.0) -> list[str]:
    """Symbols held longer than max_minutes: the trade has neither hit its target nor its stop, so free the capital."""
    return [s for s, t0 in open_positions.items() if (now - t0).total_seconds() >= max_minutes * 60]


def live_decision(strategy, bars_so_far):
    """Backtest-to-live mapping: run the backtest's own strategy on today's completed bars and return the signal made by the LATEST bar."""
    n = len(bars_so_far)
    for s in strategy.signals(bars_so_far):
        if s.i == n - 1:
            return s
    return None


# ------------------------------------------------------------------ executor (broker injected, so it is testable offline)
@dataclass
class Executor:
    broker: object                      # needs: equity(), quote(sym)->(bid,ask), submit_bracket(sym, qty, stop, target), close(sym), flatten_all()
    window: EntryWindow = field(default_factory=EntryWindow)
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    profit_lock: ProfitLock | None = None
    risk_pct: float = 0.005
    max_pos_frac: float = 0.25
    max_spread: float = 0.05
    max_spread_pct: float | None = None       # when set, the guard is relative (fraction of price) instead of absolute dollars
    max_positions: int = 4
    stale_minutes: float = 45.0
    sleep: object = time.sleep
    opened: dict = field(default_factory=dict)      # symbol -> entry time
    kill_switch: bool = False
    last_qty: int = 0
    last_order: object = None                 # the broker's reply to the last entry order (id, status, fill), when it gives one
    sent_ids: set = field(default_factory=set)      # client order ids already sent (a restart or a repeated tick cannot double-enter)

    def submit(self, symbol, qty, stop, target, coid, side=1):
        """Brackets when the broker supports them (longs only); plain market orders (virtual exits) otherwise. side -1 = sell short."""
        if hasattr(self.broker, "submit_bracket") and side == 1:
            return self.broker.submit_bracket(symbol, qty, stop, target, coid)
        if side == -1:
            return self.broker.submit_market(symbol, qty, coid, side="sell")
        return self.broker.submit_market(symbol, qty, coid)

    def on_tick(self, now_et: datetime) -> list[str]:
        """Housekeeping: breaker, flatten at the close, stale exits. Returns what it did."""
        done = []
        equity = retry_call(self.broker.equity, sleep=self.sleep)
        if self.breaker.update(equity, now_et):
            retry_call(self.broker.flatten_all, sleep=self.sleep)
            self.opened.clear()
            return ["breaker: flattened"]
        if self.profit_lock and self.profit_lock.update(equity, now_et):
            retry_call(self.broker.flatten_all, sleep=self.sleep)
            self.opened.clear()
            return ["profit lock: flattened"]
        if self.window.must_flatten(now_et) and self.opened:
            retry_call(self.broker.flatten_all, sleep=self.sleep)
            self.opened.clear()
            return ["flattened for the close"]
        for sym in stale_positions(self.opened, now_et, self.stale_minutes):
            retry_call(self.broker.close, sym, sleep=self.sleep)
            self.opened.pop(sym, None)
            done.append(f"stale exit {sym}")
        return done

    def try_enter(self, symbol: str, signal, entry_ref: float, now_et: datetime) -> str:
        """Returns 'ordered' or the reason the order was not placed."""
        if self.kill_switch:
            return "kill switch"
        if not self.breaker.entries_allowed(now_et):
            return "breaker lock"
        if self.profit_lock and not self.profit_lock.entries_allowed(now_et):
            return "profit lock"
        if not self.window.entries_allowed(now_et):
            return "outside entry window"
        coid = f"ab-{symbol}-{now_et:%Y%m%d}-{signal.i}"
        if coid in self.sent_ids:
            return "duplicate order id"
        if symbol in self.opened or len(self.opened) >= self.max_positions:
            return "position limit"
        bid, ask = retry_call(self.broker.quote, symbol, sleep=self.sleep)
        if not spread_ok(bid, ask, self.max_spread, self.max_spread_pct):
            return f"spread too wide ({ask - bid:.3f})"
        equity = retry_call(self.broker.equity, sleep=self.sleep)
        qty = position_size(equity, self.risk_pct, entry_ref, signal.stop_dist, self.max_pos_frac)
        if qty < 1:
            return "size < 1 share"
        side = -1 if signal.side == -1 else 1
        stop = round(entry_ref - side * signal.stop_dist, 2)
        target = round(entry_ref + side * signal.target_r * signal.stop_dist, 2) if signal.target_r > 0 else None
        self.sent_ids.add(coid)
        self.last_order = retry_call(self.submit, symbol, qty, stop, target, coid, side, sleep=self.sleep)
        self.opened[symbol] = now_et
        self.last_qty = qty
        log.info("ordered %s %s x%d stop %.2f target %s", "SHORT" if side == -1 else "LONG", symbol, qty, stop, target)
        return "ordered"


class AlpacaBroker:
    """Thin adapter over alpaca-py. Paper account only: the client is always created with paper=True."""

    def __init__(self, key: str, secret: str):
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient
        self.trading = TradingClient(key, secret, paper=True)       # no live switch exists on purpose
        self.data = StockHistoricalDataClient(key, secret)

    def equity(self) -> float:
        return float(self.trading.get_account().equity)

    def quote(self, symbol: str):
        from alpaca.data.requests import StockLatestQuoteRequest
        q = self.data.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=symbol))[symbol]
        return float(q.bid_price), float(q.ask_price)

    def submit_bracket(self, symbol: str, qty: int, stop: float, target: float | None, client_order_id: str | None = None):
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest
        req = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY, client_order_id=client_order_id,
                                 order_class=OrderClass.BRACKET if target else OrderClass.OTO,
                                 stop_loss=StopLossRequest(stop_price=stop),
                                 **({"take_profit": TakeProfitRequest(limit_price=target)} if target else {}))
        return self.trading.submit_order(order_data=req)

    def submit_market(self, symbol: str, qty: int):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        return self.trading.submit_order(order_data=MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY))

    def close(self, symbol: str):
        return self.trading.close_position(symbol)

    def flatten_all(self):
        self.trading.cancel_orders()
        return self.trading.close_all_positions(cancel_orders=True)


# ------------------------------------------------------------------ virtual exits (live twin of the backtest's managed exit)
class VirtualExitManager:
    """Tracks stop/target/high-water mark in memory AND on disk. Entries are plain market orders; nothing rests at the exchange, so a bracket
    rejection (HTTP 422 on a fast tape) cannot happen. poll() is meant to run every ~15 seconds with the live quote."""

    def __init__(self, store, broker=None, sleep=time.sleep):
        self.store, self.broker, self.sleep = store, broker, sleep
        self.positions = store.load()          # restart-safe: open-trade parameters survive a reboot

    def open(self, symbol: str, qty: int, entry: float, atr: float, adx: float, now: datetime, trail: float = 1.5, tp_scale: float = 1.0,
             side: int = 1):
        """side -1: a short, tracked on the mirrored price (-price), so the same ExitState code trails it from the LOW."""
        from .exits import ExitState
        self.positions[symbol] = {"state": ExitState.open(side * entry, atr, adx, trail, tp_scale), "qty": qty, "opened": now.isoformat(),
                                  "side": side}
        self.store.save(self.positions)

    def on_quote(self, symbol: str, bid: float, ask: float) -> str | None:
        """Feed one quote. Longs exit at the bid, shorts buy back at the ask. Returns the exit reason when a level was crossed."""
        pos = self.positions.get(symbol)
        side = pos.get("side", 1) if pos else 1
        if not pos or not bid or bid <= 0 or (side == -1 and (not ask or ask <= 0)):
            return None
        st = pos["state"]
        px = bid if side == 1 else -ask
        hit = st.check(px, px, px)
        if hit:
            if self.broker is not None:
                retry_call(self.broker.close, symbol, sleep=self.sleep)
            self.positions.pop(symbol)
            self.store.save(self.positions)
            log.info("virtual %s exit %s %s at %.2f (level crossed)", hit[0], "short" if side == -1 else "long", symbol, abs(px))
            return hit[0]
        before = (st.stop, getattr(st, "hwm", None))
        st.ratchet(px)
        if (st.stop, getattr(st, "hwm", None)) != before:
            self.store.save(self.positions)
        return None

    def open_signal(self, symbol: str, qty: int, fill: float, sig, now: datetime, entry_bar: str):
        """Open with the exact exit plan the backtest uses for this signal: the trailing manager for managed signals, otherwise the
        expert's fixed stop and R-multiple target; plus its time stop (max_bars) and indicator exit (exit_col), checked at each bar close."""
        from .exits import ExitState, FixedState
        side = -1 if sig.side == -1 else 1
        e = side * fill                                  # shorts are tracked on -price
        if sig.managed:
            st = ExitState.open(e, sig.atr, sig.adx, sig.trail, sig.tp_scale)
        else:
            st = FixedState(e, e - sig.stop_dist, e + sig.target_r * sig.stop_dist if sig.target_r > 0 else float("inf"), sig.stop_dist, 1.0)
        self.positions[symbol] = {"state": st, "qty": qty, "opened": now.isoformat(), "side": side, "max_bars": int(sig.max_bars),
                                  "exit_col": sig.exit_col, "entry_bar": entry_bar, "tag": sig.tag}
        self.store.save(self.positions)

    def opened_at(self) -> dict[str, datetime]:
        return {s: datetime.fromisoformat(p["opened"]) for s, p in self.positions.items()}
