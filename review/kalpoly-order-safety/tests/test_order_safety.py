"""Offline tests for the review-only order intent candidate.

No socket is opened. Transports are callables. Passing these tests is not
qualification to send a real order.
"""

from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from order_safety.callers import CALL_SITES, place_demo, place_ioc_then_rest, paper_fill
from order_safety.host import ProductionRefused
from order_safety.machine import NoRealPolymarketOrderApi, OrderMachine, SubmitBlocked
from order_safety.mp_support import contend

DEMO = "https://demo-api.kalshi.co/trade-api/v2"
OTHER_DEMO = "https://external-api.demo.kalshi.co/trade-api/v2"
PROD = "https://trading-api.kalshi.com/trade-api/v2"


def _ack(order, fill):
    return {"outcome": "ack", "order_id": "venue-1", "fill_count": fill if fill is not None else order["count"]}


class OrderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "ledger.sqlite")
        self.machine = OrderMachine(self.path)
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def _place(self, transport, decision="d1", caller="runner", count=5, limit_cents=40, account="demo-a"):
        return place_demo(
            self.machine, transport, caller=caller, account_label=account, decision_id=decision,
            ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="yes", count=count, limit_cents=limit_cents)

    def test_intent_is_durable_before_transport_and_lost_ack_does_not_resubmit(self):
        seen = {}

        def transport(order):
            row = self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")[0]
            seen["state_during_post"] = row["state"]
            seen["client"] = row["client_order_id"]
            self.calls.append(order["client_order_id"])
            raise TimeoutError("response lost after accept")

        first = self._place(transport)
        self.assertEqual(seen["state_during_post"], "submitting")
        self.assertEqual(first["state"], "submit_unknown")
        self.assertEqual(first["client_order_id"], seen["client"])
        self.assertTrue(self.machine.is_blocked(venue="kalshi_demo", mode="demo", account_label="demo-a"))
        second = self._place(transport)
        self.assertFalse(second["submitted_this_call"])
        self.assertEqual(self.calls, [seen["client"]])
        with self.assertRaises(SubmitBlocked):
            self._place(transport, decision="d2")
        self.assertEqual(len(self.calls), 1)

    def test_unparseable_ack_is_unknown_and_is_not_resent(self):
        def transport(order):
            self.calls.append(order)
            return {"outcome": "ack"}

        row = self._place(transport, count=4, limit_cents=41)
        self.assertEqual(row["state"], "submit_unknown")
        self.assertEqual(self.calls[0]["count"], 4)
        self.assertEqual(self.calls[0]["limit_cents"], 41)
        again = self._place(transport, count=4, limit_cents=41)
        self.assertFalse(again["submitted_this_call"])
        self.assertEqual(len(self.calls), 1)

    def test_explicit_reconcile_clears_a_lost_ack_without_a_second_post(self):
        def transport(order):
            self.calls.append(order["client_order_id"])
            raise ConnectionError("lost")

        row = self._place(transport)
        blocked = self.machine.reconcile(
            {"orders": []}, venue="kalshi_demo", mode="demo", account_label="demo-a", base_url=DEMO)
        self.assertTrue(blocked["blocked"])
        self.assertEqual(self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")[0]["state"],
                         "submit_unknown")
        done = self.machine.reconcile(
            {"orders": [{"client_order_id": row["client_order_id"], "status": "executed",
                         "filled_count": 5, "order_id": "venue-9"}]},
            venue="kalshi_demo", mode="demo", account_label="demo-a", base_url=DEMO)
        self.assertTrue(done["ok"])
        settled = self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")[0]
        self.assertEqual(settled["state"], "filled")
        self.assertEqual(settled["filled_count"], 5)
        self.assertFalse(self.machine.is_blocked(venue="kalshi_demo", mode="demo", account_label="demo-a"))
        self._place(lambda order: self.calls.append(order) or _ack(order, 2), decision="d2", count=2)
        self.assertEqual(len(self.calls), 2)

    def test_concurrent_writers_share_one_client_id(self):
        counter = str(Path(self.tmp.name) / "posts.txt")
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=contend, args=(self.path, "same-decision", counter)) for _ in range(2)]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(10)
            self.assertEqual(proc.exitcode, 0)
        lines = Path(counter).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        rows = self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["client_order_id"], lines[0])
        if self.machine.is_blocked(venue="kalshi_demo", mode="demo", account_label="demo-a"):
            result = self.machine.reconcile(
                {"orders": [{"client_order_id": lines[0], "status": "filled", "filled_count": 2, "order_id": "venue-1"}]},
                venue="kalshi_demo", mode="demo", account_label="demo-a", base_url=DEMO)
            self.assertTrue(result["ok"])

    def test_ioc_remainder_is_exact_and_a_lost_ioc_opens_no_child(self):
        def ioc(order):
            self.calls.append(("ioc", order["count"], order["client_order_id"]))
            return _ack(order, 2)

        def rest(order):
            self.calls.append(("rest", order["count"], order["client_order_id"]))
            return _ack(order, order["count"])

        out = place_ioc_then_rest(
            self.machine, ioc, rest, caller="me_kalshi", account_label="demo-a", decision_id="pick-1",
            ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="no", count=5, limit_cents=37)
        self.assertEqual(out["parent"]["filled_count"], 2)
        self.assertEqual(out["child"]["count"], 3)
        self.assertEqual(out["child"]["limit_cents"], 37)
        self.assertEqual(out["child"]["side"], "no")
        self.assertNotEqual(out["parent"]["client_order_id"], out["child"]["client_order_id"])
        self.assertEqual([(kind, count) for kind, count, _cid in self.calls], [("ioc", 5), ("rest", 3)])
        again = place_ioc_then_rest(
            self.machine, ioc, rest, caller="me_kalshi", account_label="demo-a", decision_id="pick-1",
            ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="no", count=5, limit_cents=37)
        self.assertFalse(again["parent"]["submitted_this_call"])
        self.assertFalse(again["child"]["submitted_this_call"])
        self.assertEqual(len(self.calls), 2)
        rows = self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")
        self.assertEqual(sum(row["filled_count"] for row in rows), 5)

        lost = OrderMachine(str(Path(self.tmp.name) / "lost.sqlite"))
        rest_calls = []
        parent = place_ioc_then_rest(
            lost, lambda order: (_ for _ in ()).throw(TimeoutError("lost")),
            lambda order: rest_calls.append(order),
            caller="nba_pacer", account_label="demo-a", decision_id="pick-2",
            ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="yes", count=5, limit_cents=40)
        self.assertIsNone(parent["child"])
        self.assertEqual(rest_calls, [])
        self.assertEqual(parent["parent"]["state"], "submit_unknown")

    def test_cancel_response_is_not_cancellation(self):
        row = self._place(lambda order: _ack(order, 5), caller="live_runner")
        cancelled = self.machine.request_cancel(
            lambda payload: {"reduced_by": 5, "http": 200},
            intent_id=row["intent_id"], account_label="demo-a", venue="kalshi_demo", mode="demo", base_url=DEMO)
        self.assertEqual(cancelled["state"], "cancel_unknown")
        self.assertTrue(self.machine.is_blocked(venue="kalshi_demo", mode="demo", account_label="demo-a"))
        with self.assertRaises(SubmitBlocked):
            self._place(lambda order: self.calls.append(order), decision="next")
        self.assertEqual(self.calls, [])
        inferred = self.machine.reconcile(
            {"orders": [{"client_order_id": row["client_order_id"], "status": "expired_or_filled", "filled_count": 5}]},
            venue="kalshi_demo", mode="demo", account_label="demo-a", base_url=DEMO)
        self.assertFalse(inferred["ok"])
        self.assertEqual(
            self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")[0]["state"],
            "cancel_unknown")
        explicit = self.machine.reconcile(
            {"orders": [{"client_order_id": row["client_order_id"], "status": "canceled",
                         "filled_count": 5, "order_id": "venue-1"}]},
            venue="kalshi_demo", mode="demo", account_label="demo-a", base_url=DEMO)
        self.assertTrue(explicit["ok"])
        self.assertEqual(
            self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")[0]["state"],
            "cancelled")

    def test_inventory_disagreement_blocks_and_does_not_infer(self):
        row = self._place(lambda order: (_ for _ in ()).throw(TimeoutError("lost")))
        extra = self.machine.reconcile(
            {"orders": [
                {"client_order_id": row["client_order_id"], "status": "executed", "filled_count": 5, "order_id": "v"},
                {"client_order_id": "not-ours", "status": "executed", "filled_count": 1, "order_id": "other"},
            ]},
            venue="kalshi_demo", mode="demo", account_label="demo-a", base_url=DEMO)
        self.assertFalse(extra["ok"])
        self.assertEqual(
            self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")[0]["state"],
            "submit_unknown")
        mismatch = OrderMachine(str(Path(self.tmp.name) / "mismatch.sqlite"))
        known = place_demo(
            mismatch, lambda order: _ack(order, 2), caller="marv_cbb", account_label="demo-a", decision_id="m",
            ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="yes", count=5, limit_cents=40)
        bad = mismatch.reconcile(
            {"orders": [{"client_order_id": known["client_order_id"], "status": "filled", "filled_count": 4}]},
            venue="kalshi_demo", mode="demo", account_label="demo-a", base_url=DEMO)
        self.assertFalse(bad["ok"])
        self.assertEqual(mismatch.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")[0]["filled_count"], 2)
        with self.assertRaises(SubmitBlocked):
            place_demo(
                mismatch, lambda order: self.calls.append(order), caller="marv_cbb", account_label="demo-a",
                decision_id="other", ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="yes", count=1, limit_cents=40)
        self.assertEqual(self.calls, [])

    def test_recovery_crosschecks_identity_mode_and_host(self):
        row = self._place(lambda order: _ack(order, 1), count=1, caller="runner_props")
        with self.assertRaises(SubmitBlocked):
            self.machine.request_cancel(
                lambda payload: {"http": 200}, intent_id=row["intent_id"], account_label="other-account",
                venue="kalshi_demo", mode="demo", base_url=DEMO)
        with self.assertRaises(ValueError):
            place_demo(
                self.machine, lambda order: self.calls.append(order), caller="runner", account_label="unknown",
                decision_id="x", ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="yes", count=1, limit_cents=40)
        with self.assertRaises(ValueError):
            self.machine.submit(
                lambda order: self.calls.append(order), venue="kalshi_demo", mode="live", account_label="demo-a",
                decision_id="live-1", ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="yes", count=1, limit_cents=40,
                caller="runner", base_url=DEMO)
        host = self.machine.reconcile(
            {"orders": [{"client_order_id": row["client_order_id"], "status": "filled", "filled_count": 1}]},
            venue="kalshi_demo", mode="demo", account_label="demo-a", base_url=OTHER_DEMO)
        self.assertFalse(host["ok"])
        self.assertEqual(self.calls, [])
        self.assertEqual(
            self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a")[0]["state"], "filled")

    def test_production_host_is_refused_before_transport(self):
        with self.assertRaises(ProductionRefused):
            place_demo(
                self.machine, lambda order: self.calls.append(order), caller="runner", account_label="demo-a",
                decision_id="prod", ticker="KXNBAGAME-26NOV08BOSNYK-BOS", side="yes", count=1, limit_cents=40,
                base_url=PROD)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.machine.list_intents(venue="kalshi_demo", mode="demo", account_label="demo-a"), [])

    def test_every_kalshi_call_site_uses_one_stable_id_and_keeps_size(self):
        first_id = None
        for caller in CALL_SITES:
            def transport(order, caller=caller):
                self.calls.append((caller, order["count"], order["limit_cents"], order["client_order_id"]))
                return _ack(order, 1)

            row = self._place(transport, decision="shared", caller=caller, count=3, limit_cents=44, account="demo-b")
            first_id = first_id or row["client_order_id"]
            self.assertEqual(row["client_order_id"], first_id)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1:], (3, 44, first_id))

    def test_paper_persistence_is_local_and_refuses_a_network_target(self):
        def transport(order):
            self.calls.append(order)
            raise TimeoutError("lost local ack")

        row = paper_fill(
            self.machine, transport, account_label="paper-a", decision_id="slug-1", slug="nba-bos-nyk",
            side="yes", count=2, limit_cents=51, fee_text="0.0695")
        self.assertEqual(row["state"], "submit_unknown")
        self.assertEqual(row["fee_text"], "0.0695")
        self.assertIsNone(row["host"])
        again = paper_fill(
            self.machine, transport, account_label="paper-a", decision_id="slug-1", slug="nba-bos-nyk",
            side="yes", count=2, limit_cents=51, fee_text="0.0695")
        self.assertFalse(again["submitted_this_call"])
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(NoRealPolymarketOrderApi):
            self.machine.submit(
                transport, venue="polymarket_paper", mode="paper", account_label="paper-a", decision_id="slug-2",
                ticker="nba-bos-nyk", side="yes", count=1, limit_cents=40, caller="poly_paper",
                base_url="https://clob.polymarket.com")
        self.assertEqual(len(self.calls), 1)

    def test_candidate_source_does_not_open_a_network_client(self):
        root = Path(__file__).resolve().parents[1] / "order_safety"
        banned = ("import requests", "urlopen", "import socket", "http.client", "time.sleep")
        for path in root.glob("*.py"):
            if path.name == "mp_support.py":
                continue
            text = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, text, path.name)


if __name__ == "__main__":
    unittest.main()
