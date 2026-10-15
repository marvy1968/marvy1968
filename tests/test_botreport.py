import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from marv import botreport


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


class BotReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _w(self.tmp / "alpaca_paper/trades.jsonl", [
            {"date": "2026-10-14", "symbol": "COP", "side": "long", "qty": 10, "entry": 1, "exit": 1, "pnl": -10.0, "r": -0.2, "reason": "stop", "opened": "a", "expert": "vwap_dev"},
            {"date": "2026-10-15", "symbol": "PLD", "side": "short", "qty": 10, "entry": 1, "exit": 1, "pnl": 68.0, "r": 1.0, "reason": "trail", "opened": "b"},
            {"date": "2026-10-15", "symbol": "ABBV", "side": "long", "qty": 5, "entry": 1, "exit": 1, "pnl": -20.0, "r": -1.0, "reason": "cut50", "opened": "c", "partial": True},
            {"date": "2026-10-15", "symbol": "ABBV", "side": "long", "qty": 5, "entry": 1, "exit": 1, "pnl": -19.0, "r": -1.0, "reason": "stop", "opened": "c"},
            {"date": "2026-10-01", "symbol": "OLD", "side": "long", "qty": 1, "entry": 1, "exit": 1, "pnl": 500.0, "r": 1.0, "reason": "stop", "opened": "d"}])
        _w(self.tmp / "alpaca_paper/equity.jsonl", [{"date": "2026-10-14", "equity": 54000.0, "spy": 770.0}])
        _w(self.tmp / "fx_paper/trades.jsonl", [{"date": "2026-10-14", "closed": "2026-10-15T14:20:34+00:00", "strategy": "box_h1", "instrument": "GBP_USD",
                                                 "side": "short", "pl": -262.76, "reason": "closed_outside_runner"}])
        (self.tmp / "fx_paper/status.txt").write_text("MARV FX\nbook equity $58,000\nmarket OPEN\nlast bar\nopen groups 1\n  box_h1 short\n")

    def test_parse_days(self):
        self.assertIsNone(botreport.parse_days("/report"))
        self.assertEqual(botreport.parse_days("/report 7"), 7)
        self.assertEqual(botreport.parse_days("/report card 7"), 7)
        self.assertEqual(botreport.parse_days("/report 500"), 90)

    def test_seven_days_counts_whole_trades_and_excludes_older(self):
        txt = botreport.report(self.tmp, 7, today=date(2026, 10, 15))
        self.assertIn("3 trades", txt)                    # COP, PLD, ABBV (the cut folds into its trade); OLD is outside the window
        self.assertNotIn("OLD", txt)
        self.assertIn("2026-10-15: 2 trades", txt)
        self.assertIn("by exit:", txt)
        self.assertIn("vwap_dev 1", txt)
        self.assertIn("closed_outside_runner", txt)

    def test_current_has_both_sections(self):
        txt = botreport.report(self.tmp, None, today=date(2026, 10, 15))
        self.assertIn("STOCKS", txt)
        self.assertIn("FOREX", txt)
        self.assertIn("book equity", txt)

    def test_empty_state_does_not_crash(self):
        txt = botreport.report(Path(tempfile.mkdtemp()), 7, today=date(2026, 10, 15))
        self.assertIn("no trades", txt)

    def test_querybot_routes_report(self):
        from types import SimpleNamespace
        from marv import querybot
        out = querybot.answer(SimpleNamespace(state_dir=str(self.tmp)), "/report 7")
        self.assertIn("REPORT CARD - last 7 days", out)


if __name__ == "__main__":
    unittest.main()
