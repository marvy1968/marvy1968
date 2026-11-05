from pathlib import Path

p = Path("/home/moonrider1968/bots/harvest_alpaca_sip_canonical.py")
code = p.read_text()

# Restore full 18-symbol universe
old_sym = 'SYMBOLS = ["SPY"]'
full_sym = '''SYMBOLS = [
    "SPY", "QQQ",
    "AAPL", "MSFT", "NVDA", "AVGO",
    "GOOGL", "META", "AMZN", "TSLA",
    "JPM", "GS", "XOM", "CVX",
    "WMT", "COST", "CAT", "GE"
]'''

if old_sym in code:
    code = code.replace(old_sym, full_sym)

# Optimize datetime parsing to suppress parser warning and speed up loops
old_dt = 'df_chunk["time"] = pd.to_datetime(df_chunk["t"], utc=True)'
new_dt = 'df_chunk["time"] = pd.to_datetime(df_chunk["t"], format="ISO8601", utc=True)'
if old_dt in code:
    code = code.replace(old_dt, new_dt)

p.write_text(code)
print("Successfully expanded universe to 18 symbols and optimized ISO8601 parsing.")
