import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marv import sharpgap
from marv.models import Game


def event(bov_total, pin_total, bov_spread, pin_spread, start):
    def book(key, total, spread):
        return {"key": key, "markets": [
            {"key": "totals", "outcomes": [{"name": "Over", "point": total, "price": -110}, {"name": "Under", "point": total, "price": -110}]},
            {"key": "spreads", "outcomes": [{"name": "Alabama Crimson Tide", "point": spread, "price": -110},
                                            {"name": "Georgia Bulldogs", "point": -spread, "price": -110}]}]}
    return {"id": "ev1", "commence_time": start.isoformat().replace("+00:00", "Z"), "home_team": "Alabama Crimson Tide",
            "away_team": "Georgia Bulldogs", "bookmakers": [book("bovado", bov_total, bov_spread), book("pinnacle", pin_total, pin_spread)]}


class SharpGapTests(unittest.TestCase):
    def test_takes_the_better_number_at_bovado(self):
        start = datetime.now(timezone.utc) + timedelta(days=2)
        out = {e["market"]: e for e in sharpgap.gaps("cfb", [event(51.5, 52.5, -2.5, -3.5, start)])}
        self.assertEqual(out["total"]["pick"], "Over 51.5")  # Bovado lower than Pinnacle -> over
        self.assertEqual(out["spread"]["pick"], "Alabama Crimson Tide -2.5")  # home gets a point more at Bovado
        self.assertAlmostEqual(out["total"]["pinnacle_fair"], 0.5)
        self.assertEqual(sharpgap.gaps("cfb", [event(52.5, 52.5, -3.0, -3.0, start)]), [])
        away = sharpgap.gaps("cfb", [event(53.0, 52.0, -4.5, -3.5, start)])
        picks = {e["market"]: e["pick"] for e in away}
        self.assertEqual(picks, {"total": "Under 53", "spread": "Georgia Bulldogs +4.5"})

    def test_log_alerts_once_and_grades(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            start = datetime.now(timezone.utc) + timedelta(hours=5)
            entries = sharpgap.gaps("cfb", [event(51.5, 52.5, -2.5, -3.5, start)])
            self.assertEqual(len(sharpgap.log_gaps(state, entries)), 2)
            self.assertEqual(sharpgap.log_gaps(state, entries), [])  # already alerted
            final = Game("x", "cfb", start, "Alabama", "Georgia", completed=True, home_score=31, away_score=24)
            sharpgap.grade(state, "cfb", [final])
            rec = sharpgap.record(state)
            self.assertEqual(rec["cfb total"][:2], [1, 0])  # 55 > 51.5
            self.assertEqual(rec["cfb spread"][:2], [1, 0])  # Alabama by 7 covers -2.5
            self.assertIn("cfb total: 1-0", sharpgap.report(rec))


if __name__ == "__main__":
    unittest.main()
