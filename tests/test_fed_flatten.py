import tempfile
import unittest
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from alpacabot import paper

ET = ZoneInfo("America/New_York")


class FakeSwing:
    calls = 0

    def __init__(self, state):
        self.state = state

    def close_all(self, now):
        FakeSwing.calls += 1
        return {"MSFT": "closed @ 500.00"}


class FedFlattenTests(unittest.TestCase):
    def setUp(self):
        FakeSwing.calls = 0

    def cfg(self, tmp):
        return {"at": dtime(13, 0), "until": dtime(15, 55), "state": tmp}

    def test_flattens_once_on_fed_day_after_13(self):
        with tempfile.TemporaryDirectory() as tmp:
            msgs = []
            self.assertIsNone(paper.fed_flatten(datetime(2026, 10, 28, 12, 59, tzinfo=ET), self.cfg(tmp), FakeSwing, msgs.append))
            out = paper.fed_flatten(datetime(2026, 10, 28, 13, 0, 20, tzinfo=ET), self.cfg(tmp), FakeSwing, msgs.append)
            self.assertEqual(out, {"MSFT": "closed @ 500.00"})
            self.assertIsNone(paper.fed_flatten(datetime(2026, 10, 28, 13, 5, tzinfo=ET), self.cfg(tmp), FakeSwing, msgs.append))
            self.assertEqual(FakeSwing.calls, 1)
            self.assertEqual(len(msgs), 1)
            self.assertTrue((Path(tmp) / "fed_flatten_2026-10-28.done").exists())

    def test_not_on_other_days_or_after_the_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(paper.fed_flatten(datetime(2026, 10, 27, 13, 30, tzinfo=ET), self.cfg(tmp), FakeSwing))
            self.assertIsNone(paper.fed_flatten(datetime(2026, 10, 28, 15, 56, tzinfo=ET), self.cfg(tmp), FakeSwing))
            self.assertIsNone(paper.fed_flatten(datetime(2026, 10, 28, 13, 30, tzinfo=ET), None, FakeSwing))
            self.assertEqual(FakeSwing.calls, 0)

    def test_failure_is_reported_not_retried(self):
        class Broken(FakeSwing):
            def close_all(self, now):
                raise RuntimeError("broker down")
        with tempfile.TemporaryDirectory() as tmp:
            msgs = []
            out = paper.fed_flatten(datetime(2026, 12, 9, 13, 1, tzinfo=ET), self.cfg(tmp), Broken, msgs.append)
            self.assertIn("error", out)
            self.assertIn("broker down", msgs[0])
            self.assertIsNone(paper.fed_flatten(datetime(2026, 12, 9, 13, 2, tzinfo=ET), self.cfg(tmp), Broken, msgs.append))

    def test_live_config(self):
        self.assertEqual(paper.FED_FLATTEN["at"], dtime(13, 0))
        self.assertEqual(paper.FED_FLATTEN["state"], "state/alpaca_swing")


if __name__ == "__main__":
    unittest.main()
