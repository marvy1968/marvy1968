"""Paper-only URL guard. Imported by config and the broker adapter."""

from __future__ import annotations

from .errors import LiveEndpointRefused

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_HOST = "api.alpaca.markets"


def assert_paper_url(url: str) -> str:
    """Accept only the paper trading host. Data still uses data.alpaca.markets."""
    cleaned = str(url or "").strip().rstrip("/")
    host = cleaned.split("://", 1)[-1].split("/", 1)[0].lower()
    if cleaned != PAPER_URL or host == LIVE_HOST or "paper-api.alpaca.markets" not in cleaned:
        raise LiveEndpointRefused(cleaned or url)
    return PAPER_URL
