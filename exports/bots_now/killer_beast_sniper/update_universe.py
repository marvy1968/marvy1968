daemon_path = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py"

with open(daemon_path, "r") as f:
    code = f.read()

# Expanded 56-ticker sector universe
expanded_universe = """UNIVERSE = [
    # Mega-Cap & Big Tech
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NFLX", "ORCL", "CRM", "ADBE", "CSCO",
    # Semiconductors & AI Hardware
    "NVDA", "AMD", "AVGO", "TSM", "QCOM", "MU", "INTC", "ARM", "AMAT", "LRCX",
    # EV, Automotive & Space
    "TSLA", "RIVN", "LCID", "F", "GM", "RKLB",
    # Fintech & Crypto Proxies
    "COIN", "MARA", "RIOT", "CLSK", "MSTR", "PYPL", "SQ", "HOOD", "SOFI", "AFRM",
    # High-Beta Growth & Software
    "PLTR", "SNOW", "DDOG", "NET", "PATH", "ROKU", "SNAP", "U",
    # Energy & Industrial Beta
    "XOM", "CVX", "SLB", "HAL", "OXY", "CAT",
    # Financials & Major Banks
    "JPM", "BAC", "GS", "MS", "C", "WFC"
]"""

import re
code = re.sub(r'UNIVERSE\s*=\s*\[[^\]]*\]', expanded_universe, code)

with open(daemon_path, "w") as f:
    f.write(code)

print("✅ UNIVERSE updated to 56 multi-sector stocks!")
