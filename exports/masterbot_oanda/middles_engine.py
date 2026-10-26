#!/usr/bin/env python3
import sqlite3, sys, time
from datetime import datetime
from betting_math import calculate_ev
from dataclasses import dataclass
from typing import List
@dataclass
class MiddleOpportunity:
    game: str
    original_bet_team: str
    original_line: float
    live_bet_team: str
    live_line: float
    window_low: float
    window_high: float
    window_size: int
    confidence: float
    strength: str
    ev_percent: float
    summary: str
class MiddlesEngine:
    def __init__(self, min_conf=70.0):
        self.min_conf = min_conf
        self.strength_tiers = {12:"A",8:"B",5:"C"}
        self.last_alerts = set()
    def run_once(self, live_spreads):
        try:
            conn = sqlite3.connect("performance_tracking.db")
            rows = conn.execute("SELECT game, bet_team, line, odds FROM tracked_bets WHERE market LIKE '%spread%' AND result IS NULL").fetchall()
            conn.close()
        except:
            return []
        opps = []
        for orig in [{"game":r[0],"original_bet_team":r[1],"original_line":float(r[2])} for r in rows]:
            for live in live_spreads:
                if live["game"] == orig["game"]:
                    ref_orig = orig["original_line"] if orig["original_bet_team"].lower() == live["live_bet_team"].lower() else -orig["original_line"]
                    low = min(ref_orig, live["live_line"]) + 0.5
                    high = max(ref_orig, live["live_line"]) - 0.5
                    size = max(0, int(high - low) + 1)
                    if size < 5: continue
                    conf = min(98.0, 82.0 + size * 1.8)
                    if conf < self.min_conf: continue
                    strength = next((g for s,g in sorted(self.strength_tiers.items(), reverse=True) if size >= s), "C")
                    ev = calculate_ev(0.5, live["live_line"]) * 100
                    summary = f"already have {orig['original_bet_team']} {orig['original_line']:+} | Bet now: {live['live_bet_team']} {live['live_line']:+} | middle={low:.1f}-{high:.1f} | size={size}"
                    opps.append(MiddleOpportunity(game=orig["game"], original_bet_team=orig["original_bet_team"], original_line=orig["original_line"], live_bet_team=live["live_bet_team"], live_line=live["live_line"], window_low=round(low,1), window_high=round(high,1), window_size=size, confidence=round(conf,1), strength=strength, ev_percent=round(ev,2), summary=summary))
                    break
        return opps[:3]
    def print_alerts(self, middles):
        for m in middles:
            key = f"{m.game}|{m.window_size}"
            if key in self.last_alerts: continue
            self.last_alerts.add(key)
            print(f"🎯 *MIDDLE ALERT* — {m.game}\n🔥 *{m.strength} GRADE* | conf {m.confidence}%\n✅ Already have: `{m.original_bet_team} {m.original_line:+}`\n🔄 Bet now: `{m.live_bet_team} {m.live_line:+}`\n📏 Middle: `{m.window_low:.1f}—{m.window_high:.1f}` (**size {m.window_size}**)\n💰 EV2: `{m.ev_percent:+.2f}%`")
            print("-" * 60)
if __name__ == "__main__":
    engine = MiddlesEngine()
    print("🚀 Middles Engine started")
    while True:
        current_live_spreads = []  # replace with live spreads later
        middles = engine.run_once(current_live_spreads)
        if middles:
            engine.print_alerts(middles)
        time.sleep(30)
