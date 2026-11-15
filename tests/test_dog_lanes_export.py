import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("export_vm", Path(__file__).resolve().parents[1] / "tools/dog_lanes/export_vm.py")
X = importlib.util.module_from_spec(spec)
spec.loader.exec_module(X)

COLS = ["id", "ts", "date", "key", "pick", "slug", "side", "contracts", "price", "fee", "cost", "fair", "edge", "status",
        "pnl", "result", "payout", "settle_px", "settled_ts", "settle_src", "me_type", "me_alert_id", "lane",
        "bovada_price", "bovada_line", "bovada_ts"]
ROWS = [  # the three sample rows printed by `dog_lanes.py inspect` on the VM, plus a favourite and an unsettled row
    (1, "2026-10-13T08:00:05-05:00", "2026-10-13", "h2h:nhl:401892490:away", "Buffalo Sabres", "s", "long", 117.0, 0.41,
     1.967, 49.937, 0.4807, 0.0539, "settled", -49.937, "loss", 0.0, 0.0, None, "espn_final", None, None, None, None, None, None),
    (2, "2026-10-13T08:00:05-05:00", "2026-10-13", "h2h:nhl:401892492:away", "Seattle Kraken", "s", "long", 98.0, 0.49,
     1.7021, 49.7221, 0.5588, 0.0514, "settled", 48.2779, "win", 98.0, 1.0, None, "polymarket", None, None, None, None, None, None),
    (3, "2026-10-13T08:00:05-05:00", "2026-10-13", "h2h:nhl:401892493:home", "New York Rangers", "s", "short", 117.0, 0.41,
     1.967, 49.937, 0.5213, 0.0945, "settled", 67.063, "win", 117.0, 1.0, None, "polymarket", None, None, "nhl_dog", 150, None, None),
    (4, "2026-10-14T08:00:05-05:00", "2026-10-14", "h2h:nba:1:home", "Fav", "s", "long", 60.0, 0.70, 1.0, 43.0, 0.75, 0.04,
     "settled", 17.0, "win", 60.0, 1.0, None, "polymarket", None, None, None, None, None, None),
    (5, "2026-10-15T08:00:05-05:00", "2026-10-15", "h2h:nba:2:home", "Open", "s", "long", 60.0, 0.30, 1.0, 19.0, 0.4, 0.08,
     "open", None, None, None, None, None, None, None, None, None, None, None, None),
]


class ExportTests(unittest.TestCase):
    def test_polymarket_dogs_sides_and_home(self):
        with tempfile.TemporaryDirectory() as t:
            pm, clv = Path(t) / "pm.db", Path(t) / "clv.db"
            c = sqlite3.connect(pm)
            c.execute(f"CREATE TABLE fills ({', '.join(COLS)})")
            c.executemany(f"INSERT INTO fills VALUES ({','.join('?' * len(COLS))})", ROWS)
            c.commit()
            c = sqlite3.connect(clv)
            c.execute("CREATE TABLE clv (key, src, start, close_fair_p)")
            c.execute("INSERT INTO clv VALUES ('pm:3', 'polymarket', '2026-10-13T23:00:00+00:00', 0.55)")
            c.commit()
            rows = X.main(str(Path(t) / "out.csv"), 0.5, str(pm), str(clv), str(Path(t) / "none.jsonl"))
            self.assertEqual([r["source_id"] for r in rows], ["pm:1", "pm:2", "pm:3"])  # favourite and open bet excluded
            r1, r3 = rows[0], rows[2]
            self.assertEqual((r1["is_home"], r1["result"], r1["lane"]), (0, 0, "nhl:ml dog"))
            # short on the home Rangers = bought the away side; close prob of our side = 1 - close of the pick
            self.assertEqual((r3["is_home"], r3["lane"], r3["side"]), (0, "nhl_dog", "against New York Rangers"))
            self.assertAlmostEqual(r3["close_p"], 0.45)
            self.assertAlmostEqual(r3["sharp_p"], 1 - 100 / 250)  # Bovada +150 on the pick -> our side
            self.assertEqual(r3["start"], "2026-10-13T23:00:00+00:00")

    def test_kalshi_shadow_rows_are_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "k.jsonl"
            p.write_text(json.dumps({"key": "h2h:nfl:x:home", "pick": "A", "ask": 40, "fair_c": 52, "mode": "shadow"}) + "\n"
                         + json.dumps({"key": "h2h:nfl:y:away", "pick": "B", "fair_c": 45, "mode": "demo", "result": "win",
                                       "order": {"order_id": "o1", "fill_count": 10, "average_fill_price": 38}}) + "\n")
            rows, seen = X.kalshi(str(p), 0.5)
            self.assertEqual(seen, {"rows": 2, "demo_filled": 1, "graded": 1})
            self.assertEqual((rows[0]["price"], rows[0]["model_p"], rows[0]["is_home"]), (0.38, 0.45, 0))


if __name__ == "__main__":
    unittest.main()
