import os, requests
from dotenv import load_dotenv

load_dotenv('/home/moonrider1968/bots/killer_beast_sniper/.env')
API_KEY = os.getenv('APCA_API_KEY_ID')
SECRET_KEY = os.getenv('APCA_API_SECRET_KEY')
DATA_URL = "https://data.alpaca.markets/v2"

SECTOR_MAP = {
    'XLK': 'Technology',
    'XLC': 'Communications',
    'XLY': 'Consumer Discretionary',
    'XLF': 'Financials',
    'XLI': 'Industrials',
    'XLE': 'Energy',
    'XLV': 'Healthcare',
    'XLP': 'Consumer Staples',
    'XLU': 'Utilities',
    'XLB': 'Materials',
    'XLRE': 'Real Estate'
}

def get_sector_rankings():
    headers = {'APCA-API-KEY-ID': API_KEY, 'APCA-API-SECRET-KEY': SECRET_KEY}
    symbols = list(SECTOR_MAP.keys()) + ['SPY']
    url = f"{DATA_URL}/stocks/snapshots?symbols={','.join(symbols)}"
    
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        print("Failed to fetch snapshots:", res.text)
        return [], []
    
    data = res.json()
    spy_change = 0.0
    if 'SPY' in data and data['SPY'].get('dailyBar') and data['SPY'].get('prevDailyBar'):
        c = data['SPY']['dailyBar']['c']
        pc = data['SPY']['prevDailyBar']['c']
        spy_change = ((c - pc) / pc) * 100

    sector_scores = []
    for etf, name in SECTOR_MAP.items():
        if etf in data and data[etf].get('dailyBar') and data[etf].get('prevDailyBar'):
            c = data[etf]['dailyBar']['c']
            pc = data[etf]['prevDailyBar']['c']
            pct = ((c - pc) / pc) * 100
            rel_strength = pct - spy_change
            sector_scores.append({
                'etf': etf,
                'name': name,
                'change': pct,
                'rs_vs_spy': rel_strength
            })

    sector_scores.sort(key=lambda x: x['rs_vs_spy'], reverse=True)
    
    hot = sector_scores[:3]
    cold = sector_scores[-3:]
    return hot, cold

if __name__ == '__main__':
    hot, cold = get_sector_rankings()
    print("=" * 60)
    print("🔥 TOP 3 HOT LEADERSHIP SECTORS (Stage 2 Focus)")
    for s in hot:
        print(f"   🟢 {s['etf']:<5} | {s['name']:<22} | Day: {s['change']:+.2f}% | RS: {s['rs_vs_spy']:+.2f}%")
    
    print("\n❄️ TOP 3 COLD / LAGGING SECTORS (Filtered Out)")
    for s in cold:
        print(f"   🔴 {s['etf']:<5} | {s['name']:<22} | Day: {s['change']:+.2f}% | RS: {s['rs_vs_spy']:+.2f}%")
    print("=" * 60)
