"""Offline tests for TelegramBot.ensure_polling.

The exported tests/test_telegram_bot.py needs ajbet.alerts, value_scan and a conftest
config fixture, none of which are in this bundle. These tests stub that surface and
never open a socket.
"""
import importlib
import logging
import sys
import threading
import time
import types
from pathlib import Path

import pytest
import requests

_SRC = Path(__file__).resolve().parents[1] / "source" / "aj-bet-bot" / "source"
CHAT = "424242"  # synthetic fixture; not a real Telegram chat id


def _install_ajbet():
    for name in list(sys.modules):
        if name == "ajbet" or name.startswith("ajbet."):
            del sys.modules[name]
    pkg = types.ModuleType("ajbet")
    pkg.__path__ = [str(_SRC)]
    pkg.__package__ = "ajbet"
    pkg.__file__ = str(_SRC / "__init__.py")
    sys.modules["ajbet"] = pkg
    exec(compile((_SRC / "__init__.py").read_text(encoding="utf-8"), str(_SRC / "__init__.py"), "exec"), pkg.__dict__)

    def stub(modname, **attrs):
        mod = types.ModuleType(modname)
        mod.__package__ = "ajbet"
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[modname] = mod

    stub("ajbet.markets", match_event=lambda *a, **k: None, name_sim=lambda *a, **k: 0.0)
    stub("ajbet.mybook")
    stub(
        "ajbet.models",
        norm_cdf=lambda *a, **k: 0.0,
        norm_ppf=lambda *a, **k: 0.0,
        poisson_cdf=lambda *a, **k: 0.0,
        poisson_lambda_for=lambda *a, **k: 0.0,
        poisson_pmf=lambda *a, **k: 0.0,
    )
    stub(
        "ajbet.oddsmath",
        american_to_decimal=lambda price: 1.0,
        american_to_prob=lambda price: 0.5,
        devig_american=lambda *a, **k: {},
        prob_to_american=lambda p: 100,
    )
    stub("ajbet.friendly")
    return importlib.import_module("ajbet.telegram_bot")


_tb = _install_ajbet()
TelegramBot = _tb.TelegramBot


class Notifier:
    """Stand-in for ajbet.alerts.Notifier. Only the attributes the poller reads."""

    def __init__(self):
        self._token = "T0KEN"
        self._chat = CHAT

    def send(self, text, html=False):
        return True


class Resp:
    def __init__(self, code, body):
        self.status_code = code
        self._body = body

    def json(self):
        return self._body


def _ok():
    return Resp(200, {"ok": True, "result": []})


def _config():
    return {"telegram": {"bot": {"enabled": True, "skip_backlog": True, "username": "March_betbot"}}}


def make_bot(http):
    return TelegramBot(_config(), None, None, None, Notifier(), http=http)


def _poll_threads():
    return [t for t in threading.enumerate() if t.name == "telegram-poll" and t.is_alive()]


def _release(bot, *events):
    for event in events:
        event.set()
    bot._stop.set()
    if bot._poll_stop is not None:
        bot._poll_stop.set()
    thread = bot._thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(2)


class FlakyHTTP:
    """getUpdates returns 502. deleteWebhook-style calls succeed. No network."""

    def post(self, url, data=None, timeout=None):
        if str(url).rsplit("/", 1)[-1] == "getUpdates":
            return Resp(502, {"ok": False, "description": "Bad Gateway"})
        return _ok()

    def close(self):
        return None


def test_alive_retry_does_not_start_a_second_poller():
    """A live thread in post-502 backoff keeps the only getUpdates loop."""
    bot = make_bot(FlakyHTTP())
    held = threading.Event()
    release = threading.Event()

    class Hold(threading.Event):
        def wait(self, timeout=None):
            held.set()
            release.wait()
            return super().is_set()

    bot.last_poll = time.time() - 1000  # older than 180s and older than 180+345
    bot._poll_stop = Hold()
    bot._thread = threading.Thread(target=bot._loop, args=(bot._poll_stop,), name="telegram-poll", daemon=True)
    bot._thread.start()
    try:
        assert held.wait(2), "poll thread never reached retry backoff"
        assert time.time() - bot.last_attempt < 5
        first = bot._thread
        assert bot.ensure_polling() is False
        assert bot._thread is first
        assert first.is_alive()
        assert _poll_threads() == [first]
    finally:
        release.set()
        _release(bot)


def test_dead_thread_is_restarted():
    bot = make_bot(FlakyHTTP())
    dead = threading.Thread(target=lambda: None, name="telegram-poll", daemon=True)
    dead.start()
    dead.join()
    bot._thread = dead
    gate = threading.Event()

    class Hang:
        def post(self, url, data=None, timeout=None):
            if str(url).rsplit("/", 1)[-1] == "getUpdates":
                gate.wait()
            return _ok()

        def close(self):
            gate.set()

    bot.poll_http = Hang()
    try:
        assert bot.ensure_polling() is True
        assert bot._thread is not dead
        assert bot._thread.is_alive()
        assert _poll_threads() == [bot._thread]
    finally:
        gate.set()
        _release(bot)


def test_hung_thread_restarts_with_one_live_poller(monkeypatch):
    """Close the stuck session, join it, and only then start the replacement."""
    started_old = threading.Event()

    class Blocking:
        def __init__(self):
            self.closed = threading.Event()
            self.started = threading.Event()

        def post(self, url, data=None, timeout=None):
            if str(url).rsplit("/", 1)[-1] == "getUpdates":
                self.started.set()
                started_old.set()
                if not self.closed.wait(8):
                    raise AssertionError("hung poll session was not closed")
                raise requests.ConnectionError("session closed")
            return _ok()

        def close(self):
            self.closed.set()

    old_http = Blocking()
    created = []

    def fake_session():
        session = Blocking()
        created.append(session)
        return session

    monkeypatch.setattr(requests, "Session", fake_session)
    bot = make_bot(old_http)
    bot._poll_stop = threading.Event()
    bot._thread = threading.Thread(target=bot._loop, args=(bot._poll_stop,), name="telegram-poll", daemon=True)
    bot._thread.start()
    try:
        assert started_old.wait(2)
        bot.last_poll = time.time() - 1000
        bot.last_attempt = time.time() - 1000
        old = bot._thread
        assert bot.ensure_polling() is True
        assert created, "hung restart did not swap in a new session"
        assert not old.is_alive()
        assert bot._thread is not old and bot._thread.is_alive()
        assert _poll_threads() == [bot._thread]
    finally:
        for session in created:
            session.close()
        old_http.close()
        _release(bot)


def test_hung_thread_still_alive_does_not_spawn_another(monkeypatch):
    """join(timeout=5) gives up; the stuck thread stays the only poller."""
    blocked = threading.Event()
    release = threading.Event()

    class Stuck:
        def post(self, url, data=None, timeout=None):
            if str(url).rsplit("/", 1)[-1] == "getUpdates":
                blocked.set()
                release.wait()
                raise requests.ConnectionError("released")
            return _ok()

        def close(self):
            return None

    spawned = []

    def fake_session():
        session = Stuck()
        spawned.append(session)
        return session

    monkeypatch.setattr(requests, "Session", fake_session)
    bot = make_bot(Stuck())
    bot._poll_stop = threading.Event()
    bot._thread = threading.Thread(target=bot._loop, args=(bot._poll_stop,), name="telegram-poll", daemon=True)
    bot._thread.start()
    try:
        assert blocked.wait(2)
        bot.last_poll = time.time() - 1000
        bot.last_attempt = time.time() - 1000
        old = bot._thread
        assert bot.ensure_polling() is False
        assert spawned, "hung path did not replace the session"
        assert old.is_alive()
        assert bot._thread is old
        assert _poll_threads() == [old]
    finally:
        release.set()
        _release(bot)


def test_409_logs_once_and_caps_retry_at_30s(caplog):
    waits = []

    class Stop(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) >= 8:
                self.set()
            return super().is_set()

    class Conflict:
        def post(self, url, data=None, timeout=None):
            if str(url).rsplit("/", 1)[-1] == "getUpdates":
                return Resp(409, {"ok": False, "description": "Conflict: terminated by other getUpdates request"})
            return _ok()

        def close(self):
            return None

    bot = make_bot(Conflict())
    with caplog.at_level(logging.WARNING, logger=_tb.__name__):
        bot._loop(Stop())
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
    assert max(waits) <= 30
    conflicts = [r for r in caplog.records if "409 Conflict" in r.getMessage()]
    assert len(conflicts) == 1
