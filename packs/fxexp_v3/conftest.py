import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

@pytest.fixture(autouse=True)
def _no_news_network(monkeypatch):
    """Tests never call ForexFactory: the calendar 'fails' and the Fed-day fallback is used."""
    from fxexp import news
    def _offline(*a, **k): raise OSError("offline in tests")
    monkeypatch.setattr(news, "fetch_ff", _offline)
