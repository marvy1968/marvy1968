#!/usr/bin/env python3
"""
Advanced Monte Carlo with Kelly Criterion + Regime Switching
"""

import numpy as np
import logging

class AdvancedMonteCarlo:
    def __init__(self, initial_equity=100000, num_simulations=10000, trading_days=365):
        self.initial_equity = initial_equity
        self.num_simulations = num_simulations
        self.trading_days = trading_days
        self.logger = logging.getLogger("AdvancedMC")
        logging.basicConfig(level=logging.INFO)

    def run(self, base_win_rate=0.55, avg_win=1.9, avg_loss=1.0, volatility=0.015, kelly_fraction=0.5):
        """
        Kelly Criterion + Regime Switching
        kelly_fraction: Conservative fraction of full Kelly (0.5 = half Kelly)
        """
        results = []
        max_drawdowns = []

        for sim in range(self.num_simulations):
            equity = self.initial_equity
            peak = equity
            drawdowns = []

            for day in range(self.trading_days):
                # Regime switching (50% trending, 50% ranging)
                is_trending = np.random.random() < 0.5
                current_vol = volatility * (1.5 if is_trending else 0.7)

                # Kelly sizing
                edge = base_win_rate * avg_win - (1 - base_win_rate) * avg_loss
                kelly = edge / avg_win if avg_win > 0 else 0
                risk = min(kelly * kelly_fraction, 0.02)  # Cap at 2%

                if np.random.random() < base_win_rate:
                    ret = np.random.normal(avg_win * current_vol, current_vol)
                else:
                    ret = np.random.normal(-avg_loss * current_vol, current_vol)

                equity *= (1 + ret * risk)
                peak = max(peak, equity)
                drawdown = (peak - equity) / peak
                drawdowns.append(drawdown)

            results.append(equity)
            max_drawdowns.append(max(drawdowns))

        results = np.array(results)
        max_drawdowns = np.array(max_drawdowns)

        print("\n" + "="*70)
        print("ADVANCED MONTE CARLO - KELLY + REGIME SWITCHING")
        print("="*70)
        print(f"Simulations: {self.num_simulations}")
        print(f"Initial Equity: ${self.initial_equity:,.0f}")
        print(f"Kelly Fraction: {kelly_fraction}")
        print("-"*70)
        print(f"Final Equity (Median):  ${np.median(results):,.0f}")
        print(f"Final Equity (Mean):    ${np.mean(results):,.0f}")
        print(f"Final Equity (5th %ile): ${np.percentile(results, 5):,.0f}")
        print(f"Final Equity (95th %ile): ${np.percentile(results, 95):,.0f}")
        print("-"*70)
        print(f"Max Drawdown (Median):  {np.median(max_drawdowns)*100:.1f}%")
        print(f"Max Drawdown (95th %ile): {np.percentile(max_drawdowns, 95)*100:.1f}%")
        print(f"Probability of Profit:  {(results > self.initial_equity).mean()*100:.1f}%")
        print(f"Probability of >20% DD: {(max_drawdowns > 0.20).mean()*100:.1f}%")
        print("="*70)

if __name__ == "__main__":
    mc = AdvancedMonteCarlo()
    mc.run(base_win_rate=0.55, avg_win=1.9, avg_loss=1.0, volatility=0.015, kelly_fraction=0.5)
