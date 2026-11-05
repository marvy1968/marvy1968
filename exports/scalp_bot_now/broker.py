"""Thin Alpaca Trading API wrapper (alpaca-py) with retry and a stop-modification fallback ladder."""
import logging
import random
import time
import uuid

from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (GetOrdersRequest, LimitOrderRequest, MarketOrderRequest,
                                     ReplaceOrderRequest, StopLossRequest, StopOrderRequest,
                                     TakeProfitRequest)

import config
from data import make_data_client

log = logging.getLogger("scalpbot.broker")
OPEN_STATUSES = {"new", "accepted", "held", "partially_filled", "pending_new", "pending_replace",
                 "accepted_for_bidding", "pending_cancel"}
PREFIX = "SB"
TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}

# move_stop() outcomes
REPLACED = "replaced"        # stop leg modified in place, target still live
OCO = "oco"                  # legs cancelled, new OCO (stop + target) live
STOP_ONLY = "stop_only"      # only a plain stop is live; the bot must watch the target itself
FLATTENED = "flattened"      # could not protect -> position closed at market
BREACHED = "breached"        # price already through the new stop -> caller should exit
GONE = "gone"                # no position any more (bracket leg already filled)
FAILED = "failed"            # nothing worked; old orders may still be live, retry next tick


def _val(x):
    return str(getattr(x, "value", x)).lower()


def is_transient(exc):
    """Rate limits, 5xx and network errors are worth retrying; 4xx business rejections are not."""
    code = getattr(exc, "status_code", None)
    if code is None:
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
    if code is not None:
        try:
            return int(code) in TRANSIENT_HTTP
        except (TypeError, ValueError):
            return False
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def call(fn, *args, retries=3, base_delay=0.5, what="api call", **kwargs):
    """Retry transient failures with exponential backoff + jitter; re-raise permanent ones at once."""
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:   # noqa: BLE001
            if attempt == retries or not is_transient(e):
                raise
            delay = base_delay * (2 ** attempt) * (0.8 + 0.4 * random.random())
            log.warning("%s transient error (%s) - retry %d/%d in %.1fs", what, e, attempt + 1, retries, delay)
            time.sleep(delay)


class Broker:
    def __init__(self):
        self.tc = TradingClient(config.API_KEY, config.API_SECRET, paper=config.PAPER)
        self.dc = make_data_client()
        self._assets = {}

    # ---------- account ----------
    def account(self):
        return call(self.tc.get_account, what="get_account")

    def clock(self):
        return call(self.tc.get_clock, what="get_clock")

    def positions(self):
        out = {}
        for p in call(self.tc.get_all_positions, what="get_positions"):
            q = abs(float(p.qty))
            out[p.symbol] = (q if _val(p.side) == "long" else -q, float(p.avg_entry_price))
        return out

    def _position(self, sym):
        """(signed qty, qty_available) or None if flat."""
        try:
            p = call(self.tc.get_open_position, sym, what=f"get_position {sym}")
        except Exception as e:   # noqa: BLE001
            if getattr(e, "status_code", None) == 404 or "position does not exist" in str(e).lower():
                return None
            raise
        q = abs(float(p.qty))
        avail = abs(float(getattr(p, "qty_available", None) or p.qty))
        return (q if _val(p.side) == "long" else -q, avail)

    # ---------- eligibility ----------
    def can_trade(self, sym, side, acct):
        a = self._assets.get(sym)
        if a is None:
            try:
                a = call(self.tc.get_asset, sym, what=f"get_asset {sym}")
            except Exception as e:   # noqa: BLE001
                log.warning("asset lookup %s failed: %s", sym, e)
                return False
            self._assets[sym] = a
        if not a.tradable:
            return False
        if side < 0:
            if not (a.shortable and a.easy_to_borrow):
                return False
            if not getattr(acct, "shorting_enabled", True):
                return False
            if float(acct.equity) < config.MIN_EQUITY_FOR_SHORTS:
                return False
        return True

    def spread_ok(self, sym):
        try:
            q = self.dc.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=sym, feed=DataFeed(config.LIVE_FEED)))[sym]
            bid, ask = float(q.bid_price), float(q.ask_price)
        except Exception:   # noqa: BLE001
            return False
        if bid <= 0 or ask <= 0 or ask < bid:
            return False
        return (ask - bid) / ((ask + bid) / 2) <= config.MAX_SPREAD_PCT

    # ---------- entries ----------
    def open_bracket(self, sig, qty):
        """Market entry + broker-side take-profit and stop-loss (protects you if the bot dies).
        The client_order_id is reused across retries so a retried submit can't double-enter."""
        coid = f"{PREFIX}_{sig.expert}_{sig.symbol}_{uuid.uuid4().hex[:8]}"
        req = MarketOrderRequest(
            symbol=sig.symbol, qty=qty,
            side=OrderSide.BUY if sig.side > 0 else OrderSide.SELL,
            time_in_force=TimeInForce.DAY, order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=sig.target),
            stop_loss=StopLossRequest(stop_price=sig.stop),
            client_order_id=coid)
        return call(self.tc.submit_order, order_data=req, what=f"bracket {sig.symbol}")

    # ---------- open orders ----------
    def _open_orders(self, sym):
        seen = {}
        for nested in (False, True):
            try:
                orders = call(self.tc.get_orders, filter=GetOrdersRequest(
                    status=QueryOrderStatus.OPEN, symbols=[sym], nested=nested), what=f"get_orders {sym}")
            except Exception:   # noqa: BLE001
                continue
            for o in orders:
                for x in [o] + list(o.legs or []):
                    seen[str(x.id)] = x
        return [x for x in seen.values() if _val(x.status) in OPEN_STATUSES]

    def cancel_symbol_orders(self, sym, wait_s=4.0):
        """Cancel everything for sym and WAIT until Alpaca confirms, so the shares are released.
        Returns True only when no open orders remain."""
        for o in self._open_orders(sym):
            try:
                call(self.tc.cancel_order_by_id, o.id, what=f"cancel {sym}")
            except Exception:   # noqa: BLE001
                pass   # already filled/cancelled, or will show up in the re-check below
        t0 = time.time()
        while time.time() - t0 < wait_s:
            if not self._open_orders(sym):
                return True
            time.sleep(0.3)
        return not self._open_orders(sym)

    def _wait_qty_released(self, sym, wait_s=3.0):
        """After cancels, qty_available must equal qty or a new exit order will be rejected."""
        t0 = time.time()
        while time.time() - t0 < wait_s:
            p = self._position(sym)
            if p is None:
                return None
            if abs(p[1]) >= abs(p[0]):
                return p
            time.sleep(0.3)
        return self._position(sym)

    # ---------- exits ----------
    def close(self, sym):
        if not self.cancel_symbol_orders(sym):
            log.warning("close %s: orders not confirmed cancelled, trying close anyway", sym)
        try:
            call(self.tc.close_position, sym, what=f"close {sym}")
            return True
        except Exception as e:   # noqa: BLE001
            log.error("close %s failed: %s", sym, e)
            return False

    def protect(self, sym, pos):
        """Standalone stop for a position whose bracket legs are gone."""
        return self._submit_stop(sym, abs(pos.qty), pos.side, pos.stop, pos.expert) is not None

    def _submit_stop(self, sym, qty, side, stop, expert):
        try:
            return call(self.tc.submit_order, order_data=StopOrderRequest(
                symbol=sym, qty=qty, side=OrderSide.SELL if side > 0 else OrderSide.BUY,
                stop_price=round(stop, 2), time_in_force=TimeInForce.DAY,
                client_order_id=f"{PREFIX}_{expert}_{sym}_stp{uuid.uuid4().hex[:6]}"), what=f"stop {sym}")
        except Exception as e:   # noqa: BLE001
            log.error("stop order %s failed: %s", sym, e)
            return None

    def _submit_oco(self, sym, qty, side, stop, target, expert):
        try:
            return call(self.tc.submit_order, order_data=LimitOrderRequest(
                symbol=sym, qty=qty, side=OrderSide.SELL if side > 0 else OrderSide.BUY,
                limit_price=round(target, 2), time_in_force=TimeInForce.DAY, order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=round(target, 2)),
                stop_loss=StopLossRequest(stop_price=round(stop, 2)),
                client_order_id=f"{PREFIX}_{expert}_{sym}_oco{uuid.uuid4().hex[:6]}"), what=f"oco {sym}")
        except Exception as e:   # noqa: BLE001
            log.warning("OCO %s failed: %s", sym, e)
            return None

    def open_stop_price(self, sym):
        for o in self._open_orders(sym):
            if o.stop_price is not None and o.limit_price is None:
                return float(o.stop_price)
        return None

    def move_stop(self, sym, pos, new_stop, last_price):
        """Move the protective stop, falling back step by step so the position is never left naked.

        1. price already through new_stop?  -> BREACHED (caller exits at market)
        2. modify the stop leg in place      -> REPLACED
        3. cancel legs, confirm, re-arm OCO  -> OCO        (new stop + original target)
        4. plain stop only                   -> STOP_ONLY  (bot watches the target itself)
        5. market close                      -> FLATTENED
        """
        side = pos.side
        if side * (last_price - new_stop) <= 0.01:
            return BREACHED

        # step 2: in-place modify (transient errors retried inside call())
        leg = next((o for o in self._open_orders(sym)
                    if o.stop_price is not None and o.limit_price is None), None)
        if leg is not None:
            try:
                call(self.tc.replace_order_by_id, leg.id, ReplaceOrderRequest(stop_price=round(new_stop, 2)),
                     what=f"replace stop {sym}")
                return REPLACED
            except Exception as e:   # noqa: BLE001
                log.info("in-place stop modify %s rejected (%s) - falling back to cancel + OCO", sym, e)

        # step 3: cancel the old legs and wait for the shares to be released
        if not self.cancel_symbol_orders(sym):
            log.warning("move_stop %s: old orders not confirmed cancelled - keeping them, retry next tick", sym)
            return FAILED
        p = self._wait_qty_released(sym)
        if p is None:
            return GONE   # a leg filled while we were cancelling
        qty = abs(p[0])
        if self._submit_oco(sym, qty, side, new_stop, pos.target, pos.expert) is not None:
            return OCO

        # step 4: plain stop
        if self._submit_stop(sym, qty, side, new_stop, pos.expert) is not None:
            log.warning("%s protected by plain stop only; bot will manage the target", sym)
            return STOP_ONLY

        # step 5: never stay naked
        log.critical("%s: could not re-arm any stop - flattening", sym)
        try:
            call(self.tc.close_position, sym, what=f"emergency close {sym}")
            return FLATTENED
        except Exception as e:   # noqa: BLE001
            log.critical("EMERGENCY CLOSE %s FAILED: %s - POSITION IS UNPROTECTED, CHECK THE ACCOUNT", sym, e)
            return FAILED

    def recover_expert(self, sym):
        """After a restart: which expert opened this position? (parsed from client_order_id)"""
        try:
            orders = call(self.tc.get_orders, filter=GetOrdersRequest(
                status=QueryOrderStatus.CLOSED, symbols=[sym], limit=50), what=f"history {sym}")
        except Exception:   # noqa: BLE001
            return None
        for o in orders:
            cid = o.client_order_id or ""
            if cid.startswith(PREFIX + "_") and float(o.filled_qty or 0) > 0:
                return cid.split("_")[1]
        return None
