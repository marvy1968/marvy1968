#!/usr/bin/env python3
"""
Master Simulator - All Experts + All Features
"""

import numpy as np

class MasterSimulator:
    def __init__(self, initial_equity=100000, simulations=3000, days=252):
        self.initial_equity = initial_equity
        self.simulations = simulations
        self.days = days

    def run(self, kelly=0.6, vol_rank=70, use_wyckoff=True, use_lps=True):
        results = []
        max_dds = []

        for _ in range(self.simulations):
            equity = self.initial_equity
            peak = equity
            drawdowns = []

            for day in range(self.days):
                is_trending = np.random.random() < 0.6
                vol = 0.018 * (1.4 if is_trending else 0.8)

                # Volume Rank Filter
                if np.random.random() * 100 < vol_rank:
                    continue

                # Wyckoff / LPS bias (higher win rate in accumulation)
                wyckoff_bonus = 0.08 if (use_wyckoff and np.random.random() < 0.35) else 0
                lps_bonus = 0.12 if (use_lps and np.random.random() < 0.25) else 0

                win_rate = 0.58 + wyckoff_bonus + lps_bonus
                risk = min(kelly, 0.05)

                if np.random.random() < win_rate:
                    ret = np.random.normal(2.5 * vol, vol)
                else:
                    ret = np.random.normal(-1.0 * vol, vol)

                equity *= (1 + ret * risk)
                peak = max(peak, equity)
                dd = (peak - equity) / peak
                drawdowns.append(dd)

            results.append(equity)
            max_dds.append(max(drawdowns) if drawdowns else 0)

        results = np.array(results)
        max_dds = np.array(max_dds)

        print("\n" + "="*75)
        print("MASTER SIMULATION - ALL EXPERTS + FEATURES")
        print("="*75)
        print(f"Simulations: {self.simulations}")
        print(f"Initial Equity: ${self.initial_equity:,.0f}")
        print("-"*75)
        print(f"Final Equity (Median):  ${np.median(results):,.0f}")
        print(f"Final Equity (Mean):    ${np.mean(results):,.0f}")
        print(f"5th %ile:  ${np.percentile(results, 5):,.0f}")
        print(f"95th %ile: ${np.percentile(results, 95):,.0f}")
        print("-"*75)
        print(f"Max DD (Median): {np.median(max_dds)*100:.1f}%")
        print(f"Max DD (95th): {np.percentile(max_dds, 95)*100:.1f}%")
        print(f"Profit Probability: {(results > self.initial_equity).mean()*100:.1f}%")
        print("="*75)

if __name__ == "__main__":
    sim = MasterSimulator()
    sim.run()
