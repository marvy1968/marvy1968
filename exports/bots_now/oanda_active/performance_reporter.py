#!/usr/bin/env python3
"""
Automated Performance Reporter for Trading Bots
"""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime, timedelta
import glob

class PerformanceReporter:
    def __init__(self, journal_path="trade_journal.csv", logs_dir="logs"):
        self.journal_path = Path(journal_path)
        self.logs_dir = Path(logs_dir)

    def load_trades(self):
        if not self.journal_path.exists():
            # Fallback: try to parse from logs
            return self.parse_from_logs()
        
        try:
            df = pd.read_csv(self.journal_path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df
        except:
            return pd.DataFrame()

    def parse_from_logs(self):
        # Simple fallback parser
        trades = []
        for log_file in glob.glob(str(self.logs_dir / "*.log")):
            with open(log_file) as f:
                for line in f:
                    if "CLOSE" in line and "PnL" in line:
                        try:
                            parts = line.split("|")
                            pnl_str = [p for p in parts if "PnL" in p][0]
                            pnl = float(pnl_str.split("$")[1].split()[0])
                            trades.append({"pnl": pnl, "timestamp": datetime.now()})
                        except:
                            pass
        return pd.DataFrame(trades)

    def generate_report(self, days=30):
        df = self.load_trades()
        if df.empty:
            return "No trade data found."

        # Filter recent trades
        cutoff = datetime.now() - timedelta(days=days)
        if 'timestamp' in df.columns:
            df = df[df['timestamp'] >= cutoff]

        if df.empty:
            return f"No trades in the last {days} days."

        total_pnl = df['pnl'].sum()
        wins = df[df['pnl'] > 0]
        win_rate = len(wins) / len(df) * 100
        avg_win = wins['pnl'].mean() if not wins.empty else 0
        avg_loss = df[df['pnl'] <= 0]['pnl'].mean() if len(df[df['pnl'] <= 0]) > 0 else 0

        # Simple max drawdown approximation
        equity_curve = df['pnl'].cumsum()
        peak = equity_curve.cummax()
        drawdown = (peak - equity_curve) / peak.max() * 100
        max_dd = drawdown.max()

        report = f"""
╔══════════════════════════════════════════════════════════════╗
║           TRADING PERFORMANCE REPORT - LAST {days} DAYS       ║
╚══════════════════════════════════════════════════════════════╝

Total Trades     : {len(df)}
Win Rate         : {win_rate:.1f}%
Total P&L        : ${total_pnl:,.2f}
Average Win      : ${avg_win:,.2f}
Average Loss     : ${avg_loss:,.2f}
Max Drawdown     : {max_dd:.2f}%

Profit Factor    : {abs(avg_win / avg_loss) if avg_loss != 0 else 'N/A':.2f}

Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return report

if __name__ == "__main__":
    reporter = PerformanceReporter()
    print(reporter.generate_report(days=7))   # Last 7 days
    print(reporter.generate_report(days=30))  # Last 30 days
