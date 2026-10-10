import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from marv import parlay

NOW = datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc)


def _event(home, away, ml=None, total=None, book="bovada", start="2026-10-10T23:30:00Z"):
    mk = []
    if ml:
        mk.append({"key": "h2h", "outcomes": [{"name": home, "price": ml[0]}, {"name": away, "price": ml[1]}]})
    if total:
        mk.append({"key": "totals", "outcomes": [{"name": "Over", "point": total[0], "price": total[1]},
                                                 {"name": "Under", "point": total[0], "price": total[2]}]})
    return {"home_team": home, "away_team": away, "commence_time": start, "bookmakers": [{"key": book, "markets": mk}]}


class ParlayTest(unittest.TestCase):
    def test_combined_american(self):
        self.assertEqual(parlay.combined_american([-110, -110]), 264)
        self.assertEqual(parlay.combined_american([+150, -200]), 275)
        self.assertEqual(parlay.combined_american([-500, -500]), -227)

    def test_event_prices_bovada_only(self):
        e = _event("Penn State", "USC", ml=(-180, 155), total=(54.5, -110, -110))
        p = parlay.event_prices(e)
        self.assertEqual(p["ml"], {"home": -180, "away": 155})
        self.assertEqual(p["total"], [54.5, -110, -110])
        self.assertIsNone(parlay.event_prices(_event("A", "B", ml=(-110, -110), book="draftkings")))

    def test_load_prices_uses_fresh_cache_and_refetches_stale(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []

            def fetch(key, sport_key):
                calls.append(sport_key)
                return [_event("Penn State", "USC", ml=(-180, 155))]
            parlay.save_prices(Path(d), "cfb", [_event("Alabama", "Georgia", ml=(-120, 100))], NOW)
            out = parlay.load_prices(None, Path(d), ["cfb"], NOW + timedelta(minutes=10), fetch)
            self.assertEqual(calls, [])
            self.assertEqual(out["cfb"][0]["home"], "Alabama")
            out = parlay.load_prices(None, Path(d), ["cfb"], NOW + timedelta(hours=3), fetch)
            self.assertEqual(calls, ["americanfootball_ncaaf"])
            self.assertEqual(out["cfb"][0]["home"], "Penn State")

    def test_proven_ou_legs_and_best_parlays(self):
        with tempfile.TemporaryDirectory() as d:
            st = Path(d)
            tags = {"a": {"sport": "cfb", "game_id": "1", "tag": "OVER-FADE", "side": "Under", "home": "Penn State",
                          "away": "USC", "start": "2026-10-10T23:30:00+00:00", "line": 54.5, "result": None},
                    "b": {"sport": "cfb", "game_id": "2", "tag": "OVER-FADE", "side": "Under", "home": "Alabama",
                          "away": "Georgia", "start": "2026-10-10T23:30:00+00:00", "line": 51.0, "result": None},
                    "c": {"sport": "cfb", "game_id": "3", "tag": "UNDER-FADE", "side": "Over", "home": "Iowa",
                          "away": "Ohio State", "start": "2026-10-10T23:30:00+00:00", "line": 40.0, "result": None},
                    "d": {"sport": "cfb", "game_id": "4", "tag": "OVER-FADE", "side": "Under", "home": "Old",
                          "away": "Game", "start": "2026-10-01T23:30:00+00:00", "line": 40.0, "result": None}}
            (st / "ou_tags_log.json").write_text(json.dumps(tags))
            gaps = {"g": {"sport": "cfb", "market": "total", "side": "Under", "home": "Washington Huskies",
                          "away": "Iowa Hawkeyes", "start": "2026-10-10T01:00:00Z", "line": 42.0, "price": -115,
                          "result": None}}
            (st / "sharp_gap_log.json").write_text(json.dumps(gaps))
            prices = {"cfb": [parlay.event_prices(_event("Penn State", "USC", total=(53.5, -105, -115)))]}
            legs = parlay.all_legs(st, ["cfb"], prices, NOW)
            picks = {l["game"]: (l["pick"], l["price"], l["book"]) for l in legs}
            self.assertEqual(picks["USC @ Penn State"], ("Under 53.5", -115, "Bovada"))  # live Bovada line/price
            self.assertEqual(picks["Georgia @ Alabama"], ("Under 51", -110.0, "est."))  # not posted: logged line
            self.assertEqual(picks["Iowa Hawkeyes @ Washington Huskies"][2], "Bovada (gap scan)")
            self.assertNotIn("Ohio State @ Iowa", picks)  # UNDER-FADE isn't proven
            self.assertNotIn("Game @ Old", picks)  # already played
            ps = parlay.best_parlays(legs)
            self.assertEqual(set(ps), {2, 3})
            self.assertEqual(len({l["gkey"] for l in ps[3]["legs"]}), 3)
            self.assertFalse(ps[3]["all_bovada"])  # Alabama total is an est. -110
            self.assertTrue(ps[2]["all_bovada"])
            t = parlay.text(ps, paper=True)
            self.assertIn("2-LEG", t)
            self.assertIn("3-LEG", t)
            self.assertIn("why: proven O/U", t)
            self.assertIn("OVER-FADE (53.5% hit, backtest n=1,829", t)
            self.assertIn("PAPER", t)
            self.assertNotIn("%  hit", t)

    def test_one_leg_per_game_and_conflicting_proven_rules_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            st = Path(d)
            (st / "ou_tags_log.json").write_text(json.dumps([
                {"sport": "cfb", "game_id": "1", "tag": "OVER-FADE", "side": "Under", "home": "A", "away": "B",
                 "start": "2026-10-10T23:30:00+00:00", "line": 50.0, "result": None}]))
            (st / "sharp_gap_log.json").write_text(json.dumps({"g": {
                "sport": "cfb", "market": "total", "side": "Over", "home": "A", "away": "B",
                "start": "2026-10-10T23:30:00+00:00", "line": 50.0, "price": -110, "result": None}}))
            self.assertEqual(parlay.all_legs(st, ["cfb"], {}, NOW), [])

    def test_answer_empty(self):
        with tempfile.TemporaryDirectory() as d:
            s = SimpleNamespace(state_dir=d, paper_mode=True, sports=["nfl", "cfb"], odds_api_key=None)
            self.assertIn("Not enough qualifying legs", parlay.answer(s, now=NOW))


if __name__ == "__main__":
    unittest.main()
