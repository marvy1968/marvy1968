"""Position sizing: units so that a stop-out loses ~risk_pct of NAV (account currency USD)."""
import math

def quote_to_usd(pair, prices):
    """Factor converting 1 unit of the pair's quote currency into USD. prices: {instr: mid}."""
    base, quote = pair.split("_")
    if quote == "USD": return 1.0
    if f"USD_{quote}" in prices: return 1.0 / prices[f"USD_{quote}"]
    if f"{quote}_USD" in prices: return prices[f"{quote}_USD"]
    raise KeyError(f"no USD conversion for {quote}")

def base_to_usd(pair, prices):
    base, quote = pair.split("_")
    if base == "USD": return 1.0
    if f"{base}_USD" in prices: return prices[f"{base}_USD"]
    if f"USD_{base}" in prices: return 1.0 / prices[f"USD_{base}"]
    return prices[pair] * quote_to_usd(pair, prices)

def units_for_risk(pair, entry, stop, nav, risk_pct, prices, margin_rate=0.02, max_margin_frac=0.25):
    dist = abs(entry - stop)
    if dist <= 0 or nav <= 0: return 0
    risk_usd = nav * risk_pct / 100.0
    u = risk_usd / (dist * quote_to_usd(pair, prices))
    # margin cap: notional_usd * margin_rate <= max_margin_frac * NAV
    cap = max_margin_frac * nav / (margin_rate * base_to_usd(pair, prices))
    return int(math.floor(min(u, cap)))
