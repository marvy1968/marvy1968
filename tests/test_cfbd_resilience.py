import unittest
from datetime import datetime, timezone
from unittest import mock

import requests

from marv.betcard import tag_candidates
from marv.data.cfbd import CFBDClient
from marv.models import Game, Odds


class Resp:
    def __init__(self, code=200, data=None):
        self.status_code, self._d = code, data or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._d


class CFBDResilience(unittest.TestCase):
    def test_timeout_is_retried(self):
        sess = mock.Mock(headers={})
        sess.get.side_effect = [requests.ReadTimeout("slow"), Resp(503), Resp(200, [{"id": 1}])]
        with mock.patch("marv.data.cfbd.time.sleep"):
            self.assertEqual(CFBDClient("k", sess).games(2026), [{"id": 1}])
        self.assertEqual(sess.get.call_count, 3)

    def test_gives_up_after_three(self):
        sess = mock.Mock(headers={})
        sess.get.side_effect = requests.ReadTimeout("slow")
        with mock.patch("marv.data.cfbd.time.sleep"), self.assertRaises(requests.ReadTimeout):
            CFBDClient("k", sess).games(2026)

    def test_fcs_games_get_no_trend_bets(self):
        def game(gid, fbs):
            return Game(gid, "cfb", datetime(2026, 10, 10, tzinfo=timezone.utc), "Marist", "Columbia",
                        odds=Odds(total=43.5, over_price=-110, under_price=-110), info={"home_fbs": fbs, "away_fbs": fbs})
        tags = {"a": [("UNDER-FADE", "Over")], "b": [("UNDER-FADE", "Over")]}
        got = tag_candidates("cfb", [game("a", False), game("b", True)], tags)
        self.assertEqual(len(got), 1)


if __name__ == "__main__":
    unittest.main()


class CFBDCache(unittest.TestCase):
    def test_second_call_uses_cache(self):
        import tempfile
        sess = mock.Mock(headers={})
        sess.get.return_value = Resp(200, [{"id": 7}])
        with tempfile.TemporaryDirectory() as d:
            c = CFBDClient("k", sess, cache_dir=d)
            self.assertEqual(c.games(2026), c.games(2026))
            self.assertEqual(sess.get.call_count, 1)
