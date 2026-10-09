import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from marv import upset

U = {"sport": "nfl", "game_id": "2026_05_TB_DAL", "start": "2026-10-09T00:15:00+00:00", "home": "Dallas Cowboys",
     "away": "Tampa Bay Buccaneers", "fav": "Dallas Cowboys", "dog": "Tampa Bay Buccaneers", "price": -450.0,
     "spread": -9.5, "heavy": "ML -450", "price_text": "-450", "reasons": ["Cowboys D bad (29th yds allowed)"],
     "verdict": "mixed"}


class UpsetTest(unittest.TestCase):
    def test_text(self):
        t = upset.text(U, paper=True)
        self.assertTrue(t.startswith("🚨 UPSET WATCH — Cowboys -450 · Cowboys D bad (29th yds allowed) — fade the favorite"))
        self.assertIn("paper upset watch", t)
        self.assertIn("Tampa Bay Buccaneers @ Dallas Cowboys", t)

    def test_defense_short(self):
        p = {"bad": True, "fading": False, "pa": 28, "ya": 392, "pa3": 28, "pa_rank": 5, "pa_of": 32, "ya_rank": 4, "ya_of": 32}
        self.assertEqual(upset.defense_short(p, "Cowboys"), "Cowboys D bad (29th yds allowed)")
        self.assertIsNone(upset.defense_short({"bad": False, "fading": False}, "X"))
        p2 = {**p, "bad": False, "fading": True, "pa3": 33, "pa": 24}
        self.assertEqual(upset.defense_short(p2, "Cowboys"), "Cowboys D trending down (33/g last 3 vs 24/g)")

    def test_notify_dedup_and_paper_log(self):
        with tempfile.TemporaryDirectory() as d:
            sent = []
            s = SimpleNamespace(state_dir=d, paper_mode=True, telegram_bot_token="t", telegram_chat_id="c")
            send = lambda tok, chat, text: sent.append(text)  # noqa: E731
            self.assertTrue(upset.notify(s, U, "scan", send))
            self.assertFalse(upset.notify(s, U, "march_edge_overlay", send))  # same game, same day
            self.assertEqual(len(sent), 1)
            log = json.loads((Path(d) / upset.LOG).read_text())
            (rec,) = log.values()
            self.assertEqual(rec["mode"], "paper upset watch")
            self.assertTrue(rec["paper"] and rec["sent"])
            self.assertIsNone(rec["result"])

    def test_send_failure_logged_not_resent(self):
        with tempfile.TemporaryDirectory() as d:
            s = SimpleNamespace(state_dir=d, paper_mode=True, telegram_bot_token="t", telegram_chat_id="c")

            def boom(*a):
                raise RuntimeError("down")
            self.assertFalse(upset.notify(s, U, "scan", boom))
            (rec,) = json.loads((Path(d) / upset.LOG).read_text()).values()
            self.assertFalse(rec["sent"])

    def test_disabled(self):
        import os
        os.environ["UPSET_ALERTS"] = "off"
        try:
            self.assertEqual(upset.scan(SimpleNamespace(state_dir="/nonexistent")), [])
        finally:
            del os.environ["UPSET_ALERTS"]


if __name__ == "__main__":
    unittest.main()
