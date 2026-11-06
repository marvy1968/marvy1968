"""IEX bars and quotes. SIP is refused. Incomplete bars are dropped."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .bars import assert_iex, drop_incomplete, history_start
from .clock import ET, as_et
from .errors import BrokerReadError, FeedRefused
from .http_retry import call_read, force_timeouts
from .types import Bar, MarketBundle, Quote


class AlpacaData:
    def __init__(self, api_key: str, secret_key: str, cfg, sleep=None):
        self.feed = assert_iex(cfg.feed)
        self.cfg = cfg
        self._api_key = api_key
        self._secret_key = secret_key
        self._client = None
        self._sleep = sleep or __import__("time").sleep

    def client(self):
        if self._client is not None:
            return self._client
        from alpaca.data.historical import StockHistoricalDataClient

        client = StockHistoricalDataClient(self._api_key, self._secret_key)
        force_timeouts(client, self.cfg.timeouts.connect_sec, self.cfg.timeouts.read_sec)
        self._client = client
        return client

    def bundle(self, symbols: list[str], now: datetime, sessions: dict) -> MarketBundle:
        if self.feed != "iex":
            raise FeedRefused(self.feed)
        end = as_et(now).astimezone(timezone.utc)
        daily = self._fetch(symbols, "day", history_start(now, "daily"), end)
        minute5 = self._fetch(symbols, "5", history_start(now, "day"), end)
        minute1 = self._fetch(symbols, "1", history_start(now, "scalp"), end)
        quotes = self._quotes(symbols)
        return MarketBundle(
            daily={sym: drop_incomplete(rows, now, 1440, sessions) for sym, rows in daily.items()},
            min5={sym: drop_incomplete(rows, now, 5, sessions) for sym, rows in minute5.items()},
            min1={sym: drop_incomplete(rows, now, 1, sessions) for sym, rows in minute1.items()},
            quotes=quotes,
        )

    def _fetch(self, symbols: list[str], kind: str, start: datetime, end: datetime) -> dict[str, list[Bar]]:
        retries = max(1, int(self.cfg.timeouts.retries))
        backoff = tuple(self.cfg.timeouts.backoff_sec)

        def _go():
            from alpaca.data.enums import DataFeed
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

            if kind == "day":
                timeframe = TimeFrame.Day
            else:
                timeframe = TimeFrame(int(kind), TimeFrameUnit.Minute)
            # feed is set explicitly. The default must not be SIP.
            request = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=timeframe,
                start=start,
                end=end,
                feed=DataFeed.IEX,
            )
            feed = getattr(request, "feed", None)
            feed_name = str(getattr(feed, "value", feed)).lower()
            if feed_name != "iex":
                raise FeedRefused(feed_name)
            return self.client().get_stock_bars(request)

        try:
            result = call_read(_go, retries, backoff, self._sleep)
        except BrokerReadError:
            raise
        except Exception as exc:
            raise BrokerReadError(type(exc).__name__) from exc
        data = getattr(result, "data", result) or {}
        out: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
        for symbol, rows in data.items():
            parsed = []
            for row in rows:
                ts = row.timestamp
                if isinstance(ts, datetime) and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                parsed.append(
                    Bar(
                        ts=ts.astimezone(ET),
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=float(row.volume or 0),
                    )
                )
            out[str(symbol)] = parsed
        return out

    def _quotes(self, symbols: list[str]) -> dict[str, Quote]:
        retries = max(1, int(self.cfg.timeouts.retries))
        backoff = tuple(self.cfg.timeouts.backoff_sec)

        def _go():
            from alpaca.data.enums import DataFeed
            from alpaca.data.requests import StockLatestQuoteRequest

            request = StockLatestQuoteRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX)
            return self.client().get_stock_latest_quote(request)

        result = call_read(_go, retries, backoff, self._sleep)
        raw = getattr(result, "data", result) or result
        out: dict[str, Quote] = {}
        items = raw.items() if hasattr(raw, "items") else []
        for symbol, quote in items:
            bid = float(getattr(quote, "bid_price", 0) or 0)
            ask = float(getattr(quote, "ask_price", 0) or 0)
            ts = getattr(quote, "timestamp", None)
            out[str(symbol)] = Quote(bid=bid, ask=ask, ts=ts)
        return out
