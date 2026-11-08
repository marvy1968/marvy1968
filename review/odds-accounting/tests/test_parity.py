"""Second-pass parity. Synthetic transports only."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import requests

PKG = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(PKG / "integrations"))
sys.path.insert(0, str(REPO))

from odds_accounting.headers import parse_count
from odds_accounting.ledger import WRITE_ATTEMPTS, read_state
from odds_accounting.reconcile import reconcile

SECRET = "synthetic-" + "parity-secret"


def _stamp(month=11, day=8, hour=12):
    return datetime(2026, month, day, hour, 0, tzinfo=timezone.utc)


class Hop:
    def __init__(self, status, headers, history=None):
        self.status_code = status
        self.headers = headers
        self.history = history or []
        self.reason = "synthetic"
        self._body = [{"id": "body-kept"}]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} synthetic", response=self)

    def json(self):
        return self._body


def _rows(path):
    state = read_state(Path(path))
    return [row for row in state["attempts"] if row["is_physical"] == 1]


class ParserAndReconcileTests(unittest.TestCase):
    def test_numeric_bounds_do_not_keep_malformed_text(self):
        huge = "9" * 10000 + SECRET
        started = time.monotonic()
        value, status = parse_count(huge)
        self.assertLess(time.monotonic() - started, 0.05)
        self.assertIsNone(value)
        self.assertEqual(status, "out_of_range")
        self.assertEqual(parse_count(-5), (None, "negative_rejected"))
        self.assertEqual(parse_count(1_000_000_000), (None, "out_of_range"))
        self.assertEqual(parse_count("0"), (0, "header"))

        class Bad:
            def __str__(self):
                return "https://example.test?apiKey=" + SECRET

        self.assertEqual(parse_count(Bad()), (None, "unparseable"))
        from odds_accounting.boundary import observe_exchange
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "leak.sqlite"
        observe_exchange(
            source_job="synthetic-a", endpoint_template="/v4/sports/{sport}/odds", sport="synthetic_sport",
            estimated_cost=1, estimate_provenance="test", http_status=200,
            headers={"x-requests-last": ("nope-" + SECRET), "x-requests-used": "1" * 80,
                     "Authorization": "Bearer " + SECRET},
            ledger_path=path, observed_at=_stamp(), attempt_id=f"{9:032x}",
        )
        blob = path.read_bytes()
        self.assertNotIn(SECRET.encode(), blob)
        self.assertNotIn(b"Bearer", blob)
        self.assertNotIn(b"nope-", blob)

    def test_month_increase_is_not_attributed(self):
        from odds_accounting.boundary import observe_exchange
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "ledger.sqlite"
        for index, (month, day, used, actual, job) in enumerate((
            (11, 30, 100, 1, "synthetic-a"),
            (12, 1, 103, 2, "synthetic-b"),
        ), start=1):
            observe_exchange(
                source_job=job, endpoint_template="/v4/sports/{sport}/odds", sport="synthetic_sport",
                markets=["h2h"], regions=["us"], estimated_cost=3, estimate_provenance="test",
                account_label="synthetic", http_status=200,
                headers={"x-requests-last": str(actual), "x-requests-used": str(used), "x-requests-remaining": "1"},
                attempt_id=f"{index:032x}", ledger_path=path,
                observed_at=_stamp(month, day, 12),
            )
        report = reconcile(path)["by_account_label"]["synthetic"]
        self.assertEqual(report["header_actual_by_source_job"], {"synthetic-a": 1, "synthetic-b": 2})
        self.assertIsNone(report["global_counter_delta_not_a_consumer_bill"])
        self.assertEqual(report["unattributed_status"], "unknown")
        self.assertIn("possible_utc_month_reset_unknown", report["notes"])
        self.assertIsNone(report["unattributed_assigned_to"])
        self.assertEqual(report["refunds_reported"], 0)

    def test_observer_wait_is_bounded_and_has_no_sleep(self):
        self.assertEqual(WRITE_ATTEMPTS, 4)
        package = "\n".join(path.read_text() for path in (PKG / "odds_accounting").glob("*.py"))
        self.assertNotIn("time.sleep", package)
        self.assertNotIn("sleep(", (REPO / "marv/data/odds_observe.py").read_text())
        session = requests.Session()
        self.assertEqual(int(session.get_adapter("https://").max_retries.total), 0)


class TransportParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.sqlite"
        os.environ["ODDS_ACCOUNTING_LEDGER"] = str(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: os.environ.pop("ODDS_ACCOUNTING_LEDGER", None))

    def _feed(self, result):
        from marv.data import oddsapi as feed
        seen = []

        def fake(url, timeout=None, params=None):
            seen.append((url, timeout, dict(params)))
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch("marv.data.oddsapi.requests.get", fake):
            if isinstance(result, Exception) or getattr(result, "status_code", 200) >= 400:
                with self.assertRaises(type(result) if isinstance(result, Exception) else requests.HTTPError) as ctx:
                    feed.fetch(SECRET, "basketball_nba", "us")
                if isinstance(result, Exception):
                    self.assertIs(ctx.exception, result)
            else:
                self.assertEqual(feed.fetch(SECRET, "basketball_nba", "us"), result.json())
        return seen

    def test_feed_header_and_http_matrix(self):
        cases = (
            (200, {"x-requests-last": "2", "x-requests-used": "9", "x-requests-remaining": "1"}, 2, "header"),
            (200, {"x-requests-used": "9"}, None, "estimate_only_not_actual"),
            (200, {"x-requests-last": "0"}, 0, "header"),
            (200, {"x-requests-last": "bad"}, None, "unparseable"),
            (401, {"x-requests-last": "1"}, 1, "header"),
            (429, {"x-requests-last": "1"}, 1, "header"),
            (502, {}, None, "unknown_never_assert_zero"),
        )
        for status, headers, actual, basis in cases:
            os.environ["ODDS_ACCOUNTING_LEDGER"] = str(self.path)
            self.path.unlink(missing_ok=True)
            seen = self._feed(Hop(status, headers))
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0][1], 30)
            self.assertEqual(seen[0][2]["apiKey"], SECRET)
            self.assertEqual(seen[0][2]["markets"], "h2h,spreads,totals")
            self.assertEqual(seen[0][2]["regions"], "us")
            row = _rows(self.path)[0]
            self.assertEqual(row["actual_header_cost"], actual)
            self.assertEqual(row["cost_basis"], basis)
            self.assertEqual(row["source_job"], "unknown")
            blob = self.path.read_bytes()
            self.assertNotIn(SECRET.encode(), blob)
            self.assertNotIn(b"the-odds-api.com", blob)
        err = requests.Timeout("synthetic-timeout")
        self.path.unlink(missing_ok=True)
        seen = self._feed(err)
        self.assertEqual(len(seen), 1)
        self.assertEqual(_rows(self.path)[0]["outcome"], "transport_exception")
        self.assertIsNone(_rows(self.path)[0]["actual_header_cost"])

    def test_redirect_is_one_library_call_and_two_attempts(self):
        from marv.data import oddsapi as feed
        hop = Hop(302, {"Location": "https://evil.test/?apiKey=" + SECRET, "x-requests-last": "0"})
        final = Hop(200, {"x-requests-last": "2", "x-requests-used": "4", "x-requests-remaining": "1"}, [hop])
        seen = []

        def fake(url, timeout=None, params=None):
            seen.append(1)
            return final

        with mock.patch("marv.data.oddsapi.requests.get", fake):
            self.assertEqual(feed.fetch(SECRET, "basketball_nba"), final.json())
            self.assertEqual(feed.fetch(SECRET, "basketball_nba"), final.json())
        self.assertEqual(seen, [1, 1])
        rows = _rows(self.path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(row["http_status"] for row in rows), [200, 302])
        self.assertEqual(rows[0]["logical_request_id"], rows[1]["logical_request_id"])
        blob = self.path.read_bytes()
        self.assertNotIn(b"evil.test", blob)
        self.assertNotIn(SECRET.encode(), blob)

    def test_sharpgap_parlay_and_props_keep_shape(self):
        from marv import parlay, sharpgap
        from marv.props import odds as props
        for label, caller in (
            ("marv-sharpgap", lambda hop: sharpgap.fetch(SECRET, "nfl")),
            ("marv-parlay", lambda hop: parlay._fetch_bovada(SECRET, "basketball_nba")),
        ):
            self.path.unlink(missing_ok=True)
            hop = Hop(200, {"x-requests-last": "0", "x-requests-remaining": "3"})
            seen = []

            def fake(url, timeout=None, params=None, _seen=seen):
                _seen.append((timeout, dict(params)))
                return hop

            target = "marv.sharpgap.requests.get" if label == "marv-sharpgap" else "requests.get"
            with mock.patch(target, fake):
                caller(hop)
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0][0], 30)
            self.assertEqual(seen[0][1]["apiKey"], SECRET)
            row = _rows(self.path)[0]
            self.assertEqual(row["source_job"], label)
            self.assertEqual(row["actual_header_cost"], 0)
            self.assertNotIn(SECRET.encode(), self.path.read_bytes())

        self.path.unlink(missing_ok=True)
        err = requests.ConnectionError("still-me")
        with mock.patch("marv.props.odds.requests.get", side_effect=err):
            with self.assertRaises(requests.ConnectionError) as ctx:
                props.event_props(SECRET, "basketball_nba", "event-9", ["player_points"], "bovada", None,
                                  source_job="marv-props-nba")
        self.assertIs(ctx.exception, err)
        self.assertEqual(_rows(self.path)[0]["source_job"], "marv-props-nba")
        self.assertNotIn(b"event-9", self.path.read_bytes())

        cache = Path(self.tmp.name) / "cache"
        cache.mkdir()
        (cache / "props_event-9.json").write_text('{"cached": true}')
        (cache / "events_basketball_nba_2026-11-08T12.json").write_text("[]")
        with mock.patch("marv.props.odds.requests.get", side_effect=AssertionError("cache hit requested")):
            self.assertEqual(props.historical_event_props(
                SECRET, "basketball_nba", "event-9", "2026-11-08T12:00:00Z", ["player_points"],
                "bovada", cache, props.Budget(10), source_job="marv-props-nba"), {"cached": True})
            self.assertEqual(props.historical_events(
                SECRET, "basketball_nba", "2026-11-08T12:00:00Z", cache, props.Budget(10),
                source_job="marv-props-nba"), [])
        self.assertEqual(len(_rows(self.path)), 1)

    def test_ledger_faults_do_not_change_feed_or_call_enforcement(self):
        from marv.data import oddsapi as feed
        import odds_accounting.enforcement as enforcement
        body = Hop(200, {"x-requests-last": "2", "x-requests-used": "2", "x-requests-remaining": "2"})
        calls = []

        def fake(url, timeout=None, params=None):
            calls.append(dict(params))
            return body

        def boom(*_args, **_kwargs):
            raise NotImplementedError("must not run")

        with mock.patch("marv.data.oddsapi.requests.get", fake), \
                mock.patch.object(enforcement, "shared_cap_decision", boom), \
                mock.patch.object(enforcement, "priority_decision", boom), \
                mock.patch.object(enforcement, "polling_or_cache_decision", boom):
            self.assertEqual(feed.fetch(SECRET, "basketball_nba", source_job="marv-sources"), body.json())
        self.assertEqual(len(calls), 1)
        self.assertEqual(_rows(self.path)[0]["source_job"], "marv-sources")

        holder = sqlite3.connect(self.path)
        holder.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with mock.patch("marv.data.oddsapi.requests.get", fake):
            self.assertEqual(feed.fetch(SECRET, "basketball_nba", source_job="marv-board"), body.json())
        self.assertLess(time.monotonic() - started, 2.0)
        holder.execute("ROLLBACK")
        holder.close()
        self.assertEqual(len(calls), 2)

        blocked = Path(self.tmp.name) / "blocked"
        blocked.write_text("x")
        os.environ["ODDS_ACCOUNTING_LEDGER"] = str(blocked / "ledger.sqlite")
        with mock.patch("marv.data.oddsapi.requests.get", fake):
            self.assertEqual(feed.fetch(SECRET, "basketball_nba"), body.json())
        corrupt = Path(self.tmp.name) / "corrupt.sqlite"
        corrupt.write_bytes(b"not a database")
        os.environ["ODDS_ACCOUNTING_LEDGER"] = str(corrupt)
        with mock.patch("marv.data.oddsapi.requests.get", fake):
            self.assertEqual(feed.fetch(SECRET, "basketball_nba"), body.json())
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0]["apiKey"], SECRET)

    def test_aj_redirect_retry_and_session_does_not_retry_status(self):
        from ajbet.db import DB
        from ajbet.oddsapi import OddsAPI, requests_transport
        from ajbet.providers.theoddsapi import TheOddsAPIProvider
        db = DB(":memory:")
        cfg = {"odds_api": {"bookmakers": ["pinnacle"], "markets": "h2h", "monthly_plan_credits": 100000,
                            "daily_credit_cap": 3000}}
        hop = Hop(302, {"Location": "https://evil.test/a?apiKey=" + SECRET, "x-requests-last": "0"})
        final = Hop(200, {"x-requests-last": "1", "x-requests-used": "8", "x-requests-remaining": "1"})
        final.history = [hop]
        final._body = [{"id": "game"}]
        seen = []

        def fake_get(self, url, params=None, timeout=None, **kwargs):
            seen.append((url, dict(params), timeout, kwargs.get("allow_redirects", True)))
            return final

        with mock.patch("requests.Session.get", fake_get):
            client = OddsAPI(cfg, db, transport=requests_transport(), api_key=[{"label": "paid", "key": SECRET}])
            provider = TheOddsAPIProvider(cfg, db, {"enabled": True}, client=client)
            self.assertEqual(provider.odds("basketball_nba", "h2h", strategy="live"),
                             [{"id": "game", "_provider": "theoddsapi"}])
        self.assertEqual(len(seen), 1)
        self.assertEqual(client.calls, 1)
        self.assertEqual(seen[0][1]["apiKey"], SECRET)
        self.assertEqual(seen[0][2], 20.0)
        rows = _rows(self.path)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["source_job"] for row in rows}, {"aj-live"})
        self.assertEqual(len({row["logical_request_id"] for row in rows}), 1)
        usage = db.conn.execute("SELECT COUNT(*) AS n FROM api_usage").fetchone()["n"]
        self.assertEqual(usage, 1)
        blob = self.path.read_bytes()
        self.assertNotIn(SECRET.encode(), blob)
        self.assertNotIn(b"evil.test", blob)

        self.path.unlink(missing_ok=True)
        db = DB(":memory:")
        calls = []

        def transport(url, params):
            calls.append(params["apiKey"])
            if len(calls) == 1:
                return 429, {"x-requests-last": "1"}, {"message": "slow"}
            return 200, {"x-requests-last": "2", "x-requests-used": "3", "x-requests-remaining": "4"}, [{"id": "ok"}]

        client = OddsAPI(cfg, db, transport=transport, api_key=[
            {"label": "paid", "key": SECRET}, {"label": "free", "key": "synthetic-free"}])
        self.assertEqual(client.live_odds("basketball_nba", strategy="value"), [{"id": "ok"}])
        self.assertEqual(calls, [SECRET, "synthetic-free"])
        self.assertEqual(client.calls, 2)
        retry_rows = sorted(_rows(self.path), key=lambda row: row["attempt_ordinal"])
        self.assertEqual([row["http_status"] for row in retry_rows], [429, 200])
        self.assertEqual(retry_rows[0]["logical_request_id"], retry_rows[1]["logical_request_id"])
        self.assertEqual(retry_rows[0]["attempt_ordinal"] + 1, retry_rows[1]["attempt_ordinal"])

    def test_shadow_cap_and_errors_stay_on_the_credit_ledger(self):
        from shadow_collector.credits import DEFAULT_DAILY_CAP, CreditError, Ledger
        from shadow_collector.odds_request import _odds_request
        self.assertEqual(hashlib_credits(), 
                         "e32f73e4a4ff2cca6faaedd677791dbd292def79b8cc92ae9ed629c547e13ac4")
        self.assertEqual(DEFAULT_DAILY_CAP, 100)
        when = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        ledger = Ledger(Path(self.tmp.name) / "usage.json", cap=100, now=when)
        self.addCleanup(ledger.close)
        with self.assertRaises(CreditError):
            ledger.reserve("event_odds", 101, "over")
        self.assertEqual(ledger.snapshot()["used"], 0)
        seen = []

        def transport(url):
            seen.append(url)
            return 401, {"x-requests-last": "1", "x-requests-remaining": "5"}, b"{}"

        with self.assertRaises(CreditError):
            _odds_request(ledger, transport, SECRET, "/sports/basketball_nba/events/EVENT/odds",
                          {"markets": "player_points", "bookmakers": "bovada", "oddsFormat": "american"},
                          "event_odds", 4, "game")
        self.assertEqual(len(seen), 1)
        self.assertIn(SECRET, seen[0])
        snap = ledger.snapshot()
        self.assertTrue(snap["fail_closed"])
        self.assertEqual(snap["used"], 4)
        row = _rows(self.path)[0]
        self.assertEqual(row["http_status"], 401)
        self.assertEqual(row["actual_header_cost"], 1)
        self.assertEqual(row["source_job"], "shadow-collector")
        self.assertNotIn(SECRET.encode(), self.path.read_bytes())
        self.assertNotIn(b"EVENT", self.path.read_bytes())

        fresh = Ledger(Path(self.tmp.name) / "malformed.json", cap=100, now=when)
        self.addCleanup(fresh.close)
        with self.assertRaises(CreditError):
            _odds_request(fresh, lambda _url: (200, {"x-requests-last": "bad"}, b"{}"),
                          SECRET, "/sports/basketball_nba/events", {}, "events", 0, "nba")
        self.assertTrue(fresh.snapshot()["fail_closed"])
        self.assertEqual(fresh.snapshot()["used"], 0)
        bad = next(row for row in _rows(self.path) if row["actual_status"] == "unparseable")
        self.assertIsNone(bad["actual_header_cost"])
        self.assertNotIn(SECRET.encode(), self.path.read_bytes())

    def test_job_tags_match_only_equivalent_entrypoints(self):
        tagged = {
            REPO / "marv/sources.py": "marv-sources",
            REPO / "marv/board.py": "marv-board",
            REPO / "marv/betcard.py": "marv-betcard",
            REPO / "marv/live_props.py": "marv-live-props",
            REPO / "marv/cfb_props_shadow.py": "marv-cfb-props-shadow",
            REPO / "marv/props/run.py": "marv-props-nfl",
            REPO / "marv/props/cfb_run.py": "marv-props-cfb",
            REPO / "marv/props/basketball_run.py": "marv-props-basketball",
        }
        for path, label in tagged.items():
            self.assertIn(label, path.read_text())
        for relative in ("marv/live_monitor.py", "marv/main.py", "marv/live_prop_engine.py"):
            self.assertNotIn("source_job", (REPO / relative).read_text())


def hashlib_credits():
    import hashlib
    return hashlib.sha256((PKG / "integrations/shadow_collector/credits.py").read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
