import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from marv.models import Game, Odds, Pick, Prediction
from marv.reports import sport_sheet, weekly_chart
from marv.sports import SPORTS


class ReportTest(unittest.TestCase):
    def test_pdfs_build(self):
        g = Game("1", "nfl", datetime.now(timezone.utc), "Dallas Cowboys", "Tampa Bay Buccaneers",
                 odds=Odds(total=47.5, home_ml=-455, away_ml=350))
        p = Prediction(g, 27, 21, 6, 48.0, 0.82)
        p.picks = [Pick("ml", "Dallas Cowboys", -455, -455, 0.82, 0.0, 0.0)]
        with tempfile.TemporaryDirectory() as d:
            chart = weekly_chart(SPORTS["nfl"], [p], Path(d) / "c.pdf")
            sheet = sport_sheet(SPORTS["nba"], Path(d) / "s.pdf")
            empty = weekly_chart(SPORTS["ncaab"], [], Path(d) / "e.pdf")
            for f in (chart, sheet, empty):
                self.assertTrue(f.read_bytes().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
