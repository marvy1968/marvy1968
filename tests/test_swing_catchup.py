import unittest
from datetime import datetime

import pandas as pd

from alpacabot.swing_paper import ET, catchup_time


def fake_fetch(last_day):
    def f(symbol, tf, start, end, feed):
        idx = pd.DatetimeIndex(pd.bdate_range(end=last_day, periods=5)).tz_localize("UTC") + pd.Timedelta(hours=5)
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)
    return f


class CatchupTests(unittest.TestCase):
    def test_monday_morning_gives_friday_close(self):
        now = datetime(2026, 11, 2, 5, 50, tzinfo=ET)
        t = catchup_time(now, fake_fetch("2026-10-30"))
        self.assertEqual(t, datetime(2026, 10, 30, 16, 25, tzinfo=ET))

    def test_refused_when_todays_bar_exists(self):
        now = datetime(2026, 11, 2, 17, 0, tzinfo=ET)
        self.assertIsNone(catchup_time(now, fake_fetch("2026-11-02")))


if __name__ == "__main__":
    unittest.main()
