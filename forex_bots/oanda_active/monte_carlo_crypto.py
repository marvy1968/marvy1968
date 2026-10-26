#!/usr/bin/env python3
"""
Monte Carlo for Velez Crypto Bot
"""

import numpy as np
import logging

class MonteCarloCrypto:
    def __init__(self, initial_equity=100000, num_simulations=10000, trading_days=365):
        self.initial_equity = initial_equity
        self.num_simulations = num_simulations
        self.trading_days = trading_days
        self.logger = logging.getLogger("CryptoMonteCarlo")
        logging.basicConfig(level=logging.INFO)

    def run(self, win_rate=0.52, avg_win=2.2, avg_loss=1.0, volatility=0.035):
        results = []
        max_drawdowns = []

        for sim in range(self.num_simulations):
            equity = self.initial_equity
            peak = equity
            drawdowns = []

            for day in range(self.trading_days):
                if np.random.random() < win_rate:
                    ret = np.random.normal(avg_win * volatility, volatility)
                else:
                    ret = np.random.normal(-avg_loss * volatility, volatility)

                equity *= (1 + ret)
                peak = max(peak, equity)
                drawdown = (peak - equity) / peak
                drawdowns.append(drawdown)

            results.append(equity)
            max_drawdowns.append(max(drawdowns))

        results = np.array(results)
        max_drawdowns = np.array(max_drawdowns)

        print("\n" + "="*60)
        print("INSTITUTIONAL MONTE CARLO - VELEZ CRYPTO BOT")
        print("="*60)
        print(f"Simulations: {self.num_simulations}")
        print(f"Initial Equity: ${self.initial_equity:,.0f}")
        print(f"Trading Days: {self.trading_days}")
        print("-"*60)
        print(f"Final Equity (Median):  ${np.median(results):,.0f}")
        print(f"Final Equity (Mean):    ${np.mean(results):,.0f}")
        print(f"Final Equity (5th %ile): ${np.percentile(results, 5):,.0f}")
        print(f"Final Equity (95th %ile): ${np.percentile(results, 95):,.0f}")
        print("-"*60)
        print(f"Max Drawdown (Median):  {np.median(max_drawdowns)*100:.1f}%")
        print(f"Max Drawdown (95th %ile): {np.percentile(max_drawdowns, 95)*100:.1f}%")
        print(f"Probability of Profit:  {(results > self.initial_equity).mean()*100:.1f}%")
        print(f"Probability of >25% DD: {(max_drawdowns > 0.25).mean()*100:.1f}%")
        print("="*60)

if __name__ == "__main__":
    mc = MonteCarloCrypto()
    mc.run(win_rate=0.52, avg_win=2.2, avg_loss=1.0, volatility=0.035)
