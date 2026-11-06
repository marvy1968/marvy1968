"""Headline scorer for the Alpaca news API.

Positive words minus negative words. A positive score allows or biases long.
A negative score allows or biases short. A zero score is neutral and does not
block. The switch is off unless config says otherwise. This is a lexicon, not
a claim that headlines predict returns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .http_retry import call_read, force_timeouts

_WORD = re.compile(r"[a-z]+")

POS = {
    "upgrade", "upgrades", "upgraded", "beat", "beats", "raise", "raises", "raised",
    "strong", "stronger", "strength", "rise", "rises", "jump", "jumps", "surge", "surges",
    "record", "gain", "gains", "growth", "bullish", "outperform", "buy", "approval",
    "approved", "win", "wins", "boost", "boosts", "expand", "expands", "partnership",
    "upside", "rally", "profit", "profits", "dividend", "buyback",
}
NEG = {
    "downgrade", "downgrades", "downgraded", "miss", "misses", "missed", "cut", "cuts",
    "weak", "weaker", "weakness", "decline", "declines", "drop", "drops", "fall", "falls",
    "plunge", "plunges", "slump", "loss", "losses", "lawsuit", "probe", "investigation",
    "recall", "layoff", "layoffs", "warning", "warns", "bearish", "underperform", "fraud",
    "halt", "disappoint", "disappoints", "slowdown", "bankruptcy", "default", "downside",
    "headwind", "headwinds",
}


def score_text(text: str) -> int:
    words = _WORD.findall((text or "").lower())
    return sum(word in POS for word in words) - sum(word in NEG for word in words)


@dataclass
class NewsSnapshot:
    by_symbol: dict[str, int] = field(default_factory=dict)
    by_sector: dict[str, int] = field(default_factory=dict)
    market: int = 0
    articles: int = 0

    def symbol_score(self, symbol: str) -> int:
        return int(self.by_symbol.get(symbol.upper(), 0))

    def sector_score(self, sector: str) -> int:
        return int(self.by_sector.get(sector, 0))


def snapshot_from_articles(articles: list[dict], sector_of) -> NewsSnapshot:
    snap = NewsSnapshot()
    for article in articles:
        score = int(article.get("score", score_text(article.get("text", ""))))
        snap.market += score
        snap.articles += 1
        for symbol in article.get("symbols") or []:
            symbol = str(symbol).upper()
            snap.by_symbol[symbol] = snap.by_symbol.get(symbol, 0) + score
            sector = sector_of(symbol)
            if sector and sector != "INDEX":
                snap.by_sector[sector] = snap.by_sector.get(sector, 0) + score
    return snap


def _side_ok(score: int, side: str) -> bool:
    """Zero is neutral. A positive score only allows long. A negative score only allows short."""
    if score == 0:
        return True
    if side == "long":
        return score > 0
    return score < 0


def news_decision(side: str, symbol: str, sector: str, snap: NewsSnapshot | None, enabled: bool) -> tuple[bool, str]:
    if not enabled:
        return True, "bypass"
    if snap is None:
        return False, "unread"
    symbol_score = snap.symbol_score(symbol)
    sector_score = snap.sector_score(sector)
    ok = _side_ok(snap.market, side) and _side_ok(sector_score, side) and _side_ok(symbol_score, side)
    detail = f"market={snap.market} sector={sector_score} symbol={symbol_score}"
    return ok, ("allow " + detail) if ok else ("block " + detail)


class AlpacaNews:
    """Recent headlines from the Alpaca news API. Same key pair as the paper account."""

    def __init__(self, api_key: str, secret_key: str, cfg):
        self._api_key = api_key
        self._secret_key = secret_key
        self.cfg = cfg
        self._client = None

    def client(self):
        if self._client is None:
            from alpaca.data.historical.news import NewsClient

            client = NewsClient(self._api_key, self._secret_key)
            force_timeouts(client, self.cfg.timeouts.connect_sec, self.cfg.timeouts.read_sec)
            self._client = client
        return self._client

    def snapshot(self, symbols: list[str], now: datetime, sector_of) -> NewsSnapshot:
        start = now - timedelta(hours=float(self.cfg.news.lookback_hours))
        articles = self._fetch(symbols, start, now)
        return snapshot_from_articles(articles, sector_of)

    def _fetch(self, symbols: list[str], start: datetime, end: datetime) -> list[dict]:
        from alpaca.data.requests import NewsRequest

        retries = max(1, int(self.cfg.timeouts.retries))
        backoff = tuple(self.cfg.timeouts.backoff_sec)
        request = NewsRequest(
            start=start,
            end=end,
            symbols=",".join(symbols),
            limit=50,
            include_content=False,
        )

        def _go():
            return self.client().get_news(request)

        result = call_read(_go, retries, backoff, __import__("time").sleep)
        rows = []
        data = getattr(result, "data", None) or {}
        if isinstance(data, dict):
            grouped = data.values()
        else:
            grouped = [data]
        for group in grouped:
            for article in group or []:
                headline = getattr(article, "headline", "") or ""
                summary = getattr(article, "summary", "") or ""
                rows.append(
                    {
                        "symbols": [str(s).upper() for s in (getattr(article, "symbols", None) or [])],
                        "score": score_text(f"{headline} {summary}"),
                        "text": f"{headline} {summary}",
                    }
                )
        return rows
