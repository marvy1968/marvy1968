import unittest

import pandas as pd


class SectorNewsTests(unittest.TestCase):
    def test_word_score(self):
        from alpacabot.news import score
        self.assertLess(score("Analyst downgrades Apple, cuts target on weak demand"), 0)
        self.assertGreater(score("Nvidia beats estimates, raises guidance; shares surge"), 0)
        self.assertEqual(score(""), 0)

    def test_sector_negative_needs_enough_bad_articles_and_respects_the_close(self):
        from alpacabot.news import sector_news_negative
        arts = [{"t": "2026-10-14T14:00:00Z", "symbols": ["AAPL"], "s": -2}, {"t": "2026-10-14T15:00:00Z", "symbols": ["MSFT"], "s": -1},
                {"t": "2026-10-15T21:30:00Z", "symbols": ["NVDA"], "s": -1},          # 17:30 ET: counts for the NEXT session
                {"t": "2026-10-15T13:00:00Z", "symbols": ["JPM"], "s": 2}]
        days = pd.bdate_range("2026-10-12", "2026-10-19")
        r = sector_news_negative(arts, {"AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "JPM": "XLF"}, days)
        self.assertFalse(bool(r["XLK"][pd.Timestamp("2026-10-15")]))       # only 2 articles known by the 15th's close
        self.assertTrue(bool(r["XLK"][pd.Timestamp("2026-10-16")]))        # the after-hours article joins on the 16th
        self.assertFalse(r["XLF"].any())

    def test_sector_trend_negative(self):
        import numpy as np
        from alpacabot.news import sector_trend_negative
        idx = pd.bdate_range("2026-01-02", periods=120)
        down = pd.DataFrame({"close": np.linspace(100, 70, 120)}, index=idx)
        self.assertTrue(bool(sector_trend_negative({"XLK": down})["XLK"].iloc[-1]))


if __name__ == "__main__":
    unittest.main()
