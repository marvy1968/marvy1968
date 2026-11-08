"""DEMO host rule copied from /opt/kalshi-demo/kalshi_demo.py lines 8-40.

A host is allowed only when it is one of the two demo names and the name
contains 'demo'. This module does not read credentials or open a socket.
"""

from urllib.parse import urlparse

ALLOWED_DEMO_HOSTS = frozenset({"demo-api.kalshi.co", "external-api.demo.kalshi.co"})


class ProductionRefused(RuntimeError):
    pass


def assert_demo_base(base: str) -> str:
    host = urlparse(str(base or "")).hostname or ""
    if host not in ALLOWED_DEMO_HOSTS or "demo" not in host:
        raise ProductionRefused(f"host {host!r} is not a Kalshi DEMO host - refused")
    return host
