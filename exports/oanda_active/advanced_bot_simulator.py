#!/usr/bin/env python3
"""
Advanced Bot Simulator with All Upgrades
"""

import numpy as np

class AdvancedBotSimulator:
    def __init__(self, initial_equity=100000, num_simulations=5000, days=252):
        self.initial_equity = initial_equity
        self.num_simulations = num_simulations
        self.days = days

    def simulate(self, kelly=0.6, rr=2.5, atr_mult=1.7, vol_rank_threshold=70):
        results = []
        max_dds = []

        for _ in range(self.num_simulations):
            equity = self.initial_equity
            peak = equity
            drawdowns = []
            recent_pnls = []

            for day in range(self.days):
                is_trending = np.random.random() < 0.6
                vol = 0.018 * (1.4 if is_trending else 0.8)

                # Regime + Volume Rank
                if np.random.random() * 100 > vol_rank_threshold:
                    continue  # Skip low volume

                risk = min(kelly, 0.05)
                if np.random.random() < 0.58:
                    ret = np.random.normal(2.4 * vol, vol)
                else:
                    ret = np.random.normal(-1.0 * vol, vol)

                equity *= (1 + ret * risk)
                peak = max(peak, equity)
                dd = (peak - equity) / peak
                drawdowns.append(dd)
                recent_pnls.append(ret * risk)

            results.append(equity)
            max_dds.append(max(drawdowns) if drawdowns else 0)

        results = np.array(results)
        max_dds = np.array(max_dds)

        print("\n" + "="*70)
        print("ADVANCED BOT SIMULATION - ALL UPGRADES")
        print("="*70)
        print(f"Simulations: {self.num_simulations}")
        print(f"Initial Equity: ${self.initial_equity:,.0f}")
        print("-"*70)
        print(f"Final Equity (Median):  ${np.median(results):,.0f}")
        print(f"Final Equity (Mean):    ${np.mean(results):,.0f}")
        print(f"5th %ile: ${np.percentile(results, 5):,.0f}")
        print(f"95th %ile: ${np.percentile(results, 95):,.0f}")
        print("-"*70)
        print(f"Max DD (Median): {np.median(max_dds)*100:.1f}%")
        print(f"Max DD (95th): {np.percentile(max_dds, 95)*100:.1f}%")
        print(f"Profit Probability: {(results > self.initial_equity).mean()*100:.1f}%")
        print("="*70)

if __name__ == "__main__":
    sim = AdvancedBotSimulator()
    sim.simulate()
