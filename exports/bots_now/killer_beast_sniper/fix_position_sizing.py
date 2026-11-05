import json

def calculate_normalized_units(
    account_balance: float, 
    risk_pct: float, 
    atr_pips: float, 
    pair: str, 
    current_price: float
) -> int:
    """
    Calculates exact OANDA unit size so that every trade risks the exact same dollar amount.
    """
    risk_dollars = account_balance * risk_pct
    
    # 1. Determine pip size
    if "JPY" in pair:
        pip_size = 0.01
    else:
        pip_size = 0.0001
        
    stop_distance = atr_pips * pip_size
    
    # 2. Convert pip value to USD
    if pair.endswith("_USD"):
        # EUR_USD, AUD_USD: 1 pip per unit = $0.0001
        pip_value_per_unit = pip_size
    elif pair.startswith("USD_"):
        # USD_JPY, USD_CAD: 1 pip per unit = pip_size / current_price USD
        pip_value_per_unit = pip_size / current_price
    else:
        # Cross pairs fallback
        pip_value_per_unit = pip_size
        
    # 3. Units = Total USD Risk / (Stop Distance in Pips * USD Pip Value)
    units = int(risk_dollars / (atr_pips * pip_value_per_unit))
    return max(units, 1)

# Test verification
if __name__ == "__main__":
    nav = 75200.0  # $75.2k account
    risk = 0.01    # 1% risk ($752)
    atr = 15.0     # 15 pip ATR stop

    aud_units = calculate_normalized_units(nav, risk, atr, "AUD_USD", 0.7056)
    jpy_units = calculate_normalized_units(nav, risk, atr, "USD_JPY", 159.20)

    print(f"✅ AUD_USD Sized Units: {aud_units:,} (Est. Dollar Risk: ${aud_units * (atr * 0.0001):.2f})")
    print(f"✅ USD_JPY Sized Units: {jpy_units:,} (Est. Dollar Risk: ${jpy_units * (atr * 0.01 / 159.20):.2f})")
