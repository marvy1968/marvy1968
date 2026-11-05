import os
import json
from datetime import datetime
import alpaca_trade_api as tradeapi

stock_script = '/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py'
state_file = '/home/moonrider1968/bots/killer_beast_sniper/data/crypto_daemon_state.json'

API_KEY = None
API_SECRET = None

if os.path.exists(stock_script):
    with open(stock_script, 'r') as f:
        for line in f:
            line_str = line.strip()
            if 'API_KEY' in line_str and '=' in line_str and not line_str.startswith('#'):
                parts = line_str.split('=', 1)
                if len(parts) == 2 and not API_KEY:
                    API_KEY = parts[1].strip().strip('"\'')
            if ('SECRET_KEY' in line_str or 'SECRET' in line_str) and '=' in line_str and not line_str.startswith('#'):
                parts = line_str.split('=', 1)
                if len(parts) == 2 and not API_SECRET:
                    API_SECRET = parts[1].strip().strip('"\'')

if not API_KEY or not API_SECRET:
    print("❌ Error: Could not extract API credentials from velez_stage4_short_daemon.py")
    exit(1)

api = tradeapi.REST(key_id=API_KEY, secret_key=API_SECRET, base_url='https://paper-api.alpaca.markets', api_version='v2')

# 1. Load Disk State
sl_cooldowns = {}
peak_prices = {}
if os.path.exists(state_file):
    with open(state_file, 'r') as f:
        try:
            data = json.load(f)
            sl_cooldowns = data.get('sl_cooldowns', {})
            peak_prices = data.get('peak_prices', {})
        except Exception:
            pass

# 2. Fetch Open Positions
try:
    positions = api.list_positions()
except Exception as e:
    print(f"❌ API Request Failed: {e}")
    exit(1)

stock_tickers = ['AAPL', 'NVDA', 'AMD', 'TSLA', 'MSFT', 'COIN', 'RBLX', 'AMZN', 'META', 'GOOGL']
crypto_positions = [p for p in positions if '/' in p.symbol or ('USD' in p.symbol and p.symbol not in stock_tickers)]

print(f"\n=== 📊 ACTIVE CRYPTO POSITIONS ({len(crypto_positions)}) ===")
if crypto_positions:
    print(f"{'SYMBOL':<10} | {'QTY':<10} | {'ENTRY':<10} | {'CURRENT':<10} | {'PEAK HIGH':<10} | {'UNREALIZED PNL'}")
    print('-'*75)
    for p in crypto_positions:
        sym = p.symbol
        qty = float(p.qty)
        entry = float(p.avg_entry_price)
        curr = float(p.current_price)
        pnl = float(p.unrealized_pl)
        pnl_pct = float(p.unrealized_plpc) * 100
        clean_sym = sym.replace('/', '')
        peak = peak_prices.get(clean_sym, peak_prices.get(sym, entry))
        
        status_icon = '🟢' if pnl >= 0 else '🔴'
        print(f"{sym:<10} | {qty:<10.2f} | ${entry:<9.2f} | ${curr:<9.2f} | ${peak:<9.2f} | {status_icon} ${pnl:<7.2f} ({pnl_pct:+.2f}%)")
else:
    print("No open crypto positions at this moment.")

print("\n=== ⏳ ACTIVE DISK COOLDOWN LOCKOUTS ===")
now = datetime.now()
active_cd = False
for sym, exp_str in sl_cooldowns.items():
    try:
        exp_dt = datetime.fromisoformat(exp_str)
        if exp_dt > now:
            active_cd = True
            rem_min = int((exp_dt - now).total_seconds() / 60)
            print(f"🔒 {sym}: Locked out for another {rem_min} mins (until {exp_dt.strftime('%H:%M:%S')})")
    except Exception:
        continue

if not active_cd:
    print("None. All tickers cleared for scanning.")
