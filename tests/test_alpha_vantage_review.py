"""Synthetic, offline tests for the review-only Alpha Vantage adapter.

Fixtures are invented. They are not copies of provider bars.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "review" / "alpha-vantage"
sys.path.insert(0, str(PACKAGE_ROOT))

from alpha_vantage import LIVE_CALLS_ENABLED_BY_DEFAULT, client, compare, frames, quota, research, spec, validate
from alpha_vantage.__main__ import main
from alpha_vantage.errors import (
    CacheRejected,
    HttpStatusError,
    LocalRefusal,
    MissingKey,
    NetworkError,
    OptInRequired,
    ProviderMessage,
    QuotaExceeded,
    RepeatAttemptRefused,
    StaleSeries,
    UnsafeLogging,
    ValidationError,
)
from alpha_vantage.validate import parse_request

AS_OF = datetime(2026, 11, 8, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
NOW = datetime(2026, 11, 8, 17, 0, tzinfo=timezone.utc)
KEY = "SUPERSECRETKEY"
NY = "America/New_York"


def weekdays_ending(end: date, count: int) -> list[date]:
    found = []
    day = end
    while len(found) < count:
        if day.weekday() < 5:
            found.append(day)
        day -= timedelta(days=1)
    return list(reversed(found))


def daily_payload(symbol="TEST", sessions=None, timezone_name="US/Eastern", output_size="Compact", symbol_meta=None):
    sessions = sessions or weekdays_ending(date(2026, 11, 6), 3)
    bars = {}
    for i, session in enumerate(sessions):
        bars[session.isoformat()] = {
            "1. open": "10.00",
            "2. high": "12.00",
            "3. low": "9.00",
            "4. close": "11.00",
            "5. volume": str(1000 + i),
        }
    last = sessions[-1].isoformat()
    return {
        "Meta Data": {
            "1. Information": "Synthetic daily prices",
            "2. Symbol": symbol if symbol_meta is None else symbol_meta,
            "3. Last Refreshed": last,
            "4. Output Size": output_size,
            "5. Time Zone": timezone_name,
        },
        "Time Series (Daily)": bars,
    }


def weekly_payload(symbol="TEST", close="20.00", adjusted="10.00", dividend="0.25", session="2026-11-06"):
    return {
        "Meta Data": {
            "1. Information": "Synthetic weekly adjusted prices",
            "2. Symbol": symbol,
            "3. Last Refreshed": session,
            "4. Time Zone": "US/Eastern",
        },
        "Weekly Adjusted Time Series": {
            session: {
                "1. open": "18.00",
                "2. high": "21.00",
                "3. low": "17.00",
                "4. close": close,
                "5. adjusted close": adjusted,
                "6. volume": "5000",
                "7. dividend amount": dividend,
            }
        },
    }


class EnvGuard(dict):
    def __init__(self, *args, raise_on_key=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.raise_on_key = raise_on_key

    def get(self, key, default=None):
        if key == "ALPHA_VANTAGE_API_KEY" and self.raise_on_key:
            raise AssertionError("API key was read")
        return super().get(key, default)

    def __getitem__(self, key):
        if key == "ALPHA_VANTAGE_API_KEY" and self.raise_on_key:
            raise AssertionError("API key was read")
        return super().__getitem__(key)


class ContractTests(unittest.TestCase):
    def test_live_calls_are_off_by_default(self):
        self.assertFalse(LIVE_CALLS_ENABLED_BY_DEFAULT)
        self.assertEqual(spec.FREE_DAILY_QUOTA, 25)
        self.assertIn("does not promise", spec.SHARED_QUOTA_CAVEAT)
        self.assertIn("25", spec.SHARED_QUOTA_CAVEAT)

    def test_package_source_does_not_call_alpaca_or_shell(self):
        for path in (PACKAGE_ROOT / "alpha_vantage").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("import alpacabot", text, path.name)
            self.assertNotIn("alpaca.markets", text, path.name)
            self.assertNotIn("subprocess", text, path.name)
            self.assertNotIn("systemctl", text, path.name)
            self.assertNotIn(KEY, text, path.name)

    def test_client_has_no_retry_loop(self):
        tree = ast.parse((PACKAGE_ROOT / "alpha_vantage" / "client.py").read_text(encoding="utf-8"))
        loops = [node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While))]
        self.assertEqual(loops, [])

    def test_import_does_not_require_a_key(self):
        env = os.environ.copy()
        env["ALPHA_VANTAGE_API_KEY"] = KEY
        env.pop("ALPHA_VANTAGE_RESEARCH_OPT_IN", None)
        completed = subprocess.run(
            [sys.executable, "-c", "import alpha_vantage, alpha_vantage.research, alpha_vantage.client; print(alpha_vantage.LIVE_CALLS_ENABLED_BY_DEFAULT)"],
            cwd=PACKAGE_ROOT,
            env={**env, "PYTHONPATH": str(PACKAGE_ROOT)},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "False")
        self.assertNotIn(KEY, completed.stdout)
        self.assertNotIn(KEY, completed.stderr)

    def test_alpaca_bar_layout_still_matches_and_is_unchanged(self):
        import pandas as pd

        import alpacabot.data as data

        text = (ROOT / "alpacabot" / "data.py").read_text(encoding="utf-8")
        self.assertIn('"adjustment": "all"', text)
        self.assertIn('FEED = os.environ.get("ALPACA_FEED", "sip")', text)
        self.assertEqual(list(spec.ALPACA_BAR_COLUMNS), list(data.COLS))
        self.assertEqual(spec.NY, data.NY)
        empty = data._empty()
        result = research.offline_from_payload(
            daily_payload(),
            symbol="TEST",
            function=spec.DAILY,
            outputsize="compact",
            as_of=AS_OF,
            fetched_at=NOW,
        )
        self.assertEqual(frames.alpaca_layout_problems(result.raw_ohlcv), [])
        self.assertEqual(frames.alpaca_layout_problems(empty), [])
        combined = pd.concat([empty, result.raw_ohlcv])
        self.assertEqual(list(combined.columns), list(data.COLS))
        self.assertEqual(combined.index.name, "time")
        self.assertEqual(str(result.raw_ohlcv.index.tz), NY)


class RequestAndValidationTests(unittest.TestCase):
    def test_premium_and_batch_requests_are_refused_locally(self):
        for function, size in (
            ("TIME_SERIES_INTRADAY", None),
            ("TIME_SERIES_DAILY_ADJUSTED", "compact"),
            (spec.DAILY, "full"),
            (spec.DAILY, None),
            (spec.WEEKLY_ADJUSTED, "compact"),
        ):
            with self.assertRaises(LocalRefusal):
                parse_request("TEST", function, size)
        with self.assertRaises(LocalRefusal):
            parse_request("TEST,AAPL", spec.DAILY, "compact")
        with self.assertRaises(LocalRefusal):
            parse_request("../etc", spec.DAILY, "compact")

    def test_daily_raw_frame_uses_new_york_session_dates(self):
        payload = daily_payload(sessions=[date(2026, 1, 5), date(2026, 7, 6), date(2026, 11, 6)])
        result = research.offline_from_payload(
            payload, symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW
        )
        dates = [stamp.tz_convert(NY).date() for stamp in result.raw_ohlcv.index]
        self.assertEqual(dates, [date(2026, 1, 5), date(2026, 7, 6), date(2026, 11, 6)])
        self.assertNotIn("adjusted_close", result.raw_ohlcv.columns)
        self.assertEqual(result.raw_ohlcv.attrs["adjustment_basis"], spec.ADJUSTMENT_DAILY_RAW)
        self.assertEqual(result.raw_ohlcv.attrs["ohlc_basis"], "raw_unadjusted")
        self.assertEqual(result.raw_ohlcv.attrs["feed"], "not_an_alpaca_feed")
        self.assertIsNone(result.weekly_research)
        self.assertEqual(result.network_calls, 0)

    def test_weekly_adjusted_close_stays_off_the_raw_ohlc(self):
        result = research.offline_from_payload(
            weekly_payload(), symbol="TEST", function=spec.WEEKLY_ADJUSTED, outputsize=None, as_of=AS_OF, fetched_at=NOW
        )
        self.assertNotIn("adjusted_close", result.raw_ohlcv.columns)
        self.assertEqual(result.raw_ohlcv.attrs["adjustment_basis"], "raw_unadjusted_ohlcv")
        self.assertTrue(result.raw_ohlcv.attrs["adjusted_close_omitted"])
        self.assertAlmostEqual(float(result.raw_ohlcv["close"].iloc[0]), 20.0)
        research_frame = result.weekly_research
        self.assertAlmostEqual(float(research_frame["close"].iloc[0]), 20.0)
        self.assertAlmostEqual(float(research_frame["adjusted_close"].iloc[0]), 10.0)
        self.assertAlmostEqual(float(research_frame["dividend_amount"].iloc[0]), 0.25)
        self.assertEqual(research_frame.attrs["adjustment_basis"], spec.ADJUSTMENT_WEEKLY)
        self.assertTrue(research_frame.attrs["do_not_replace_close_with_adjusted_close"])
        self.assertTrue(research_frame.attrs["raw_ohlc_not_adjusted"])

    def test_internal_weekly_aggregation_does_not_invent_an_adjusted_close(self):
        sessions = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)]
        payload = daily_payload(sessions=sessions)
        # Give each day a distinct raw bar. Monday open 10, Friday close 11 is the default;
        # override so the week high/low/volume are unambiguous.
        series = payload["Time Series (Daily)"]
        values = {
            "2026-11-02": ("10.00", "12.00", "9.00", "11.00", "100"),
            "2026-11-03": ("11.00", "13.00", "10.00", "12.00", "100"),
            "2026-11-04": ("12.00", "14.00", "11.00", "13.00", "100"),
            "2026-11-05": ("13.00", "15.00", "8.00", "9.00", "100"),
            "2026-11-06": ("9.00", "16.00", "7.00", "10.00", "100"),
        }
        for day, fields in values.items():
            series[day] = {
                "1. open": fields[0], "2. high": fields[1], "3. low": fields[2], "4. close": fields[3], "5. volume": fields[4],
            }
        result = research.offline_from_payload(
            payload, symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW
        )
        week = frames.aggregate_raw_daily_to_weeks(result.raw_ohlcv)
        self.assertEqual(list(week.columns), list(spec.ALPACA_BAR_COLUMNS))
        self.assertEqual(week.index[0].tz_convert(NY).date(), date(2026, 11, 6))
        self.assertAlmostEqual(float(week["open"].iloc[0]), 10.0)
        self.assertAlmostEqual(float(week["high"].iloc[0]), 16.0)
        self.assertAlmostEqual(float(week["low"].iloc[0]), 7.0)
        self.assertAlmostEqual(float(week["close"].iloc[0]), 10.0)
        self.assertAlmostEqual(float(week["volume"].iloc[0]), 500.0)
        self.assertEqual(week.attrs["adjusted_close_basis"], "not_computed")
        with self.assertRaises(ValidationError):
            frames.aggregate_raw_daily_to_weeks(result.weekly_research) if False else frames.aggregate_raw_daily_to_weeks(
                research.offline_from_payload(
                    weekly_payload(), symbol="TEST", function=spec.WEEKLY_ADJUSTED, outputsize=None, as_of=AS_OF, fetched_at=NOW
                ).weekly_research
            )

    def test_refuses_stale_wrong_symbol_weekend_future_and_bad_ohlc(self):
        with self.assertRaises(StaleSeries):
            research.offline_from_payload(
                daily_payload(sessions=weekdays_ending(date(2026, 10, 1), 2)),
                symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
            )
        with self.assertRaises(ValidationError) as wrong:
            research.offline_from_payload(
                daily_payload(symbol_meta="OTHER"),
                symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
            )
        self.assertEqual(wrong.exception.code, "wrong_symbol")
        with self.assertRaises(ValidationError) as weekend:
            research.offline_from_payload(
                daily_payload(sessions=[date(2026, 11, 7)]),
                symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
            )
        self.assertEqual(weekend.exception.code, "weekend_date")
        future = daily_payload(sessions=[date(2026, 11, 9)])
        with self.assertRaises(ValidationError) as future_error:
            research.offline_from_payload(
                future, symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
            )
        self.assertEqual(future_error.exception.code, "future_bar")
        broken = daily_payload()
        broken["Time Series (Daily)"]["2026-11-06"]["2. high"] = "8.00"
        with self.assertRaises(ValidationError) as ohlc:
            research.offline_from_payload(
                broken, symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
            )
        self.assertEqual(ohlc.exception.code, "illformed_ohlc")
        zero_volume = daily_payload()
        zero_volume["Time Series (Daily)"]["2026-11-06"]["5. volume"] = "0"
        with self.assertRaises(ValidationError):
            research.offline_from_payload(
                zero_volume, symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
            )

    def test_compact_cap_timezone_and_gap_policy(self):
        with self.assertRaises(ValidationError) as too_many:
            research.offline_from_payload(
                daily_payload(sessions=weekdays_ending(date(2026, 11, 6), 101)),
                symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
            )
        self.assertEqual(too_many.exception.code, "too_many_bars")
        ok = research.offline_from_payload(
            daily_payload(sessions=weekdays_ending(date(2026, 11, 6), 100)),
            symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
        )
        self.assertEqual(len(ok.series.bars), 100)
        bad_zone = daily_payload(timezone_name="UTC")
        with self.assertRaises(ValidationError) as zone:
            research.offline_from_payload(
                bad_zone, symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
            )
        self.assertEqual(zone.exception.code, "bad_timezone")
        full = daily_payload(output_size="Full")
        with self.assertRaises(ValidationError) as output_size:
            research.offline_from_payload(
                full, symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
            )
        self.assertEqual(output_size.exception.code, "output_size")
        gapped = research.offline_from_payload(
            daily_payload(sessions=[date(2026, 11, 2), date(2026, 11, 6)]),
            symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
        )
        self.assertEqual(len(gapped.series.bars), 2)

    def test_provider_messages_are_not_bars(self):
        for key, category in (("Note", "note"), ("Information", "information"), ("Error Message", "error_message")):
            payload = daily_payload()
            payload[key] = f"notice apikey={KEY}"
            with self.assertRaises(ProviderMessage) as caught:
                validate.validate_payload(payload, parse_request("TEST", spec.DAILY, "compact"), AS_OF)
            self.assertEqual(caught.exception.category, category)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn("11.00", str(caught.exception))


class CacheQuotaAndNetworkTests(unittest.TestCase):
    def _load(self, directory, payload, session, **kwargs):
        calls = {"n": 0}

        def fake(url, params, timeout):
            calls["n"] += 1
            calls["timeout"] = timeout
            calls["url"] = url
            calls["params"] = dict(params)
            if isinstance(session, Exception):
                raise session
            status, body = session(calls) if callable(session) else session
            return status, body

        env = EnvGuard({
            "ALPHA_VANTAGE_RESEARCH_OPT_IN": "1",
            "ALPHA_VANTAGE_API_KEY": KEY,
        })
        try:
            result = research.load_series(
                symbol="TEST",
                function=spec.DAILY,
                outputsize="compact",
                cache_dir=directory,
                as_of=AS_OF,
                opt_in=True,
                environ=env,
                session_get=fake,
                now=NOW,
                **kwargs,
            )
        except Exception:
            raise
        finally:
            calls["env_saw_key"] = "ALPHA_VANTAGE_API_KEY" in env
        return result, calls

    def test_disabled_paths_do_not_read_the_key_or_call_out(self):
        def explode(url, params, timeout):
            raise AssertionError("network")

        with tempfile.TemporaryDirectory() as directory:
            env = EnvGuard({KEY: "nope"}, raise_on_key=True)
            with self.assertRaises(OptInRequired):
                research.load_series(
                    symbol="TEST", function=spec.DAILY, outputsize="compact", cache_dir=directory,
                    as_of=AS_OF, opt_in=False, environ=env, session_get=explode, now=NOW,
                )
            env["ALPHA_VANTAGE_RESEARCH_OPT_IN"] = "true"
            with self.assertRaises(OptInRequired):
                research.load_series(
                    symbol="TEST", function=spec.DAILY, outputsize="compact", cache_dir=directory,
                    as_of=AS_OF, opt_in=True, environ=env, session_get=explode, now=NOW,
                )
            self.assertFalse((Path(directory) / "quota-ledger.json").exists())

    def test_success_then_warm_cache_makes_no_second_call(self):
        body = json.dumps(daily_payload()).encode()
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self._load(directory, daily_payload(), (200, body))
            self.assertEqual(calls["n"], 1)
            self.assertEqual(calls["timeout"], spec.HTTP_TIMEOUT_SECONDS)
            self.assertEqual(calls["url"], client.ENDPOINT)
            self.assertNotIn("apikey", calls["params"])
            self.assertEqual(result.network_calls, 1)
            self.assertFalse(result.cache_hit)
            self.assertEqual(result.provenance["fetched_at_utc"], NOW.isoformat())
            self.assertEqual(result.provenance["adjustment_basis"], spec.ADJUSTMENT_DAILY_RAW)
            self.assertEqual(result.provenance["feed"], "not_an_alpaca_feed")
            cached_text = Path(directory, "series", spec.DAILY, "TEST", "compact.json").read_text(encoding="utf-8")
            self.assertNotIn(KEY, cached_text)
            self.assertNotIn("apikey=", cached_text)
            ledger = Path(directory, "quota-ledger.json").read_text(encoding="utf-8")
            self.assertNotIn(KEY, ledger)
            self.assertIn("success", ledger)

            def explode(url, params, timeout):
                raise AssertionError("warm cache still called the network")

            env = EnvGuard(raise_on_key=True)
            warmed = research.load_series(
                symbol="TEST", function=spec.DAILY, outputsize="compact", cache_dir=directory,
                as_of=AS_OF, opt_in=False, environ=env, session_get=explode, now=NOW,
            )
            self.assertTrue(warmed.cache_hit)
            self.assertEqual(warmed.network_calls, 0)
            self.assertEqual(warmed.provenance["fetched_at_utc"], NOW.isoformat())
            self.assertEqual(warmed.provenance["source"], "cache")

    def test_note_information_and_http_errors_count_and_do_not_retry(self):
        note = dict(daily_payload())
        note["Note"] = f"slow down apikey={KEY}"
        with tempfile.TemporaryDirectory() as directory:
            calls = {"n": 0}

            def fake(url, params, timeout):
                calls["n"] += 1
                return 200, json.dumps(note).encode()

            env = {"ALPHA_VANTAGE_RESEARCH_OPT_IN": "1"}
            with self.assertRaises(ProviderMessage) as caught:
                research.load_series(
                    symbol="TEST", function=spec.DAILY, outputsize="compact", cache_dir=directory,
                    as_of=AS_OF, opt_in=True, environ=env, session_get=fake, now=NOW,
                )
            self.assertEqual(caught.exception.category, "note")
            self.assertNotIn(KEY, str(caught.exception))
            self.assertEqual(calls["n"], 1)
            self.assertFalse(Path(directory, "series").exists())
            with self.assertRaises(RepeatAttemptRefused):
                research.load_series(
                    symbol="TEST", function=spec.DAILY, outputsize="compact", cache_dir=directory,
                    as_of=AS_OF, opt_in=True, environ=env, session_get=fake, now=NOW,
                )
            self.assertEqual(calls["n"], 1)
            self.assertEqual(quota.QuotaLedger(directory).attempt_count(NOW), 1)

        with tempfile.TemporaryDirectory() as directory:
            calls = {"n": 0}

            def fake(url, params, timeout):
                calls["n"] += 1
                return 429, f"apikey={KEY}".encode()

            env = {"ALPHA_VANTAGE_RESEARCH_OPT_IN": "1"}
            with self.assertRaises(HttpStatusError) as caught:
                research.load_series(
                    symbol="TEST", function=spec.DAILY, outputsize="compact", cache_dir=directory,
                    as_of=AS_OF, opt_in=True, environ=env, session_get=fake, now=NOW,
                )
            self.assertEqual(caught.exception.status, 429)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertEqual(calls["n"], 1)
            ledger = Path(directory, "quota-ledger.json").read_text(encoding="utf-8")
            self.assertIn("http_status", ledger)
            self.assertNotIn(KEY, ledger)

    def test_exception_text_from_the_socket_is_dropped(self):
        def fake(url, params, timeout):
            raise RuntimeError(f"{url}?apikey={KEY}&symbol=TEST")

        request = parse_request("TEST", spec.DAILY, "compact")
        with self.assertRaises(NetworkError) as caught:
            client.http_get(request, {"ALPHA_VANTAGE_API_KEY": KEY}, session_get=fake)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertNotIn("apikey", str(caught.exception))
        self.assertNotIn(client.ENDPOINT, str(caught.exception))

    def test_real_get_passes_the_key_once_and_hides_it(self):
        seen = {}

        def fake(url, params, timeout):
            seen["url"] = url
            seen["params"] = dict(params)
            seen["timeout"] = timeout
            raise RuntimeError(params["apikey"])

        request = parse_request("TEST", spec.DAILY, "compact")
        with self.assertRaises(NetworkError) as caught:
            client.http_get(request, {"ALPHA_VANTAGE_API_KEY": f"  {KEY}  "}, session_get=fake)
        self.assertEqual(seen["url"], client.ENDPOINT)
        self.assertEqual(seen["params"]["apikey"], KEY)
        self.assertEqual(seen["params"]["function"], spec.DAILY)
        self.assertEqual(seen["params"]["outputsize"], "compact")
        self.assertEqual(seen["params"]["datatype"], "json")
        self.assertEqual(set(seen["params"]), {"function", "symbol", "datatype", "apikey", "outputsize"})
        self.assertEqual(seen["timeout"], 20.0)
        self.assertNotIn(KEY, str(caught.exception))

    def test_default_get_uses_no_retries_and_redacts(self):
        import requests

        calls = {"n": 0}

        class FakeAdapter:
            def __init__(self, max_retries=0):
                self.max_retries = max_retries

        class FakeSession:
            def mount(self, prefix, adapter):
                calls["retries"] = adapter.max_retries
                calls["prefix"] = prefix

            def get(self, url, params, timeout):
                calls["n"] += 1
                raise RuntimeError(f"{url}?apikey={params['apikey']}")

        original_session = requests.Session
        original_adapter = requests.adapters.HTTPAdapter
        requests.Session = FakeSession
        requests.adapters.HTTPAdapter = FakeAdapter
        try:
            with self.assertRaises(NetworkError) as caught:
                client._default_get(client.ENDPOINT, {"apikey": KEY}, 20)
        finally:
            requests.Session = original_session
            requests.adapters.HTTPAdapter = original_adapter
        self.assertEqual(calls["n"], 1)
        self.assertEqual(calls["retries"], 0)
        self.assertEqual(calls["prefix"], "https://")
        self.assertNotIn(KEY, str(caught.exception))

    def test_debug_logging_refuses_before_reserve(self):
        log = logging.getLogger("urllib3")
        previous = log.level
        log.setLevel(logging.DEBUG)
        try:
            with tempfile.TemporaryDirectory() as directory:
                env = {"ALPHA_VANTAGE_RESEARCH_OPT_IN": "1", "ALPHA_VANTAGE_API_KEY": KEY}
                with self.assertRaises(UnsafeLogging):
                    research.load_series(
                        symbol="TEST", function=spec.DAILY, outputsize="compact", cache_dir=directory,
                        as_of=AS_OF, opt_in=True, environ=env, session_get=lambda *a: (_ for _ in ()).throw(AssertionError("sent")),
                        now=NOW,
                    )
                self.assertFalse((Path(directory) / "quota-ledger.json").exists())
        finally:
            log.setLevel(previous)

    def test_missing_key_does_not_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MissingKey):
                research.load_series(
                    symbol="TEST", function=spec.DAILY, outputsize="compact", cache_dir=directory,
                    as_of=AS_OF, opt_in=True, environ={"ALPHA_VANTAGE_RESEARCH_OPT_IN": "1"}, now=NOW,
                )
            self.assertFalse((Path(directory) / "quota-ledger.json").exists())

    def test_corrupt_ledger_refuses_without_a_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota-ledger.json"
            path.write_text("{", encoding="utf-8")
            calls = {"n": 0}

            def fake(url, params, timeout):
                calls["n"] += 1
                return 200, json.dumps(daily_payload()).encode()

            with self.assertRaises(LocalRefusal):
                research.load_series(
                    symbol="TEST", function=spec.DAILY, outputsize="compact", cache_dir=directory,
                    as_of=AS_OF, opt_in=True, environ={"ALPHA_VANTAGE_RESEARCH_OPT_IN": "1"},
                    session_get=fake, now=NOW,
                )
            self.assertEqual(calls["n"], 0)

    def test_failed_attempt_stays_counted_and_repeat_is_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = quota.QuotaLedger(directory)
            request = parse_request("TEST", spec.DAILY, "compact")
            first = ledger.reserve(request, now=NOW)
            ledger.complete(first.token, "network", now=NOW)
            with self.assertRaises(RepeatAttemptRefused):
                ledger.reserve(request, now=NOW)
            self.assertEqual(ledger.attempt_count(NOW), 1)
            second = ledger.reserve(request, now=NOW, allow_repeat=True)
            self.assertEqual(second.attempt_number, 2)
            self.assertEqual(ledger.attempt_count(NOW), 2)

    def test_threads_and_processes_stop_at_25(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = quota.QuotaLedger(directory)
            for i in range(24):
                ledger.reserve(parse_request(f"A{i:02d}", spec.DAILY, "compact"), now=NOW)
            results = []
            lock = threading.Lock()

            def once(symbol):
                try:
                    quota.QuotaLedger(directory).reserve(parse_request(symbol, spec.DAILY, "compact"), now=NOW)
                    outcome = "ok"
                except QuotaExceeded:
                    outcome = "quota"
                with lock:
                    results.append(outcome)

            threads = [threading.Thread(target=once, args=(f"B{i:02d}",)) for i in range(10)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(results.count("ok"), 1)
            self.assertEqual(results.count("quota"), 9)
            self.assertEqual(quota.QuotaLedger(directory).attempt_count(NOW), 25)

        with tempfile.TemporaryDirectory() as directory:
            script = """
import sys
from datetime import datetime, timezone
sys.path.insert(0, sys.argv[1])
from alpha_vantage.errors import QuotaExceeded
from alpha_vantage.quota import QuotaLedger
from alpha_vantage.validate import parse_request
now = datetime(2026, 11, 8, 17, 0, tzinfo=timezone.utc)
try:
    QuotaLedger(sys.argv[2]).reserve(parse_request(sys.argv[3], "TIME_SERIES_DAILY", "compact"), now=now)
    print("ok")
except QuotaExceeded:
    print("quota")
"""
            processes = []
            for i in range(30):
                processes.append(subprocess.Popen(
                    [sys.executable, "-c", script, str(PACKAGE_ROOT), directory, f"S{i:02d}"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ))
            outputs = []
            for process in processes:
                stdout, stderr = process.communicate()
                self.assertEqual(process.returncode, 0, stderr)
                outputs.append(stdout.strip())
            self.assertEqual(outputs.count("ok"), 25)
            self.assertEqual(outputs.count("quota"), 5)

    def test_weekly_cache_roundtrip_keeps_adjusted_close_separate(self):
        body = json.dumps(weekly_payload()).encode()
        with tempfile.TemporaryDirectory() as directory:
            calls = {"n": 0}

            def fake(url, params, timeout):
                calls["n"] += 1
                return 200, body

            result = research.load_series(
                symbol="TEST", function=spec.WEEKLY_ADJUSTED, outputsize=None, cache_dir=directory,
                as_of=AS_OF, opt_in=True, environ={"ALPHA_VANTAGE_RESEARCH_OPT_IN": "1"},
                session_get=fake, now=NOW,
            )
            self.assertAlmostEqual(float(result.raw_ohlcv["close"].iloc[0]), 20.0)
            self.assertAlmostEqual(float(result.weekly_research["adjusted_close"].iloc[0]), 10.0)
            self.assertNotIn("adjusted_close", result.raw_ohlcv.columns)

            def explode(url, params, timeout):
                raise AssertionError("warm weekly cache called the network")

            warmed = research.load_series(
                symbol="TEST", function=spec.WEEKLY_ADJUSTED, outputsize=None, cache_dir=directory,
                as_of=AS_OF, opt_in=False, environ=EnvGuard(raise_on_key=True), session_get=explode, now=NOW,
            )
            self.assertEqual(calls["n"], 1)
            self.assertTrue(warmed.cache_hit)
            self.assertEqual(warmed.network_calls, 0)
            self.assertAlmostEqual(float(warmed.raw_ohlcv["close"].iloc[0]), 20.0)
            self.assertAlmostEqual(float(warmed.weekly_research["adjusted_close"].iloc[0]), 10.0)
            text = Path(directory, "series", spec.WEEKLY_ADJUSTED, "TEST", "none.json").read_text(encoding="utf-8")
            self.assertIn(spec.ADJUSTMENT_WEEKLY, text)
            self.assertIn("raw_unadjusted", text)
            self.assertNotIn(KEY, text)


class CompareAndCliTests(unittest.TestCase):
    def test_comparison_labels_venue_and_adjustment_without_a_ranking(self):
        import pandas as pd

        left = research.offline_from_payload(
            daily_payload(sessions=[date(2026, 11, 6)]),
            symbol="TEST", function=spec.DAILY, outputsize="compact", as_of=AS_OF, fetched_at=NOW,
        ).raw_ohlcv
        right = pd.DataFrame(
            {"open": [10.0], "high": [12.0], "low": [9.0], "close": [10.5], "volume": [50.0]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-11-06T05:00:00Z")], name="time"),
        )
        report = compare.compare_sessions(
            left,
            right,
            compare.FeedSpec("alpha_vantage", "not_independently_verified", spec.ADJUSTMENT_DAILY_RAW, "1Day"),
            compare.FeedSpec("supplied_iex", "iex", "alpaca_adjustment_all", "1Day"),
        )
        self.assertEqual(report["conclusion"], "not_a_ranking")
        self.assertEqual(report["aligned_sessions"], 1)
        self.assertFalse(report["adjustment_same"])
        self.assertFalse(report["venue_same"])
        self.assertEqual(report["field_deltas"]["close"]["max_abs"], 0.5)
        self.assertGreater(report["field_deltas"]["volume"]["max_abs"], 0)
        blob = json.dumps(report).lower()
        for word in ("profit", "superior", "outperform", "winner"):
            self.assertNotIn(word, blob)
        shifted = pd.DataFrame(
            {"open": [10.0], "high": [12.0], "low": [9.0], "close": [10.5], "volume": [50.0]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-11-06T00:00:00Z")], name="time"),
        )
        missed = compare.compare_sessions(
            left, shifted,
            compare.FeedSpec("alpha_vantage", "not_independently_verified", spec.ADJUSTMENT_DAILY_RAW, "1Day"),
            compare.FeedSpec("supplied_iex", "iex", "alpaca_adjustment_all", "1Day"),
        )
        self.assertEqual(missed["aligned_sessions"], 0)
        self.assertEqual(missed["only_left"]["count"], 1)
        mismatched = left.copy()
        mismatched.attrs["adjustment_basis"] = "alpaca_adjustment_all"
        with self.assertRaises(ValidationError):
            compare.compare_sessions(
                mismatched, right,
                compare.FeedSpec("alpha_vantage", "not_independently_verified", spec.ADJUSTMENT_DAILY_RAW, "1Day"),
                compare.FeedSpec("supplied_iex", "iex", "alpaca_adjustment_all", "1Day"),
            )

    def test_weekly_compare_uses_raw_close_not_adjusted_close(self):
        import pandas as pd

        result = research.offline_from_payload(
            weekly_payload(close="20.00", adjusted="10.00"),
            symbol="TEST", function=spec.WEEKLY_ADJUSTED, outputsize=None, as_of=AS_OF, fetched_at=NOW,
        )
        other = pd.DataFrame(
            {"open": [18.0], "high": [21.0], "low": [17.0], "close": [20.0], "volume": [5000.0]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-11-06T05:00:00Z")], name="time"),
        )
        report = compare.compare_sessions(
            result.weekly_research,
            other,
            compare.FeedSpec("alpha_vantage", "not_independently_verified", spec.ADJUSTMENT_WEEKLY, "1Week"),
            compare.FeedSpec("supplied_iex", "iex", "raw_as_traded_ohlcv", "1Week"),
        )
        self.assertEqual(report["field_deltas"]["close"]["max_abs"], 0.0)
        self.assertTrue(report["left_had_adjusted_close_column"])
        self.assertTrue(report["adjusted_close_was_not_used_as_close"])
        self.assertFalse(report["adjustment_same"])

    def test_cli_offline_and_disabled_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            payload_path = directory / "daily.json"
            payload_path.write_text(json.dumps(daily_payload()), encoding="utf-8")
            iex_path = directory / "iex.csv"
            iex_path.write_text(
                "time,open,high,low,close,volume\n2026-11-06T05:00:00Z,10,12,9,10.5,50\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "validate-fixture", "--json", str(payload_path), "--symbol", "TEST",
                    "--function", spec.DAILY, "--as-of", "2026-11-08T12:00:00-05:00",
                ])
            self.assertEqual(code, 0)
            self.assertIn("raw_as_traded_ohlcv", stdout.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())["network_calls"], 0)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "compare", "--av-response", str(payload_path), "--iex", str(iex_path),
                    "--symbol", "TEST", "--function", spec.DAILY, "--as-of", "2026-11-08T12:00:00-05:00",
                    "--iex-adjustment", "alpaca_adjustment_all", "--iex-venue", "iex", "--iex-timeframe", "1Day",
                ])
            self.assertEqual(code, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["conclusion"], "not_a_ranking")
            self.assertIn("does not rank", report["disclaimer"])
            previous_get = client.http_get

            def explode(*args, **kwargs):
                raise AssertionError("disabled fetch contacted the client")

            client.http_get = explode
            os.environ["ALPHA_VANTAGE_API_KEY"] = KEY
            os.environ.pop("ALPHA_VANTAGE_RESEARCH_OPT_IN", None)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = main([
                        "fetch", "--symbol", "TEST", "--function", spec.DAILY,
                        "--as-of", "2026-11-08T12:00:00-05:00", "--cache-dir", str(directory / "cache"),
                        "--opt-in",
                    ])
            finally:
                client.http_get = previous_get
                os.environ.pop("ALPHA_VANTAGE_API_KEY", None)
            self.assertEqual(code, 2)
            self.assertFalse((directory / "cache" / "quota-ledger.json").exists())


if __name__ == "__main__":
    unittest.main()
