from backtest_consensus_calibrated import test_calibrated

print("=" * 74)
print(f"{'PAIR (H1)':<10} | {'TRADES':<7} | {'WIN RATE':<9} | {'NET PIPS':<10} | {'PF':<5} | {'AVG TRADE'}")
print("-" * 74)
for p in ["GBP_USD", "EUR_USD", "EUR_GBP"]:
    test_calibrated(p, "H1", 2000, spread_pips=1.2)
print("=" * 74)
