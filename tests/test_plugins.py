import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from marv.models import Game, Odds, Pick, Prediction
from marv.plugins import AuditDatabase, MarvSportPlugin


def pred():
    g = Game("g1", "nba", datetime.now(timezone.utc), "Boston Celtics", "Miami Heat", odds=Odds(total=220.5, home_ml=-900, away_ml=600))
    p = Prediction(g, 118, 104, 14, 222, 0.91)
    p.picks = [Pick("ml", "Boston Celtics", -900, -900, 0.91, 0.01, 0.005),
               Pick("total", "Over", 220.5, -110, 0.53, 0.01, 0.005, vetoes=["O/U edge +3% below 17%"])]
    return p


class PluginTest(unittest.TestCase):
    def test_blueprint_loop_with_real_plugin(self):
        with mock.patch("marv.engine.predict", return_value=[pred()]), \
             mock.patch("marv.sources.load_for_run", return_value=([], [pred().game], {})), \
             mock.patch("marv.stats.registry.project", return_value={}):
            plugin = MarvSportPlugin("nba")
            games = plugin.fetch_slate_data("today")
        self.assertEqual(games[0]["total_line"], 220.5)
        projection, confidence = plugin.run_simulation(games[0])
        self.assertEqual((projection, confidence), (222, 0.91))
        self.assertFalse(plugin.apply_sport_vetos(games[0]))  # the ML pick is live

    def test_audit_db(self):
        with tempfile.TemporaryDirectory() as d:
            db = AuditDatabase(Path(d) / "a.db")
            db.log_predictions("nba", [pred()])
            rows = sqlite3.connect(db.db_path).execute(
                "select market, veto_status, reasons from execution_logs order by id").fetchall()
        self.assertEqual(rows[0][:2], ("ml", "LOCKED_PLAY"))
        self.assertEqual(rows[1][1], "VETOED")
        self.assertIn("O/U edge", rows[1][2])


if __name__ == "__main__":
    unittest.main()
