import unittest
from datetime import date, timedelta

import pandas as pd

from alpacabot import eqspy


class EqSpyTests(unittest.TestCase):
    def test_counts(self):
        days = [date(2026, 10, 12) + timedelta(days=i) for i in range(6)]
        acct = pd.Series([100, 99, 100, 101, 100, 102.0], index=days)       # down, up, up, down, up
        spy = pd.Series([50, 49, 50.5, 50.0, 49.0, 50.0], index=days)       # down, up, down, down, up
        lines = eqspy.compare(acct, spy)
        self.assertIn("account DOWN days: 2; SPY was also down on 2 of them (100%)", lines[1])
        self.assertIn("account UP days: 3; SPY was also up on 2 of them (67%)", lines[2])

    def test_flat_days_skipped(self):
        days = [date(2026, 10, 12) + timedelta(days=i) for i in range(5)]
        lines = eqspy.compare(pd.Series([100, 100, 100, 101, 100.0], index=days), pd.Series([1, 2, 3, 4, 5.0], index=days))
        self.assertEqual(lines, ["- not enough days to compare"])


if __name__ == "__main__":
    unittest.main()
