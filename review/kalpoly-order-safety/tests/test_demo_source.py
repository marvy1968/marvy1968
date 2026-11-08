"""Source-backed tests for the review DEMO client.

The HTTP callable is a fake. These tests show the request JSON the candidate
would send. They do not qualify a live order, and they do not import the
running caller modules.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from order_safety.caller_status import CALLER_PATHS
from order_safety.demo_client import AmbiguousLegacyOrder, DemoClient, FundingUnsupported
from order_safety.host import ProductionRefused
from order_safety.machine import OrderMachine, SubmitBlocked, client_order_id_for

DEMO = "https://demo-api.kalshi.co/trade-api/v2"
PROD = "https://trading-api.kalshi.com/trade-api/v2"
SHARD0 = {"exchange_index": 0}
TICKER = "KXNBAGAME-26NOV08BOSNYK-BOS"


class DemoSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "ledger.sqlite")
        self.http_calls = []
        self.machine = OrderMachine(self.path)
        self.client = DemoClient(self.machine, self._http, account_label="demo-a", base_url=DEMO)

    def tearDown(self):
        self.tmp.cleanup()

    def _http(self, request):
        self.http_calls.append(request)
        raise AssertionError("this test must replace _http")

    def _client(self, http, account="demo-a", base=DEMO):
        return DemoClient(self.machine, http, account_label=account, base_url=base)

    def test_ioc_json_uses_the_persisted_client_id_and_a_lost_response_is_not_resent(self):
        expected = client_order_id_for("kalshi_demo.demo.demo-a.pick-1")

        def http(request):
            self.http_calls.append(request)
            row = self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")[0]
            self.assertEqual(row["state"], "submitting")
            self.assertEqual(request["json"]["client_order_id"], row["client_order_id"])
            raise TimeoutError("response lost after accept")

        client = self._client(http)
        out = client.place_order(
            TICKER, "yes", 5, 40, decision_id="pick-1", shard_fact=SHARD0, rest_enabled=False)
        body = self.http_calls[0]["json"]
        self.assertEqual(self.http_calls[0]["method"], "POST")
        self.assertEqual(self.http_calls[0]["path"], "/portfolio/events/orders")
        self.assertEqual(body, {
            "ticker": TICKER,
            "client_order_id": expected,
            "side": "bid",
            "count": "5.00",
            "price": "0.4000",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
        })
        self.assertNotEqual(body["client_order_id"], str(uuid.uuid4()))
        self.assertEqual(out["parent"]["state"], "submit_unknown")
        self.assertIsNone(out["child"])
        restarted = DemoClient(OrderMachine(self.path), http, account_label="demo-a", base_url=DEMO)
        again = restarted.place_order(
            TICKER, "yes", 5, 40, decision_id="pick-1", shard_fact=SHARD0, rest_enabled=False)
        self.assertFalse(again["parent"]["submitted_this_call"])
        self.assertEqual(len(self.http_calls), 1)
        self.assertEqual(again["parent"]["client_order_id"], expected)

    def test_no_price_body_and_exact_remainder_use_a_second_persisted_id(self):
        def http(request):
            self.http_calls.append(request)
            kind = request["json"]["time_in_force"]
            fill = 2 if kind == "immediate_or_cancel" else 1
            return {"order_id": "venue-" + kind, "fill_count": fill, "client_order_id": request["json"]["client_order_id"]}

        out = self._client(http).place_order(
            TICKER, "no", 5, 37, decision_id="pick-no", shard_fact=SHARD0,
            rest_enabled=True, now_s=1_700_000_000, rest_seconds=300)
        self.assertEqual(self.http_calls[0]["json"]["side"], "ask")
        self.assertEqual(self.http_calls[0]["json"]["price"], "0.6300")
        self.assertEqual(self.http_calls[0]["json"]["count"], "5.00")
        self.assertEqual(self.http_calls[1]["json"]["count"], "3.00")
        self.assertEqual(self.http_calls[1]["json"]["price"], "0.6300")
        self.assertEqual(self.http_calls[1]["json"]["time_in_force"], "good_till_canceled")
        self.assertEqual(self.http_calls[1]["json"]["expiration_ts"], 1_700_000_300)
        self.assertNotEqual(self.http_calls[0]["json"]["client_order_id"], self.http_calls[1]["json"]["client_order_id"])
        self.assertEqual(out["parent"]["filled_count"], 2)
        self.assertEqual(out["child"]["count"], 3)
        self.assertEqual(out["child"]["filled_count"], 1)
        self.assertEqual(out["parent"]["filled_count"] + out["child"]["filled_count"], 3)
        rows = self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")
        self.assertEqual(sum(row["count"] for row in rows), 8)
        self.assertEqual(sum(row["filled_count"] for row in rows), 3)

    def test_repeated_snapshots_do_not_change_fills_and_a_disagreement_blocks(self):
        def http(request):
            self.http_calls.append(request)
            fill = 2 if request["json"]["time_in_force"] == "immediate_or_cancel" else 3
            return {"order_id": "venue-" + request["json"]["client_order_id"][:8], "fill_count": fill}

        out = self._client(http).place_order(
            TICKER, "yes", 5, 40, decision_id="pick-sum", shard_fact=SHARD0,
            rest_enabled=True, now_s=50, rest_seconds=300)
        self.assertEqual(out["parent"]["filled_count"] + out["child"]["filled_count"], 5)
        snapshot = {"orders": [
            {"client_order_id": out["parent"]["client_order_id"], "status": "executed",
             "filled_count": 2, "order_id": out["parent"]["venue_order_id"]},
            {"client_order_id": out["child"]["client_order_id"], "status": "resting",
             "filled_count": 3, "order_id": out["child"]["venue_order_id"]},
            {"client_order_id": out["child"]["client_order_id"], "status": "resting",
             "filled_count": 3, "order_id": out["child"]["venue_order_id"]},
        ]}
        client = self._client(http)
        self.assertTrue(client.reconcile(snapshot)["ok"])
        self.assertTrue(client.reconcile(snapshot)["ok"])
        rows = {row["role"]: row for row in self.machine.list_intents(
            venue="kalshi_demo", mode="demo", account_label="demo-a")}
        self.assertEqual(rows["parent"]["filled_count"], 2)
        self.assertEqual(rows["resting_child"]["filled_count"], 3)
        self.assertEqual(len(self.http_calls), 2)
        conflict = {"orders": [
            {"client_order_id": out["child"]["client_order_id"], "status": "filled", "filled_count": 3,
             "order_id": out["child"]["venue_order_id"]},
            {"client_order_id": out["child"]["client_order_id"], "status": "filled", "filled_count": 1,
             "order_id": out["child"]["venue_order_id"]},
        ]}
        blocked = client.reconcile(conflict)
        self.assertFalse(blocked["ok"])
        self.assertEqual(rows["resting_child"]["filled_count"], self.machine.list_intents(
            venue="kalshi_demo", mode="demo", account_label="demo-a")[1]["filled_count"])

    def test_malformed_wrong_client_and_wrong_order_do_not_open_a_child_or_fill(self):
        cases = [
            {"order_id": "v1"},
            {"order_id": "v1", "fill_count": "2"},
            {"order_id": "v1", "fill_count": 2.0},
            {"order_id": "v1", "fill_count": 2, "client_order_id": "not-the-persisted-id"},
            {"order_id": "v1", "fill_count": 2, "ticker": "OTHER-TICKER"},
            "not-json",
        ]
        for index, payload in enumerate(cases):
            def http(request, payload=payload):
                self.http_calls.append(request)
                return payload

            machine = OrderMachine(str(Path(self.tmp.name) / f"bad-{index}.sqlite"))
            client = DemoClient(machine, http, account_label="demo-a", base_url=DEMO)
            out = client.place_order(
                TICKER, "yes", 5, 40, decision_id=f"bad-{index}", shard_fact=SHARD0,
                rest_enabled=True, now_s=10)
            self.assertIsNone(out["child"])
            self.assertEqual(out["parent"]["state"], "submit_unknown")
            self.assertIsNone(out["parent"]["filled_count"])
            self.assertEqual(len(self.http_calls), index + 1)

    def test_cancel_sweep_stays_unknown_and_legacy_ids_are_not_adopted(self):
        def http(request):
            self.http_calls.append(request)
            if request["method"] == "DELETE":
                return {"reduced_by": 3, "http": 200}
            fill = 2 if request["json"]["time_in_force"] == "immediate_or_cancel" else 1
            return {"order_id": "venue-" + request["json"]["time_in_force"], "fill_count": fill}

        client = self._client(http)
        out = client.place_order(
            TICKER, "yes", 5, 40, decision_id="rest-1", shard_fact=SHARD0,
            rest_enabled=True, now_s=100, rest_seconds=300)
        self.assertEqual(out["child"]["state"], "partial")
        swept = client.sweep_resting(400)
        self.assertEqual(swept["sent"], [out["child"]["intent_id"]])
        delete = [call for call in self.http_calls if call["method"] == "DELETE"]
        self.assertEqual(delete, [{
            "method": "DELETE",
            "path": "/portfolio/events/orders/venue-good_till_canceled",
            "json": None,
            "base_url": DEMO,
        }])
        child = [row for row in self.machine.list_intents(
            venue="kalshi_demo", mode="demo", account_label="demo-a") if row["role"] == "resting_child"][0]
        self.assertEqual(child["state"], "cancel_unknown")
        client.sweep_resting(500)
        self.assertEqual(len([call for call in self.http_calls if call["method"] == "DELETE"]), 1)
        before = len(self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a"))
        with self.assertRaises(AmbiguousLegacyOrder):
            client.sweep_resting(500, legacy_rows=[{"order_id": str(uuid.uuid4()), "ticker": TICKER, "count": 3}])
        self.assertEqual(len(self.machine.list_intents(
            venue="kalshi_demo", mode="demo", account_label="demo-a")), before)
        self.assertTrue(self.machine.is_blocked(venue="kalshi_demo", mode="demo", account_label="demo-a"))

    def test_nonterminal_restart_snapshot_does_not_invent_a_terminal_state(self):
        def http(request):
            self.http_calls.append(request)
            if request["json"]["time_in_force"] != "immediate_or_cancel":
                raise TimeoutError("rest response lost")
            return {"order_id": "venue-ioc", "fill_count": 2}

        client = self._client(http)
        out = client.place_order(
            TICKER, "yes", 5, 40, decision_id="cold-1", shard_fact=SHARD0,
            rest_enabled=True, now_s=20)
        self.assertEqual(out["parent"]["state"], "partial")
        self.assertEqual(out["child"]["state"], "submit_unknown")
        restarted = DemoClient(OrderMachine(self.path), http, account_label="demo-a", base_url=DEMO)
        with self.assertRaises(SubmitBlocked):
            restarted.place_order(
                TICKER, "yes", 1, 40, decision_id="cold-2", shard_fact=SHARD0, rest_enabled=False)
        self.assertEqual(len(self.http_calls), 2)
        result = restarted.reconcile({"orders": [
            {"client_order_id": out["parent"]["client_order_id"], "status": "executed",
             "filled_count": 2, "order_id": "venue-ioc"},
        ]})
        self.assertFalse(result["ok"])
        rows = {row["decision_id"]: row for row in self.machine.list_intents(
            venue="kalshi_demo", mode="demo", account_label="demo-a")}
        self.assertEqual(rows["cold-1"]["state"], "partial")
        self.assertEqual(rows["cold-1.rest"]["state"], "submit_unknown")
        self.assertNotIn(rows["cold-1.rest"]["state"], {"filled", "cancelled"})

    def test_funding_shortfall_or_unknown_shard_sends_nothing(self):
        def http(request):
            self.http_calls.append(request)
            return {"order_id": "should-not-send", "fill_count": 1}

        client = self._client(http)
        for fact in (None, {"exchange_index": 3}, {"exchange_index": 3, "balances_cents": {3: 10, 0: 5000}}):
            with self.assertRaises(FundingUnsupported):
                client.place_order(
                    TICKER, "yes", 5, 40, decision_id="fund-1", shard_fact=fact, rest_enabled=False)
        self.assertEqual(self.http_calls, [])
        self.assertEqual(self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a"), [])
        covered = {"exchange_index": 3, "balances_cents": {3: 5000, 0: 0}}
        client.place_order(TICKER, "yes", 1, 40, decision_id="fund-ok", shard_fact=covered, rest_enabled=False)
        self.assertEqual(len(self.http_calls), 1)
        self.assertNotIn("intra_exchange", self.http_calls[0]["path"])

    def test_wrong_host_account_and_order_id_are_rejected(self):
        def http(request):
            self.http_calls.append(request)
            return {"order_id": "venue-real", "fill_count": 1}

        out = self._client(http).place_order(
            TICKER, "yes", 1, 40, decision_id="id-1", shard_fact=SHARD0, rest_enabled=False)
        with self.assertRaises(ProductionRefused):
            DemoClient(self.machine, http, account_label="demo-a", base_url=PROD)
        other = DemoClient(self.machine, http, account_label="demo-b", base_url=DEMO)
        borrowed = other.reconcile({"orders": [
            {"client_order_id": out["parent"]["client_order_id"], "status": "filled",
             "filled_count": 1, "order_id": "venue-real"},
        ]})
        self.assertFalse(borrowed["ok"])
        self.assertEqual(self.machine.list_intents(
            venue="kalshi_demo", mode="demo", account_label="demo-b"), [])
        mismatch = self.client.reconcile({"orders": [
            {"client_order_id": out["parent"]["client_order_id"], "status": "filled",
             "filled_count": 1, "order_id": "some-other-order"},
        ]})
        self.assertFalse(mismatch["ok"])
        self.assertEqual(self.machine.list_intents(
            venue="kalshi_demo", mode="demo", account_label="demo-a")[0]["venue_order_id"], "venue-real")
        self.assertEqual(len(self.http_calls), 1)

    def test_reported_callers_are_blocked_and_the_client_does_not_use_the_legacy_path(self):
        self.assertGreaterEqual(len(CALLER_PATHS), 6)
        for item in CALLER_PATHS:
            self.assertEqual(item["status"], "blocked")
            self.assertTrue(item["reason"])
        root = Path(__file__).resolve().parents[1] / "order_safety"
        for name in ("demo_client.py", "demo_body.py", "caller_status.py"):
            text = (root / name).read_text(encoding="utf-8")
            for banned in ("uuid4", "import requests", "os.environ", "ensure_shard_funds",
                           "intra_exchange", "time.sleep", "KalshiDemo"):
                self.assertNotIn(banned, text, name)
        self.assertNotIn("import requests", (root / "machine.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
