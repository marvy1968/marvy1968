"""Defense quality / trend + heavy-favourite gate in the Marv H2H insight (Oct 9 2026)."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marv import bridge, defense, insight

TEAMS = ["DAL", "TB", "PHI", "NYG", "WAS", "GB", "CHI", "DET", "MIN", "SF"]


def _cache(st: Path, dal_allowed):
    """nflverse-style games: 10 teams, 4 past weeks. DAL allows dal_allowed[i] in week i; everyone else 20."""
    (st / "cache").mkdir(parents=True, exist_ok=True)
    rows = ["game_id,season,game_type,week,gameday,away_team,home_team,away_score,home_score"]
    today = datetime.now(timezone.utc).date()
    for w in range(4):
        day = (today - timedelta(days=7 * (4 - w))).isoformat()
        for i in range(0, 10, 2):
            h, a = TEAMS[(i + w * 2) % 10], TEAMS[(i + 1 + w * 2) % 10]
            hs, as_ = 20, 20
            if h == "DAL":
                as_ = dal_allowed[w]
            if a == "DAL":
                hs = dal_allowed[w]
            rows.append(f"2026_{w}_{a}_{h},2026,REG,{w + 1},{day},{a},{h},{as_},{hs}")
    (st / "cache" / "nflverse_games.csv").write_text("\n".join(rows) + "\n")


class DefenseTest(unittest.TestCase):
    def setUp(self):
        defense._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        st = self.state = Path(self.tmp.name)
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        (st / "predictions.json").write_text(json.dumps({"nfl:G1": {
            "sport": "nfl", "game_id": "G1", "start": start, "home": "Dallas Cowboys", "away": "Tampa Bay Buccaneers",
            "home_exp": 27, "away_exp": 20, "model_total": 47.0, "model_margin": 7.0, "home_win": 0.7, "qualified": [],
            "notes": []}}))
        (st / "marv_predict").mkdir()
        (st / "marv_predict" / "h2h_nfl.json").write_text(json.dumps({"card_day": datetime.now(timezone.utc).date().isoformat(),
            "games": [{"game_id": "G1", "home": "Dallas Cowboys", "away": "Tampa Bay Buccaneers", "margin": 8.0,
                       "rating_total": 44.0, "cat_home": 2, "cat_away": 0, "stars_home": 3, "stars_away": 1, "fade": "",
                       "pick": "Dallas Cowboys", "method": "category sweep 2-0", "total_line": 49.0}]}))

    def tearDown(self):
        self.tmp.cleanup()
        defense._CACHE.clear()

    def test_profile_bad_and_fading(self):
        _cache(self.state, [17, 31, 34, 38])
        p = defense.profile(self.state, "nfl", "Dallas Cowboys")
        self.assertTrue(p["bad"] and p["fading"])
        self.assertAlmostEqual(p["pa3"], 103 / 3)
        self.assertIn("Cowboys D bad + trending down (allowed 34/g last 3 vs 30/g season", defense.describe(p, "Cowboys"))

    def test_heavy_favourite_bad_fading_defense_is_bad(self):
        _cache(self.state, [17, 31, 34, 38])
        o = bridge.overlay(self.state, "nfl", "h2h", "Dallas Cowboys", price=-450)
        self.assertEqual(o.verdict, "not")  # ratings/stars/model all favour Dallas, defense gate wins
        self.assertTrue(o.why.startswith("Cowboys D bad + trending down"))
        self.assertIn("fade the favorite", o.line())
        self.assertIn("👎 bad", o.line())

    def test_heavy_favourite_bad_defense_only_is_mixed(self):
        _cache(self.state, [30, 30, 30, 30])
        o = bridge.overlay(self.state, "nfl", "h2h", "Dallas Cowboys", price=-450)
        self.assertEqual(o.verdict, "mixed")
        self.assertIn("Cowboys D bad (allowed 30/g", o.why)

    def test_dog_side_gets_the_fade_reason(self):
        _cache(self.state, [17, 31, 34, 38])
        o = bridge.overlay(self.state, "nfl", "spreads", "Tampa Bay Buccaneers", line=9.5, price=-110)
        self.assertIn("Cowboys D bad + trending down", o.why)
        self.assertIn("for:", o.why)

    def test_heavy_favourite_any_other_metric_off_is_capped(self):
        _cache(self.state, [20, 20, 20, 20])  # defense fine
        gap = {"k": {"sport": "nfl", "start": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                     "home": "Dallas Cowboys", "away": "Tampa Bay Buccaneers", "market": "spread",
                     "side": "Tampa Bay Buccaneers", "line": 9.5, "pinnacle_line": 8.5, "result": None}}
        (self.state / "sharp_gap_log.json").write_text(json.dumps(gap))
        o = bridge.overlay(self.state, "nfl", "h2h", "Dallas Cowboys", price=-450)
        self.assertEqual(o.verdict, "mixed")
        self.assertTrue(o.why.startswith("heavy fav (ML -450) but Bovada vs Pinnacle"))

    def test_clean_heavy_favourite_stays_good(self):
        _cache(self.state, [20, 20, 20, 20])
        o = bridge.overlay(self.state, "nfl", "h2h", "Dallas Cowboys", price=-450)
        self.assertEqual(o.verdict, "good")

    def test_no_cache_never_raises(self):
        o = bridge.overlay(self.state, "nfl", "h2h", "Dallas Cowboys", price=-450)
        self.assertEqual(o.verdict, "good")


if __name__ == "__main__":
    unittest.main()
