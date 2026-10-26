#!/usr/bin/env python3
import sqlite3
import time
import sys
from datetime import date, datetime
from typing import Dict, Optional

DB = "performance_tracking.db"
REPORT_INTERVAL = 300

class DailyPLSummary:
    def __init__(self, confidence_floor: float = 70.0):
        self.confidence_floor = confidence_floor
        self.last_summary: Optional[Dict] = None
        self.last_print_time = 0

    def _get_summary(self) -> Dict:
        conn = sqlite3.connect(DB)
        query = """
            SELECT date(timestamp) as bet_date, COUNT(*) as bets,
                   SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN result='win' THEN 1.0 ELSE -1.1 END) as units,
                   AVG(CASE WHEN edge IS NOT NULL THEN edge ELSE 0 END) as avg_edge,
                   AVG(CASE WHEN ev_percent IS NOT NULL THEN ev_percent ELSE 0 END) as avg_ev
            FROM tracked_bets WHERE result IS NOT NULL GROUP BY bet_date ORDER BY bet_date DESC
        """
        rows = conn.execute(query).fetchall()
        conn.close()
        today_str = str(date.today())
        today_data = None
        lifetime = 0.0
        for r in rows:
            u = round(r[3] or 0, 2)
            lifetime += u
            if r[0] == today_str:
                today_data = {
                    "bets": r[1], "wins": r[2] or 0,
                    "win_rate": round((r[2]/r[1]*100), 1) if r[1] else 0,
                    "units": u, "avg_edge": round((r[4] or 0)*100, 1),
                    "avg_ev": round(r[5] or 0, 2)
                }
        recovered = lifetime + 16.07
        progress = min(100, max(0, int((recovered / 16.07) * 100))) if 16.07 else 0
        return {"today": today_data, "lifetime_units": round(lifetime, 2),
                "recovered_units": round(recovered, 2), "recovery_progress": progress, "date": today_str}

    def print_if_changed(self):
        now = time.time()
        if now - self.last_print_time < REPORT_INTERVAL: return
        current = self._get_summary()
        if self.last_summary and current == self.last_summary: return
        t = current["today"]
        bar = "█" * (current["recovery_progress"] // 5) + "░" * (20 - current["recovery_progress"] // 5)
        if t:
            today_line = f"📅 *TODAY* ({current['date']})\nBets: `{t['bets']}` | Win Rate: `{t['win_rate']}%` ({t['wins']}W)\nP/L: `{t['units']:+.2f}` | Edge: `{t['avg_edge']:+.1f}%` | EV: `{t['avg_ev']:+.2f}%`"
        else:
            today_line = "📅 *TODAY* — No closed bets yet."
        msg = (f"🏆 *MARCHEDGE DAILY P/L REPORT* — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
               f"{today_line}\n\n📈 *LIFETIME RECOVERY*\nTotal P/L: `{current['lifetime_units']:+.2f}`\n"
               f"Progress: `{bar}` **{current['recovery_progress']}%**\nRecovered: `{current['recovered_units']:.2f}` / 16.07")
        print(msg)
        print("=" * 60)
        self.last_summary = current
        self.last_print_time = now

if __name__ == "__main__":
    floor = float(sys.argv[1]) if len(sys.argv) > 1 else 70.0
    print(f"📊 Daily P/L Summary started (floor: {floor}%)")
    reporter = DailyPLSummary(confidence_floor=floor)
    while True:
        reporter.print_if_changed()
        time.sleep(60)
