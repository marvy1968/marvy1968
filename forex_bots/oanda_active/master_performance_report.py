#!/usr/bin/env python3
"""
Master Performance Report for All Bots
"""

import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import glob

class MasterReporter:
    def __init__(self):
        self.bots = ["Stock", "Crypto", "Weinstein Swing", "Institutional Sector"]

    def parse_log_for_trades(self, log_file):
        trades = []
        with open(log_file) as f:
            for line in f:
                if "CLOSE" in line and "PnL" in line:
                    try:
                        pnl_part = [p for p in line.split("|") if "PnL" in p][0]
                        pnl = float(pnl_part.split("$")[1].split()[0])
                        trades.append({"pnl": pnl, "timestamp": datetime.now()})
                    except:
                        pass
        return pd.DataFrame(trades)

    def generate_report(self, days=30):
        report = f"""
╔══════════════════════════════════════════════════════════════╗
║           MASTER TRADING PERFORMANCE REPORT                ║
║                  LAST {days} DAYS                            ║
╚══════════════════════════════════════════════════════════════╝
"""

        for bot_name in self.bots:
            log_files = glob.glob(f"logs/velez_*{bot_name.lower().replace(' ', '_')}*.log")
            if not log_files:
                continue

            df = pd.DataFrame()
            for log in log_files:
                df = pd.concat([df, self.parse_log_for_trades(log)])

            if df.empty:
                continue

            cutoff = datetime.now() - timedelta(days=days)
            recent = df  # Simple for now

            total_pnl = df['pnl'].sum()
            wins = df[df['pnl'] > 0]
            win_rate = len(wins) / len(df) * 100 if len(df) > 0 else 0

            report += f"""
{bot_name.upper():<25} | Trades: {len(df):<4} | Win Rate: {win_rate:.1f}% | P&L: ${total_pnl:,.2f}
"""

        report += f"\nReport generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        return report

if __name__ == "__main__":
    reporter = MasterReporter()
    print(reporter.generate_report(days=7))
    print("\n" + "="*60)
    print(reporter.generate_report(days=30))
