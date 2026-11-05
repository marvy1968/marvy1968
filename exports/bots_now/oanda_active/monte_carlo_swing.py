#!/usr/bin/env python3
"""
Full Monte Carlo Simulation for Velez-Weinstein Swing Bot
"""

import numpy as np
import logging

class MonteCarloSwing:
    def __init__(self, initial_equity=100000, num_simulations=10000, trading_days=252):
        self.initial_equity = initial_equity
        self.num_simulations = num_simulations
        self.trading_days = trading_days
        self.logger = logging.getLogger("SwingMonteCarlo")
        logging.basicConfig(level=logging.INFO)

    def run(self, win_rate=0.58, avg_win=2.2, avg_loss=1.0, volatility=0.018, kelly_fraction=0.5):
        results = []
        max_drawdowns = []

        for sim in range(self.num_simulations):
            equity = self.initial_equity
            peak = equity
            drawdowns = []

            for day in range(self.trading_days):
                # Regime switching
                is_trending = np.random.random() < 0.6
                current_vol = volatility * (1.4 if is_trending else 0.8)

                # Kelly sizing
                edge = win_rate * avg_win - (1 - win_rate) * avg_loss
                kelly = edge / avg_win if avg_win > 0 else 0
                risk = min(kelly * kelly_fraction, 0.015)

                if np.random.random() < win_rate:
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
        print("FULL MONTE CARLO - VELEZ-WEINSTEIN SWING BOT")
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
    mc = MonteCarloSwing()
    mc.run(win_rate=0.58, avg_win=2.2, avg_loss=1.0, volatility=0.018, kelly_fraction=0.5)
