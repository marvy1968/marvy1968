"""Synthetic observe-only accounting tests. No network and no production state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(PKG / "integrations"))
sys.path.insert(0, str(REPO))

os.environ.pop("ODDS_ACCOUNTING_LEDGER", None)
os.environ["PYTHONPATH"] = os.pathsep.join(
    [str(PKG), str(PKG / "integrations"), os.environ.get("PYTHONPATH", "")])

import requests

from odds_accounting.boundary import import_legacy_usage_row, observe_exchange
from odds_accounting.enforcement import (
    ENFORCEMENT_ENABLED,
    ENFORCEMENT_IMPLEMENTED,
    polling_or_cache_decision,
    priority_decision,
    shared_cap_decision,
)
from odds_accounting.ledger import read_state
from odds_accounting.reconcile import reconcile

CREDITS_SHA = "e32f73e4a4ff2cca6faaedd677791dbd292def79b8cc92ae9ed629c547e13ac4"
DB_SHA = "b584f4aaec9edc3b439327ef95d025772c44e66be4aad3571b8f5fc66b99bd57"
SAT_SHA = "af356f7bd114b430724d0133d2e3e176fb54b64a01c4b6ceb6bb06fa5f171878"
BASE_SHA = "63c05ea0d673f9544ed3795182bdfc5170d1c1c743a730295083d0b07fb18be1"
PROVIDER_SHA = "70b2ed57ed394e7d33dd1d0b849137fa0f9b551ca026fefc6a00a76d9d1fc9dc"
AJ_ODDS_BEFORE = "45e43b187777f656172090841b8214ce917f4b9c10f813180205bfb275be4ddc"


def _stamp(hour=12, day=8, month=11):
    return datetime(2026, month, day, hour, 0, tzinfo=timezone.utc)


def _observe(path, **kwargs):
    defaults = dict(
        source_job="synthetic-a",
        endpoint_template="/v4/sports/{sport}/odds",
        sport="synthetic_sport",
        markets=["h2h", "spreads", "totals"],
        regions=["us"],
        estimated_cost=3,
        estimate_provenance="markets_times_regions",
        account_label="synthetic",
        http_status=200,
        headers={"x-requests-last": "2", "x-requests-used": "100", "x-requests-remaining": "900"},
        ledger_path=path,
        observed_at=_stamp(),
    )
    defaults.update(kwargs)
    return observe_exchange(**defaults)


def _physical(path):
    state = read_state(Path(path))
    return [row for row in state["attempts"] if row["is_physical"] == 1]


class Response:
    def __init__(self, status=200, headers=None, body=None):
        self.status_code = status
        self.headers = {} if headers is None else headers
        self._body = [] if body is None else body
        self.reason = "synthetic"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} synthetic", response=self)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.sqlite"
        self.addCleanup(self.tmp.cleanup)

    def test_header_actual_zero_and_mismatch(self):
        _observe(self.path, headers={"x-requests-last": "0", "x-requests-used": "100", "x-requests-remaining": "9"},
                 attempt_id="10000000000000000000000000000001")
        row = _physical(self.path)[0]
        self.assertEqual(row["actual_header_cost"], 0)
        self.assertEqual(row["cost_basis"], "header")
        self.assertEqual(row["estimated_cost"], 3)
        self.assertEqual(row["shared_used"], 100)
        self.assertNotEqual(row["actual_header_cost"], row["shared_used"])

    def test_missing_malformed_negative_and_error_headers(self):
        cases = [
            ("20000000000000000000000000000001", 200, {"x-requests-used": "105"}, None, "missing", "estimate_only_not_actual"),
            ("20000000000000000000000000000002", 200, {"x-requests-last": "not-a-number"}, None, "unparseable", "unparseable"),
            ("20000000000000000000000000000003", 200, {"x-requests-last": "-4"}, None, "negative_rejected", "negative_rejected"),
            ("20000000000000000000000000000004", 502, {"x-requests-last": "1"}, 1, "header", "header"),
            ("20000000000000000000000000000005", 502, {}, None, "missing", "unknown_never_assert_zero"),
            ("20000000000000000000000000000006", 502, {}, None, "missing", "unknown"),
        ]
        for attempt, status, headers, actual, actual_status, basis in cases[:5]:
            _observe(self.path, attempt_id=attempt, http_status=status, headers=headers, estimated_cost=3)
        _observe(self.path, attempt_id=cases[5][0], http_status=502, headers={}, estimated_cost=0,
                 endpoint_template="/v4/sports/{sport}/events")
        rows = {row["attempt_id"]: row for row in _physical(self.path)}
        for attempt, _status, _headers, actual, actual_status, basis in cases:
            row = rows[attempt]
            self.assertEqual(row["actual_header_cost"], actual)
            self.assertEqual(row["actual_status"], actual_status)
            self.assertEqual(row["cost_basis"], basis)
            self.assertNotEqual(row["actual_header_cost"], 0 if actual is None else None)

    def test_transport_exception_is_unknown_and_keeps_cause_types_only(self):
        secret = "synthetic-" + "not-a-credential"
        try:
            raise ConnectionError("apiKey=" + secret)
        except ConnectionError as exc:
            try:
                raise TimeoutError("https://example.test/odds?apiKey=" + secret) from exc
            except TimeoutError as outer:
                err = outer
        _observe(self.path, http_status=None, headers=None, transport_error=err,
                 attempt_id="30000000000000000000000000000001")
        row = _physical(self.path)[0]
        self.assertEqual(row["outcome"], "transport_exception")
        self.assertIsNone(row["actual_header_cost"])
        self.assertEqual(row["cost_basis"], "unknown")
        self.assertEqual(row["exception_type"], "TimeoutError")
        self.assertEqual(row["exception_cause_type"], "ConnectionError")
        blob = self.path.read_bytes()
        self.assertNotIn(secret.encode(), blob)
        self.assertNotIn(b"example.test", blob)
        self.assertNotIn(b"apiKey", blob)

    def test_later_header_duplicate_and_retry_attempts(self):
        logical = "4000000000000000000000000000000a"
        first = "40000000000000000000000000000001"
        second = "40000000000000000000000000000002"
        _observe(self.path, attempt_id=first, logical_request_id=logical, attempt_ordinal=1,
                 headers={"x-requests-used": "100"})
        again = _observe(self.path, attempt_id=first, logical_request_id=logical, attempt_ordinal=1,
                         headers={"x-requests-last": "4", "x-requests-used": "100", "x-requests-remaining": "1"})
        self.assertTrue(again["duplicate"])
        self.assertTrue(again["filled_later_header"])
        _observe(self.path, attempt_id=second, logical_request_id=logical, attempt_ordinal=2,
                 headers={"x-requests-last": "1", "x-requests-used": "101", "x-requests-remaining": "1"},
                 observed_at=_stamp(13))
        _observe(self.path, attempt_id=first, logical_request_id=logical,
                 headers={"x-requests-last": "9", "x-requests-used": "100"})
        rows = _physical(self.path)
        self.assertEqual(len(rows), 2)
        filled = next(row for row in rows if row["attempt_id"] == first)
        self.assertEqual(filled["actual_header_cost"], 4)
        report = reconcile(self.path)["by_account_label"]["synthetic"]
        self.assertEqual(report["physical_attempts"], 2)
        self.assertEqual(report["retry_attempts"], 1)
        self.assertEqual(report["logical_requests"], 1)
        self.assertEqual(report["header_actual_total"], 5)

    def test_global_delta_is_not_a_consumer_bill_or_refund(self):
        _observe(self.path, source_job="synthetic-a", attempt_id="50000000000000000000000000000001",
                 headers={"x-requests-last": "3", "x-requests-used": "100", "x-requests-remaining": "1"},
                 observed_at=_stamp(12))
        _observe(self.path, source_job="synthetic-b", attempt_id="50000000000000000000000000000002",
                 headers={"x-requests-last": "1", "x-requests-used": "110", "x-requests-remaining": "1"},
                 observed_at=_stamp(13))
        report = reconcile(self.path)["by_account_label"]["synthetic"]
        self.assertEqual(report["header_actual_by_source_job"], {"synthetic-a": 3, "synthetic-b": 1})
        self.assertEqual(report["global_counter_delta_not_a_consumer_bill"], 10)
        self.assertEqual(report["unattributed_status"], "unattributed_unknown")
        self.assertEqual(report["unattributed_amount"], 6)
        self.assertIsNone(report["unattributed_assigned_to"])
        self.assertEqual(report["refunds_reported"], 0)
        self.assertNotEqual(report["header_actual_by_source_job"]["synthetic-b"], 10)

    def test_stale_header_and_month_reset_do_not_refund(self):
        _observe(self.path, attempt_id="60000000000000000000000000000001",
                 headers={"x-requests-last": "3", "x-requests-used": "100", "x-requests-remaining": "1"},
                 observed_at=_stamp(12))
        _observe(self.path, attempt_id="60000000000000000000000000000002", source_job="synthetic-b",
                 headers={"x-requests-last": "1", "x-requests-used": "90", "x-requests-remaining": "1"},
                 observed_at=_stamp(13))
        stale = reconcile(self.path)["by_account_label"]["synthetic"]
        self.assertIn("stale_or_out_of_order_or_external_unknown", stale["notes"])
        self.assertIsNone(stale["unattributed_amount"])
        self.assertEqual(stale["refunds_reported"], 0)
        self.assertEqual(stale["header_actual_by_source_job"]["synthetic-b"], 1)

        other = Path(self.tmp.name) / "month.sqlite"
        _observe(other, attempt_id="61000000000000000000000000000001",
                 headers={"x-requests-last": "2", "x-requests-used": "500", "x-requests-remaining": "1"},
                 observed_at=datetime(2026, 11, 30, 23, tzinfo=timezone.utc))
        _observe(other, attempt_id="61000000000000000000000000000002",
                 headers={"x-requests-last": "1", "x-requests-used": "10", "x-requests-remaining": "1"},
                 observed_at=datetime(2026, 12, 1, 0, 5, tzinfo=timezone.utc))
        reset = reconcile(other)["by_account_label"]["synthetic"]
        self.assertIn("possible_utc_month_reset_unknown", reset["notes"])
        self.assertEqual(reset["refunds_reported"], 0)
        self.assertIsNone(reset["global_counter_delta_not_a_consumer_bill"])

    def test_legacy_import_does_not_relabel_estimate_as_actual(self):
        when = _stamp()
        import_legacy_usage_row(
            source_job="aj-live", endpoint_template="/v4/sports/{sport}/odds", legacy_row_id=7,
            legacy_cost=0, sport="synthetic_sport", strategy="live", account_label="paid",
            observed_at=when, ledger_path=self.path)
        import_legacy_usage_row(
            source_job="aj-value", endpoint_template="/v4/sports/{sport}/events", legacy_row_id=8,
            legacy_cost=15, sport="synthetic_sport", strategy="value", account_label="paid",
            observed_at=when, ledger_path=self.path)
        import_legacy_usage_row(
            source_job="aj-live", endpoint_template="/v4/sports/{sport}/odds", legacy_row_id=7,
            legacy_cost=0, sport="synthetic_sport", strategy="live", account_label="paid",
            observed_at=when, ledger_path=self.path)
        state = read_state(self.path)
        self.assertEqual(len(state["attempts"]), 2)
        for row in state["attempts"]:
            self.assertIsNone(row["actual_header_cost"])
            self.assertEqual(row["actual_status"], "legacy_unlabeled")
            self.assertEqual(row["cost_basis"], "legacy_unlabeled_not_actual")
            self.assertEqual(row["is_physical"], 0)
        costs = sorted(row["legacy_cost_unlabeled"] for row in state["attempts"])
        self.assertEqual(costs, [0, 15])
        report = reconcile(self.path)["by_account_label"]["paid"]
        self.assertEqual(report["header_actual_total"], 0)
        self.assertEqual(report["legacy_imports"], 2)
        self.assertNotIn(15, report["header_actual_by_source_job"].values())

    def test_crash_partial_write_rolls_back(self):
        def boom(_conn):
            raise sqlite3.OperationalError("synthetic commit failure")

        with mock.patch("odds_accounting.ledger._commit", boom):
            result = _observe(self.path, attempt_id="70000000000000000000000000000001")
        self.assertFalse(result["stored"])
        self.assertFalse(self.path.exists() and _physical(self.path))

    def test_locked_unwritable_and_corrupt_do_not_raise(self):
        _observe(self.path, attempt_id="80000000000000000000000000000001")
        holder = sqlite3.connect(self.path, timeout=0.05)
        holder.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        locked = _observe(self.path, attempt_id="80000000000000000000000000000002")
        elapsed = time.monotonic() - started
        holder.execute("ROLLBACK")
        holder.close()
        self.assertFalse(locked["ok"])
        self.assertEqual(locked["reason"], "locked")
        self.assertLess(elapsed, 2.0)
        self.assertEqual(len(_physical(self.path)), 1)

        blocked = Path(self.tmp.name) / "not-a-dir"
        blocked.write_text("x")
        unwritable = observe_exchange(
            source_job="synthetic-a", endpoint_template="/v4/sports/{sport}/odds",
            estimated_cost=1, estimate_provenance="test", http_status=200,
            headers={"x-requests-last": "1"}, ledger_path=blocked / "ledger.sqlite", observed_at=_stamp())
        self.assertFalse(unwritable["ok"])
        self.assertEqual(unwritable["reason"], "unwritable")

        corrupt = Path(self.tmp.name) / "corrupt.sqlite"
        corrupt.write_bytes(b"this is not a database")
        bad = _observe(corrupt, attempt_id="80000000000000000000000000000003")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "corrupt")

    def test_cross_process_writers_and_duplicate_attempt(self):
        import multiprocessing
        from odds_accounting.mp_support import write_attempts, write_fixed

        ctx = multiprocessing.get_context("spawn")
        procs = [
            ctx.Process(target=write_attempts, args=(str(self.path), 12, "synthetic-a", 1, 2)),
            ctx.Process(target=write_attempts, args=(str(self.path), 12, "synthetic-b", 2, 3)),
        ]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(20)
            self.assertEqual(proc.exitcode, 0)
        rows = _physical(self.path)
        self.assertEqual(len(rows), 24)
        report = reconcile(self.path)["by_account_label"]["synthetic"]
        self.assertEqual(report["header_actual_by_source_job"], {"synthetic-a": 24, "synthetic-b": 36})
        self.assertEqual(len(report["header_actual_by_consumer"]), 2)

        shared = Path(self.tmp.name) / "once.sqlite"
        attempt = "90000000000000000000000000000001"
        dupes = [
            ctx.Process(target=write_fixed, args=(str(shared), attempt, "synthetic-a", "4")),
            ctx.Process(target=write_fixed, args=(str(shared), attempt, "synthetic-b", "4")),
        ]
        for proc in dupes:
            proc.start()
        for proc in dupes:
            proc.join(20)
            self.assertEqual(proc.exitcode, 0)
        self.assertEqual(len(_physical(shared)), 1)
        self.assertEqual(_physical(shared)[0]["actual_header_cost"], 4)

    def test_redaction_and_separate_labels(self):
        secret = "synthetic-" + "query-token"
        _observe(
            self.path,
            endpoint_path="https://api.the-odds-api.com/v4/sports/x/odds?apiKey=" + secret,
            endpoint_template=None,
            headers={
                "Authorization": "Bearer " + secret,
                "x-requests-last": "2",
                "x-requests-used": "10",
                "x-requests-remaining": "10",
            },
            markets=["h2h", secret],
            account_label=secret,
            body={"apiKey": secret},
            url="https://example.test?apiKey=" + secret,
            attempt_id="a0000000000000000000000000000001",
        )
        blob = self.path.read_bytes()
        self.assertNotIn(secret.encode(), blob)
        self.assertNotIn(b"apiKey", blob)
        self.assertNotIn(b"Bearer", blob)
        self.assertNotIn(b"example.test", blob)
        row = _physical(self.path)[0]
        self.assertEqual(row["endpoint_template"], "rejected-endpoint")
        self.assertIsNone(row["account_label"])
        self.assertNotIn(secret, row["markets"])
        self.assertEqual(row["actual_header_cost"], 2)

    def test_event_id_is_not_retained_and_request_count_is_not_credit_count(self):
        _observe(
            self.path,
            endpoint_path="/sports/basketball_nba/events/EVENTTOKEN123/odds",
            endpoint_template=None,
            sport=None,
            markets=["player_points", "player_rebounds"],
            regions=["us", "eu"],
            estimated_cost=4,
            headers={"x-requests-last": "0", "x-requests-used": "8", "x-requests-remaining": "1"},
            attempt_id="b0000000000000000000000000000001",
        )
        blob = self.path.read_bytes()
        self.assertNotIn(b"EVENTTOKEN123", blob)
        row = _physical(self.path)[0]
        self.assertEqual(row["endpoint_template"], "/v4/sports/{sport}/events/{event}/odds")
        self.assertEqual(row["sport"], "basketball_nba")
        self.assertEqual(row["request_shape"]["region_count"], 2)
        self.assertEqual(row["request_shape"]["market_count"], 2)
        self.assertEqual(row["actual_header_cost"], 0)
        grouped = reconcile(self.path)
        report = grouped["by_account_label"]["synthetic"]
        self.assertEqual(report["physical_attempts"], 1)
        self.assertEqual(report["header_actual_total"], 0)
        self.assertTrue(grouped["request_count_is_not_credit_count"])

    def test_naive_timestamp_is_not_stored(self):
        result = _observe(self.path, observed_at=datetime(2026, 11, 8, 12, 0))
        self.assertEqual(result["reason"], "naive_timestamp")
        self.assertFalse(result["stored"])

    def test_enforcement_is_disabled(self):
        self.assertFalse(ENFORCEMENT_ENABLED)
        self.assertFalse(ENFORCEMENT_IMPLEMENTED)
        for fn in (shared_cap_decision, priority_decision, polling_or_cache_decision):
            with self.assertRaises(NotImplementedError):
                fn()
        package = PKG / "odds_accounting"
        text = "\n".join(path.read_text() for path in package.glob("*.py"))
        self.assertNotIn("threading.Timer", text)
        self.assertNotIn("while True", text)
        self.assertNotIn("schedule.", text)


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.sqlite"
        os.environ["ODDS_ACCOUNTING_LEDGER"] = str(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: os.environ.pop("ODDS_ACCOUNTING_LEDGER", None))

    def test_marv_transports_keep_request_result_and_exception(self):
        from marv.data import oddsapi as feed
        from marv import parlay, sharpgap
        from marv.props import odds as props

        secret = "synthetic-" + "feed-key"
        body = [{"id": "event-1", "bookmakers": []}]
        seen = {}

        def fake_get(url, timeout=None, params=None):
            seen.setdefault("calls", []).append((url, timeout, dict(params)))
            return Response(200, {"x-requests-last": "2", "x-requests-remaining": "80",
                                  "x-requests-used": "20"}, body)

        with mock.patch("marv.data.oddsapi.requests.get", fake_get):
            self.assertEqual(feed.fetch(secret, "basketball_nba", "us,eu"), body)
        self.assertEqual(len(seen["calls"]), 1)
        url, timeout, params = seen["calls"][0]
        self.assertEqual(timeout, 30)
        self.assertEqual(params["apiKey"], secret)
        self.assertEqual(params["regions"], "us,eu")
        self.assertEqual(params["markets"], "h2h,spreads,totals")
        self.assertNotIn(secret.encode(), self.path.read_bytes())
        row = _physical(self.path)[0]
        self.assertEqual(row["source_job"], "unknown")
        self.assertEqual(row["estimated_cost"], 6)
        self.assertEqual(row["actual_header_cost"], 2)
        self.assertEqual(row["cost_basis"], "header")

        err = requests.ConnectionError("synthetic-down")

        def explode(*_args, **_kwargs):
            seen["calls"].append("exploded")
            raise err

        with mock.patch("marv.data.oddsapi.requests.get", explode):
            with self.assertRaises(requests.ConnectionError) as ctx:
                feed.fetch(secret, "basketball_nba")
        self.assertIs(ctx.exception, err)
        self.assertEqual(seen["calls"].count("exploded"), 1)

        sharp_body = [{"id": "gap"}]
        with mock.patch("marv.sharpgap.requests.get",
                        lambda url, timeout=None, params=None: Response(
                            200, {"x-requests-last": "2", "x-requests-remaining": "7"}, sharp_body)):
            self.assertEqual(sharpgap.fetch(secret, "nfl"), sharp_body)
        sharp = next(row for row in _physical(self.path) if row["source_job"] == "marv-sharpgap")
        self.assertEqual(sharp["estimated_cost"], 2)
        self.assertEqual(sharp["bookmakers"], ["bovada", "pinnacle"])
        self.assertEqual(sharp["sport"], "americanfootball_nfl")
        before = len(_physical(self.path))
        with mock.patch("marv.sharpgap.requests.get",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unknown sport requested"))):
            with self.assertRaises(KeyError):
                sharpgap.fetch(secret, "not-a-sport")
        self.assertEqual(len(_physical(self.path)), before)

        with mock.patch("requests.get", lambda url, timeout=None, params=None: Response(
                502, {"x-requests-last": "1"}, {"message": "synthetic"})):
            with self.assertRaises(requests.HTTPError):
                parlay._fetch_bovada(secret, "basketball_nba")
        parlay_row = next(row for row in _physical(self.path) if row["source_job"] == "marv-parlay")
        self.assertEqual(parlay_row["http_status"], 502)
        self.assertEqual(parlay_row["actual_header_cost"], 1)
        self.assertEqual(parlay_row["estimated_cost"], 3)

        budget = props.Budget(100)
        markets = ["player_points", "player_rebounds", "player_assists", "player_threes"]
        with mock.patch("marv.props.odds.requests.get", lambda url, timeout=None, params=None: Response(
                200, {"x-requests-last": "0", "x-requests-remaining": "4"}, {"id": "e", "bookmakers": []})):
            result = props.event_props(secret, "basketball_nba", "event-1", markets, "bovada", budget)
        self.assertEqual(result["id"], "e")
        # The string "0" is present, so the existing charge uses 0. A missing header
        # still falls back to the estimate. That line is intentionally unchanged.
        self.assertEqual(budget.used, 0)
        props_row = next(row for row in _physical(self.path)
                         if row["endpoint_template"] == "/v4/sports/{sport}/events/{event}/odds"
                         and row["outcome"] == "success")
        self.assertEqual(props_row["actual_header_cost"], 0)
        self.assertEqual(props_row["estimated_cost"], 4)
        self.assertNotIn(b"event-1", self.path.read_bytes())
        missing_budget = props.Budget(100)
        with mock.patch("marv.props.odds.requests.get", lambda url, timeout=None, params=None: Response(
                200, {"x-requests-remaining": "4"}, {"id": "e2", "bookmakers": []})):
            props.event_props(secret, "basketball_nba", "event-2", markets, "bovada", missing_budget)
        self.assertEqual(missing_budget.used, 4)
        missing = next(row for row in _physical(self.path) if row["actual_status"] == "missing")
        self.assertIsNone(missing["actual_header_cost"])
        self.assertEqual(missing["cost_basis"], "estimate_only_not_actual")
        self.assertNotIn(b"event-2", self.path.read_bytes())

    def test_props_errors_cache_skip_and_historical_estimate(self):
        from marv.props import odds as props

        secret = "synthetic-" + "props-key"
        calls = []

        def fake_get(url, timeout=None, params=None):
            calls.append((url, timeout, dict(params)))
            if "/historical/" in url and "/odds" in url:
                return Response(200, {"x-requests-last": "30", "x-requests-remaining": "1"},
                                {"data": {"id": "hist"}})
            if url.endswith("/events"):
                return Response(200, {"x-requests-last": "0"}, [])
            return Response(502, {}, {"message": "down"})

        with mock.patch("marv.props.odds.requests.get", fake_get):
            self.assertEqual(props.events(secret, "basketball_nba"), [])
            with self.assertRaises(requests.HTTPError):
                props.event_props(secret, "basketball_nba", "event-9", ["player_points"], "bovada", None)
            cache = Path(self.tmp.name) / "cache"
            cache.mkdir()
            cached = cache / "events_basketball_nba_2026-11-08T12.json"
            cached.write_text("[]")
            before = len(calls)
            self.assertEqual(
                props.historical_events(secret, "basketball_nba", "2026-11-08T12:00:00Z", cache, props.Budget(50)),
                [])
            self.assertEqual(len(calls), before)
            hist = props.historical_event_props(
                secret, "basketball_nba", "event-9", "2026-11-08T12:00:00Z",
                ["player_points", "player_rebounds", "player_assists", "player_threes"],
                "bovada", cache, props.Budget(100))
        self.assertEqual(hist, {"id": "hist"})
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1], 30)
        self.assertEqual(calls[0][2]["apiKey"], secret)
        rows = _physical(self.path)
        self.assertTrue(any(row["endpoint_template"] == "/v4/sports/{sport}/events"
                            and row["actual_header_cost"] == 0 for row in rows))
        self.assertTrue(any(row["http_status"] == 502 and row["actual_header_cost"] is None
                            and row["cost_basis"] == "unknown_never_assert_zero" for row in rows))
        historical = next(row for row in rows if row["endpoint_template"].startswith("/v4/historical/"))
        self.assertEqual(historical["estimated_cost"], 40)
        self.assertEqual(historical["actual_header_cost"], 30)
        self.assertTrue(historical["request_shape"]["historical"])
        self.assertNotIn(b"2026-11-08T12:00:00Z", self.path.read_bytes())

        calls.clear()
        budget = props.Budget(1)
        with mock.patch("marv.props.odds.requests.get", fake_get):
            with self.assertRaisesRegex(RuntimeError, "^credit cap reached$"):
                props.event_props(secret, "basketball_nba", "event-9", ["player_points", "player_rebounds"],
                                  "bovada", budget)
        self.assertEqual(calls, [])
        self.assertEqual(budget.used, 0)
        skipped = [row for row in read_state(self.path)["attempts"] if row["outcome"] == "skipped_preflight"]
        self.assertEqual(len(skipped), 1)
        self.assertIsNone(skipped[0]["actual_header_cost"])

    def test_observer_failure_does_not_change_marv_outcome(self):
        from marv.data import oddsapi as feed

        def explode(**_kwargs):
            raise RuntimeError("observer failed")

        body = [{"id": "ok"}]
        with mock.patch("odds_accounting.boundary.observe_exchange", explode):
            with mock.patch("marv.data.oddsapi.requests.get",
                            lambda *args, **kwargs: Response(200, {"x-requests-last": "3"}, body)):
                self.assertEqual(feed.fetch("synthetic-key", "basketball_nba"), body)
            err = requests.HTTPError("keep")
            def fail(*_args, **_kwargs):
                raise err
            with mock.patch("marv.data.oddsapi.requests.get", fail):
                with self.assertRaises(requests.HTTPError) as ctx:
                    feed.fetch("synthetic-key", "basketball_nba")
        self.assertIs(ctx.exception, err)

    def test_aj_lanes_fallback_budget_and_legacy_cost_column(self):
        from ajbet.db import DB
        from ajbet.oddsapi import BudgetExceeded, OddsAPI
        from ajbet.providers.theoddsapi import TheOddsAPIProvider

        secret_paid = "synthetic-" + "paid-key"
        secret_free = "synthetic-" + "free-key"
        db = DB(":memory:")
        cfg = {"odds_api": {
            "regions": "us",
            "bookmakers": ["pinnacle", "draftkings"],
            "markets": "h2h,spreads,totals",
            "daily_credit_cap": 3000,
            "monthly_plan_credits": 100000,
            "stop_at_remaining": 50,
        }}
        calls = []

        def transport(url, params):
            calls.append((url, dict(params)))
            if url.endswith("/odds") and params["apiKey"] == secret_paid and len(calls) == 1:
                return 401, {"x-requests-last": "0", "x-requests-used": "10", "x-requests-remaining": "90"}, {"message": "no"}
            return 200, {"x-requests-last": "2", "x-requests-used": "12", "x-requests-remaining": "88"}, [{"id": "game"}]

        client = OddsAPI(cfg, db, transport=transport, api_key=[
            {"label": "paid", "key": secret_paid}, {"label": "free", "key": secret_free}])
        provider = TheOddsAPIProvider(cfg, db, {"enabled": True}, client=client)
        self.assertEqual(provider.odds("basketball_nba", "h2h,spreads", strategy="value"), [{"id": "game", "_provider": "theoddsapi"}])
        self.assertEqual(len(calls), 2)
        self.assertEqual(client.calls, 2)
        self.assertEqual(calls[0][1]["apiKey"], secret_paid)
        self.assertEqual(calls[1][1]["apiKey"], secret_free)
        rows = _physical(self.path)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["source_job"] for row in rows}, {"aj-value"})
        rows = sorted(rows, key=lambda row: row["attempt_ordinal"])
        self.assertEqual([row["attempt_ordinal"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["logical_request_id"], rows[1]["logical_request_id"])
        self.assertEqual(rows[0]["http_status"], 401)
        self.assertEqual(rows[0]["actual_header_cost"], 0)
        self.assertEqual(rows[1]["actual_header_cost"], 2)
        self.assertEqual(rows[1]["account_label"], "free")
        usage = db.conn.execute("SELECT cost, status, key_label FROM api_usage ORDER BY id").fetchall()
        self.assertEqual([(row["status"], row["key_label"]) for row in usage], [(401, "paid"), (200, "free")])
        blob = self.path.read_bytes()
        self.assertNotIn(secret_paid.encode(), blob)
        self.assertNotIn(secret_free.encode(), blob)

        empty = OddsAPI(cfg, db, transport=lambda url, params: (200, {}, []), api_key=[{"label": "paid", "key": secret_paid}])
        self.assertEqual(empty.events("basketball_nba", strategy="value"), [])
        free = next(row for row in _physical(self.path) if row["endpoint_template"] == "/v4/sports/{sport}/events")
        self.assertEqual(free["estimated_cost"], 0)
        self.assertIsNone(free["actual_header_cost"])
        self.assertEqual(free["cost_basis"], "estimate_only_not_actual")
        free_usage = db.conn.execute(
            "SELECT cost FROM api_usage WHERE endpoint='events' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(free_usage["cost"], 0)

        missing = OddsAPI(dict(cfg, odds_api=dict(cfg["odds_api"], daily_credit_cap=1)), db,
                          transport=lambda *_args: (_ for _ in ()).throw(AssertionError("request")),
                          api_key=[{"label": "paid", "key": secret_paid}])
        with self.assertRaises(BudgetExceeded) as ctx:
            missing.live_odds("basketball_nba", strategy="live")
        self.assertIn("daily cap", str(ctx.exception))
        skipped = [row for row in read_state(self.path)["attempts"] if row["outcome"] == "skipped_preflight"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["source_job"], "aj-live")
        self.assertIsNone(skipped[0]["actual_header_cost"])

        def down(_url, _params):
            raise TimeoutError("down " + secret_paid)

        errored = OddsAPI(cfg, db, transport=down, api_key=[{"label": "paid", "key": secret_paid}])
        before = errored.calls
        self.assertIsNone(errored.live_odds("basketball_nba", strategy="live_props"))
        self.assertEqual(errored.calls, before)
        note = db.conn.execute(
            "SELECT note, cost FROM api_usage WHERE note LIKE 'ERROR %' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("***", note["note"])
        self.assertNotIn(secret_paid, note["note"])
        self.assertIsNone(note["cost"])
        failed = next(row for row in _physical(self.path) if row["outcome"] == "transport_exception")
        self.assertEqual(failed["source_job"], "aj-live_props")
        self.assertIsNone(failed["actual_header_cost"])
        self.assertNotIn(secret_paid.encode(), self.path.read_bytes())

        unknown = OddsAPI(cfg, db, transport=lambda *_a: (502, {}, {"message": "synthetic"}),
                          api_key=[{"label": "paid", "key": secret_paid}])
        self.assertIsNone(unknown.event_odds("basketball_nba", "event-7", "player_points,player_rebounds",
                                            strategy="props_nba"))
        billed = db.conn.execute(
            "SELECT cost, status FROM api_usage WHERE strategy='props_nba' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(billed["status"], 502)
        self.assertEqual(billed["cost"], 0)
        nba = next(row for row in _physical(self.path) if row["source_job"] == "aj-props_nba")
        self.assertIsNone(nba["actual_header_cost"])
        self.assertEqual(nba["cost_basis"], "unknown_never_assert_zero")
        self.assertEqual(nba["estimated_cost"], 2)
        self.assertNotIn(b"event-7", self.path.read_bytes())

        refused = OddsAPI(cfg, db, transport=lambda *_a: (_ for _ in ()).throw(AssertionError("historical request")),
                          api_key=[{"label": "paid", "key": secret_paid}])
        with self.assertRaisesRegex(ValueError, "historical endpoints are not used"):
            refused._get("/historical/sports/basketball_nba/odds", {}, 10, "basketball_nba", "odds", "close")
        self.assertTrue(any(row["outcome"] == "refused_historical" and row["source_job"] == "aj-close"
                            for row in read_state(self.path)["attempts"]))

    def test_aj_observer_failure_preserves_budget_exception_and_none_return(self):
        from ajbet.db import DB
        from ajbet.oddsapi import BudgetExceeded, OddsAPI

        db = DB(":memory:")
        cfg = {"odds_api": {"daily_credit_cap": 1, "monthly_plan_credits": 100000, "bookmakers": ["pinnacle"]}}

        def boom(**_kwargs):
            raise RuntimeError("ledger down")

        with mock.patch("odds_accounting.boundary.observe_exchange", boom):
            client = OddsAPI(cfg, db, transport=lambda *_a: (_ for _ in ()).throw(AssertionError("called")),
                             api_key=[{"label": "paid", "key": "synthetic-paid"}])
            with self.assertRaises(BudgetExceeded):
                client.live_odds("basketball_nba", strategy="team_props")
            def down(_url, _params):
                raise TimeoutError("still the provider error")
            client = OddsAPI({"odds_api": {"monthly_plan_credits": 100000, "bookmakers": ["pinnacle"]}}, db,
                             transport=down, api_key=[{"label": "paid", "key": "synthetic-paid"}])
            self.assertIsNone(client.live_odds("basketball_nba", strategy="ncaab_alias_not_a_lane"))
        self.assertTrue(any("ERROR" in row["note"] for row in db.conn.execute("SELECT note FROM api_usage")))

    def test_shadow_budget_and_replay_allowlist_stay_unchanged(self):
        from datetime import timezone as tz
        from shadow_collector.credits import DEFAULT_DAILY_CAP, CreditError, Ledger
        from shadow_collector.odds_request import CollectorError, _odds_request, replay_child_env

        self.assertEqual(DEFAULT_DAILY_CAP, 100)
        credits = Path(self.tmp.name) / "usage.json"
        ledger = Ledger(credits, cap=100, now=datetime(2026, 11, 8, 15, tzinfo=tz.utc))
        with self.assertRaises(CreditError):
            ledger.reserve("event_odds", 101, "too many")
        self.assertEqual(ledger.snapshot()["used"], 0)
        self.assertFalse(ledger.snapshot()["fail_closed"])

        secret = "synthetic-" + "shadow-key"
        seen = []

        def transport(url):
            seen.append(url)
            return 200, {"x-requests-last": "1", "x-requests-remaining": "90"}, b'{"id":"g"}'

        with self.assertRaises(CreditError):
            _odds_request(ledger, transport, secret, "/sports/basketball_nba/events/EVENTTOKEN/odds",
                          {"markets": "player_points,player_rebounds,player_assists,player_threes",
                           "bookmakers": "bovada", "oddsFormat": "american"},
                          "event_odds", 4, "synthetic-game")
        self.assertEqual(len(seen), 1)
        self.assertIn(secret, seen[0])
        snap = ledger.snapshot()
        self.assertTrue(snap["fail_closed"])
        self.assertEqual(snap["used"], 4)
        self.assertNotIn(secret.encode(), self.path.read_bytes())
        self.assertNotIn(b"EVENTTOKEN", self.path.read_bytes())
        observed = _physical(self.path)[0]
        self.assertEqual(observed["source_job"], "shadow-collector")
        self.assertEqual(observed["actual_header_cost"], 1)
        self.assertEqual(observed["estimated_cost"], 4)
        self.assertEqual(observed["shadow_reservation_id"], "2026-11-08-1")
        report = reconcile(self.path)
        self.assertNotIn("4", json.dumps(report["by_account_label"]["unspecified"]["header_actual_by_source_job"]))
        self.assertEqual(report["by_account_label"]["unspecified"]["header_actual_total"], 1)

        fresh = Ledger(Path(self.tmp.name) / "free.json", cap=100, now=datetime(2026, 11, 8, tzinfo=tz.utc))
        body = _odds_request(fresh, lambda _url: (200, {"x-requests-last": "0", "x-requests-remaining": "80"}, b"[]"),
                             secret, "/sports/basketball_nba/events", {}, "events", 0, "nba")
        self.assertEqual(body, [])
        self.assertEqual(fresh.snapshot()["used"], 0)
        self.assertFalse(fresh.snapshot()["fail_closed"])

        blocked = Ledger(Path(self.tmp.name) / "missing.json", cap=100, now=datetime(2026, 11, 8, tzinfo=tz.utc))
        with self.assertRaises(CreditError):
            _odds_request(blocked, lambda _url: (502, {}, b"{}"), secret,
                          "/sports/basketball_nba/events", {}, "events", 0, "nba")
        self.assertTrue(blocked.snapshot()["fail_closed"])
        unknown = next(row for row in _physical(self.path) if row["http_status"] == 502)
        self.assertIsNone(unknown["actual_header_cost"])
        self.assertEqual(unknown["cost_basis"], "unknown")

        original = TimeoutError("provider timeout")

        def fail(_url):
            raise original

        failing = Ledger(Path(self.tmp.name) / "down.json", cap=100, now=datetime(2026, 11, 8, tzinfo=tz.utc))
        blocker = Path(self.tmp.name) / "blocked-parent"
        blocker.write_text("x")
        os.environ["ODDS_ACCOUNTING_LEDGER"] = str(blocker / "ledger.sqlite")
        try:
            # The unchanged credit ledger fail-closes inside the transport except.
            # CreditError is raised before the CollectorError line; the provider
            # error remains the context, and a bad observe path does not replace it.
            with self.assertRaises(CreditError) as ctx:
                _odds_request(failing, fail, secret, "/sports/basketball_nba/events", {}, "events", 0, "nba")
        finally:
            os.environ["ODDS_ACCOUNTING_LEDGER"] = str(self.path)
        self.assertIs(ctx.exception.__context__, original)
        self.assertTrue(failing.snapshot()["fail_closed"])
        self.assertEqual(failing.snapshot()["used"], 0)

        held = Ledger(Path(self.tmp.name) / "held.json", cap=100, now=datetime(2026, 11, 8, tzinfo=tz.utc))
        held.reserve("events", 0, "unfinished")
        self.assertTrue(held.fail_unfinished()["fail_closed"])
        nxt = Ledger(Path(self.tmp.name) / "held.json", cap=100, now=datetime(2026, 11, 9, tzinfo=tz.utc))
        self.assertEqual(nxt.snapshot()["used"], 0)
        self.assertEqual(nxt.snapshot()["date"], "2026-11-09")

        child = replay_child_env({"PATH": "/usr/bin", "ODDS_API_KEY": secret, "HOME": "/tmp/home",
                                  "LIVE_PROPS_SEND": "on"})
        self.assertEqual(child["PATH"], "/usr/bin")
        self.assertEqual(child["LIVE_PROPS_SEND"], "off")
        self.assertEqual(child["LIVE_PROPS_ENGINE"], "on")
        self.assertNotIn("ODDS_API_KEY", child)
        self.assertNotIn("HOME", child)
        for open_ledger in (ledger, fresh, blocked, failing, held, nxt):
            open_ledger.close()

    def test_verbatim_budget_files_and_hooks_do_not_call_enforcement(self):
        files = {
            PKG / "integrations/shadow_collector/credits.py": CREDITS_SHA,
            PKG / "integrations/ajbet/db.py": DB_SHA,
            PKG / "integrations/ajbet/sat_priority.py": SAT_SHA,
            PKG / "integrations/ajbet/providers/base.py": BASE_SHA,
            PKG / "integrations/ajbet/providers/theoddsapi.py": PROVIDER_SHA,
        }
        for path, digest in files.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        changed = PKG / "integrations/ajbet/oddsapi.py"
        self.assertNotEqual(hashlib.sha256(changed.read_bytes()).hexdigest(), AJ_ODDS_BEFORE)
        text = changed.read_text()
        self.assertIn("last if last is not None else (cost if status == 200 else 0)", text)
        self.assertIn("raise BudgetExceeded(why)", text)
        charge = (REPO / "marv/props/odds.py").read_text()
        self.assertIn('self.used += int(resp.headers.get("x-requests-last") or estimate)', charge)
        for path in (
            REPO / "marv/data/oddsapi.py",
            REPO / "marv/props/odds.py",
            REPO / "marv/sharpgap.py",
            REPO / "marv/parlay.py",
            changed,
            PKG / "integrations/shadow_collector/odds_request.py",
        ):
            body = path.read_text()
            self.assertNotIn("shared_cap_decision", body)
            self.assertNotIn("priority_decision", body)
            self.assertNotIn("polling_or_cache_decision", body)

    def test_fixture_cases(self):
        cases = json.loads((PKG / "fixtures/synthetic-response-cases.json").read_text())
        self.assertTrue(cases["synthetic"])
        for index, case in enumerate(cases["cases"], start=1):
            if case["case"] in {"global_counter_not_job_delta", "observer_storage_failure"}:
                continue
            scope = case["scope"]
            result_path = Path(self.tmp.name) / f"fix-{case['case']}.sqlite"
            status = case.get("status")
            headers = case.get("headers")
            transport = None
            if case.get("transport_exception"):
                transport = TimeoutError("synthetic")
                status = None
                headers = None
            observe_exchange(
                source_job="synthetic-a",
                endpoint_template=scope["endpoint_template"],
                sport=scope.get("sport", "synthetic_sport"),
                markets=["mkt"] * int(scope.get("market_count", 0)),
                regions=["us"] * int(scope.get("region_units", 0)),
                estimated_cost=scope.get("estimate_credits"),
                estimate_provenance="fixture",
                http_status=status,
                headers=headers,
                transport_error=transport,
                ledger_path=result_path,
                observed_at=_stamp(),
                attempt_id=f"{index:032x}",
            )
            expected = case["expected_observation"]
            row = read_state(result_path)["attempts"][0]
            self.assertEqual(row["actual_header_cost"], expected.get("actual_header_cost"), case["case"])
            self.assertEqual(row["cost_basis"], expected["basis"], case["case"])
            if "estimate" in expected:
                self.assertEqual(row["estimated_cost"], expected["estimate"], case["case"])

        blocked = Path(self.tmp.name) / "file-parent"
        blocked.write_text("x")
        failed = observe_exchange(
            source_job="synthetic-a", endpoint_template="/v4/sports/{sport}/odds",
            estimated_cost=3, estimate_provenance="fixture", http_status=200,
            headers={"x-requests-last": "1"}, ledger_path=blocked / "nope.sqlite", observed_at=_stamp())
        self.assertFalse(failed["stored"])


class CoverageTests(unittest.TestCase):
    def test_uncovered_callers_are_named(self):
        text = (PKG / "COVERAGE.md").read_text()
        for name in (
            "forex_bots/masterbot_oanda/app.py",
            "forex_bots/masterbot_oanda/live_odds_engine.py",
            "tools/fetch_bovada_nfl.py",
            "muse-prop1/bovada.py",
            "ajbet/parlayapi.py",
            "ajbet/providers/oddsapiio.py",
            "bulk legacy ESPN replay fixtures",
        ):
            self.assertIn(name, text)
        self.assertIn("not claimed complete", text.lower())


if __name__ == "__main__":
    unittest.main()
