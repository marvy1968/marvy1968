"""Candidate adapters are callable and are not imported by the running tree."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(PKG / "integrations"))
sys.path.insert(0, str(REPO))

os.environ.pop("ODDS_ACCOUNTING_LEDGER", None)

from odds_accounting.ledger import read_state
import candidate_calls as calls


class Response:
    def __init__(self, status=200, headers=None, body=None):
        self.status_code = status
        self.status = status
        self.headers = headers or {"x-requests-last": "1", "x-requests-used": "5", "x-requests-remaining": "5"}
        self.history = []
        self._body = [{"id": "kept"}] if body is None else body
        self.reason = "synthetic"

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.sqlite"
        os.environ["ODDS_ACCOUNTING_LEDGER"] = str(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: os.environ.pop("ODDS_ACCOUNTING_LEDGER", None))

    def test_repo_direct_shapes_match_source_and_one_call(self):
        secret = "synthetic-" + "direct-key"
        for spec in calls.REPO_DIRECT:
            text = (REPO / spec["file"]).read_text()
            self.assertIn(spec["needle"], text)
            self.assertNotIn("candidate_calls", text)
            self.assertNotIn("odds_accounting", text)
            self.path.unlink(missing_ok=True)
            seen = []
            err = RuntimeError("same-object")

            def get(url, params=None, timeout=None, _seen=seen, _err=err, _spec=spec):
                _seen.append((url, dict(params), timeout))
                if _spec["id"].endswith("events") and False:
                    raise _err
                return Response()

            response = calls.run_repo_direct(spec, get, secret, bookmakers=["draftkings", "fanduel"])
            self.assertIsInstance(response, Response)
            self.assertEqual(len(seen), 1)
            url, params, timeout = seen[0]
            self.assertEqual(timeout, spec["timeout"])
            self.assertEqual(params["apiKey"], secret)
            self.assertNotIn("apiKey", url)
            self.assertIn(spec["sport"], url)
            row = read_state(self.path)["attempts"][0]
            self.assertEqual(row["source_job"], spec["id"])
            self.assertNotIn(secret.encode(), self.path.read_bytes())
            self.path.unlink(missing_ok=True)

            def boom(url, params=None, timeout=None):
                raise err

            with self.assertRaises(RuntimeError) as ctx:
                calls.run_repo_direct(spec, boom, secret)
            self.assertIs(ctx.exception, err)

    def test_vm_wrappers_tag_and_keep_exceptions(self):
        secret = "synthetic-" + "vm-key"
        seen = []

        def fetch(api_key, key, source_job=None):
            seen.append((api_key, key, source_job))
            if key == "bad":
                raise RuntimeError("provider")
            return [{"id": key}]

        events, errors = calls.vm_live_monitor_fetch(fetch, secret, ["basketball_nba", "bad"])
        self.assertEqual(events, [{"id": "basketball_nba"}])
        self.assertIs(errors[0][1].__class__, RuntimeError)
        self.assertEqual(seen[0][2], calls.VM_LIVE)

        def events_fn(api_key, sport, source_job=None):
            seen.append(("events", sport, source_job))
            return [{"id": "e"}]

        def props_fn(api_key, sport, event_id, markets, book, budget=None, source_job=None):
            seen.append(("props", sport, event_id, markets, book, budget, source_job))
            return {"id": event_id}

        self.assertEqual(calls.vm_main_probe_events(events_fn, secret), [{"id": "e"}])
        self.assertEqual(calls.vm_main_probe_props(props_fn, secret, "eid", ["player_points"], "bovada"), {"id": "eid"})
        self.assertEqual(seen[-2][2], calls.VM_MAIN_PROBE)
        self.assertEqual(seen[-1][-1], calls.VM_MAIN_PROBE)
        calls.vm_live_prop_engine_events(events_fn, secret, "basketball_nba")
        calls.vm_live_prop_engine_props(props_fn, secret, "basketball_nba", "eid", ["player_points"])
        self.assertEqual(seen[-2][2], calls.VM_LIVE_PROP_ENGINE)
        self.assertEqual(seen[-1][4], "bovada")
        calls.vm_alt70_events(events_fn, secret, "americanfootball_nfl")
        calls.vm_alt70_props(props_fn, secret, "americanfootball_ncaaf", "eid", ["alternate_team_totals"], object())
        self.assertEqual(seen[-2][2], calls.VM_ALT70_NFL)
        self.assertEqual(seen[-1][-1], calls.VM_ALT70_NCAAF)

    def test_running_imports_do_not_name_the_candidate(self):
        untouched = [
            "marv/live_monitor.py", "marv/main.py", "marv/live_prop_engine.py",
            "tools/fetch_bovada_nfl.py",
            "forex_bots/masterbot_oanda/app.py",
            "forex_bots/masterbot_oanda/live_odds_engine.py",
            "forex_bots/masterbot_oanda/build_backtest_data.py",
            "forex_bots/masterbot_oanda/build_backtest_history.py",
            "forex_bots/masterbot_oanda/live_engine/payload_builder.py",
        ]
        for relative in untouched:
            text = (REPO / relative).read_text()
            self.assertNotIn("candidate_calls", text)
            self.assertNotIn("odds_accounting", text)
            self.assertNotIn("source_job", text)
        self.assertIn("urlopen follows redirects", (PKG / "integrations/shadow_collector/odds_request.py").read_text())
        self.assertNotIn("ODDS_ACCOUNTING_LEDGER=", (PKG / "candidate_calls.py").read_text())

    def test_aiohttp_shape_observes_status(self):
        import asyncio
        secret = "synthetic-" + "async-key"
        seen = []

        class Resp:
            status = 200
            headers = {"x-requests-last": "3", "x-requests-used": "3", "x-requests-remaining": "3"}
            history = []

            def raise_for_status(self):
                return None

            async def json(self):
                return [{"async": True}]

        class CM:
            async def __aenter__(self):
                return Resp()

            async def __aexit__(self, *args):
                return False

        class Session:
            def get(self, url, params=None):
                seen.append((url, dict(params)))
                return CM()

        url = "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds"
        params = {"apiKey": secret, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}

        async def run():
            response = await calls.aiohttp_get(
                Session(), url, params, source_job="repo-live-odds-engine",
                endpoint_template="/v4/sports/{sport}/odds", sport="basketball_ncaab",
                markets=["h2h", "spreads", "totals"], regions=["us"])
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), [{"async": True}])

        asyncio.run(run())
        self.assertEqual(seen[0][0], url)
        self.assertEqual(seen[0][1]["apiKey"], secret)
        row = read_state(self.path)["attempts"][0]
        self.assertEqual(row["source_job"], "repo-live-odds-engine")
        self.assertEqual(row["http_status"], 200)
        self.assertEqual(row["actual_header_cost"], 3)
        self.assertNotIn(secret.encode(), self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
