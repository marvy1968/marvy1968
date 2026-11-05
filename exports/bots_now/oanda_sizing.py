def calculate_position_size(
    account_nav_usd: float,
    risk_pct: float,
    stop_distance_quote: float,
    pair: str,
    usd_quote_rate: float,
    max_leverage_per_trade: float = 2.0,
    hard_unit_cap: int | None = 20_000,
) -> int:
    if account_nav_usd <= 0:
        raise ValueError("account_nav_usd must be positive")
    if not (0 < risk_pct <= 1.0):
        raise ValueError("risk_pct must be between 0 and 1.0")
    if stop_distance_quote <= 0:
        raise ValueError("stop_distance_quote must be positive")
    if usd_quote_rate <= 0:
        raise ValueError("usd_quote_rate must be positive")

    risk_usd = account_nav_usd * risk_pct

    # 1. Stop-Loss Risk Sizing (Dollars at risk if stop is hit)
    if pair.endswith("_USD"):
        units_stop = risk_usd / stop_distance_quote
        notional_units_max = (account_nav_usd * max_leverage_per_trade) / usd_quote_rate
    elif pair.startswith("USD_"):
        units_stop = (risk_usd * usd_quote_rate) / stop_distance_quote
        notional_units_max = account_nav_usd * max_leverage_per_trade
    else:
        raise NotImplementedError("Cross pair requires explicit quote-to-USD conversion.")

    # 2. Dual Boundary Caps (Stop risk vs gross notional ceiling)
    effective_units = min(units_stop, notional_units_max)

    if hard_unit_cap is not None:
        effective_units = min(effective_units, hard_unit_cap)

    return max(int(effective_units), 0)


if __name__ == "__main__":
    nav = 62856.0
    risk = 0.005
    stop = 0.84
    rate = 157.80
    units = calculate_position_size(nav, risk, stop, "USD_JPY", rate, hard_unit_cap=20000)
    print(f"Sanity Check (USD_JPY @ 20k cap): {units} units")
    units_uncapped = calculate_position_size(nav, risk, stop, "USD_JPY", rate, hard_unit_cap=None)
    print(f"Sanity Check (USD_JPY uncapped): {units_uncapped} units")
