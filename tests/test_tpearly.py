import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import tpearly as tp
from alpacabot import velez as ve


class TpEarlyTests(unittest.TestCase):
    def test_table_verdicts(self):
        d1, d2 = date(2026, 8, 1), date(2026, 10, 1)
        base = [(d1, 1.0), (d1, -1.0), (d2, 1.0), (d2, -1.0)]
        better = [(d1, 1.0), (d1, -0.9), (d2, 1.5), (d2, -1.0)]
        worse_first = [(d1, 0.5), (d1, -1.0), (d2, 2.0), (d2, -1.0)]
        lines = tp.table("x", {"cur": (base, base), "b": (better, better), "w": (worse_first, worse_first)}, "cur", date(2026, 9, 1))
        row = {l.split("|")[1].strip(): l.split("|")[-2].strip() for l in lines[3:]}
        self.assertEqual(row, {"cur": "current rule", "b": "YES", "w": "no"})

    def test_velez_targets_patchable(self):
        n = 30
        c = np.r_[np.full(5, 100.0), np.linspace(100, 104, 25)]
        sd = {"o": c, "h": c + 0.01, "l": c - 0.01, "n": n}
        fd = pd.DataFrame({"close_min": [], "c": [], "sma20": []})
        ev = ve.walk(sd, 1, 100.0, 99.0, fd, flat=29)
        self.assertEqual([x[3] for x in ev], ["t1", "t2"])
        self.assertAlmostEqual(ev[0][2], 102.0, places=1)
        keep = dict(ve.TGT)
        try:
            ve.TGT.update(t1=1.8, t2=2.8)
            ev = ve.walk(sd, 1, 100.0, 99.0, fd, flat=29)
            self.assertAlmostEqual(ev[0][2], 101.8, places=1)
            self.assertAlmostEqual(ev[1][2], 102.8, places=1)
        finally:
            ve.TGT.clear()
            ve.TGT.update(keep)
        self.assertEqual(ve.TGT, {"t1": 2.0, "t2": 3.0})

    def test_week_trade_tp_shave(self):
        idx = list(range(20))
        o = np.full(20, 100.0)
        h = np.full(20, 100.5)
        l = np.full(20, 99.0)
        c = np.full(20, 100.0)
        l[11:] = 99.5
        h[12] = 101.48                            # reaches 0.5% under a 101.5 target only
        arr = {"A": ({t: i for i, t in enumerate(idx)}, o, h, l, c)}
        full = tp.week_trade_tp(arr, "A", 10, 1.0, 0.0, 0.0)
        shaved = tp.week_trade_tp(arr, "A", 10, 1.0, 0.005, 0.0)
        self.assertLess(full, shaved)


if __name__ == "__main__":
    unittest.main()
