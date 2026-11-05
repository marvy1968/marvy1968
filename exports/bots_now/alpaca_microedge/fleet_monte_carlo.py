import numpy as np
np.random.seed(42)

def simulate_engine(initial, years, daily_trades, win_rate, vol_factor):
    equity = initial
    days = years * 252
    for _ in range(days):
        daily_pnl = 0
        for _ in range(daily_trades):
            if np.random.rand() < win_rate:
                # 45% taken at 2R + trailing remainder scaling up to 2.8R-3.5R
                daily_pnl += 0.003 * (2.0 * 0.45 + np.random.choice([2.8, 3.5]) * 0.55)
            else:
                # Local RAM virtual stops capping losses strictly
                daily_pnl -= 0.003 * np.random.uniform(0.7, 1.1)
        # 1.5% Global Daily Circuit Breaker Protection
        daily_pnl = max(daily_pnl, -0.015)
        equity *= (1 + daily_pnl * vol_factor)
        # 0.8% Probability of an exogenous Black Swan market gap event
        if np.random.rand() < 0.008: 
            equity *= np.random.uniform(0.86, 0.97)
    return equity

initial = 53757
print("=== FLEET MONTE CARLO PROJECTIONS (v26.6 Live Metrics) ===")
for y in [1, 3, 5]:
    print(f"\n📈 {y}-Year Horizon Projections:")
    crypto = simulate_engine(initial * 0.40, y, 16, 0.57, 1.15)
    stock  = simulate_engine(initial * 0.35, y, 10, 0.61, 0.95)
    forex  = simulate_engine(initial * 0.25, y, 12, 0.59, 0.85)
    total  = crypto + stock + forex
    print(f"  🪙 Crypto (40% Weight):  ${crypto:,.2f}")
    print(f"  📊 Stocks (35% Weight):  ${stock:,.2f}")
    print(f"  💱 Forex  (25% Weight):  ${forex:,.2f}")
    print(f"  🚀 UNIFIED FLEET EQUITY: ${total:,.2f} | Net Return: +{(total/initial-1)*100:.1f}%")
