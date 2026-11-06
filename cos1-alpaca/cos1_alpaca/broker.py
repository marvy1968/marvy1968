"""Alpaca paper adapter. The live trading host is refused before a client exists."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .broker_guard import PAPER_URL, assert_paper_url
from .clock import ET
from .errors import BrokerReadError, LiveEndpointRefused
from .http_retry import call_read, call_submit, force_timeouts
from .types import Account, Asset, BrokerOrder, BrokerPosition, Fill, Session


def _value(obj):
    return getattr(obj, "value", obj)


def _enum_name(obj) -> str:
    raw = str(_value(obj) if obj is not None else "").lower()
    if "." in raw:
        raw = raw.split(".")[-1]
    return raw


class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, cfg, sleep=None):
        assert_paper_url(cfg.account.base_url)
        if list(cfg.rollout.live):
            raise LiveEndpointRefused("rollout.live")
        self.base_url = PAPER_URL
        self.cfg = cfg
        self._api_key = api_key
        self._secret_key = secret_key
        self._client = None
        self._sleep = sleep or __import__("time").sleep

    def _timeouts(self):
        return (
            max(1, int(self.cfg.timeouts.retries)),
            tuple(self.cfg.timeouts.backoff_sec),
            self._sleep,
        )

    def client(self):
        if self._client is not None:
            return self._client
        assert_paper_url(self.base_url)
        from alpaca.trading.client import TradingClient

        client = TradingClient(self._api_key, self._secret_key, paper=True, url_override=PAPER_URL)
        force_timeouts(client, self.cfg.timeouts.connect_sec, self.cfg.timeouts.read_sec)
        base = getattr(client, "_base_url", "")
        text = str(getattr(base, "value", base) or "")
        if "paper-api.alpaca.markets" not in text:
            raise LiveEndpointRefused(text)
        self._client = client
        return client

    def get_account(self) -> Account:
        retries, backoff, sleep = self._timeouts()
        account = call_read(lambda: self.client().get_account(), retries, backoff, sleep)
        return Account(equity=float(account.equity), cash=float(account.cash))

    def get_positions(self) -> list[BrokerPosition]:
        retries, backoff, sleep = self._timeouts()
        rows = call_read(lambda: self.client().get_all_positions(), retries, backoff, sleep)
        out = []
        for row in rows:
            side = _enum_name(getattr(row, "side", "long"))
            if side not in ("long", "short"):
                side = "long" if float(row.qty) > 0 else "short"
            out.append(
                BrokerPosition(
                    symbol=row.symbol,
                    qty=abs(float(row.qty)),
                    side=side,
                    avg_price=float(row.avg_entry_price),
                    current_price=float(getattr(row, "current_price", None) or row.avg_entry_price),
                )
            )
        return out

    def get_open_orders(self) -> list[BrokerOrder]:
        retries, backoff, sleep = self._timeouts()

        def _go():
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            return self.client().get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500))

        rows = call_read(_go, retries, backoff, sleep)
        return [self._order(row) for row in rows]

    def get_calendar(self, start: date, end: date) -> list[Session]:
        retries, backoff, sleep = self._timeouts()

        def _go():
            from alpaca.trading.requests import GetCalendarRequest

            return self.client().get_calendar(GetCalendarRequest(start=start, end=end))

        rows = call_read(_go, retries, backoff, sleep)
        sessions = []
        for row in rows:
            day = row.date if isinstance(row.date, date) else date.fromisoformat(str(row.date)[:10])
            open_t = _as_time(row.open)
            close_t = _as_time(row.close)
            open_dt = datetime.combine(day, open_t, tzinfo=ET)
            close_dt = datetime.combine(day, close_t, tzinfo=ET)
            sessions.append(Session(day=day, open=open_dt, close=close_dt, half_day=close_dt.hour < 16))
        return sessions

    def get_asset(self, symbol: str) -> Asset:
        retries, backoff, sleep = self._timeouts()
        row = call_read(lambda: self.client().get_asset(symbol), retries, backoff, sleep)
        return Asset(
            symbol=symbol,
            tradable=bool(row.tradable),
            shortable=bool(row.shortable),
            easy_to_borrow=bool(getattr(row, "easy_to_borrow", False)),
        )

    def get_fills_since(self, start: datetime) -> list[Fill]:
        """FILL activities. TradingClient has no activities helper, so this is the REST route."""
        retries, backoff, sleep = self._timeouts()

        def _go():
            params = {
                "activity_types": "FILL",
                "after": start.astimezone(timezone.utc).isoformat(),
                "direction": "asc",
                "page_size": 100,
            }
            rows = []
            seen = set()
            for _ in range(20):
                page = self.client().get("/account/activities", params) or []
                if isinstance(page, dict):
                    page = page.get("activities") or []
                if not page:
                    break
                rows.extend(page)
                last = page[-1]
                token = last.get("id") if isinstance(last, dict) else getattr(last, "id", None)
                if not token or token in seen or len(page) < 100:
                    break
                seen.add(token)
                params["page_token"] = token
            return rows

        rows = call_read(_go, retries, backoff, sleep)
        return [self._fill(row) for row in rows or []]

    def find_by_client_id(self, client_order_id: str):
        try:
            row = self.client().get_order_by_client_id(client_order_id)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            text = str(exc).lower()
            if status == 404 or "404" in text or "not found" in text or "no order" in text:
                return None
            raise
        if row is None:
            return None
        return self._order(row)

    def submit_bracket(self, plan):
        retries, backoff, sleep = self._timeouts()

        def _go():
            from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
            from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

            request = MarketOrderRequest(
                symbol=plan.symbol,
                qty=plan.qty,
                side=OrderSide.BUY if plan.side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY if plan.time_in_force == "day" else TimeInForce.GTC,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=plan.take_profit),
                stop_loss=StopLossRequest(stop_price=plan.stop),
                client_order_id=plan.client_order_id,
            )
            return self.client().submit_order(order_data=request)

        row = call_submit(_go, lambda: self.find_by_client_id(plan.client_order_id), retries, backoff, sleep)
        return self._order(row)

    def submit_stop(self, symbol: str, qty: int, side: str, stop_price: float, client_order_id: str, time_in_force: str = "day"):
        retries, backoff, sleep = self._timeouts()

        def _go():
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import StopOrderRequest

            request = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL if side == "sell" else OrderSide.BUY,
                time_in_force=TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC,
                stop_price=stop_price,
                client_order_id=client_order_id,
            )
            return self.client().submit_order(order_data=request)

        row = call_submit(_go, lambda: self.find_by_client_id(client_order_id), retries, backoff, sleep)
        return self._order(row)

    def cancel_order(self, order_id: str) -> None:
        retries, backoff, sleep = self._timeouts()
        call_read(lambda: self.client().cancel_order_by_id(order_id), retries, backoff, sleep)

    def close_position(self, symbol: str, qty: int, side: str, client_order_id: str):
        retries, backoff, sleep = self._timeouts()

        def _go():
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import MarketOrderRequest

            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL if side == "sell" else OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
            )
            return self.client().submit_order(order_data=request)

        row = call_submit(_go, lambda: self.find_by_client_id(client_order_id), retries, backoff, sleep)
        return self._order(row)

    def stop_is_working(self, order_id: str) -> bool:
        retries, backoff, sleep = self._timeouts()

        def _go():
            return self.client().get_order_by_id(order_id)

        try:
            row = call_read(_go, retries, backoff, sleep)
        except BrokerReadError:
            return False
        return _enum_name(getattr(row, "status", "")) in {"new", "accepted", "pending_new", "held", "open"}

    def _order(self, row) -> BrokerOrder:
        legs = []
        for leg in getattr(row, "legs", None) or []:
            legs.append(self._order(leg))
        limit_price = getattr(row, "limit_price", None)
        stop_price = getattr(row, "stop_price", None)
        filled = getattr(row, "filled_avg_price", None)
        return BrokerOrder(
            id=str(getattr(row, "id", "") or ""),
            client_order_id=str(getattr(row, "client_order_id", "") or ""),
            symbol=str(getattr(row, "symbol", "") or ""),
            side=_enum_name(getattr(row, "side", "")),
            qty=float(getattr(row, "qty", 0) or 0),
            status=_enum_name(getattr(row, "status", "")),
            type=_enum_name(getattr(row, "type", "") or getattr(row, "order_type", "")),
            limit_price=float(limit_price) if limit_price is not None else None,
            stop_price=float(stop_price) if stop_price is not None else None,
            filled_avg_price=float(filled) if filled is not None else None,
            legs=legs,
        )

    def _fill(self, row) -> Fill:
        def field(name, default=None):
            if isinstance(row, dict):
                return row.get(name, default)
            return getattr(row, name, default)

        ts = field("transaction_time") or field("timestamp") or datetime.now(timezone.utc)
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        side = _enum_name(field("side", ""))
        return Fill(
            id=str(field("id", "") or ""),
            ts=ts,
            symbol=str(field("symbol", "") or ""),
            side=side,
            qty=float(field("qty", 0) or 0),
            price=float(field("price", 0) or 0),
            fee=float(field("commission", 0) or 0) + float(field("fee", 0) or 0),
            order_id=str(field("order_id", "") or ""),
            client_order_id=str(field("client_order_id", "") or ""),
        )


def _as_time(value) -> time:
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    text = str(value)
    parts = text.split(":")
    return time(int(parts[0]), int(parts[1]))


def calendar_span(now: datetime) -> tuple[date, date]:
    day = now.astimezone(ET).date() if now.tzinfo else now.date()
    return day - timedelta(days=10), day + timedelta(days=10)
