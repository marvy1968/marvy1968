import unittest

import numpy as np
import pandas as pd

from alpacabot.engine import Costs, simulate_swing_short
from alpacabot.strategies import GatedSectorTopDown, SectorTopDownShort


def ser(seed, mu, idx):
    r = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(r.normal(mu, 0.015, len(idx))))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)


class ShortOnSpyDownDays(unittest.TestCase):
    def test_shorts_only_on_spy_down_days(self):
        idx = pd.bdate_range("2020-01-02", periods=900, tz="America/New_York")
        spy = ser(1, 0.0002, idx)
        sc = spy["close"]
        sma = sc.rolling(20).mean()
        gate = ((sc < sma) & (sma < sma.shift(1))).fillna(False)
        d = ser(5, -0.0004, idx)
        tr = simulate_swing_short("X", d, GatedSectorTopDown(SectorTopDownShort(spy["close"]), gate), Costs(slip_bps=5.0))
        self.assertTrue(len(tr) > 0)
        pos = {t: i for i, t in enumerate(idx)}
        for t in tr:
            self.assertTrue(bool(gate.iloc[pos[t.entry_time] - 1]), t.entry_time)     # the signal bar was a SPY-down day
            self.assertEqual(t.side, -1)


if __name__ == "__main__":
    unittest.main()
