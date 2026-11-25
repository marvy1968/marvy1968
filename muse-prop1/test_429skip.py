"""Offline tests for the 429/5xx run-skip fix. Mocked transport only; no network, no keys."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import collector as C
from credits import CreditError, Ledger
from test_collector import KEY, NOW_LIVE, NOW_PRE, Feed, _cfg, _env, _runner


class EventsFail(Feed):
    """The first Odds API call of a run (/events, 0 credits) is refused."""

    def __init__(self, status, headers=None, body=b'{"message":"slow down"}', **kw):
        super().__init__(**kw)
        self.events_status = status
        self.events_headers = headers or {}
        self.events_body = body
        self.fail_events = True

    def __call__(self, url):
        if url.split("?")[0].endswith("/events") and self.fail_events:
            self.urls.append(url)
            return self.events_status, dict(self.events_headers), self.events_body
        return super().__call__(url)


def _usage(td):
    return json.loads((Path(td) / "state" / "usage.json").read_text())


def _run(td, feed, now=NOW_LIVE, cap=100):
    return C.collect_once(_cfg(Path(td), cap=cap), Path(td) / "state", Path(td) / "archive",
                          feed, _runner([]), now, _env())


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_events_error_skips_run_without_latch_and_next_run_works(status):
    with TemporaryDirectory() as td:
        feed = EventsFail(status, board_state="pre")
        with pytest.raises(C.CollectorError) as err:
            _run(td, feed, now=NOW_PRE)
        msg = str(err.value)
        assert KEY not in msg and "quota" not in msg and "run skipped" in msg
        if status == 429:
            assert "rate-limited" in msg
        usage = _usage(td)
        assert usage["fail_closed"] is False
        assert [r["status"] for r in usage["reservations"]] == ["skipped_transient"]
        assert not any(r["status"] == "held" for r in usage["reservations"])
        assert feed.props_calls == 0
        feed.fail_events = False
        _run(td, feed, now=NOW_PRE)
        assert feed.props_calls == 1
        usage = _usage(td)
        assert usage["fail_closed"] is False and usage["used"] >= 4
        assert usage["reservations"][-1]["status"].startswith("reconciled")


@pytest.mark.parametrize("status", [429, 502])
def test_transient_props_error_records_call_keeps_credits_and_does_not_latch(status):
    with TemporaryDirectory() as td:
        feed = Feed(props_status=status, props_header=None, board_state="pre")
        _run(td, feed, now=NOW_PRE)
        usage = _usage(td)
        assert usage["fail_closed"] is False
        props = [r for r in usage["reservations"] if r["kind"] == "event_odds"]
        assert len(props) == 1 and props[0]["status"] == "skipped_transient"
        assert props[0]["counted"] == props[0]["worst_case"] > 0
        assert usage["used"] == props[0]["worst_case"]
        assert feed.props_calls == 1


def test_401_still_latches_the_day():
    with TemporaryDirectory() as td:
        feed = EventsFail(401)
        with pytest.raises(C.CollectorError) as err:
            _run(td, feed)
        assert "auth" in str(err.value) and KEY not in str(err.value)
        assert _usage(td)["fail_closed"] is True
        feed.fail_events = False
        with pytest.raises(C.CollectorError):
            _run(td, feed)
        assert len(feed.urls) == 2 or sum("/events" in u for u in feed.urls) == 1


def test_429_with_exhausted_remaining_header_latches():
    with TemporaryDirectory() as td:
        feed = EventsFail(429, headers={"x-requests-last": "0", "x-requests-remaining": "0"})
        with pytest.raises(C.CollectorError) as err:
            _run(td, feed)
        assert "quota exhausted" in str(err.value)
        assert _usage(td)["fail_closed"] is True


def test_429_with_quota_body_latches():
    with TemporaryDirectory() as td:
        feed = EventsFail(429, body=b'{"error_code":"OUT_OF_USAGE_CREDITS"}')
        with pytest.raises(C.CollectorError) as err:
            _run(td, feed)
        assert "quota exhausted" in str(err.value)
        assert _usage(td)["fail_closed"] is True


def test_local_cap_still_refuses_the_props_call():
    with TemporaryDirectory() as td:
        feed = Feed(board_state="pre")
        _run(td, feed, now=NOW_PRE, cap=1)
        assert feed.props_calls == 0
        abst = (Path(td) / "state" / "abstentions.jsonl").read_text()
        assert "credit_cap" in abst


def test_ledger_transient_billing_discrepancy_still_latches():
    with TemporaryDirectory() as td:
        led = Ledger(Path(td) / "usage.json", cap=100, now=NOW_LIVE)
        rid = led.reserve("event_odds", 4, "x")
        with pytest.raises(CreditError):
            led.reconcile(rid, {"x-requests-last": "9", "x-requests-remaining": "400"},
                          error="odds http 502; run skipped", transient=True)
        data = json.loads((Path(td) / "usage.json").read_text())
        assert data["fail_closed"] is True and data["used"] == 9
        led.close()
