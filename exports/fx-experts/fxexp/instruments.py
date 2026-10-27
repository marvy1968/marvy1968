"""Static instrument facts. Spreads are conservative typical OANDA (US, standard pricing)
spreads in pips during liquid hours; ROLL_MULT widens them for fills at the 17:00 NY open."""
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF", "NZD_USD", "EUR_JPY", "GBP_JPY"]
# extra crosses used only by systems with their own pair list (Tharp), so they never collide with the majors
EXTRA_PAIRS = ["EUR_GBP", "EUR_CHF", "AUD_JPY", "CAD_JPY", "AUD_NZD", "EUR_AUD"]
ALL_PAIRS = PAIRS + EXTRA_PAIRS
TYPICAL_SPREAD_PIPS = {"EUR_GBP": 1.6, "EUR_CHF": 2.2, "AUD_JPY": 2.2, "CAD_JPY": 2.6, "AUD_NZD": 3.0, "EUR_AUD": 2.8,
                       "EUR_USD": 1.4, "GBP_USD": 2.0, "USD_JPY": 1.4, "AUD_USD": 1.4, "USD_CAD": 2.2,
                       "USD_CHF": 1.8, "NZD_USD": 2.0, "EUR_JPY": 2.0, "GBP_JPY": 3.0}
ROLL_MULT = 2.0          # spread multiplier for fills right at the 17:00 NY daily open
SLIP_PIPS = 0.3          # extra slippage on stop-type fills (entry stops, stop losses, market)
FIN_PIPS_PER_NIGHT = 0.3 # rough net financing cost per 17:00 NY rollover held (x3 on Wednesday)
MAX_SPREAD_MULT = 2.5    # live: do not place/fire orders if spread > this x typical

def pip(pair: str) -> float:
    return 0.01 if pair.endswith("_JPY") else 0.0001

def precision(pair: str) -> int:
    return 3 if pair.endswith("_JPY") else 5

def spread(pair: str) -> float:
    """Typical spread in price units."""
    return TYPICAL_SPREAD_PIPS[pair] * pip(pair)

def rnd(pair: str, px: float) -> float:
    return round(px, precision(pair))
