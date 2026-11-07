"""Synthetic append-only decision, gate, and delivery tests. No network."""
import importlib.util
import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from marv import betcard, decision_audit as audit
from marv.models import Game, Odds, Pick, Prediction
from marv.state import Store

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")
NIGHT = datetime(2030, 1, 1, 19, tzinfo=ET)
NEXT = datetime(2030, 1, 2, 19, tzinfo=ET)


def _ncaab():
    path = ROOT / "review" / "card-fixes" / "overlays" / "feed" / "marv_ncaab_card.py"
    spec = importlib.util.spec_from_file_location("review_marv_ncaab_card", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _play(gid, edge, start=NIGHT, margin=3.0, market="spread", side="Example Away"):
    line = -3.5 if market == "spread" else 140.0
    game = Game(gid, "ncaab", start, "Example Home", "Example Away",
                odds=Odds(spread=-3.5, total=140.0, home_ml=-150, away_ml=130))
    pick = Pick(market, side, line, -110, 0.60, 0.12, edge)
    return Prediction(game, 70, 67, margin, 140.0, 0.55, picks=[pick])


class DecisionAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.card = _ncaab()
        self.card._TRACKED.clear()
        self.card._RUN.clear()
        self.card._ALLOWED.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_ids_are_stable_and_exposure_is_once(self):
        first = audit.decision_id(ledger="qualified", sport="ncaab", game_id="g1", market="spread",
                                  side="Example Away", et_date=NIGHT.date())
        second = audit.decision_id(ledger="qualified", sport="ncaab", game_id="g1", market="spread",
                                   side="example away", et_date=NIGHT.date())
        other = audit.decision_id(ledger="qualified", sport="ncaab", game_id="g1", market="spread",
                                  side="Example Home", et_date=NIGHT.date())
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        audit.record_allocation(self.state, run_id="run-a", sport="ncaab", game_id="g1", market="spread",
                                side="Example Away", et_date=NIGHT.date(), stake_fraction=0.03, game="A @ H")
        again = audit.record_allocation(self.state, run_id="run-b", sport="ncaab", game_id="g1", market="spread",
                                        side="Example Away", et_date=NIGHT.date(), stake_fraction=0.01, game="A @ H")
        self.assertEqual(again["kind"], "reevaluation")
        self.assertEqual(again["preserved_stake_fraction"], 0.03)
        self.assertEqual(again["proposed_stake_fraction"], 0.01)
        rows = audit.events(self.state)
        self.assertEqual(sum(row["kind"] == "allocation" for row in rows), 1)
        self.assertEqual(audit.qualified_exposure("ncaab", NIGHT.date(), rows), 0.03)
        original = next(row for row in rows if row["kind"] == "allocation")
        self.assertEqual(original["stake_fraction"], 0.03)

    def test_append_is_idempotent_concurrent_and_skips_a_torn_line(self):
        first = audit.append_event(self.state, {"kind": "note", "idempotency_key": "same", "n": 1})
        second = audit.append_event(self.state, {"kind": "note", "idempotency_key": "same", "n": 2})
        self.assertEqual(first["n"], second["n"])
        errors = []

        def worker(i):
            try:
                audit.append_event(self.state, {"kind": "note", "idempotency_key": f"k{i}", "n": i})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        keys = {row["idempotency_key"] for row in audit.events(self.state)}
        self.assertTrue({f"k{i}" for i in range(24)} <= keys)
        with (self.state / audit.LEDGER).open("a") as handle:
            handle.write('{"idempotency_key": "torn"')
        self.assertEqual(sum(row["kind"] == "note" for row in audit.events(self.state)), 25)

    def test_legacy_and_audit_count_once_and_sports_stay_independent(self):
        legacy = [{
            "sport": "ncaab", "game_id": "old", "market": "spread", "side": "Example Away",
            "start": NIGHT.isoformat(), "price": -110, "prob": 0.6, "ncaab_stake": 0.03,
        }]
        audit.record_allocation(self.state, run_id="run-a", sport="ncaab", game_id="old", market="spread",
                                side="Example Away", et_date=NIGHT.date(), stake_fraction=0.03)
        preserved, prior = audit.exposure_context(
            "ncaab", {"old"}, legacy, lambda row: 0.03, audit.events(self.state),
            lambda value: NIGHT.date())
        self.assertEqual(prior, {})
        self.assertEqual(preserved[("old", "spread", "example away")]["stake_fraction"], 0.03)
        away = [{
            "sport": "ncaab", "game_id": "morning", "market": "spread", "side": "Example Away",
            "start": NIGHT.isoformat(), "price": -110, "prob": 0.6, "ncaab_stake": 0.12,
        }]
        _preserved, prior = audit.exposure_context("ncaab", set(), away, lambda row: 0.99, [], lambda value: NIGHT.date())
        self.assertAlmostEqual(prior[NIGHT.date()], 0.12)
        audit.record_allocation(self.state, run_id="run-a", sport="ncaab", game_id="full", market="spread",
                                side="Example Away", et_date=NIGHT.date(), stake_fraction=0.15)
        self.assertEqual(audit.qualified_exposure("ncaaw", NIGHT.date(), audit.events(self.state)), 0.0)
        woman = _play("w1", 0.2, start=NIGHT)
        woman.game.sport = "ncaaw"
        self.card.gate([woman], prior={}, sport="ncaaw")
        self.assertTrue(woman.picks[0].active)
        self.assertFalse(getattr(woman.picks[0], "_ncaab_track", None))

    def test_subslate_remainder_and_preserved_play_are_not_relabeled(self):
        self.assertAlmostEqual(self.card.stake_frac(_play("x", 0.2).picks[0]), 0.03)
        kept = _play("kept", 0.01, margin=3.0)
        self.card.gate([kept])
        self.assertTrue(kept.picks[0].active)
        self.card._commit(self.state, "ncaab", [kept])
        rerun = _play("kept", 0.01, margin=40.0)
        highs = [_play(f"n{i}", 0.5 - i / 100, margin=3.0) for i in range(5)]
        self.card._TRACKED.clear()
        self.card._gate_once([rerun, *highs], state_dir=self.state)
        self.assertTrue(rerun.picks[0].active)
        self.assertTrue(rerun.picks[0]._allocation_preserved)
        self.assertNotIn("night_cap", rerun.picks[0].vetoes[0] if rerun.picks[0].vetoes else "")
        self.assertFalse(any("model_gap" in str(v) for v in rerun.picks[0].vetoes))
        self.card._commit(self.state, "ncaab", [rerun, *highs])
        self.assertAlmostEqual(audit.qualified_exposure("ncaab", NIGHT.date(), audit.events(self.state)), 0.15)
        allocations = [row for row in audit.events(self.state) if row["kind"] == "allocation" and row["game_id"] == "kept"]
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0]["stake_fraction"], 0.03)
        stages = [row for row in audit.events(self.state) if row["kind"] == "gate_snapshot"]
        self.assertTrue(stages)
        raw = json.dumps(stages[0], sort_keys=True)
        self.card._commit(self.state, "ncaab", [rerun, *highs])
        again = next(row for row in audit.events(self.state) if row["idempotency_key"] == stages[0]["idempotency_key"])
        self.assertEqual(json.dumps(again, sort_keys=True), raw)

        late_only = [_play("late1", 0.4), _play("late2", 0.3)]
        tracked = self.card.gate(late_only, prior={NIGHT.date(): 0.12})
        self.assertTrue(late_only[0].picks[0].active)
        self.assertFalse(late_only[1].picks[0].active)
        self.assertIn("night_cap", tracked[0][2])
        other_night = _play("tomorrow", 0.4, start=NEXT)
        self.card.gate([other_night], prior={NIGHT.date(): 0.15})
        self.assertTrue(other_night.picks[0].active)

        cut = _play("cut", 0.4)
        self.card.gate([cut], prior={NIGHT.date(): 0.145})
        self.assertTrue(cut.picks[0].active)
        self.assertAlmostEqual(cut.picks[0]._ncaab_stake, 0.005)
        overflow = _play("over", 0.4)
        self.card.gate([overflow], prior={NIGHT.date(): 0.146})
        self.assertFalse(overflow.picks[0].active)
        self.assertIn("night_cap", overflow.picks[0]._ncaab_track)

    def test_track_history_and_legacy_grader_stay_put(self):
        pred = _play("hist", 0.2, margin=30.0)
        self.card.gate([pred])
        self.assertIn("model_gap", pred.picks[0]._ncaab_track)
        path = self.state / "ncaab_track_only.json"
        original = {
            "sport": "ncaab", "game_id": "hist", "market": "spread", "side": "Example Away",
            "track_gate": "ncaab_model_gap", "result": None, "stake": 0.0,
        }
        path.write_text(json.dumps([original]))
        before = path.read_text()
        self.card._TRACKED[id(pred.picks[0])] = (pred, pred.picks[0], "ncaab_night_cap")
        self.card._log_tracked(self.state, [pred])
        self.assertEqual(json.loads(path.read_text())[0]["track_gate"], "ncaab_model_gap")
        self.assertEqual(path.read_text(), before)
        fresh = _play("newtrack", 0.2, margin=30.0)
        self.card._TRACKED[id(fresh.picks[0])] = (fresh, fresh.picks[0], "ncaab_model_gap")
        self.card._log_tracked(self.state, [fresh])
        rows = json.loads(path.read_text())
        self.assertEqual(rows[0], original)
        self.assertEqual(rows[1]["game_id"], "newtrack")

        picks = self.state / "picks.json"
        ledger = [{
            "sport": "ncaab", "game_id": "graded", "start": NIGHT.isoformat(),
            "home": "Example Home", "away": "Example Away", "market": "ml", "side": "Example Home",
            "line": None, "price": -110, "prob": 0.6, "ev": 0.1, "edge": 0.05, "result": None,
        }]
        picks.write_text(json.dumps(ledger))
        finished = Game("graded", "ncaab", NIGHT, "Example Home", "Example Away",
                        completed=True, home_score=80, away_score=60)
        self.assertEqual(Store(self.state).resolve("ncaab", [finished]), 1)
        graded = json.loads(picks.read_text())[0]
        self.assertGreater(graded["result"], 0)
        self.assertEqual(json.loads(path.read_text())[0]["track_gate"], "ncaab_model_gap")
        self.card._grade_tracked(self.state, [finished])
        self.assertIsNone(json.loads(path.read_text())[0]["result"])

    def test_send_statuses_do_not_invent_a_receipt_or_retry_unknown(self):
        calls = []

        def unknown():
            calls.append("unknown")
            return None

        code = audit.apply_betcard_send(
            self.state, NIGHT, [], "synthetic card", unknown, lambda: None, lambda: None, lambda status: None)
        self.assertEqual(code, "unknown")
        delivery = next(row for row in audit.events(self.state) if row["kind"] == "delivery")
        self.assertIsNone(delivery["receipt"])
        self.assertIn("no provider receipt", delivery["note"])
        self.assertEqual(audit.apply_betcard_send(
            self.state, NIGHT, [], "synthetic card", unknown, lambda: None, lambda: None, lambda status: None), "blocked")
        self.assertEqual(calls, ["unknown"])

        def boom():
            calls.append("boom")
            raise RuntimeError("socket timed out after the request")

        marks, alerts = [], []
        code = audit.apply_betcard_send(
            self.state, NEXT, [], "other card", boom, lambda: (_ for _ in ()).throw(AssertionError("logged")),
            lambda: alerts.append(1), lambda status: marks.append(status.code))
        self.assertEqual(code, "unknown")
        self.assertEqual(marks, ["unknown"])
        self.assertEqual(alerts, [1])
        self.assertEqual(audit.apply_betcard_send(
            self.state, NEXT, [], "other card", boom, lambda: None, lambda: alerts.append(1),
            lambda status: marks.append(status.code)), "blocked")
        self.assertEqual(calls.count("boom"), 1)

        prewire = self.state / "prewire"
        prewire.mkdir()

        def missing():
            calls.append("missing")
            raise ValueError(audit._PREWIRE)

        marks.clear()
        code = audit.apply_betcard_send(
            prewire, NIGHT, [], "card", missing, lambda: None, lambda: alerts.append("pre"), lambda status: marks.append(status.code))
        self.assertEqual(code, "failed")
        self.assertEqual(marks, [])
        audit.apply_betcard_send(
            prewire, NIGHT, [], "card", missing, lambda: None, lambda: None, lambda status: None)
        self.assertEqual(calls.count("missing"), 2)

        logged = self.state / "logged"
        logged.mkdir()

        def ok():
            calls.append("ok")
            return None

        def broken_log():
            raise OSError("disk full")

        self.assertEqual(audit.apply_betcard_send(
            logged, NIGHT, [], "card", ok, broken_log, lambda: None, lambda status: None), "log_failure")
        self.assertEqual(calls.count("ok"), 1)
        self.assertEqual(next(row["status"] for row in audit.events(logged) if row["kind"] == "delivery"), "unknown")
        self.assertTrue(any(row["kind"] == "log_failure" for row in audit.events(logged)))

        acked = self.state / "acked"
        acked.mkdir()

        def receipt():
            return {"message_id": 15}

        status = audit.deliver(acked, run_id="run-r", channel="betcard", when=NIGHT, decision_ids=["dec_x"],
                               payload="card", send_fn=receipt)
        self.assertEqual(status.code, "acknowledged")
        self.assertEqual(next(row["receipt"] for row in audit.events(acked) if row["kind"] == "delivery"),
                         {"message_id": 15})
        again = audit.deliver(acked, run_id="run-r", channel="betcard", when=NIGHT, decision_ids=["dec_x"],
                              payload="card", send_fn=lambda: (_ for _ in ()).throw(AssertionError("retried")))
        self.assertEqual(again.code, "acknowledged")

        legacy = self.state / "legacy"
        legacy.mkdir()
        (legacy / "betcard_sent.json").write_text(json.dumps({NIGHT.date().isoformat(): {"games": ["old"]}}))
        self.assertTrue(audit.resend_blocked(legacy, NIGHT.date(), "betcard"))
        self.assertEqual(audit.apply_betcard_send(
            legacy, NIGHT, [], "card", lambda: (_ for _ in ()).throw(AssertionError("sent")),
            lambda: None, lambda: None, lambda status: None), "blocked")

    def test_internal_high_label_is_unchanged(self):
        saved = (betcard.candidates, betcard.select, betcard.text, betcard.HARD)
        try:
            self.card.apply_betcard()
            game = "Example Away @ Example Home"
            self.card._ALLOWED["ncaab"] = {self.card._bk("ncaab", game, "spread", "Example Away"): 2.5}
            bet = betcard.Bet("ncaab", game, NIGHT.isoformat(), "Example Home", "Example Away",
                              "spread", "Example Away", -3.5, -110, "SIGNAL", 0.08, 0.5, "synthetic")
            chosen = betcard.select([bet])
            self.assertEqual(chosen[0].tier, "HIGH")
            self.assertEqual(chosen[0].units, 2.5)
            self.assertIn("HIGH CONFIDENCE", betcard.text(chosen, NIGHT))
        finally:
            betcard.candidates, betcard.select, betcard.text, betcard.HARD = saved


if __name__ == "__main__":
    unittest.main()
