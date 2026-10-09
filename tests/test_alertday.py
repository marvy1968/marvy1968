import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from marv import alertday, querybot, sharpgap

ET = ZoneInfo("America/New_York")
NOON = datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc)  # Thu noon ET


class AlertDayTests(unittest.TestCase):
    def test_is_today_uses_the_eastern_date(self):
        self.assertTrue(alertday.is_today(datetime(2026, 10, 9, 0, 15, tzinfo=timezone.utc), NOON))  # 8:15 PM ET Thu
        self.assertFalse(alertday.is_today(datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc), NOON))  # 1 AM ET Fri
        self.assertFalse(alertday.is_today("2026-10-11T17:00:00Z", NOON))  # Sunday's games on Thursday
        self.assertTrue(alertday.is_today("2026-10-08T23:30:00+00:00", NOON))
        with mock.patch.dict(os.environ, {"ALERT_DAY_ONLY": "false"}):
            self.assertTrue(alertday.is_today("2026-10-11T17:00:00Z", NOON))

    def test_board_alerts_wait_for_game_day_but_stay_queryable(self):
        with tempfile.TemporaryDirectory() as d:
            soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
            e = {"key": "nfl:1:ml:home:None", "start": soon, "status": "recommended", "edge": 0.1, "price": -110,
                 "p_market": 0.5}
            self.assertEqual(querybot.track(Path(d), [e]), [])  # not today: no alert, nothing recorded
            with mock.patch.dict(os.environ, {"ALERT_DAY_ONLY": "false"}):
                self.assertEqual(len(querybot.track(Path(d), [e])), 1)

    def test_gap_alerts_only_for_today(self):
        with tempfile.TemporaryDirectory() as d:
            far = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
            entry = {"key": "nfl:e:total:Over", "sport": "nfl", "event": "e", "start": far, "home": "H", "away": "A",
                     "market": "total", "side": "Over", "line": 44.5, "price": -110, "pinnacle_line": 45,
                     "pinnacle_fair": 0.5, "gap": 0.5, "pick": "Over 44.5", "label": ""}
            self.assertEqual(sharpgap.log_gaps(Path(d), [entry]), [])  # Sunday's gap isn't announced on Thursday


if __name__ == "__main__":
    unittest.main()


class GapLeftoverTests(unittest.TestCase):
    def test_advance_entries_alert_again_on_game_day(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            far = datetime.now(timezone.utc) + timedelta(days=2)
            entry = {"key": "cfb:e:total:Under", "sport": "cfb", "event": "e", "start": far.isoformat(), "home": "H",
                     "away": "A", "market": "total", "side": "Under", "line": 53.5, "price": -110, "pinnacle_line": 52.5,
                     "pinnacle_fair": 0.5, "gap": 1.0, "pick": "Under 53.5", "label": ""}
            (Path(d) / "sharp_gap_log.json").write_text(json.dumps({entry["key"]: {**entry, "first_seen": "x", "result": None}}))
            self.assertEqual(sharpgap.log_gaps(Path(d), []), [])  # not game day: forgotten, not announced
            self.assertEqual(json.loads((Path(d) / "sharp_gap_log.json").read_text()), {})
            # game day: the same gap now alerts
            day = datetime.now(timezone.utc) + timedelta(minutes=5)
            if alertday.is_today(day):
                self.assertEqual(len(sharpgap.log_gaps(Path(d), [{**entry, "start": day.isoformat()}])), 1)
