import unittest
import fetch_cfbd as f


class FakeSession:
    def __init__(self): self.calls = []
    def get(self, url, params=None, timeout=0):
        self.calls.append((url.replace(f.BASE, ""), dict(params or {})))
        path, p = self.calls[-1]
        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self_inner):
                if path == "/teams/fbs":
                    return [{"school": "Georgia", "conference": "SEC"}, {"school": "Ohio State", "conference": "Big Ten"}]
                if p["seasonType"] == "regular":
                    return [{"homeTeam": "Georgia", "awayTeam": "Ohio State", "homePoints": 27, "awayPoints": 20,
                             "startDate": "2026-09-05T20:00:00.000Z", "neutralSite": True, "week": 1},
                            {"home_team": "Georgia", "away_team": "Ohio State", "home_points": None, "away_points": None,
                             "start_date": "2026-12-05T20:00:00.000Z"}]
                return [{"homeTeam": "Ohio State", "awayTeam": "Georgia", "homePoints": 31, "awayPoints": 28,
                         "startDate": "2026-12-31T20:00:00.000Z", "neutralSite": True}]
        return R()


class T(unittest.TestCase):
    def test_fetch_keeps_only_completed_games_and_maps_fields(self):
        g, t = f.fetch([2026], FakeSession())
        self.assertEqual(len(g), 2)
        self.assertEqual(list(g["home"]), ["Georgia", "Ohio State"])
        self.assertEqual(set(g.columns) >= {"season", "date", "home", "away", "hg", "ag", "neutral"}, True)
        self.assertEqual(g.iloc[0]["date"], "2026-09-05"); self.assertEqual(int(g.iloc[0]["neutral"]), 1)
        self.assertEqual(set(t["team"]), {"Georgia", "Ohio State"})

    def test_regular_only_skips_postseason(self):
        g, _ = f.fetch([2026], FakeSession(), regular_only=True)
        self.assertEqual(len(g), 1)

    def test_output_feeds_the_backtest_loader_columns(self):
        g, t = f.fetch([2026], FakeSession())
        for c in ("season", "date", "home", "away", "hg", "ag"):
            self.assertIn(c, g.columns)
        for c in ("season", "team", "conference"):
            self.assertIn(c, t.columns)


if __name__ == "__main__":
    unittest.main()
