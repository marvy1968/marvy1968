import json, os

MATRIX_PATH = "/home/moonrider1968/bots/killer_beast_sniper/forex_learning_matrix.json"
ACTIVE_WHITELIST_PATH = "/home/moonrider1968/bots/killer_beast_sniper/active_whitelist.json"

def evaluate_promotion():
    if not os.path.exists(MATRIX_PATH):
        print("No matrix found.")
        return

    with open(MATRIX_PATH, 'r') as f:
        matrix = json.load(f)

    promoted = []
    demoted = []

    for symbol, stats in matrix.items():
        total = stats.get('wins', 0) + stats.get('losses', 0)
        if total >= 30:
            win_rate = stats.get('wins', 0) / total
            if win_rate >= 0.55:
                promoted.append(symbol)
            elif win_rate < 0.45:
                demoted.append(symbol)

    print(f"📈 Candidates for Auto-Promotion (>55% WR): {promoted}")
    print(f"📉 Candidates for Demotion (<45% WR): {demoted}")

if __name__ == '__main__':
    evaluate_promotion()
