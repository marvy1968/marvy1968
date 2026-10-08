import unittest
from datetime import datetime, timezone

from marv.data.euroleague_live import apply_header
from marv.data.espn import parse_event
from marv.models import Game


def g():
    return Game("E2026-12", "euroleague", datetime.now(timezone.utc), "Real Madrid", "Olympiacos")


class EuroLeagueLiveTests(unittest.TestCase):
    def test_end_of_quarter_live_and_final(self):
        a = g()
        apply_header(a, {"Live": True, "ScoreA": "41", "ScoreB": "37", "Quarter": "2", "RemainingPartialTime": "00:00"})
        self.assertEqual((a.info["state"], a.info["status_name"], a.info["period"]), ("in", "STATUS_END_PERIOD", 2))
        self.assertEqual((a.info["live_home"], a.info["live_away"]), (41.0, 37.0))
        b = g()
        apply_header(b, {"Live": True, "ScoreA": 20, "ScoreB": 22, "Quarter": 2, "RemainingPartialTime": "04:31"})
        self.assertEqual(b.info["status_name"], "STATUS_IN_PROGRESS")
        c = g()
        apply_header(c, {"Live": False, "ScoreA": "88", "ScoreB": "80", "Quarter": "4", "RemainingPartialTime": "00:00"})
        self.assertEqual(c.info["state"], "post")
        self.assertTrue(c.completed)
        self.assertEqual((c.home_score, c.away_score), (88.0, 80.0))
        d = g()
        apply_header(d, {"Live": False, "ScoreA": "0", "ScoreB": "0", "Quarter": "", "RemainingPartialTime": ""})
        self.assertEqual(d.info["state"], "pre")

    def test_espn_preseason_is_flagged(self):
        ev = {"id": "1", "date": "2026-10-08T23:30Z", "season": {"type": 1},
              "status": {"type": {"state": "pre", "name": "STATUS_SCHEDULED", "completed": False}},
              "competitions": [{"competitors": [
                  {"homeAway": "home", "score": "0", "team": {"displayName": "Boston Celtics", "abbreviation": "BOS"}},
                  {"homeAway": "away", "score": "0", "team": {"displayName": "New York Knicks", "abbreviation": "NY"}}]}]}
        self.assertTrue(parse_event(ev, "nba").info.get("preseason"))


if __name__ == "__main__":
    unittest.main()


class ESPNRangeFallbackTests(unittest.TestCase):
    def test_range_rejected_then_day_by_day(self):
        import requests
        from datetime import timedelta
        from marv.data.espn import ESPNClient

        class Resp:
            def __init__(self, code, events=()):
                self.status_code, self._events = code, list(events)
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.HTTPError(response=self)
            def json(self):
                return {"events": self._events}

        class Session:
            headers = {}
            def __init__(self):
                self.calls = []
            def get(self, url, params, timeout):
                self.calls.append(params["dates"])
                if "-" in params["dates"]:
                    return Resp(400)
                return Resp(200, [{"id": params["dates"]}, {"id": "dup"}])

        sess = Session()
        start = datetime(2026, 10, 8, 20, tzinfo=timezone.utc)
        events = ESPNClient(session=sess).scoreboard("football/nfl", start, start + timedelta(hours=10))
        self.assertEqual(sess.calls, ["20261008-20261009", "20261008", "20261009"])
        self.assertEqual([e["id"] for e in events], ["20261008", "dup", "20261009"])
