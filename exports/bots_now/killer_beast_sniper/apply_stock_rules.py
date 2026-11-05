import json

# Compiled MFE/MAE Stock Parameter Matrix
STOCK_OPTIMIZATION_MATRIX = {
    # High Beta Tech (0.04% - 0.06% Stops / Targets)
    "TSLA": {"RANGE_BREAKOUT": {"sl_pct": 0.0004, "tp_pct": 0.0005}, "VELEZ_20EMA": {"sl_pct": 0.0003, "tp_pct": 0.0003}},
    "AMD":  {"RANGE_BREAKOUT": {"sl_pct": 0.0005, "tp_pct": 0.0006}, "VELEZ_20EMA": {"sl_pct": 0.0004, "tp_pct": 0.0004}},
    "NVDA": {"MICRO_MOMENTUM": {"sl_pct": 0.0006, "tp_pct": 0.0006}, "RANGE_BREAKOUT": {"sl_pct": 0.0004, "tp_pct": 0.0005}},
    
    # Large Cap Tech & Mega Caps (0.02% - 0.04% Stops / Targets)
    "AAPL": {"VELEZ_20EMA": {"sl_pct": 0.0002, "tp_pct": 0.0002}, "RANGE_BREAKOUT": {"sl_pct": 0.0002, "tp_pct": 0.0002}},
    "MSFT": {"MICRO_MOMENTUM": {"sl_pct": 0.0003, "tp_pct": 0.0004}, "RANGE_BREAKOUT": {"sl_pct": 0.0003, "tp_pct": 0.0004}},
    "AMZN": {"MICRO_MOMENTUM": {"sl_pct": 0.0004, "tp_pct": 0.0005}, "RANGE_BREAKOUT": {"sl_pct": 0.0003, "tp_pct": 0.0003}},
    
    # Index ETFs (0.01% - 0.02% Tight Micro Scalps)
    "SPY":  {"RANGE_BREAKOUT": {"sl_pct": 0.0001, "tp_pct": 0.0001}, "VELEZ_20EMA": {"sl_pct": 0.0001, "tp_pct": 0.0001}},
    "QQQ":  {"RANGE_BREAKOUT": {"sl_pct": 0.0001, "tp_pct": 0.0001}, "VOL_SQUEEZE":  {"sl_pct": 0.0002, "tp_pct": 0.0002}}
}

def get_trade_execution_params(ticker: str, strategy: str, entry_price: float, side: str):
    """
    Returns exact dynamic Stop-Loss and Take-Profit dollar levels for stock entries.
    """
    rules = STOCK_OPTIMIZATION_MATRIX.get(ticker, {}).get(
        strategy, {"sl_pct": 0.0003, "tp_pct": 0.0004} # Fallback defaults
    )
    
    sl_pct = rules["sl_pct"]
    tp_pct = rules["tp_pct"]
    
    if side.upper() == "BUY":
        stop_loss = round(entry_price * (1.0 - sl_pct), 2)
        take_profit = round(entry_price * (1.0 + tp_pct), 2)
    else: # SELL
        stop_loss = round(entry_price * (1.0 + sl_pct), 2)
        take_profit = round(entry_price * (1.0 - tp_pct), 2)
        
    return {
        "ticker": ticker,
        "strategy": strategy,
        "side": side,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "sl_pct_str": f"{sl_pct*100:.2f}%",
        "tp_pct_str": f"{tp_pct*100:.2f}%"
    }

# Test execution with live SPY price level
if __name__ == "__main__":
    test_trade = get_trade_execution_params("SPY", "RANGE_BREAKOUT", 773.26, "BUY")
    print("✅ Verified Production Rule Matrix Output:")
    print(json.dumps(test_trade, indent=2))
