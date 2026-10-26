from backtest import Backtester
from alpaca_swing_trend_bot import AlpacaSwingTrendBot

bt = Backtester(AlpacaSwingTrendBot)
results = bt.run(
    symbol="AAPL",
    start="2024-01-01",
    end="2025-06-01",
    timeframe="D",
    use_real_data=True
)
print(results)
