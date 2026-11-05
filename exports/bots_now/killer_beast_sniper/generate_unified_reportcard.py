import json, os

STOCK_MATRIX_PATH = "/home/moonrider1968/bots/killer_beast_sniper/stock_learning_matrix.json"
FOREX_MATRIX_PATH = "/home/moonrider1968/bots/killer_beast_sniper/forex_learning_matrix.json"

def load_matrix(path):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

stock_data = load_matrix(STOCK_MATRIX_PATH)
forex_data = load_matrix(FOREX_MATRIX_PATH)

print("=" * 70)
print("📊 KILLER BEAST SNIPER — UNIFIED SYSTEM REPORT CARD")
print("=" * 70)

print("\n📈 ALPACA EQUITIES LEARNING MATRIX:")
if stock_data:
    for sym, s in list(stock_data.items())[:8]:
        total = s.get('wins', 0) + s.get('losses', 0)
        wr = (s.get('wins', 0) / total * 100) if total > 0 else 0.0
        print(f"   • {sym:<6}: {s.get('wins', 0)}W - {s.get('losses', 0)}L | WR: {wr:.1f}% | Failures: {s.get('consecutive_failures', 0)}")
else:
    print("   • No active stock records yet.")

print("\n💱 OANDA FOREX LEARNING MATRIX:")
if forex_data:
    for pair, s in list(forex_data.items())[:8]:
        total = s.get('wins', 0) + s.get('losses', 0)
        wr = (s.get('wins', 0) / total * 100) if total > 0 else 0.0
        print(f"   • {pair:<8}: {s.get('wins', 0)}W - {s.get('losses', 0)}L | WR: {wr:.1f}% | Failures: {s.get('consecutive_failures', 0)}")
else:
    print("   • No active forex records yet.")

print("=" * 70)
