"""Owner 2026-11-05 Top-Down sizing test with the live 2R target (research.SIZE2R_SPEC): the 2R wrapper exits at 2R and the report runs."""
import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot.engine import Costs, simulate_swing
from alpacabot.research import _TopDown2R, topdown_size2r_lines
from alpacabot.strategies import GatedSectorTopDown, SectorTopDown


def _daily(seed, drift, n=900):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n, tz="America/New_York")
    c = 50 * np.exp(np.cumsum(drift + rng.normal(0, 0.015, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": 1e6}, index=idx)


class TopDownSize2R(unittest.TestCase):
    def test_tp_and_report(self):
        spy = _daily(1, 0.0006)
        data = {f"S{k}": _daily(10 + k, 0.001) for k in range(6)}
        c = spy["close"]
        s2 = _TopDown2R(GatedSectorTopDown(SectorTopDown(c), None), 2.0)
        trades = [t for d in data.values() for t in simulate_swing("X", d, s2, Costs(slip_bps=0), all_signals=False)]
        self.assertTrue(trades)
        wins = [t for t in trades if t.reason == "zone"]
        for t in wins:                                    # a 2R exit is 2R from the fill (or better on a gap)
            self.assertGreaterEqual((t.exit_px - t.entry_px) / t.stop_dist, 2.0 - 1e-9)
        self.assertTrue(all((t.exit_px - t.entry_px) / t.stop_dist <= 2.0 + 1e-6 or t.reason != "stop" for t in trades))
        import alpacabot.research as R
        keep = dict(R.SIZE2R_SPEC)
        try:
            R.SIZE2R_SPEC.update(select=("2019-06-01", "2020-06-30"), confirm_from="2020-07-01")
            out = topdown_size2r_lines({"a": data, "b": data}, spy, date(2022, 6, 1))
        finally:
            R.SIZE2R_SPEC.clear()
            R.SIZE2R_SPEC.update(keep)
        self.assertTrue(any("PRE-REGISTERED VERDICT" in x for x in out))
        self.assertEqual(sum(" x 5 (LIVE)" in x or "0.50% x 5 (LIVE)" in x for x in out), 4)


if __name__ == "__main__":
    unittest.main()
