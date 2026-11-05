#!/usr/bin/env python3
"""
Parameter Optimization via Monte Carlo
"""

import numpy as np
import itertools

class Optimizer:
    def __init__(self):
        pass

    def simulate(self, kelly, rr, atr_mult, win_rate=0.58, volatility=0.018, simulations=5000, days=252):
        results = []
        for _ in range(simulations):
            equity = 100000.0
            peak = equity
            for day in range(days):
                is_trending = np.random.random() < 0.6
                vol = volatility * (1.4 if is_trending else 0.8)
                risk = min(kelly, 0.02)
                if np.random.random() < win_rate:
                    ret = np.random.normal(2.4 * vol, vol)
                else:
                    ret = np.random.normal(-1.0 * vol, vol)
                equity *= (1 + ret * risk)
                peak = max(peak, equity)
            results.append(equity)
        return np.median(results), np.percentile(results, 5)

    def optimize(self):
        best_median = 0
        best_params = None

        kelly_range = [0.5, 0.6, 0.7]
        rr_range = [2.0, 2.5, 3.0]
        atr_range = [1.5, 1.7, 2.0]

        for k, r, a in itertools.product(kelly_range, rr_range, atr_range):
            median, fifth = self.simulate(k, r, a)
            if median > best_median and fifth > 105000:
                best_median = median
                best_params = (k, r, a)
                print(f"New best: Kelly={k}, RR={r}, ATR={a} | Median=${median:,.0f}")

        print(f"\nBest parameters: Kelly={best_params[0]}, RR={best_params[1]}, ATR={best_params[2]}")
        print(f"Median Equity: ${best_median:,.0f}")

if __name__ == "__main__":
    opt = Optimizer()
    opt.optimize()
