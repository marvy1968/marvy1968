import json
import os
from datetime import datetime, timedelta

class TickerPreferenceEngine:
    def __init__(self, history_file="/home/moonrider1968/bots/killer_beast_sniper/trade_history.json"):
        self.history_file = history_file
        self.history = self._load_history()

    def _load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_history(self):
        with open(self.history_file, 'w') as f:
            json.dump(self.history, f, indent=2)

    def record_trade(self, ticker: str, pnl_pct: float, is_win: bool, setup_tier: str = "Tier 1"):
        entry = {
            "ticker": ticker,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pnl_pct": round(pnl_pct, 4),
            "win": is_win,
            "tier": setup_tier
        }
        self.history.append(entry)
        self._save_history()

    def evaluate_ticker(self, ticker: str, lookback_days: int = 30) -> dict:
        cutoff = datetime.now() - timedelta(days=lookback_days)
        recent_trades = []

        for trade in self.history:
            trade_time = datetime.strptime(trade["timestamp"], "%Y-%m-%d %H:%M:%S")
            if trade["ticker"] == ticker and trade_time >= cutoff:
                recent_trades.append(trade)

        if len(recent_trades) < 5:
            return {"status": "NEUTRAL", "win_rate": 0.0, "total_trades": len(recent_trades)}

        wins = sum(1 for t in recent_trades if t["win"])
        win_rate = wins / len(recent_trades)

        if win_rate < 0.35:
            return {"status": "BENCHED", "win_rate": win_rate, "total_trades": len(recent_trades)}
        elif win_rate >= 0.60:
            return {"status": "HOT", "win_rate": win_rate, "total_trades": len(recent_trades)}
        else:
            return {"status": "NEUTRAL", "win_rate": win_rate, "total_trades": len(recent_trades)}
