path = "/home/moonrider1968/bots/trade_velez_weinstein_swing.py"
with open(path, "r") as f:
    text = f.read()

# Replace the inner ticker extraction loop in fetch_signals
old_loop = """    for sym in all_syms:
        if sym == BENCHMARK:
            continue
        try:
            df = raw[sym].dropna()"""

new_loop = """    for sym in all_syms:
        if sym == BENCHMARK:
            continue
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if sym not in raw.columns.levels[0]:
                    continue
                df = raw[sym].dropna()
            else:
                df = raw.dropna()"""

if old_loop in text:
    text = text.replace(old_loop, new_loop)
    with open(path, "w") as f:
        f.write(text)
    print("[✓] Patched MultiIndex DataFrame handling.")
else:
    print("[!] Target loop header not found; inspecting fetch_signals block.")
